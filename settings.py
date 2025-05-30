# settings.py

# ───────────────────────────────────────────────
# Core Bot Identification Settings
# ───────────────────────────────────────────────

char = "!"  # Prefixul pentru comenzile botului

nickname = "BlackBoT"  # Nickname-ul principal al botului
username = "Black"  # Identul (username) IRC al botului
altnick = "BlackBoT_"  # Nickname alternativ, folosit dacă nickname-ul principal e deja luat
realname = "BlackBoT: Python Edition"  # Realname-ul botului (vizibil în WHOIS)
away = "No Away"  # Mesajul implicit de away pentru bot

# ───────────────────────────────────────────────
# Server & Conexiune
# ───────────────────────────────────────────────

sourceIP = ""  # IP-ul sursă de pe care se face conexiunea ("" = auto)
sourcePort = 3337  # Portul de ieșire (util doar dacă sourceIP este setat)

# Lista serverelor IRC la care se poate conecta (format: "host port")
servers = [
    "irc.libera.chat 6667",
    "irc.undernet.org 6667"
]

port = 6667  # Portul default folosit dacă nu e specificat în lista `servers`

ssl_use = 0  # Setează 1 pentru a folosi SSL, 0 pentru conexiuni necriptate

# ───────────────────────────────────────────────
# Channel Settings
# ───────────────────────────────────────────────

channels = ["#BT"]  # Canalele default pe care botul le va accesa la prima rulare

# ───────────────────────────────────────────────
# Hostname Format
# ───────────────────────────────────────────────
# Formatul pentru identificarea unui utilizator (hostname)
# 1 = *!*@host
# 2 = *!user@host
# 3 = nick!user@host
# 4 = nick!*@*
# 5 = *!user@*
default_hostname = 1

# ───────────────────────────────────────────────
# Autentificare & Acces
# ───────────────────────────────────────────────

multiple_logins = 1  # Permite logarea multiplă de pe mai multe hosturi? (1 = da, 0 = nu)

# Timpul (în ore) după care utilizatorii inactivi sunt auto-deautentificați
autoDeauthTime = 1

# ───────────────────────────────────────────────
# Channel Rejoin
# ───────────────────────────────────────────────

# Număr maxim de încercări de a reintra într-un canal (în caz de kick, etc.)
maxAttemptRejoin = 5

# ───────────────────────────────────────────────
# Database
# ───────────────────────────────────────────────

# Numele bazei de date SQLite folosită de bot
sqlite3_database = "work.db"

# ───────────────────────────────────────────────
# CTCP
# ───────────────────────────────────────────────

# Mesajul trimis ca răspuns la CTCP VERSION
version = "BlackBoT: Python Edition"
