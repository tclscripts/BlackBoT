# settings.py

# ───────────────────────────────────────────────
# Core Bot Identification Settings
# ───────────────────────────────────────────────

char = "!"  # Prefix for bot commands

nickname = "BlackBoT"  # Bot's main nickname
username = "Black"  # IRC ident (username) of the bot
altnick = "BlackBoT_"  # Alternate nickname, used if the main one is taken
realname = "BlackBoT: Python Edition"  # Realname shown in WHOIS
away = "No Away"  # Default away message for the bot

# ───────────────────────────────────────────────
# Server & Connection
# ───────────────────────────────────────────────

sourceIP = ""  # Source IP for connection ("" = auto)
sourcePort = 3337  # Outgoing port (used only if sourceIP is set)

# List of IRC servers the bot can connect to (format: "host port")
servers = [
    "irc.libera.chat 6667",
    "irc.undernet.org 6667"
]

port = 6667  # Default port used if not specified in `servers` list

ssl_use = 0  # Set to 1 to use SSL, 0 for unencrypted connections

# ───────────────────────────────────────────────
# Channel Settings
# ───────────────────────────────────────────────

channels = ["#BT"]  # Default channels the bot will join on first run

# ───────────────────────────────────────────────
# Hostname Format
# ───────────────────────────────────────────────
# Format used to identify a user (hostname)
# 1 = *!*@host
# 2 = *!user@host
# 3 = nick!user@host
# 4 = nick!*@*
# 5 = *!user@*
default_hostname = 1

# ───────────────────────────────────────────────
# Authentication & Access
# ───────────────────────────────────────────────

multiple_logins = 1  # Allow multiple logins from different hosts? (1 = yes, 0 = no)

# Time (in hours) after which inactive users are auto-deauthenticated
autoDeauthTime = 1

# ───────────────────────────────────────────────
# Channel Rejoin
# ───────────────────────────────────────────────

# Maximum number of attempts to rejoin a channel (in case of kick, etc.)
maxAttemptRejoin = 5

# ───────────────────────────────────────────────
# Private flood protection
# ───────────────────────────────────────────────

# Format: max:seconds (e.g., 5 messages in 3 seconds)
private_flood_limit = "5:3"

# Ignore time (minutes)
private_flood_time = 2

# ───────────────────────────────────────────────
# Delayed message queue (anti-flood mechanism)
# ───────────────────────────────────────────────

# Number of seconds
message_delay = 1

# ───────────────────────────────────────────────
# Automatically splits long bot messages into safe IRC-sized lines
# ───────────────────────────────────────────────

# Maximum number of chars
message_max_chars = 450

# ───────────────────────────────────────────────
# Database
# ───────────────────────────────────────────────

# Name of the SQLite database used by the bot
sqlite3_database = "work.db"

# ───────────────────────────────────────────────
# CTCP
# ───────────────────────────────────────────────

# Message sent in response to CTCP VERSION
version = "BlackBoT: Python Edition"