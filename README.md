# 🤖 BlackBoT

**BlackBoT** is a powerful and modular IRC bot built with Python and Twisted, featuring channel moderation, user authentication, role-based access control, and automatic update support via GitHub.

> ⚠️ This project is currently in progress and may contain bugs or unfinished features.

---

## 📦 Features

- Multi-server support with automatic failover
- User authentication system with password hashing
- Role-based permissions with per-channel and global flags
- Channel management: op, voice, kick, join/leave
- Auto-update from GitHub with setting preservation
- SQLite database backend for persistent user and channel data
- Modular command architecture

---

## 🚀 Getting Started

```bash
git clone https://github.com/tclscripts/BlackBoT.git
cd BlackBoT
nano settings.py
python Starter.py
```

## 📂 File Structure
``` 
  BlackBoT/
├── core/ # Core package with internal logic
│ ├── commands.py # Implementation of commands (auth, access, uptime, etc.)
│ ├── commands_map.py # Command mapping to IDs, flags, and descriptions
│ ├── log.py # Logging module (can be wired to Python's logging)
│ ├── SQL.py # SQLite wrapper (connections, queries, WAL mode)
│ ├── sql_manager.py # SQL manager singleton (initializes DB and tables)
│ ├── threading_utils.py # ThreadWorker with stop/reset and global events
│ ├── update.py # Auto-update mechanism from GitHub
│ └── Variables.py # Global variables: roles, settings, access lists
│
├── BlackBoT.py # Main bot implementation (Twisted IRCClient)
├── BlackBoT_RuN.sh # Bash script for setup and running on Linux
├── Starter.py # Python entry point for launching the bot
├── settings.py # Bot configuration (server, nick, passwords, etc.)
├── requirements.txt # Python dependencies
└── VERSION # Current version file
```

