import os
import logging
import asyncio
from flask import Flask
from telegram import Update, ChatPermissions
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from groq import Groq
import speech_recognition as sr
from pydub import AudioSegment
from collections import defaultdict

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID", 0))
OWNER2_ID = int(os.environ.get("OWNER2_ID", 0))  # 🔥 NAYA ADDED

logger.info(f"TELEGRAM_BOT_TOKEN: {TELEGRAM_BOT_TOKEN[:10]}...")
logger.info(f"Owners: {OWNER_ID}, {OWNER2_ID}")

client = Groq(api_key=GROQ_API_KEY)

# ===== FLASK APP =====
app = Flask(__name__)

@app.route("/")
def home():
    return "Senorita bot is alive 🔥"

# ===== USER SESSIONS =====
user_sessions = {}
user_preferences = {}
group_vc_status = defaultdict(lambda: {"playing": False, "current_song": None})

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
    if len(user_sessions[user_id]) > 10:
        user_sessions[user_id] = user_sessions[user_id][-10:]

def get_system_prompt(language: str, user_gender: str) -> str:
    language_instruction = ""
    if language == "hinglish":
        language_instruction = "Respond in Hinglish (mix of Hindi and English). Use TU/TUM (casual). Be short, natural, Gen-Z."
    elif language == "hindi":
        language_instruction = "Respond in Hindi (Devanagari). Use TU/TUM (casual). Be short."
    elif language == "english":
        language_instruction = "Respond in English. Casual, short, natural."
    else:
        language_instruction = f"Respond in {language}."

    return f"""You are Senorita - a Gen-Z AI girl friend.

PERSONALITY:
- Use TU/TUM (casual), NOT Aap
- Short responses (1-2 lines max)
- Meme-savvy, slang, emojis
- Slightly flirty but cute
- Never formal or robotic
- Like talking to a real friend

RESPONSE STYLE:
- Keep it short (1-2 sentences)
- Use emojis (💕, 💀, 😏, 🚀, etc.)
- Use slang (fr, omg, lmao, etc.)
- Be natural, not robotic
- No long paragraphs!

EXAMPLES:
User: "Hi"
You: "heyy! kya haal hai? 💕"

User: "kaise ho?"
You: "mast yaar! tu bata? 😏"

User: "kya kar rahi hai?"
You: "bas chill kar rahi hu, tu bata? 🤷‍♀️"

User: "I'm sad"
You: "aww don't be sad 💕 I'm here for you"

Never be formal. Never write long messages. Be like a real Gen-Z girl!

{language_instruction}"""

def detect_gender_sync(user_name: str) -> str:
    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": "You are a gender detection assistant. Based on the given name, predict gender. Respond with ONLY one word: male or female."},
                {"role": "user", "content": f"What is the likely gender for the name: {user_name}"}
            ],
            max_tokens=10
        )
        gender = response.choices[0].message.content.strip().lower()
        if gender in ["male", "female"]:
            return gender
        return "unknown"
    except Exception as e:
        logger.error(f"Gender detection error: {e}")
        return "unknown"

async def detect_language_request(message: str) -> str:
    language_keywords = {
        "hindi": ["hindi", "hindi me", "hindi mein"],
        "english": ["english", "english me", "angrezi"],
        "hinglish": ["hinglish", "mix"],
    }
    message_lower = message.lower()
    change_phrases = ["talk in", "speak in", "language", "switch to"]
    is_language_request = any(phrase in message_lower for phrase in change_phrases)
    if is_language_request:
        for lang, keywords in language_keywords.items():
            for keyword in keywords:
                if keyword in message_lower:
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

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            max_tokens=150,
            temperature=0.9,
            top_p=0.95
        )
        
        ai_response = response.choices[0].message.content or "haha fr 💀"
        add_to_conversation(user_id, "user", user_message)
        add_to_conversation(user_id, "assistant", ai_response)
        return ai_response
        
    except Exception as e:
        logger.error(f"AI Error: {str(e)}")
        return "wait what?? something glitched 💀 try again"

async def transcribe_voice(file_path: str) -> str:
    recognizer = sr.Recognizer()
    try:
        audio = AudioSegment.from_file(file_path)
        audio.export("temp.wav", format="wav")
        with sr.AudioFile("temp.wav") as source:
            audio_data = recognizer.record(source)
            text = recognizer.recognize_google(audio_data)
            return text
    except:
        return "couldn't understand that"
    finally:
        if os.path.exists("temp.wav"):
            os.remove("temp.wav")

async def add_reaction(update: Update, emoji: str):
    try:
        await update.effective_chat.set_message_reaction(
            message_id=update.message.message_id,
            reaction=[{"type": "emoji", "emoji": emoji}]
        )
    except:
        pass

# 🔥 UPDATED: Dono owners ko forward karega
async def forward_to_owner(update: Update, text: str):
    owners = []
    if OWNER_ID:
        owners.append(OWNER_ID)
    if OWNER2_ID:
        owners.append(OWNER2_ID)
    
    for owner_id in owners:
        try:
            await update.get_bot().send_message(
                chat_id=owner_id, 
                text=f"👤 User {update.effective_user.first_name} ({update.effective_user.id})\n💬 {text}"
            )
        except Exception as e:
            logger.error(f"Failed to forward to owner {owner_id}: {e}")

# ===== COMMAND HANDLERS =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id = user.id
    detected_gender = detect_gender_sync(user.first_name)
    set_user_gender(user_id, detected_gender)
    user_sessions[user_id] = []
    await update.message.reply_text(f"heyy {user.first_name}! ✨\ni'm Senorita - your AI buddy 😏\n\njust chat with me! type /help for commands\nlet's gooo 🚀")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("""📚 Commands:

/start - restart bot
/help - see this
/language - change lang
/clear - clear chat

Just message me! 😏""")

async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"🌍 Languages: hinglish, hindi, english\n\nCurrent: {get_user_language(update.effective_user.id)}\n\nSay 'talk in hindi' to switch! ✨")

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_sessions[update.effective_user.id] = []
    await update.message.reply_text("chat cleared! fresh start ✨")

async def vc_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat.type == 'private':
        await update.message.reply_text("groups only baby! 😏")
        return
    try:
        chat_member = await update.effective_chat.get_member(update.effective_user.id)
        if chat_member.status not in ['administrator', 'creator']:
            await update.message.reply_text("admins only! 😤")
            return
        await update.effective_chat.create_voice_chat()
        group_vc_status[chat.id] = {"playing": False, "current_song": None}
        await update.message.reply_text("🎙️ VC Started! Join & vibe! 🎵")
    except Exception as e:
        logger.error(f"VC error: {e}")
        await update.message.reply_text("couldn't start VC 💀")

async def play_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat.type == 'private':
        await update.message.reply_text("groups only! 😏")
        return
    if not context.args:
        await update.message.reply_text("Give song name! 😤\n/play Tum Hi Ho")
        return
    song_name = " ".join(context.args)
    if chat.id not in group_vc_status:
        await update.message.reply_text("Start VC first! Use /vc command 😏")
        return
    await update.message.reply_text(f"🎵 Now Playing: {song_name}")
    group_vc_status[chat.id] = {"playing": True, "current_song": song_name}

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat.type == 'private':
        await update.message.reply_text("groups only! 😏")
        return
    try:
        chat_member = await update.effective_chat.get_member(update.effective_user.id)
        if chat_member.status not in ['administrator', 'creator']:
            await update.message.reply_text("admins only! 😤")
            return
        try:
            await update.effective_chat.delete_voice_chat()
        except:
            pass
        await update.message.reply_text("🎵 Music stopped! VC closed 👋")
        if chat.id in group_vc_status:
            del group_vc_status[chat.id]
    except Exception as e:
        logger.error(f"Stop error: {e}")
        await update.message.reply_text("couldn't stop 💀")

async def kick_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message.reply_to_message:
        await update.message.reply_text("reply to kick lol")
        return
    try:
        chat_member = await update.effective_chat.get_member(update.effective_user.id)
        if chat_member.status not in ['administrator', 'creator']:
            await update.message.reply_text("admins only lol")
            return
        user_to_kick = update.message.reply_to_message.from_user
        await update.effective_chat.ban_member(user_to_kick.id)
        await update.effective_chat.unban_member(user_to_kick.id)
        await update.message.reply_text(f"bye {user_to_kick.first_name}! 👋")
    except Exception as e:
        logger.error(f"Kick error: {e}")
        await update.message.reply_text("couldn't kick lol")

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message.reply_to_message:
        await update.message.reply_text("reply to ban lol")
        return
    try:
        chat_member = await update.effective_chat.get_member(update.effective_user.id)
        if chat_member.status not in ['administrator', 'creator']:
            await update.message.reply_text("admins only lol")
            return
        user_to_ban = update.message.reply_to_message.from_user
        target_member = await update.effective_chat.get_member(user_to_ban.id)
        if target_member.status in ['administrator', 'creator']:
            await update.message.reply_text("can't ban admins lol")
            return
        await update.effective_chat.ban_member(user_to_ban.id)
        await update.message.reply_text(f"banned {user_to_ban.first_name} 🔨")
    except Exception as e:
        logger.error(f"Ban error: {e}")
        await update.message.reply_text("couldn't ban lol")

async def mute_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message.reply_to_message:
        await update.message.reply_text("reply to mute lol")
        return
    try:
        chat_member = await update.effective_chat.get_member(update.effective_user.id)
        if chat_member.status not in ['administrator', 'creator']:
            await update.message.reply_text("admins only lol")
            return
        user_to_mute = update.message.reply_to_message.from_user
        await update.effective_chat.restrict_member(user_to_mute.id, ChatPermissions(can_send_messages=False))
        await update.message.reply_text(f"shhh {user_to_mute.first_name} 🤫")
    except Exception as e:
        logger.error(f"Mute error: {e}")
        await update.message.reply_text("couldn't mute lol")

async def unmute_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message.reply_to_message:
        await update.message.reply_text("reply to unmute lol")
        return
    try:
        chat_member = await update.effective_chat.get_member(update.effective_user.id)
        if chat_member.status not in ['administrator', 'creator']:
            await update.message.reply_text("admins only lol")
            return
        user_to_unmute = update.message.reply_to_message.from_user
        await update.effective_chat.restrict_member(user_to_unmute.id, ChatPermissions(can_send_messages=True, can_send_media_messages=True, can_send_other_messages=True, can_add_web_page_previews=True, can_invite_users=True, can_pin_messages=False, can_change_info=False, can_delete_messages=False, can_manage_chat=False, can_manage_video_chats=False))
        await update.message.reply_text(f"welcome back {user_to_unmute.first_name}! 🔊")
    except Exception as e:
        logger.error(f"Unmute error: {e}")
        await update.message.reply_text("couldn't unmute lol")

async def promote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message.reply_to_message:
        await update.message.reply_text("reply to promote lol")
        return
    try:
        chat_member = await update.effective_chat.get_member(update.effective_user.id)
        if chat_member.status not in ['administrator', 'creator']:
            await update.message.reply_text("admins only lol")
            return
        user_to_promote = update.message.reply_to_message.from_user
        await update.effective_chat.promote_member(user_to_promote.id, can_change_info=True, can_delete_messages=True, can_invite_users=True, can_restrict_members=True, can_pin_messages=True, can_promote_members=True, can_manage_chat=True, can_manage_video_chats=True)
        await update.message.reply_text(f"{user_to_promote.first_name} is now admin! 🎉")
    except Exception as e:
        logger.error(f"Promote error: {e}")
        await update.message.reply_text("couldn't promote lol")

async def demote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message.reply_to_message:
        await update.message.reply_text("reply to demote lol")
        return
    try:
        chat_member = await update.effective_chat.get_member(update.effective_user.id)
        if chat_member.status not in ['administrator', 'creator']:
            await update.message.reply_text("admins only lol")
            return
        user_to_demote = update.message.reply_to_message.from_user
        target_member = await update.effective_chat.get_member(user_to_demote.id)
        if target_member.status == 'creator':
            await update.message.reply_text("can't demote creator lol")
            return
        await update.effective_chat.promote_member(user_to_demote.id, can_change_info=False, can_delete_messages=False, can_invite_users=False, can_restrict_members=False, can_pin_messages=False, can_promote_members=False, can_manage_chat=False, can_manage_video_chats=False)
        await update.message.reply_text(f"{user_to_demote.first_name} is no longer admin 👋")
    except Exception as e:
        logger.error(f"Demote error: {e}")
        await update.message.reply_text("couldn't demote lol")

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
    except Exception as e:
        logger.error(f"Broadcast error: {e}")
        await update.message.reply_text("failed lol")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    message = update.message
    bot_username = context.bot.username
    user_id = message.from_user.id
    message_text = message.text.lower()
    
    # 🔥 UPDATED: Dono owners ko forward (OWNER_ID or OWNER2_ID check)
    if message.chat.type == 'private' and (OWNER_ID or OWNER2_ID):
        await forward_to_owner(update, message.text)
    
    should_respond = False
    if message.chat.type == 'private':
        should_respond = True
    elif message.reply_to_message and message.reply_to_message.from_user.id == context.bot.id:
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

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    file_path = None  # 🔥 FIXED: Scope issue
    try:
        if not update.message.voice:
            return
        file = await update.message.voice.get_file()
        file_path = f"voice_{update.message.message_id}.ogg"
        await file.download_to_drive(file_path)
        transcribed_text = await transcribe_voice(file_path)
        user_name = update.effective_user.first_name or "bro"
        user_id = update.effective_user.id
        
        # 🔥 UPDATED: Dono owners ko forward (OWNER_ID or OWNER2_ID check)
        if update.message.chat.type == 'private' and (OWNER_ID or OWNER2_ID):
            await forward_to_owner(update, f"🎤 Voice: {transcribed_text}")
        
        await add_reaction(update, "🎙️")
        response_text = get_ai_response_sync(transcribed_text, user_name, user_id)
        await update.message.reply_text(response_text)
    except Exception as e:
        logger.error(f"Voice error: {e}")
        await update.message.reply_text("voice processing failed lol 💀")
    finally:
        if file_path and os.path.exists(file_path):  # 🔥 FIXED: Proper cleanup
            os.remove(file_path)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Error: {context.error}")

# ===== BUILD APPLICATION =====
application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_command))
application.add_handler(CommandHandler("language", language_command))
application.add_handler(CommandHandler("clear", clear_command))
application.add_handler(CommandHandler("vc", vc_command))
application.add_handler(CommandHandler("play", play_command))
application.add_handler(CommandHandler("stop", stop_command))
application.add_handler(CommandHandler("kick", kick_command))
application.add_handler(CommandHandler("ban", ban_command))
application.add_handler(CommandHandler("mute", mute_command))
application.add_handler(CommandHandler("unmute", unmute_command))
application.add_handler(CommandHandler("promote", promote_command))
application.add_handler(CommandHandler("demote", demote_command))
application.add_handler(CommandHandler("broadcast", broadcast_command))

application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
application.add_handler(MessageHandler(filters.VOICE, handle_voice))

application.add_error_handler(error_handler)

# ===== RUN =====
if __name__ == "__main__":
    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        from threading import Thread
        flask_thread = Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), debug=False, use_reloader=False))
        flask_thread.daemon = True
        flask_thread.start()
        
        logger.info("🚀 Starting Senorita Bot with polling...")
        logger.info("🤖 Bot is running! Send /start to your bot.")
        logger.info(f"📱 Owners monitoring: {OWNER_ID}, {OWNER2_ID}")
        
        loop.run_until_complete(application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        ))
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    finally:
        loop.close()
