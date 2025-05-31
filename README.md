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
  ├── core/                # Command logic, SQL, variables
  ├── modules/             # External modules like YouTube title fetcher
  ├── settings.py          # Configuration and bot settings
  ├── update.py            # Auto-update mechanism
  ├── commands_map.py      # Command configuration list
  ├── BlackBoT.py          # Main bot implementation
  └── Starter.py           # Entry point for launching
```

