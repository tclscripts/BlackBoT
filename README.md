# 🤖 BlackBoT — Modular IRC Bot with Web Stats & Multi-Instance Manager

BlackBoT is a modern, modular IRC bot written in Python, designed for **stability**, **performance**, and **multi-instance operation**.  
It includes a powerful **Web Statistics UI**, a full **instance manager**, and a rich command system with fine-grained permissions.

**Uptime Contest:**  
https://uptime.tclscripts.net (BlackBoT instances)

---

# 📊 Stats Module (Web UI) — Overview

The **Stats Module** is one of the core features of BlackBoT.

It collects IRC activity in real time and provides a **beautiful web interface** for both channels and users.

## 🔗 Access Links

Once a bot instance is running, the Stats UI is available at:

- **Channel UI**  
  `http://<server-ip>:<port>/ui/<channel>`

- **User Profile**  
  `http://<server-ip>:<port>/profile/<channel>/<nick>`

> If `STATS_API_HOST=0.0.0.0`, the UI is exposed on the server IP + port.

Each bot instance can run its own Stats UI on a **separate port** (multi-bot safe).

---

## 🧰 What the Stats UI Provides

### 📌 Channel View (`/ui/<channel>`)
- total messages, words, active users
- **Top Talkers** (messages / words / characters)
- activity heatmap (peak hours)
- fun metrics (emoji, caps, questions, exclamations, links)
- **reply pairs** (who interacts with whom)
- records (longest message, most emojis, etc.)

### 👤 User Profile (`/profile/<channel>/<nick>`)
- total messages & activity style
- average words per message
- question / exclamation rate
- sentiment analysis + trends
- preferred hours & activity patterns
- top interaction partners

---

# ⚡ Highlights (Fixes & Improvements)

## ✅ Fixes & Stability
- safer restart / shutdown logic
- improved DB locking & retry handling
- more predictable ban / unban logic
- consistent user linking (click nick → profile)
- robust stats API startup & recovery

## 🧠 Memory & Performance
- optimized SQLite usage (WAL, cache tuning)
- batched stats aggregation (non-blocking)
- reduced memory pressure in workers
- smarter caching for frequent lookups
- thread supervisor for background workers

---

# 🚀 Core Features

- 🔥 **Multi-Instance Manager** (create / start / stop / restart bots)
- 📦 Per-instance `.env` configuration
- 📊 **Web Stats UI (per instance port)**
- 🧠 Automatic updates
- 🔐 NickServ authentication
- 🛡️ SSL/TLS IRC connections
- 🚓 Flood protection
- 📡 DCC support
- 🧵 Threaded background workers
- 🧩 Modular command system
- 🗂️ SQLite backend (per instance)

---

# ▶️ Getting Started

## 1️⃣ Start with the Launcher (IMPORTANT)

BlackBoT is **always started via the Launcher** on first run.

```bash
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