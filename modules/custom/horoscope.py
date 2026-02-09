"""
Horoscope Plugin (OOP / PluginBase)
===================================

Commands (single entrypoint):
  .horoscope [sign] [today|tomorrow|yesterday]  -> show horoscope (or default sign if omitted)
  .horoscope sign <sign>                         -> save default sign (per-nick)
  .horoscope help                                -> help (from description)

Notes:
- Scrapes daily horoscope from horoscope.com.
- Stores default sign in sqlite table horoscope_prefs (per nick).
- Threaded execution via deferToThread (Twisted).
"""

from __future__ import annotations

import re
import sqlite3
from typing import Optional, List, Tuple

import requests
from bs4 import BeautifulSoup
from twisted.internet.threads import deferToThread

from core.log import get_logger
from core.environment_config import config
from core.plugin_manager import PluginBase

logger = get_logger("horoscope")

PLUGIN_INFO = {
    "name": "horoscope",
    "version": "1.0.0",
    "author": "BlackBoT Team",
    "description": "Horoscope plugin (horoscope.com) with prefs (default sign) + threaded scraping",
    "dependencies": ["requests", "bs4", "twisted"],
}

# ---------------------------------------------------------------------------
# Zodiac mapping (horoscope.com uses sign=1..12)
# ---------------------------------------------------------------------------

ZODIAC_SIGNS = {
    "Aries": 1, "Taurus": 2, "Gemini": 3,
    "Cancer": 4, "Leo": 5, "Virgo": 6,
    "Libra": 7, "Scorpio": 8, "Sagittarius": 9,
    "Capricorn": 10, "Aquarius": 11, "Pisces": 12,
}

# Accept some common aliases (optional, keeps UX nicer)
ALIASES = {
    "berbec": "Aries",
    "taur": "Taurus",
    "gemeni": "Gemini",
    "rac": "Cancer",
    "leu": "Leo",
    "fecioara": "Virgo",
    "balanta": "Libra",
    "scorpion": "Scorpio",
    "sagetator": "Sagittarius",
    "capricorn": "Capricorn",
    "varsator": "Aquarius",
    "pesti": "Pisces",
}

ALLOWED_DAYS = {"today", "tomorrow", "yesterday"}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0 Safari/537.36"
)

BASE_URL = "https://www.horoscope.com/us/horoscopes/general/"


# =============================================================================
# DB Helper (User Preferences)
# =============================================================================

class HoroscopeDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_table()

    def _init_table(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS horoscope_prefs (
                        nick TEXT PRIMARY KEY,
                        sign TEXT NOT NULL,
                        updated_at INTEGER
                    )
                """)
        except Exception as e:
            logger.error(f"Failed to init horoscope DB: {e}", exc_info=True)

    def get_sign(self, nick: str) -> Optional[str]:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT sign FROM horoscope_prefs WHERE nick = ? COLLATE NOCASE",
                    (nick,)
                )
                row = cur.fetchone()
                return row[0] if row else None
        except Exception:
            return None

    def set_sign(self, nick: str, sign: str) -> bool:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO horoscope_prefs (nick, sign, updated_at)
                    VALUES (?, ?, strftime('%s','now'))
                """, (nick, sign))
            return True
        except Exception as e:
            logger.error(f"Failed to set sign: {e}", exc_info=True)
            return False


# =============================================================================
# Helpers
# =============================================================================

def _normalize_sign(raw: str) -> Optional[str]:
    s = (raw or "").strip()
    if not s:
        return None

    s_low = s.lower()

    # alias ro -> en
    if s_low in ALIASES:
        return ALIASES[s_low]

    # Title case (aries -> Aries)
    s_tc = s_low.capitalize()

    # handle multi-word weird inputs safely (shouldn't happen for zodiac)
    s_tc = re.sub(r"\s+", " ", s_tc).strip()

    if s_tc in ZODIAC_SIGNS:
        return s_tc

    # try strict match ignoring case
    for k in ZODIAC_SIGNS.keys():
        if k.lower() == s_low:
            return k

    return None


def _split_chunks(text: str, maxlen: int = 400) -> List[str]:
    out = []
    s = (text or "").strip()
    while len(s) > maxlen:
        cut = s.rfind(" ", 0, maxlen)
        if cut == -1:
            cut = maxlen
        out.append(s[:cut].strip())
        s = s[cut:].strip()
    if s:
        out.append(s)
    return out


def _fetch_horoscope(sign_name: str, day: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Returns: (text, error)
    """
    try:
        zodiac_id = ZODIAC_SIGNS.get(sign_name)
        if not zodiac_id:
            return None, "Invalid sign."

        url = f"{BASE_URL}horoscope-general-daily-{day}.aspx?sign={zodiac_id}"

        r = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=10
        )
        r.raise_for_status()

        soup = BeautifulSoup(r.content, "html.parser")

        container = soup.find("div", class_="main-horoscope")
        if not container:
            return None, "Could not parse page (main container missing)."

        p = container.find("p")
        if not p or not p.text:
            return None, "Could not parse horoscope text."

        text = p.text.strip()

        # Sometimes it starts with a date like "Feb 8, 2026 - ..."
        # Strip " - " if it appears early.
        if " - " in text[:25]:
            text = text.split(" - ", 1)[1].strip()

        if not text:
            return None, "Empty horoscope text."

        return text, None

    except requests.Timeout:
        return None, "Request timeout."
    except Exception as e:
        logger.error(f"Horoscope fetch error: {e}", exc_info=True)
        return None, "Fetch error."


# =============================================================================
# Plugin
# =============================================================================

class Plugin(PluginBase):
    def __init__(self, bot):
        super().__init__(bot)
        self.db = HoroscopeDB(bot.sql.database)

    def on_load(self):
        desc = (
            ".horoscope [sign] [today|tomorrow|yesterday] -> show horoscope (or default sign)\n"
            ".horoscope sign <sign>                      -> save default sign\n"
            ".horoscope help                             -> show this help\n"
            "Signs: Aries, Taurus, Gemini, Cancer, Leo, Virgo, Libra, Scorpio, Sagittarius, Capricorn, Aquarius, Pisces"
        )

        self.register_command("horoscope", self.cmd_horoscope, flags="-", description=desc)
        self.register_command("horo", self.cmd_horoscope, flags="-", description=desc)
        self.register_command("hs", self.cmd_horoscope, flags="-", description=desc)

    def cmd_horoscope(self, bot, channel, feedback, nick, host, msg):
        args = (msg or "").strip().split()
        sub = args[0].lower() if args else ""

        # help
        if sub in ("help", "h", "?"):
            pm = getattr(bot, "plugin_manager", None)
            if pm and hasattr(pm, "send_command_help"):
                return pm.send_command_help(bot, feedback, channel, nick, host, "horoscope")
            bot.send_message(
                feedback,
                f"Usage: {config.char}horoscope [sign] [today|tomorrow|yesterday] | "
                f"{config.char}horoscope sign <sign>"
            )
            return

        # set default sign
        if sub == "sign":
            raw_sign = " ".join(args[1:]).strip()
            sign = _normalize_sign(raw_sign)
            if not sign:
                bot.send_message(feedback, f"⚠️ Usage: {config.char}horoscope sign <sign>")
                return

            if self.db.set_sign(nick, sign):
                bot.send_message(feedback, f"✅ Saved default sign: {sign}")
            else:
                bot.send_message(feedback, "❌ Database error.")
            return

        # normal lookup:
        # .horoscope [sign] [day]
        sign = None
        day = "today"

        if args:
            # If first arg looks like a day, user probably wants default sign for that day
            if sub in ALLOWED_DAYS:
                day = sub
            else:
                sign = _normalize_sign(args[0])
                if not sign:
                    bot.send_message(feedback, f"❌ Invalid sign '{args[0]}'. Try: {config.char}horoscope help")
                    return
                if len(args) > 1 and args[1].lower() in ALLOWED_DAYS:
                    day = args[1].lower()

        if not sign:
            sign = self.db.get_sign(nick)
            if not sign:
                bot.send_message(
                    feedback,
                    f"{nick}: Specify sign ({config.char}horo Aries) or set default ({config.char}horo sign Aries)."
                )
                return

        def work():
            text, err = _fetch_horoscope(sign, day)
            return sign, day, text, err

        def done(result):
            sign_out, day_out, text, err = result
            if err:
                bot.send_message(feedback, f"{nick}: ❌ {err}")
                return

            chunks = _split_chunks(text, maxlen=400)
            if not chunks:
                bot.send_message(feedback, f"{nick}: ❌ Could not parse horoscope text.")
                return

            # First line includes prefix
            bot.send_message(feedback, f"🔮 {nick}: {sign_out} ({day_out}): {chunks[0]}")
            for c in chunks[1:]:
                bot.send_message(feedback, f"... {c}")

        deferToThread(work).addCallback(done)


def register(bot):
    return Plugin(bot)
