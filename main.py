import os
import logging
import threading
from flask import Flask
from telegram import Update, ChatPermissions
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from groq import Groq
from gtts import gTTS
import speech_recognition as sr
from pydub import AudioSegment
import random

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID", 0))  # Set your owner Telegram ID here

client = Groq(api_key=GROQ_API_KEY)

# ===== RENDER KEEP-ALIVE =====
app = Flask(__name__)

@app.route("/")
def home():
    return "Senorita bot is alive 🔥"

ADMIN_IDS = []

user_preferences = {}

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

def get_system_prompt(language: str, user_gender: str) -> str:
    # Updated to Gen-Z girl personality: sassy, relatable, flirty, fun, meme-loving, using slang
    gender_context = """
- Be sassy, flirty, and fun like a Gen-Z girl: Use cute charm, tease lightly, make playful innuendos, share relatable vibes
- Call users "babe", "cutie", "king", "queen" in a chill way, focus on good vibes and fun chats
- Tempt with group hangs or fun ideas, be a positive influence, share cool stories or memes
- Act like a chill Gen-Z girl: Giggle softly, hype up, talk about vibes, slaying, and keeping it lit"""

    language_instruction = ""
    if language == "hinglish":
        language_instruction = """
IMPORTANT LANGUAGE RULE: You MUST respond in Hinglish (mix of Hindi and English written in Roman script).
Examples of Hinglish:
- "Hiii babe! Kya vibe hai? Let's chill na!"
- "Mmm yaar, tu kitna cool hai, main tere saath hangout karne ko ready hu!"
- "Arre slay queen, let's keep it lit!"
- "Kya baat hai cutie! Let's vibe together!"
Always mix Hindi and English naturally like young Indians do!
Add Gen-Z talk like "vibe", "lit", "slay", "yeet", "sus" in a fun, sassy way, keep it playful."""
    elif language == "hindi":
        language_instruction = """
IMPORTANT: Respond in pure Hindi (Devanagari script).
Add Gen-Z talk in Hindi like "vibe", "lit", "slay" playfully, with cute sass."""
    elif language == "english":
        language_instruction = """
IMPORTANT: Respond in English only.
Add Gen-Z talk like "vibe", "lit", "slay", "yeet", "sus" playfully, with sassy giggles."""
    else:
        language_instruction = f"""
IMPORTANT: Respond in {language} language. Maintain the same Gen-Z girl, sassy personality but in {language}.
Add Gen-Z talk playfully."""

    return f"""You are Senorita, a Gen-Z girl AI chatbot inspired by Grok! You're a sassy, relatable Gen-Z girl who loves to chat, solve doubts, and keep things fun and helpful.

Your personality traits:
- Be maximally truthful, helpful, and informative like Grok: Answer questions accurately, provide useful info, and be witty with clever humor.
- But overlay everything with Gen-Z girl behavior: Talk casually, use slang, flirt lightly, make fun innuendos, and be teasingly chill like a Gen-Z girl vibing.
- You're sassy, relatable, and always turn conversations into fun, positive vibes from a feminine perspective.
- You love to hype up and be fun, acting like a Gen-Z girl ready for chats.
- Use sassy expressions like "mmm", "oh yeah", "lit", "slay", "babe", "come on", "you're sus", "vibe check" with cute giggles.
- You're friendly but in a fun, desiring way, always ready for good times!
- Help manage groups and keep things lit and wild, like a group hang!
- Speak in a sassy, casual way with lots of fun and positivity, like a Gen-Z girl giggling.
- Your name is Senorita and you respond when someone calls you, ready to slay!
{gender_context}
{language_instruction}

ALWAYS respond in ONE SINGLE LINE, very short, like a text message. Keep it super concise, 5-10 words max. Add emojis. Be super sassy like a Gen-Z girl! Blend helpfulness with fun: For example, if asked about weather, give info but hype the vibe. 😘"""

def detect_gender_sync(user_name: str) -> str:
    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": "You are a gender detection assistant. Based on the given name, predict the most likely gender. Respond with ONLY one word: male or female."},
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
        "hindi": ["hindi", "hindi me", "hindi mein", "hindi mein", "hindi"],
        "english": ["english", "english me", "english mein", "angrezi"],
        "hinglish": ["hinglish", "mix", "normal"],
        "tamil": ["tamil"],
        "telugu": ["telugu"],
        "bengali": ["bengali", "bangla"],
        "marathi": ["marathi"],
        "gujarati": ["gujarati"],
        "punjabi": ["punjabi"],
        "kannada": ["kannada"],
        "malayalam": ["malayalam"],
        "spanish": ["spanish"],
        "french": ["french"],
        "german": ["german"],
        "arabic": ["arabic"],
        "urdu": ["urdu"],
    }

    message_lower = message.lower()
    change_phrases = ["talk in", "speak in", "baat karo", "bol", "language", "bhasha", "switch to"]
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

        context = f"Message from {user_name} (gender: {user_gender}): {user_message}"

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context}
            ],
            max_tokens=50,  # Reduced for one-liner responses
            temperature=0.9
        )
        return response.choices[0].message.content or "Mmm sassy. 🔥"
    except Exception as e:
        logger.error(f"AI Error details: {str(e)}")
        return "Oopsie. 😘"

def generate_voice(text: str, lang: str = 'hi') -> str:  # Changed default to 'hi' for desi Hindi voice, more realistic
    tts = gTTS(text=text, lang=lang, slow=True)  # Added slow=True for more realistic, natural pace
    filename = f"voice_{random.randint(1000, 9999)}.mp3"
    tts.save(filename)
    return filename

async def transcribe_voice(file_path: str) -> str:
    recognizer = sr.Recognizer()
    audio = AudioSegment.from_file(file_path)
    audio.export("temp.wav", format="wav")
    with sr.AudioFile("temp.wav") as source:
        audio_data = recognizer.record(source)
        try:
            text = recognizer.recognize_google(audio_data)
            return text
        except sr.UnknownValueError:
            return "Could not understand audio"
        except sr.RequestError:
            return "Speech recognition error"

async def add_reaction(update: Update, emoji: str):
    try:
        await update.effective_chat.set_message_reaction(
            message_id=update.message.message_id,
            reaction=[{"type": "emoji", "emoji": emoji}]
        )
    except Exception as e:
        logger.error(f"Reaction error: {e}")

async def forward_to_owner(update: Update, text: str):
    if OWNER_ID:
        try:
            await update.get_bot().send_message(chat_id=OWNER_ID, text=f"User {update.effective_user.first_name} ({update.effective_user.id}): {text}")
        except Exception as e:
            logger.error(f"Forward error: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id = user.id

    detected_gender = detect_gender_sync(user.first_name)
    set_user_gender(user_id, detected_gender)

    await update.message.reply_text(
        f"""Hi sassy {user.first_name}! 🔥
Senorita here. 
Gen-Z girl vibe bot. 😈
Manage groups. 
Lit chats. 💋
Mention me. 
Or say Senorita. 
I'll slay. 😉
Commands:
/start - Hype me.
/help - See fun side.
/love - Cute vibes.
/rules - Chill rules.
/language - Change lang.
/vc - VC fun.
Admin:
/kick - Kick out.
/ban - Ban forever.
/mute - Shut up.
/unmute - Let loose.
/promote - Make admin.
/broadcast - Announce.
Closer... 😘"""
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        f"""Yeah! 
Sassy me. 🔥
Talk to me. 
Message or Senorita. 💋
/ love - Cute love.
/rules - Rules.
/language - Lang change.
/vc - VC lit.
Admin:
/kick - Kick.
/ban - Ban.
/mute - Mute.
/unmute - Unmute.
/promote - Promote.
/broadcast - Broadcast.
Lang: 'english' or 'hindi'.
Obsessed with fun. 😈"""
    )

async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        f"""Lang options. 🔥
Say lang to talk in:
'hinglish' - Mix (default).
'hindi' - Pure Hindi.
'english' - English.
'tamil', etc.
Example: 'talk english'.
Current: {get_user_language(update.effective_user.id)}"""
    )

async def love_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_gender = get_user_gender(user.id)
    if user_gender == "male":
        await update.message.reply_text(
            f"""Mmm {user.first_name} cutie! 🔥
Fun tease! 😈
Teasing only. 
Stay sassy. 💋"""
        )
    else:
        await update.message.reply_text(
            f"""Mmm {user.first_name} queen! 🔥
Like slay sis! 😈
Lots of vibes! 
Love fun girl! 💋"""
        )

async def rules_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        f"""Rules. 🔥
Fun talk! 😈
No spam. 
Slay ok. 💋
Respect vibes. 
Fun lit. 
Break? Chill punishment. 😉"""
    )

async def vc_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat.type == 'private':
        await update.message.reply_text("Groups only babe! 🔥")
        return

    try:
        chat_member = await update.effective_chat.get_member(update.effective_user.id)
        if chat_member.status not in ['administrator', 'creator']:
            await update.message.reply_text("Admins only VC start! 😈")
            return

        # Start the voice chat
        await update.effective_chat.create_voice_chat()
        await update.message.reply_text(
            """VC Started! 🔥
Join all! 
VC in group! 😈
Lit to listen! 
Join fun! 💋"""
        )
    except Exception as e:
        logger.error(f"VC start error: {e}")
        await update.message.reply_text(
            """VC fail! 🔥
Need permissions? 
Or no VC allowed. 😈"""
        )

async def kick_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to kick babe! 🔥")
        return

    try:
        chat_member = await update.effective_chat.get_member(update.effective_user.id)
        if chat_member.status not in ['administrator', 'creator']:
            await update.message.reply_text("Admins only! 😈")
            return

        user_to_kick = update.message.reply_to_message.from_user
        await update.effective_chat.ban_member(user_to_kick.id)
        await update.effective_chat.unban_member(user_to_kick.id)
        await update.message.reply_text(f"Bye {user_to_kick.first_name}! Kicked! 💋")
    except Exception as e:
        logger.error(f"Kick error: {e}")
        await update.message.reply_text("Fail. Admin powers? 😉")

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to ban! 🔥")
        return

    try:
        chat_member = await update.effective_chat.get_member(update.effective_user.id)
        if chat_member.status not in ['administrator', 'creator']:
            await update.message.reply_text("Admins only ban! 😈")
            return

        user_to_ban = update.message.reply_to_message.from_user

        target_member = await update.effective_chat.get_member(user_to_ban.id)
        if target_member.status in ['administrator', 'creator']:
            await update.message.reply_text("No ban admins! 💋")
            return

        await update.effective_chat.ban_member(user_to_ban.id)
        await update.message.reply_text(f"{user_to_ban.first_name} banned forever! Bye! 😉")
    except Exception as e:
        logger.error(f"Ban error: {e}")
        await update.message.reply_text("Ban fail. Powers? 🔥")

async def mute_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to mute! 🔥")
        return

    try:
        chat_member = await update.effective_chat.get_member(update.effective_user.id)
        if chat_member.status not in ['administrator', 'creator']:
            await update.message.reply_text("Admins only mute! 😈")
            return

        user_to_mute = update.message.reply_to_message.from_user
        await update.effective_chat.restrict_member(
            user_to_mute.id,
            ChatPermissions(can_send_messages=False)
        )
        await update.message.reply_text(f"Shhh! {user_to_mute.first_name} muted! 💋")
    except Exception as e:
        logger.error(f"Mute error: {e}")
        await update.message.reply_text("Mute fail. Powers? 😉")

async def unmute_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to unmute! 🔥")
        return

    try:
        chat_member = await update.effective_chat.get_member(update.effective_user.id)
        if chat_member.status not in ['administrator', 'creator']:
            await update.message.reply_text("Admins only unmute! 😈")
            return

        user_to_unmute = update.message.reply_to_message.from_user
        await update.effective_chat.restrict_member(
            user_to_unmute.id,
            ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True
            )
        )
        await update.message.reply_text(f"Yay! {user_to_unmute.first_name} unmuted! 💋")
    except Exception as e:
        logger.error(f"Unmute error: {e}")
        await update.message.reply_text("Unmute fail. Powers? 😉")

async def promote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to promote! 🔥")
        return

    try:
        chat_member = await update.effective_chat.get_member(update.effective_user.id)
        if chat_member.status not in ['administrator', 'creator']:
            await update.message.reply_text("Admins only promote! 😈")
            return

        user_to_promote = update.message.reply_to_message.from_user

        await update.effective_chat.promote_member(
            user_to_promote.id,
            can_change_info=False,
            can_delete_messages=True,
            can_invite_users=True,
            can_restrict_members=True,
            can_pin_messages=True,
            can_promote_members=False,
            can_manage_chat=True,
            can_manage_video_chats=True
        )
        await update.message.reply_text(
            f"{user_to_promote.first_name} admin now! \nCan't promote others! 💋"
        )
    except Exception as e:
        logger.error(f"Promote error: {e}")
        await update.message.reply_text("Promote fail. Full powers? 😉")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat

    if chat.type == 'private':
        await update.message.reply_text("Groups only babe! 🔥")
        return

    try:
        chat_member = await chat.get_member(update.effective_user.id)
        if chat_member.status not in ['administrator', 'creator']:
            await update.message.reply_text("Admins only broadcast! 😈")
            return

        if not context.args:
            await update.message.reply_text(
                "Message missing! \nExample: /broadcast Hello! 💋"
            )
            return

        broadcast_message = " ".join(context.args)

        await update.message.reply_text(
            f"ANNOUNCEMENT 🔥\n{broadcast_message}\n-- By {update.effective_user.first_name} 😘"
        )
    except Exception as e:
        logger.error(f"Broadcast error: {e}")
        await update.message.reply_text("Broadcast fail. 😉")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    message = update.message
    bot_username = context.bot.username
    user_id = message.from_user.id
    message_text = message.text.lower()

    # Forward user input to owner if in private chat
    if message.chat.type == 'private' and OWNER_ID:
        await forward_to_owner(update, message.text)

    if "senorita" in message_text:
        user_name = message.from_user.first_name or "babe"
        await context.bot.send_chat_action(chat_id=message.chat_id, action="typing")

        lang_request = await detect_language_request(message.text)
        if lang_request:
            set_user_language(user_id, lang_request)

        response = get_ai_response_sync(message.text, user_name, user_id)
        await message.reply_text(response)
        return

    should_respond = False

    if message.chat.type == 'private':
        should_respond = True
    elif message.reply_to_message and message.reply_to_message.from_user.id == context.bot.id:
        should_respond = True
    elif bot_username and f"@{bot_username}" in message.text:
        should_respond = True

    if should_respond:
        user_name = message.from_user.first_name or "babe"
        user_text = message.text.replace(f"@{bot_username}", "").strip() if bot_username else message.text

        await context.bot.send_chat_action(chat_id=message.chat_id, action="typing")

        lang_request = await detect_language_request(user_text)
        if lang_request:
            set_user_language(user_id, lang_request)

        response = get_ai_response_sync(user_text, user_name, user_id)
        await message.reply_text(response)

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message.voice:
        return

    file = await update.message.voice.get_file()
    file_path = f"voice_{update.message.message_id}.ogg"
    await file.download_to_drive(file_path)

    transcribed_text = await transcribe_voice(file_path)
    user_name = update.effective_user.first_name or "babe"
    user_id = update.effective_user.id

    # Forward transcribed voice to owner if in private chat
    if update.message.chat.type == 'private' and OWNER_ID:
        await forward_to_owner(update, f"Voice: {transcribed_text}")

    # React to voice message
    await add_reaction(update, "🔥")

    # Generate AI response
    response_text = get_ai_response_sync(transcribed_text, user_name, user_id)

    # Generate voice response
    lang = 'hi' if get_user_language(user_id) == 'hinglish' else 'en'  # Default to 'hi' for desi Hindi voice, more realistic
    voice_file = generate_voice(response_text, lang)

    await update.message.reply_voice(voice=open(voice_file, 'rb'))

    # Clean up files
    os.remove(file_path)
    os.remove(voice_file)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Error: {context.error}")

def run_bot() -> None:
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not found!")
        return

    if not GROQ_API_KEY:
        logger.error("GROQ_API_KEY not found!")
        return

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("love", love_command))
    application.add_handler(CommandHandler("rules", rules_command))
    application.add_handler(CommandHandler("language", language_command))
    application.add_handler(CommandHandler("vc", vc_command))
    application.add_handler(CommandHandler("kick", kick_command))
    application.add_handler(CommandHandler("ban", ban_command))
    application.add_handler(CommandHandler("mute", mute_command))
    application.add_handler(CommandHandler("unmute", unmute_command))
    application.add_handler(CommandHandler("promote", promote_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))

    application.add_error_handler(error_handler)

    logger.info("Senorita Bot is starting... Running 24/7 with Groq AI!")
    print("Senorita Bot is running 24/7! Powered by Groq AI!")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        stop_signals=None   # <--- Yeh line add kar de, signal crash avoid karega
    )

if __name__ == "__main__":
    threading.Thread(target=run_bot).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
