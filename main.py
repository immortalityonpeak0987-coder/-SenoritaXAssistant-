import asyncio
import logging
import os
import re
import json
import tempfile
import random
from io import BytesIO
from collections import defaultdict, deque
from datetime import datetime, timedelta
from threading import Thread
from typing import Optional

from dotenv import load_dotenv
from flask import Flask
from telegram import Update, ChatPermissions, ChatAdministratorRights
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ChatMemberHandler,
    ContextTypes, filters,
)

from music.engine import MusicEngine
from PIL import Image, ImageDraw, ImageFont

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
seen_members = defaultdict(dict)
served_chats = set()
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
    served_chats.add(update.effective_chat.id)
    seen_members[update.effective_chat.id][uid] = {"id":uid,"first_name":update.effective_user.first_name or "User","is_bot":update.effective_user.is_bot}
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
/stats /id

Fun
/love /couples /hug /pat /slap /truth /dare
/8ball /dice /coin /cute /wish /mmf""")


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
    served_chats.add(chat_id)
    seen_members[chat_id][uid] = {"id":uid,"first_name":message.from_user.first_name or "User","username":message.from_user.username,"is_bot":message.from_user.is_bot}
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



async def admin_only(update, required=None):
    if update.effective_chat.type == "private":
        return False
    try:
        member = await update.effective_chat.get_member(update.effective_user.id)
        if member.status == "creator":
            return True
        if member.status != "administrator":
            return False
        return True if required is None else bool(getattr(member, required, False))
    except Exception:
        return False


async def resolve_target(update, context):
    msg = update.effective_message
    if msg.reply_to_message and msg.reply_to_message.from_user:
        return msg.reply_to_message.from_user
    if context.args:
        raw = context.args[0].lstrip("@")
        try:
            uid = int(raw)
            return (await update.effective_chat.get_member(uid)).user
        except Exception:
            try:
                return await context.bot.get_chat("@" + raw)
            except Exception:
                return None
    return None


async def protected_target(update, user_id):
    if user_id == update.get_bot().id:
        return "I cannot moderate myself."
    try:
        member = await update.effective_chat.get_member(user_id)
        if member.status in ("creator", "administrator"):
            return "I won't moderate another admin."
    except Exception:
        pass
    return None


def command_reason(context):
    return " ".join(context.args[1:]).strip() if len(context.args) > 1 else ""


async def kick_command(update, context):
    if not await admin_only(update, "can_restrict_members"):
        return await update.message.reply_text("You need admin rights with restrict-members permission.")
    u = await resolve_target(update, context)
    if not u: return await update.message.reply_text("Reply to a user or use /kick @username.")
    p = await protected_target(update, u.id)
    if p: return await update.message.reply_text(p)
    try:
        await update.effective_chat.ban_member(u.id)
        await update.effective_chat.unban_member(u.id)
        await update.message.reply_text(f"{u.first_name} was kicked.")
    except Exception as e:
        log.warning("kick: %s", e)
        await update.message.reply_text("Kick failed. Check my admin permissions.")


async def ban_command(update, context):
    if not await admin_only(update, "can_restrict_members"):
        return await update.message.reply_text("You need admin rights with restrict-members permission.")
    u = await resolve_target(update, context)
    if not u: return await update.message.reply_text("Reply to a user or use /ban @username [reason].")
    p = await protected_target(update, u.id)
    if p: return await update.message.reply_text(p)
    try:
        await update.effective_chat.ban_member(u.id)
        reason = command_reason(context)
        await update.message.reply_text(f"{u.first_name} was banned." + (f"\nReason: {reason}" if reason else ""))
    except Exception as e:
        log.warning("ban: %s", e)
        await update.message.reply_text("Ban failed. Check my admin permissions.")


async def unban_command(update, context):
    if not await admin_only(update, "can_restrict_members"):
        return await update.message.reply_text("You need admin rights with restrict-members permission.")
    u = await resolve_target(update, context)
    if not u: return await update.message.reply_text("Reply to a banned user or use /unban USER_ID.")
    try:
        await update.effective_chat.unban_member(u.id, only_if_banned=True)
        await update.message.reply_text(f"{u.first_name} was unbanned.")
    except Exception as e:
        log.warning("unban: %s", e)
        await update.message.reply_text("Unban failed. Check the user ID and my permissions.")


async def mute_command(update, context):
    if not await admin_only(update, "can_restrict_members"):
        return await update.message.reply_text("You need admin rights with restrict-members permission.")
    u = await resolve_target(update, context)
    if not u: return await update.message.reply_text("Reply to a user or use /mute @username [reason].")
    p = await protected_target(update, u.id)
    if p: return await update.message.reply_text(p)
    try:
        await update.effective_chat.restrict_member(u.id, ChatPermissions(can_send_messages=False))
        reason = command_reason(context)
        await update.message.reply_text(f"{u.first_name} was muted." + (f"\nReason: {reason}" if reason else ""))
    except Exception as e:
        log.warning("mute: %s", e)
        await update.message.reply_text("Mute failed. Check my restrict permission.")


async def unmute_command(update, context):
    if not await admin_only(update, "can_restrict_members"):
        return await update.message.reply_text("You need admin rights with restrict-members permission.")
    u = await resolve_target(update, context)
    if not u: return await update.message.reply_text("Reply to a muted user or use /unmute @username.")
    try:
        perms = ChatPermissions(
            can_send_messages=True, can_send_audios=True, can_send_documents=True,
            can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
            can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True,
            can_add_web_page_previews=True, can_invite_users=True
        )
        await update.effective_chat.restrict_member(u.id, perms)
        await update.message.reply_text(f"{u.first_name} was unmuted.")
    except Exception as e:
        log.warning("unmute: %s", e)
        await update.message.reply_text("Unmute failed. Check my restrict permission.")


async def promote_command(update, context):
    if not await admin_only(update, "can_promote_members"):
        return await update.message.reply_text("You need admin rights with promote permission.")
    u = await resolve_target(update, context)
    if not u: return await update.message.reply_text("Reply to a user or use /promote @username [title].")
    p = await protected_target(update, u.id)
    if p: return await update.message.reply_text(p)
    try:
        rights = ChatAdministratorRights(
            is_anonymous=False, can_manage_chat=True, can_delete_messages=True,
            can_manage_video_chats=True, can_restrict_members=True,
            can_promote_members=False, can_change_info=False, can_invite_users=True,
            can_post_messages=False, can_edit_messages=False, can_pin_messages=True,
            can_manage_topics=True, can_post_stories=False, can_edit_stories=False,
            can_delete_stories=False
        )
        await context.bot.promote_chat_member(update.effective_chat.id, u.id, rights)
        title = " ".join(context.args[1:]).strip() if len(context.args) > 1 else ""
        if title:
            try:
                await context.bot.set_chat_administrator_custom_title(update.effective_chat.id, u.id, title[:16])
            except Exception:
                pass
        await update.message.reply_text(f"{u.first_name} was promoted.")
    except Exception as e:
        log.warning("promote: %s", e)
        await update.message.reply_text("Promote failed. Check Telegram's admin hierarchy and my permissions.")


async def demote_command(update, context):
    if not await admin_only(update, "can_promote_members"):
        return await update.message.reply_text("You need admin rights with promote permission.")
    u = await resolve_target(update, context)
    if not u: return await update.message.reply_text("Reply to an admin or use /demote @username.")
    if u.id == update.effective_user.id:
        return await update.message.reply_text("You cannot demote yourself.")
    try:
        rights = ChatAdministratorRights(
            is_anonymous=False, can_manage_chat=False, can_delete_messages=False,
            can_manage_video_chats=False, can_restrict_members=False,
            can_promote_members=False, can_change_info=False, can_invite_users=False,
            can_post_messages=False, can_edit_messages=False, can_pin_messages=False,
            can_manage_topics=False, can_post_stories=False, can_edit_stories=False,
            can_delete_stories=False
        )
        await context.bot.promote_chat_member(update.effective_chat.id, u.id, rights)
        await update.message.reply_text(f"{u.first_name} was demoted.")
    except Exception as e:
        log.warning("demote: %s", e)
        await update.message.reply_text("Demote failed. Check Telegram's admin hierarchy and my permissions.")


async def purge_command(update, context):
    if not await admin_only(update, "can_delete_messages"):
        return await update.message.reply_text("You need admin rights with delete-message permission.")
    if not update.message.reply_to_message:
        return await update.message.reply_text("Reply to the first message to start purge.")
    first = update.message.reply_to_message.message_id
    last = update.message.message_id
    if last - first > 1000:
        return await update.message.reply_text("Maximum 1000 messages per purge.")
    ids = list(range(first, last + 1))
    deleted = 0
    for i in range(0, len(ids), 100):
        batch = ids[i:i+100]
        try:
            await context.bot.delete_messages(update.effective_chat.id, batch)
            deleted += len(batch)
        except Exception:
            for mid in batch:
                try:
                    await context.bot.delete_message(update.effective_chat.id, mid)
                    deleted += 1
                except Exception:
                    pass
    await update.message.reply_text(f"Purged {deleted} messages.")


async def iter_group_users(chat_id):
    # The MTProto assistant can enumerate members just like a music userbot.
    if music_engine.client and music_engine.started:
        try:
            async for member in music_engine.client.get_chat_members(chat_id):
                if member.user and not member.user.is_bot:
                    yield member.user
            return
        except Exception:
            log.exception("member enumeration failed")
    for u in seen_members.get(chat_id, {}).values():
        if not u.get("is_bot"):
            yield type("SeenUser", (), u)()


async def tagall_command(update, context):
    if not await admin_only(update, "can_delete_messages"):
        return await update.message.reply_text("Admins only.")
    users = []
    async for u in iter_group_users(update.effective_chat.id):
        users.append(u)
        if len(users) >= 500: break
    if not users:
        return await update.message.reply_text("No members available yet. Let members send a message first.")
    prefix = " ".join(context.args).strip()
    buf = prefix
    chunks = []
    for u in users:
        mention = f'<a href="tg://user?id={u.id}">{u.first_name or "User"}</a>'
        if len(buf) + len(mention) + 1 > 3800:
            chunks.append(buf); buf = mention
        else:
            buf = f"{buf} {mention}".strip()
    if buf: chunks.append(buf)
    for chunk in chunks:
        await update.message.reply_text(chunk, parse_mode="HTML")
        await asyncio.sleep(0.7)


async def broadcast_command(update, context):
    text = " ".join(context.args).strip()
    if not text and update.message.reply_to_message:
        text = update.message.reply_to_message.text or update.message.reply_to_message.caption or ""
    if not text:
        return await update.message.reply_text("Use /broadcast your message or reply to a message.")
    uid = update.effective_user.id
    if OWNER_ID and uid == OWNER_ID:
        targets = list(served_chats)
    elif await admin_only(update):
        targets = [update.effective_chat.id]
    else:
        return await update.message.reply_text("Admins only. Global broadcast is owner-only.")
    sent = 0
    for cid in targets:
        try:
            await context.bot.send_message(cid, text)
            sent += 1
            await asyncio.sleep(0.2)
        except Exception:
            pass
    await update.message.reply_text(f"Broadcast sent to {sent} chat(s).")


async def setwelcome_command(update, context):
    if not await admin_only(update, "can_change_info"):
        return await update.message.reply_text("You need admin rights with group-info permission.")
    if not context.args:
        return await update.message.reply_text("Use /setwelcome Welcome {user} to {chat}!")
    welcome_messages[update.effective_chat.id] = " ".join(context.args)
    await update.message.reply_text("Welcome message saved.")


async def welcome_command(update, context):
    if not await admin_only(update, "can_change_info"):
        return await update.message.reply_text("You need admin rights with group-info permission.")
    cid = update.effective_chat.id
    welcome_status[cid] = not welcome_status.get(cid, False)
    await update.message.reply_text("Welcome ON." if welcome_status[cid] else "Welcome OFF.")


async def new_member_handler(update, context):
    msg = update.effective_message
    if not msg or not getattr(msg, "new_chat_members", None): return
    for u in msg.new_chat_members:
        seen_members[msg.chat.id][u.id] = {"id":u.id,"first_name":u.first_name or "User","is_bot":u.is_bot}
        if welcome_status.get(msg.chat.id, False):
            template = welcome_messages.get(msg.chat.id, "Welcome {user} to {chat}!")
            try:
                await msg.reply_text(template.format(user=u.mention_html(), chat=msg.chat.title or "this group"), parse_mode="HTML")
            except Exception:
                pass


async def stats_command(update, context):
    if update.effective_chat.type == "private":
        return await update.message.reply_text("Use /stats in a group.")
    s = chat_stats[update.effective_chat.id]
    await update.message.reply_text(
        f"Chat stats\nMessages: {s['msgs']}\nTracked users: {len(seen_members.get(update.effective_chat.id, {}))}\n"
        f"Active users: {len(s['users'])}\nMusic assistant: {'ON' if music_engine.started else 'OFF'}"
    )


async def id_command(update, context):
    u = update.message.reply_to_message.from_user if update.message.reply_to_message else update.effective_user
    await update.message.reply_text(
        f"User ID: {u.id}\nChat ID: {update.effective_chat.id}\nUsername: @{u.username or 'None'}\nName: {u.first_name or 'Unknown'}"
    )


FUN_TRUTHS = [
    "What is one skill you wish you were better at?",
    "What is the funniest thing you believed as a kid?",
    "What song have you played too many times lately?",
    "What is your most random talent?",
]
FUN_DARES = [
    "Send a message using only three words.",
    "Describe your day like a movie trailer.",
    "Reply with your best one-line joke.",
    "Type your next message without using the letter e.",
]


async def _target_or_self(update, context):
    return await resolve_target(update, context) or update.effective_user


async def love_command(update, context):
    if update.effective_chat.type == "private":
        return await update.message.reply_text("Love calculator works in groups.")
    a = await _target_or_self(update, context)
    members = [u async for u in iter_group_users(update.effective_chat.id) if u.id != a.id]
    if not members: return await update.message.reply_text("I need at least two members.")
    b = random.choice(members)
    await update.message.reply_text(f"Love calculator\n{a.first_name} + {b.first_name} = {random.randint(10,100)}%")


async def couples_command(update, context):
    if update.effective_chat.type == "private":
        return await update.message.reply_text("Couples works in groups.")
    members = [u async for u in iter_group_users(update.effective_chat.id)]
    if len(members) < 2: return await update.message.reply_text("I need at least two members.")
    a, b = random.sample(members, 2)
    await update.message.reply_text(f"Couple of the day\n{a.first_name} + {b.first_name}\nMatch score: {random.randint(40,100)}%")


async def _social_action(update, context, action):
    target = await resolve_target(update, context)
    if not target: return await update.message.reply_text(f"Reply to someone or use /{action} @username.")
    await update.message.reply_text(f"{update.effective_user.first_name} gives {target.first_name} a friendly {action}.")


async def hug_command(update, context): await _social_action(update, context, "hug")
async def pat_command(update, context): await _social_action(update, context, "pat")
async def slap_command(update, context): await _social_action(update, context, "slap")


async def truth_command(update, context): await update.message.reply_text("Truth: " + random.choice(FUN_TRUTHS))
async def dare_command(update, context): await update.message.reply_text("Dare: " + random.choice(FUN_DARES))
async def eightball_command(update, context): await update.message.reply_text(random.choice(["Definitely.","Probably.","Looks promising.","Ask again later.","Not likely.","Nope."]))
async def dice_command(update, context): await update.message.reply_text(f"Dice: {random.randint(1,6)}")
async def coin_command(update, context): await update.message.reply_text(random.choice(["Heads","Tails"]))
async def cute_command(update, context): await update.message.reply_text(f"Cuteness score: {random.randint(40,100)}%")
async def wish_command(update, context):
    wish=" ".join(context.args).strip()
    if not wish: return await update.message.reply_text("Use /wish your wish.")
    await update.message.reply_text(f"Wish noted: {wish}\nPossibility score: {random.randint(1,100)}%")


async def mmf_command(update, context):
    reply = update.message.reply_to_message
    text = " ".join(context.args).strip()
    if not reply: return await update.message.reply_text("Reply to a photo or static sticker: /mmf YOUR TEXT")
    if not text: return await update.message.reply_text("Use /mmf YOUR TEXT while replying to media.")
    if not (reply.photo or reply.sticker): return await update.message.reply_text("For now /mmf supports photos and static stickers.")
    if reply.sticker and (reply.sticker.is_animated or reply.sticker.is_video):
        return await update.message.reply_text("Animated/video stickers are not supported.")
    out = None
    try:
        file_id = reply.photo[-1].file_id if reply.photo else reply.sticker.file_id
        tg_file = await context.bot.get_file(file_id)
        raw = BytesIO()
        await tg_file.download_to_memory(raw)
        raw.seek(0)
        img = Image.open(raw).convert("RGB")
        img.thumbnail((1200,1200))
        draw = ImageDraw.Draw(img)
        try: font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", max(24,img.width//14))
        except Exception: font = ImageFont.load_default()
        top,bottom = text.split(";",1) if ";" in text else (text,"")
        def cap(s,y):
            if not s: return
            words=s.upper().split(); lines=[]; line=""
            for w in words:
                test=(line+" "+w).strip()
                if len(test)>18 and line: lines.append(line); line=w
                else: line=test
            if line: lines.append(line)
            for line in lines:
                box=draw.textbbox((0,0),line,font=font,stroke_width=3)
                x=(img.width-(box[2]-box[0]))/2
                draw.text((x,y),line,font=font,fill="white",stroke_width=3,stroke_fill="black")
                y += box[3]-box[1]+8
        cap(top,10)
        if bottom:
            box=draw.textbbox((0,0),bottom.upper(),font=font,stroke_width=3)
            draw.text(((img.width-(box[2]-box[0]))/2,img.height-(box[3]-box[1])-20),bottom.upper(),font=font,fill="white",stroke_width=3,stroke_fill="black")
        out=tempfile.mktemp(suffix=".jpg"); img.save(out,"JPEG",quality=92)
        with open(out,"rb") as f: await update.message.reply_photo(f, caption="Memified.")
    except Exception as e:
        log.exception("mmf: %s",e)
        await update.message.reply_text("Could not memify that media.")
    finally:
        if out:
            try: os.remove(out)
            except OSError: pass

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
        save_training_to_file()
    except Exception:
        log.exception("final training save failed")
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
        "love": love_command, "couples": couples_command, "hug": hug_command, "pat": pat_command,
        "slap": slap_command, "truth": truth_command, "dare": dare_command, "8ball": eightball_command,
        "dice": dice_command, "coin": coin_command, "cute": cute_command, "wish": wish_command, "mmf": mmf_command,
    }
    for name, fn in commands.items():
        application.add_handler(CommandHandler(name, fn))

    for name, fn in music_engine.command_handlers().items():
        application.add_handler(CommandHandler(name, fn))

    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_member_handler))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice), group=1)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text), group=2)
    application.add_error_handler(error_handler)

    log.info("Senorita starting on port %s", PORT)
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True, poll_interval=1.0, timeout=10)


if __name__ == "__main__":
    main()
