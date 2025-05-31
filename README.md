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

🔑 Access Flags
- → No access required (private only)

N → Global BOSS owner
n → Global owner
m → Global master
M → Channel Manager
A → Channel Admin
O → Channel Owner
V → Voiced User

---

  📂 File Structure
  plaintext
  Copy
  Edit
  BlackBoT/
  ├── core/                # Command logic, SQL, variables
  ├── modules/             # External modules like YouTube title fetcher
  ├── settings.py          # Configuration and bot settings
  ├── update.py            # Auto-update mechanism
  ├── commands_map.py      # Command configuration list
  ├── BlackBoT.py          # Main bot implementation
  └── Starter.py           # Entry point for launching

---

🧠 Commands Overview
🔧 Admin & Bot Control
Command	Description	Access Flags

!hello	Initialize a new bot (Boss Only)	-
!addchan	Add and join a channel	Nn
!delchan	Remove channel	Nn
!say	Make the bot say something	NnMA
!jump	Jump to next configured server	Nn
!restart	Restart the bot	Nn
!die	Shutdown the bot	Nn
!rehash	Reload command modules	Nn
!uptime	Show bot uptime and stats	Nn
!channels	Show channel statuses	Nn
!update	Check for GitHub updates	N

---

🔑 Authentication
Command	Description	Access Flags
!pass	Set your password	-
!newpass	Change existing password	-
!auth	Authenticate to the bot	-
!deauth	Deauthenticate yourself	-
!myset	Change your user settings	-

---

🔐 User Access Management
Command	Description	Access Flags
!add	Add user with access rights	NnmMA
!userlist	Show list of users with access	NnmMAOV
!delacc	Remove user access rights	NnmMA
!del	Permanently delete user from DB	NnmM

---

💬 Channel Privileges
Command	Description	Access Flags
!op	Gain operator status	NnmMAO
!voice	Gain voice status	NnmMAOV
!hop	Gain half-operator status	NnmMAO
!deop	Remove operator status	NnmMAO
!devoice	Remove voice status	NnmMAOV
!hdeop	Remove half-operator status	NnmMAO

---

🛠 Miscellaneous
Command	Description	Access Flags
!cycle	Rejoin the current channel	NnmMA
!version	Display bot version	NnmMAOV
!info	Get info about a user or channel	NnmMAOV

---

🤝 Contributions
Pull requests and issues are welcome! Please open an issue to discuss your changes or ideas.


