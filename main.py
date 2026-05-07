import os
import logging
import asyncio
import re
import random
from flask import Flask
from telegram import Update, ChatPermissions
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from groq import Groq
import speech_recognition as sr
from pydub import AudioSegment
from collections import defaultdict
import aiohttp
import json
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ===== AI VOICE SETUP (Copyright Safe) =====
AI_VOICE_API_KEY = os.environ.get("SARVAM_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
OWNER_ID = int(os.environ.get("OWNER_ID", 0))

AI_BASE_URL = "https://api.sarvam.ai"
AI_VOICE_MODEL = "sarvam-voice-1.0"
USE_AI_VOICE = AI_VOICE_API_KEY is not None

if not all([GROQ_API_KEY, TELEGRAM_BOT_TOKEN]):
    logger.error("API keys missing! 😤")
    exit(1)

if USE_AI_VOICE:
    logger.info("✅ AI Voice (Indian languages): ACTIVE")
else:
    logger.warning("⚠️ AI Voice: Add API key for voice features")

client = Groq(api_key=GROQ_API_KEY)

# ===== AI VOICE FUNCTIONS =====
async def transcribe_with_ai(audio_file_path: str, language: str = "hi") -> Optional[str]:
    if not AI_VOICE_API_KEY:
        return None
    try:
        with open(audio_file_path, 'rb') as f:
            audio_bytes = f.read()
        headers = {"Authorization": f"Bearer {AI_VOICE_API_KEY}", "Content-Type": "application/json"}
        data = {"audio": audio_bytes, "language": language, "model": AI_VOICE_MODEL, "response_format": "json"}
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{AI_BASE_URL}/v1/audio/transcriptions", headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=30)) as response:
                if response.status == 200:
                    result = await response.json()
                    return result.get("text", "").strip()
        return None
    except:
        return None

async def generate_indian_girl_voice(text: str, output_path: str) -> bool:
    if not AI_VOICE_API_KEY:
        return False
    try:
        headers = {"Authorization": f"Bearer {AI_VOICE_API_KEY}", "Content-Type": "application/json"}
        data = {"text": text[:200], "voice": "shruti", "speed": 1.1, "format": "mp3"}
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{AI_BASE_URL}/v1/audio/speech", headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=15)) as response:
                if response.status == 200:
                    with open(output_path, 'wb') as f:
                        f.write(await response.read())
                    return True
        return False
    except:
        return False

# ===== FLASK APP =====
app = Flask(__name__)

@app.route("/")
def home():
    voice_status = "✅ AI Voice: ACTIVE 🇮🇳" if USE_AI_VOICE else "❌ AI Voice: DISABLED"
    return f"Senorita bot alive 🔥\n{voice_status}\nVoice replies enabled!"

# ===== USER SESSIONS =====
user_sessions = {}
user_preferences = {}
user_requests = defaultdict(list)
chat_stats = defaultdict(lambda: {"msgs": 0, "users": set()})

def get_user_language(user_id: int) -> str:
    return user_preferences.get(user_id, {}).get("language", "hinglish")

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
    language_instruction = ""
    if language == "hinglish":
        language_instruction = "Respond in Hinglish (mix of Hindi and English). Use TU/TUM (casual). Be short, natural, Gen-Z."
    elif language == "hindi":
        language_instruction = "Respond in Hindi (Devanagari). Use TU/TUM (casual). Be short."
    elif language == "english":
        language_instruction = "Respond in English. Casual, short, natural."
    elif language == "bengali":
        language_instruction = "Respond in Bengali (Bangla). Use TUI (casual). Be short, natural."
    elif language == "marathi":
        language_instruction = "Respond in Marathi. Use TU (casual). Be short, natural."
    elif language == "bhojpuri":
        language_instruction = "Respond in Bhojpuri. Use TU (casual). Be short, natural, desi style."
    else:
        language_instruction = f"Respond in {language}."
    return f"""You are Senorita - a Gen-Z AI girl friend with advanced powers 🔥
PERSONALITY: - Use TU/TUM/TUI (casual), NOT Aap - Short responses (1-2 lines max) - Meme-savvy, slang, emojis - Slightly flirty but cute - Never formal or robotic - Like talking to a real friend
{language_instruction}
FEATURES: - Can tag anyone - Give welcomes - Track stats - Purge messages - Advanced moderation"""

def detect_gender_sync(user_name: str) -> str:
    try:
        response = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "system", "content": "You are a gender detection assistant. Based on the given name, predict gender. Respond with ONLY one word: male or female."}, {"role": "user", "content": f"What is the likely gender for the name: {user_name}"} ], max_tokens=10)
        gender = response.choices[0].message.content.strip().lower()
        return gender if gender in ["male", "female"] else "unknown"
    except:
        return "unknown"

async def detect_language_request(message: str) -> str:
    language_keywords = {"hindi": ["hindi", "hindi me", "hindi mein"], "english": ["english", "english me", "angrezi"], "hinglish": ["hinglish", "mix", "minglish"], "bengali": ["bengali", "bangla", "bengali me"], "marathi": ["marathi", "marathi me"], "bhojpuri": ["bhojpuri", "bhojpuri me"]}
    message_lower = message.lower()
    change_phrases = ["talk in", "speak in", "language", "switch to", "batao in"]
    if any(phrase in message_lower for phrase in change_phrases):
        for lang, keywords in language_keywords.items():
            if any(keyword in message_lower for keyword in keywords):
                return lang
    return ""

def get_ai_response_sync(user_message: str, user_name: str, user_id: int) -> str:
    try:
        user_gender = get_user_gender(user_id)
        if user_gender == "unknown":
            detected_gender = detect_gender_sync(user_name)
            set_user_gender(user_id, detected_gender)
            user_gender = detected_gender
        language = get_user_language(user_id)
        system_prompt = get_system_prompt(language, user_gender)
        conversation = get_conversation_history(user_id)
        messages = [{"role": "system", "content": system_prompt}]
        for msg in conversation:
            messages.append(msg)
        messages.append({"role": "user", "content": user_message})
        response = client.chat.completions.create(model="llama-3.3-70b-versatile", messages=messages, max_tokens=150, temperature=0.9, top_p=0.95)
        ai_response = response.choices[0].message.content or "haha fr 💀"
        add_to_conversation(user_id, "user", user_message)
        add_to_conversation(user_id, "assistant", ai_response)
        return ai_response
    except Exception as e:
        logger.error(f"AI Error: {str(e)}")
        return "wait what?? something glitched 💀 try again"

async def transcribe_voice(file_path: str) -> str:
    if USE_AI_VOICE:
        logger.info("🔊 Using AI voice engine...")
        user_lang = get_user_language(12345) or "hi"
        ai_result = await transcribe_with_ai(file_path, user_lang)
        if ai_result:
            return ai_result
    logger.info("🔊 Using Google Speech (backup)...")
    recognizer = sr.Recognizer()
    try:
        audio = AudioSegment.from_file(file_path)
        audio.export("temp.wav", format="wav")
        with sr.AudioFile("temp.wav") as source:
            recognizer.adjust_for_ambient_noise(source)
            audio_data = recognizer.record(source)
            text = recognizer.recognize_google(audio_data, language='hi-IN')
            return text if text else "couldn't understand that"
    except:
        return "couldn't understand that voice 😅"
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
        await update.effective_chat.set_message_reaction(message_id=update.message.message_id, reaction=[{"type": "emoji", "emoji": emoji}])
    except:
        pass

async def forward_to_owner(update: Update, text: str):
    if OWNER_ID:
        try:
            await update.get_bot().send_message(chat_id=OWNER_ID, text=f"User {update.effective_user.first_name} ({update.effective_user.id}): {text}")
        except:
            pass

# ===== COMMANDS =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id = user.id
    detected_gender = detect_gender_sync(user.first_name)
    set_user_gender(user_id, detected_gender)
    user_sessions[user_id] = []
    voice_status = "✅ AI Voice: ACTIVE 🇮🇳" if USE_AI_VOICE else "❌ AI Voice: Add API key"
    welcome_text = f"""heyy {user.first_name}! ✨

i'm **Senorita** - your AI buddy with advanced powers 😏🔥

💬 Just chat normally
🎙️ Send voice → Get **voice reply** back!
🌍 Languages: hinglish, hindi, english, bengali, marathi, bhojpuri

**{voice_status}**

**/help** for all commands 🚀"""
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    voice_info = "\n🎙️ **Voice:** Send voice → Get cute voice reply! 🇮🇳" if USE_AI_VOICE else ""
    help_text = f"""🔥 **Senorita Commands**{voice_info}

**Chat:**
/start - restart
/language - change lang  
/clear - clear chat

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

**Fun/Stats:**
/stats - chat stats
/id - your info
/alive - bot status

**Welcome:**
/setwelcome - set welcome
/welcome on/off - toggle

Chat naturally! 😏💕"""
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    current_lang = get_user_language(update.effective_user.id)
    voice_note = "\n\n🌟 Voice auto-detects language!" if USE_AI_VOICE else ""
    lang_text = f"""🌍 **Languages:**
- hinglish (default)
- hindi 
- english
- bengali ⭐
- marathi ⭐  
- bhojpuri ⭐

Current: `{current_lang}`{voice_note}

"talk in bengali" bol do! ✨"""
    await update.message.reply_text(lang_text, parse_mode='Markdown')

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_sessions[update.effective_user.id] = []
    await update.message.reply_text("chat cleared! fresh start ✨")

# ===== MODERATION (All 12 commands) =====
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
        permissions = ChatPermissions(can_send_messages=True, can_send_media_messages=True, can_send_polls=True, can_send_other_messages=True, can_add_web_page_previews=True, can_change_info=False, can_invite_users=True, can_pin_messages=False)
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
        await update.effective_chat.promote_member(user_to_promote.id, can_change_info=True, can_delete_messages=True, can_restrict_members=True)
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
        await update.effective_chat.promote_member(user_to_demote.id, can_change_info=False, can_delete_messages=False, can_restrict_members=False)
        await update.message.reply_text(f"👋 {user_to_demote.first_name} is no longer admin!")
    except:
        await update.message.reply_text("couldn't demote!")

# ===== SPECIAL FEATURES =====
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
    await update.message.reply_text(f"🚀 Senorita alive & kicking! 🔥\n{voice_status}\nAll features loaded 😏")

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

# ===== MAIN MESSAGE HANDLER =====
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
    elif "senorita" in message_text:
        should_respond = True
    if should_respond:
        user_name = message.from_user.first_name or "bro"
        if bot_username:
            user_text = message.text.replace(f"@{bot_username}", "").strip()
        else:
            user_text = message.text
        lang_request = await detect_language_request(user_text)
        if lang_request:
            set_user_language(user_id, lang_request)
            await message.reply_text(f"aight, switching to {lang_request} ✨")
            return
        await context.bot.send_chat_action(chat_id=message.chat_id, action="typing")
        response = get_ai_response_sync(user_text, user_name, user_id)
        await add_reaction(update, "🔥")
        await message.reply_text(response)

# ===== VOICE HANDLER - INDIAN GIRL VOICE REPLY =====
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    file_path = None
    voice_reply_path = None
    try:
        if not update.message.voice:
            return
        if not await rate_limit_check(update.effective_user.id):
            await update.message.reply_text("Chill kar spam mat kar 😤")
            return
        file = await update.message.voice.get_file()
        file_path = f"voice_{update.message.message_id}.ogg"
        await file.download_to_drive(file_path)
        user_name = update.effective_user.first_name or "bro"
        user_id = update.effective_user.id
        if update.message.chat.type == 'private' and OWNER_ID:
            await forward_to_owner(update, f"🎤 Voice message received")
        await add_reaction(update, "🎙️")
        transcribed_text = await transcribe_voice(file_path)
        response_text = get_ai_response_sync(transcribed_text, user_name, user_id)
        if USE_AI_VOICE:
            voice_reply_path = f"reply_{update.message.message_id}.mp3"
            tts_success = await generate_indian_girl_voice(response_text, voice_reply_path)
            if tts_success and os.path.exists(voice_reply_path):
                with open(voice_reply_path, 'rb') as audio:
                    await update.message.reply_voice(audio, caption=f"**You:** `{transcribed_text[:50]}...`\n**Me:** {response_text}", parse_mode='Markdown')
                await add_reaction(update, "💕")
            else:
                await update.message.reply_text(f"**You:** `{transcribed_text}`\n\n**Me:** {response_text}", parse_mode='Markdown')
        else:
            await update.message.reply_text(f"**You:** `{transcribed_text}`\n\n**Me:** {response_text}", parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Voice error: {e}")
        await update.message.reply_text("voice processing failed lol 💀\nTry text instead!")
    finally:
        for path in [file_path, voice_reply_path]:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except:
                    pass

# ===== ERROR HANDLER =====
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Error: {context.error}")

# ===== APPLICATION BUILDER =====
application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

# All 20+ Commands
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

# ===== RUN =====
if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
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
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True, poll_interval=1.0)
