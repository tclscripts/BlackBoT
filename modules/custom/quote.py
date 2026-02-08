"""
Quote Plugin (single main command: .quote)
==========================================

Subcommands:
  .quote                 -> random
  .quote <id>            -> by id
  .quote <search text>   -> search (random match)
  .quote add <quote>
  .quote add <author> | <quote>
  .quote del <id>        -> restricted (see ACCESS below)
  .quote stats

Help:
  .quote help / .quote ? -> help is generated from command description (multiline),
                            and lines with (FLAGS) are auto-filtered by send_command_help().

Notes:
- BlackBoT access system uses bot.check_access() (GLOBALACCESS/CHANNELACCESS + VALIDACCESS).
- For restricted subcommands we treat "FLAGS" as ANY-OF (OR) by default (e.g. NnmM means
  user must have N OR n OR m OR M).

Author: BlackBoT Team
Version: 1.0.0
"""

from datetime import datetime
from core.plugin_manager import PluginBase

PLUGIN_INFO = {
    "name": "quote",
    "version": "1.0.0",
    "author": "BlackBoT Team",
    "description": "Quote database with subcommands (.quote add/del/stats) and flags on del",
    "dependencies": [],
}

# ---------------------------------------------------------------------------
# Single source of truth for access rules
# ---------------------------------------------------------------------------

ACCESS = {
    # delete: BossOwner/Owner/Manager (OR semantics)
    "del": "NnmM",
    # if you ever want to restrict others, add here:
    # "add": "mM",
    # "stats": "mM",
}


class Plugin(PluginBase):
    def __init__(self, bot):
        super().__init__(bot)
        self.sql = bot.sql

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def on_load(self):
        self._init_database()

        del_flags = ACCESS["del"]

        # Help is generated from this multiline description.
        # Lines that contain "(...)" can be filtered by your send_command_help().
        desc = (
            ".quote                 -> random / id / search\n"
            ".quote add <text>       -> add quote\n"
            f".quote del <id>        -> delete quote ({del_flags})\n"
            ".quote stats            -> statistics\n"
            ".quote help             -> show this help"
        )

        self.register_command(
            "quote",
            self.cmd_quote,
            flags="-",
            description=desc,
        )

    # -----------------------------------------------------------------------
    # DB init
    # -----------------------------------------------------------------------

    def _init_database(self):
        create_table_sql = """
        CREATE TABLE IF NOT EXISTS QUOTES (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quote TEXT NOT NULL,
            author TEXT,
            added_by TEXT NOT NULL,
            added_at INTEGER NOT NULL,
            channel TEXT
        )
        """
        try:
            self.sql.sqlite3_update(create_table_sql, ())
        except Exception as e:
            # avoid spamming channels; optionally log to monitor if available
            try:
                self.bot.send_message(self.bot.monitor, f"[quote] DB init error: {e}")
            except Exception:
                pass

    # -----------------------------------------------------------------------
    # Access helper (OR on flags)
    # -----------------------------------------------------------------------

    def _has_any_access(self, channel: str, nick: str, host: str, any_flags: str) -> bool:
        """
        Returns True if user has ANY of the provided flags (OR semantics).
        Uses BlackBoT's bot.check_access().
        """
        # resolve_user_id should be provided by PluginBase; if not, add it there.
        user_id = getattr(self, "resolve_user_id", None)
        if not callable(user_id):
            # fallback (keeps plugin standalone if PluginBase lacks resolve_user_id)
            info = self.bot.sql.sqlite_handle(self.bot.botId, nick, host)
            uid = info[0] if info else None
        else:
            uid = self.resolve_user_id(nick, host)

        if not uid:
            return False

        flags = (any_flags or "").strip()
        if not flags or flags == "-":
            return True

        for f in flags:
            if f and self.bot.check_access(channel, uid, f):
                return True

        return False

    def _deny_silent(self):
        # silent deny (no message)
        return

    # -----------------------------------------------------------------------
    # Main command
    # -----------------------------------------------------------------------

    def cmd_quote(self, bot, channel, feedback, nick, host, msg):
        """
        .quote [subcommand...]

        Subcommands:
          add, del, stats, help
        Otherwise:
          random / id / search
        """
        args = (msg or "").strip().split()
        sub = args[0].lower() if args else ""

        # help
        if sub in ("help", "h", "?"):
            pm = getattr(self.bot, "plugin_manager", None)
            if pm and hasattr(pm, "send_command_help"):
                return pm.send_command_help(bot, feedback, channel, nick, host, "quote")
            # fallback help if plugin_manager doesn't have send_command_help
            bot.send_message(feedback, "Usage: .quote [id|search] | .quote add <text> | .quote del <id> | .quote stats")
            return

        # no args -> random
        if not args:
            return self._quote_random(bot, feedback)

        # add
        if sub == "add":
            rest = (msg or "")[len(args[0]):].strip()
            return self._quote_add(bot, channel, feedback, nick, rest)

        # del (restricted)
        if sub in ("del", "delete", "rm", "remove"):
            if not self._has_any_access(channel, nick, host, ACCESS["del"]):
                return self._deny_silent()

            if len(args) < 2 or not args[1].isdigit():
                bot.send_message(feedback, "Usage: .quote del <id>")
                return

            return self._quote_del(bot, feedback, int(args[1]))

        # stats
        if sub in ("stats", "stat", "info"):
            return self._quote_stats(bot, feedback)

        # normal mode: id or search
        if args[0].isdigit():
            return self._quote_by_id(bot, feedback, int(args[0]))

        return self._quote_search(bot, feedback, " ".join(args))

    # -----------------------------------------------------------------------
    # Actions
    # -----------------------------------------------------------------------

    def _quote_add(self, bot, channel, feedback, nick, text: str):
        if not text:
            bot.send_message(feedback, "Usage: .quote add <quote> OR .quote add <author> | <quote>")
            return

        author = None
        quote = text.strip()

        if "|" in quote:
            parts = quote.split("|", 1)
            author = parts[0].strip() or None
            quote = parts[1].strip()

        if not quote:
            bot.send_message(feedback, "❌ Quote cannot be empty")
            return

        try:
            insert_sql = """
            INSERT INTO QUOTES (quote, author, added_by, added_at, channel)
            VALUES (?, ?, ?, ?, ?)
            """
            self.sql.sqlite3_insert(
                insert_sql,
                (quote, author, nick, int(datetime.now().timestamp()), channel),
            )

            result = self.sql.sqlite_select("SELECT last_insert_rowid()", ())
            quote_id = result[0][0] if result else "?"

            bot.send_message(feedback, f"✅ Quote #{quote_id} added!")
        except Exception as e:
            bot.send_message(feedback, f"❌ Error adding quote: {e}")

    def _quote_del(self, bot, feedback, quote_id: int):
        try:
            row = self.sql.sqlite_select("SELECT id FROM QUOTES WHERE id = ?", (quote_id,))
            if not row:
                bot.send_message(feedback, "❌ Quote not found")
                return

            self.sql.sqlite3_update("DELETE FROM QUOTES WHERE id = ?", (quote_id,))
            bot.send_message(feedback, f"🗑️ Quote #{quote_id} deleted")
        except Exception as e:
            bot.send_message(feedback, f"❌ Error deleting quote: {e}")

    def _quote_stats(self, bot, feedback):
        try:
            result = self.sql.sqlite_select("SELECT COUNT(*) FROM QUOTES", ())
            total = result[0][0] if result and result[0] else 0

            top = self.sql.sqlite_select(
                "SELECT added_by, COUNT(*) as count "
                "FROM QUOTES GROUP BY added_by ORDER BY count DESC LIMIT 3",
                (),
            )

            bot.send_message(feedback, f"📊 Quote Stats: {total} total quotes")
            if top:
                bot.send_message(feedback, "Top contributors:")
                for i, (user, count) in enumerate(top, 1):
                    bot.send_message(feedback, f"  {i}. {user}: {count} quotes")
        except Exception as e:
            bot.send_message(feedback, f"❌ Error: {e}")

    def _quote_random(self, bot, feedback):
        try:
            result = self.sql.sqlite_select(
                "SELECT id, quote, author FROM QUOTES ORDER BY RANDOM() LIMIT 1",
                (),
            )
            return self._emit_quote(bot, feedback, result)
        except Exception as e:
            bot.send_message(feedback, f"❌ Error: {e}")

    def _quote_by_id(self, bot, feedback, quote_id: int):
        try:
            result = self.sql.sqlite_select(
                "SELECT id, quote, author FROM QUOTES WHERE id = ?",
                (quote_id,),
            )
            return self._emit_quote(bot, feedback, result)
        except Exception as e:
            bot.send_message(feedback, f"❌ Error: {e}")

    def _quote_search(self, bot, feedback, term: str):
        try:
            search_term = f"%{term}%"
            result = self.sql.sqlite_select(
                "SELECT id, quote, author FROM QUOTES "
                "WHERE quote LIKE ? OR author LIKE ? "
                "ORDER BY RANDOM() LIMIT 1",
                (search_term, search_term),
            )
            return self._emit_quote(bot, feedback, result, not_found="❌ No quotes found for that search")
        except Exception as e:
            bot.send_message(feedback, f"❌ Error: {e}")

    def _emit_quote(self, bot, feedback, result, not_found="❌ No quotes found"):
        if result and result[0]:
            quote_id, quote, author = result[0]
            if author:
                bot.send_message(feedback, f"Quote #{quote_id} by {author}: {quote}")
            else:
                bot.send_message(feedback, f"Quote #{quote_id}: {quote}")
        else:
            bot.send_message(feedback, not_found)


def register(bot):
    # compatible with plugin_manager patched (calls on_load() for PluginBase returns)
    return Plugin(bot)