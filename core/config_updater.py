#!/usr/bin/env python3
# config_updater.py
# ═══════════════════════════════════════════════════════════════════
# Configuration Update System - Full Version
# Respects "Notes" section and updates timestamps
# ═══════════════════════════════════════════════════════════════════

import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Set, Tuple, List
from core.log import get_logger

logger = get_logger("config_updater")

# ═══════════════════════════════════════════════════════════════════
# 1. CONFIGURATION DEFINITIONS (FULL LIST)
# ═══════════════════════════════════════════════════════════════════

# Aici definim titlurile secțiunilor pentru variabilele noi
# Cheia este variabila (format ENV), Valoarea este Titlul Secțiunii
SECTION_HEADERS = {
    'OPENWEATHER_API_KEY': 'Weather Module Configuration',
    'STATS_API_ENABLED': 'Stats Configuration',
    'STATS_API_HOST': 'Stats Configuration',
    'STATS_API_PORT': 'Stats Configuration',
    'BLACKBOT_BOTLINK_AUTOCONNECT_GLOBAL': 'Advanced Features',
    'BLACKBOT_DCC_PUBLIC_IP': 'Network & DCC Configuration',
}

# Configurația implicită completă
DEFAULT_CONFIG = {
    # --- Bot Identity ---
    'nickname': ('BlackBoT', 'Bot nickname'),
    'username': ('Black', 'Bot username'),
    'realname': ('BlackBoT: Python Edition', 'Bot realname'),
    'altnick': ('BlackBoT_', 'Alternative nickname'),
    'away': ('No Away', 'Default away message'),
    'char': ('!', 'Command prefix'),

    # --- Network ---
    'servers': (['irc.undernet.org 6667'], 'IRC Servers list'),
    'port': (6667, 'Default Port'),
    'ssl_use': (0, 'Use SSL (0/1)'),
    'sourceIP': ('', 'Source IP (Bind)'),
    'sourcePort': (3337, 'Source Port'),

    # --- Auth ---
    'nickserv_login_enabled': (False, 'Enable NickServ'),
    'nickserv_nick': ('NickServ', 'NickServ Service Name'),
    'nickserv_botnick': ('BlackBoT', 'NickServ Account'),
    'nickserv_password': ('', 'NickServ Password'),
    'require_nickserv_ident': (True, 'Require Ident before join'),

    'quakenet_auth_enabled': (False, 'QuakeNet Auth'),
    'quakenet_username': ('', 'QuakeNet User'),
    'quakenet_password': ('', 'QuakeNet Pass'),

    'undernet_auth_enabled': (False, 'Undernet Auth'),
    'undernet_username': ('', 'Undernet User'),
    'undernet_password': ('', 'Undernet Pass'),
    'auth_timeout_seconds': (30, 'Auth Timeout'),

    # --- Channels & User Modes ---
    'channels': (['#BT'], 'Channels to join'),
    'user_modes': ('+x', 'User modes on connect'),
    'default_hostname': (1, 'Hostname format'),

    # --- Access & Security ---
    'multiple_logins': (1, 'Allow multiple logins'),
    'autoDeauthTime': (1, 'Auto deauth time (hours)'),
    'maxAttemptRejoin': (5, 'Max rejoin attempts'),

    # --- Flood & Limits ---
    'message_delay': (1.5, 'Message delay'),
    'message_max_chars': (450, 'Max chars per line'),
    'private_flood_limit': ('5:3', 'Private flood limit'),
    'private_flood_time': (2, 'Flood time window'),
    'cache_size': (2000, 'Internal Cache Size'),
    'cache_ttl': (6, 'Internal Cache TTL'),

    # --- Database & Logs ---
    'sqlite3_database': ('instances/BlackBoT/BlackBoT.db', 'Database Path'),
    'logs_dir': ('logs', 'Logs Directory'),
    'log_file': ('BlackBoT.log', 'Log Filename'),
    'log_level': ('INFO', 'Log Level'),
    'log_max_lines': (100000, 'Max Log Lines'),
    'log_backup_count': (3, 'Log Backups'),

    # --- DCC ---
    'dcc_listen_port': (52000, 'DCC Listen Port'),
    'dcc_public_ip': ('', 'DCC Public IP'),
    'dcc_port_range': ((50000, 52000), 'DCC Port Range'),
    'dcc_idle_timeout': (600, 'DCC Timeout'),
    'dcc_allow_unauthed': (False, 'Allow Unauthed DCC'),

    # --- Advanced Features ---
    'autoUpdateEnabled': (True, 'Auto Update'),
    'autoUpdateInterval': (60, 'Update Interval'),
    'monitor_status': (True, 'Monitor Status'),
    'botlink_autoconnect_global': (True, 'BotLink AutoConnect'),
    'botlink_autoconnect_interval': (30, 'BotLink Interval'),

    # --- API / Modules ---
    'STATS_API_ENABLED': (True, 'Enable Stats API'),
    'STATS_API_HOST': ('0.0.0.0', 'Stats Host'),
    'STATS_API_PORT': (8001, 'Stats Port'),
    'OPENWEATHER_API_KEY': ('', 'OpenWeatherMap API Key'),

    # --- System ---
    'version': ('BlackBoT: Python Edition', 'Version String'),
}


# ═══════════════════════════════════════════════════════════════════
# 2. HELPERS
# ═══════════════════════════════════════════════════════════════════

def _format_value_for_env(value: Any) -> str:
    """Formatează valorile pentru fișierul .env"""
    if isinstance(value, bool):
        return 'true' if value else 'false'
    elif isinstance(value, list):
        if all(' ' in str(item) for item in value):
            return ','.join(str(item).replace(' ', ':') for item in value)
        return ','.join(str(item) for item in value)
    elif isinstance(value, tuple):
        return f"{value[0]},{value[1]}"
    elif value == '':
        return ''
    return str(value)


def _read_existing_keys(env_path: Path) -> Set[str]:
    """Citește doar cheile (partea stângă a =) din fișier."""
    keys = set()
    if env_path.exists():
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    keys.add(line.split('=', 1)[0].strip())
    return keys


def _env_key_from_config_key(config_key: str) -> str:
    """Convertește cheia internă (ex: logs_dir) în cheia ENV (ex: BLACKBOT_LOG_DIR)."""
    # Chei care rămân neschimbate (de obicei cele de API)
    if config_key.isupper() and ("API" in config_key or "OPENWEATHER" in config_key):
        return config_key

    # Mapări speciale
    special_map = {
        'char': 'BLACKBOT_COMMAND_CHAR',
        'autoUpdateEnabled': 'BLACKBOT_AUTO_UPDATE_ENABLED',
        'autoUpdateInterval': 'BLACKBOT_AUTO_UPDATE_INTERVAL',
        'sourceIP': 'BLACKBOT_SOURCE_IP',
        'sourcePort': 'BLACKBOT_SOURCE_PORT',
        'ssl_use': 'BLACKBOT_SSL_USE',
        'nickserv_login_enabled': 'BLACKBOT_NICKSERV_ENABLED',
        'require_nickserv_ident': 'BLACKBOT_REQUIRE_NICKSERV_IDENT',
        'quakenet_auth_enabled': 'BLACKBOT_QUAKENET_AUTH_ENABLED',
        'undernet_auth_enabled': 'BLACKBOT_UNDERNET_AUTH_ENABLED',
        'sqlite3_database': 'BLACKBOT_DATABASE',
        'logs_dir': 'BLACKBOT_LOG_DIR',
        'dcc_listen_port': 'BLACKBOT_DCC_PORT',
        'autoDeauthTime': 'BLACKBOT_AUTO_DEAUTH_TIME',
        'maxAttemptRejoin': 'BLACKBOT_MAX_ATTEMPT_REJOIN',
        'message_delay': 'BLACKBOT_MESSAGE_DELAY',
        'message_max_chars': 'BLACKBOT_MESSAGE_MAX_CHARS',
        'private_flood_limit': 'BLACKBOT_PRIVATE_FLOOD_LIMIT',
        'private_flood_time': 'BLACKBOT_PRIVATE_FLOOD_TIME',
        'cache_size': 'BLACKBOT_CACHE_SIZE',
        'cache_ttl': 'BLACKBOT_CACHE_TTL',
        'botlink_autoconnect_global': 'BLACKBOT_BOTLINK_AUTOCONNECT_GLOBAL',
        'botlink_autoconnect_interval': 'BLACKBOT_BOTLINK_AUTOCONNECT_INTERVAL',
    }

    if config_key in special_map:
        return special_map[config_key]

    # Conversie automată (camelCase -> SNAKE_CASE)
    # Ex: nickservNick -> BLACKBOT_NICKSERV_NICK
    import re
    result = re.sub('([a-z0-9])([A-Z])', r'\1_\2', config_key).upper()
    return f"BLACKBOT_{result}"


# ═══════════════════════════════════════════════════════════════════
# 3. CORE UPDATE LOGIC
# ═══════════════════════════════════════════════════════════════════

def update_env_file(env_path: Path, instance_name: str = None, force: bool = False) -> bool:
    """
    Actualizează fișierul .env:
    1. Identifică variabilele lipsă.
    2. Le grupează pe secțiuni.
    3. Le inserează ÎNAINTE de secțiunea '# Notes'.
    4. Actualizează timestamp-ul de la final.
    """
    if not force and not env_path.exists():
        return False

    existing_keys = _read_existing_keys(env_path)
    missing_vars = {}

    # 1. Identificare lipsuri
    for config_key, (default_val, desc) in DEFAULT_CONFIG.items():
        env_key = _env_key_from_config_key(config_key)
        if env_key not in existing_keys:
            missing_vars[config_key] = (env_key, default_val, desc)

    # Dacă nu lipsește nimic și fișierul există, doar actualizăm data
    if not missing_vars and env_path.exists():
        _update_timestamp_only(env_path)
        return False

    # 2. Citire conținut curent
    lines = []
    if env_path.exists():
        with open(env_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    else:
        # Fișier nou
        lines = [
            f"# BlackBoT Configuration - {instance_name or 'Main'}\n",
            f"# Generated by config_updater.py\n\n",
            f"# ═══════════════════════════════════════════════════════════════════\n",
            f"# Notes\n",
            f"# ═══════════════════════════════════════════════════════════════════\n",
            f"# \n"
        ]

    # 3. Găsire punct de inserare (Înainte de Notes)
    insert_idx = len(lines)  # Default la final

    for i, line in enumerate(lines):
        # Căutăm secțiunea Notes
        if line.strip().startswith('#') and "Notes" in line:
            # Verificăm dacă linia de deasupra e separatorul
            if i > 0 and "════" in lines[i - 1]:
                insert_idx = i - 1
            else:
                insert_idx = i
            break

    # 4. Construire bloc nou
    new_content = []

    # Grupăm variabilele pe secțiuni
    grouped_missing = {}
    generic_missing = []

    for _, (env_key, val, desc) in missing_vars.items():
        # Încercăm să găsim un titlu
        header = SECTION_HEADERS.get(env_key)

        # Logică de fallback pentru titluri
        if not header:
            if "API" in env_key:
                header = "API Configuration"
            elif "WEATHER" in env_key:
                header = "Weather Module Configuration"
            elif "STATS" in env_key:
                header = "Stats Configuration"
            elif "BOTLINK" in env_key:
                header = "Advanced Features"

        if header:
            grouped_missing.setdefault(header, []).append((env_key, val, desc))
        else:
            generic_missing.append((env_key, val, desc))

    # Generare text pentru grupuri
    for header, items in grouped_missing.items():
        new_content.append(f'\n# ═══════════════════════════════════════════════════════════════════\n')
        new_content.append(f'# {header}\n')
        new_content.append(f'# ═══════════════════════════════════════════════════════════════════\n\n')
        for env_key, val, desc in items:
            new_content.append(f'# {desc}\n')
            new_content.append(f'{env_key}={_format_value_for_env(val)}\n\n')

    # Generare text pentru variabile generice
    if generic_missing:
        new_content.append(f'\n# ═══════════════════════════════════════════════════════════════════\n')
        new_content.append(f'# New Configuration\n')
        new_content.append(f'# ═══════════════════════════════════════════════════════════════════\n\n')
        for env_key, val, desc in generic_missing:
            new_content.append(f'# {desc}\n')
            new_content.append(f'{env_key}={_format_value_for_env(val)}\n\n')

    # 5. Asamblare finală
    final_lines = lines[:insert_idx] + new_content + lines[insert_idx:]

    # 6. Actualizare Timestamp
    final_lines = _update_timestamp_in_lines(final_lines)

    # 7. Scriere pe disc (cu backup)
    try:
        if env_path.exists():
            shutil.copy2(env_path, env_path.with_suffix('.env.backup'))

        with open(env_path, 'w', encoding='utf-8') as f:
            f.writelines(final_lines)

        logger.info(f"✅ Updated {env_path} - Added {len(missing_vars)} new variables.")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to update config: {e}")
        return False


def _update_timestamp_in_lines(lines: List[str]) -> List[str]:
    """Caută și actualizează linia cu data de la final."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new_lines = []
    found = False

    for line in lines:
        if "Instance created:" in line or "Last updated:" in line:
            new_lines.append(f"# Last updated: {now_str}\n")
            found = True
        else:
            new_lines.append(line)

    if not found:
        if new_lines and not new_lines[-1].endswith('\n'):
            new_lines.append('\n')
        new_lines.append(f"# Last updated: {now_str}\n")

    return new_lines


def _update_timestamp_only(env_path: Path):
    """Doar actualizează data dacă nu sunt modificări."""
    try:
        with open(env_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        lines = _update_timestamp_in_lines(lines)

        with open(env_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)
    except:
        pass


def update_all_instance_configs():
    """Rulează update pe toate instanțele găsite."""
    p = Path("instances")
    if p.exists():
        for d in p.iterdir():
            if d.is_dir():
                update_env_file(d / ".env", d.name)


def ensure_instance_config(instance_name: str) -> Path:
    """Asigură existența unui config valid."""
    env_file = Path("instances") / instance_name / ".env"
    if not env_file.exists():
        env_file.parent.mkdir(parents=True, exist_ok=True)
        # Creăm scheletul ca să aibă secțiunea Notes
        with open(env_file, 'w', encoding='utf-8') as f:
            f.write(f"# BlackBoT Configuration - {instance_name}\n")
            f.write(f"# Created: {datetime.now()}\n\n")
            f.write(f"# ═══════════════════════════════════════════════════════════════════\n")
            f.write(f"# Notes\n")
            f.write(f"# ═══════════════════════════════════════════════════════════════════\n")
            f.write(f"# Add your custom notes here\n")

    update_env_file(env_file, instance_name, force=True)
    return env_file


if __name__ == "__main__":
    # Test rapid dacă rulezi scriptul direct
    update_all_instance_configs()