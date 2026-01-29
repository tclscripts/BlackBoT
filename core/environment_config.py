import os
from pathlib import Path
from typing import Any
import sys


# ═══════════════════════════════════════════════════════════════════
# Environment-Based Configuration System
# ═══════════════════════════════════════════════════════════════════


def safe_print(*args, **kwargs):
    """
    Print care nu crapă pe Windows când apar emoji / Unicode.
    - Pe Linux/macOS se comportă ca print normal.
    - Pe Windows, la UnicodeEncodeError, înlocuiește caracterele
      care nu pot fi afișate.
    """
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", "") or "utf-8"
        new_args = []
        for a in args:
            if isinstance(a, str):
                try:
                    # dacă se encodează, o lăsăm așa
                    a.encode(enc)
                    new_args.append(a)
                except UnicodeEncodeError:
                    # înlocuim caracterele „problemă” cu '?'
                    safe = a.encode(enc, errors="replace").decode(enc, errors="replace")
                    new_args.append(safe)
            else:
                new_args.append(a)
        print(*new_args, **kwargs)

class EnvironmentConfig:
    """
    Environment-based configuration system that replaces settings.py
    Loads configuration from .env files and environment variables
    """

    def __init__(self):
        self.config = {}
        self.instance_name = os.getenv('BLACKBOT_INSTANCE_NAME', 'main')
        self.load_configuration()

    def load_configuration(self):
        """Load configuration from environment and .env files"""

        # Default configuration values (from original settings.py)
        defaults = {
            # Core Bot Identification
            'char': '!',
            'nickname': 'BlackBoT',
            'username': 'Black',
            'altnick': 'BlackBoT_',
            'realname': 'BlackBoT: Python Edition',
            'away': 'No Away',

            # Auto-Update Settings
            'autoUpdateEnabled': True,
            'autoUpdateInterval': 60,

            # Server & Connection Settings
            'sourceIP': '',
            'sourcePort': 3337,
            'servers': ['irc.libera.chat 6667', 'irc.undernet.org 6667'],
            'port': 6667,

            # SSL Settings
            'ssl_use': 0,
            'ssl_cert_file': '',
            'ssl_key_file': '',

            # NickServ/X/Q Authentication Settings
            'auth_method': 'nickserv',  # Options: 'nickserv', 'x', 'q'
            'nickserv_login_enabled': False,
            'require_nickserv_ident': True,
            'nickserv_nick': 'NickServ',
            'nickserv_botnick': 'BlackBoT',
            'nickserv_password': '',

            # QuakeNet Q Authentication
            'quakenet_auth_enabled': False,
            'quakenet_username': '',
            'quakenet_password': '',

            # Undernet X Authentication
            'undernet_auth_enabled': False,
            'undernet_username': '',
            'undernet_password': '',

            # Authentication timeout
            'auth_timeout_seconds': 30,

            # User modes to set after connection
            'user_modes': '',  # Example: '+x' for Undernet IP hiding, '+ix' for invisible+hide

            # Channel Settings
            'channels': ['#BlackBoT'],

            # Hostname Format
            'default_hostname': 1,

            # Authentication & Access
            'multiple_logins': 1,
            'autoDeauthTime': 1,

            # Channel Rejoin Handling
            'maxAttemptRejoin': 5,

            # Private Message Flood Protection
            'private_flood_limit': '5:3',
            'private_flood_time': 2,

            # Message Queue
            'message_delay': 1.5,

            # Long Message Splitting
            'message_max_chars': 450,

            # SQLite Database
            'sqlite3_database': 'database.db',

            # CTCP Handling
            'version': 'BlackBoT: Python Edition',

            # Monitor status
            'monitor_status': True,

            # DCC / BotLink Settings
            'dcc_public_ip': '',
            'dcc_listen_port': 51999,
            'dcc_port_range': (50000, 52000),
            'dcc_idle_timeout': 600,
            'dcc_allow_unauthed': False,
            'botlink_autoconnect_global': True,
            'botlink_autoconnect_interval': 30,

            # Logging Configuration
            'logs_dir': 'logs',
            'log_file': 'blackbot.log',
            'log_max_lines': 100000,
            'log_backup_count': 3,
            'log_level': 'DEBUG',
            # Monitor credentials
            'monitor_cred_file': 'data/cred.json',
        }

        # Start with defaults
        self.config = defaults.copy()

        # Load from instance-specific .env file if available
        instance_env_files = [
            f'instances/{self.instance_name}/.env',
            f'.env.{self.instance_name}',
            '.env'
        ]

        for env_file in instance_env_files:
            if Path(env_file).exists():
                safe_print(f"📋 Loading configuration from {env_file}")
                self._load_env_file(env_file)
                break
        else:
            safe_print("💡 No .env file found, using defaults")

        # Override with direct environment variables (highest priority)
        self._load_from_environment()

        # Process configuration values
        self._process_configuration()

        # Show configuration summary
        self._show_config_summary()

    def _load_env_file(self, env_file: str):
        """Load variables from .env file"""
        try:
            with open(env_file, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()

                    # Skip empty lines and comments
                    if not line or line.startswith('#'):
                        continue

                    # Parse key=value
                    if '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip().strip('"\'')

                        # Map environment variables to config keys
                        config_key = self._env_to_config_key(key)
                        if config_key:
                            self.config[config_key] = self._convert_value(config_key, value)

        except Exception as e:
            safe_print(f"⚠️ Error loading {env_file}: {e}")

    def _load_from_environment(self):
        """Load configuration from environment variables"""
        env_mappings = {
            'BLACKBOT_CHAR': 'char',
            'BLACKBOT_COMMAND_CHAR': 'char',
            'BLACKBOT_NICKNAME': 'nickname',
            'BLACKBOT_USERNAME': 'username',
            'BLACKBOT_ALTNICK': 'altnick',
            'BLACKBOT_REALNAME': 'realname',
            'BLACKBOT_AWAY': 'away',
            'BLACKBOT_AUTO_UPDATE_ENABLED': 'autoUpdateEnabled',
            'BLACKBOT_AUTO_UPDATE_INTERVAL': 'autoUpdateInterval',
            'BLACKBOT_SOURCE_IP': 'sourceIP',
            'BLACKBOT_SOURCE_PORT': 'sourcePort',
            'BLACKBOT_SERVERS': 'servers',
            'BLACKBOT_PORT': 'port',
            'BLACKBOT_SSL_USE': 'ssl_use',
            'BLACKBOT_SSL_CERT_FILE': 'ssl_cert_file',
            'BLACKBOT_SSL_KEY_FILE': 'ssl_key_file',
            'BLACKBOT_NICKSERV_ENABLED': 'nickserv_login_enabled',
            'BLACKBOT_NICKSERV_LOGIN_ENABLED': 'nickserv_login_enabled',
            'BLACKBOT_REQUIRE_NICKSERV_IDENT': 'require_nickserv_ident',
            'BLACKBOT_NICKSERV_NICK': 'nickserv_nick',
            'BLACKBOT_NICKSERV_BOTNICK': 'nickserv_botnick',
            'BLACKBOT_NICKSERV_PASSWORD': 'nickserv_password',
            'BLACKBOT_CHANNELS': 'channels',
            'BLACKBOT_DEFAULT_HOSTNAME': 'default_hostname',
            'BLACKBOT_MULTIPLE_LOGINS': 'multiple_logins',
            'BLACKBOT_AUTO_DEAUTH_TIME': 'autoDeauthTime',
            'BLACKBOT_MAX_ATTEMPT_REJOIN': 'maxAttemptRejoin',
            'BLACKBOT_PRIVATE_FLOOD_LIMIT': 'private_flood_limit',
            'BLACKBOT_PRIVATE_FLOOD_TIME': 'private_flood_time',
            'BLACKBOT_MESSAGE_DELAY': 'message_delay',
            'BLACKBOT_MESSAGE_MAX_CHARS': 'message_max_chars',
            'BLACKBOT_DATABASE': 'sqlite3_database',
            'BLACKBOT_SQLITE3_DATABASE': 'sqlite3_database',
            'BLACKBOT_VERSION': 'version',
            'BLACKBOT_MONITOR_STATUS': 'monitor_status',
            'BLACKBOT_DCC_PUBLIC_IP': 'dcc_public_ip',
            'BLACKBOT_DCC_PORT': 'dcc_listen_port',
            'BLACKBOT_DCC_LISTEN_PORT': 'dcc_listen_port',
            'BLACKBOT_DCC_PORT_RANGE': 'dcc_port_range',
            'BLACKBOT_DCC_IDLE_TIMEOUT': 'dcc_idle_timeout',
            'BLACKBOT_DCC_ALLOW_UNAUTHED': 'dcc_allow_unauthed',
            'BLACKBOT_BOTLINK_AUTOCONNECT_GLOBAL': 'botlink_autoconnect_global',
            'BLACKBOT_BOTLINK_AUTOCONNECT_INTERVAL': 'botlink_autoconnect_interval',
            'BLACKBOT_LOG_DIR': 'logs_dir',
            'BLACKBOT_LOGS_DIR': 'logs_dir',
            'BLACKBOT_LOG_FILE': 'log_file',
            'BLACKBOT_LOG_MAX_LINES': 'log_max_lines',
            'BLACKBOT_LOG_BACKUP_COUNT': 'log_backup_count',
            'BLACKBOT_LOG_LEVEL': 'log_level'
        }

        for env_key, config_key in env_mappings.items():
            if env_key in os.environ:
                value = os.environ[env_key]
                self.config[config_key] = self._convert_value(config_key, value)

    def _env_to_config_key(self, env_key: str) -> str:
        """Convert environment variable name to config key"""
        mappings = {
            'BLACKBOT_CHAR': 'char',
            'BLACKBOT_COMMAND_CHAR': 'char',
            'BLACKBOT_NICKNAME': 'nickname',
            'BLACKBOT_USERNAME': 'username',
            'BLACKBOT_ALTNICK': 'altnick',
            'BLACKBOT_REALNAME': 'realname',
            'BLACKBOT_AWAY': 'away',
            'BLACKBOT_AUTO_UPDATE_ENABLED': 'autoUpdateEnabled',
            'BLACKBOT_AUTO_UPDATE_INTERVAL': 'autoUpdateInterval',
            'BLACKBOT_SOURCE_IP': 'sourceIP',
            'BLACKBOT_SOURCE_PORT': 'sourcePort',
            'BLACKBOT_SERVERS': 'servers',
            'BLACKBOT_PORT': 'port',
            'BLACKBOT_SSL_USE': 'ssl_use',
            'BLACKBOT_SSL_CERT_FILE': 'ssl_cert_file',
            'BLACKBOT_SSL_KEY_FILE': 'ssl_key_file',
            'BLACKBOT_NICKSERV_ENABLED': 'nickserv_login_enabled',
            'BLACKBOT_NICKSERV_LOGIN_ENABLED': 'nickserv_login_enabled',
            'BLACKBOT_REQUIRE_NICKSERV_IDENT': 'require_nickserv_ident',
            'BLACKBOT_QUAKENET_AUTH_ENABLED': 'quakenet_auth_enabled',
            'BLACKBOT_QUAKENET_USERNAME': 'quakenet_username',
            'BLACKBOT_QUAKENET_PASSWORD': 'quakenet_password',
            'BLACKBOT_UNDERNET_AUTH_ENABLED': 'undernet_auth_enabled',
            'BLACKBOT_UNDERNET_USERNAME': 'undernet_username',
            'BLACKBOT_UNDERNET_PASSWORD': 'undernet_password',
            'BLACKBOT_NICKSERV_NICK': 'nickserv_nick',
            'BLACKBOT_NICKSERV_BOTNICK': 'nickserv_botnick',
            'BLACKBOT_NICKSERV_PASSWORD': 'nickserv_password',
            'BLACKBOT_USER_MODES': 'user_modes',
            'BLACKBOT_AUTH_TIMEOUT_SECONDS': 'auth_timeout_seconds',
            'BLACKBOT_CHANNELS': 'channels',
            'BLACKBOT_DEFAULT_HOSTNAME': 'default_hostname',
            'BLACKBOT_MULTIPLE_LOGINS': 'multiple_logins',
            'BLACKBOT_AUTO_DEAUTH_TIME': 'autoDeauthTime',
            'BLACKBOT_MAX_ATTEMPT_REJOIN': 'maxAttemptRejoin',
            'BLACKBOT_PRIVATE_FLOOD_LIMIT': 'private_flood_limit',
            'BLACKBOT_PRIVATE_FLOOD_TIME': 'private_flood_time',
            'BLACKBOT_MESSAGE_DELAY': 'message_delay',
            'BLACKBOT_MESSAGE_MAX_CHARS': 'message_max_chars',
            'BLACKBOT_DATABASE': 'sqlite3_database',
            'BLACKBOT_SQLITE3_DATABASE': 'sqlite3_database',
            'BLACKBOT_VERSION': 'version',
            'BLACKBOT_MONITOR_STATUS': 'monitor_status',
            'BLACKBOT_DCC_PUBLIC_IP': 'dcc_public_ip',
            'BLACKBOT_DCC_PORT': 'dcc_listen_port',
            'BLACKBOT_DCC_LISTEN_PORT': 'dcc_listen_port',
            'BLACKBOT_DCC_PORT_RANGE': 'dcc_port_range',
            'BLACKBOT_DCC_IDLE_TIMEOUT': 'dcc_idle_timeout',
            'BLACKBOT_DCC_ALLOW_UNAUTHED': 'dcc_allow_unauthed',
            'BLACKBOT_BOTLINK_AUTOCONNECT_GLOBAL': 'botlink_autoconnect_global',
            'BLACKBOT_BOTLINK_AUTOCONNECT_INTERVAL': 'botlink_autoconnect_interval',
            'BLACKBOT_LOG_DIR': 'logs_dir',
            'BLACKBOT_LOGS_DIR': 'logs_dir',
            'BLACKBOT_LOG_FILE': 'log_file',
            'BLACKBOT_LOG_MAX_LINES': 'log_max_lines',
            'BLACKBOT_LOG_BACKUP_COUNT': 'log_backup_count',
            'BLACKBOT_LOG_LEVEL': 'log_level'
        }
        return mappings.get(env_key)

    def _convert_value(self, key: str, value: str) -> Any:
        """Convert string value to appropriate type"""
        if not value:
            return ''

        # Boolean conversions
        boolean_keys = [
            'autoUpdateEnabled', 'nickserv_login_enabled', 'require_nickserv_ident',
            'monitor_status', 'dcc_allow_unauthed', 'botlink_autoconnect_global', 'ssl_use', 'quakenet_auth_enabled',
            'undernet_auth_enabled'
        ]
        if key in boolean_keys:
            return value.lower() in ['true', '1', 'yes', 'on']

        # Integer conversions
        integer_keys = [
            'autoUpdateInterval', 'sourcePort', 'port', 'default_hostname',
            'multiple_logins', 'autoDeauthTime', 'maxAttemptRejoin', 'private_flood_time',
            'message_max_chars', 'dcc_listen_port', 'dcc_idle_timeout', 'botlink_autoconnect_interval',
            'log_max_lines', 'log_backup_count'
        ]
        if key in integer_keys:
            try:
                return int(value)
            except ValueError:
                safe_print(f"⚠️ Invalid integer value for {key}: {value}")
                return self.config.get(key, 0)

        # Float conversions
        if key == 'message_delay':
            try:
                return float(value)
            except ValueError:
                safe_print(f"⚠️ Invalid float value for {key}: {value}")
                return self.config.get(key, 1.5)

        # List conversions
        if key == 'servers':
            if ',' in value:
                # Format: host1:port1,host2:port2
                servers = []
                for server in value.split(','):
                    server = server.strip()
                    if ':' in server:
                        host, port = server.split(':', 1)
                        servers.append(f"{host} {port}")
                    else:
                        servers.append(f"{server} {self.config.get('port', 6667)}")
                return servers
            else:
                # Single server
                if ':' in value:
                    host, port = value.split(':', 1)
                    return [f"{host} {port}"]
                else:
                    return [f"{value} {self.config.get('port', 6667)}"]

        if key == 'channels':
            if ',' in value:
                return [ch.strip() for ch in value.split(',')]
            else:
                return [value.strip()]

        # Tuple conversions
        if key == 'dcc_port_range':
            if ',' in value:
                try:
                    parts = value.split(',', 1)
                    return (int(parts[0].strip()), int(parts[1].strip()))
                except ValueError:
                    return (50000, 52000)
            else:
                return (50000, 52000)

        # String conversions (default)
        return value

    def _process_configuration(self):
        """Process and validate configuration"""

        # Ensure channels list has at least one channel
        if not self.config['channels']:
            self.config['channels'] = [f"#{self.instance_name}"]

        # Ensure logs directory exists for instance
        if self.instance_name != 'main':
            if not self.config['logs_dir'].startswith('instances/'):
                self.config['logs_dir'] = f"instances/{self.instance_name}/logs"
            if not self.config['log_file'].startswith(self.instance_name):
                self.config['log_file'] = f"{self.instance_name}.log"
            if not self.config['sqlite3_database'].startswith('instances/'):
                self.config['sqlite3_database'] = f"instances/{self.instance_name}/{self.instance_name}.db"
                # 🔐 cred.json pentru monitor
            if not self.config['monitor_cred_file'].startswith('instances/'):
                self.config['monitor_cred_file'] = f"instances/{self.instance_name}/data/cred.json"
        # Create necessary directories
        log_dir = Path(self.config['logs_dir'])
        log_dir.mkdir(parents=True, exist_ok=True)

        db_path = Path(self.config['sqlite3_database'])
        db_path.parent.mkdir(parents=True, exist_ok=True)

    def _show_config_summary(self):
        """Show configuration summary"""
        safe_print(f"🤖 BlackBoT Configuration Summary")
        safe_print(f"   Instance: {self.instance_name}")
        safe_print(f"   Nickname: {self.config['nickname']}")
        safe_print(f"   Servers: {', '.join(self.config['servers'])}")
        safe_print(f"   Channels: {', '.join(self.config['channels'])}")
        safe_print(f"   Database: {self.config['sqlite3_database']}")
        safe_print(f"   Log Level: {self.config['log_level']}")

        if self.config['nickserv_login_enabled']:
            safe_print(f"   NickServ: Enabled")

        if self.config['ssl_use']:
            safe_print(f"   SSL: Enabled")

    def __getattr__(self, name: str) -> Any:
        """Allow accessing config values as attributes (backwards compatibility)"""
        if name in self.config:
            return self.config[name]
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")

    def get(self, name: str, default: Any = None) -> Any:
        """Get configuration value with default"""
        return self.config.get(name, default)


config = EnvironmentConfig()