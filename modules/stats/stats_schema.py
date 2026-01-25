"""
IRC Statistics Database Schema
================================
Event-sourcing approach: captură evenimente raw în DB, apoi agregări incrementale.

UPDATE (all funny + useful):
- STATS_WORDS_DAILY: top words per zi/canal
- STATS_DAILY: smiles/sads/laughs/angries/hearts
- STATS_LAST_SPOKEN: last message + last word per (botId, channel, nick)
- STATS_CHANNEL_RECORDS: recorduri per canal (longest line, most emojis, peak minute)
- STATS_REPLY_PAIRS: cine răspunde cui (simple graph)
- STATS_NICK_ACTIVITY: first_seen/last_seen per canal (retention/newcomers)
- STATS_BOT_LIFECYCLE: reconnects / disconnect timeline (optional)
"""

# =============================================================================
# TABELA 1: IRC_EVENTS (event sourcing - toate evenimentele raw)
# =============================================================================
CREATE_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS IRC_EVENTS (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    botId INTEGER NOT NULL,
    channel TEXT,               -- #chan sau NULL pentru PM/global events
    ts INTEGER NOT NULL,        -- unix timestamp
    event_type TEXT NOT NULL,   -- PRIVMSG|ACTION|JOIN|PART|QUIT|KICK|NICK|TOPIC|MODE|BOT

    -- User info
    nick TEXT NOT NULL,
    ident TEXT,
    host TEXT,

    -- Event payload (varies by type)
    message TEXT,           -- pentru PRIVMSG/ACTION/TOPIC sau BOT events message
    target_nick TEXT,       -- pentru KICK/NICK (new nick sau kicked user)
    reason TEXT,            -- pentru KICK/PART/QUIT
    mode_change TEXT,       -- pentru MODE (+o/-o/+b etc)

    -- Metrics (pre-calculated pentru viteză)
    words INTEGER DEFAULT 0,
    chars INTEGER DEFAULT 0,
    is_question INTEGER DEFAULT 0,     -- 1 dacă message conține '?'
    is_exclamation INTEGER DEFAULT 0,  -- 1 dacă message conține '!'
    has_url INTEGER DEFAULT 0,
    is_caps INTEGER DEFAULT 0          -- 1 dacă >50% uppercase
);
"""

CREATE_EVENTS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_events_channel_ts ON IRC_EVENTS(botId, channel, ts DESC);",
    "CREATE INDEX IF NOT EXISTS idx_events_nick ON IRC_EVENTS(botId, channel, nick, ts DESC);",
    "CREATE INDEX IF NOT EXISTS idx_events_type ON IRC_EVENTS(botId, channel, event_type, ts DESC);",
    "CREATE INDEX IF NOT EXISTS idx_events_ts ON IRC_EVENTS(ts DESC);",
]


# =============================================================================
# TABELA 2: STATS_DAILY (agregări zilnice per canal + nick)
# =============================================================================
CREATE_STATS_DAILY_TABLE = """
CREATE TABLE IF NOT EXISTS STATS_DAILY (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    botId INTEGER NOT NULL,
    channel TEXT NOT NULL,
    nick TEXT NOT NULL,
    date TEXT NOT NULL,  -- YYYY-MM-DD format

    -- Message stats
    messages INTEGER DEFAULT 0,
    actions INTEGER DEFAULT 0,  -- /me
    words INTEGER DEFAULT 0,
    chars INTEGER DEFAULT 0,

    -- Behavior stats
    questions INTEGER DEFAULT 0,
    exclamations INTEGER DEFAULT 0,
    urls INTEGER DEFAULT 0,
    caps_msgs INTEGER DEFAULT 0,

    -- Funny / emotive stats
    smiles INTEGER DEFAULT 0,
    sads INTEGER DEFAULT 0,
    laughs INTEGER DEFAULT 0,
    angries INTEGER DEFAULT 0,
    hearts INTEGER DEFAULT 0,

    -- Activity stats
    joins INTEGER DEFAULT 0,
    parts INTEGER DEFAULT 0,
    quits INTEGER DEFAULT 0,
    kicks_received INTEGER DEFAULT 0,
    kicks_given INTEGER DEFAULT 0,

    -- Time tracking
    first_seen_ts INTEGER,  -- primul event al zilei
    last_seen_ts INTEGER,   -- ultimul event al zilei

    -- Metrics
    avg_msg_length REAL DEFAULT 0.0,

    UNIQUE(botId, channel, nick, date)
);
"""

CREATE_STATS_DAILY_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_daily_channel_date ON STATS_DAILY(botId, channel, date DESC);",
    "CREATE INDEX IF NOT EXISTS idx_daily_nick ON STATS_DAILY(botId, channel, nick, date DESC);",
]

# =============================================================================
# TABELA 3: STATS_HOURLY (heatmap: hour x day of week)
# =============================================================================
CREATE_STATS_HOURLY_TABLE = """
CREATE TABLE IF NOT EXISTS STATS_HOURLY (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    botId INTEGER NOT NULL,
    channel TEXT NOT NULL,
    hour INTEGER NOT NULL,        -- 0-23
    day_of_week INTEGER NOT NULL, -- 0=Monday, 6=Sunday

    -- Aggregate counts (cumulative)
    total_messages INTEGER DEFAULT 0,
    total_words INTEGER DEFAULT 0,
    unique_users INTEGER DEFAULT 0,            -- JSON array sau comma-separated (lightweight)

    UNIQUE(botId, channel, hour, day_of_week)
);
"""

CREATE_STATS_HOURLY_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_hourly_channel ON STATS_HOURLY(botId, channel);",
]


# =============================================================================
# TABELA 4: STATS_CHANNEL (summary per canal - cache)
# =============================================================================
CREATE_STATS_CHANNEL_TABLE = """
CREATE TABLE IF NOT EXISTS STATS_CHANNEL (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    botId INTEGER NOT NULL,
    channel TEXT NOT NULL,

    -- Overall stats
    total_messages INTEGER DEFAULT 0,
    total_words INTEGER DEFAULT 0,
    total_users INTEGER DEFAULT 0,

    -- Top metrics (cached)
    top_talker_nick TEXT,
    top_talker_count INTEGER DEFAULT 0,

    most_active_day TEXT,      -- YYYY-MM-DD
    most_active_hour INTEGER,  -- 0-23

    -- Temporal
    first_event_ts INTEGER,
    last_event_ts INTEGER,
    last_aggregated_ts INTEGER,

    UNIQUE(botId, channel)
);
"""


# =============================================================================
# TABELA 5: STATS_PRIVACY (per-channel privacy settings)
# =============================================================================
CREATE_STATS_PRIVACY_TABLE = """
CREATE TABLE IF NOT EXISTS STATS_PRIVACY (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    botId INTEGER NOT NULL,
    channel TEXT NOT NULL,

    stats_enabled INTEGER DEFAULT 1,
    public_stats INTEGER DEFAULT 1,
    anonymize_hosts INTEGER DEFAULT 1,
    exclude_bots INTEGER DEFAULT 1,

    UNIQUE(botId, channel)
);
"""


# =============================================================================
# TABELA 6: STATS_COMMAND_USAGE (tracking comenzi bot)
# =============================================================================
CREATE_STATS_COMMANDS_TABLE = """
CREATE TABLE IF NOT EXISTS STATS_COMMAND_USAGE (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    botId INTEGER NOT NULL,
    channel TEXT,
    nick TEXT NOT NULL,
    command TEXT NOT NULL,
    ts INTEGER NOT NULL,
    success INTEGER DEFAULT 1
);
"""

CREATE_STATS_COMMANDS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_cmd_usage ON STATS_COMMAND_USAGE(botId, channel, command, ts DESC);",
]


# =============================================================================
# TABELA 7: STATS_WORDS_DAILY (Top words per canal, per zi)
# =============================================================================
CREATE_STATS_WORDS_DAILY_TABLE = """
CREATE TABLE IF NOT EXISTS STATS_WORDS_DAILY (
    botId INTEGER NOT NULL,
    channel TEXT NOT NULL,
    date TEXT NOT NULL,      -- YYYY-MM-DD
    word TEXT NOT NULL,
    count INTEGER DEFAULT 0,
    PRIMARY KEY (botId, channel, date, word)
);
"""

CREATE_STATS_WORDS_DAILY_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_words_chan_date ON STATS_WORDS_DAILY(botId, channel, date DESC);",
    "CREATE INDEX IF NOT EXISTS idx_words_word ON STATS_WORDS_DAILY(botId, channel, word);",
]


# =============================================================================
# TABELA 8: STATS_LAST_SPOKEN (last message/word per user per canal)
# =============================================================================
CREATE_STATS_LAST_SPOKEN_TABLE = """
CREATE TABLE IF NOT EXISTS STATS_LAST_SPOKEN (
    botId INTEGER NOT NULL,
    channel TEXT NOT NULL,
    nick TEXT NOT NULL,

    last_ts INTEGER NOT NULL,
    last_message TEXT,
    last_word TEXT,

    PRIMARY KEY (botId, channel, nick)
);
"""

CREATE_STATS_LAST_SPOKEN_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_last_spoken_chan_ts ON STATS_LAST_SPOKEN(botId, channel, last_ts DESC);",
]


# =============================================================================
# TABELA 9: STATS_CHANNEL_RECORDS (recorduri per canal)
# =============================================================================
CREATE_STATS_CHANNEL_RECORDS_TABLE = """
CREATE TABLE IF NOT EXISTS STATS_CHANNEL_RECORDS (
    botId INTEGER NOT NULL,
    channel TEXT NOT NULL,

    -- Longest message (chars)
    longest_chars INTEGER DEFAULT 0,
    longest_nick TEXT,
    longest_ts INTEGER,
    longest_message TEXT,

    -- Most emojis in one line
    most_emojis INTEGER DEFAULT 0,
    most_emojis_nick TEXT,
    most_emojis_ts INTEGER,
    most_emojis_message TEXT,

    -- Peak minute (burst)
    peak_minute_count INTEGER DEFAULT 0,
    peak_minute_ts INTEGER,       -- timestamp (minute bucket start)
    peak_minute_label TEXT,       -- optional formatted label

    PRIMARY KEY (botId, channel)
);
"""


# =============================================================================
# TABELA 10: STATS_REPLY_PAIRS (who replies to whom)
# =============================================================================
CREATE_STATS_REPLY_PAIRS_TABLE = """
CREATE TABLE IF NOT EXISTS STATS_REPLY_PAIRS (
    botId INTEGER NOT NULL,
    channel TEXT NOT NULL,
    from_nick TEXT NOT NULL,
    to_nick TEXT NOT NULL,
    count INTEGER DEFAULT 0,
    last_ts INTEGER,

    PRIMARY KEY (botId, channel, from_nick, to_nick)
);
"""

CREATE_STATS_REPLY_PAIRS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_reply_pairs_chan ON STATS_REPLY_PAIRS(botId, channel, count DESC);",
]


# =============================================================================
# TABELA 11: STATS_NICK_ACTIVITY (first_seen/last_seen per canal for retention)
# =============================================================================
CREATE_STATS_NICK_ACTIVITY_TABLE = """
CREATE TABLE IF NOT EXISTS STATS_NICK_ACTIVITY (
    botId INTEGER NOT NULL,
    channel TEXT NOT NULL,
    nick TEXT NOT NULL,

    first_seen_ts INTEGER,
    last_seen_ts INTEGER,

    PRIMARY KEY (botId, channel, nick)
);
"""

CREATE_STATS_NICK_ACTIVITY_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_nick_activity_last ON STATS_NICK_ACTIVITY(botId, channel, last_seen_ts DESC);",
    "CREATE INDEX IF NOT EXISTS idx_nick_activity_first ON STATS_NICK_ACTIVITY(botId, channel, first_seen_ts DESC);",
]


# =============================================================================
# TABELA 12: STATS_BOT_LIFECYCLE (connect/reconnect/disconnect timeline - optional)
# =============================================================================
CREATE_STATS_BOT_LIFECYCLE_TABLE = """
CREATE TABLE IF NOT EXISTS STATS_BOT_LIFECYCLE (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    botId INTEGER NOT NULL,
    ts INTEGER NOT NULL,
    event TEXT NOT NULL,        -- CONNECTED|DISCONNECTED|RECONNECTING|ERROR
    detail TEXT
);
"""

CREATE_STATS_BOT_LIFECYCLE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_bot_lifecycle_ts ON STATS_BOT_LIFECYCLE(botId, ts DESC);",
]


# =============================================================================
# MIGRATIONS (safe)
# =============================================================================
def _safe_exec(sql_instance, statement: str):
    try:
        sql_instance.sqlite3_execute(statement)
    except Exception:
        # Ignore (duplicate column, already exists, etc.)
        pass


def _column_exists(sql_instance, table: str, column: str) -> bool:
    try:
        rows = sql_instance.sqlite_select(f"PRAGMA table_info({table})")
        return any(r[1] == column for r in rows)  # r[1] = name
    except Exception:
        return False


def _apply_migrations(sql_instance):
    """
    Migrații pentru DB-uri deja existente:
    - STATS_DAILY: adaugă coloane funny dacă lipsesc.
    (fără spam în log)
    """
    cols = ["smiles", "sads", "laughs", "angries", "hearts"]
    for col in cols:
        if not _column_exists(sql_instance, "STATS_DAILY", col):
            sql_instance.sqlite3_execute(
                f"ALTER TABLE STATS_DAILY ADD COLUMN {col} INTEGER DEFAULT 0;"
            )

# =============================================================================
# Init function
# =============================================================================
def create_stats_tables(sql_instance):
    """
    Creează toate tabelele de statistici (idempotent).
    """
    # Raw events
    sql_instance.sqlite3_execute(CREATE_EVENTS_TABLE)
    for idx_sql in CREATE_EVENTS_INDEXES:
        sql_instance.sqlite3_execute(idx_sql)

    # Aggregation tables
    sql_instance.sqlite3_execute(CREATE_STATS_DAILY_TABLE)
    for idx_sql in CREATE_STATS_DAILY_INDEXES:
        sql_instance.sqlite3_execute(idx_sql)

    sql_instance.sqlite3_execute(CREATE_STATS_HOURLY_TABLE)
    for idx_sql in CREATE_STATS_HOURLY_INDEXES:
        sql_instance.sqlite3_execute(idx_sql)

    sql_instance.sqlite3_execute(CREATE_STATS_CHANNEL_TABLE)
    sql_instance.sqlite3_execute(CREATE_STATS_PRIVACY_TABLE)

    sql_instance.sqlite3_execute(CREATE_STATS_COMMANDS_TABLE)
    for idx_sql in CREATE_STATS_COMMANDS_INDEXES:
        sql_instance.sqlite3_execute(idx_sql)

    # Words
    sql_instance.sqlite3_execute(CREATE_STATS_WORDS_DAILY_TABLE)
    for idx_sql in CREATE_STATS_WORDS_DAILY_INDEXES:
        sql_instance.sqlite3_execute(idx_sql)

    # New “all funny/useful”
    sql_instance.sqlite3_execute(CREATE_STATS_LAST_SPOKEN_TABLE)
    for idx_sql in CREATE_STATS_LAST_SPOKEN_INDEXES:
        sql_instance.sqlite3_execute(idx_sql)

    sql_instance.sqlite3_execute(CREATE_STATS_CHANNEL_RECORDS_TABLE)

    sql_instance.sqlite3_execute(CREATE_STATS_REPLY_PAIRS_TABLE)
    for idx_sql in CREATE_STATS_REPLY_PAIRS_INDEXES:
        sql_instance.sqlite3_execute(idx_sql)

    sql_instance.sqlite3_execute(CREATE_STATS_NICK_ACTIVITY_TABLE)
    for idx_sql in CREATE_STATS_NICK_ACTIVITY_INDEXES:
        sql_instance.sqlite3_execute(idx_sql)

    sql_instance.sqlite3_execute(CREATE_STATS_BOT_LIFECYCLE_TABLE)
    for idx_sql in CREATE_STATS_BOT_LIFECYCLE_INDEXES:
        sql_instance.sqlite3_execute(idx_sql)

    # Migrations for existing DBs
    _apply_migrations(sql_instance)

    print("[STATS] All statistics tables created successfully")


# =============================================================================
# Utility: pre-calculate metrics pentru un message
# =============================================================================
def calculate_message_metrics(message):
    """
    Returnează dict cu metrics pentru un PRIVMSG/ACTION.
    """
    if not message:
        return {
            'words': 0,
            'chars': 0,
            'is_question': 0,
            'is_exclamation': 0,
            'has_url': 0,
            'is_caps': 0,
        }

    words = len(message.split())
    chars = len(message)

    has_url = 1 if ('http://' in message or 'https://' in message or 'www.' in message) else 0
    is_question = 1 if '?' in message else 0
    is_exclamation = 1 if '!' in message else 0

    letters = [c for c in message if c.isalpha()]
    if letters:
        uppercase_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
        is_caps = 1 if uppercase_ratio > 0.5 else 0
    else:
        is_caps = 0

    return {
        'words': words,
        'chars': chars,
        'is_question': is_question,
        'is_exclamation': is_exclamation,
        'has_url': has_url,
        'is_caps': is_caps,
    }
