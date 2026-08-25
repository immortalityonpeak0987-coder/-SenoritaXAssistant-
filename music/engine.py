"""
Senorita music/voice-chat integration.

This is an independent integration layer. It keeps the Bot API client
(python-telegram-bot) separate from the MTProto assistant session
(Pyrogram + PyTgCalls), so the bot token is used by only one polling client.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import random
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yt_dlp

try:
    from pyrogram import Client
    from pytgcalls import PyTgCalls
    from pytgcalls.types import MediaStream
except Exception:
    Client = None
    PyTgCalls = None
    MediaStream = None


@dataclass
class Track:
    title: str
    webpage_url: str
    stream_url: str
    duration: int = 0
    requested_by: str = ""
    video: bool = False


class MusicEngine:
    def __init__(self) -> None:
        self.api_id = int(os.getenv("API_ID", "0") or 0)
        self.api_hash = os.getenv("API_HASH", "").strip()
        self.session = os.getenv("STRING_SESSION", "").strip()

        self.spotify_id = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
        self.spotify_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()

        self.client = None
        self.calls = None
        self.started = False

        self.queues: dict[int, list[Track]] = {}
        self.current: dict[int, Track] = {}
        self.loop_mode: dict[int, bool] = {}
        self.autoplay: dict[int, bool] = {}
        self.volume: dict[int, int] = {}
        self.playlists: dict[int, list[str]] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._cookie_file: Optional[str] = None

    def _queue(self, chat_id: int) -> list[Track]:
        return self.queues.setdefault(chat_id, [])

    def _lock(self, chat_id: int) -> asyncio.Lock:
        return self._locks.setdefault(chat_id, asyncio.Lock())

    @staticmethod
    def _is_url(value: str) -> bool:
        return bool(re.match(r"^https?://", value.strip(), re.I))

    async def start(self) -> None:
        if self.started:
            return

        if not (self.api_id and self.api_hash and self.session):
            return
        if Client is None or PyTgCalls is None:
            return

        # Optional Render-friendly cookies. The base64 value can be supplied
        # as an environment variable without committing a cookie file.
        cookie_b64 = os.getenv("YTDLP_COOKIES_B64", "").strip()
        cookie_path = os.getenv("YTDLP_COOKIES_FILE", "").strip()
        if cookie_b64:
            try:
                fd, path = tempfile.mkstemp(prefix="yt_", suffix=".txt")
                with os.fdopen(fd, "wb") as f:
                    f.write(base64.b64decode(cookie_b64))
                self._cookie_file = path
            except Exception:
                self._cookie_file = None
        elif cookie_path and os.path.exists(cookie_path):
            self._cookie_file = cookie_path

        self.client = Client(
            "senorita_assistant",
            api_id=self.api_id,
            api_hash=self.api_hash,
            session_string=self.session,
            in_memory=True,
        )
        await self.client.start()
        self.calls = PyTgCalls(self.client)
        await self.calls.start()
        self.started = True

    async def stop(self) -> None:
        if self.calls:
            try:
                result = self.calls.stop()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                pass

        if self.client:
            try:
                await self.client.stop()
            except Exception:
                pass

        if self._cookie_file and os.path.exists(self._cookie_file):
            try:
                os.remove(self._cookie_file)
            except OSError:
                pass

        self.calls = None
        self.client = None
        self.started = False

    def command_handlers(self):
        return {
            "play": self.play_command,
            "vplay": self.vplay_command,
            "cplay": self.play_command,
            "cvplay": self.vplay_command,
            "pause": self.pause_command,
            "resume": self.resume_command,
            "skip": self.skip_command,
            "next": self.skip_command,
            "stop": self.stop_command,
            "end": self.stop_command,
            "loop": self.loop_command,
            "shuffle": self.shuffle_command,
            "queue": self.queue_command,
            "player": self.player_command,
            "playing": self.player_command,
            "volume": self.volume_command,
            "autoplay": self.autoplay_command,
            "playlist": self.playlist_command,
            "addsong": self.playlist_add_command,
            "rmsong": self.playlist_remove_command,
            "plplay": self.playlist_play_command,
            "lyrics": self.lyrics_command,
            "song": self.song_command,
            "vc": self.vc_command,
            "leavevc": self.leave_vc_command,
            "tts": self.tts_command,
            "musichelp": self.musichelp_command,
        }

    async def _resolve_spotify(self, query: str) -> Optional[str]:
        if not (self.spotify_id and self.spotify_secret):
            return None
        try:
            import spotipy
            from spotipy.oauth2 import SpotifyClientCredentials

            sp = spotipy.Spotify(
                auth_manager=SpotifyClientCredentials(
                    client_id=self.spotify_id,
                    client_secret=self.spotify_secret,
                )
            )
            data = await asyncio.to_thread(sp.track, query)
            artists = ", ".join(a.get("name", "") for a in data.get("artists", []))
            return f"{data.get('name', '')} {artists}".strip()
        except Exception:
            return None

    def _yt_options(self, video: bool = False) -> dict:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "skip_download": True,
            "extract_flat": False,
            "format": "bestvideo+bestaudio/best" if video else "bestaudio/best",
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0 Safari/537.36"
                )
            },
            "extractor_args": {
                "youtube": {
                    "player_client": ["default", "web_embedded"],
                }
            },
        }
        if self._cookie_file:
            opts["cookiefile"] = self._cookie_file
        return opts

    async def _extract(
        self, query: str, requester: str, video: bool = False
    ) -> Optional[Track]:
        if query.lower().startswith(("https://open.spotify.com/track/", "http://open.spotify.com/track/")):
            resolved = await self._resolve_spotify(query)
            if resolved:
                query = resolved

        target = query if self._is_url(query) else f"ytsearch1:{query}"

        def extract():
            with yt_dlp.YoutubeDL(self._yt_options(video)) as ydl:
                info = ydl.extract_info(target, download=False)
                if info and info.get("entries"):
                    info = next((x for x in info["entries"] if x), None)
                return info

        info = await asyncio.to_thread(extract)
        if not info:
            return None

        stream = info.get("url")
        if not stream:
            formats = [f for f in info.get("formats", []) if f.get("url")]
            if video:
                av = [
                    f for f in formats
                    if f.get("vcodec") != "none" and f.get("acodec") != "none"
                ]
                formats = av or formats
            else:
                audio = [f for f in formats if f.get("acodec") != "none"]
                formats = audio or formats
            if formats:
                stream = formats[-1]["url"]

        if not stream:
            return None

        return Track(
            title=info.get("title") or "Unknown track",
            webpage_url=info.get("webpage_url") or query,
            stream_url=stream,
            duration=int(info.get("duration") or 0),
            requested_by=requester,
            video=video,
        )

    async def _play_track(self, chat_id: int, track: Track) -> None:
        if not self.started or not self.calls or MediaStream is None:
            raise RuntimeError(
                "Music assistant is not configured. Set API_ID, API_HASH and STRING_SESSION."
            )
        # PyTgCalls 2.x methods are awaitable.
        await self.calls.play(chat_id, MediaStream(track.stream_url))
        self.current[chat_id] = track

    async def _start_next(self, chat_id: int) -> Optional[Track]:
        queue = self._queue(chat_id)
        if not queue:
            self.current.pop(chat_id, None)
            return None
        track = queue.pop(0)
        await self._play_track(chat_id, track)
        return track

    async def _search_and_play(self, update, query: str, video: bool) -> None:
        if not self.started:
            return await update.message.reply_text(
                "Music assistant is not configured. Add API_ID, API_HASH and STRING_SESSION."
            )

        try:
            track = await self._extract(
                query,
                update.effective_user.first_name or "user",
                video,
            )
            if not track:
                return await update.message.reply_text("Track not found.")

            chat_id = update.effective_chat.id
            async with self._lock(chat_id):
                if self.current.get(chat_id):
                    self._queue(chat_id).append(track)
                    return await update.message.reply_text(
                        f"Added to queue: {track.title}"
                    )
                await self._play_track(chat_id, track)

            await update.message.reply_text(
                f"Playing: {track.title}" if not video else f"Playing video: {track.title}"
            )
        except Exception as exc:
            msg = str(exc)
            if "Sign in to confirm" in msg or "not a bot" in msg.lower():
                return await update.message.reply_text(
                    "YouTube is asking yt-dlp for verification. "
                    "Configure YTDLP_COOKIES_B64 or use another supported URL/source."
                )
            await update.message.reply_text(f"Music error: {msg[:300]}")

    async def play_command(self, update, context):
        if not context.args:
            return await update.message.reply_text("Use /play song name or URL")
        await update.message.reply_text("Finding the track...")
        await self._search_and_play(update, " ".join(context.args), False)

    async def vplay_command(self, update, context):
        if not context.args:
            return await update.message.reply_text("Use /vplay video name or URL")
        await update.message.reply_text("Finding the video...")
        await self._search_and_play(update, " ".join(context.args), True)

    async def pause_command(self, update, context):
        if not self.calls:
            return await update.message.reply_text("Music assistant is offline.")
        try:
            await self.calls.pause(update.effective_chat.id)
            await update.message.reply_text("Playback paused.")
        except Exception as exc:
            await update.message.reply_text(f"Pause failed: {str(exc)[:180]}")

    async def resume_command(self, update, context):
        if not self.calls:
            return await update.message.reply_text("Music assistant is offline.")
        try:
            await self.calls.resume(update.effective_chat.id)
            await update.message.reply_text("Playback resumed.")
        except Exception as exc:
            await update.message.reply_text(f"Resume failed: {str(exc)[:180]}")

    async def skip_command(self, update, context):
        chat_id = update.effective_chat.id
        if not self.calls:
            return await update.message.reply_text("Music assistant is offline.")
        try:
            await self.calls.leave_call(chat_id)
        except Exception:
            pass
        self.current.pop(chat_id, None)
        try:
            track = await self._start_next(chat_id)
        except Exception as exc:
            return await update.message.reply_text(f"Next track failed: {str(exc)[:180]}")
        await update.message.reply_text(
            f"Playing next: {track.title}" if track else "Queue ended."
        )

    async def stop_command(self, update, context):
        chat_id = update.effective_chat.id
        if self.calls:
            try:
                await self.calls.leave_call(chat_id)
            except Exception:
                pass
        self.queues.pop(chat_id, None)
        self.current.pop(chat_id, None)
        await update.message.reply_text("Playback stopped.")

    async def loop_command(self, update, context):
        chat_id = update.effective_chat.id
        self.loop_mode[chat_id] = not self.loop_mode.get(chat_id, False)
        await update.message.reply_text(
            "Loop on." if self.loop_mode[chat_id] else "Loop off."
        )

    async def shuffle_command(self, update, context):
        queue = self._queue(update.effective_chat.id)
        random.shuffle(queue)
        await update.message.reply_text(f"Shuffled {len(queue)} queued tracks.")

    async def queue_command(self, update, context):
        chat_id = update.effective_chat.id
        lines = []
        if self.current.get(chat_id):
            lines.append("Now: " + self.current[chat_id].title)
        for i, track in enumerate(self._queue(chat_id), 1):
            lines.append(f"{i}. {track.title}")
        await update.message.reply_text(
            "Queue is empty." if not lines else "\n".join(lines[:40])
        )

    async def player_command(self, update, context):
        track = self.current.get(update.effective_chat.id)
        await update.message.reply_text(
            "Nothing is playing." if not track else f"Now playing: {track.title}"
        )

    async def volume_command(self, update, context):
        if not context.args:
            return await update.message.reply_text("Use /volume 1-200")
        if not self.calls:
            return await update.message.reply_text("Music assistant is offline.")
        try:
            volume = max(1, min(200, int(context.args[0])))
            await self.calls.change_volume_call(update.effective_chat.id, volume)
            self.volume[update.effective_chat.id] = volume
            await update.message.reply_text(f"Volume set to {volume}.")
        except Exception as exc:
            await update.message.reply_text(f"Volume failed: {str(exc)[:180]}")

    async def autoplay_command(self, update, context):
        chat_id = update.effective_chat.id
        self.autoplay[chat_id] = not self.autoplay.get(chat_id, False)
        await update.message.reply_text(
            "Autoplay on." if self.autoplay[chat_id] else "Autoplay off."
        )

    def _load_playlists(self) -> None:
        path = Path(os.getenv("PLAYLIST_FILE", "data/playlists.json"))
        if not path.exists():
            self.playlists = {}
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.playlists = {
                int(k): list(v) for k, v in data.items() if isinstance(v, list)
            }
        except Exception:
            self.playlists = {}

    def _save_playlists(self) -> None:
        path = Path(os.getenv("PLAYLIST_FILE", "data/playlists.json"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({str(k): v for k, v in self.playlists.items()}, indent=2),
            encoding="utf-8",
        )

    async def playlist_command(self, update, context):
        self._load_playlists()
        items = self.playlists.get(update.effective_user.id, [])
        if not items:
            return await update.message.reply_text(
                "Your playlist is empty. Use /addsong song name."
            )
        lines = [f"{i}. {item}" for i, item in enumerate(items, 1)]
        await update.message.reply_text("\n".join(lines[:50]))

    async def playlist_add_command(self, update, context):
        if not context.args:
            return await update.message.reply_text("Use /addsong song name or URL")
        self._load_playlists()
        uid = update.effective_user.id
        self.playlists.setdefault(uid, []).append(" ".join(context.args))
        self._save_playlists()
        await update.message.reply_text("Added to your playlist.")

    async def playlist_remove_command(self, update, context):
        if not context.args:
            return await update.message.reply_text("Use /rmsong NUMBER")
        self._load_playlists()
        uid = update.effective_user.id
        try:
            index = int(context.args[0]) - 1
            items = self.playlists.get(uid, [])
            if index < 0 or index >= len(items):
                raise ValueError
            removed = items.pop(index)
            self._save_playlists()
            await update.message.reply_text(f"Removed: {removed}")
        except ValueError:
            await update.message.reply_text("Invalid playlist number.")

    async def playlist_play_command(self, update, context):
        self._load_playlists()
        items = self.playlists.get(update.effective_user.id, [])
        if not items:
            return await update.message.reply_text("Your playlist is empty.")

        added = 0
        for item in items:
            try:
                track = await self._extract(
                    item,
                    update.effective_user.first_name or "user",
                    False,
                )
                if track:
                    self._queue(update.effective_chat.id).append(track)
                    added += 1
            except Exception:
                continue

        chat_id = update.effective_chat.id
        if added and not self.current.get(chat_id):
            try:
                await self._start_next(chat_id)
            except Exception as exc:
                return await update.message.reply_text(
                    f"Playlist playback failed: {str(exc)[:180]}"
                )
        await update.message.reply_text(f"Loaded {added} playlist tracks.")

    async def lyrics_command(self, update, context):
        # Keep this command safe and dependency-light. It does not scrape lyrics.
        if not context.args:
            return await update.message.reply_text("Use /lyrics song name")
        await update.message.reply_text(
            "Lyrics lookup is not bundled in this build. Use /song for track information."
        )

    async def song_command(self, update, context):
        if not context.args:
            return await update.message.reply_text("Use /song song name or URL")
        try:
            track = await self._extract(
                " ".join(context.args),
                update.effective_user.first_name or "user",
                False,
            )
            if not track:
                return await update.message.reply_text("Song not found.")
            await update.message.reply_text(
                f"{track.title}\n{track.webpage_url}"
            )
        except Exception as exc:
            await update.message.reply_text(f"Song lookup failed: {str(exc)[:180]}")

    async def vc_command(self, update, context):
        if not self.started:
            return await update.message.reply_text(
                "Music assistant is not configured. Add API_ID, API_HASH and STRING_SESSION."
            )
        await update.message.reply_text(
            "Music assistant is ready. Start a Telegram voice chat, then use /play."
        )

    async def leave_vc_command(self, update, context):
        chat_id = update.effective_chat.id
        if self.calls:
            try:
                await self.calls.leave_call(chat_id)
            except Exception:
                pass
        self.current.pop(chat_id, None)
        self.queues.pop(chat_id, None)
        await update.message.reply_text("Left the voice chat.")

    async def tts_command(self, update, context):
        text = " ".join(context.args).strip()
        if not text and update.message.reply_to_message:
            text = (
                update.message.reply_to_message.text
                or update.message.reply_to_message.caption
                or ""
            ).strip()
        if not text:
            return await update.message.reply_text("Use /tts text or reply to a text message.")

        try:
            import edge_tts
            voice = os.getenv("TTS_VOICE", "en-IN-NeerjaNeural")
            fd, path = tempfile.mkstemp(prefix="senorita_tts_", suffix=".mp3")
            os.close(fd)
            await edge_tts.Communicate(text[:2000], voice).save(path)
            with open(path, "rb") as audio:
                await update.message.reply_voice(voice=audio)
            os.remove(path)
        except Exception as exc:
            await update.message.reply_text(f"TTS failed: {str(exc)[:180]}")

    async def musichelp_command(self, update, context):
        await update.message.reply_text(
            "Music commands\n\n"
            "/play song or URL\n"
            "/vplay video or URL\n"
            "/pause /resume\n"
            "/skip /next /stop /end\n"
            "/queue /player /playing\n"
            "/loop /shuffle /volume 1-200 /autoplay\n"
            "/playlist /addsong /rmsong /plplay\n"
            "/song /lyrics /tts\n"
            "/vc /leavevc"
        )
