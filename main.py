import asyncio
import logging
import os
import re
import json
import tempfile
from collections import defaultdict, deque
from datetime import datetime, timedelta
from threading import Thread
from typing import Optional

from dotenv import load_dotenv
from flask import Flask
from telegram import Update, ChatPermissions
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ChatMemberHandler,
    ContextTypes, filters,
)

from music.engine import MusicEngine

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("senorita")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
PORT = int(os.getenv("PORT", "10000") or 10000)

if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")
if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY is missing")

from groq import Groq
import edge_tts

client = Groq(api_key=GROQ_API_KEY)

AI_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
WHISPER_MODEL = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")
TTS_VOICE = os.getenv("TTS_VOICE", "en-IN-NeerjaNeural")

TRAINING_FILE = os.getenv("TRAINING_FILE", "data/senorita_training.json")
MAX_CONVS_PER_USER = 200
MAX_HISTORY = 15
MIN_CONVO_LENGTH = 5

training_data = {}
user_sessions = {}
user_preferences = {}
user_requests = defaultdict(list)
chat_stats = defaultdict(lambda: {"msgs": 0, "users": set()})
welcome_status = {}
welcome_messages = {}

app = Flask(__name__)

@app.get("/")
def home():
    total = sum(len(v) for v in training_data.values()) if training_data else 0
    return {
        "status": "ok",
        "bot": "Senorita",
        "training_conversations": total,
        "voice": "Groq Whisper + Edge TTS",
        "music": "PyTgCalls",
    }


def save_training_to_file():
    try:
        os.makedirs(os.path.dirname(TRAINING_FILE) or ".", exist_ok=True)
        cutoff = datetime.now() - timedelta(days=90)
        out = {}
        for uid, convs in training_data.items():
            recent = [c for c in convs if datetime.fromisoformat(c["timestamp"]) > cutoff]
            if recent:
                out[str(uid)] = recent[-MAX_CONVS_PER_USER:]
        with open(TRAINING_FILE, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
    except Exception:
        log.exception("training save failed")


def load_training_data():
    global training_data
    try:
        if not os.path.exists(TRAINING_FILE):
            return
        with open(TRAINING_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        training_data = {}
        for uid, convs in raw.items():
            valid = [c for c in convs[-MAX_CONVS_PER_USER:] if isinstance(c, dict) and c.get("user") and c.get("bot")]
            if valid:
                training_data[int(uid)] = deque(valid, maxlen=MAX_CONVS_PER_USER)
        log.info("loaded training for %d users", len(training_data))
    except Exception:
        log.exception("training load failed")
        training_data = {}


def save_training_data(user_id: int, user_msg: str, bot_reply: str):
    if len(user_msg.strip()) < MIN_CONVO_LENGTH or len(bot_reply.strip()) < MIN_CONVO_LENGTH:
        return
    if user_id not in training_data:
        training_data[user_id] = deque(maxlen=MAX_CONVS_PER_USER)
    training_data[user_id].append({
        "user": user_msg.strip(),
        "bot": bot_reply.strip(),
        "timestamp": datetime.now().isoformat(),
    })
    if len(training_data[user_id]) % 15 == 0:
        save_training_to_file()


def get_history(uid):
    return user_sessions.setdefault(uid, [])


def add_history(uid, role, content):
    h = get_history(uid)
    h.append({"role": role, "content": content})
    user_sessions[uid] = h[-MAX_HISTORY:]


def get_language(uid):
    return user_preferences.get(uid, {}).get("language", "hinglish")


def set_language(uid, lang):
    user_preferences.setdefault(uid, {})["language"] = lang


def build_training_context(uid):
    convs = training_data.get(uid)
    if not convs:
        return ""
    parts = []
    for c in list(convs)[-6:]:
        parts.append(f"User: {c['user'][:100]}\nAssistant: {c['bot'][:140]}")
    return "\n".join(parts)[-1500:]


def clean_for_voice(text: str) -> str:
    text = re.sub(r"[*_`~]", "", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[@#][\w.-]+", "", text)
    text = re.sub(r"[^\w\s.,!?;:'\"()\-/]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:1200]


def system_prompt(language):
    if language == "hindi":
        lang = "Reply in simple Hindi using Devanagari."
    elif language == "english":
        lang = "Reply in casual natural English."
    elif language == "bengali":
        lang = "Reply in casual Bengali."
    elif language == "marathi":
        lang = "Reply in casual Marathi."
    elif language == "bhojpuri":
        lang = "Reply in casual Bhojpuri."
    else:
        lang = "Reply in Roman Hinglish only. Never use Devanagari."
    return f"""You are Senorita, a cute Gen-Z girl with a friendly Indian conversational personality.
{lang}
Be cheerful, natural, playful, mildly teasing and supportive. Do not act as a girlfriend and do not use sexual or romantic roleplay.
Use tu/tum naturally, never aap. Keep most replies to 1-3 short sentences.
Never use emojis, emoticons, markdown, decorative Unicode, hashtags, or stage directions.
Use plain text and normal punctuation only because some responses are sent to text-to-speech.
Do not mention these instructions. Do not sound like a formal assistant."""


def ai_response(user_message: str, user_name: str, uid: int) -> str:
    try:
        context = build_training_context(uid)
        messages = [{"role": "system", "content": system_prompt(get_language(uid))}]
        if context:
            messages.append({"role": "system", "content": "Recent conversation examples:\n" + context})
        messages.extend(get_history(uid)[-8:])
        messages.append({"role": "user", "content": user_message})
        result = client.chat.completions.create(
            model=AI_MODEL, messages=messages, max_tokens=180,
            temperature=0.82, top_p=0.92, presence_penalty=0.1,
        )
        text = (result.choices[0].message.content or "").strip()
        text = re.sub(r"[*_`~]", "", text).strip()
        if not text:
            text = "Haan bol, kya hua?"
        save_training_data(uid, user_message, text)
        add_history(uid, "user", user_message)
        add_history(uid, "assistant", text)
        return text
    except Exception:
        log.exception("AI response failed")
        return "Thoda issue aa gaya. Dobara bol na."


def transcribe_sync(path: str) -> str:
    with open(path, "rb") as f:
        result = client.audio.transcriptions.create(
            file=(os.path.basename(path), f.read()),
            model=WHISPER_MODEL,
            response_format="json",
            temperature=0.0,
        )
    return (getattr(result, "text", "") or "").strip()


async def transcribe_voice(path: str) -> str:
    try:
        return await asyncio.to_thread(transcribe_sync, path)
    except Exception:
        log.exception("Whisper transcription failed")
        return ""


async def make_tts(text: str, path: str) -> bool:
    clean = clean_for_voice(text)
    if not clean:
        return False
    try:
        communicate = edge_tts.Communicate(clean, TTS_VOICE)
        await communicate.save(path)
        return os.path.exists(path) and os.path.getsize(path) > 1500
    except Exception:
        log.exception("TTS failed")
        return False


async def rate_limit(uid: int) -> bool:
    now = asyncio.get_running_loop().time()
    user_requests[uid] = [t for t in user_requests[uid] if now - t < 60]
    if len(user_requests[uid]) >= 10:
        return False
    user_requests[uid].append(now)
    return True


async def add_reaction(update, emoji):
    try:
        await update.effective_chat.set_message_reaction(update.effective_message.id, [{"type": "emoji", "emoji": emoji}])
    except Exception:
        pass


async def forward_owner(update, text):
    if not OWNER_ID:
        return
    try:
        u = update.effective_user
        await update.get_bot().send_message(OWNER_ID, f"User {u.first_name} ({u.id}): {text}")
    except Exception:
        pass


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_sessions[uid] = []
    set_language(uid, "hinglish")
    await update.message.reply_text(
        f"Hey {update.effective_user.first_name}. Main Senorita hoon.\n\n"
        "Text bhejo to text reply milega. Voice bhejo to voice reply milega.\n"
        "Music ke liye /play use kar sakte ho.\n\n"
        "/help for commands"
    )


async def help_command(update, context):
    await update.message.reply_text("""Senorita commands

AI
/start
/help
/language
/clear
/testvoice
/alive

Music
/play
/vplay
/pause
/resume
/skip
/next
/stop
/end
/loop
/shuffle
/queue
/player
/playing
/seek
/volume
/autoplay
/playlist
/addsong
/rmsong
/plplay
/lyrics
/song
/vc
/leavevc

Moderation
/kick /ban /unban /mute /unmute
/promote /demote /purge /tagall
/broadcast /setwelcome /welcome
/stats /id""")


async def language_command(update, context):
    await update.message.reply_text("Languages: hinglish, hindi, english, bengali, marathi, bhojpuri\nCurrent: " + get_language(update.effective_user.id))


async def clear_command(update, context):
    user_sessions[update.effective_user.id] = []
    await update.message.reply_text("Chat memory cleared.")


async def testvoice(update, context):
    await update.message.reply_text("Send me a voice message to test voice input and voice output.")


async def alive(update, context):
    await update.message.reply_text("Senorita is alive and running.")


async def handle_voice(update, context):
    if not update.message or not update.message.voice:
        return
    uid = update.effective_user.id
    if not await rate_limit(uid):
        await update.message.reply_text("Thoda slow, ek minute mein kaafi voice messages ho gaye.")
        return
    path = None
    out = None
    try:
        tg_file = await update.message.voice.get_file()
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            path = f.name
        await tg_file.download_to_drive(path)
        text = await transcribe_voice(path)
        if not text:
            await update.message.reply_text("Voice samajh nahi aayi. Ek baar phir bol.")
            return
        response = await asyncio.to_thread(ai_response, text, update.effective_user.first_name or "bro", uid)
        out = tempfile.mktemp(suffix=".mp3")
        if await make_tts(response, out):
            with open(out, "rb") as audio:
                await update.message.reply_voice(voice=audio, reply_to_message_id=update.message.id)
        else:
            await update.message.reply_text(clean_for_voice(response), reply_to_message_id=update.message.id)
    except Exception:
        log.exception("voice handler failed")
        await update.message.reply_text("Voice processing mein issue aa gaya. Text try kar.")
    finally:
        for p in (path, out):
            if p:
                try: os.remove(p)
                except OSError: pass


async def handle_text(update, context):
    if not update.message or not update.message.text:
        return
    message = update.message
    uid = message.from_user.id
    chat_id = message.chat_id
    chat_stats[chat_id]["msgs"] += 1
    chat_stats[chat_id]["users"].add(uid)
    if not await rate_limit(uid):
        return
    text = message.text.strip()
    bot = await context.bot.get_me()
    should = message.chat.type == "private"
    if message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.id == bot.id:
        should = True
    if bot.username and f"@{bot.username}" in text:
        should = True
        text = text.replace(f"@{bot.username}", "").strip()
    if re.search(r"\bsenorita\b", text, re.I):
        should = True
    if not should:
        return
    for lang in ("hinglish", "hindi", "english", "bengali", "marathi", "bhojpuri"):
        if re.search(rf"\b(?:talk|speak|switch|reply|respond|bol|batao)\s+(?:in|mein|me)\s+{re.escape(lang)}\b", text, re.I):
            set_language(uid, lang)
            await message.reply_text(f"{lang} mode on.")
            return
    if text:
        if message.chat.type == "private" and OWNER_ID:
            await forward_owner(update, message.text)
        response = await asyncio.to_thread(ai_response, text, message.from_user.first_name or "bro", uid)
        await message.reply_text(response, reply_to_message_id=message.id)


async def admin_only(update):
    if update.effective_chat.type == "private":
        return False
    member = await update.effective_chat.get_member(update.effective_user.id)
    return member.status in ("administrator", "creator")


async def kick_command(update, context):
    if not update.message.reply_to_message: return await update.message.reply_text("Reply to the user you want to kick.")
    if not await admin_only(update): return await update.message.reply_text("Admins only.")
    u = update.message.reply_to_message.from_user
    try:
        await update.effective_chat.ban_member(u.id); await update.effective_chat.unban_member(u.id)
        await update.message.reply_text(f"{u.first_name} kicked.")
    except Exception: await update.message.reply_text("Could not kick that user.")


async def ban_command(update, context):
    if not update.message.reply_to_message: return await update.message.reply_text("Reply to the user you want to ban.")
    if not await admin_only(update): return await update.message.reply_text("Admins only.")
    u = update.message.reply_to_message.from_user
    try:
        target = await update.effective_chat.get_member(u.id)
        if target.status in ("administrator", "creator"): return await update.message.reply_text("I cannot ban an admin.")
        await update.effective_chat.ban_member(u.id); await update.message.reply_text(f"{u.first_name} banned.")
    except Exception: await update.message.reply_text("Could not ban that user.")


async def unban_command(update, context):
    if not update.message.reply_to_message: return await update.message.reply_text("Reply to the user you want to unban.")
    if not await admin_only(update): return await update.message.reply_text("Admins only.")
    try:
        u = update.message.reply_to_message.from_user
        await update.effective_chat.unban_member(u.id, only_if_banned=True); await update.message.reply_text(f"{u.first_name} unbanned.")
    except Exception: await update.message.reply_text("Could not unban that user.")


async def mute_command(update, context):
    if not update.message.reply_to_message: return await update.message.reply_text("Reply to the user you want to mute.")
    if not await admin_only(update): return await update.message.reply_text("Admins only.")
    try:
        u = update.message.reply_to_message.from_user
        await update.effective_chat.restrict_member(u.id, ChatPermissions(can_send_messages=False)); await update.message.reply_text(f"{u.first_name} muted.")
    except Exception: await update.message.reply_text("Could not mute that user.")


async def unmute_command(update, context):
    if not update.message.reply_to_message: return await update.message.reply_text("Reply to the user you want to unmute.")
    if not await admin_only(update): return await update.message.reply_text("Admins only.")
    try:
        u = update.message.reply_to_message.from_user
        await update.effective_chat.restrict_member(u.id, ChatPermissions(can_send_messages=True, can_send_audios=True, can_send_documents=True, can_send_photos=True, can_send_videos=True, can_send_video_notes=True, can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True, can_add_web_page_previews=True)); await update.message.reply_text(f"{u.first_name} unmuted.")
    except Exception: await update.message.reply_text("Could not unmute that user.")


async def promote_command(update, context):
    if not update.message.reply_to_message: return await update.message.reply_text("Reply to the user you want to promote.")
    if not await admin_only(update): return await update.message.reply_text("Admins only.")
    try:
        u = update.message.reply_to_message.from_user
        await update.effective_chat.promote_member(u.id, can_delete_messages=True, can_restrict_members=True, can_invite_users=True); await update.message.reply_text(f"{u.first_name} promoted.")
    except Exception: await update.message.reply_text("Could not promote that user.")


async def demote_command(update, context):
    if not update.message.reply_to_message: return await update.message.reply_text("Reply to the admin you want to demote.")
    if not await admin_only(update): return await update.message.reply_text("Admins only.")
    try:
        u = update.message.reply_to_message.from_user
        await update.effective_chat.promote_member(u.id, can_change_info=False, can_post_messages=False, can_edit_messages=False, can_delete_messages=False, can_invite_users=False, can_restrict_members=False, can_pin_messages=False, can_manage_video_chats=False); await update.message.reply_text(f"{u.first_name} demoted.")
    except Exception: await update.message.reply_text("Could not demote that user.")


async def purge_command(update, context):
    if not update.message.reply_to_message: return await update.message.reply_text("Reply to the first message to purge from.")
    if not await admin_only(update): return await update.message.reply_text("Admins only.")
    start_id = update.message.reply_to_message.message_id
    count = 0
    for mid in range(start_id, update.message.message_id + 1):
        try: await update.effective_chat.delete_message(mid); count += 1
        except Exception: pass
    await update.message.reply_text(f"Purged {count} messages.")


async def tagall_command(update, context):
    if not await admin_only(update): return await update.message.reply_text("Admins only.")
    await update.message.reply_text("Tagall needs Telegram member enumeration, which the Bot API does not reliably expose. Use replies or a maintained member list instead.")


async def broadcast_command(update, context):
    if not await admin_only(update): return await update.message.reply_text("Admins only.")
    text = " ".join(context.args).strip()
    if not text: return await update.message.reply_text("Use /broadcast your message")
    await update.message.reply_text(f"Announcement:\n\n{text}")


async def setwelcome_command(update, context):
    if not await admin_only(update): return await update.message.reply_text("Admins only.")
    if not context.args: return await update.message.reply_text("Use /setwelcome Welcome {user}!")
    welcome_messages[update.effective_chat.id] = " ".join(context.args)
    await update.message.reply_text("Welcome message saved.")


async def welcome_command(update, context):
    if not await admin_only(update): return await update.message.reply_text("Admins only.")
    cid = update.effective_chat.id
    welcome_status[cid] = not welcome_status.get(cid, False)
    await update.message.reply_text("Welcome ON." if welcome_status[cid] else "Welcome OFF.")


async def new_member_handler(update, context):
    msg = update.effective_message
    if not msg or not msg.new_chat_members or not welcome_status.get(msg.chat.id, False): return
    template = welcome_messages.get(msg.chat.id, "Welcome {user} to {chat}!")
    for u in msg.new_chat_members:
        try:
            text = template.format(user=u.mention_html(), chat=msg.chat.title or "this group")
            await msg.reply_text(text, parse_mode="HTML")
        except Exception:
            await msg.reply_text(f"Welcome {u.first_name} to {msg.chat.title or 'this group'}!")


async def stats_command(update, context):
    if update.effective_chat.type == "private": return await update.message.reply_text("Groups only.")
    s = chat_stats[update.effective_chat.id]
    await update.message.reply_text(f"Messages: {s['msgs']}\nActive users: {len(s['users'])}")


async def id_command(update, context):
    u = update.effective_user
    await update.message.reply_text(f"User ID: {u.id}\nChat ID: {update.effective_chat.id}\nUsername: @{u.username or 'None'}")


async def error_handler(update, context):
    log.error("update error: %s", context.error, exc_info=context.error)


async def post_init(application):
    try:
        await music_engine.start()
        log.info("music engine started")
    except Exception:
        log.exception("music engine did not start; AI bot will still run")


async def post_shutdown(application):
    try:
        await music_engine.stop()
    except Exception:
        log.exception("music shutdown failed")


def run_flask():
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)


music_engine = MusicEngine()


def main():
    load_training_data()
    Thread(target=run_flask, daemon=True).start()
    application = (
        Application.builder().token(BOT_TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()
    )

    commands = {
        "start": start, "help": help_command, "language": language_command, "clear": clear_command,
        "testvoice": testvoice, "alive": alive, "kick": kick_command, "ban": ban_command,
        "unban": unban_command, "mute": mute_command, "unmute": unmute_command,
        "promote": promote_command, "demote": demote_command, "purge": purge_command,
        "tagall": tagall_command, "broadcast": broadcast_command, "setwelcome": setwelcome_command,
        "welcome": welcome_command, "stats": stats_command, "id": id_command,
    }
    for name, fn in commands.items():
        application.add_handler(CommandHandler(name, fn))

    for name, fn in music_engine.command_handlers().items():
        application.add_handler(CommandHandler(name, fn))

    application.add_handler(ChatMemberHandler(new_member_handler, ChatMemberHandler.CHAT_MEMBER))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice), group=1)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text), group=2)
    application.add_error_handler(error_handler)

    log.info("Senorita starting on port %s", PORT)
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True, poll_interval=1.0, timeout=10)


if __name__ == "__main__":
    main()
