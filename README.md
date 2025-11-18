# 🤖 BlackBoT – Modern IRC Bot with Multi‑Instance Manager

Welcome to **BlackBoT**, a modular, modern, multi‑instance capable IRC bot written in Python.
Check https://uptime.tclscripts.net for BoT Uptime Contest

---

## 🚀 Features

* 🔥 **Multi‑Instance Manager** (create, start, stop, edit, delete bots)
* 📦 **Per‑instance environment configuration** (`.env`)
* 🔐 **NickServ authentication** (with optional ident requirement)
* 🛡️ **SSL/TLS support** (server TLS + optional client certificates)
* 🧠 **Auto‑update system**
* 🚓 **Flood protection**
* 🎚️ **Per‑instance logging**
* 🧵 **ThreadWorker supervisor** (safe background workers)
* 📡 **DCC support**
* 🔄 **BotLink system** (inter‑bot communication)
* 🧩 **Modular command system** (channel + PM commands)

---

## 📁 Project Structure

```
BlackBoT/
│── Manager.py          # Multi‑Instance controller
│── Launcher.py         # Environment + dependency setup
│── BlackBoT.py         # Core bot runtime
│── environment_config.py
│── commands.py
│── SQL.py
│── ...
│
└── instances/
    └── <InstanceName>/
        ├── .env        # Per‑bot configuration
        ├── logs/       # Per‑bot logs
        ├── data/       # Credentials + misc
        ├── <bot>.db    # SQLite user/channel DB
        └── <bot>.pid   # Runtime process PID
```

---

# 🧩 Installation

## 1️⃣ Clone Repository

```bash
git clone https://github.com/tclscripts/BlackBoT.git
cd BlackBoT
```

## 2️⃣ Run Launcher (auto‑setup)

```bash
python3 Launcher.py
```

This will:

* create a virtual environment
* install all dependencies
* validate the installation

---

# 🛠 Creating Your First Bot Instance

Run the multi‑instance manager:

```bash
python3 Manager.py
```

Then choose:

```
1. Create new instance
```

You will be prompted for:

* bot nickname
* username + realname
* IRC servers
* SSL/TLS settings
* channel list
* NickServ credentials
* log level
* auto‑start preferences

When finished, your instance will appear as:

```
instances/<Name>/.env
```

---

# ⚙️ `.env` Configuration (Modern Format)

Each bot has its own `.env` file. Example:

```env
# Identity
BLACKBOT_NICKNAME=Legion
BLACKBOT_USERNAME=Legion
BLACKBOT_REALNAME="BlackBoT"
BLACKBOT_AWAY="No Away"
BLACKBOT_ALTNICK=Legion_

# Servers + TLS
BLACKBOT_SERVERS=irc.libera.chat:6697
BLACKBOT_PORT=6697
BLACKBOT_SSL_USE=true
BLACKBOT_SSL_CERT_FILE=
BLACKBOT_SSL_KEY_FILE=

# Channels
BLACKBOT_CHANNELS=#MyChannel,#MyOtherChannel

# NickServ
BLACKBOT_NICKSERV_ENABLED=true
BLACKBOT_NICKSERV_PASSWORD=secretpass
BLACKBOT_REQUIRE_NICKSERV_IDENT=false

# Performance
BLACKBOT_MESSAGE_DELAY=1.5

# Auto-Logout (mins)
BLACKBOT_AUTO_DEAUTH_TIME=30
```

Every variable beginning with `BLACKBOT_` is automatically parsed into the bot via `environment_config.py`.

---

# ▶️ Running Bots

## Start all instances

```bash
python3 Manager.py start
```

## Start one instance

```bash
python3 Manager.py start <InstanceName>
```

## Stop an instance

```bash
python3 Manager.py stop <InstanceName>
```

Supports:

* graceful SIGTERM
* forced SIGKILL
* process‑group kill on Linux

## Restart instance

```bash
python3 Manager.py restart <InstanceName>
```

---

# 📝 Editing Configurations

### Edit `.env` via Manager

```
Advanced → Edit instance configuration
```

This opens the instance's `.env` with your system editor.

### Browse instance files

```
Advanced → Browse instance files
```

---

# 🔐 NickServ Behavior

* If `BLACKBOT_NICKSERV_ENABLED=true` → bot logs in using `/msg NickServ IDENTIFY`.
* If `BLACKBOT_REQUIRE_NICKSERV_IDENT=true` → bot **waits** for identification before joining channels.

Set to false if the network language differs from English.

```env
BLACKBOT_REQUIRE_NICKSERV_IDENT=false
```

---

# 📡 Channel Join Logic

On first run (**empty DB**):

* joins channels from `.env` → `BLACKBOT_CHANNELS`

On later runs:

* loads channels from SQLite DB (`CHANNELS` table)

If no channels exist in DB but you want to force `.env`:

```bash
rm instances/<Name>/<Name>.db*
```

Bot becomes "new" again.

---

# 🔐 Auto-Logout System

A background worker monitors logged-in users.

---

# 🧵 Worker System

BlackBoT uses a custom **ThreadWorker** implementation with:

* supervised child threads
* heartbeat pings
* auto-restart on freeze
* stoppable via `stop_event`

Used for:

* bcrypt offloading
* login session monitoring
* auto-update polling

---

# ❤️ Contribute & Support

Pull requests and enhancements are welcome!
For feature requests or help, open an issue on GitHub.

---

# 📜 License

MIT License – feel free to use, modify, and distribute.
