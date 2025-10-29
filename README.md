# 🤖 BlackBoT

**BlackBoT** is a powerful and modular IRC bot built with Python and Twisted, featuring channel moderation, user authentication, role-based access control, and automatic update support via GitHub.

📣 BlackBoT Best Connect Time Contest
Check out https://uptime.tclscripts.net to monitor your bot’s status and track your performance.

--

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
- Threaded workers with stop/reset for background tasks  
- Advanced status reporting (CPU, RAM, threads, uptime, system info)  
- CTCP support (VERSION reply)  
- Flexible hostmask formats for user management  
- Caching with TTL and memory limits  
- Extensible module system (e.g., YouTube fetcher)  
- Cross-platform support (Linux/Windows) 

---

## 🚀 Getting Started

```bash
Clone the repository:

git clone https://github.com/tclscripts/BlackBoT.git
cd BlackBoT

Edit the configuration file:
nano settings.py

Use the Bash script (Linux)
  - The repo also includes a helper script BlackBoT_RuN.sh which:
  - Creates a virtual environment
  - Installs the required Python packages
  - Starts the bot automatically

Run it as:
bash BlackBoT_RuN.sh

Requirements
Python 3.8+
Linux or Windows (Linux recommended for BlackBoT_RuN.sh or directly Starter.py for Windows)
Internet connection (for auto-update and external modules)

Dependencies are listed in requirements.txt and will be installed automatically if you use the Bash script.
```

---

## 🆘 The !help Command

The !help command is your gateway to discovering what BlackBoT can do.

Dynamic filtering: Shows only commands you have access to (public, local per-channel, or global).

Usage examples:
``` 
!help                  → Lists all accessible commands
!help say              → Shows details about the "say" command
!help #channel         → In PM, lists local commands for that channel

Detailed descriptions: Each command can provide multi-line usage and notes.
``` 
## 📂 File Structure
``` 
  BlackBoT/
├── core/                      # Core package with internal logic
│   ├── commands.py            # Implementation of commands (auth, access, uptime, etc.)
│   ├── commands_map.py        # Command mapping to IDs, flags, and descriptions
│   ├── log.py                 # Logging module
│   ├── monitor_client.py      # Background monitor for uptime and stats
│   ├── seen.py                # "Seen" system: tracks last activity and stats
│   ├── SQL.py                 # SQLite wrapper (connections, queries, WAL mode)
│   ├── sql_manager.py         # SQL manager singleton (initializes DB and tables)
│   ├── threading_utils.py     # ThreadWorker with stop/reset and global events
│   ├── update.py              # Auto-update mechanism from GitHub
│   └── Variables.py           # Global variables: roles, settings, access lists
│
├── BlackBoT.py                # Main bot implementation (Twisted IRCClient)
├── BlackBoT_RuN.sh            # Bash script for setup and running on Linux
├── Starter.py                 # Python entry point for launching the bot
├── settings.py                # Bot configuration (server, nick, passwords, etc.)
├── requirements.txt           # Python dependencies
├── VERSION                    # Current version file
└── changes                    # Changelog for updates
```

