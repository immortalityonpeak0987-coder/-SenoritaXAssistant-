# Senorita X Assistant

A clean merged Telegram bot built around:

- Cute Gen-Z girl personality (friendly, not girlfriend roleplay)
- Text -> text AI chat
- Voice -> Groq Whisper -> AI -> Edge TTS voice reply
- Telegram group moderation
- Telegram voice-chat music playback using a separate Pyrogram assistant session
- Queue, pause, resume, skip, stop, loop, shuffle, volume, autoplay toggle
- Personal playlists
- Spotify track resolution when Spotify credentials are configured
- `/tts` text-to-speech
- Flask health endpoint for Render

## Important architecture

The Bot API uses `python-telegram-bot` and polls with the bot token.
Voice-chat playback uses a separate MTProto assistant session through
Pyrogram + PyTgCalls. Do not use the bot token as the StringSession.

## Render

Use a Docker Web Service or a normal Web Service with:

Build:
`pip install -r requirements.txt`

Start:
`python main.py`

The Flask health server binds to `0.0.0.0:$PORT` and the Telegram polling
application runs in the main process.

## Required environment variables

- `TELEGRAM_BOT_TOKEN`
- `GROQ_API_KEY`
- `OWNER_ID` (optional; use 0 if unused)

For voice-chat music:

- `API_ID`
- `API_HASH`
- `STRING_SESSION`

Optional:

- `SPOTIFY_CLIENT_ID`
- `SPOTIFY_CLIENT_SECRET`
- `YTDLP_COOKIES_B64`

Do not commit real tokens, API keys, StringSession values, or cookie data.

## Commands

AI:
`/start /help /language /clear /testvoice /alive`

Voice:
send a Telegram voice message for voice -> voice
`/tts text`

Music:
`/play /vplay /pause /resume /skip /next /stop /end`
`/loop /shuffle /queue /player /playing /volume /autoplay`
`/playlist /addsong /rmsong /plplay`
`/song /lyrics /vc /leavevc /musichelp`

Moderation:
`/kick /ban /unban /mute /unmute /promote /demote /purge /tagall`
`/broadcast /setwelcome /welcome /stats /id`

## YouTube note

YouTube can sometimes require additional verification for automated
requests. This build supports an optional `YTDLP_COOKIES_B64` environment
variable for a user-provided cookies file. A missing cookie file does not
break the rest of the bot.

## Third-party libraries

This project uses third-party libraries under their own licenses. See
`ATTRIBUTION.md` for the integration/dependency note.


## Moderation and fun extras
The bot includes robust admin checks for kick, ban, unban, mute, unmute, promote, demote, purge, tagall, broadcast, welcome, stats and ID commands. It also includes safe group fun commands such as love, couples, hug, pat, slap, truth, dare, 8ball, dice, coin, cute, wish and an image memifier (`/mmf`).

The MMF command supports photos and static stickers. Animated/video stickers are intentionally not processed by this lightweight implementation.
