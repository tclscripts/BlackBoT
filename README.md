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

# ❤️ Contribute & Support

Pull requests and feature requests are welcome.
