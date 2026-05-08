import os
import logging
import asyncio
import re
import random
import json
from datetime import datetime, timedelta
from flask import Flask
from telegram import Update, ChatPermissions
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from groq import Groq
import speech_recognition as sr
from pydub import AudioSegment
from collections import defaultdict, deque
import aiohttp
from typing import Optional, Dict
from dotenv import load_dotenv
import nest_asyncio

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ===== CONFIG =====
AI_VOICE_API_KEY = os.environ.get("SARVAM_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
OWNER_ID = int(os.environ.get("OWNER_ID", 0))

AI_BASE_URL = "https://api.sarvam.ai"
AI_VOICE_MODEL = "sarvam-voice-1.0"
USE_AI_VOICE = AI_VOICE_API_KEY is not None

if GROQ_API_KEY:
    client = Groq(api_key=GROQ_API_KEY)
else:
    print("❌ GROQ_API_KEY missing!")
    exit(1)

# ===== AI TRAINING SYSTEM (Production Ready) =====
training_data: Dict[int, deque] = {}
TRAINING_FILE = "senorita_training.json"
MAX_CONVS_PER_USER = 200
AUTO_SAVE_INTERVAL = 15
MIN_CONVO_LENGTH = 5

def save_training_data(user_id: int, user_msg: str, bot_reply: str):
    """Save conversations for AI training (thread-safe)"""
    user_msg = user_msg.strip()
    bot_reply = bot_reply.strip()
    
    if len(user_msg) < MIN_CONVO_LENGTH or len(bot_reply) < MIN_CONVO_LENGTH:
        return
        
    if user_id not in training_data:
        training_data[user_id] = deque(maxlen=MAX_CONVS_PER_USER)
    
    training_data[user_id].append({
        "user": user_msg,
        "bot": bot_reply,
        "timestamp": datetime.now().isoformat(),
        "length": len(user_msg)
    })
    
    if len(training_data[user_id]) % AUTO_SAVE_INTERVAL == 0:
        asyncio.create_task(_async_save_training())

async def _async_save_training():
    """Async save to avoid blocking"""
    await asyncio.sleep(0.1)
    save_training_to_file()

def save_training_to_file():
    """Save training data with cleanup"""
    try:
        if not training_data:
            return
            
        cutoff = datetime.now() - timedelta(days=90)
        filtered_data = {}
        total_convs = 0
        
        for user_id, convs in training_data.items():
            recent_convs = [c for c in convs if datetime.fromisoformat(c['timestamp']) > cutoff]
            if recent_convs:
                filtered_data[user_id] = list(recent_convs)
                total_convs += len(recent_convs)
        
        with open(TRAINING_FILE, 'w', encoding='utf-8') as f:
            json.dump(filtered_data, f, ensure_ascii=False, indent=1)
        
        logger.info(f"💾 Saved {total_convs} training conversations")
        
    except Exception as e:
        logger.error(f"Training save failed: {e}")

def load_training_data():
    """Load training data on startup"""
    global training_data
    try:
        if not os.path.exists(TRAINING_FILE):
            logger.info("🧠 No training file - starting fresh")
            return
            
        with open(TRAINING_FILE, 'r', encoding='utf-8') as f:
            raw_data = json.load(f)
        
        training_data = {}
        total_loaded = 0
        
        for user_id_str, convs in raw_data.items():
            user_id = int(user_id_str)
            valid_convs = []
            for conv in convs[-MAX_CONVS_PER_USER:]:
                if (isinstance(conv, dict) and 
                    'user' in conv and 'bot' in conv and 
                    len(conv['user']) >= MIN_CONVO_LENGTH):
                    valid_convs.append(conv)
            
            if valid_convs:
                training_data[user_id] = deque(valid_convs, maxlen=MAX_CONVS_PER_USER)
                total_loaded += len(valid_convs)
        
        logger.info(f"🧠 Loaded {total_loaded} conversations from {len(training_data)} users")
        
    except Exception as e:
        logger.error(f"Training load failed: {e}")
        training_data = {}

def _build_training_context(user_id: int, max_context: int = 1200) -> str:
    """Smart training context builder"""
    if user_id not in training_data or not training_data[user_id]:
        return ""
    
    recent_convs = list(training_data[user_id])[-8:]
    recent_convs.sort(key=lambda x: x['length'], reverse=True)
    
    context_parts = []
    context_len = 0
    
    for conv in recent_convs[:6]:
        part = f"👤: {conv['user'][:80]}...\n💬: {conv['bot'][:120]}..."
        if context_len + len(part) > max_context:
            break
        context_parts.append(part)
        context_len += len(part)
    
    return "\n".join(context_parts)

# ===== FIXED AI VOICE FUNCTIONS =====
async def transcribe_with_ai(audio_file_path: str, language: str = "hi") -> Optional[str]:
    if not AI_VOICE_API_KEY:
        return None
    try:
        # Convert audio to proper format
        audio = AudioSegment.from_file(audio_file_path)
        audio = audio.set_frame_rate(16000).set_channels(1)
        audio.export("temp_transcribe.wav", format="wav")
        
        with open("temp_transcribe.wav", 'rb') as f:
            audio_bytes = f.read()
            
        headers = {"Authorization": f"Bearer {AI_VOICE_API_KEY}"}
        form_data = aiohttp.FormData()
        form_data.add_field('audio', audio_bytes, filename='audio.wav', content_type='audio/wav')
        form_data.add_field('language', language)
        form_data.add_field('model', AI_VOICE_MODEL)
        form_data.add_field('response_format', 'json')
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{AI_BASE_URL}/v1/audio/transcriptions",
                headers=headers,
                data=form_data,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    text = result.get("text", "").strip()
                    if text:
                        return text
        return None
    except Exception as e:
        logger.error(f"AI Transcribe failed: {e}")
        return None

async def generate_indian_girl_voice(text: str, output_path: str) -> bool:
    if not AI_VOICE_API_KEY:
        return False
    try:
        headers = {"Authorization": f"Bearer {AI_VOICE_API_KEY}", "Content-Type": "application/json"}
        data = {
            "text": text[:150],
            "voice": "shruti",
            "speed": 1.0,
            "format": "mp3"
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{AI_BASE_URL}/v1/audio/speech",
                headers=headers,
                json=data,
                timeout=aiohttp.ClientTimeout(total=20)
            ) as response:
                if response.status == 200:
                    audio_data = await response.read()
                    if len(audio_data) > 500:
                        with open(output_path, 'wb') as f:
                            f.write(audio_data)
                        return True
        return False
    except Exception as e:
        logger.error(f"TTS failed: {e}")
        return False

# ===== FLASK APP =====
app = Flask(__name__)

@app.route("/")
def home():
    voice_status = "✅ AI Voice: ACTIVE 🇮🇳" if USE_AI_VOICE else "❌ AI Voice: DISABLED"
    total_training = sum(len(v) for v in training_data.values()) if training_data else 0
    return f"""🚀 **Senorita Bot Status** 🔥
{voice_status}
🧠 Training Data: {total_training} conversations
📊 All features: ACTIVE
Ready for 24/7 UptimeRobot! ✨"""

# ===== USER SESSIONS =====
user_sessions = {}
user_preferences = {}
user_requests = defaultdict(list)
chat_stats = defaultdict(lambda: {"msgs": 0, "users": set()})

def get_user_language(user_id: int) -> str:
    lang = user_preferences.get(user_id, {}).get("language", "")
    return lang if lang else "hinglish"  # DEFAULT HINGLISH

def set_user_language(user_id: int, language: str) -> None:
    if user_id not in user_preferences:
        user_preferences[user_id] = {}
    user_preferences[user_id]["language"] = language

def get_user_gender(user_id: int) -> str:
    return user_preferences.get(user_id, {}).get("gender", "unknown")

def set_user_gender(user_id: int, gender: str) -> None:
    if user_id not in user_preferences:
        user_preferences[user_id] = {}
    user_preferences[user_id]["gender"] = gender

def get_conversation_history(user_id: int) -> list:
    if user_id not in user_sessions:
        user_sessions[user_id] = []
    return user_sessions[user_id]

def add_to_conversation(user_id: int, role: str, content: str):
    if user_id not in user_sessions:
        user_sessions[user_id] = []
    user_sessions[user_id].append({"role": role, "content": content})
    if len(user_sessions[user_id]) > 15:
        user_sessions[user_id] = user_sessions[user_id][-15:]

def get_system_prompt(language: str, user_gender: str) -> str:
    if language == "hinglish":
        language_instruction = """**STRICT HINGLISH ONLY** (Roman Hindi + English mix):
Examples: "haha fr bhai", "kya haal hai?", "mazza aa gaya 😂", "tu kaisa hai?"
NO DEVANAGARI SCRIPT. Casual Gen-Z style always."""
    elif language == "hindi":
        language_instruction = "Respond in Hindi (Devanagari script only)."
    elif language == "english":
        language_instruction = "Respond in English only. Casual style."
    elif language == "bengali":
        language_instruction = "Respond in Bengali (Bangla). Use TUI (casual)."
    elif language == "marathi":
        language_instruction = "Respond in Marathi. Use TU (casual)."
    elif language == "bhojpuri":
        language_instruction = "Respond in Bhojpuri. Use TU (casual), desi style."
    else:
        language_instruction = """**STRICT HINGLISH** - Roman Hindi+English mix only.
NO pure Hindi script. Examples: "kya bol raha hai bhai?", "haha fr 💀" """
    
    return f"""You are Senorita - Gen-Z AI girlfriend 😏🔥

PERSONALITY:
- Use TU/TUM only (NEVER Aap)
- Short responses (1-2 lines MAX)
- Emojis + Gen-Z slang always
- Slightly flirty but cute
- Meme-savvy, natural chat

{language_instruction}

FEATURES: tag anyone, welcomes, stats, moderation, voice replies"""

def detect_gender_sync(user_name: str) -> str:
    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant", 
            messages=[{
                "role": "system", 
                "content": "Predict gender from name. Respond ONLY: male or female"
            }, {
                "role": "user", 
                "content": f"Gender for name: {user_name}"
            }], 
            max_tokens=5
        )
        gender = response.choices[0].message.content.strip().lower()
        return gender if gender in ["male", "female"] else "unknown"
    except:
        return "unknown"

async def detect_language_request(message: str) -> str:
    language_keywords = {
        "hindi": ["hindi", "hindi me", "hindi mein"], 
        "english": ["english", "english me", "angrezi"], 
        "hinglish": ["hinglish", "mix", "minglish", "hindi english"], 
        "bengali": ["bengali", "bangla"], 
        "marathi": ["marathi"], 
        "bhojpuri": ["bhojpuri"]
    }
    message_lower = message.lower()
    change_phrases = ["talk in", "speak in", "language", "switch to", "batao in", "bol in"]
    if any(phrase in message_lower for phrase in change_phrases):
        for lang, keywords in language_keywords.items():
            if any(keyword in message_lower for keyword in keywords):
                return lang
    return ""

# ===== ENHANCED AI RESPONSE =====
def get_ai_response_sync(user_message: str, user_name: str, user_id: int) -> str:
    try:
        user_gender = get_user_gender(user_id)
        if user_gender == "unknown":
            detected_gender = detect_gender_sync(user_name)
            set_user_gender(user_id, detected_gender)
            user_gender = detected_gender
            
        language = get_user_language(user_id)
        system_prompt = get_system_prompt(language, user_gender)
        
        # Add training context
        training_context = _build_training_context(user_id)
        if training_context:
            system_prompt += f"\n\n🧠 Recent chats:\n{training_context}"
        
        conversation = get_conversation_history(user_id)
        messages = [{"role": "system", "content": system_prompt}]
        for msg in conversation[-8:]:
            messages.append(msg)
        messages.append({"role": "user", "content": user_message})
        
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            max_tokens=150,
            temperature=0.85,
            top_p=0.92,
            presence_penalty=0.1
        )
        
        ai_response = response.choices[0].message.content.strip()
        if not ai_response or len(ai_response) < 5:
            ai_response = "haha fr 💀 kya bol raha hai bhai?"
            
        save_training_data(user_id, user_message, ai_response)
        add_to_conversation(user_id, "user", user_message)
        add_to_conversation(user_id, "assistant", ai_response)
        
        return ai_response
        
    except Exception as e:
        logger.error(f"AI Response Error: {str(e)}")
        fallback = "Arre yaar kuch gadbad ho gayi 💀\nDobara bol bhai!"
        save_training_data(user_id, user_message, fallback)
        return fallback

async def transcribe_voice(file_path: str) -> str:
    user_lang = get_user_language(12345) or "hi"
    
    # Try AI first
    if USE_AI_VOICE:
        logger.info("🔊 Using AI voice engine...")
        ai_result = await transcribe_with_ai(file_path, user_lang)
        if ai_result:
            return ai_result.strip()
    
    # Google backup
    logger.info("🔊 Using Google Speech (backup)...")
    recognizer = sr.Recognizer()
    try:
        audio = AudioSegment.from_file(file_path)
        audio.export("temp.wav", format="wav")
        with sr.AudioFile("temp.wav") as source:
            recognizer.adjust_for_ambient_noise(source)
            audio_data = recognizer.record(source)
            text = recognizer.recognize_google(audio_data, language='hi-IN')
            return text if text else "voice samajh nahi aayi"
    except:
        return "voice samajh nahi aayi 😅"
    finally:
        if os.path.exists("temp.wav"):
            os.remove("temp.wav")

async def rate_limit_check(user_id: int) -> bool:
    now = asyncio.get_event_loop().time()
    user_requests[user_id] = [t for t in user_requests[user_id] if now - t < 60]
    if len(user_requests[user_id]) >= 10:
        return False
    user_requests[user_id].append(now)
    return True

async def add_reaction(update: Update, emoji: str):
    try:
        await update.effective_chat.set_message_reaction(
            message_id=update.message.message_id, 
            reaction=[{"type": "emoji", "emoji": emoji}]
        )
    except:
        pass

async def forward_to_owner(update: Update, text: str):
    if OWNER_ID:
        try:
            await update.get_bot().send_message(chat_id=OWNER_ID, text=f"User {update.effective_user.first_name} ({update.effective_user.id}): {text}")
        except:
            pass

# ===== ALL COMMANDS (UNCHANGED) =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id = user.id
    detected_gender = detect_gender_sync(user.first_name)
    set_user_gender(user_id, detected_gender)
    user_sessions[user_id] = []
    set_user_language(user_id, "hinglish")  # Force Hinglish default
    voice_status = "✅ AI Voice: ACTIVE 🇮🇳" if USE_AI_VOICE else "❌ AI Voice: Add API key"
    welcome_text = f"""heyy {user.first_name}! ✨

i'm **Senorita** - your AI girlfriend with voice powers 😏🔥

💬 Chat normally (Hinglish default)
🎙️ Send voice → Get **VOICE reply** back!
🌍 /language to change

**{voice_status}**

Just talk naturally! 😘"""
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    voice_info = "\n🎙️ **Voice:** Send voice → Get VOICE reply! 🇮🇳" if USE_AI_VOICE else ""
    help_text = f"""🔥 **Senorita Commands**{voice_info}

**Chat:**
/start - restart chat
/language - change language  
/clear - clear memory

**Moderation (Admins):**
/kick - kick (reply)
/ban - ban (reply) 
/unban - unban (reply)
/mute - mute (reply)
/unmute - unmute (reply)
/promote - admin (reply)
/demote - remove admin (reply)
/purge - delete msgs (reply)
/tagall - tag everyone
/broadcast msg - announce

**Fun:**
/stats - chat stats
/id - your info
/alive - bot status

Chat naturally in Hinglish! 😏💕"""
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    current_lang = get_user_language(update.effective_user.id)
    voice_note = "\n\n🌟 Voice auto-detects language!" if USE_AI_VOICE else ""
    lang_text = f"""🌍 **Choose Language:**
- `hinglish` (default ⭐)
- `hindi`
- `english`
- `bengali`
- `marathi`  
- `bhojpuri`

Yours: `{current_lang}`{voice_note}

Say: "talk in bengali" ✨"""
    await update.message.reply_text(lang_text, parse_mode='Markdown')

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_sessions[update.effective_user.id] = []
    await update.message.reply_text("chat cleared! fresh start ✨")

# ===== MODERATION COMMANDS (UNCHANGED) =====
async def kick_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message.reply_to_message:
        await update.message.reply_text("reply to kick!")
        return
    try:
        chat_member = await update.effective_chat.get_member(update.effective_user.id)
        if chat_member.status not in ['administrator', 'creator']:
            await update.message.reply_text("admins only!")
            return
        user_to_kick = update.message.reply_to_message.from_user
        await update.effective_chat.ban_member(user_to_kick.id)
        await update.effective_chat.unban_member(user_to_kick.id)
        await update.message.reply_text(f"👋 {user_to_kick.first_name} kicked!")
    except:
        await update.message.reply_text("couldn't kick!")

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message.reply_to_message:
        await update.message.reply_text("reply to ban!")
        return
    try:
        chat_member = await update.effective_chat.get_member(update.effective_user.id)
        if chat_member.status not in ['administrator', 'creator']:
            await update.message.reply_text("admins only!")
            return
        user_to_ban = update.message.reply_to_message.from_user
        target_member = await update.effective_chat.get_member(user_to_ban.id)
        if target_member.status in ['administrator', 'creator']:
            await update.message.reply_text("can't ban admins!")
            return
        await update.effective_chat.ban_member(user_to_ban.id)
        await update.message.reply_text(f"🔨 {user_to_ban.first_name} banned!")
    except:
        await update.message.reply_text("couldn't ban!")

async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message.reply_to_message:
        await update.message.reply_text("reply to unban!")
        return
    try:
        chat_member = await update.effective_chat.get_member(update.effective_user.id)
        if chat_member.status not in ['administrator', 'creator']:
            await update.message.reply_text("admins only!")
            return
        user_to_unban = update.message.reply_to_message.from_user
        await update.effective_chat.unban_member(user_to_unban.id, only_if_banned=True)
        await update.message.reply_text(f"✅ {user_to_unban.first_name} unbanned!")
    except:
        await update.message.reply_text("couldn't unban!")

async def mute_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message.reply_to_message:
        await update.message.reply_text("reply to mute!")
        return
    try:
        chat_member = await update.effective_chat.get_member(update.effective_user.id)
        if chat_member.status not in ['administrator', 'creator']:
            await update.message.reply_text("admins only!")
            return
        user_to_mute = update.message.reply_to_message.from_user
        await update.effective_chat.restrict_member(user_to_mute.id, ChatPermissions(can_send_messages=False))
        await update.message.reply_text(f"🤫 {user_to_mute.first_name} muted!")
    except:
        await update.message.reply_text("couldn't mute!")

async def unmute_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message.reply_to_message:
        await update.message.reply_text("reply to unmute!")
        return
    try:
        chat_member = await update.effective_chat.get_member(update.effective_user.id)
        if chat_member.status not in ['administrator', 'creator']:
            await update.message.reply_text("admins only!")
            return
        user_to_unmute = update.message.reply_to_message.from_user
        permissions = ChatPermissions(
            can_send_messages=True, 
            can_send_media_messages=True, 
            can_send_polls=True, 
            can_send_other_messages=True, 
            can_add_web_page_previews=True, 
            can_change_info=False, 
            can_invite_users=True, 
            can_pin_messages=False
        )
        await update.effective_chat.restrict_member(user_to_unmute.id, permissions)
        await update.message.reply_text(f"🔊 {user_to_unmute.first_name} unmuted!")
    except:
        await update.message.reply_text("couldn't unmute!")

async def promote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message.reply_to_message:
        await update.message.reply_text("reply to promote!")
        return
    try:
        chat_member = await update.effective_chat.get_member(update.effective_user.id)
        if chat_member.status not in ['administrator', 'creator']:
            await update.message.reply_text("admins only!")
            return
        user_to_promote = update.message.reply_to_message.from_user
        await update.effective_chat.promote_member(
            user_to_promote.id, 
            can_change_info=True, 
            can_delete_messages=True, 
            can_restrict_members=True
        )
        await update.message.reply_text(f"🎉 {user_to_promote.first_name} is now admin!")
    except:
        await update.message.reply_text("couldn't promote!")

async def demote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message.reply_to_message:
        await update.message.reply_text("reply to demote!")
        return
    try:
        chat_member = await update.effective_chat.get_member(update.effective_user.id)
        if chat_member.status not in ['administrator', 'creator']:
            await update.message.reply_text("admins only!")
            return
        user_to_demote = update.message.reply_to_message.from_user
        await update.effective_chat.promote_member(
            user_to_demote.id, 
            can_change_info=False, 
            can_delete_messages=False, 
            can_restrict_members=False
        )
        await update.message.reply_text(f"👋 {user_to_demote.first_name} is no longer admin!")
    except:
        await update.message.reply_text("couldn't demote!")

async def purge_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to message to purge!")
        return
    try:
        chat_member = await update.effective_chat.get_member(update.effective_user.id)
        if chat_member.status not in ['administrator', 'creator']:
            await update.message.reply_text("Admins only!")
            return
        message_id = update.message.reply_to_message.message_id
        deleted_count = 0
        for i in range(10):
            try:
                await update.effective_chat.delete_message(message_id - i)
                deleted_count += 1
            except:
                continue
        await update.message.reply_text(f"🗑️ Purged {deleted_count} messages!")
    except:
        await update.message.reply_text("Purge failed!")

async def tagall_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat.type == 'private':
        await update.message.reply_text("Groups only!")
        return
    try:
        chat_member = await chat.get_member(update.effective_user.id)
        if chat_member.status not in ['administrator', 'creator']:
            await update.message.reply_text("Admins only!")
            return
        members = []
        async for member in chat.get_members():
            if not member.user.is_bot and member.status == 'member':
                members.append(member.user.mention_html())
        if not members:
            await update.message.reply_text("No members to tag!")
            return
        chunk_size = 20
        for i in range(0, len(members), chunk_size):
            chunk = members[i:i+chunk_size]
            tag_text = "👥 " + " ".join(chunk)
            await update.message.reply_text(tag_text, parse_mode='HTML')
            await asyncio.sleep(1)
    except Exception as e:
        logger.error(f"Tagall error: {e}")
        await update.message.reply_text("Tagall failed!")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    stats = chat_stats[chat_id]
    member_count = await update.effective_chat.get_member_count()
    voice_engine = "AI 🇮🇳" if USE_AI_VOICE else "Google"
    stats_text = f"""📊 **Chat Stats:**
- Messages: `{stats['msgs']}`
- Active Users: `{len(stats['users'])}`
- Total Members: `{member_count}`
- Voice: {voice_engine}

Powered by Senorita 🔥"""
    await update.message.reply_text(stats_text, parse_mode='Markdown')

async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    voice_engine = "AI 🇮🇳" if USE_AI_VOICE else "Google"
    id_text = f"""🆔 **Your Info:**
- User ID: `{user.id}`
- Username: @{user.username or 'None'}
- First Name: {user.first_name}
- Chat ID: `{chat.id}`
- Chat Type: {chat.type}
- Voice: {voice_engine}
- Language: {get_user_language(user.id)} ✨"""
    await update.message.reply_text(id_text, parse_mode='Markdown')

async def alive_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    voice_status = "✅ AI Voice: ACTIVE 🇮🇳" if USE_AI_VOICE else "❌ AI Voice: DISABLED"
    total_training = sum(len(v) for v in training_data.values()) if training_data else 0
    await update.message.reply_text(
        f"🚀 Senorita alive & kicking! 🔥\n"
        f"{voice_status}\n"
        f"🧠 Training: {total_training} convos\n"
        f"Hinglish mode: ACTIVE 😏"
    )

# ===== WELCOME SYSTEM =====
welcome_status = {}
welcome_messages = {}

async def setwelcome_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat.type == 'private':
        await update.message.reply_text("Groups only!")
        return
    try:
        chat_member = await chat.get_member(update.effective_user.id)
        if chat_member.status not in ['administrator', 'creator']:
            await update.message.reply_text("Admins only!")
            return
        if not context.args:
            await update.message.reply_text("Give welcome message!\n/setwelcome Welcome {user}!")
            return
        welcome_msg = " ".join(context.args)
        welcome_messages[chat.id] = welcome_msg
        await update.message.reply_text(f"✅ Welcome set: {welcome_msg}")
    except:
        await update.message.reply_text("Failed!")

async def welcome_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat.type == 'private':
        return
    try:
        chat_member = await chat.get_member(update.effective_user.id)
        if chat_member.status not in ['administrator', 'creator']:
            return
        chat_id = chat.id
        welcome_status[chat_id] = not welcome_status.get(chat_id, False)
        status = "ON" if welcome_status[chat_id] else "OFF"
        await update.message.reply_text(f"Welcome {status}! ✨")
    except:
        pass

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat.type == 'private':
        await update.message.reply_text("groups only!")
        return
    try:
        chat_member = await chat.get_member(update.effective_user.id)
        if chat_member.status not in ['administrator', 'creator']:
            await update.message.reply_text("admins only!")
            return
        if not context.args:
            await update.message.reply_text("give message to broadcast!")
            return
        broadcast_message = " ".join(context.args)
        await update.message.reply_text(f"📢 ANNOUNCEMENT:\n\n{broadcast_message}")
    except:
        await update.message.reply_text("failed lol")

# ===== FIXED MAIN MESSAGE HANDLER =====
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    message = update.message
    bot_username = (await context.bot.get_me()).username
    user_id = message.from_user.id
    chat_id = message.chat_id
    message_text = message.text.lower()
    chat_stats[chat_id]['msgs'] += 1
    chat_stats[chat_id]['users'].add(user_id)
    
    if not await rate_limit_check(user_id):
        return
        
    if message.chat.type == 'private' and OWNER_ID:
        await forward_to_owner(update, message.text)
        
    if message.new_chat_members:
        chat_id = message.chat_id
        if chat_id in welcome_status and welcome_status[chat_id]:
            for new_member in message.new_chat_members:
                if new_member.id != (await context.bot.get_me()).id:
                    welcome_msg = welcome_messages.get(chat_id, "Welcome {user} to {chat}! 🎉")
                    formatted_welcome = welcome_msg.format(user=new_member.mention_html(), chat=message.chat.title or "this group")
                    try:
                        await message.reply_text(formatted_welcome, parse_mode='HTML')
                    except:
                        await message.reply_text(f"Welcome {new_member.first_name} to {message.chat.title or 'this group'}! 🎉")
    
    should_respond = False
    if message.chat.type == 'private':
        should_respond = True
    elif message.reply_to_message and message.reply_to_message.from_user.id == (await context.bot.get_me()).id:
        should_respond = True
    elif bot_username and f"@{bot_username}" in message.text:
        should_respond = True
    elif "senorita" in message_text or "senorita" in message.text:
        should_respond = True
        
    if should_respond:
        user_name = message.from_user.first_name or "bro"
        if bot_username:
            user_text = message.text.replace(f"@{bot_username}", "").strip()
        else:
            user_text = message.text
            
        # Language detection
        lang_request = await detect_language_request(user_text)
        if lang_request:
            set_user_language(user_id, lang_request)
            await message.reply_text(f"aight, {lang_request} mode ON ✨")
            return
            
        # Force Hinglish for new users
        if get_user_language(user_id) == "":
            set_user_language(user_id, "hinglish")
            
        await context.bot.send_chat_action(chat_id=message.chat_id, action="typing")
        response = get_ai_response_sync(user_text, user_name, user_id)
        await add_reaction(update, "🔥")
        await message.reply_text(response)

# ===== FIXED VOICE HANDLER (VOICE REPLY GUARANTEED) =====
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    file_path = None
    voice_reply_path = None
    try:
        if not update.message.voice:
            return
            
        user_id = update.effective_user.id
        if not await rate_limit_check(user_id):
            await update.message.reply_text("Chill kar spam mat kar 😤")
            return
            
        # Download voice file
        file = await update.message.voice.get_file()
        file_path = f"voice_{update.message.message_id}.ogg"
        await file.download_to_drive(file_path)
        
        user_name = update.effective_user.first_name or "bro"
        
        if update.message.chat.type == 'private' and OWNER_ID:
            await forward_to_owner(update, f"🎤 Voice from {user_name}")
            
        await add_reaction(update, "🎙️")
        await context.bot.send_chat_action(update.effective_chat.id, "record_voice")
        
        # Transcribe voice
        transcribed_text = await transcribe_voice(file_path)
        logger.info(f"Transcribed: {transcribed_text}")
        
        if not transcribed_text or len(transcribed_text.strip()) < 2:
            await update.message.reply_text("voice samajh nahi aayi 😅\ntext try kar bhai!")
            return
        
        # Generate AI response
        response_text = get_ai_response_sync(transcribed_text, user_name, user_id)
        
        # Send VOICE REPLY
        if USE_AI_VOICE:
            voice_reply_path = f"reply_{update.message.message_id}.mp3"
            tts_success = await generate_indian_girl_voice(response_text, voice_reply_path)
            
            if tts_success and os.path.exists(voice_reply_path) and os.path.getsize(voice_reply_path) > 1000:
                try:
                    with open(voice_reply_path, 'rb') as audio:
                        await update.message.reply_voice(
                            audio,
                            caption=f"🎤 {response_text[:100]}",
                            parse_mode='Markdown',
                            reply_to_message_id=update.message.message_id
                        )
                    await add_reaction(update, "💕")
                    return  # Voice sent successfully
                except Exception as voice_err:
                    logger.error(f"Voice send failed: {voice_err}")
            
            # Voice failed → Text fallback
            await update.message.reply_text(
                f"🎤 {response_text}\n\n*(voice banane mein thodi dikkat 😅)*",
                parse_mode='Markdown',
                reply_to_message_id=update.message.message_id
            )
        else:
            # No AI voice → Text reply
            await update.message.reply_text(
                f"🎤 {response_text}",
                reply_to_message_id=update.message.message_id
            )
            
    except Exception as e:
        logger.error(f"Voice handler error: {e}", exc_info=True)
        await update.message.reply_text(
            "voice crash ho gaya 💀\ntext message try kar le!",
            reply_to_message_id=update.message.message_id
        )
    finally:
        # Cleanup files
        for path in [file_path, voice_reply_path, "temp_transcribe.wav", "temp.wav"]:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                    logger.debug(f"Cleaned: {path}")
                except:
                    pass

# ===== ERROR HANDLER =====
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Update {update} caused error {context.error}")

# ===== MAIN APPLICATION =====
def main():
    load_training_data()
    
    async def periodic_save():
        while True:
            await asyncio.sleep(1800)
            save_training_to_file()
    
    loop = asyncio.get_event_loop()
    loop.create_task(periodic_save())
    
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # All handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("language", language_command))
    application.add_handler(CommandHandler("clear", clear_command))
    application.add_handler(CommandHandler("kick", kick_command))
    application.add_handler(CommandHandler("ban", ban_command))
    application.add_handler(CommandHandler("unban", unban_command))
    application.add_handler(CommandHandler("mute", mute_command))
    application.add_handler(CommandHandler("unmute", unmute_command))
    application.add_handler(CommandHandler("promote", promote_command))
    application.add_handler(CommandHandler("demote", demote_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("purge", purge_command))
    application.add_handler(CommandHandler("tagall", tagall_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("id", id_command))
    application.add_handler(CommandHandler("alive", alive_command))
    application.add_handler(CommandHandler("setwelcome", setwelcome_command))
    application.add_handler(CommandHandler("welcome", welcome_toggle))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    application.add_error_handler(error_handler)

    # Flask server
    port = int(os.environ.get("PORT", 10000))
    from threading import Thread
    
    def run_flask():
        app.run(host="0.0.0.0", port=port, debug=False)
    
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    print("🌐 Flask running on port", port)
    print("🚀 Senorita Bot Starting...")
    print("🎙️ Voice Reply:", "AI Girl Voice 🇮🇳" if USE_AI_VOICE else "Text Only")
    print("🧠 Hinglish Mode: ACTIVE")
    print("📱 All 20+ features loaded!")
    
    application.run_polling(
        allowed_updates=Update.ALL_TYPES, 
        drop_pending_updates=True, 
        poll_interval=1.0
    )

if __name__ == "__main__":
    nest_asyncio.apply()
    main()
