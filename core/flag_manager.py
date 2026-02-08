"""
Flag Manager for Plugins
=========================
Sistem de flag-uri pentru plugin-uri bazat pe Variables.py

FLAG-URI DISPONIBILE:
  N - BOSS OWNER (the all mighty) - GLOBAL
  n - OWNER (almost mighty) - GLOBAL
  m - MASTER (global access on channels) - GLOBAL
  M - MANAGER (channel manager) - PER CHANNEL
  A - ADMIN (channel admin) - PER CHANNEL
  O - OP (channel op) - PER CHANNEL
  V - VOICE (channel voice) - PER CHANNEL
  P - PROTECT (protected from bot actions) - SPECIAL
  B - BOT (bot account) - SPECIAL
  - - PUBLIC (oricine poate folosi) - PUBLIC

UTILIZARE ÎN PLUGIN-URI:
  level='n'     → Doar owner
  level='nM'    → Owner SAU Manager
  level='OV'    → Op SAU Voice
  level='MAO'   → Manager SAU Admin SAU Op
  level='-'     → Public (oricine)

Author: BlackBoT Team
Version: 1.0.0
"""

import Variables as v

# =============================================================================
# Flag Definitions (din Variables.py)
# =============================================================================

# Mapare flag → info din Variables
FLAG_DEFINITIONS = {
    'N': v.boss_owner,  # Boss Owner (highest global)
    'n': v.owner,  # Owner (global)
    'm': v.master,  # Master (global)
    'M': v.manager,  # Manager (channel)
    'A': v.admin,  # Admin (channel)
    'O': v.op,  # Op (channel)
    'V': v.voice,  # Voice (channel)
    'P': v.protect,  # Protected (special)
    'B': v.bot,  # Bot (special)
}

# Global flags (aplică pe toate canalele)
GLOBAL_FLAGS = ['N', 'n', 'm']

# Channel flags (aplică doar pe canal)
CHANNEL_FLAGS = ['M', 'A', 'O', 'V']

# Special flags (nu intră în ierarhie)
SPECIAL_FLAGS = ['P', 'B']

# Public flag (oricine)
PUBLIC_FLAGS = ['-', '', 'public', 'Public', 'PUBLIC']

# Ierarhie de la cel mai mare la cel mai mic
FLAG_HIERARCHY = ['N', 'n', 'm', 'M', 'A', 'O', 'V']


# =============================================================================
# Flag Information Functions
# =============================================================================

def get_flag_info(flag: str) -> dict:
    """
    Obține informații despre un flag.

    Args:
        flag: Flag-ul (ex: 'n', 'M', 'O')

    Returns:
        Dict cu info: {'name': str, 'fullname': str, 'flag': str, 'desc': str}
        sau None dacă flag-ul nu există

    Example:
        >>> get_flag_info('n')
        {'name': 'OWNER', 'fullname': 'OWNER', 'flag': 'n', 'desc': 'Almost mighty'}
    """
    return FLAG_DEFINITIONS.get(flag)


def get_flag_level(flag: str) -> int:
    """
    Obține nivelul numeric al unui flag (pentru comparații).

    Niveluri:
      N = 100 (highest)
      n = 90
      m = 80
      M = 70
      A = 60
      O = 50
      V = 40 (lowest privileged)
      P, B = 0 (special)

    Args:
        flag: Flag-ul

    Returns:
        Nivel numeric (0-100)
    """
    if flag in SPECIAL_FLAGS:
        return 0

    if flag in FLAG_HIERARCHY:
        # Index inversed: N=100, n=90, ..., V=40
        return 100 - (FLAG_HIERARCHY.index(flag) * 10)

    return 0


def is_public_flag(flags: str) -> bool:
    """
    Verifică dacă flag-urile indică access public.

    Args:
        flags: String cu flag-uri (ex: '-', 'public')

    Returns:
        True dacă e public
    """
    return flags in PUBLIC_FLAGS


def is_global_flag(flag: str) -> bool:
    """
    Verifică dacă flag-ul e global (aplică pe toate canalele).

    Args:
        flag: Flag-ul (ex: 'n', 'M')

    Returns:
        True dacă e global
    """
    return flag in GLOBAL_FLAGS


def is_channel_flag(flag: str) -> bool:
    """
    Verifică dacă flag-ul e per-canal.

    Args:
        flag: Flag-ul

    Returns:
        True dacă e per-canal
    """
    return flag in CHANNEL_FLAGS


def format_flag_description(flag: str) -> str:
    """
    Formatează o descriere human-readable pentru flag.

    Args:
        flag: Flag-ul

    Returns:
        String formatat

    Example:
        >>> format_flag_description('n')
        "n - OWNER (Almost mighty) [GLOBAL]"
    """
    info = get_flag_info(flag)
    if not info:
        return f"{flag} - Unknown flag"

    flag_type = ''
    if is_global_flag(flag):
        flag_type = '[GLOBAL]'
    elif is_channel_flag(flag):
        flag_type = '[CHANNEL]'
    elif flag in SPECIAL_FLAGS:
        flag_type = '[SPECIAL]'

    return f"{flag} - {info['name']} ({info['desc']}) {flag_type}"


# =============================================================================
# Flag Checking Functions
# =============================================================================

def has_flag(user_flags: str, required_flag: str) -> bool:
    """
    Verifică dacă user are exact flag-ul cerut.

    Args:
        user_flags: Flag-urile user-ului (ex: "nMO")
        required_flag: Flag-ul cerut (ex: "O")

    Returns:
        True dacă user are flag-ul exact

    Example:
        >>> has_flag("nMO", "O")
        True
        >>> has_flag("nM", "O")
        False
    """
    if not user_flags or not required_flag:
        return False

    return required_flag in user_flags


def has_flag_or_higher(user_flags: str, required_flag: str) -> bool:
    """
    Verifică dacă user are flag-ul cerut SAU unul mai mare în ierarhie.

    Args:
        user_flags: Flag-urile user-ului (ex: "nMO")
        required_flag: Flag-ul minim necesar (ex: "O")

    Returns:
        True dacă user are access

    Example:
        >>> has_flag_or_higher("n", "O")
        True  # 'n' (owner) > 'O' (op)
        >>> has_flag_or_higher("V", "O")
        False  # 'V' (voice) < 'O' (op)
    """
    if not user_flags or not required_flag:
        return False

    required_level = get_flag_level(required_flag)

    # Verifică fiecare flag al user-ului
    for flag in user_flags:
        if flag == required_flag:
            return True  # Exact match

        user_level = get_flag_level(flag)
        if user_level >= required_level and user_level > 0:
            return True  # Has higher flag

    return False


def has_any_flag(user_flags: str, required_flags: str) -> bool:
    """
    Verifică dacă user are ORICARE din flag-urile cerute.

    Această funcție e cea mai folosită pentru plugin-uri!

    Args:
        user_flags: Flag-urile user-ului (ex: "VO")
        required_flags: Flag-uri acceptate (ex: "nMO")
                       = owner SAU manager SAU op

    Returns:
        True dacă user are măcar un flag din listă

    Example:
        >>> has_any_flag("VO", "nMO")
        True  # are 'O'
        >>> has_any_flag("V", "nMO")
        False  # nu are niciun flag din listă
        >>> has_any_flag("n", "MO")
        True  # owner are access la comenzi MO (ierarhie)
    """
    if not required_flags:
        return True

    # Check public
    if is_public_flag(required_flags):
        return True

    if not user_flags:
        return False

    # Verifică fiecare flag cerut
    for required_flag in required_flags:
        # Check exact match SAU flag mai mare în ierarhie
        if has_flag_or_higher(user_flags, required_flag):
            return True

    return False


def has_all_flags(user_flags: str, required_flags: str) -> bool:
    """
    Verifică dacă user are TOATE flag-urile cerute.

    Args:
        user_flags: Flag-urile user-ului (ex: "nMO")
        required_flags: Flag-uri necesare (ex: "MO")

    Returns:
        True dacă user are toate flag-urile

    Example:
        >>> has_all_flags("nMO", "MO")
        True
        >>> has_all_flags("nM", "MO")
        False  # lipsește 'O'
    """
    if not required_flags:
        return True

    if not user_flags:
        return False

    for required_flag in required_flags:
        if not has_flag(user_flags, required_flag):
            return False

    return True


def get_highest_flag(user_flags: str) -> str:
    """
    Obține cel mai mare flag al user-ului din ierarhie.

    Args:
        user_flags: Flag-urile user-ului (ex: "nMOV")

    Returns:
        Cel mai mare flag sau None

    Example:
        >>> get_highest_flag("nMO")
        'n'  # Owner e cel mai mare
        >>> get_highest_flag("VO")
        'O'  # Op > Voice
    """
    if not user_flags:
        return None

    highest = None
    highest_level = 0

    for flag in user_flags:
        level = get_flag_level(flag)
        if level > highest_level:
            highest_level = level
            highest = flag

    return highest


def list_user_flags(user_flags: str) -> list:
    """
    Listează toate flag-urile user-ului cu descrieri.

    Args:
        user_flags: Flag-urile user-ului (ex: "nMO")

    Returns:
        List de dict-uri cu info despre fiecare flag

    Example:
        >>> list_user_flags("nO")
        [
            {'flag': 'n', 'name': 'OWNER', 'desc': 'Almost mighty', 'level': 90},
            {'flag': 'O', 'name': 'OP', 'desc': 'The op...', 'level': 50}
        ]
    """
    if not user_flags:
        return []

    flags = []
    for flag in user_flags:
        info = get_flag_info(flag)
        if info:
            flags.append({
                'flag': flag,
                'name': info['name'],
                'desc': info['desc'],
                'level': get_flag_level(flag),
                'type': 'global' if is_global_flag(flag) else 'channel' if is_channel_flag(flag) else 'special'
            })

    # Sort by level (descending)
    flags.sort(key=lambda x: x['level'], reverse=True)

    return flags


# =============================================================================
# Utility Functions
# =============================================================================

def list_all_flags() -> list:
    """
    Listează TOATE flag-urile disponibile în sistem.

    Returns:
        List de dict-uri cu info despre toate flag-urile
    """
    flags = []

    for flag in FLAG_HIERARCHY:
        info = get_flag_info(flag)
        if info:
            flags.append({
                'flag': flag,
                'name': info['name'],
                'desc': info['desc'],
                'level': get_flag_level(flag),
                'type': 'global' if is_global_flag(flag) else 'channel'
            })

    for flag in SPECIAL_FLAGS:
        info = get_flag_info(flag)
        if info:
            flags.append({
                'flag': flag,
                'name': info['name'],
                'desc': info['desc'],
                'level': 0,
                'type': 'special'
            })

    return flags


def format_flags_list() -> str:
    """
    Formatează lista completă de flag-uri pentru afișare.

    Returns:
        String multi-line cu toate flag-urile
    """
    lines = ["📋 Available Flags:"]
    lines.append("")
    lines.append("GLOBAL FLAGS (apply on all channels):")

    for flag in GLOBAL_FLAGS:
        lines.append(f"  {format_flag_description(flag)}")

    lines.append("")
    lines.append("CHANNEL FLAGS (apply per channel):")

    for flag in CHANNEL_FLAGS:
        lines.append(f"  {format_flag_description(flag)}")

    lines.append("")
    lines.append("SPECIAL FLAGS:")

    for flag in SPECIAL_FLAGS:
        lines.append(f"  {format_flag_description(flag)}")

    return "\n".join(lines)


# =============================================================================
# Common Flag Combinations (pentru convenience)
# =============================================================================

# Combinații comune de flag-uri
COMMON_FLAGS = {
    'PUBLIC': '-',  # Oricine
    'AUTH': 'NnmMAOV',  # Orice user autentificat cu flags
    'OWNER': 'n',  # Doar owner
    'OWNERS': 'Nn',  # Boss owner SAU owner
    'GLOBAL': 'Nnm',  # Orice global flag
    'ADMINS': 'NnmMA',  # Admins (global + channel)
    'MODERATORS': 'NnmMAO',  # Moderatori (fără voice)
    'OPS_PLUS': 'NnmMAO',  # Ops și mai sus
    'ALL_STAFF': 'NnmMAOV',  # Tot staff-ul
    'CHANNEL_STAFF': 'MAOV',  # Doar channel staff
    'CHANNEL_ADMINS': 'MAO',  # Channel admins (fără voice)
}


def get_common_flags(name: str) -> str:
    """
    Obține flag-uri predefinite după nume.

    Args:
        name: Numele combinației (ex: 'ADMINS', 'MODERATORS')

    Returns:
        String cu flag-uri sau None

    Example:
        >>> get_common_flags('ADMINS')
        'NnmMA'
    """
    return COMMON_FLAGS.get(name.upper())


# =============================================================================
# Export pentru import ușor
# =============================================================================

__all__ = [
    # Constants
    'FLAG_DEFINITIONS',
    'GLOBAL_FLAGS',
    'CHANNEL_FLAGS',
    'SPECIAL_FLAGS',
    'PUBLIC_FLAGS',
    'FLAG_HIERARCHY',
    'COMMON_FLAGS',

    # Info functions
    'get_flag_info',
    'get_flag_level',
    'is_public_flag',
    'is_global_flag',
    'is_channel_flag',
    'format_flag_description',

    # Checking functions
    'has_flag',
    'has_flag_or_higher',
    'has_any_flag',
    'has_all_flags',
    'get_highest_flag',
    'list_user_flags',

    # Utility functions
    'list_all_flags',
    'format_flags_list',
    'get_common_flags',
]