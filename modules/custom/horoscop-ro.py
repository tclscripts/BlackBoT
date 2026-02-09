"""
Horoscop RO (Astromax Eva) Plugin
=================================

Source: astromax.eva.ro (Horoscop zilnic)

Commands:
  !horoscop [zodie] [azi|maine|ieri]
  !horoscop sign <zodie>           (save default sign per nick)
  !horoscop help

Dependencies:
- requests
- bs4
- twisted
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

logger = get_logger("horoscope_ro")

PLUGIN_INFO = {
    "name": "horoscope_ro",
    "version": "1.0.0",
    "author": "BlackBoT Team",
    "description": "Horoscop RO din astromax.eva.ro + prefs (zodie implicită).",
    "dependencies": ["requests", "bs4", "twisted"],
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0 Safari/537.36"
)

ZODII = [
    "Berbec", "Taur", "Gemeni", "Rac", "Leu", "Fecioara",
    "Balanta", "Scorpion", "Sagetator", "Capricorn", "Varsator", "Pesti"
]

ALIASES = {
    "berbec": "Berbec",
    "taur": "Taur",
    "gemeni": "Gemeni",
    "rac": "Rac",
    "leu": "Leu",
    "fecioara": "Fecioara",
    "fecioară": "Fecioara",
    "balanta": "Balanta",
    "balanță": "Balanta",
    "scorpion": "Scorpion",
    "sagetator": "Sagetator",
    "săgetător": "Sagetator",
    "capricorn": "Capricorn",
    "varsator": "Varsator",
    "vărsător": "Varsator",
    "pesti": "Pesti",
    "pești": "Pesti",
}

ZODIE_SLUG = {
    "Berbec": "berbec",
    "Taur": "taur",
    "Gemeni": "gemeni",
    "Rac": "rac",
    "Leu": "leu",
    "Fecioara": "fecioara",
    "Balanta": "balanta",
    "Scorpion": "scorpion",
    "Sagetator": "sagetator",
    "Capricorn": "capricorn",
    "Varsator": "varsator",
    "Pesti": "pesti",
}

DAY_ALIASES = {
    "azi": "azi",
    "astazi": "azi",
    "astăzi": "azi",
    "ieri": "ieri",
    "maine": "maine",
    "mâine": "maine",
}

# Astromax paths (azi confirmat; ieri confirmat din pagină; maine încercat)
BASE = "https://astromax.eva.ro"
PATHS = {
    "azi": "/horoscop-zilnic/{slug}.html",
    "ieri": "/horoscopul-de-ieri/{slug}.html",
    "maine": "/horoscopul-de-maine/{slug}.html",
}


def _normalize_zodie(raw: str) -> Optional[str]:
    s = (raw or "").strip().lower()
    if not s:
        return None
    s = re.sub(r"\s+", " ", s)
    if s in ALIASES:
        return ALIASES[s]
    # fallback case-insensitive
    for z in ZODII:
        if z.lower() == s:
            return z
    return None


def _normalize_day(raw: str) -> str:
    s = (raw or "").strip().lower()
    return DAY_ALIASES.get(s, "azi")


def _clean_text(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s).strip()
    # remove control chars
    s = re.sub(r"[\x00-\x1F\x7F]", "", s)
    return s


def _split_chunks(text: str, maxlen: int = 380) -> List[str]:
    text = _clean_text(text)
    if not text:
        return []
    out = []
    while len(text) > maxlen:
        cut = text.rfind(" ", 0, maxlen)
        if cut < 60:
            cut = maxlen
        out.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        out.append(text)
    return out


class HoroscopeRoDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init()

    def _init(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS horoscope_ro_prefs (
                    nick TEXT PRIMARY KEY,
                    zodie TEXT NOT NULL,
                    updated_at INTEGER
                )
            """)

    def get_zodie(self, nick: str) -> Optional[str]:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT zodie FROM horoscope_ro_prefs WHERE nick = ? COLLATE NOCASE",
                    (nick,)
                )
                row = cur.fetchone()
                return row[0] if row else None
        except Exception:
            return None

    def set_zodie(self, nick: str, zodie: str) -> bool:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO horoscope_ro_prefs (nick, zodie, updated_at)
                    VALUES (?, ?, strftime('%s','now'))
                """, (nick, zodie))
            return True
        except Exception as e:
            logger.error(f"DB set_zodie failed: {e}", exc_info=True)
            return False


def fetch_astromax(zodie: str, day: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Returns (date_str, horoscope_text, error)
    """
    slug = ZODIE_SLUG[zodie]
    path = PATHS.get(day, PATHS["azi"]).format(slug=slug)
    url = BASE + path

    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=12)
        if r.status_code == 404:
            return None, None, f"Nu există horoscop pentru '{day}' pe Astromax (404)."
        r.raise_for_status()

        soup = BeautifulSoup(r.content, "html.parser")

        # text: <p class="about"> ... </p>
        p = soup.select_one("#hor-zilnic p.about") or soup.select_one("p.about")
        if not p:
            return None, None, "Nu am găsit textul horoscopului (selector p.about)."

        text = _clean_text(p.get_text(" ", strip=True))
        if len(text) < 40:
            return None, None, "Text prea scurt / invalid."

        # date: <span class="date">Duminica, 08-02-2026</span>
        d = soup.select_one("#hor-zilnic .title-star span.date") or soup.select_one("span.date")
        date_str = _clean_text(d.get_text(" ", strip=True)) if d else None

        return date_str, text, None

    except Exception as e:
        logger.error(f"Fetch error {url}: {e}", exc_info=True)
        return None, None, "Eroare la preluare (network/parse)."


class Plugin(PluginBase):
    def __init__(self, bot):
        super().__init__(bot)
        self.db = HoroscopeRoDB(bot.sql.database)

    def on_load(self):
        desc = (
            f"{config.char}horoscop [zodie] [azi|maine|ieri] -> horoscop RO (Astromax)\n"
            f"{config.char}horoscop sign <zodie>            -> setează zodie implicită\n"
            f"{config.char}horoscop help                   -> help\n"
            "Zodii: Berbec, Taur, Gemeni, Rac, Leu, Fecioara, Balanta, Scorpion, "
            "Sagetator, Capricorn, Varsator, Pesti"
        )

        # IMPORTANT: NU folosim 'horoscop' ca să nu se bată cu EN
        self.register_command("horoscop", self.cmd_horo, flags="-", description=desc)

    def cmd_horo(self, bot, channel, feedback, nick, host, msg):
        args = (msg or "").strip().split()
        sub = args[0].lower() if args else ""

        if sub in ("help", "h", "?"):
            pm = getattr(bot, "plugin_manager", None)
            if pm and hasattr(pm, "send_command_help"):
                return pm.send_command_help(bot, feedback, channel, nick, host, "horoscop_ro")
            bot.send_message(feedback, f"Usage: {config.char}horoscop_ro [zodie] [azi|maine|ieri] | {config.char}horoscop_ro sign <zodie>")
            return

        if sub == "sign":
            raw = " ".join(args[1:]).strip()
            z = _normalize_zodie(raw)
            if not z:
                bot.send_message(feedback, f"⚠️ Usage: {config.char}horoscop_ro sign <zodie>")
                return
            if self.db.set_zodie(nick, z):
                bot.send_message(feedback, f"✅ Zodie salvată: {z}")
            else:
                bot.send_message(feedback, "❌ Eroare DB.")
            return

        # parse zodie/day
        zodie = None
        day = "azi"

        if args:
            # dacă primul e ziua -> folosim zodie salvată
            if sub in DAY_ALIASES:
                day = _normalize_day(sub)
            else:
                zodie = _normalize_zodie(args[0])
                if not zodie:
                    bot.send_message(feedback, f"❌ Zodie invalidă '{args[0]}'. Scrie: {config.char}horoscop_ro help")
                    return
                if len(args) > 1:
                    day = _normalize_day(args[1])

        if not zodie:
            zodie = self.db.get_zodie(nick)
            if not zodie:
                bot.send_message(feedback, f"{nick}: scrie {config.char}horoscop_ro Berbec (sau setează: {config.char}horoscop_ro sign Berbec).")
                return

        def work():
            date_str, text, err = fetch_astromax(zodie, day)
            return date_str, text, err

        def done(res):
            date_str, text, err = res
            if err:
                bot.send_message(feedback, f"{nick}: ❌ {err}")
                return

            chunks = _split_chunks(text, 380)
            if not chunks:
                bot.send_message(feedback, f"{nick}: ❌ Nu am primit text.")
                return

            date_part = f" — {date_str}" if date_str else ""
            bot.send_message(feedback, f"🔮 {nick}: {zodie} ({day}){date_part}: {chunks[0]}")
            for c in chunks[1:]:
                bot.send_message(feedback, f"... {c}")

        deferToThread(work).addCallback(done)


def register(bot):
    return Plugin(bot)
