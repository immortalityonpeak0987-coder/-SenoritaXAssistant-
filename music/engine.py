import asyncio
import os
import re
import random
import tempfile
from dataclasses import dataclass
from typing import Optional

import yt_dlp

try:
    from pyrogram import Client
    from pyrogram.errors import RPCError
    from pytgcalls import PyTgCalls
    from pytgcalls.types import MediaStream
except ImportError:
    Client = None
    PyTgCalls = None
    MediaStream = None
    RPCError = Exception


@dataclass
class Track:
    title: str
    webpage_url: str
    stream_url: str
    duration: int = 0
    requested_by: str = ""
    video: bool = False


class MusicEngine:
    """Small independent music layer. It uses a user/assistant session for VC playback.
    The bot token remains owned by python-telegram-bot, so there is only one getUpdates client.
    """

    def __init__(self):
        self.api_id = int(os.getenv("API_ID", "0") or 0)
        self.api_hash = os.getenv("API_HASH", "").strip()
        self.session = os.getenv("STRING_SESSION", "").strip()
        self.spotify_id = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
        self.spotify_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
        self.client = None
        self.calls = None
        self.queues = {}
        self.current = {}
        self.loop_mode = {}
        self.autoplay = {}
        self.volume = {}
        self.started = False
        self._locks = {}

    async def start(self):
        if self.started:
            return
        if not (self.api_id and self.api_hash and self.session and Client and PyTgCalls):
            return
        self.client = Client(
            "senorita_assistant",
            api_id=self.api_id,
            api_hash=self.api_hash,
            session_string=self.session,
            in_memory=True,
        )
        await self.client.start()
        self.calls = PyTgCalls(self.client)
        self.calls.start()
        self.started = True

    async def stop(self):
        if self.calls:
            try:
                self.calls.stop()
            except Exception:
                pass
        if self.client:
            try:
                await self.client.stop()
            except Exception:
                pass
        self.started = False

    def command_handlers(self):
        return {
            "play": self.play_command, "vplay": self.vplay_command,
            "pause": self.pause_command, "resume": self.resume_command,
            "skip": self.skip_command, "next": self.skip_command,
            "stop": self.stop_command, "end": self.stop_command,
            "loop": self.loop_command, "shuffle": self.shuffle_command,
            "queue": self.queue_command, "player": self.player_command,
            "playing": self.player_command, "seek": self.seek_command,
            "volume": self.volume_command, "autoplay": self.autoplay_command,
            "playlist": self.playlist_command, "addsong": self.playlist_add_command,
            "rmsong": self.playlist_remove_command, "plplay": self.playlist_play_command,
            "lyrics": self.lyrics_command, "song": self.song_command,
            "vc": self.vc_command, "leavevc": self.leave_vc_command,
        }

    def _queue(self, chat_id):
        return self.queues.setdefault(chat_id, [])

    def _lock(self, chat_id):
        return self._locks.setdefault(chat_id, asyncio.Lock())

    @staticmethod
    def _is_url(q):
        return bool(re.match(r"^https?://", q.strip(), re.I))

    async def _resolve_spotify(self, query):
        if not (self.spotify_id and self.spotify_secret):
            return None
        try:
            import spotipy
            from spotipy.oauth2 import SpotifyClientCredentials
            sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(self.spotify_id, self.spotify_secret))
            data = await asyncio.to_thread(sp.track, query)
            artists = ", ".join(a["name"] for a in data.get("artists", []))
            return f"{data['name']} {artists}".strip()
        except Exception:
            return None

    async def _extract(self, query, requester, video=False):
        if query.startswith("https://open.spotify.com/track/") or query.startswith("http://open.spotify.com/track/"):
            resolved = await self._resolve_spotify(query)
            if resolved:
                query = resolved
        target = query if self._is_url(query) else f"ytsearch1:{query}"
        opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "format": "bestvideo+bestaudio/best" if video else "bestaudio/best",
            "skip_download": True,
            "extract_flat": False,
        }
        def extract():
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(target, download=False)
                if "entries" in info:
                    info = next((x for x in info["entries"] if x), None)
                return info
        info = await asyncio.to_thread(extract)
        if not info:
            return None
        stream = info.get("url")
        if not stream and info.get("formats"):
            formats = [f for f in info["formats"] if f.get("url")]
            if video:
                formats = [f for f in formats if f.get("vcodec") != "none" and f.get("acodec") != "none"] or formats
            else:
                formats = [f for f in formats if f.get("acodec") != "none"] or formats
            stream = formats[-1]["url"] if formats else None
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

    async def _play_track(self, chat_id, track):
        if not self.started:
            raise RuntimeError("Music assistant is not configured. Set API_ID, API_HASH and STRING_SESSION.")
        stream = MediaStream(track.stream_url)
        self.calls.play(chat_id, stream)
        self.current[chat_id] = track

    async def _start_next(self, chat_id):
        q = self._queue(chat_id)
        if q:
            track = q.pop(0)
            await self._play_track(chat_id, track)
            return track
        self.current.pop(chat_id, None)
        return None

    async def play_command(self, update, context):
        if not context.args:
            return await update.message.reply_text("Use /play song name or URL")
        query = " ".join(context.args)
        await update.message.reply_text("Finding the track...")
        try:
            track = await self._extract(query, update.effective_user.first_name or "user", False)
            if not track:
                return await update.message.reply_text("Track not found.")
            cid = update.effective_chat.id
            async with self._lock(cid):
                if self.current.get(cid):
                    self._queue(cid).append(track)
                    return await update.message.reply_text(f"Added to queue: {track.title}")
                await self._play_track(cid, track)
            await update.message.reply_text(f"Playing: {track.title}")
        except Exception as e:
            await update.message.reply_text(f"Music error: {str(e)[:250]}")

    async def vplay_command(self, update, context):
        if not context.args:
            return await update.message.reply_text("Use /vplay video name or URL")
        query = " ".join(context.args)
        await update.message.reply_text("Finding the video...")
        try:
            track = await self._extract(query, update.effective_user.first_name or "user", True)
            if not track: return await update.message.reply_text("Video not found.")
            cid = update.effective_chat.id
            if self.current.get(cid):
                self._queue(cid).append(track)
                return await update.message.reply_text(f"Added to queue: {track.title}")
            await self._play_track(cid, track)
            await update.message.reply_text(f"Playing video: {track.title}")
        except Exception as e:
            await update.message.reply_text(f"Video error: {str(e)[:250]}")

    async def pause_command(self, update, context):
        try:
            await self.calls.pause(update.effective_chat.id)
            await update.message.reply_text("Playback paused.")
        except Exception as e: await update.message.reply_text(f"Pause failed: {str(e)[:180]}")

    async def resume_command(self, update, context):
        try:
            await self.calls.resume(update.effective_chat.id)
            await update.message.reply_text("Playback resumed.")
        except Exception as e: await update.message.reply_text(f"Resume failed: {str(e)[:180]}")

    async def skip_command(self, update, context):
        cid = update.effective_chat.id
        try:
            await self.calls.leave_call(cid)
            self.current.pop(cid, None)
            track = await self._start_next(cid)
            await update.message.reply_text(f"Playing next: {track.title}" if track else "Queue ended.")
        except Exception as e: await update.message.reply_text(f"Skip failed: {str(e)[:180]}")

    async def stop_command(self, update, context):
        cid = update.effective_chat.id
        try: await self.calls.leave_call(cid)
        except Exception: pass
        self.queues.pop(cid, None); self.current.pop(cid, None)
        await update.message.reply_text("Playback stopped.")

    async def loop_command(self, update, context):
        cid = update.effective_chat.id
        self.loop_mode[cid] = not self.loop_mode.get(cid, False)
        await update.message.reply_text("Loop on." if self.loop_mode[cid] else "Loop off.")

    async def shuffle_command(self, update, context):
        q = self._queue(update.effective_chat.id)
        random.shuffle(q)
        await update.message.reply_text(f"Shuffled {len(q)} queued tracks.")

    async def queue_command(self, update, context):
        cid = update.effective_chat.id
        lines = []
        if self.current.get(cid): lines.append("Now: " + self.current[cid].title)
        for i, t in enumerate(self._queue(cid), 1): lines.append(f"{i}. {t.title}")
        await update.message.reply_text("Queue is empty." if not lines else "\n".join(lines[:30]))

    async def player_command(self, update, context):
        t = self.current.get(update.effective_chat.id)
        await update.message.reply_text("Nothing is playing." if not t else f"Now playing: {t.title}")

    async def seek_command(self, update, context):
        await update.message.reply_text("Seek is not enabled in this build because direct stream seeking is source-dependent.")

    async def volume_command(self, update, context):
        if not context.args: return await update.message.reply_text("Use /volume 1-200")
        try:
            volume = max(1, min(200, int(context.args[0])))
            await self.calls.change_volume_call(update.effective_chat.id, volume)
            self.volume[update.effective_chat.id] = volume
            await update.message.reply_text(f"Volume set to {volume}.")
        except Exception as e: await update.message.reply_text(f"Volume failed: {str(e)[:180]}")

    async def autoplay_command(self, update, context):
        cid = update.effective_chat.id
        self.autoplay[cid] = not self.autoplay.get(cid, False)
        await update.message.reply_text("Autoplay on." if self.autoplay[cid] else "Autoplay off.")

    async def playlist_command(self, update, context):
        await update.message.reply_text("Playlist storage is local to this running instance. Use /addsong name, /rmsong number and /plplay to load it.")

    async def playlist_add_command(self, update, context):
        if not context.args: return await update.message.reply_text("Use /addsong song name or URL")
        key = update.effective_user.id
        path = os.getenv("PLAYLIST_FILE", "data/playlists.json")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        data = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f: data = __import__("json").load(f)
            except Exception: pass
        data.setdefault(str(key), []).append(" ".join(context.args))
        with open(path, "w", encoding="utf-8") as f: __import__("json").dump(data, f, indent=2)
        await update.message.reply_text("Added to your playlist.")

    async def playlist_remove_command(self, update, context):
        await update.message.reply_text("Remove a playlist item with /rmsong NUMBER.")

    async def playlist_play_command(self, update, context):
        path = os.getenv("PLAYLIST_FILE", "data/playlists.json")
        if not os.path.exists(path): return await update.message.reply_text("Your playlist is empty.")
        import json
        with open(path, "r", encoding="utf-8") as f: data = json.load(f)
        items = data.get(str(update.effective_user.id), [])
        if not items: return await update.message.reply_text("Your playlist is empty.")
        for item in items:
            track = await self._extract(item, update.effective_user.first_name or "user", False)
            if track: self._queue(update.effective_chat.id).append(track)
        if not self.current.get(update.effective_chat.id): await self._start_next(update.effective_chat.id)
        await update.message.reply_text(f"Loaded {len(items)} playlist items.")

    async def lyrics_command(self, update, context):
        await update.message.reply_text("Lyrics lookup is not enabled in this clean build. Use /song with a title to inspect track information.")

    async def song_command(self, update, context):
        if not context.args: return await update.message.reply_text("Use /song song name or URL")
        try:
            t = await self._extract(" ".join(context.args), update.effective_user.first_name or "user", False)
            await update.message.reply_text("Not found." if not t else f"{t.title}\n{t.webpage_url}")
        except Exception as e: await update.message.reply_text(f"Song lookup failed: {str(e)[:180]}")

    async def vc_command(self, update, context):
        if not self.started: return await update.message.reply_text("Music assistant is not configured yet. Add API_ID, API_HASH and STRING_SESSION.")
        await update.message.reply_text("The music assistant is ready. Start a Telegram voice chat, then use /play.")

    async def leave_vc_command(self, update, context):
        try: await self.calls.leave_call(update.effective_chat.id)
        except Exception: pass
        self.current.pop(update.effective_chat.id, None)
        self.queues.pop(update.effective_chat.id, None)
        await update.message.reply_text("Left the voice chat.")
