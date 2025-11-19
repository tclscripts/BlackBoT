# 🤖 BlackBoT – Modern IRC Bot with Multi‑Instance Manager

Welcome to **BlackBoT**, a modular, modern, multi‑instance capable IRC bot written in Python.

Check [https://uptime.tclscripts.net](https://uptime.tclscripts.net) for BoT Uptime Contest

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
* 🧩 **Modular command system** (channel + PM + DCC commands)

---

## 📁 Project Structure

```
BlackBoT/
│── Manager.py
│── Launcher.py
│── BlackBoT.py
│── commands.py
│── environment_config.py
│── SQL.py
│── ...
│
└── instances/
    └── <InstanceName>/
        ├── .env
        ├── logs/
        ├── data/
        ├── <bot>.db
        └── <bot>.pid
```

---

# 🛠 Installation

## 1️⃣ Requirements

* Python 3.8+
* Git
* OpenSSL (for SSL IRC connections)
* A Linux system is recommended, but Windows works too

## 2️⃣ Clone Repository

```
git clone https://github.com/tclscripts/BlackBoT.git
cd BlackBoT
```

## 3️⃣ Initial Setup (Automatic)

Run the launcher:

```
python3 Launcher.py
```

This will:

* Create a virtual environment (`.venv`)
* Install all required dependencies
* Validate environment

If everything succeeds, you’re ready to create a bot instance.

---

# ▶️ Running BlackBoT

## Start Multi‑Instance Manager

```
python3 Manager.py
```

You will see an interactive menu:

1. Create new instance
2. Start instance
3. Stop instance
4. Restart instance
5. Edit configuration
6. Delete instance

## Start All Bot Instances

```
python3 Manager.py start
```

## Start One Instance

```
python3 Manager.py start <InstanceName>
```

## Stop a Running Instance

```
python3 Manager.py stop <InstanceName>
```

## Restart a Bot

```
python3 Manager.py restart <InstanceName>
```

## Check Process Running

Instances write their PID file here:

```
instances/<Name>/<Name>.pid
```

Logs are stored at:

```
instances/<Name>/logs/
```

To follow logs live:

```
tail -f instances/<Name>/logs/<Name>.log
```

---

Follow the instructions as before.

---

# 🧩 Using Commands

BlackBoT includes an advanced modular command system. All commands can be executed in:

### ✔️ Channel (public)

Use the command prefix (default `!`) inside any channel where the bot is present.

### ✔️ Private Message (PM)

Send the bot a private message with the same command syntax.

### ✔️ DCC Chat

If you open a DCC session with the bot, all commands also work there.

---

## ℹ️ Getting Help About Commands

Use:

```
!help
```

This will show **all commands you have access to**, grouped by:

* **Public** commands – available to everyone
* **Local (Channel)** commands – depend on your access flags in that channel
* **Global** commands – require higher privileges

To get help for a specific command:

```
!help command
```

Example:

```
!help op
```

To view channel‑specific help while in PM/DCC:

```
!help #channel
```

---

## 🧩 Command Access System

Access is based on **flags** stored in the SQLite DB:

* `N` – Boss Owner
* `n` – Owner
* `m` – Master
* `M` – Manager
* `A` – Admin
* `O` – Op
* `V` – Voice
* `P` – Protected
* `B` – Bot

Each command lists the flags required. Use `!help command` to check.

---

## 🛰️ DCC & BotLink Notes

* Bot can accept DCC CHAT sessions.
* Commands work identically over DCC.
* BotLink uses DCC internally for bot‑to‑bot communication.

---

# ▶️ Running Bots

Same instructions as original README.

---

# 📚 Complete Command List

Below is the **full list of commands** supported by BlackBoT, extracted from the bot's internal command registry. Commands may be used **in channel**, **via private message**, or **over DCC**, depending on access.

---

## 🟢 Public Commands (no access flags required)

* **!hello** — private greeting
* **!uptime** — show bot uptime
* **!version** — show bot version
* **!help** — show help for commands

---

## 🏷️ Channel-Level Commands (require channel access)

These depend on your access inside each channel.

* **!op [nick]** — give +o
* **!deop [nick]** — remove +o
* **!voice [nick]** — give +v
* **!devoice [nick]** — remove +v
* **!hop [nick]** — give +h
* **!hdeop [nick]** — remove +h
* **!cycle [#channel]** — part + rejoin
* **!say <target> <text>** — send message through bot

---

## 🌐 Global Commands (require global flags: N,n,m,M,A,O depending on command)

### 🔧 Bot Management

* **!addchan #channel** — register/join channel
* **!delchan #channel** — unregister/part channel
* **!channels** — list stored channels
* **!jump** — change to next server
* **!restart** — restart bot
* **!die** — shut down bot
* **!rehash** — reload configuration
* **!update check/start** — check or run updater
* **!status** — system & thread status report

### 👤 User & Access Management

* **!add <nick> <flags> [#channel]** — grant access
* **!delacc <nick> [#channel]** — remove access
* **!del <nick>** — delete user
* **!userlist [#channel]** — list users with access
* **!info <nick|#channel>** — inspect info

### 🔒 Authentication Commands

* **auth <user> <pass>** — authenticate (PM only)
* **auth save** — save current host
* **pass <password>** — set your password (PM)
* **newpass <password>** — change password (PM)
* **deauth** — logout current host
* **myset <setting> <value>** — change user settings

### 🚫 Moderation

* **!ban <mask> [options]** — advanced regex/mask ban

---

# ❤️ Contribute & Support

Pull requests and feature requests are welcome.
