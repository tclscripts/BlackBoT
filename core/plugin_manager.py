"""
Hot-Pluggable Plugin System for BlackBoT
========================================
Sistem modular de încărcare dinamică a comenzilor și funcționalităților.

Features:
- ✅ Auto-discovery de plugin-uri din modules/custom/ + plugins/
- ✅ Hot reload la runtime (fără restart)
- ✅ Plugin isolation (fiecare plugin rulează independent)
- ✅ Dependency management (metadata + auto-install)
- ✅ Error handling per plugin
- ✅ Plugin lifecycle hooks (on_load/on_unload/on_reload)
- ✅ Hook dispatch pentru evenimente IRC (PRIVMSG/JOIN/PART/QUIT/NICK/KICK/etc.)
- ✅ Alias-uri comenzi (ex: weather -> w)
- ✅ Help generic: .help <cmd> / .help <alias> (filtrează liniile pe flags din paranteză)

Author: Claude
Version: 1.0 (patched + aliases + hooks + deps auto-install)
"""

import sys
import ast
import re
import subprocess
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
        TRUE dacă user are ORICARE din flags (logic OR).
        """
        user_id = self.resolve_user_id(nick, host)
        if not user_id:
            return False

        required_flags = (required_flags or "").strip()
        if not required_flags or required_flags == "-":
            return True

        for f in required_flags:
            try:
                if self.bot.check_access(channel, user_id, f):
                    return True
            except Exception:
                continue
        return False

    def get_user_flags(self, channel: str, nick: str, host: str, universe: str = "NnmMAOV") -> str:
        """Returnează flags pe care le are user-ul (calculat prin bot.check_access)."""
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
    """
    IMPORTANT:
    - dependencies din PLUGIN_INFO sunt "pip names" (ex: yt-dlp, beautifulsoup4).
    - import-name poate fi diferit (ex: yt_dlp, bs4).
    """

    # pip-name -> pip-name (când pluginul dă un alias scurt)
    _PIP_NAME_MAP = {
        "bs4": "beautifulsoup4",
    }

    # pip-name -> import-name (pentru find_spec)
    _IMPORT_NAME_MAP = {
        "yt-dlp": "yt_dlp",
        "beautifulsoup4": "bs4",
        "bs4": "bs4",
    }

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
    # Dependencies: read + install before importing plugin module
    # -------------------------------------------------------------------------

    def _peek_plugin_info(self, plugin_path: Path) -> dict:
        """
        Parsează PLUGIN_INFO dict fără import (safe).
        Returnează {} dacă nu există sau nu poate fi evaluat ca literal.
        """
        try:
            source = plugin_path.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(source, filename=str(plugin_path))

            for node in tree.body:
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == "PLUGIN_INFO":
                            return ast.literal_eval(node.value)  # safe literal dict/list/str/etc
            return {}
        except Exception as e:
            logger.warning(f"Could not parse PLUGIN_INFO for {plugin_path.name}: {e}")
            return {}

    @staticmethod
    def _strip_version(dep: str) -> str:
        base = (dep or "").strip()
        if not base:
            return ""
        return re.split(r"[<>=!~\s]", base, maxsplit=1)[0].strip()

    def _dependency_installed(self, dep: str) -> bool:
        """
        Verifică dacă dependența e importabilă.
        Fix pentru cazuri gen:
          - pip: yt-dlp   -> import: yt_dlp
          - pip: beautifulsoup4 -> import: bs4
        """
        base = self._strip_version(dep)
        if not base:
            return True

        import_map = getattr(self, "_IMPORT_NAME_MAP", {}) or {}
        import_name = import_map.get(base, base)

        # 1) direct
        try:
            if importlib.util.find_spec(import_name) is not None:
                return True
        except Exception:
            pass

        # 2) heuristic: dash -> underscore (yt-dlp -> yt_dlp)
        if "-" in base:
            try_name = base.replace("-", "_")
            try:
                if importlib.util.find_spec(try_name) is not None:
                    return True
            except Exception:
                pass

        # 3) last resort: first segment (rare)
        if "." in base:
            try:
                if importlib.util.find_spec(base.split(".", 1)[0]) is not None:
                    return True
            except Exception:
                pass

        return False

    def _ensure_dependencies(self, deps: List[str], plugin_name: str) -> bool:
        """
        Instalează dependențe lipsă cu pip.
        IMPORTANT: dacă check-ul e corect, nu vei mai vedea "Installing..." la fiecare restart.
        """
        deps = deps or []
        missing: List[str] = []

        for d in deps:
            if not d:
                continue
            if self._dependency_installed(d):
                continue

            base = self._strip_version(d)
            pip_name = self._PIP_NAME_MAP.get(base, d)
            missing.append(pip_name)

        if not missing:
            return True

        logger.info(f"Installing missing deps for '{plugin_name}': {missing}")

        try:
            cmd = [
                sys.executable, "-m", "pip", "install",
                "--disable-pip-version-check", "--no-input",
                *missing
            ]
            p = subprocess.run(cmd, capture_output=True, text=True)

            out = (p.stdout or "") + "\n" + (p.stderr or "")
            if p.returncode != 0:
                logger.error(f"pip install failed for '{plugin_name}': {out.strip()}")
                return False

            # Verificare după pip (ca să nu mințim în log)
            still_missing = [d for d in deps if not self._dependency_installed(d)]
            if still_missing:
                logger.error(f"Deps still missing after pip for '{plugin_name}': {still_missing}")
                return False

            # log mai curat
            if "already satisfied" in out.lower():
                logger.info(f"ℹ️ Deps already satisfied for '{plugin_name}'")
            else:
                logger.info(f"✅ Deps installed for '{plugin_name}': {missing}")
            return True

        except Exception as e:
            logger.error(f"Dependency install error for '{plugin_name}': {e}", exc_info=True)
            return False

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
        command = self.resolve_command_name(command)

        cmd = self.get_command(command)
        if not cmd or not cmd.description:
            bot.send_message(feedback, "No help available.")
            return

        info = bot.sql.sqlite_handle(bot.botId, nick, host)
        user_id = info[0] if info else None

        def has_any_access(flags: str) -> bool:
            flags = (flags or "").strip()
            if not flags or flags == "-":
                return True
            if user_id is None:
                return False
            for f in flags:
                try:
                    if bot.check_access(channel, user_id, f):
                        return True
                except Exception:
                    continue
            return False

        if cmd.flags and cmd.flags != "-":
            if not has_any_access(cmd.flags):
                return

        lines = [l.rstrip() for l in cmd.description.splitlines() if l.strip()]
        for line in lines:
            if "(" in line and ")" in line and user_id is not None:
                inside = line[line.find("(") + 1:line.find(")")].strip()
                if inside.isalpha() and not has_any_access(inside):
                    continue
            bot.send_message(feedback, line)

    # -------------------------------------------------------------------------
    # Hook dispatch
    # -------------------------------------------------------------------------

    def dispatch_hook(self, event: str, **kwargs):
        """
        Dispatch hooks registered by plugins.
        Safe for different hook signatures (filters kwargs).
        """
        import inspect

        event = (event or "").upper().strip()
        if not event:
            return

        payload = dict(kwargs)
        payload.setdefault("bot", self.bot)

        if "message" in payload and "msg" not in payload:
            payload["msg"] = payload["message"]
        if "msg" in payload and "message" not in payload:
            payload["message"] = payload["msg"]

        for plugin_name, plug in list(self.plugins.items()):
            hooks = getattr(plug, "hooks", None)
            if not isinstance(hooks, dict):
                continue

            funcs = hooks.get(event) or hooks.get(event.upper())
            if not funcs:
                continue

            for fn in list(funcs):
                try:
                    sig = inspect.signature(fn)
                    accepted = sig.parameters
                    call_kwargs = {k: v for k, v in payload.items() if k in accepted}

                    # dacă lipsesc args obligatorii, nu apela hook-ul (nu spama log)
                    for name, p in accepted.items():
                        if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                            continue
                        if p.default is inspect._empty and name not in call_kwargs:
                            raise StopIteration

                    fn(**call_kwargs)

                except StopIteration:
                    continue
                except Exception as e:
                    logger.error(f"Hook error: {plugin_name}.{event}: {e}", exc_info=True)

    # -------------------------------------------------------------------------
    # Load / Unload / Reload
    # -------------------------------------------------------------------------

    def load_plugin(self, plugin_path: Path) -> bool:
        plugin_name = plugin_path.stem

        try:
            # --- deps BEFORE import ---
            peek_info = self._peek_plugin_info(plugin_path)
            deps = (peek_info or {}).get("dependencies", []) or []
            if deps:
                if not self._ensure_dependencies(deps, plugin_name):
                    raise RuntimeError("Missing dependencies and auto-install failed.")

            if plugin_name in self.plugins:
                logger.warning(f"Plugin '{plugin_name}' already loaded")
                return False

            logger.info(f"Loading plugin: {plugin_name}")

            spec = importlib.util.spec_from_file_location(plugin_name, plugin_path)
            if not spec or not spec.loader:
                raise ImportError(f"Could not load spec for {plugin_path}")

            module = importlib.util.module_from_spec(spec)

            sys.modules[plugin_name] = module
            spec.loader.exec_module(module)

            self._loaded_modules[plugin_name] = module

            plugin_instance: Optional[PluginBase] = None

            if hasattr(module, "Plugin"):
                PluginClass = getattr(module, "Plugin")
                plugin_instance = PluginClass(self.bot)
                plugin_instance.name = plugin_name

                if hasattr(plugin_instance, "on_load"):
                    plugin_instance.on_load()

                self._register_plugin_instance(plugin_name, plugin_instance)

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

            if hasattr(plugin, "on_unload"):
                try:
                    plugin.on_unload()
                except Exception:
                    logger.error(f"Error in on_unload for '{plugin_name}'", exc_info=True)

            commands_to_remove = [
                cmd_name for cmd_name, cmd in list(self.commands.items())
                if getattr(cmd, "plugin_name", "") == plugin_name
            ]
            for cmd_name in commands_to_remove:
                self.commands.pop(cmd_name, None)

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

        if is_public_flag(flags):
            plugin_cmd.function(bot, channel, feedback, nick, host, message)
            return True

        info = bot.sql.sqlite_handle(bot.botId, nick, host)
        if not info:
            return True  # silent deny

        user_id = info[0]

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
