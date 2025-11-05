

# access types
#
boss_owner = {
    'name': 'BOSS OWNER',
    'fullname': 'BOSS OWNER',
    'flag': 'N',
    'desc': 'The all mighty'
}

owner = {
    'name': 'OWNER',
    'fullname': 'OWNER',
    'flag': 'n',
    'desc': 'Almost mighty'
}

master = {
    'name': 'MASTER',
    'fullname': 'MASTER',
    'flag': 'm',
    'desc': 'A global access that has control only on channels added to the eggdrop.'
}

manager = {
    'name': 'MANAGER',
    'fullname': 'Channel Manager',
    'flag': 'M',
    'desc': 'The manager of the channel'
}

admin = {
    'name' : 'ADMIN',
    'fullname': 'Channel admin',
    'flag': 'A',
    'desc': 'The admin of  the channel, has lower access than manager.'
}

op = {
    'name': 'OP',
    'fullname': 'OPER',
    'flag': 'O',
    'desc': 'The op of  the channel, has lower access than admin'
}

voice = {
    'name': 'VOICE',
    'fullname': 'Channel voice',
    'flag': 'V',
    'desc': 'The voice of the channel, has lower access than op. Can *only* have + on access'
}

protect = {
    'name': 'PROTECT',
    'fullname': 'Channel protected',
    'flag': 'P',
    'desc': 'Is protected on channel from the bot protections or channel commands'
}

bot = {
    'name': 'BOT',
    'fullname': 'Channel BOT',
    'flag': 'B',
    'desc': 'Is protected from protections and also has other exceptions for it'
}

# list that contains the access types
access_list = [boss_owner, owner, master, manager, admin, op, voice, protect, bot]

# settings list (you can set default values here, for flags the values are 0 for disable and 1 for enabled)
# type for settings 1 (it's value is a string), 0 (it's value is a flag, like +setting or -setting)

settings = [
    {'name': 'jointime', 'description': 'Stores the chan join time for the bot', 'type': '1', 'value': ''}
]

# Users settings
users_settings = ['lastseen', 'lastSeenOn']

# Users settings allowed to be changed
users_settings_change = ['email', 'greet', 'autologin', 'botlink', 'botlink_ip', 'botlink_port']
