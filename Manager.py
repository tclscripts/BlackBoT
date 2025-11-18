# Manager.py - Unified BlackBoT Configuration & Multi-Instance Manager

import os
import sys
import json
import yaml
import shutil
import signal
import time
import subprocess
from pathlib import Path
from typing import Dict, List
from dataclasses import dataclass, asdict
from datetime import datetime
import re
import platform

# Colors for terminal output
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    WARNING = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    END = '\033[0m'

def print_header(text: str):
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'='*70}{Colors.END}")
    print(f"{Colors.HEADER}{Colors.BOLD}{text.center(70)}{Colors.END}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'='*70}{Colors.END}\n")

def print_success(text: str):
    print(f"{Colors.GREEN}✅ {text}{Colors.END}")

def print_warning(text: str):
    print(f"{Colors.WARNING}⚠️  {text}{Colors.END}")

def print_error(text: str):
    print(f"{Colors.RED}❌ {text}{Colors.END}")

def print_info(text: str):
    print(f"{Colors.BLUE}💡 {text}{Colors.END}")

def print_question(text: str):
    return input(f"{Colors.CYAN}❓ {text}{Colors.END}")

@dataclass
class BotInstanceConfig:
    """Complete configuration for a BlackBoT instance"""
    # Core Identity
    name: str
    nickname: str
    username: str
    realname: str

    # Network Configuration
    servers: List[str]
    port: int = 6667
    ssl_enabled: bool = False

    ssl_cert_file: str = ""
    ssl_key_file: str = ""

    # Channels
    channels: List[str] = None

    # Authentication
    nickserv_enabled: bool = False
    nickserv_password: str = ""

    # Database & Logging
    database: str = ""
    log_level: str = "INFO"
    log_dir: str = "logs"

    # Network & Performance
    dcc_port: int = 51999
    message_delay: float = 1.5
    command_char: str = "!"

    # Environment & Instance
    environment: str = "local"
    enabled: bool = True
    auto_start: bool = False

    # File paths (auto-generated)
    config_file: str = ""
    log_file: str = ""
    pid_file: str = ""

    def __post_init__(self):
        if self.channels is None:
            self.channels = [f"#{self.name}"]
        if not self.database:
            self.database = f"instances/{self.name}/{self.name}.db"
        if not self.config_file:
            self.config_file = f"instances/{self.name}/.env"
        if not self.log_file:
            self.log_file = f"instances/{self.name}/logs/{self.name}.log"
        if not self.pid_file:
            self.pid_file = f"instances/{self.name}/{self.name}.pid"


def _kill_process(pid, force=False):
    system = platform.system().lower()

    if system == "windows":
        if force:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        else:
            subprocess.run(["taskkill", "/PID", str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    else:
        if force:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        else:
            os.killpg(os.getpgid(pid), signal.SIGTERM)


class UnifiedBlackBotManager:
    """Unified manager for all BlackBoT configuration and instance management"""

    def __init__(self):
        self.base_dir = Path(__file__).parent.resolve()
        self.instances_dir = self.base_dir / "instances"
        self.config_dir = self.base_dir / "config"
        self.registry_file = self.base_dir / "instances.json"
        self.instances: Dict[str, BotInstanceConfig] = {}

        # Ensure directories exist
        self.instances_dir.mkdir(exist_ok=True)
        self.config_dir.mkdir(exist_ok=True)

        # Load existing instances
        self.load_instances()

        print_info(f"🤖 Unified BlackBoT Manager initialized")
        print_info(f"📁 Base directory: {self.base_dir}")

    def _detect_advanced_config(self) -> bool:
        """Detect if advanced config system is available"""
        required_files = ["config_manager.py", "blackbot_config_integration.py"]
        return all((self.base_dir / f).exists() for f in required_files)


    def load_instances(self):
        """Load instance configurations from registry"""
        if self.registry_file.exists():
            try:
                with open(self.registry_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                self.instances = {}
                for name, config_data in data.get('instances', {}).items():
                    self.instances[name] = BotInstanceConfig(**config_data)

                if self.instances:
                    print_success(f"Loaded {len(self.instances)} instance configurations")

            except Exception as e:
                print_error(f"Failed to load instances registry: {e}")
                self.instances = {}

    def save_instances(self):
        """Save instance configurations to registry"""
        try:
            registry_data = {
                'version': '2.0',
                'created': datetime.now().isoformat(),
                'unified_manager': True,
                'instances': {name: asdict(config) for name, config in self.instances.items()}
            }

            with open(self.registry_file, 'w', encoding='utf-8') as f:
                json.dump(registry_data, f, indent=2)

        except Exception as e:
            print_error(f"Failed to save instances registry: {e}")

    def create_instance_interactive(self) -> bool:
        """Interactive instance creation with full configuration"""
        print_header("🆕 Create New BlackBoT Instance")

        try:
            # Instance name
            while True:
                name = print_question("Instance name (alphanumeric, unique): ").strip()
                if name and self._validate_instance_name(name) and name not in self.instances:
                    break
                if name in self.instances:
                    print_error(f"Instance '{name}' already exists")
                else:
                    print_error("Invalid instance name. Use alphanumeric characters and underscores only")

            print_info(f"Creating instance: {name}")
            print()

            # Core Identity
            print(f"{Colors.BOLD}🤖 Bot Identity{Colors.END}")
            nickname = print_question(f"Bot nickname [{name}_bot]: ").strip()
            if not nickname:
                nickname = f"{name}_bot"

            username = print_question(f"IRC username/ident [{name}]: ").strip()
            if not username:
                username = name

            realname = print_question(f"Real name [BlackBoT Instance: {name}]: ").strip()
            if not realname:
                realname = f"BlackBoT Instance: {name}"

            print()

            # Network Configuration
            print(f"{Colors.BOLD}🌐 Network Configuration{Colors.END}")

            # Show popular networks
            self._show_popular_networks()

            servers_input = print_question("IRC servers (comma-separated) [irc.libera.chat:6667]: ").strip()
            if not servers_input:
                servers_input = "irc.libera.chat:6667"

            # Parse servers and normalize format
            servers = []
            for server in servers_input.split(','):
                server = server.strip()
                if ':' not in server:
                    server = f"{server}:6667"
                servers.append(server)

            # Port and SSL
            port = 6667
            ssl_enabled = print_question("Enable SSL connection? [y/N]: ").lower().startswith('y')
            ssl_cert_file = ""
            ssl_key_file = ""

            if ssl_enabled:
                port = 6697
                print_success("SSL enabled - will use secure connection (default port 6697)")

                use_client_cert = print_question(
                    "Use client TLS certificate (mutual TLS)? [y/N]: "
                ).lower().startswith('y')

                if use_client_cert:
                    default_cert = f"instances/{name}/client_cert.pem"
                    default_key = f"instances/{name}/client_key.pem"

                    cert_input = print_question(
                        f"Path to certificate file (.pem) [{default_cert}]: "
                    ).strip()
                    key_input = print_question(
                        f"Path to private key file (.pem) [{default_key}]: "
                    ).strip()

                    ssl_cert_file = cert_input or default_cert
                    ssl_key_file = key_input or default_key

                    print_success(f"TLS client certificate configured: {ssl_cert_file}")
                else:
                    print_info("No client certificate configured (server-side TLS only)")

            print()

            # Channels
            print(f"{Colors.BOLD}📺 Channel Configuration{Colors.END}")
            channels_input = print_question(f"Channels (comma-separated) [#{name}]: ").strip()
            if not channels_input:
                channels_input = f"#{name}"

            channels = [c.strip() for c in channels_input.split(',')]
            # Ensure channels start with #
            channels = [c if c.startswith('#') else f"#{c}" for c in channels]

            print()

            # Authentication
            print(f"{Colors.BOLD}🔐 Authentication{Colors.END}")
            nickserv_enabled = print_question("Enable NickServ authentication? [y/N]: ").lower().startswith('y')
            nickserv_password = ""

            if nickserv_enabled:
                import getpass
                nickserv_password = getpass.getpass(f"{Colors.CYAN}❓ NickServ password: {Colors.END}")
                if nickserv_password:
                    print_success("NickServ authentication configured")

            print()

            # Advanced Configuration
            print(f"{Colors.BOLD}⚙️ Advanced Configuration{Colors.END}")

            # Environment
            env_options = ['local', 'dev', 'prod', 'test']
            print("Environment types:")
            for i, env in enumerate(env_options, 1):
                desc = {
                    'local': 'Local development (default)',
                    'dev': 'Development environment',
                    'prod': 'Production environment',
                    'test': 'Testing environment'
                }[env]
                print(f"  {i}. {env} - {desc}")

            env_choice = print_question("Choose environment [1-4, default=1]: ").strip()
            try:
                env_idx = int(env_choice) - 1 if env_choice else 0
                environment = env_options[env_idx] if 0 <= env_idx < len(env_options) else 'local'
            except:
                environment = 'local'

            # DCC Port (auto-assign unique)
            dcc_port = self._get_next_available_port()
            print_info(f"Assigned DCC port: {dcc_port}")

            # Other settings with defaults
            message_delay = 1.5
            advanced = print_question("Configure advanced settings (delays, logging)? [y/N]: ").lower().startswith('y')
            if advanced:
                try:
                    delay_input = print_question(f"Message delay in seconds [{message_delay}]: ").strip()
                    if delay_input:
                        message_delay = float(delay_input)
                except:
                    pass

                log_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR']
                print("Log levels:")
                for i, level in enumerate(log_levels, 1):
                    print(f"  {i}. {level}")

                log_choice = print_question("Choose log level [1-4, default=2]: ").strip()
                try:
                    log_idx = int(log_choice) - 1 if log_choice else 1
                    log_level = log_levels[log_idx] if 0 <= log_idx < len(log_levels) else 'INFO'
                except:
                    log_level = 'INFO'
            else:
                log_level = 'INFO'

            # Auto-start
            auto_start = print_question("Auto-start with 'start all'? [y/N]: ").lower().startswith('y')

            print()

            # Create instance configuration
            config = BotInstanceConfig(
                name=name,
                nickname=nickname,
                username=username,
                realname=realname,
                servers=servers,
                port=port,
                ssl_enabled=ssl_enabled,
                channels=channels,
                nickserv_enabled=nickserv_enabled,
                nickserv_password=nickserv_password,
                environment=environment,
                dcc_port=dcc_port,
                message_delay=message_delay,
                log_level=log_level,
                auto_start=auto_start,
                ssl_cert_file = ssl_cert_file,
                ssl_key_file = ssl_key_file
            )

            # Create instance files
            if self._create_instance_files(config):
                self.instances[name] = config
                self.save_instances()
                print_success(f"Instance '{name}' created successfully!")

                # Show summary
                self._show_instance_summary(config)

                # Ask to start now
                start_now = print_question("Start instance now? [Y/n]: ").lower()
                if start_now != 'n':
                    return self.start_instance(name)

                return True
            else:
                print_error(f"Failed to create instance '{name}'")
                return False

        except KeyboardInterrupt:
            print(f"\n{Colors.WARNING}Instance creation cancelled{Colors.END}")
            return False
        except Exception as e:
            print_error(f"Error creating instance: {e}")
            return False

    def _show_popular_networks(self):
        """Show popular IRC networks for reference"""
        print_info("Popular IRC networks:")
        networks = [
            ("Libera Chat", "irc.libera.chat", "6667/6697 (SSL)"),
            ("OFTC", "irc.oftc.net", "6667/6697 (SSL)"),
            ("Undernet", "irc.undernet.org", "6667"),
            ("QuakeNet", "irc.quakenet.org", "6667"),
            ("Rizon", "irc.rizon.net", "6667/6697 (SSL)"),
        ]

        for name, server, ports in networks:
            print(f"   • {Colors.CYAN}{name}{Colors.END}: {server} ({ports})")
        print()

    def _show_instance_summary(self, config: BotInstanceConfig):
        """Show instance configuration summary"""
        print_info("📋 Instance Configuration Summary:")
        print(f"   🤖 Name: {Colors.CYAN}{config.name}{Colors.END}")
        print(f"   🏷️  Nickname: {Colors.CYAN}{config.nickname}{Colors.END}")
        print(f"   🌐 Servers: {Colors.CYAN}{', '.join(config.servers)}{Colors.END}")
        print(f"   📺 Channels: {Colors.CYAN}{', '.join(config.channels)}{Colors.END}")
        print(f"   🌍 Environment: {Colors.CYAN}{config.environment}{Colors.END}")
        print(f"   🔐 SSL: {Colors.CYAN}{'Enabled' if config.ssl_enabled else 'Disabled'}{Colors.END}")
        print(f"   🔌 DCC Port: {Colors.CYAN}{config.dcc_port}{Colors.END}")
        print(f"   💾 Database: {Colors.CYAN}{config.database}{Colors.END}")
        print()

    def _validate_instance_name(self, name: str) -> bool:
        """Validate instance name"""
        return bool(re.match(r'^[a-zA-Z][a-zA-Z0-9_-]*$', name)) and len(name) <= 32

    def _get_next_available_port(self, start_port: int = 52000) -> int:
        """Get next available DCC port"""
        used_ports = {config.dcc_port for config in self.instances.values()}

        port = start_port
        while port in used_ports:
            port += 1

        return port

    def _create_instance_files(self, config: BotInstanceConfig) -> bool:
        """Create all files for an instance"""
        try:
            instance_dir = self.instances_dir / config.name
            instance_dir.mkdir(exist_ok=True)

            # Create subdirectories
            (instance_dir / "logs").mkdir(exist_ok=True)
            (instance_dir / "data").mkdir(exist_ok=True)

            # Create .env file
            env_content = self._generate_env_content(config)
            env_path = self.base_dir / config.config_file

            with open(env_path, 'w', encoding='utf-8') as f:
                f.write(env_content)

            os.chmod(env_path, 0o600)  # Secure permissions

            print_success(f"Created configuration files for '{config.name}'")
            return True

        except Exception as e:
            print_error(f"Failed to create instance files: {e}")
            return False

    def _generate_env_content(self, config: BotInstanceConfig) -> str:
        """Generate .env file content with all supported variables"""
        servers_str = ','.join(config.servers)
        channels_str = ','.join(config.channels)

        env_content = f"""# BlackBoT Instance Configuration: {config.name}
# Generated by Unified BlackBoT Manager on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
# This file contains environment variables for this specific instance

# ═══════════════════════════════════════════════════════════════════
# Environment & Instance Identity
# ═══════════════════════════════════════════════════════════════════

BLACKBOT_ENV={config.environment}
BLACKBOT_INSTANCE_NAME={config.name}

# ═══════════════════════════════════════════════════════════════════
# Bot Identity (IRC Persona)
# ═══════════════════════════════════════════════════════════════════

BLACKBOT_NICKNAME={config.nickname}
BLACKBOT_USERNAME={config.username}
BLACKBOT_REALNAME="{config.realname}"
BLACKBOT_AWAY="No Away"
BLACKBOT_ALTNICK={config.nickname}_

# ═══════════════════════════════════════════════════════════════════
# Network & Server Configuration
# ═══════════════════════════════════════════════════════════════════

BLACKBOT_SERVERS={servers_str}
BLACKBOT_PORT={config.port}
BLACKBOT_SSL_USE={str(config.ssl_enabled).lower()}

# TLS client certificate (optional)
BLACKBOT_SSL_CERT_FILE={config.ssl_cert_file}
BLACKBOT_SSL_KEY_FILE={config.ssl_key_file}

# Source IP configuration (leave empty for auto-detection)
BLACKBOT_SOURCE_IP=
BLACKBOT_SOURCE_PORT=3337

# ═══════════════════════════════════════════════════════════════════
# Channel Configuration
# ═══════════════════════════════════════════════════════════════════

BLACKBOT_CHANNELS={channels_str}

# ═══════════════════════════════════════════════════════════════════
# Authentication & Security
# ═══════════════════════════════════════════════════════════════════

BLACKBOT_NICKSERV_ENABLED={str(config.nickserv_enabled).lower()}
BLACKBOT_NICKSERV_NICK=NickServ
BLACKBOT_NICKSERV_BOTNICK={config.nickname}
"""

        # Add password only if set (security)
        if config.nickserv_enabled and config.nickserv_password:
            env_content += f"BLACKBOT_NICKSERV_PASSWORD={config.nickserv_password}\n"

        env_content += f"""
# ═══════════════════════════════════════════════════════════════════
# Database & Persistence  
# ═══════════════════════════════════════════════════════════════════

BLACKBOT_DATABASE={config.database}

# ═══════════════════════════════════════════════════════════════════
# Logging Configuration
# ═══════════════════════════════════════════════════════════════════

BLACKBOT_LOG_LEVEL={config.log_level}
BLACKBOT_LOG_DIR={config.log_dir}
BLACKBOT_LOG_FILE={config.name}.log
BLACKBOT_LOG_MAX_LINES=100000
BLACKBOT_LOG_BACKUP_COUNT=3

# ═══════════════════════════════════════════════════════════════════
# Performance & Behavior
# ═══════════════════════════════════════════════════════════════════

BLACKBOT_MESSAGE_DELAY={config.message_delay}
BLACKBOT_MESSAGE_MAX_CHARS=450
BLACKBOT_CACHE_SIZE=2000
BLACKBOT_CACHE_TTL=6

# Flood protection
BLACKBOT_PRIVATE_FLOOD_LIMIT=5:3
BLACKBOT_PRIVATE_FLOOD_TIME=2

# ═══════════════════════════════════════════════════════════════════
# Commands & Interaction
# ═══════════════════════════════════════════════════════════════════

BLACKBOT_COMMAND_CHAR={config.command_char}

# ═══════════════════════════════════════════════════════════════════
# Network & DCC Configuration
# ═══════════════════════════════════════════════════════════════════

BLACKBOT_DCC_PORT={config.dcc_port}
BLACKBOT_DCC_PUBLIC_IP=
BLACKBOT_DCC_PORT_RANGE=50000,52000
BLACKBOT_DCC_IDLE_TIMEOUT=600
BLACKBOT_DCC_ALLOW_UNAUTHED=false

# ═══════════════════════════════════════════════════════════════════
# Advanced Features
# ═══════════════════════════════════════════════════════════════════

# Auto-update
BLACKBOT_AUTO_UPDATE_ENABLED=true
BLACKBOT_AUTO_UPDATE_INTERVAL=60

# Monitoring
BLACKBOT_MONITOR_STATUS=true

# BotLink
BLACKBOT_BOTLINK_AUTOCONNECT_GLOBAL=true
BLACKBOT_BOTLINK_AUTOCONNECT_INTERVAL=30

# Authentication & Access
BLACKBOT_MULTIPLE_LOGINS=1
BLACKBOT_AUTO_DEAUTH_TIME=1
BLACKBOT_MAX_ATTEMPT_REJOIN=5

# Hostname format (1-5, see documentation)
BLACKBOT_DEFAULT_HOSTNAME=2

# ═══════════════════════════════════════════════════════════════════
# Instance-Specific Files (Auto-Generated)
# ═══════════════════════════════════════════════════════════════════

BLACKBOT_CONFIG_FILE={config.config_file}
BLACKBOT_LOG_FILE_PATH={config.log_file}
BLACKBOT_PID_FILE={config.pid_file}

# ═══════════════════════════════════════════════════════════════════
# Version & Attribution
# ═══════════════════════════════════════════════════════════════════

BLACKBOT_VERSION="BlackBoT: Python Edition"
BLACKBOT_MANAGER_VERSION="Unified Manager 2.0"

# ═══════════════════════════════════════════════════════════════════
# Notes
# ═══════════════════════════════════════════════════════════════════

# This file is automatically generated and managed by the Unified BlackBoT Manager
# You can edit values manually, but structure changes may be overwritten
# For advanced configuration, use config/{config.environment}.yaml if available
# Instance created: {datetime.now().isoformat()}
"""

        return env_content

    def _create_advanced_config_files(self, config: BotInstanceConfig):
        """Create advanced YAML config files if system available"""
        try:
            instance_config_dir = self.instances_dir / config.name / "config"
            instance_config_dir.mkdir(exist_ok=True)

            # Base YAML config template
            yaml_config = {
                'identity': {
                    'nickname': config.nickname,
                    'username': config.username,
                    'realname': config.realname,
                    'altnick': f"{config.nickname}_"
                },
                'server': {
                    'servers': [{'host': s.split(':')[0], 'port': int(s.split(':')[1])} for s in config.servers],
                    'ssl_enabled': config.ssl_enabled,
                },
                'channels': {
                    'channels': config.channels
                },
                'database': {
                    'sqlite3_database': config.database
                },
                'logging': {
                    'log_level': config.log_level,
                    'log_dir': f"instances/{config.name}/logs",
                    'log_file': f"{config.name}.log"
                },
                'dcc': {
                    'listen_port': config.dcc_port
                },
                'performance': {
                    'message_delay': config.message_delay,
                    'cache_size': 2000,
                    'cache_ttl': 6
                }
            }

            # Add authentication if enabled
            if config.nickserv_enabled:
                yaml_config['authentication'] = {
                    'nickserv_enabled': True,
                    'nickserv_password': config.nickserv_password
                }

            # Save environment-specific config
            config_file = instance_config_dir / f"{config.environment}.yaml"
            with open(config_file, 'w', encoding='utf-8') as f:
                yaml.dump(yaml_config, f, default_flow_style=False, indent=2)

            print_info(f"Created advanced YAML config for '{config.name}'")

        except Exception as e:
            print_warning(f"Could not create advanced config: {e}")

    def get_instance_status(self, name: str) -> str:
        """Get status of an instance"""
        if name not in self.instances:
            return "unknown"

        config = self.instances[name]
        pid_file = self.base_dir / config.pid_file

        if not pid_file.exists():
            return "stopped"

        try:
            with open(pid_file, 'r', encoding='utf-8') as f:
                pid = int(f.read().strip())
        except (ValueError, OSError):
            try:
                pid_file.unlink()
            except Exception:
                pass
            return "stopped"

        if os.name == "nt":
            try:
                import psutil
                p = psutil.Process(pid)
                if p.is_running():
                    return "running"
                else:
                    raise psutil.NoSuchProcess(pid)
            except Exception:
                try:
                    pid_file.unlink()
                except Exception:
                    pass
                return "stopped"
        else:
            try:
                os.kill(pid, 0)
                return "running"
            except (OSError, ProcessLookupError):
                try:
                    pid_file.unlink()
                except Exception:
                    pass
                return "stopped"

    def start_instance(self, name: str, background: bool = True) -> bool:
        """Start a bot instance with full environment setup"""
        if name not in self.instances:
            print_error(f"Instance '{name}' not found")
            return False

        config = self.instances[name]

        if not config.enabled:
            print_warning(f"Instance '{name}' is disabled")
            return False

        # deja rulează?
        if self.get_instance_status(name) == "running":
            print_warning(f"Instance '{name}' is already running")
            return True

        try:
            print_info(f"Starting instance '{name}'...")

            # env de bază
            env = os.environ.copy()

            # încarcă .env dacă există
            env_file = self.base_dir / config.config_file
            if env_file.exists():
                print(f"📋 Loading configuration from {config.config_file}")
                with open(env_file, 'r', encoding='utf-8', errors='replace') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            key, value = line.split('=', 1)
                            env[key.strip()] = value.strip().strip('"\'')

            # variabile pentru instanță
            env.update({
                'BLACKBOT_INSTANCE_NAME': config.name,
                'BLACKBOT_CONFIG_FILE': config.config_file,
                'BLACKBOT_ENV': config.environment,
                'PYTHONPATH': str(self.base_dir)
            })

            # determină fișierul de startup
            startup_file = None
            for option in ("BlackBoT.py",):
                if (self.base_dir / option).exists():
                    startup_file = option
                    break

            if not startup_file:
                print_error("No startup file found (BlackBoT.py)")
                return False

            # alegem executabilul Python
            python_exec = sys.executable
            if os.name == "nt" and python_exec.lower().endswith("python.exe"):
                candidate = python_exec[:-len("python.exe")] + "pythonw.exe"
                if os.path.exists(candidate):
                    python_exec = candidate

            cmd = [python_exec, startup_file]

            if background:
                # ---- BACKGROUND MODE ----
                log_path = self.base_dir / config.log_file
                log_path.parent.mkdir(parents=True, exist_ok=True)
                print_info(f"Starting in background, logging to {config.log_file}")

                if os.name == "nt":
                    # Windows: fără consolă, detașat de terminalul părinte
                    creationflags = 0
                    creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
                    creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)

                    with open(log_path, 'a', encoding='utf-8', errors='replace') as log_file:
                        process = subprocess.Popen(
                            cmd,
                            cwd=self.base_dir,
                            env=env,
                            stdout=log_file,
                            stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL,
                            creationflags=creationflags,
                            close_fds=True,
                        )
                else:
                    # Linux / macOS
                    with open(log_path, 'a', encoding='utf-8', errors='replace') as log_file:
                        process = subprocess.Popen(
                            cmd,
                            cwd=self.base_dir,
                            env=env,
                            stdout=log_file,
                            stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL,
                            start_new_session=True,
                            close_fds=True,
                        )

                # PID file
                pid_file = self.base_dir / config.pid_file
                pid_file.parent.mkdir(parents=True, exist_ok=True)
                with open(pid_file, 'w', encoding='utf-8', errors='replace') as f:
                    f.write(str(process.pid))

                # verificăm dacă a pornit ok
                time.sleep(3)
                if self.get_instance_status(name) == "running":
                    from pathlib import Path as _P
                    py_cmd = _P(sys.executable).name or "python"
                    this_script = _P(sys.argv[0]).name
                    print_success(f"Instance '{name}' started successfully (PID: {process.pid})")
                    print_info(f"📋 Log file: {config.log_file}")
                    print_info(f"🛑 Stop with: {py_cmd} {this_script} stop {name}")
                    return True
                else:
                    print_error(f"Instance '{name}' failed to start")
                    try:
                        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
                            lines = f.readlines()[-10:]
                            if lines:
                                print_error("Last few log lines:")
                                for line in lines:
                                    print("   " + line.rstrip())
                    except Exception:
                        pass
                    return False

            else:
                # ---- FOREGROUND MODE ----
                print_info(f"Starting instance '{name}' in foreground...")
                result = subprocess.run(cmd, cwd=self.base_dir, env=env)
                return result.returncode == 0

        except Exception as e:
            print_error(f"Failed to start instance '{name}': {e}")
            return False

    def stop_instance(self, name: str, force: bool = False) -> bool:
        """Stop a bot instance"""
        if name not in self.instances:
            print_error(f"Instance '{name}' not found")
            return False

        config = self.instances[name]

        if self.get_instance_status(name) == "stopped":
            print_info(f"Instance '{name}' is already stopped")
            return True

        try:
            pid_file = self.base_dir / config.pid_file
            if not pid_file.exists():
                print_warning(f"PID file not found for '{name}'")
                return True

            with open(pid_file, 'r', encoding='utf-8', errors='replace') as f:
                pid = int(f.read().strip())

            print_info(f"Stopping instance '{name}' (PID: {pid})")

            if os.name == "nt":
                # 🔹 Windows: folosim taskkill
                try:
                    if force:
                        # direct hard-kill
                        subprocess.run(
                            ["taskkill", "/PID", str(pid), "/F", "/T"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            check=False,
                        )
                        print_success(f"Force stopped instance '{name}'")
                    else:
                        # 1) încercăm politicos (fără /F)
                        subprocess.run(
                            ["taskkill", "/PID", str(pid)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            check=False,
                        )
                        print_info(f"Sent termination signal to '{name}'")

                        time.sleep(2)

                        check = subprocess.run(
                            ["tasklist", "/FI", f"PID eq {pid}"],
                            capture_output=True,
                            text=True,
                        )
                        if str(pid) in (check.stdout or ""):
                            print_warning(f"Instance '{name}' didn't terminate gracefully, force killing...")
                            subprocess.run(
                                ["taskkill", "/PID", str(pid), "/F", "/T"],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                check=False,
                            )
                        else:
                            print_success(f"Instance '{name}' stopped gracefully")
                except Exception as e:
                    print_error(f"Failed to stop instance '{name}' via taskkill: {e}")
                    return False

            else:
                # 🔹 Linux / macOS: folosim kill / killpg
                try:
                    import psutil

                    # alegem semnalul
                    sig = signal.SIGKILL if force else signal.SIGTERM

                    # încercăm să luăm PGID (process group id)
                    try:
                        pgid = os.getpgid(pid)
                    except Exception:
                        pgid = None

                    if pgid and pgid > 1:
                        # omorâm întregul group (bot + copii)
                        os.killpg(pgid, sig)
                    else:
                        # fallback: doar procesul
                        os.kill(pid, sig)

                    if not force:
                        # așteptăm puțin și, dacă încă trăiește, dăm KILL
                        time.sleep(2)
                        if psutil.pid_exists(pid):
                            print_warning(f"Instance '{name}' didn't terminate gracefully, force killing...")
                            try:
                                if pgid and pgid > 1:
                                    os.killpg(pgid, signal.SIGKILL)
                                else:
                                    os.kill(pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass

                    # dacă am ajuns aici, am trimis semnalele de stop
                    print_success(f"Stop signal sent to instance '{name}'")

                except Exception as e:
                    print_error(f"Failed to stop instance '{name}' on POSIX: {e}")
                    return False

        except (ValueError, OSError, ProcessLookupError) as e:
            print_error(f"Failed to stop instance '{name}': {e}")
            return False

    def list_instances_detailed(self):
        """Show detailed list of all instances"""
        if not self.instances:
            print_info("No instances configured")
            return

        print_header("🤖 BlackBoT Instances - Detailed View")

        for name, config in self.instances.items():
            status = self.get_instance_status(name)
            status_color = Colors.GREEN if status == "running" else Colors.RED if status == "stopped" else Colors.WARNING
            status_emoji = "🟢" if status == "running" else "🔴" if status == "stopped" else "🟡"

            print(f"{status_emoji} {Colors.BOLD}{name}{Colors.END} ({status_color}{status}{Colors.END})")
            print(f"   🏷️  Nickname: {config.nickname}")
            print(f"   🌐 Servers: {', '.join(config.servers)}")
            print(f"   📺 Channels: {', '.join(config.channels)}")
            print(f"   🌍 Environment: {config.environment}")
            print(f"   🔐 SSL: {'Enabled' if config.ssl_enabled else 'Disabled'}")
            print(f"   🔌 DCC Port: {config.dcc_port}")
            print(f"   💾 Database: {config.database}")
            print(f"   📋 Config: {config.config_file}")

            if status == "running":
                try:
                    pid_file = self.base_dir / config.pid_file
                    if pid_file.exists():
                        with open(pid_file, 'r', encoding='utf-8', errors='replace') as f:
                            pid = f.read().strip()
                        print(f"   🆔 PID: {pid}")
                except:
                    pass

            print()

    def show_status_table(self):
        """Show compact status table"""
        if not self.instances:
            print_info("No instances configured")
            return

        print_header("🤖 BlackBoT Multi-Instance Status")

        print(f"{'Name':<20} {'Nickname':<15} {'Status':<10} {'Environment':<10} {'DCC':<6} {'Channels':<25}")
        print("─" * 100)

        for name, config in self.instances.items():
            status = self.get_instance_status(name)
            status_color = Colors.GREEN if status == "running" else Colors.RED if status == "stopped" else Colors.WARNING

            channels_str = ','.join(config.channels[:2])  # Show first 2 channels
            if len(config.channels) > 2:
                channels_str += f",+{len(config.channels)-2}"

            print(f"{name:<20} {config.nickname:<15} {status_color}{status:<10}{Colors.END} "
                  f"{config.environment:<10} {config.dcc_port:<6} {channels_str:<25}")

        print()

        # Show summary
        running = sum(1 for name in self.instances if self.get_instance_status(name) == "running")
        total = len(self.instances)
        print(f"📊 Summary: {Colors.GREEN}{running} running{Colors.END}, {Colors.BLUE}{total-running} stopped{Colors.END}, {Colors.BOLD}{total} total{Colors.END}")

    def start_all_autostart(self):
        """Start all instances marked for auto-start"""
        auto_start_instances = [name for name, config in self.instances.items() if config.auto_start and config.enabled]

        if not auto_start_instances:
            print_info("No instances configured for auto-start")
            return

        print_info(f"Starting {len(auto_start_instances)} auto-start instances...")

        started = 0
        for name in auto_start_instances:
            if self.start_instance(name):
                started += 1
            time.sleep(2)  # Brief pause between starts

        print_success(f"Started {started}/{len(auto_start_instances)} instances")

    def stop_all_instances(self):
        """Stop all running instances"""
        running_instances = [name for name in self.instances if self.get_instance_status(name) == "running"]

        if not running_instances:
            print_info("No running instances to stop")
            return

        print_info(f"Stopping {len(running_instances)} running instances...")

        stopped = 0
        for name in running_instances:
            if self.stop_instance(name):
                stopped += 1

        print_success(f"Stopped {stopped}/{len(running_instances)} instances")

    def delete_instance(self, name: str, keep_data: bool = False) -> bool:
        """Delete an instance"""
        if name not in self.instances:
            print_error(f"Instance '{name}' not found")
            return False

        # Stop if running
        if self.get_instance_status(name) == "running":
            print_info(f"Stopping running instance '{name}'...")
            self.stop_instance(name)

        try:
            config = self.instances[name]
            instance_dir = self.instances_dir / name

            if not keep_data and instance_dir.exists():
                shutil.rmtree(instance_dir)
                print_success(f"Deleted instance directory for '{name}'")
            elif keep_data:
                print_info(f"Kept instance data for '{name}' (as requested)")

            # Remove from registry
            del self.instances[name]
            self.save_instances()

            print_success(f"Instance '{name}' deleted successfully")
            return True

        except Exception as e:
            print_error(f"Failed to delete instance '{name}': {e}")
            return False


def show_main_menu(manager: UnifiedBlackBotManager):
    """Show main interactive menu"""
    while True:
        print_header("🤖 Unified BlackBoT Manager")

        print(f"{Colors.BOLD}System Status:{Colors.END}")
        print(f"   📁 Base Directory: {manager.base_dir}")
        print(f"   🤖 Instances: {len(manager.instances)}")
        running = sum(1 for name in manager.instances if manager.get_instance_status(name) == "running")
        print(f"   ▶️  Running: {running}")
        print()

        print(f"{Colors.BOLD}Available Actions:{Colors.END}")
        print("  1. 🆕 Create new instance (interactive)")
        print("  2. 📋 List instances (detailed)")
        print("  3. 📊 Show status table")
        print("  4. ▶️  Start instance")
        print("  5. ⏹️  Stop instance")
        print("  6. 🗑️  Delete instance")
        print("  7. 🚀 Start all auto-start instances")
        print("  8. ⛔ Stop all running instances")
        print("  9. ⚙️  Instance management (advanced)")
        print("  10. 📄 Export instance configurations")
        print("  11. ❌ Exit")

        try:
            choice = input(f"\n{Colors.CYAN}Choose action [1-12]: {Colors.END}").strip()

            if choice == "1":
                manager.create_instance_interactive()
            elif choice == "2":
                manager.list_instances_detailed()
            elif choice == "3":
                manager.show_status_table()
            elif choice == "4":
                start_instance_interactive(manager)
            elif choice == "5":
                stop_instance_interactive(manager)
            elif choice == "6":
                delete_instance_interactive(manager)
            elif choice == "7":
                manager.start_all_autostart()
            elif choice == "8":
                manager.stop_all_instances()
            elif choice == "9":
                show_advanced_menu(manager)
            elif choice == "10":
                export_configurations(manager)
            elif choice == "11":
                print_info("Goodbye!")
                break
            else:
                print_warning("Invalid choice, please try again")

        except KeyboardInterrupt:
            print(f"\n{Colors.WARNING}Exiting...{Colors.END}")
            break
        except Exception as e:
            print_error(f"Error: {e}")

        if choice != "12":
            input(f"\n{Colors.CYAN}Press Enter to continue...{Colors.END}")

def start_instance_interactive(manager: UnifiedBlackBotManager):
    """Interactive instance start"""
    available = [name for name, config in manager.instances.items() if config.enabled]
    if not available:
        print_info("No enabled instances available")
        return

    print("Available instances:")
    for i, name in enumerate(available, 1):
        status = manager.get_instance_status(name)
        config = manager.instances[name]
        status_emoji = "🟢" if status == "running" else "🔴"
        print(f"  {i}. {status_emoji} {name} ({config.nickname}) - {status}")

    try:
        choice = int(input(f"{Colors.CYAN}Choose instance to start [1-{len(available)}]: {Colors.END}"))
        if 1 <= choice <= len(available):
            name = available[choice - 1]

            foreground = input(f"{Colors.CYAN}Start in foreground (for debugging)? [y/N]: {Colors.END}").lower().startswith('y')
            manager.start_instance(name, background=not foreground)
        else:
            print_error("Invalid choice")
    except (ValueError, KeyboardInterrupt):
        print_warning("Operation cancelled")

def stop_instance_interactive(manager: UnifiedBlackBotManager):
    """Interactive instance stop"""
    running = [name for name in manager.instances if manager.get_instance_status(name) == "running"]

    if not running:
        print_info("No running instances")
        return

    print("Running instances:")
    for i, name in enumerate(running, 1):
        config = manager.instances[name]
        print(f"  {i}. 🟢 {name} ({config.nickname})")

    try:
        choice = int(input(f"{Colors.CYAN}Choose instance to stop [1-{len(running)}]: {Colors.END}"))
        if 1 <= choice <= len(running):
            name = running[choice - 1]
            force = input(f"{Colors.CYAN}Force stop (kill immediately)? [y/N]: {Colors.END}").lower().startswith('y')
            manager.stop_instance(name, force)
        else:
            print_error("Invalid choice")
    except (ValueError, KeyboardInterrupt):
        print_warning("Operation cancelled")

def delete_instance_interactive(manager: UnifiedBlackBotManager):
    """Interactive instance deletion"""
    available = list(manager.instances.keys())
    if not available:
        print_info("No instances available")
        return

    print("Available instances:")
    for i, name in enumerate(available, 1):
        status = manager.get_instance_status(name)
        config = manager.instances[name]
        print(f"  {i}. {name} ({config.nickname}) - {status}")

    try:
        choice = int(input(f"{Colors.CYAN}Choose instance to delete [1-{len(available)}]: {Colors.END}"))
        if 1 <= choice <= len(available):
            name = available[choice - 1]

            # Confirmation
            print_warning(f"This will permanently delete instance '{name}'")
            confirm = input(f"{Colors.RED}Type 'DELETE' to confirm: {Colors.END}")
            if confirm == "DELETE":
                keep_data = input(f"{Colors.CYAN}Keep instance data files? [y/N]: {Colors.END}").lower().startswith('y')
                manager.delete_instance(name, keep_data)
            else:
                print_info("Deletion cancelled")
        else:
            print_error("Invalid choice")
    except (ValueError, KeyboardInterrupt):
        print_warning("Operation cancelled")

def show_advanced_menu(manager: UnifiedBlackBotManager):
    """Show advanced management options"""
    while True:
        print_header("⚙️  Advanced Instance Management")

        print("Advanced options:")
        print("  1. 🔧 Edit instance configuration")
        print("  2. 📁 Browse instance files")
        print("  3. 📊 Instance resource usage")
        print("  4. 🔄 Reload instance configurations")
        print("  5. 🧪 Test instance connectivity")
        print("  6. 📝 View instance logs")
        print("  7. 🔙 Back to main menu")

        try:
            choice = input(f"\n{Colors.CYAN}Choose option [1-7]: {Colors.END}").strip()

            if choice == "1":
                edit_instance_config(manager)
            elif choice == "2":
                browse_instance_files(manager)
            elif choice == "3":
                show_resource_usage(manager)
            elif choice == "4":
                manager.load_instances()
                print_success("Instance configurations reloaded")
            elif choice == "5":
                test_instance_connectivity(manager)
            elif choice == "6":
                view_instance_logs(manager)
            elif choice == "7":
                break
            else:
                print_warning("Invalid choice")

        except KeyboardInterrupt:
            break

        if choice != "7":
            input(f"\n{Colors.CYAN}Press Enter to continue...{Colors.END}")

def edit_instance_config(manager: UnifiedBlackBotManager):
    """Edit instance configuration"""
    available = list(manager.instances.keys())
    if not available:
        print_info("No instances available")
        return

    print("Available instances:")
    for i, name in enumerate(available, 1):
        print(f"  {i}. {name}")

    try:
        choice = int(input(f"{Colors.CYAN}Choose instance to edit [1-{len(available)}]: {Colors.END}"))
        if 1 <= choice <= len(available):
            name = available[choice - 1]
            config = manager.instances[name]
            env_file = manager.base_dir / config.config_file

            if env_file.exists():
                print_info(f"Opening {config.config_file} for editing...")
                if os.name == "nt":
                    editor = os.environ.get("EDITOR", "notepad")
                else:
                    editor = os.environ.get("EDITOR", "nano")
                subprocess.run([editor, str(env_file)])
                print_success("Configuration file edited")
            else:
                print_error("Configuration file not found")
    except (ValueError, KeyboardInterrupt):
        print_warning("Operation cancelled")

def browse_instance_files(manager: UnifiedBlackBotManager):
    """Browse instance files"""
    available = list(manager.instances.keys())
    if not available:
        print_info("No instances available")
        return

    print("Available instances:")
    for i, name in enumerate(available, 1):
        print(f"  {i}. {name}")

    try:
        choice = int(input(f"{Colors.CYAN}Choose instance [1-{len(available)}]: {Colors.END}"))
        if 1 <= choice <= len(available):
            name = available[choice - 1]
            instance_dir = manager.instances_dir / name

            print_info(f"Instance '{name}' files:")
            for item in sorted(instance_dir.rglob('*')):
                if item.is_file():
                    rel_path = item.relative_to(instance_dir)
                    size = item.stat().st_size
                    print(f"   📄 {rel_path} ({size} bytes)")
                elif item.is_dir() and item != instance_dir:
                    rel_path = item.relative_to(instance_dir)
                    print(f"   📁 {rel_path}/")
    except (ValueError, KeyboardInterrupt):
        print_warning("Operation cancelled")

def show_resource_usage(manager: UnifiedBlackBotManager):
    """Show resource usage for running instances"""
    import psutil

    running_instances = [name for name in manager.instances if manager.get_instance_status(name) == "running"]

    if not running_instances:
        print_info("No running instances")
        return

    print_header("📊 Instance Resource Usage")

    for name in running_instances:
        config = manager.instances[name]
        pid_file = manager.base_dir / config.pid_file

        try:
            with open(pid_file, 'r', encoding='utf-8', errors='replace') as f:
                pid = int(f.read().strip())

            process = psutil.Process(pid)

            # Memory usage
            memory_info = process.memory_info()
            memory_mb = memory_info.rss / (1024 * 1024)

            # CPU usage
            cpu_percent = process.cpu_percent(interval=1.0)

            # Uptime
            create_time = process.create_time()
            uptime = time.time() - create_time
            uptime_str = f"{int(uptime//3600):02d}:{int((uptime%3600)//60):02d}:{int(uptime%60):02d}"

            print(f"🤖 {Colors.BOLD}{name}{Colors.END} (PID: {pid})")
            print(f"   💾 Memory: {memory_mb:.1f} MB")
            print(f"   🔥 CPU: {cpu_percent:.1f}%")
            print(f"   ⏱️  Uptime: {uptime_str}")
            print()

        except Exception as e:
            print_warning(f"Could not get stats for {name}: {e}")

def test_instance_connectivity(manager: UnifiedBlackBotManager):
    """Test connectivity for instance servers"""
    available = list(manager.instances.keys())
    if not available:
        print_info("No instances available")
        return

    print("Available instances:")
    for i, name in enumerate(available, 1):
        print(f"  {i}. {name}")

    try:
        choice = int(input(f"{Colors.CYAN}Choose instance to test [1-{len(available)}]: {Colors.END}"))
        if 1 <= choice <= len(available):
            name = available[choice - 1]
            config = manager.instances[name]

            print_info(f"Testing connectivity for instance '{name}'...")

            for server in config.servers:
                host, port = server.split(':')
                port = int(port)

                try:
                    import socket
                    sock = socket.create_connection((host, port), timeout=5)
                    sock.close()
                    print_success(f"✅ {host}:{port} - Connection successful")
                except Exception as e:
                    print_error(f"❌ {host}:{port} - Connection failed: {e}")
    except (ValueError, KeyboardInterrupt):
        print_warning("Operation cancelled")

def view_instance_logs(manager: UnifiedBlackBotManager):
    """View instance logs"""
    available = list(manager.instances.keys())
    if not available:
        print_info("No instances available")
        return

    print("Available instances:")
    for i, name in enumerate(available, 1):
        config = manager.instances[name]
        log_file = manager.base_dir / config.log_file
        status = "📄" if log_file.exists() else "❌"
        print(f"  {i}. {status} {name}")

    try:
        choice = int(input(f"{Colors.CYAN}Choose instance [1-{len(available)}]: {Colors.END}"))
        if 1 <= choice <= len(available):
            name = available[choice - 1]
            config = manager.instances[name]
            log_file = manager.base_dir / config.log_file

            if log_file.exists():
                lines = int(input(f"{Colors.CYAN}Show last how many lines [50]: {Colors.END}") or "50")

                try:
                    with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
                        log_lines = f.readlines()

                    print_header(f"📝 Last {lines} lines from {name}")
                    for line in log_lines[-lines:]:
                        print(line.rstrip())

                except Exception as e:
                    print_error(f"Could not read log file: {e}")
            else:
                print_error("Log file not found")
    except (ValueError, KeyboardInterrupt):
        print_warning("Operation cancelled")

def export_configurations(manager: UnifiedBlackBotManager):
    """Export instance configurations"""
    if not manager.instances:
        print_info("No instances to export")
        return

    export_file = f"blackbot_instances_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    try:
        export_data = {
            'export_date': datetime.now().isoformat(),
            'manager_version': '2.0',
            'instances': {name: asdict(config) for name, config in manager.instances.items()}
        }

        with open(export_file, 'w', encoding='utf-8') as f:
            json.dump(export_data, f, indent=2)

        print_success(f"Configurations exported to: {export_file}")
        print_info(f"Exported {len(manager.instances)} instance configurations")

    except Exception as e:
        print_error(f"Export failed: {e}")



def main():
    """Main entry point"""
    if len(sys.argv) > 1:
        # Command line interface
        manager = UnifiedBlackBotManager()

        command = sys.argv[1].lower()

        if command in ['list', 'ls', 'status']:
            manager.show_status_table()
        elif command == 'start':
            if len(sys.argv) > 2:
                manager.start_instance(sys.argv[2])
            else:
                manager.start_all_autostart()
        elif command == 'stop':
            if len(sys.argv) > 2:
                manager.stop_instance(sys.argv[2])
            else:
                manager.stop_all_instances()
        elif command in ['restart', 'reload']:
            if len(sys.argv) > 2:
                name = sys.argv[2]
                if manager.stop_instance(name):
                    time.sleep(2)
                    manager.start_instance(name)
            else:
                print_error("Instance name required for restart")
        elif command in ['create', 'new']:
            manager.create_instance_interactive()
        elif command in ['delete', 'remove', 'rm']:
            if len(sys.argv) > 2:
                keep = '--keep-data' in sys.argv
                manager.delete_instance(sys.argv[2], keep)
            else:
                print_error("Instance name required for delete")
        elif command == 'detailed':
            manager.list_instances_detailed()
        elif command == 'export':
            export_configurations(manager)
        elif command in ['help', '--help', '-h']:
            print(f"""Unified BlackBoT Manager - Command Line Interface

Usage: {sys.argv[0]} [command] [options]

Commands:
  list, status         Show status table of all instances
  detailed            Show detailed instance information
  start [name]        Start specific instance or all auto-start instances
  stop [name]         Stop specific instance or all running instances
  restart [name]      Restart specific instance
  create, new         Create new instance (interactive)
  delete [name]       Delete instance
  export              Export all configurations
  help                Show this help

Examples:
  {sys.argv[0]} list                 # Show all instances
  {sys.argv[0]} start bot1           # Start instance 'bot1'
  {sys.argv[0]} start                # Start all auto-start instances
  {sys.argv[0]} stop                 # Stop all running instances
  {sys.argv[0]} create               # Create new instance
  {sys.argv[0]} delete bot1 --keep-data  # Delete but keep data

For interactive mode, run without arguments: {sys.argv[0]}
""")
        else:
            print_error(f"Unknown command: {command}")
            print_info(f"Use '{sys.argv[0]} help' for available commands")
    else:
        # Interactive mode
        manager = UnifiedBlackBotManager()
        show_main_menu(manager)

if __name__ == "__main__":
    main()
