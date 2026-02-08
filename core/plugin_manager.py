"""
Hot-Pluggable Plugin System for BlackBoT
========================================
Sistem modular de încărcare dinamică a comenzilor și funcționalităților.

Features:
- ✅ Auto-discovery de plugin-uri din modules/custom/ + plugins/
- ✅ Hot reload la runtime (fără restart)
- ✅ Plugin isolation (fiecare plugin rulează independent)
- ✅ Dependency management (metadata only)
- ✅ Error handling per plugin
- ✅ Plugin lifecycle hooks (on_load/on_unload/on_reload)
- ✅ Hook dispatch pentru evenimente IRC (PRIVMSG/JOIN/PART/QUIT/NICK/KICK/etc.)
- ✅ Alias-uri comenzi (ex: weather -> w)
- ✅ Help generic: .help <cmd> / .help <alias> (filtrează liniile pe flags din paranteză)

Author: Claude
Version: 1.0 (patched + aliases + hooks)
"""

import sys
import importlib.util
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass, field
from datetime import datetime

from core.log import get_logger

logger = get_logger("plugin_manager")


# =============================================================================
# Plugin Metadata
# =============================================================================

@dataclass
class PluginMetadata:
    """Metadata pentru un plugin"""
    name: str
    version: str = "1.0.0"
    author: str = "Unknown"
    description: str = ""
    dependencies: List[str] = field(default_factory=list)
    commands: List[str] = field(default_factory=list)
    enabled: bool = True

    loaded_at: Optional[datetime] = None
    module_path: Optional[str] = None
    error: Optional[str] = None


@dataclass
class PluginCommand:
    """Comandă exportată de un plugin"""
    name: str
    function: Callable
    flags: str = "-"         # '-' = public
    description: str = ""    # help text (multi-line ok)
    plugin_name: str = ""    # setat de manager

    @property
    def level(self):
        """Legacy level property (compat)"""
        if self.flags in ("-", "", "public"):
            return "-"
        if "n" in self.flags or "N" in self.flags:
            return "8"    # owner
        if "M" in self.flags or "A" in self.flags:
            return "40"   # channel admin
        if "O" in self.flags:
            return "40"   # op
        return "10"


# =============================================================================
# Plugin Base Class
# =============================================================================

class PluginBase:
    """
    Clasă de bază pentru plugin-uri.

    Recomandat: definești class Plugin(PluginBase) + on_load() unde faci register_command().
    """

    def __init__(self, bot):
        self.bot = bot
        self.name = self.__class__.__name__  # managerul îl va suprascrie cu numele fișierului
        self.commands: Dict[str, PluginCommand] = {}
        self.hooks: Dict[str, List[Callable]] = {}

    def register_command(self, name: str, function: Callable, flags: str = "-", description: str = ""):
        self.commands[name] = PluginCommand(
            name=name,
            function=function,
            flags=flags,
            description=description or "",
            plugin_name=self.name,
        )

    def register_hook(self, event: str, function: Callable):
        event = (event or "").upper().strip()
        if not event:
            return
        self.hooks.setdefault(event, []).append(function)

    # -------------------------
    # Access helpers (folosește sistemul real BlackBoT: bot.check_access)
    # -------------------------

    def resolve_user_id(self, nick: str, host: str) -> Optional[int]:
        info = self.bot.sql.sqlite_handle(self.bot.botId, nick, host)
        return info[0] if info else None

    def has_access(self, channel: str, nick: str, host: str, required_flags: str) -> bool:
        """
        required_flags poate fi 'n' sau 'nmM' etc.
        Semnificație: TRUE dacă user are ORICARE din flags (same logic ca has_any_flag).
        """
        user_id = self.resolve_user_id(nick, host)
        if not user_id:
            return False

        required_flags = (required_flags or "").strip()
        if not required_flags or required_flags == "-":
            return True

        # OR: dacă are cel puțin una dintre litere
        for f in required_flags:
            try:
                if self.bot.check_access(channel, user_id, f):
                    return True
            except Exception:
                continue
        return False

    def get_user_flags(self, channel: str, nick: str, host: str, universe: str = "NnmMAOV") -> str:
        """
        Returnează un string cu flags-urile pe care le are user-ul (dintr-un univers cunoscut),
        calculat prin bot.check_access(). Nu depinde de coloana USERS.flags.
        """
        user_id = self.resolve_user_id(nick, host)
        if not user_id:
            return ""
        out = []
        for f in universe:
            try:
                if self.bot.check_access(channel, user_id, f):
                    out.append(f)
            except Exception:
                pass
        return "".join(out)

    # -------------------------
    # Lifecycle hooks
    # -------------------------

    def on_load(self):   # optional
        pass

    def on_unload(self): # optional
        pass

    def on_reload(self): # optional
        pass


# =============================================================================
# Plugin Manager
# =============================================================================

class PluginManager:
    def __init__(self, bot, plugin_dirs: List[str] = None):
        self.bot = bot
        self.plugin_dirs = plugin_dirs or ["modules/custom"]

        self.plugins: Dict[str, Any] = {}              # plugin_name -> instance OR module
        self.metadata: Dict[str, PluginMetadata] = {}  # plugin_name -> metadata
        self.commands: Dict[str, PluginCommand] = {}   # canonical_command -> PluginCommand

        self._loaded_modules: Dict[str, Any] = {}      # plugin_name -> module

        # alias -> canonical
        self.command_aliases: Dict[str, str] = {}

        logger.info("PluginManager initialized")

    # -------------------------------------------------------------------------
    # Discovery
    # -------------------------------------------------------------------------

    def discover_plugins(self) -> List[Path]:
        discovered: List[Path] = []
        for plugin_dir in self.plugin_dirs:
            plugin_path = Path(plugin_dir)
            if not plugin_path.exists():
                logger.warning(f"Plugin directory not found: {plugin_dir}")
                continue

            for py_file in plugin_path.glob("*.py"):
                if py_file.name.startswith("_"):
                    continue
                discovered.append(py_file)
        return discovered

    # -------------------------------------------------------------------------
    # Aliases
    # -------------------------------------------------------------------------

    def register_aliases(self, canonical_command: str, aliases: List[str]):
        canonical_command = (canonical_command or "").strip()
        if not canonical_command:
            return
        for a in aliases or []:
            a = (a or "").strip()
            if not a:
                continue
            self.command_aliases[a] = canonical_command

    def resolve_command_name(self, name: str) -> str:
        name = (name or "").strip()
        return self.command_aliases.get(name, name)

    # -------------------------------------------------------------------------
    # Help
    # -------------------------------------------------------------------------

    def send_command_help(self, bot, feedback, channel, nick, host, command: str):
        # 🔁 resolve alias -> canonical command
        command = self.resolve_command_name(command)

        cmd = self.get_command(command)
        if not cmd or not cmd.description:
            bot.send_message(feedback, "No help available.")
            return

        # calc user_id o singură dată
        info = bot.sql.sqlite_handle(bot.botId, nick, host)
        user_id = info[0] if info else None

        def has_any_access(flags: str) -> bool:
            flags = (flags or "").strip()
            if not flags or flags == "-":
                return True
            if user_id is None:
                return False
            # OR
            for f in flags:
                try:
                    if bot.check_access(channel, user_id, f):
                        return True
                except Exception:
                    continue
            return False

        # dacă comanda principală e restricționată, nu afișa help
        if cmd.flags and cmd.flags != "-":
            if not has_any_access(cmd.flags):
                return

        # trimite liniile din description (multi-line)
        lines = [l.rstrip() for l in cmd.description.splitlines() if l.strip()]

        for line in lines:
            # Filtru pe liniile cu flags în paranteză (ex: "... (nmM)")
            # Recomandare: pune flags fix la final, ex: ".quote del <id> -> delete (nmM)"
            if "(" in line and ")" in line and user_id is not None:
                inside = line[line.find("(") + 1:line.find(")")].strip()
                # acceptă doar string-uri de litere (nmM etc.)
                if inside.isalpha() and not has_any_access(inside):
                    continue

            bot.send_message(feedback, line)

    # -------------------------------------------------------------------------
    # Hook dispatch
    # -------------------------------------------------------------------------

    def dispatch_hook(self, event: str, **kwargs):
        """
        Apelează toate hook-urile înregistrate de plugin-uri pentru un event.
        Semnături suportate:
          - hook(bot, **kwargs)
          - hook(**kwargs)
        """
        event = (event or "").upper().strip()
        if not event:
            return

        for plugin_name, plug in list(self.plugins.items()):
            hooks = getattr(plug, "hooks", None)
            if not isinstance(hooks, dict):
                continue

            funcs = hooks.get(event) or hooks.get(event.upper())
            if not funcs:
                continue

            for fn in list(funcs):
                try:
                    try:
                        fn(self.bot, **kwargs)
                    except TypeError:
                        fn(**kwargs)
                except Exception as e:
                    logger.error(f"Hook error: {plugin_name}.{event}: {e}", exc_info=True)

    # -------------------------------------------------------------------------
    # Load / Unload / Reload
    # -------------------------------------------------------------------------

    def load_plugin(self, plugin_path: Path) -> bool:
        plugin_name = plugin_path.stem

        try:
            if plugin_name in self.plugins:
                logger.warning(f"Plugin '{plugin_name}' already loaded")
                return False

            logger.info(f"Loading plugin: {plugin_name}")

            spec = importlib.util.spec_from_file_location(plugin_name, plugin_path)
            if not spec or not spec.loader:
                raise ImportError(f"Could not load spec for {plugin_path}")

            module = importlib.util.module_from_spec(spec)

            # IMPORTANT: pune în sys.modules ca import intern/reload să funcționeze
            sys.modules[plugin_name] = module
            spec.loader.exec_module(module)

            self._loaded_modules[plugin_name] = module

            plugin_instance: Optional[PluginBase] = None

            # 1) Preferă class Plugin
            if hasattr(module, "Plugin"):
                PluginClass = getattr(module, "Plugin")
                plugin_instance = PluginClass(self.bot)
                plugin_instance.name = plugin_name

                if hasattr(plugin_instance, "on_load"):
                    plugin_instance.on_load()

                self._register_plugin_instance(plugin_name, plugin_instance)

            # 2) register(bot)
            elif hasattr(module, "register"):
                result = module.register(self.bot)

                if isinstance(result, dict):
                    for cmd_name, cmd_func in result.items():
                        self._register_command(plugin_name, cmd_name, cmd_func)

                elif isinstance(result, PluginBase):
                    plugin_instance = result
                    plugin_instance.name = plugin_name

                    if hasattr(plugin_instance, "on_load"):
                        plugin_instance.on_load()

                    self._register_plugin_instance(plugin_name, plugin_instance)

                else:
                    raise ValueError(
                        f"register(bot) must return dict or PluginBase; got {type(result).__name__}"
                    )

            else:
                raise ValueError(f"Plugin {plugin_name} must have register() or Plugin class")

            # Metadata (după ce ai înregistrat comenzile)
            metadata = self._extract_metadata(module, plugin_name, str(plugin_path))
            self.metadata[plugin_name] = metadata

            self.plugins[plugin_name] = plugin_instance or module

            logger.info(f"✅ Plugin '{plugin_name}' loaded successfully ({len(metadata.commands)} commands)")
            return True

        except Exception as e:
            logger.error(f"Failed to load plugin '{plugin_name}': {e}", exc_info=True)

            if plugin_name in sys.modules:
                try:
                    del sys.modules[plugin_name]
                except Exception:
                    pass

            self.metadata[plugin_name] = PluginMetadata(
                name=plugin_name,
                error=str(e),
                enabled=False
            )
            return False

    def unload_plugin(self, plugin_name: str) -> bool:
        if plugin_name not in self.plugins:
            logger.warning(f"Plugin '{plugin_name}' not loaded")
            return False

        try:
            logger.info(f"Unloading plugin: {plugin_name}")
            plugin = self.plugins[plugin_name]

            # on_unload
            if hasattr(plugin, "on_unload"):
                try:
                    plugin.on_unload()
                except Exception:
                    logger.error(f"Error in on_unload for '{plugin_name}'", exc_info=True)

            # remove commands
            commands_to_remove = [
                cmd_name for cmd_name, cmd in list(self.commands.items())
                if getattr(cmd, "plugin_name", "") == plugin_name
            ]
            for cmd_name in commands_to_remove:
                self.commands.pop(cmd_name, None)

            # remove aliases that point to removed commands
            alias_to_remove = []
            for alias, canonical in self.command_aliases.items():
                if canonical in commands_to_remove:
                    alias_to_remove.append(alias)
            for a in alias_to_remove:
                self.command_aliases.pop(a, None)

            self.plugins.pop(plugin_name, None)
            self._loaded_modules.pop(plugin_name, None)

            if plugin_name in sys.modules:
                try:
                    del sys.modules[plugin_name]
                except Exception:
                    pass

            if plugin_name in self.metadata:
                self.metadata[plugin_name].enabled = False
                self.metadata[plugin_name].commands = []

            logger.info(f"✅ Plugin '{plugin_name}' unloaded successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to unload plugin '{plugin_name}': {e}", exc_info=True)
            return False

    def reload_plugin(self, plugin_name: str) -> bool:
        metadata = self.metadata.get(plugin_name)
        if not metadata or not metadata.module_path:
            logger.error(f"Cannot reload plugin '{plugin_name}': metadata not found")
            return False

        plugin_path = Path(metadata.module_path)

        if not self.unload_plugin(plugin_name):
            return False

        return self.load_plugin(plugin_path)

    def load_all(self) -> Dict[str, bool]:
        results: Dict[str, bool] = {}
        discovered = self.discover_plugins()

        logger.info(f"Discovered {len(discovered)} plugins")
        for plugin_path in discovered:
            name = plugin_path.stem
            results[name] = self.load_plugin(plugin_path)

        loaded_count = sum(1 for ok in results.values() if ok)
        logger.info(f"Loaded {loaded_count}/{len(results)} plugins successfully")
        return results

    # -------------------------------------------------------------------------
    # Command registration
    # -------------------------------------------------------------------------

    def _register_command(self, plugin_name: str, cmd_name: str, cmd_func: Callable, **kwargs):
        """Dict-style"""
        command = PluginCommand(
            name=cmd_name,
            function=cmd_func,
            plugin_name=plugin_name,
            **kwargs
        )
        self.commands[cmd_name] = command

        if plugin_name in self.metadata:
            self.metadata[plugin_name].commands.append(cmd_name)

    def _register_plugin_instance(self, plugin_name: str, plugin_instance: PluginBase):
        """OOP-style"""
        if not hasattr(plugin_instance, "commands"):
            return

        for cmd_name, cmd in plugin_instance.commands.items():
            cmd.plugin_name = plugin_name
            self.commands[cmd_name] = cmd

    # -------------------------------------------------------------------------
    # Metadata
    # -------------------------------------------------------------------------

    def _extract_metadata(self, module: Any, plugin_name: str, module_path: str) -> PluginMetadata:
        metadata = PluginMetadata(
            name=plugin_name,
            module_path=module_path,
            loaded_at=datetime.now()
        )

        if hasattr(module, "PLUGIN_INFO"):
            info = module.PLUGIN_INFO
            metadata.version = info.get("version", "1.0.0")
            metadata.author = info.get("author", "Unknown")
            metadata.description = info.get("description", "")
            metadata.dependencies = info.get("dependencies", [])

        metadata.commands = [
            cmd_name for cmd_name, cmd in self.commands.items()
            if getattr(cmd, "plugin_name", "") == plugin_name
        ]
        return metadata

    # -------------------------------------------------------------------------
    # Query helpers
    # -------------------------------------------------------------------------

    def get_command(self, cmd_name: str) -> Optional[PluginCommand]:
        cmd_name = self.resolve_command_name(cmd_name)
        return self.commands.get(cmd_name)

    def list_plugins(self) -> List[PluginMetadata]:
        return list(self.metadata.values())

    def list_commands(self) -> List[PluginCommand]:
        return list(self.commands.values())

    def get_plugin_info(self, plugin_name: str) -> Optional[PluginMetadata]:
        return self.metadata.get(plugin_name)


# =============================================================================
# Integration helpers
# =============================================================================

def init_plugin_system(bot) -> PluginManager:
    pm = PluginManager(bot)
    pm.load_all()
    bot.plugin_manager = pm
    logger.info("Plugin system initialized")
    return pm


def handle_plugin_command(bot, channel, feedback, nick, host, command, message):
    """
    Integrează în loop-ul principal de comenzi.
    Returnează True dacă a fost procesată (chiar și silent deny).
    """
    pm = getattr(bot, "plugin_manager", None)
    if not pm:
        return False

    command = pm.resolve_command_name(command)

    plugin_cmd = pm.get_command(command)
    if not plugin_cmd:
        return False

    try:
        from core.flag_manager import is_public_flag

        flags = plugin_cmd.flags or "-"

        # Public -> direct
        if is_public_flag(flags):
            plugin_cmd.function(bot, channel, feedback, nick, host, message)
            return True

        # userId
        info = bot.sql.sqlite_handle(bot.botId, nick, host)
        if not info:
            return True  # silent deny

        user_id = info[0]

        # IMPORTANT:
        # - Dacă flags e un string gen "nmM", noi păstrăm aceeași logică:
        #   acces dacă are ORICARE dintre ele (exact ca help-ul).
        ok = False
        for f in (flags or ""):
            if bot.check_access(channel, user_id, f):
                ok = True
                break

        if not ok:
            return True  # silent deny

        plugin_cmd.function(bot, channel, feedback, nick, host, message)
        return True

    except Exception as e:
        logger.error(f"Error executing plugin command '{command}': {e}", exc_info=True)
        bot.send_message(feedback, f"❌ Error executing command: {e}")
        return True