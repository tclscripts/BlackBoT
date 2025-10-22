# settings.py

# ───────────────────────────────────────────────
# Core Bot Identification Settings
# ───────────────────────────────────────────────

char = "!"                        # Prefix for bot commands # Prefix for bot commands # Prefix for bot commands # Prefix for bot commands
nickname = "BlackBoT"             # Bot's main nickname # Bot's main nickname # Bot's main nickname # Bot's main nickname
username = "Black"               # IRC ident (username) of the bot # IRC ident (username) of the bot # IRC ident (username) of the bot # IRC ident (username) of the bot
altnick = "BlackBoT_"            # Alternate nickname if the main one is taken # Alternate nickname if the main one is taken # Alternate nickname if the main one is taken # Alternate nickname if the main one is taken
realname = "BlackBoT: Python Edition"  # Realname shown in WHOIS # Realname shown in WHOIS # Realname shown in WHOIS # Realname shown in WHOIS
away = "No Away"                 # Default away message # Default away message # Default away message # Default away message

# ───────────────────────────────────────────────
# Auto-Update Settings
# ───────────────────────────────────────────────

autoUpdateEnabled = True         # True = enable auto-update, False = disable # True = enable auto-update, False = disable # True = enable auto-update, False = disable # True = enable auto-update, False = disable
autoUpdateInterval = 60         # verification interval in minutes # verification interval in minutes # verification interval in minutes # verification interval in minutes

# ───────────────────────────────────────────────
# Server & Connection Settings
# ───────────────────────────────────────────────

sourceIP = ""                     # Source IP ("" = auto) # Source IP ("" = auto) # Source IP ("" = auto) # Source IP ("" = auto)
sourcePort = 3337                 # Outgoing port if sourceIP is used # Outgoing port if sourceIP is used # Outgoing port if sourceIP is used # Outgoing port if sourceIP is used

servers = [                       # List of IRC servers ("host port") # List of IRC servers ("host port") # List of IRC servers ("host port") # List of IRC servers ("host port")
    "irc.libera.chat 6667",
    "irc.undernet.org 6667"
]

port = 6667                       # Default port if not specified per-server # Default port if not specified per-server # Default port if not specified per-server # Default port if not specified per-server

# ───────────────────────────────────────────────
# SSL Settings
# ───────────────────────────────────────────────

ssl_use = 0                       # Use SSL? 1 = yes, 0 = no # Use SSL? 1 = yes, 0 = no # Use SSL? 1 = yes, 0 = no # Use SSL? 1 = yes, 0 = no

# SSL Certificate Settings (optional for mutual TLS)
ssl_cert_file = ""               # Path to client certificate file (e.g., "certs/client.crt") # Path to client certificate file (e.g., "certs/client.crt") # Path to client certificate file (e.g., "certs/client.crt") # Path to client certificate file (e.g., "certs/client.crt")
ssl_key_file = ""                # Path to client private key file (e.g., "certs/client.key") # Path to client private key file (e.g., "certs/client.key") # Path to client private key file (e.g., "certs/client.key") # Path to client private key file (e.g., "certs/client.key")


# ───────────────────────────────────────────────
# NickServ Login Settings
# ───────────────────────────────────────────────

nickserv_login_enabled = False             # Enable/disable NickServ login # Enable/disable NickServ login # Enable/disable NickServ login # Enable/disable NickServ login
require_nickserv_ident = True              # Ã¢Å“â€¦ Only join channels if identified to NickServ # âœ… Only join channels if identified to NickServ # âœ… Only join channels if identified to NickServ # ✅ Only join channels if identified to NickServ
nickserv_nick = "NickServ"                # Name of the service (usually "NickServ") # Name of the service (usually "NickServ") # Name of the service (usually "NickServ") # Name of the service (usually "NickServ")
nickserv_botnick = "BlackBoT"        # Bot's nickname for NickServ identification # Bot's nickname for NickServ identification # Bot's nickname for NickServ identification # Bot's nickname for NickServ identification
nickserv_password = "parola_ta_aici"      # Bot's password registered with NickServ # Bot's password registered with NickServ # Bot's password registered with NickServ # Bot's password registered with NickServ

# ───────────────────────────────────────────────
# Channel Settings
# ───────────────────────────────────────────────

channels = ["#BT"]                # Channels to join on first run # BT"]                # Channels to join on first run # BT"]                # Channels to join on first run # BT"]                # Channels to join on first run

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

multiple_logins = 1              # Allow multiple logins from different hosts? 1 = yes # Allow multiple logins from different hosts? 1 = yes # Allow multiple logins from different hosts? 1 = yes # Allow multiple logins from different hosts? 1 = yes
autoDeauthTime = 1               # Hours before auto-deauthentication for inactivity # Hours before auto-deauthentication for inactivity # Hours before auto-deauthentication for inactivity # Hours before auto-deauthentication for inactivity

# ───────────────────────────────────────────────
# Channel Rejoin Handling
# ───────────────────────────────────────────────

maxAttemptRejoin = 5             # Max rejoin attempts on kick/disconnect # Max rejoin attempts on kick/disconnect # Max rejoin attempts on kick/disconnect # Max rejoin attempts on kick/disconnect

# ───────────────────────────────────────────────
# Private Message Flood Protection
# ───────────────────────────────────────────────

private_flood_limit = "5:3"      # Max messages in timeframe (e.g. 5 messages in 3 seconds) # Max messages in timeframe (e.g. 5 messages in 3 seconds) # Max messages in timeframe (e.g. 5 messages in 3 seconds) # Max messages in timeframe (e.g. 5 messages in 3 seconds)
private_flood_time = 2           # Ignore duration in minutes # Ignore duration in minutes # Ignore duration in minutes # Ignore duration in minutes

# ───────────────────────────────────────────────
# Message Queue (Flood Avoidance)
# ───────────────────────────────────────────────

message_delay = 1.5                # Delay in seconds between bot messages # Delay in seconds between bot messages # Delay in seconds between bot messages # Delay in seconds between bot messages


# ───────────────────────────────────────────────
# Long Message Splitting
# ───────────────────────────────────────────────

message_max_chars = 450          # Max chars per line for IRC messages # Max chars per line for IRC messages # Max chars per line for IRC messages # Max chars per line for IRC messages

# ───────────────────────────────────────────────
# SQLite Database
# ───────────────────────────────────────────────

sqlite3_database = "work.db"     # SQLite3 DB filename # SQLite3 DB filename # SQLite3 DB filename # SQLite3 DB filename

# ───────────────────────────────────────────────
# CTCP Handling
# ───────────────────────────────────────────────

version = "BlackBoT: Python Edition"  # CTCP VERSION response # CTCP VERSION response # CTCP VERSION response # CTCP VERSION response