"""
YouTubeTitle Plugin (BlackBoT / PluginBase)
===========================================

Features:
- Auto-detect YouTube links in PRIVMSG and print title + duration + channel
- Command: .yt <url>
- Cooldown per-channel to avoid spam
- Threaded extraction (no blocking) via deferToThread

Dependencies:
- yt-dlp

Author: BlackBoT Team
"""

from __future__ import annotations

import re
import time
from typing import Optional, Dict, Tuple

from twisted.internet.threads import deferToThread

from core.log import get_logger
from core.environment_config import config
from core.plugin_manager import PluginBase

logger = get_logger("youtubetitle")

PLUGIN_INFO = {
    "name": "youtubetitle",
    "version": "1.0.0",
    "author": "BlackBoT Team",
    "description": "Show YouTube video title on link or via .yt <url> (uses yt-dlp).",
    "dependencies": ["yt-dlp"],
}

# Detect common YouTube URL forms (watch, youtu.be, shorts)
YOUTUBE_RE = re.compile(
    r"(https?://(?:www\.)?"
    r"(?:youtube\.com/(?:watch\?v=|shorts/|embed/)|youtu\.be/)"
    r"[A-Za-z0-9_\-]{6,}(?:[^\s]*)?)",
    re.IGNORECASE
)

# Keep output short-ish for IRC
MAX_TITLE = 220

# Default cooldown (seconds) per channel
DEFAULT_COOLDOWN = 12


def _fmt_duration(seconds: Optional[int]) -> str:
    if not seconds or seconds < 0:
        return "??:??"
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _safe_text(s: Optional[str]) -> str:
    s = (s or "").strip()
    if len(s) > MAX_TITLE:
        s = s[: MAX_TITLE - 1].rstrip() + "…"
    # avoid IRC control chars
    s = re.sub(r"[\x00-\x1F\x7F]", "", s)
    return s


class Plugin(PluginBase):
    def __init__(self, bot):
        super().__init__(bot)
        self.cooldown_seconds = DEFAULT_COOLDOWN
        self._last_sent: Dict[str, float] = {}  # target -> last_time

    def on_load(self):
        desc = (
            f"{config.char}yt <youtube_url> -> show title + duration + channel\n"
            f"Auto: if a YouTube link is posted, bot prints title (cooldown {DEFAULT_COOLDOWN}s/channel)"
        )
        self.register_command("yt", self.cmd_yt, flags="-", description=desc)
        self.register_command("ytt", self.cmd_yt, flags="-", description=desc)
        self.register_command("youtubetitle", self.cmd_yt, flags="-", description=desc)

        # Auto link title
        self.register_hook("PRIVMSG", self.on_privmsg)

    # -------------------------
    # Hook: auto-detect links
    # -------------------------

    def on_privmsg(self, bot, channel=None, nick=None, host=None, message=None, **_):
        msg = message or ""
        m = YOUTUBE_RE.search(msg)
        if not m:
            return

        url = m.group(1)

        # channel might be None for PMs; choose feedback target
        target = channel or nick
        if not target:
            return

        # cooldown per target
        now = time.time()
        last = self._last_sent.get(target, 0.0)
        if now - last < self.cooldown_seconds:
            return

        self._last_sent[target] = now
        self._handle_url(bot, feedback=target, nick=nick or "", url=url)

    # -------------------------
    # Command: .yt <url>
    # -------------------------

    def cmd_yt(self, bot, channel, feedback, nick, host, msg):
        url = (msg or "").strip()
        if not url:
            bot.send_message(feedback, f"Usage: {config.char}yt <youtube_url>")
            return

        # allow user to paste a sentence containing a link
        m = YOUTUBE_RE.search(url)
        if m:
            url = m.group(1)

        if not YOUTUBE_RE.match(url):
            bot.send_message(feedback, "❌ Not a valid YouTube link.")
            return

        self._handle_url(bot, feedback=feedback, nick=nick or "", url=url)

    # -------------------------
    # Worker
    # -------------------------

    def _handle_url(self, bot, feedback: str, nick: str, url: str):
        def work() -> Tuple[Optional[dict], Optional[str]]:
            try:
                # Import inside thread path is okay; dependency is ensured by PluginManager
                from yt_dlp import YoutubeDL

                ydl_opts = {
                    "quiet": True,
                    "no_warnings": True,
                    "skip_download": True,
                    "noplaylist": True,
                    "extract_flat": False,
                }

                with YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)

                # If yt-dlp returns a playlist entry even with noplaylist,
                # try to pick the first entry safely.
                if isinstance(info, dict) and info.get("_type") == "playlist":
                    entries = info.get("entries") or []
                    for e in entries:
                        if isinstance(e, dict) and e.get("title"):
                            info = e
                            break

                return info if isinstance(info, dict) else None, None

            except Exception as e:
                logger.error(f"yt-dlp extract error: {e}", exc_info=True)
                return None, "Could not fetch YouTube info."

        def done(result):
            info, err = result
            if err or not info:
                bot.send_message(feedback, f"{nick}: ❌ {err or 'Unknown error.'}".strip(": "))
                return

            title = _safe_text(info.get("title"))
            uploader = _safe_text(info.get("uploader") or info.get("channel") or info.get("uploader_id"))
            duration = _fmt_duration(info.get("duration"))

            parts = ["▶️ YouTube:"]
            if title:
                parts.append(title)
            parts.append(f"[{duration}]")
            if uploader:
                parts.append(f"• {uploader}")

            bot.send_message(feedback, " ".join(parts).strip())

        deferToThread(work).addCallback(done)


def register(bot):
    return Plugin(bot)
