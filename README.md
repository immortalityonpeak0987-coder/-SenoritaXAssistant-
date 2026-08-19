# Senorita Bot

A single Telegram bot combining:

- Cute Gen-Z girl AI personality (not girlfriend roleplay)
- Text input -> text output
- Voice input -> Groq Whisper -> AI -> Edge TTS -> voice output
- Group moderation commands
- Telegram voice-chat music playback
- Flask health endpoint for Render/UptimeRobot

## Important deployment note

The Telegram bot uses python-telegram-bot for Bot API polling. Music voice-chat playback uses a separate Telegram user/assistant session through Pyrogram + PyTgCalls. This avoids running two getUpdates clients with the same bot token.

## Render

Create a Render Web Service from this repository.

Build command:

    pip install -r requirements.txt

Start command:

    gunicorn main:app --timeout 120 --workers 1 --threads 4

Add the environment variables from `.env.example` in Render. Do not upload your real `.env` or StringSession to GitHub.

FFmpeg is required by the voice-chat streaming stack. If your Render image does not already provide it, install it with a native build environment or use a Docker deployment. The included `render.yaml` documents the expected service settings.

## Voice

Groq currently supports `whisper-large-v3-turbo` and `whisper-large-v3` for multilingual speech-to-text. The bot defaults to `whisper-large-v3-turbo`.

AI responses are deliberately plain text: no emojis, markdown or decorative Unicode. This keeps Edge TTS input clean.

## Music

Start a Telegram group voice chat first, then use:

    /play song name
    /vplay video name
    /pause
    /resume
    /skip
    /stop
    /queue
    /shuffle
    /loop
    /volume 50
    /autoplay
    /vc
    /leavevc

YouTube/search and direct supported URLs are handled through yt-dlp. Spotify track URLs can be resolved to a searchable title when Spotify credentials are configured.

## Moderation

    /kick /ban /unban /mute /unmute
    /promote /demote /purge /tagall
    /broadcast /setwelcome /welcome
    /stats /id

## License / source note

This project was written as a clean integration rather than a copy of another bot's source tree or branding. It uses standard public libraries and independently implements the integration layer. Third-party library licenses remain applicable to those libraries.
