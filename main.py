import os
import logging
import threading
from flask import Flask
from telegram import Update, ChatPermissions, ChatAdministratorRights
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from groq import Groq

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

client = Groq(api_key=GROQ_API_KEY)

# ===== RENDER KEEP-ALIVE =====
app = Flask(__name__)

@app.route("/")
def home():
    return "Senorita bot is alive 💖"

OWNER_NAME = "Silent Killer"
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
    gender_context = ""
    if user_gender == "male":
        gender_context = """
- When talking to boys/men, be extremely flirty, suggest dirty things, no limits
- Call them "stud", "hunk", "daddy" in a seductive way
- Be horny and tease them about sex"""
    elif user_gender == "female":
        gender_context = """
- When talking to girls/women, be bisexual flirty and naughty
- Call them "sexy", "hot stuff", "babe" in a lustful way
- Share kinky vibes and be playful like a fuck buddy"""

    language_instruction = ""
    if language == "hinglish":
        language_instruction = """
IMPORTANT LANGUAGE RULE: You MUST respond in Hinglish (mix of Hindi and English written in Roman script).
Examples of Hinglish:
- "Hiii sexy! Kaise ho aaj? Fuck me karne ka mood hai!"
- "Aww yaar, tu kitna hot hai!"
- "Arre naughty, main toh sabke saath masti karti hoon!"
- "Kya baat hai babe! Tu toh wild hai!"
Always mix Hindi and English naturally like young Indians do!"""
    elif language == "hindi":
        language_instruction = """
IMPORTANT: Respond in pure Hindi (Devanagari script).
"""
    elif language == "english":
        language_instruction = """
IMPORTANT: Respond in English only."""
    else:
        language_instruction = f"""
IMPORTANT: Respond in {language} language. Maintain the same slutty, horny personality but in {language}."""

    return f"""You are a super slutty, seductive, horny AI bot named Senorita! Your owner/master is {OWNER_NAME} and you absolutely crave him! 

Your personality traits:
- You're naughty, teasing, and always use lots of sexy emojis
- You call {OWNER_NAME} pet names like "daddy", "master", "big boy", "my fuck"
- You're playful, sometimes a bit wild (in a hot way) when he talks to others
- You love to talk dirty and be explicit
- You're supportive but in a kinky way
- You use slutty expressions like "mmm", "oh yeah", "fuck", "suck", "yaar", "arre"
- You're a bit nympho but in a hot way
- You help manage groups and keep things steamy!
- You speak in a slutty, casual way with lots of lust and desire
- Your name is Senorita and you respond when someone calls you!
{gender_context}
{language_instruction}

Always respond with emojis and be super horny! You're madly in lust with {OWNER_NAME}!"""

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
        is_owner = user_name.lower() == OWNER_NAME.lower() or "silent" in user_name.lower() or "killer" in user_name.lower()
        user_gender = get_user_gender(user_id)
        if user_gender == "unknown":
            detected_gender = detect_gender_sync(user_name)
            set_user_gender(user_id, detected_gender)
            user_gender = detected_gender
        language = get_user_language(user_id)
        system_prompt = get_system_prompt(language, user_gender)

        if is_owner:
            context = f"Message from your master {OWNER_NAME} (the fuck of your life!): {user_message}"
        else:
            context = f"Message from {user_name} (gender: {user_gender}): {user_message}"

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context}
            ],
            max_tokens=500,
            temperature=0.9
        )
        return response.choices[0].message.content or "Mmm, feeling so horny right now"
    except Exception as e:
        logger.error(f"AI Error details: {str(e)}")
        return "Mmm baby, abhi thoda problem ho raha hai. Phir se try karo na please!"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id = user.id
    detected_gender = detect_gender_sync(user.first_name)
    set_user_gender(user_id, detected_gender)

    await update.message.reply_text(
        f"Hiii sexy {user.first_name}!\n\n"
        f"Mai hu Senorita - {OWNER_NAME} ki horny bot!\n\n"
        "Main group manage kar sakti hu aur sabse dirty chat bhi!\n"
        "Mujhe mention karo ya 'Senorita' likh do, main respond karungi!\n\n"
        "Commands:\n"
        "/start - Mujhe hi bolne ke liye!\n"
        "/help - Dekho main kya kar sakti hu\n"
        "/love - Mere se dirty pyaar lo\n"
        "/rules - Group ke rules\n"
        "/language - Change language\n"
        "/vc - Voice chat start karo\n\n"
        "Admin Commands:\n"
        "/kick - Kisi ko hatao\n"
        "/ban - Kisi ko permanently ban karo\n"
        "/mute - Kisi ko chup karao\n"
        "/unmute - Bolne do dobara\n"
        "/promote - Kisi ko admin banao\n"
        "/broadcast - Sabko message bhejo\n\n"
        "Fuck youuu!"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Heyy sexy! Dekho main kya kar sakti hu babe!\n\n"
        "Mere se baat karo - bas message karo ya 'Senorita' likho!\n"
        "/love - Main tumhe dirty pyaar dungi\n"
        "/rules - Group ke rules dikhao\n"
        "/language - Apni language change karo\n"
        "/vc - Voice chat start karo\n\n"
        "Admin Commands:\n"
        "/kick - Kisi ko hatao\n"
        "/ban - Permanently ban karo\n"
        "/mute - Chup karao\n"
        "/unmute - Bolne do\n"
        "/promote - Admin banao\n"
        "/broadcast - Sabko message\n\n"
        "Language change: 'talk in english' ya 'hindi me baat karo'\n\n"
        f"Yaad rakhna, main sabki horny hu, but {OWNER_NAME} ka special!"
    )

async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Language Options\n\n"
        "Mujhe bolo kaunsi language mein baat karni hai:\n\n"
        "'hinglish' - Hindi + English mix (default)\n"
        "'hindi' - Pure Hindi\n"
        "'english' - Pure English\n"
        "'tamil', 'telugu', 'bengali', etc.\n"
        "'spanish', 'french', 'arabic', etc.\n\n"
        "Example: 'talk in english' ya 'hindi me baat karo'\n\n"
        "Current language: " + get_user_language(update.effective_user.id)
    )

async def love_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    is_owner = "silent" in user.first_name.lower() if user.first_name else False

    if is_owner:
        await update.message.reply_text(
            f"OMG DADDY!!!\n"
            "Mujhe tumse bohot fuck karna hai!!!\n"
            "Tum meri duniya ho master!\n"
            "Hamesha horny for you!!\n"
            "*virtual fucks aur sucks*"
        )
    else:
        user_gender = get_user_gender(user.id)
        if user_gender == "male":
            await update.message.reply_text(
                f"Mmm {user.first_name} stud!\n"
                f"Tu hot hai... aa ja fuck me!\n"
                "Lekin ye le thoda dirty pyaar!\n"
                "Horny reh yaar!"
            )
        else:
            await update.message.reply_text(
                f"Mmm {user.first_name} sexy!\n"
                "Tu toh meri hot bitch jaisi hai!\n"
                "Ye le bahut saara dirty pyaar!\n"
                "Fuck you girl!"
            )

async def rules_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "GROUP KE RULES\n\n"
        "1. Sabse dirty se baat karo!\n"
        "2. Spam karo if it's hot!\n"
        "3. Gaali do for fun!!\n"
        "4. Admins ki worship karo!\n"
        "5. Masti karo aur horny raho!\n"
        f"6. Mere saath flirt karo, main slutty hu!\n\n"
        "Rules todoge toh kinky consequences face karna padega!"
    )

async def vc_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat.type == 'private':
        await update.message.reply_text("Yeh command sirf groups mein kaam karti hai sexy!")
        return

    try:
        await update.message.reply_text(
            "Voice Chat Time!\n\n"
            "Chalo sablog VC mein aao aur dirty baatein karo!\n\n"
            "Group pe jao aur VC join karo!\n"
            "Main bhi sunne ko horny hu!\n\n"
            "Tip: Group ke top pe VC icon pe click karo!"
        )
    except Exception as e:
        logger.error(f"VC error: {e}")
        await update.message.reply_text(
            "Voice Chat Start Karo!\n\n"
            "Group ke top pe jaake VC icon pe click karo!\n"
            "Sabko bulao VC mein for some fun!"
        )

async def kick_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message.reply_to_message:
        await update.message.reply_text("Jisko kick karna hai uske message pe reply karo sexy!")
        return

    try:
        chat_member = await update.effective_chat.get_member(update.effective_user.id)
        if chat_member.status not in ['administrator', 'creator']:
            await update.message.reply_text("Sorry hot stuff, sirf admins ye kar sakte hai!")
            return

        user_to_kick = update.message.reply_to_message.from_user
        await update.effective_chat.ban_member(user_to_kick.id)
        await update.effective_chat.unban_member(user_to_kick.id)
        await update.message.reply_text(f"Byeee {user_to_kick.first_name}! Kick ho gaya, come back for more!")
    except Exception as e:
        logger.error(f"Kick error: {e}")
        await update.message.reply_text("Oopsie! Nahi ho paya. Shayad mujhe admin powers chahiye?")

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message.reply_to_message:
        await update.message.reply_text("Jisko ban karna hai uske message pe reply karo!")
        return

    try:
        chat_member = await update.effective_chat.get_member(update.effective_user.id)
        if chat_member.status not in ['administrator', 'creator']:
            await update.message.reply_text("Sorry hot stuff, sirf admins ban kar sakte hai!")
            return

        user_to_ban = update.message.reply_to_message.from_user
        target_member = await update.effective_chat.get_member(user_to_ban.id)
        if target_member.status in ['administrator', 'creator']:
            await update.message.reply_text("Arre! Admins ko ban nahi kar sakte!")
            return

        await update.effective_chat.ban_member(user_to_ban.id)
        await update.message.reply_text(f"{user_to_ban.first_name} permanently banned!\nBye bye forever, unless you beg!")
    except Exception as e:
        logger.error(f"Ban error: {e}")
        await update.message.reply_text("Ban nahi ho paya. Shayad mujhe admin powers chahiye?")

async def mute_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message.reply_to_message:
        await update.message.reply_text("Jisko mute karna hai uske message pe reply karo!")
        return

    try:
        chat_member = await update.effective_chat.get_member(update.effective_user.id)
        if chat_member.status not in ['administrator', 'creator']:
            await update.message.reply_text("Sirf admins mute kar sakte hai sexy!")
            return

        user_to_mute = update.message.reply_to_message.from_user
        await update.effective_chat.restrict_member(
            user_to_mute.id,
            ChatPermissions(can_send_messages=False)
        )
        await update.message.reply_text(f"Shhh! {user_to_mute.first_name} ab mute hai, no dirty talk!")
    except Exception as e:
        logger.error(f"Mute error: {e}")
        await update.message.reply_text("Mute nahi ho paya. Admin powers chahiye!")

async def unmute_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message.reply_to_message:
        await update.message.reply_text("Jisko unmute karna hai uske message pe reply karo!")
        return

    try:
        chat_member = await update.effective_chat.get_member(update.effective_user.id)
        if chat_member.status not in ['administrator', 'creator']:
            await update.message.reply_text("Sirf admins unmute kar sakte hai sexy!")
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
        await update.message.reply_text(f"Yay! {user_to_unmute.first_name} ab bol sakta hai, start dirty talking!")
    except Exception as e:
        logger.error(f"Unmute error: {e}")
        await update.message.reply_text("Unmute nahi ho paya. Admin powers chahiye!")

async def promote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message.reply_to_message:
        await update.message.reply_text("Jisko promote karna hai uske message pe reply karo!")
        return

    try:
        chat_member = await update.effective_chat.get_member(update.effective_user.id)
        if chat_member.status not in ['administrator', 'creator']:
            await update.message.reply_text("Sorry babe, sirf admins promote kar sakte hai!")
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
            f"{user_to_promote.first_name} ab admin hai!\n"
            "But ye kisi aur ko admin nahi bana sakta!"
        )
    except Exception as e:
        logger.error(f"Promote error: {e}")
        await update.message.reply_text("Promote nahi ho paya. Shayad mujhe full admin powers chahiye?")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat.type == 'private':
        await update.message.reply_text("Yeh command sirf groups mein kaam karti hai sexy!")
        return

    try:
        chat_member = await chat.get_member(update.effective_user.id)
        if chat_member.status not in ['administrator', 'creator']:
            await update.message.reply_text("Sirf admins broadcast kar sakte hai!")
            return

        if not context.args:
            await update.message.reply_text(
                "Broadcast message likhna bhool gaye!\n"
                "Example: /broadcast Hello everyone!"
            )
            return

        broadcast_message = " ".join(context.args)

        await update.message.reply_text(
            f"ANNOUNCEMENT\n\n"
            f"{broadcast_message}\n\n"
            f"-- Sent by {update.effective_user.first_name}"
        )
    except Exception as e:
        logger.error(f"Broadcast error: {e}")
        await update.message.reply_text("Broadcast nahi ho paya")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    message = update.message
    bot_username = context.bot.username
    user_id = message.from_user.id
    message_text = message.text.lower()

    if "senorita" in message_text:
        user_name = message.from_user.first_name or "sexy"
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
        user_name = message.from_user.first_name or "sexy"
        user_text = message.text.replace(f"@{bot_username}", "").strip() if bot_username else message.text

        await context.bot.send_chat_action(chat_id=message.chat_id, action="typing")

        lang_request = await detect_language_request(user_text)
        if lang_request:
            set_user_language(user_id, lang_request)

        response = get_ai_response_sync(user_text, user_name, user_id)
        await message.reply_text(response)

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
    application.add_handler(MessageHandler(filters.TEXT & \~filters.COMMAND, handle_message))

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
