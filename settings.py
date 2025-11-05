# settings.py

# ───────────────────────────────────────────────
# Core Bot Identification Settings
# ───────────────────────────────────────────────

char = "!"                        # Prefix for bot commands
nickname = "BlackBoT"             # Bot's main nickname
username = "Black"                # IRC ident (username) of the bot
altnick = "BlackBoT_"             # Alternate nickname if the main one is taken
realname = "BlackBoT: Python Edition"  # Realname shown in WHOIS
away = "No Away"                  # Default away message

# ───────────────────────────────────────────────
# Auto-Update Settings
# ───────────────────────────────────────────────

autoUpdateEnabled = True          # Enable/disable auto-update
autoUpdateInterval = 60           # Verification interval in minutes

# ───────────────────────────────────────────────
# Server & Connection Settings
# ───────────────────────────────────────────────

sourceIP = ""                     # Source IP ("" = auto)
sourcePort = 3337                 # Outgoing port if sourceIP is used

servers = [                       # List of IRC servers ("host port")
    "irc.libera.chat 6667",
    "irc.undernet.org 6667"
]

port = 6667                       # Default port if not specified per-server

# ───────────────────────────────────────────────
# SSL Settings
# ───────────────────────────────────────────────

ssl_use = 0                       # Use SSL? 1 = yes, 0 = no

# SSL Certificate Settings (optional for mutual TLS)
ssl_cert_file = ""                # Path to client certificate file (e.g., "certs/client.crt")
ssl_key_file = ""                 # Path to client private key file (e.g., "certs/client.key")

# ───────────────────────────────────────────────
# NickServ Login Settings
# ───────────────────────────────────────────────

nickserv_login_enabled = False    # Enable/disable NickServ login
require_nickserv_ident = True     # Only join channels if identified to NickServ
nickserv_nick = "NickServ"        # Name of the service (usually "NickServ")
nickserv_botnick = "BlackBoT"     # Bot's nickname for NickServ identification
nickserv_password = "parola_ta_aici"  # Bot's password registered with NickServ

# ───────────────────────────────────────────────
# Channel Settings
# ───────────────────────────────────────────────

channels = ["#BT"]                # Channels to join on first run

# ───────────────────────────────────────────────
# Hostname Format
# ───────────────────────────────────────────────

# Format to identify a user hostmask:
# 1 = *!*@host
# 2 = *!user@host
# 3 = nick!user@host
# 4 = nick!*@*
# 5 = *!user@*
default_hostname = 1

# ───────────────────────────────────────────────
# Authentication & Access
# ───────────────────────────────────────────────

multiple_logins = 1               # Allow multiple logins from different hosts? 1 = yes
autoDeauthTime = 1                # Hours before auto-deauthentication for inactivity

# ───────────────────────────────────────────────
# Channel Rejoin Handling
# ───────────────────────────────────────────────

maxAttemptRejoin = 5              # Max rejoin attempts on kick/disconnect

# ───────────────────────────────────────────────
# Private Message Flood Protection
# ───────────────────────────────────────────────

private_flood_limit = "5:3"       # Max messages in timeframe (e.g., 5 messages in 3 seconds)
private_flood_time = 2            # Ignore duration in minutes

# ───────────────────────────────────────────────
# Message Queue (Flood Avoidance)
# ───────────────────────────────────────────────

message_delay = 1.5               # Delay in seconds between bot messages

# ───────────────────────────────────────────────
# Long Message Splitting
# ───────────────────────────────────────────────

message_max_chars = 450           # Max chars per line for IRC messages

# ───────────────────────────────────────────────
# SQLite Database
# ───────────────────────────────────────────────

sqlite3_database = "work.db"      # SQLite3 DB filename

# ───────────────────────────────────────────────
# CTCP Handling
# ───────────────────────────────────────────────

version = "BlackBoT: Python Edition"  # CTCP VERSION response

# ───────────────────────────────────────────────
# BlackBoT monitor status (send packages to uptime.tclscripts.net)
# True or False
# ───────────────────────────────────────────────

monitor_status = True

# ───────────────────────────────────────────────
# DCC / BotLink Settings
# ───────────────────────────────────────────────

# If the bot is behind NAT, set your public IPv4 address here.
# Leave empty ("") to auto-detect it (uses sourceIP or a UDP probe).
# Example: "203.0.113.42"
dcc_public_ip = ""

# A single, fixed TCP port for DCC CHAT listening.
# Recommended if you want to open or forward only one port in your router/firewall.
# If set to None, the bot will use a random port from dcc_port_range instead.
dcc_listen_port = 51999  # choose any free TCP port and forward it if needed

# Range of ports to use if dcc_listen_port is None.
# Useful if your network allows multiple ephemeral ports instead of a single one.
dcc_port_range = (50000, 52000)

# Automatically close idle DCC sessions after N seconds.
# This helps clean up stale or inactive connections.
dcc_idle_timeout = 600

# Allow DCC chat with unauthenticated users.
# Not recommended for production bots, as it allows anyone to open a DCC session.
dcc_allow_unauthed = False

# Logging level for the DCC system ("DEBUG", "INFO", "WARNING", "ERROR", or None).
dcc_log_level = None

# ───────────────────────────────────────────────
# BotLink Auto-Connect Settings
# ───────────────────────────────────────────────

# If True, all DCCManager instances share a single global worker thread
# that automatically maintains botlink connections (recommended).
botlink_autoconnect_global = True

# Interval (in seconds) between automatic botlink maintenance cycles.
# The bot will periodically refresh the botlink peers list, send keepalives,
# and attempt reconnects for disconnected peers.
botlink_autoconnect_interval = 30

# ───────────────────────────────────────────────
# Updater logging
# ───────────────────────────────────────────────
# String level: "DEBUG" | "INFO" | "WARNING" | "ERROR" | "CRITICAL"
# Set to None (or empty string) to silence updater logs.
update_log_level = None