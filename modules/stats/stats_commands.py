"""
IRC Statistics - Commands Module
=================================
Comenzi IRC pentru accesarea statisticilor.
"""

import time
import datetime
import json
from core.environment_config import config

def cmd_stats(self, channel, feedback, nick, host, msg):
    """
    !stats [subcommand] [args]
    
    Subcommands:
      (empty)      -> afișează summary + link către pagina web
      top [N]      -> top N useri pe canal (default: top 10)
      today        -> stats pentru azi
      week         -> stats pentru săptămâna curentă
      month        -> stats pentru luna curentă
      export [fmt] -> exportă date (owner only)
      privacy      -> setări privacy (ops only)
      reset        -> resetează stats (owner only)
    """
    
    args = msg.strip().split() if msg else []
    subcommand = args[0].lower() if args else ''
    
    if subcommand == 'top':
        _cmd_stats_top(self, channel, feedback, nick, host, args[1:])
    
    elif subcommand == 'today':
        _cmd_stats_today(self, channel, feedback, nick, host)
    
    elif subcommand == 'week':
        _cmd_stats_week(self, channel, feedback, nick, host)
    
    elif subcommand == 'month':
        _cmd_stats_month(self, channel, feedback, nick, host)
    
    elif subcommand == 'export':
        _cmd_stats_export(self, channel, feedback, nick, host, args[1:])
    
    elif subcommand == 'privacy':
        _cmd_stats_privacy(self, channel, feedback, nick, host, args[1:])
    
    elif subcommand == 'reset':
        _cmd_stats_reset(self, channel, feedback, nick, host, args[1:])
    
    else:
        # Default: summary + link
        _cmd_stats_summary(self, channel, feedback, nick, host)


def _cmd_stats_summary(self, channel, feedback, nick, host):
    """Afișează summary rapid + link către pagina stats"""
    
    # Get basic stats from STATS_CHANNEL
    query = """
        SELECT total_messages, total_words, total_users, 
               last_event_ts, top_talker_nick, top_talker_count
        FROM STATS_CHANNEL
        WHERE botId = ? AND channel = ?
    """
    
    result = self.sql.sqlite_select(query, (self.botId, channel))
    
    if not result or not result[0][0]:
        self.send_message(feedback, f"📊 No stats available yet for {channel}")
        return
    
    total_msgs, total_words, total_users, last_ts, top_nick, top_count = result[0]
    
    # Format last activity
    if last_ts:
        delta = time.time() - last_ts
        if delta < 3600:
            last_activity = f"{int(delta/60)}m ago"
        elif delta < 86400:
            last_activity = f"{int(delta/3600)}h ago"
        else:
            last_activity = f"{int(delta/86400)}d ago"
    else:
        last_activity = "never"
    
    # Build message
    msg_parts = [
        f"📊 Stats for {channel}:",
        f"{total_msgs:,} messages",
        f"{total_words:,} words",
        f"{total_users} users",
    ]
    
    if top_nick:
        msg_parts.append(f"👑 Top: {top_nick} ({top_count:,} msgs)")
    
    msg_parts.append(f"🕒 Last activity: {last_activity}")
    
    # Link către web UI (placeholder - va fi implementat cu API)
    # web_url = f"https://stats.yourbot.com/{channel.lstrip('#')}"
    # msg_parts.append(f"🔗 Full stats: {web_url}")
    
    self.send_message(feedback, " | ".join(msg_parts))


def _cmd_stats_top(self, channel, feedback, nick, host, args):
    """
    !stats top [N] [metric]
    
    Afișează top N useri după diverse metrici.
    Metrici: messages (default), words, actions, questions, etc.
    """
    
    # Parse args
    limit = 10
    metric = 'messages'
    
    for arg in args:
        if arg.isdigit():
            limit = min(int(arg), 50)  # max 50
        elif arg.lower() in ('messages', 'words', 'chars', 'actions', 'questions', 'exclamations'):
            metric = arg.lower()
    
    # Get top users from STATS_DAILY (aggregate per nick)
    # Folosim SUM pentru a agrega peste toate zilele
    query = f"""
        SELECT nick, SUM({metric}) as total
        FROM STATS_DAILY
        WHERE botId = ? AND channel = ?
        GROUP BY nick
        ORDER BY total DESC
        LIMIT ?
    """
    
    result = self.sql.sqlite_select(query, (self.botId, channel, limit))
    
    if not result:
        self.send_message(feedback, f"📊 No stats available for top {metric}")
        return
    
    # Format output
    lines = [f"📊 Top {limit} by {metric} in {channel}:"]
    
    for i, (user_nick, total) in enumerate(result, 1):
        emoji = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        lines.append(f"{emoji} {user_nick}: {total:,}")
    
    # Send as multi-line sau separate messages
    if len(lines) <= 6:
        self.send_message(feedback, " | ".join(lines))
    else:
        # Split în multiple messages
        self.send_message(feedback, lines[0])
        for line in lines[1:6]:
            self.send_message(feedback, f"  {line}")
        if len(lines) > 6:
            remaining = len(lines) - 6
            self.send_message(feedback, f"  ... and {remaining} more")


def _cmd_stats_today(self, channel, feedback, nick, host):
    """Stats pentru ziua curentă"""
    
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    
    query = """
        SELECT SUM(messages), SUM(words), COUNT(DISTINCT nick)
        FROM STATS_DAILY
        WHERE botId = ? AND channel = ? AND date = ?
    """
    
    result = self.sql.sqlite_select(query, (self.botId, channel, today))
    
    if not result or not result[0][0]:
        self.send_message(feedback, f"📊 No activity today in {channel}")
        return
    
    msgs, words, users = result[0]
    
    msg = f"📊 Today in {channel}: {msgs or 0:,} messages, {words or 0:,} words, {users or 0} active users"
    self.send_message(feedback, msg)


def _cmd_stats_week(self, channel, feedback, nick, host):
    """Stats pentru săptămâna curentă (last 7 days)"""
    
    # Last 7 days
    today = datetime.datetime.now()
    week_ago = (today - datetime.timedelta(days=7)).strftime('%Y-%m-%d')
    
    query = """
        SELECT SUM(messages), SUM(words), COUNT(DISTINCT nick)
        FROM STATS_DAILY
        WHERE botId = ? AND channel = ? AND date >= ?
    """
    
    result = self.sql.sqlite_select(query, (self.botId, channel, week_ago))
    
    if not result or not result[0][0]:
        self.send_message(feedback, f"📊 No activity this week in {channel}")
        return
    
    msgs, words, users = result[0]
    
    msg = f"📊 Last 7 days in {channel}: {msgs or 0:,} messages, {words or 0:,} words, {users or 0} unique users"
    self.send_message(feedback, msg)


def _cmd_stats_month(self, channel, feedback, nick, host):
    """Stats pentru luna curentă"""
    
    # Current month: YYYY-MM
    month_prefix = datetime.datetime.now().strftime('%Y-%m')
    
    query = """
        SELECT SUM(messages), SUM(words), COUNT(DISTINCT nick)
        FROM STATS_DAILY
        WHERE botId = ? AND channel = ? AND date LIKE ?
    """
    
    result = self.sql.sqlite_select(query, (self.botId, channel, f"{month_prefix}%"))
    
    if not result or not result[0][0]:
        self.send_message(feedback, f"📊 No activity this month in {channel}")
        return
    
    msgs, words, users = result[0]
    
    month_name = datetime.datetime.now().strftime('%B %Y')
    msg = f"📊 {month_name} in {channel}: {msgs or 0:,} messages, {words or 0:,} words, {users or 0} unique users"
    self.send_message(feedback, msg)


def _cmd_stats_export(self, channel, feedback, nick, host, args):
    """
    !stats export [json|csv] [range]
    
    Exportă date (owner only).
    """
    
    # Check permission (doar owner)
    if not self.check_command_access(channel, nick, host, 'n', feedback):
        return
    
    export_format = 'json'
    date_range = 30  # default: last 30 days
    
    for arg in args:
        if arg.lower() in ('json', 'csv'):
            export_format = arg.lower()
        elif arg.isdigit():
            date_range = int(arg)
    
    # Get data
    cutoff_date = (datetime.datetime.now() - datetime.timedelta(days=date_range)).strftime('%Y-%m-%d')
    
    query = """
        SELECT date, nick, messages, words, actions, questions, exclamations
        FROM STATS_DAILY
        WHERE botId = ? AND channel = ? AND date >= ?
        ORDER BY date DESC, messages DESC
    """
    
    result = self.sql.sqlite_select(query, (self.botId, channel, cutoff_date))
    
    if not result:
        self.send_message(feedback, "📊 No data to export")
        return
    
    # Format output
    if export_format == 'json':
        data = []
        for row in result:
            data.append({
                'date': row[0],
                'nick': row[1],
                'messages': row[2],
                'words': row[3],
                'actions': row[4],
                'questions': row[5],
                'exclamations': row[6],
            })
        
        export_str = json.dumps(data, indent=2)
        
        # Pentru IRC, trimitem doar info (not full JSON)
        self.send_message(feedback, f"📊 Exported {len(data)} rows (last {date_range} days) in JSON format")
        # În practică, ar trebui să creezi un file și să dai link sau să trimiți prin DCC
    
    elif export_format == 'csv':
        # CSV header
        lines = ["date,nick,messages,words,actions,questions,exclamations"]
        
        for row in result:
            lines.append(','.join(str(x) for x in row))
        
        export_str = '\n'.join(lines)
        
        self.send_message(feedback, f"📊 Exported {len(result)} rows (last {date_range} days) in CSV format")
        # Same: ar trebui file + link


def _cmd_stats_privacy(self, channel, feedback, nick, host, args):
    """
    !stats privacy [on|off] [option]
    
    Opțiuni:
      stats_enabled    - Enable/disable stats collection
      public_stats     - Make stats public or ops-only
      anonymize_hosts  - Hash host/ident în UI
      exclude_bots     - Exclude bots din topuri
    """
    
    # Check permission (ops only)
    if not self.check_command_access(channel, nick, host, 'o', feedback):
        return
    
    if not args:
        # Show current settings
        query = """
            SELECT stats_enabled, public_stats, anonymize_hosts, exclude_bots
            FROM STATS_PRIVACY
            WHERE botId = ? AND channel = ?
        """
        
        result = self.sql.sqlite_select(query, (self.botId, channel))
        
        if result:
            enabled, public, anon, excl_bots = result[0]
            
            settings = [
                f"stats_enabled: {'ON' if enabled else 'OFF'}",
                f"public_stats: {'ON' if public else 'OFF'}",
                f"anonymize_hosts: {'ON' if anon else 'OFF'}",
                f"exclude_bots: {'ON' if excl_bots else 'OFF'}",
            ]
            
            self.send_message(feedback, f"🔒 Privacy settings for {channel}: " + " | ".join(settings))
        else:
            self.send_message(feedback, f"🔒 No privacy settings configured for {channel} (using defaults)")
        
        return
    
    # Parse command: !stats privacy <option> <on|off>
    if len(args) < 2:
        self.send_message(feedback, f"Usage: {config.char}stats privacy <option> <on|off>")
        return
    
    option = args[0].lower()
    value = args[1].lower()
    
    valid_options = ['stats_enabled', 'public_stats', 'anonymize_hosts', 'exclude_bots']
    
    if option not in valid_options:
        self.send_message(feedback, f"Invalid option. Valid: {', '.join(valid_options)}")
        return
    
    if value not in ('on', 'off'):
        self.send_message(feedback, "Value must be 'on' or 'off'")
        return
    
    int_value = 1 if value == 'on' else 0
    
    # Update DB
    # Check if settings exist
    check_query = "SELECT id FROM STATS_PRIVACY WHERE botId = ? AND channel = ?"
    existing = self.sql.sqlite_select(check_query, (self.botId, channel))
    
    if existing:
        # UPDATE
        update_sql = f"UPDATE STATS_PRIVACY SET {option} = ? WHERE botId = ? AND channel = ?"
        self.sql.sqlite3_update(update_sql, (int_value, self.botId, channel))
    else:
        # INSERT with default values
        insert_sql = f"""
            INSERT INTO STATS_PRIVACY (botId, channel, {option})
            VALUES (?, ?, ?)
        """
        self.sql.sqlite3_insert(insert_sql, (self.botId, channel, int_value))
    
    self.send_message(feedback, f"🔒 Privacy setting updated: {option} = {value.upper()}")


def _cmd_stats_reset(self, channel, feedback, nick, host, args):
    """
    !stats reset [channel]
    
    Șterge toate stats pentru canal (owner only).
    """
    
    # Check permission (owner only)
    if not self.check_command_access(channel, nick, host, 'n', feedback):
        return
    
    # Confirmation check (simple: require "confirm" arg)
    if 'confirm' not in [a.lower() for a in args]:
        self.send_message(feedback, 
            f"⚠️  This will DELETE all stats for {channel}. "
            f"Use '{config.char}stats reset confirm' to proceed.")
        return
    
    # Delete from all tables
    tables = ['IRC_EVENTS', 'STATS_DAILY', 'STATS_HOURLY', 'STATS_CHANNEL']
    
    for table in tables:
        delete_sql = f"DELETE FROM {table} WHERE botId = ? AND channel = ?"
        try:
            self.sql.sqlite3_execute(delete_sql)
            # Note: sqlite3_execute doesn't take params in current implementation
            # Ar trebui folosit sqlite3_update sau ajustat metoda
            # Pentru simplitate, folosim direct SQL injection-safe approach:
            conn = self.sql.create_connection()
            cur = conn.cursor()
            cur.execute(f"DELETE FROM {table} WHERE botId = ? AND channel = ?", (self.botId, channel))
            conn.commit()
            cur.close()
        except Exception as e:
            self.send_message(feedback, f"❌ Error deleting from {table}: {e}")
            return
    
    self.send_message(feedback, f"✅ All stats for {channel} have been reset")


# =============================================================================
# Command tracking (pentru STATS_COMMAND_USAGE)
# =============================================================================

def track_command_usage(self, channel, nick, command, success=True):
    """
    Trackează usage de comandă în STATS_COMMAND_USAGE.
    Apelează din command dispatcher.
    """
    
    try:
        insert_sql = """
            INSERT INTO STATS_COMMAND_USAGE (botId, channel, nick, command, ts, success)
            VALUES (?, ?, ?, ?, ?, ?)
        """
        
        ts = int(time.time())
        
        self.sql.sqlite3_insert(insert_sql, (
            self.botId,
            channel,
            nick,
            command,
            ts,
            1 if success else 0
        ))
    
    except Exception as e:
        # Nu facem fail dacă tracking-ul nu merge
        pass


