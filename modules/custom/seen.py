"""
SEEN Plugin (standalone / self-contained)
=========================================

Command:
  .seen <nick|pattern|ident@host>   -> last seen info (* and ? supported; ident@host supported)
  .seen stats [#channel]            -> statistics
  .seen help                        -> help from description

Tracks:
  JOIN, PART, QUIT, KICK, NICK, PRIVMSG (optional but enabled by default)

DB:
  Table SEEN is created automatically.

Notes:
- No dependency on core/seen.py. This plugin is self-contained.
- Requires PluginManager to dispatch hooks (JOIN/PART/QUIT/KICK/NICK/PRIVMSG).

Author: BlackBoT Team
Version: 1.0.0
"""

import time
from datetime import datetime, timezone

from core.log import get_logger
from core.plugin_manager import PluginBase
from core.environment_config import config

logger = get_logger("seen module")

PLUGIN_INFO = {
    "name": "seen",
    "version": "1.0.0",
    "author": "BlackBoT Team",
    "description": "Seen tracking plugin (standalone) with hooks + search + stats",
    "dependencies": [],
}

ALIASES = ["s"]

# If you want to disable message tracking (PRIVMSG), set to False
TRACK_MESSAGES = True


class Plugin(PluginBase):
    def __init__(self, bot):
        super().__init__(bot)
        self.sql = bot.sql

    def on_load(self):
        self._init_db()

        desc = (
            ".seen <nick|pattern|ident@host>   -> last seen (* and ? supported)\n"
            ".seen stats [#channel]            -> statistics\n"
            ".seen help                         -> show this help"
        )

        self.register_command(
            "seen",
            self.cmd_seen,
            flags="-",
            description=desc,
        )

        for a in ALIASES:
            self.register_command(a, self.cmd_seen, flags="-", description=desc)
            pm = getattr(self.bot, "plugin_manager", None)
            if pm and hasattr(pm, "register_aliases"):
                pm.register_aliases("seen", [a])

        # Register hooks (requires dispatcher in bot/plugin_manager)
        self.register_hook("JOIN", self.h_join)
        self.register_hook("PART", self.h_part)
        self.register_hook("QUIT", self.h_quit)
        self.register_hook("KICK", self.h_kick)
        self.register_hook("NICK", self.h_nick)

        if TRACK_MESSAGES:
            self.register_hook("PRIVMSG", self.h_privmsg)

    # ---------------------------------------------------------------------
    # DB
    # ---------------------------------------------------------------------

    def _init_db(self):
        try:
            self.sql.sqlite3_update(
                """
                CREATE TABLE IF NOT EXISTS SEEN
                (
                    botId
                    INTEGER
                    NOT
                    NULL,
                    channel
                    TEXT
                    NOT
                    NULL,
                    nick
                    TEXT
                    NOT
                    NULL,
                    ident
                    TEXT
                    DEFAULT
                    '',
                    host
                    TEXT
                    DEFAULT
                    '',
                    last_event
                    TEXT
                    NOT
                    NULL,
                    reason
                    TEXT
                    DEFAULT
                    '',
                    last_ts
                    INTEGER
                    NOT
                    NULL,
                    join_ts
                    INTEGER,
                    PRIMARY
                    KEY
                (
                    botId,
                    channel,
                    nick
                )
                    )
                """,
                (),
            )
        except Exception:
            pass

    def _upsert(self, channel: str, nick: str, ident: str, host: str,
                event: str, reason: str = "", join_ts: int | None = None):

        now = int(time.time())
        reason = reason or ""
        ident = ident or ""
        host = host or ""

        try:
            # UPDATE first
            self.sql.sqlite3_update(
                """
                UPDATE SEEN
                SET ident      = ?,
                    host       = ?,
                    last_event = ?,
                    reason     = ?,
                    last_ts    = ?,
                    join_ts    = COALESCE(?, join_ts)
                WHERE botId = ?
                  AND channel = ?
                  AND nick = ?
                """,
                (
                    ident, host, event, reason, now, join_ts,
                    self.bot.botId, channel, nick
                )
            )

            # INSERT if missing
            self.sql.sqlite3_update(
                """
                INSERT
                OR IGNORE INTO SEEN
                    (botId, channel, nick, ident, host, last_event, reason, last_ts, join_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.bot.botId, channel, nick,
                    ident, host, event, reason, now, join_ts
                )
            )

        except Exception as e:
            logger.error(f"[seen] upsert failed: {e}")

    # ---------------------------------------------------------------------
    # Command: .seen
    # ---------------------------------------------------------------------

    def cmd_seen(self, bot, channel, feedback, nick, host, msg):
        args = (msg or "").strip().split()
        sub = args[0].lower() if args else ""

        # unified help
        if sub in ("help", "h", "?"):
            pm = getattr(bot, "plugin_manager", None)
            if pm and hasattr(pm, "send_command_help"):
                return pm.send_command_help(bot, feedback, channel, nick, host, "seen")
            bot.send_message(feedback, f"Usage: {config.char}seen <nick|pattern|ident@host> | {config.char}seen stats [#channel]")
            return

        if sub == "stats":
            ch = args[1] if len(args) > 1 else channel
            return self._cmd_stats(bot, feedback, ch)

        pattern = (msg or "").strip()
        if not pattern:
            bot.send_message(feedback, f"Usage: {config.char}seen <nick|pattern|ident@host>")
            return

        out_lines = self._lookup_seen(pattern, channel_hint=channel)
        for line in out_lines:
            bot.send_message(feedback, line)

    def _cmd_stats(self, bot, feedback, channel: str):
        try:
            # total rows in channel
            r1 = self.sql.sqlite_select(
                "SELECT COUNT(*) FROM SEEN WHERE botId = ? AND channel = ?",
                (self.bot.botId, channel),
            )
            total = r1[0][0] if r1 and r1[0] else 0

            # top events
            r2 = self.sql.sqlite_select(
                """
                SELECT last_event, COUNT(*) as c
                  FROM SEEN
                 WHERE botId = ? AND channel = ?
                 GROUP BY last_event
                 ORDER BY c DESC
                 LIMIT 5
                """,
                (self.bot.botId, channel),
            )

            parts = [f"📊 seen stats for {channel}: {total} tracked users"]
            if r2:
                parts.append("Events: " + ", ".join([f"{ev}:{c}" for (ev, c) in r2]))
            bot.send_message(feedback, parts[0])
            if len(parts) > 1:
                bot.send_message(feedback, parts[1])

        except Exception as e:
            bot.send_message(feedback, f"❌ Error: {e}")

    # ---------------------------------------------------------------------
    # Lookup logic
    # ---------------------------------------------------------------------

    def _lookup_seen(self, pattern: str, channel_hint: str):
        """
        Supports:
          - exact nick
          - wildcard pattern (* ?)
          - ident@host (wildcards allowed)
        """
        pattern = (pattern or "").strip()

        # ident@host mask?
        if "@" in pattern and not pattern.startswith("#"):
            ident_pat, host_pat = pattern.split("@", 1)
            ident_pat = ident_pat.strip()
            host_pat = host_pat.strip()
            return self._lookup_by_mask(channel_hint, ident_pat, host_pat)

        # wildcard nick?
        if "*" in pattern or "?" in pattern:
            return self._lookup_by_nick_pattern(channel_hint, pattern)

        # exact nick
        return self._lookup_by_nick_exact(channel_hint, pattern)

    def _lookup_by_nick_exact(self, channel: str, nick: str):
        try:
            row = self.sql.sqlite_select(
                """
                SELECT nick, ident, host, last_event, reason, last_ts
                  FROM SEEN
                 WHERE botId = ? AND channel = ? AND nick = ?
                """,
                (self.bot.botId, channel, nick),
            )
            if not row:
                return [f"❌ I haven't seen {nick} in {channel}."]
            return [self._format_row(channel, row[0])]
        except Exception as e:
            return [f"❌ Error: {e}"]

    def _lookup_by_nick_pattern(self, channel: str, pat: str):
        like = self._wildcard_to_like(pat)
        try:
            rows = self.sql.sqlite_select(
                """
                SELECT nick, ident, host, last_event, reason, last_ts
                  FROM SEEN
                 WHERE botId = ? AND channel = ? AND nick LIKE ? ESCAPE '\\'
                 ORDER BY last_ts DESC
                 LIMIT 5
                """,
                (self.bot.botId, channel, like),
            )
            if not rows:
                return [f"❌ No matches for {pat} in {channel}."]
            lines = [f"Matches for {pat} in {channel}:"]
            for r in rows:
                lines.append("  " + self._format_row(channel, r))
            return lines
        except Exception as e:
            return [f"❌ Error: {e}"]

    def _lookup_by_mask(self, channel: str, ident_pat: str, host_pat: str):
        ident_like = self._wildcard_to_like(ident_pat or "*")
        host_like = self._wildcard_to_like(host_pat or "*")
        try:
            rows = self.sql.sqlite_select(
                """
                SELECT nick, ident, host, last_event, reason, last_ts
                  FROM SEEN
                 WHERE botId = ? AND channel = ?
                   AND ident LIKE ? ESCAPE '\\'
                   AND host  LIKE ? ESCAPE '\\'
                 ORDER BY last_ts DESC
                 LIMIT 5
                """,
                (self.bot.botId, channel, ident_like, host_like),
            )
            if not rows:
                return [f"❌ No matches for {ident_pat}@{host_pat} in {channel}."]
            lines = [f"Matches for {ident_pat}@{host_pat} in {channel}:"]
            for r in rows:
                lines.append("  " + self._format_row(channel, r))
            return lines
        except Exception as e:
            return [f"❌ Error: {e}"]

    def _wildcard_to_like(self, s: str) -> str:
        """
        Convert IRC-style wildcard pattern (* ?) to SQL LIKE pattern.
        Escapes %, _ and \ then replaces * -> %, ? -> _.
        """
        s = s or ""
        s = s.replace("\\", "\\\\")
        s = s.replace("%", "\\%").replace("_", "\\_")
        s = s.replace("*", "%").replace("?", "_")
        return s

    def _format_row(self, channel: str, row):
        nick, ident, host, last_event, reason, last_ts = row
        when = self._format_age(last_ts)
        mask = ""
        if ident or host:
            mask = f" ({ident}@{host})"

        ev = (last_event or "").upper()
        if ev == "JOIN":
            action = "joined"
        elif ev == "PART":
            action = "left"
        elif ev == "QUIT":
            action = "quit"
        elif ev == "KICK":
            action = "was kicked"
        elif ev == "NICK":
            action = "changed nick"
        elif ev == "MSG":
            action = "spoke"
        else:
            action = ev.lower() or "seen"

        extra = f" — {reason}" if reason else ""
        return f"{nick}{mask} {action} in {channel} {when}{extra}"

    def _format_age(self, ts: int) -> str:
        try:
            now = int(time.time())
            diff = max(0, now - int(ts))

            if diff < 60:
                return f"{diff}s ago"
            if diff < 3600:
                return f"{diff//60}m ago"
            if diff < 86400:
                return f"{diff//3600}h ago"
            return f"{diff//86400}d ago"
        except Exception:
            return "some time ago"

    # ---------------------------------------------------------------------
    # Hooks
    # ---------------------------------------------------------------------

    def h_join(self, channel: str, nick: str, ident: str, host: str, **kwargs):
        self._upsert(channel, nick, ident, host, "JOIN", "")

    def h_part(self, channel: str, nick: str, ident: str = "", host: str = "", reason: str = "", **kwargs):
        self._upsert(channel, nick, ident, host, "PART", reason or "")

    def h_quit(self, nick: str, message: str = "", ident: str = "", host: str = "", **kwargs):
        # Quit affects all channels where the nick exists.
        try:
            chans = self.sql.sqlite_select(
                "SELECT channel FROM SEEN WHERE botId = ? AND nick = ?",
                (self.bot.botId, nick),
            )
            for (ch,) in chans or []:
                self._upsert(ch, nick, ident, host, "QUIT", message or "")
        except Exception:
            pass

    def h_kick(self, channel: str, target: str, kicker: str, message: str = "", ident: str = "", host: str = "", **kwargs):
        reason = f"by {kicker}"
        if message:
            reason += f": {message}"
        self._upsert(channel, target, ident, host, "KICK", reason)

    def h_nick(self, oldnick: str, newnick: str, **kwargs):
        # Update across channels: keep row key by channel+nick, so we replace old with new.
        try:
            rows = self.sql.sqlite_select(
                "SELECT channel, ident, host FROM SEEN WHERE botId = ? AND nick = ?",
                (self.bot.botId, oldnick),
            )
            for channel, ident, host in rows or []:
                # delete old
                self.sql.sqlite3_update(
                    "DELETE FROM SEEN WHERE botId = ? AND channel = ? AND nick = ?",
                    (self.bot.botId, channel, oldnick),
                )
                # upsert new with NICK event
                self._upsert(channel, newnick, ident or "", host or "", "NICK", f"from {oldnick}")
        except Exception:
            pass

    def h_privmsg(self, channel: str, nick: str, ident: str, host: str, message: str = "", **kwargs):
        # Optional: track last message (avoid saving commands spam if you want)
        # Example: ignore lines starting with bot command char:
        try:
            if not TRACK_MESSAGES:
                return
            text = (message or "").strip()
            if not text:
                return
            # optionally ignore commands
            if text.startswith(config.char):
                return
            self._upsert(channel, nick, ident, host, "MSG", text)
        except Exception:
            pass


def register(bot):
    return Plugin(bot)