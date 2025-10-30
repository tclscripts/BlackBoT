# commands_map.py

# ───────────────────────────────────────────────
# Implemented commands with their configuration
# ───────────────────────────────────────────────

command_definitions = [
    {'name': 'hello',
     'description': 'Send a private greeting to the owner when the bot is fresh/just started.\nUsage: !hello',
     'flags': '-', 'id': '1'},

    {'name': 'addchan',
     'description': 'Join and register a channel in the database.\nUsage: !addchan #channel\nNotes: Persists in DB and bot will rejoin on next start.',
     'flags': 'Nn', 'id': '2'},

    {'name': 'delchan',
     'description': 'Part and unregister a channel from the database.\nUsage: !delchan #channel\nNotes: Removes from DB; bot will no longer auto-join.',
     'flags': 'Nn', 'id': '3'},

    {'name': 'say',
     'description': 'Send a message to a channel or a user.\nUsage: !say <#channel|nick> <text>\nNotes: Respects flood limits and message splitting.',
     'flags': 'NnMA', 'id': '4'},

    {'name': 'jump',
     'description': 'Switch to the next server from settings.\nUsage: !jump\nNotes: Disconnects and reconnects to the next entry.',
     'flags': 'Nn', 'id': '5'},

    {'name': 'restart',
     'description': 'Restart the bot process gracefully.\nUsage: !restart\nNotes: Saves state where applicable; reconnects after restart.',
     'flags': 'Nn', 'id': '6'},

    {'name': 'die',
     'description': 'Shut down the bot process.\nUsage: !die\nNotes: The bot will not reconnect automatically after this.',
     'flags': 'Nn', 'id': '7'},

    {'name': 'rehash',
     'description': 'Reload bot configuration and core modules without a full restart.\nUsage: !rehash',
     'flags': 'Nn', 'id': '8'},

    {'name': 'op',
     'description': 'Give +o (operator) to yourself or to a target.\nUsage: !op [nick]\nNotes: Defaults to caller if no nick provided.',
     'flags': 'NnmMAO', 'id': '8'},

    {'name': 'cycle',
     'description': 'Leave and immediately rejoin the current channel.\nUsage: !cycle [#channel]\nNotes: Useful for regaining ops when channel has +i/+k changes.',
     'flags': 'Nn', 'id': '9'},

    {'name': 'uptime',
     'description': 'Show how long the bot process has been running.\nUsage: !uptime',
     'flags': '-', 'id': '10'},

    {'name': 'channels',
     'description': 'List registered channels and their status.\nUsage: !channels\nOutput: #chan:ok | #chan:suspended | #chan:no-op | #chan:offline',
     'flags': 'Nn', 'id': '11'},

    {'name': 'pass',
     'description': 'Set your account password (PM only).\nUsage: pass <password>\nNotes: First-time set only. Use newpass to change later.',
     'flags': '-', 'id': '12'},

    {'name': 'newpass',
     'description': 'Change your existing password (PM only).\nUsage: newpass <new_password>\nNotes: You must already have a password set.',
     'flags': '-', 'id': '13'},

    {'name': 'auth',
     'description': 'Authenticate or manage trusted hosts (PM only).\nUsage: auth <username> <password>\nExtras: auth save  — save current host as trusted.\nNotes: Required for commands that need flags; host is stored as a trusted login.',
     'flags': '-', 'id': '14'},

    {'name': 'voice',
     'description': 'Give +v (voice) to yourself or to a target.\nUsage: !voice [nick]\nNotes: Defaults to caller if no nick provided.',
     'flags': 'NnmMAOV', 'id': '15'},

    {'name': 'deop',
     'description': 'Remove +o from yourself or a target.\nUsage: !deop [nick]',
     'flags': 'NnmMAO', 'id': '16'},

    {'name': 'devoice',
     'description': 'Remove +v from yourself or a target.\nUsage: !devoice [nick]',
     'flags': 'NnmMAOV', 'id': '17'},

    {'name': 'hop',
     'description': 'Give +h (half-op) to yourself or to a target.\nUsage: !hop [nick]',
     'flags': 'NnmMAO', 'id': '18'},

    {'name': 'hdeop',
     'description': 'Remove +h (half-op) from yourself or a target.\nUsage: !hdeop [nick]',
     'flags': 'NnmMAO', 'id': '19'},

    {'name': 'version',
     'description': 'Show bot version string (CTCP VERSION handler also available).\nUsage: !version',
     'flags': '-', 'id': '20'},

    {'name': 'add',
     'description': 'Grant flags to a user (global or per-channel).\nUsage: !add <nick> <flags> [#channel]\nNotes: Without #channel it applies globally; otherwise locally.',
     'flags': 'NnmMAO', 'id': '21'},

    {'name': 'userlist',
     'description': 'List users with access and their flags (global or per-channel).\nUsage: !userlist [#channel]\nNotes: Without channel shows global entries.',
     'flags': 'NnmMAO', 'id': '22'},

    {'name': 'delacc',
     'description': 'Remove flags from a user (global or per-channel).\nUsage: !delacc <nick> [#channel]\nNotes: If channel is omitted, removes global flags.',
     'flags': 'NnmMAO', 'id': '23'},

    {'name': 'del',
     'description': 'Delete a user/account from the bot’s DB.\nUsage: !del <nick>\nWarning: This is irreversible.',
     'flags': 'NnmMAO', 'id': '24'},

    {'name': 'info',
     'description': 'Show info about a user or channel.\nUsage: !info <nick|#channel>\nNotes: Can include last seen data, modes, or registration state.',
     'flags': 'NnmMAOV', 'id': '25'},

    {'name': 'update',
     'description': 'Check for a new release or start the updater.\nUsage: !update check | !update start\nNotes: check = compare local vs remote version; start = download+merge and restart.',
     'flags': 'N', 'id': '26'},

    {'name': 'deauth',
     'description': 'Deauthenticate yourself from the current host.\nUsage: !deauth\nNotes: Removes the current host from active sessions; use auth again to log in.',
     'flags': '-', 'id': '27'},

    {'name': 'myset',
     'description': 'Change your user settings.\nUsage: !myset <setting> <value>\nAllowed settings: see Variables.users_settings_change (e.g., autologin on|off).\nNotes: Some settings may require re-authentication.',
     'flags': '-', 'id': '28'},

    {'name': 'update',
     'description': 'Alias/variant of update.\nUsage: !update check | !update start\nNotes: Same behavior as the other update entry (ID 26).',
     'flags': 'N', 'id': '29'},

    {'name': 'status',
     'description': 'Show internal runtime info.\nUsage: !status\nOutput includes: uptime, CPU%, RSS memory, threads, Python and OS info.',
     'flags': 'Nn', 'id': '30'},

    {'name': 'seen',
     'description': 'Show last activity for a nick/host/pattern, or stats.\nUsage: !seen <nick|host|pattern>\nExtra: !seen -stats  — show DB statistics (global or per current channel).',
     'flags': '-', 'id': '31'},
    {'name': 'help',
     'description': 'Show available commands depending on your access level.\n'
                    'Usage:\n'
                    '  !help                → List commands you can use (public/local/global)\n'
                    '  !help <command>      → Show details and description of a single command\n'
                    '  !help #channel       → In PM: list commands available for that channel\n',
     'flags': '-', 'id': '32'},
    {'name': 'ban',
    'description': 'Ban a user or a hostmask (local or global) and store it in the DB.\n'
                'Usage:\n'
                '  !ban <nick|mask|regex> [-regex] [-sticky] [-d 1h30m] [-reason "text"]\n'
                'Examples:\n'
                '  !ban badguy                       → resolve nick to host and ban\n'
                '  !ban *!*@bad.example -sticky      → wildcard host ban, kept enforced\n'
                '  !ban ^.*@.*\\.evil\\.com$ -regex   → regex ban on host\n'
                'Notes:\n'
                '- Stores who set the ban, when, duration, reason, sticky flag.\n'
                '- Local ban: run in a channel; Global ban: run in PM or pass -g (if supported by your cmd).\n'
                '- Sticky bans are re-applied on join and can be periodically enforced.\n'
                '- Regex requires -regex; wildcard masks support * and ?.\n'
                '- Realname matching supported when mask includes :realname (e.g., *!*@*:John Doe).',
    'flags': 'NnmMAO',
    'id': '33'}
]