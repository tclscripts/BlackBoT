# commands_map.py

# ───────────────────────────────────────────────
# Implemented commands with their configuration
# ───────────────────────────────────────────────

command_definitions = [
    {'name': 'hello', 'description': 'Command executed in private by the boss owner when the bot is fresh', 'flags': '-', 'id': '1'},
    {'name': 'addchan', 'description': 'Join channel', 'flags': 'Nn', 'id': '2'},
    {'name': 'delchan', 'description': 'Join channel', 'flags': 'Nn', 'id': '3'},
    {'name': 'say', 'description': 'Command used to output message to a channel', 'flags': 'NnMA', 'id': '4'},
    {'name': 'jump', 'description': 'Jumping to the next server...', 'flags': 'Nn', 'id': '5'},
    {'name': 'restart', 'description': 'Restart python BoT', 'flags': 'Nn', 'id': '6'},
    {'name': 'die', 'description': 'python BoT death', 'flags': 'Nn', 'id': '7'},
    {'name': 'rehash', 'description': 'Reload python BoT', 'flags': 'Nn', 'id': '8'},
    {'name': 'op', 'description': 'Command executed by the user in order to get OP status on channel.', 'flags': 'NnmMAO', 'id': '8'},
    {'name': 'cycle', 'description': 'Cycle the channel upon command', 'flags': 'NnmMA', 'id': '9'},
    {'name': 'uptime', 'description': 'Displays the bot\'s uptime and system statistics.', 'flags': 'Nn', 'id': '10'},
    {'name': 'channels', 'description': 'Displays the status of all registered channels, including whether the bot is connected, suspended, or lacks operator privileges.', 'flags': 'Nn', 'id': '11'},
    {'name': 'pass', 'description': 'Set your private password', 'flags': '-', 'id': '12'},
    {'name': 'newpass', 'description': 'Change existing password', 'flags': '-', 'id': '13'},
    {'name': 'auth', 'description': 'Authenticate to your bot', 'flags': '-', 'id': '14'},
    {'name': 'voice', 'description': 'Command executed by the user in order to get VOICE status on channel.', 'flags': 'NnmMAOV', 'id': '15'},
    {'name': 'deop', 'description': 'Command executed by the user in order to remove OP on channel.', 'flags': 'NnmMAO', 'id': '16'},
    {'name': 'devoice', 'description': 'Command executed by the user in order to remove VOICE status on channel.',
     'flags': 'NnmMAOV', 'id': '17'},
    {'name': 'hop', 'description': 'Command executed by the user in order to get HalfOp on channel.', 'flags': 'NnmMAO', 'id': '18'},
    {'name': 'hdeop', 'description': 'Command executed by the user in order to remove HalfOp status on channel.',
     'flags': 'NnmMAO', 'id': '19'},
    {'name': 'version', 'description': 'Command executed by the user to view the version of the BoT.',
     'flags': 'NnmMAOV', 'id': '20'},
    {'name': 'add', 'description': 'Command executed by the user to add users with access.',
     'flags': 'NnmMA', 'id': '21'},
    {'name': 'userlist', 'description': 'Command executed by the user to add users with access.',
     'flags': 'NnmMAOV', 'id': '22'},
    {'name': 'delacc', 'description': 'Command executed by the user to remove access from users.',
     'flags': 'NnmMA', 'id': '23'},
    {'name': 'del', 'description': 'Command executed by the user to remove users from database.',
     'flags': 'NnmM', 'id': '24'},
    {'name': 'info', 'description': 'Command executed by the user to get info about a user or channel.',
     'flags': 'NnmMAOV', 'id': '25'},
    {'name': 'update', 'description': 'Command executed by the user to check for updates.',
     'flags': 'N', 'id': '26'}
]