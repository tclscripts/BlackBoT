import datetime
import sys
import ipaddress
import ssl
import os
import queue
import logging
import socket
import time
import platform
import threading
from functools import partial
from queue import Queue
from pathlib import Path
from modules.stats import init_stats_system, shutdown_stats_system

# ============================================================================
# AUTO-DETECT AND ACTIVATE VIRTUAL ENVIRONMENT
# ============================================================================
def _setup_virtual_environment():
    """
    Auto-detect and activate virtual environment, similar to Launcher.py
    This allows BlackBoT.py to run independently with all dependencies.
    """
    # Get the base directory where BlackBoT.py is located
    base_dir = Path(__file__).parent.resolve()
    # Check if we're already in a virtual environment
    if hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix):
        # Already in venv, no need to do anything
        return

    # Try to find virtual environment directory
    possible_venvs = ["environment", ".venv", "venv", "env"]
    venv_dir = None

    for venv_name in possible_venvs:
        candidate = base_dir / venv_name
        if candidate.exists():
            venv_dir = candidate
            break

    if not venv_dir:
        # No venv found, continue with system Python
        return
    # Detect OS and set appropriate paths
    if os.name == "nt":  # Windows
        venv_bin = venv_dir / "Scripts"
        python_exec = venv_bin / "python.exe"
    else:  # Linux/macOS
        venv_bin = venv_dir / "bin"
        python_exec = venv_bin / "python"

    if not python_exec.exists():
        # Venv directory exists but is invalid
        return

    # Activate virtual environment by modifying sys.path and environment
    # This is similar to what 'activate' script does

    # 1. Add venv site-packages to sys.path (at the beginning)
    if os.name == "nt":
        site_packages = venv_dir / "Lib" / "site-packages"
    else:
        # Find the correct site-packages path
        for item in venv_dir.rglob("site-packages"):
            if item.is_dir():
                site_packages = item
                break
        else:
            site_packages = None

    if site_packages and site_packages.exists():
        # Insert at position 0 to have highest priority
        sys.path.insert(0, str(site_packages))

    # 2. Update PATH environment variable to include venv bin
    old_path = os.environ.get("PATH", "")
    new_path = f"{venv_bin}{os.pathsep}{old_path}"
    os.environ["PATH"] = new_path

    # 3. Set VIRTUAL_ENV environment variable
    os.environ["VIRTUAL_ENV"] = str(venv_dir)

    # 4. Update sys.prefix and sys.exec_prefix
    sys.prefix = str(venv_dir)
    sys.exec_prefix = str(venv_dir)


# Call the setup function BEFORE importing Twisted and other dependencies
_setup_virtual_environment()

from twisted.internet import protocol, ssl, reactor
from twisted.words.protocols import irc
from collections import defaultdict, deque
from twisted.internet.threads import deferToThread

sys.path.append(os.path.join(os.path.dirname(__file__), 'core'))
import core.environment_config as env
from core import commands
from core import Variables as v
from core.commands_map import command_definitions
from core.threading_utils import ThreadWorker
from core.sql_manager import SQLManager
from core.threading_utils import get_event
from core.dcc import DCCManager
from core.optimized_cache import SmartTTLCache
from core.cache_manager import (
    LoggedUsersCache,
    KnownUsersCache,
    HostToNicksCache,
)
from core.monitor_client import ensure_enrollment, send_heartbeat, send_monitor_offline
from core.log import get_logger, install_excepthook, log_session_banner
from core.ban_expiration_manager import BanExpirationManager

# ═══════════════════════════════════════════════════════════════════
# Initialize Configuration System
# ═══════════════════════════════════════════════════════════════════
s = env.config

_GLOBAL_WORKERS: dict[str, threading.Thread] = {}
_GLOBAL_LOCK = threading.Lock()

# Initialize global logger using settings.py configuration
logger = get_logger("blackbot")

# Capture and log uncaught exceptions automatically
install_excepthook(logger)

_WORKER_POLICY = {
    "monitor": {"supervise": True, "provide_signals": True, "heartbeat_timeout": 60.0},
    "logged_users": {"supervise": True, "provide_signals": True, "heartbeat_timeout": 90.0},
    "uptime": {"supervise": True, "provide_signals": True, "heartbeat_timeout": 120.0},
    "manual_update": {"supervise": False, "provide_signals": False},
    "botlink": {"supervise": True, "provide_signals": True, "heartbeat_timeout": 90.0},
    "*": {"supervise": True, "provide_signals": False}
}

session_details = {
    "nickname": getattr(s, "nickname", "Unknown"),
    "servers": ", ".join(getattr(s, "servers", []) or []),
    "dcc_fixed_port": str(getattr(s, "dcc_listen_port", "")),
    "log_file": f"{getattr(s, 'logs_dir', 'logs')}/{getattr(s, 'log_file', 'blackbot.log')}",
}

log_session_banner(logger, title="BLACKBOT START", details=session_details)
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s')

servers_order = 0
channelStatusChars = "~@+%"
current_instance = None



def _load_version():
    try:
        with open("VERSION", "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return "unknown"


class Bot(irc.IRCClient):
    def __init__(self, nickname, realname):
        try:
            peer = self.transport.getPeer()
            self.server = getattr(self.factory, "server", None) or getattr(peer, "host", None)
            self.port = getattr(self.factory, "port", None) or getattr(peer, "port", None)
        except Exception:
            pass
        self.logger = logger  # Attach global logger to this instance
        self.logger.info("Logger initialized for BlackBoT instance.")
        self._init_worker_registry()
        self.monitorId = None
        self.shutting_down = False
        self._shutdown_lock = threading.RLock()
        self.version = _load_version()
        self.monitor_enabled = None
        self.hmac_secret = None
        self.commands = None
        self.current_connect_time = 0
        self.current_start_time = 0
        self.recover_nick_timer_start = False
        self.connected = False
        self.channel_details = []
        self.channel_info = {}
        self.nickname = nickname
        self.realname = realname
        self.away = s.away
        self.server = None
        self.port = None
        self.username = s.username
        self.newbot = 0
        self.botId = 0
        self.pending_whois = {}
        self.nickserv_waiting = False
        self.authenticated = False  # Authentication status
        self.auth_method = 'nickserv'  # 'nickserv', 'q', 'x'
        self.auth_service_username = ''  # Store username for Q/X services
        self.auth_timeout_call = None  # Timer pentru timeout autentificare
        self.ignore_cleanup_started = False
        self._hb_stop = None
        self.nick_already_in_use = 0
        self.channels = []
        self.multiple_logins = s.multiple_logins
        self.notOnChannels = []
        # rejoin channel list
        self.rejoin_pending = {}
        # MODIFICARE: Lista de canale unde avem ban temporar
        self.banned_channels = set()

        self.pending_join_requests = {}
        # ---------------------------
        # Anti-flood / rate limit
        # ---------------------------
        self._cooldowns = defaultdict(float)  # key -> last_ts
        self._debounce_calls = {}  # key -> IDelayedCall
        # logged users
        self.logged_in_users = LoggedUsersCache(maxlen=5000, ttl=48*3600)
        self.clean_logged_users(silent=True)
        self.thread_check_logged_users_started = False
        self.known_users = KnownUsersCache(maxlen=10000, ttl=12*3600)  # (channel, nick)
        self.who_lock = threading.RLock()
        self.who_queue = {}
        self.who_replies = {}
        self.user_cache = SmartTTLCache(
            maxlen=2000, ttl=6 * 3600, adaptive_ttl=True,
            background_cleanup=False, enable_stats=True
        )
        self.channel_op_state = {}
        self.pending_ban_checks = {}
        self.pending_ban_max_age = 3600
        self.pending_ban_max_per_channel = 200
        self.pending_ban_global_max = 2000
        self.flood_tracker = defaultdict(lambda: deque())
        self.current_start_time = time.time()
        self.sqlite3_database = s.sqlite3_database
        self.sql = SQLManager.get_instance()
        self.newbot = self.sql.sqlite3_bot_birth(self.username, self.nickname, self.realname,
                                                 self.away)
        self.botId = self.newbot[1]
        self.host_to_nicks = HostToNicksCache(maxlen=3000, ttl=24*3600)
        self._start_worker(
            "uptime",
            target=lambda se, b: self.sql.sqlite_update_uptime(self, self.botId, se, b),
            global_singleton=True
        )
        self._start_worker("known_users", target=self.cleanup_known_users, global_singleton=True)
        self._start_worker("sql_keepalive", target=self._sql_keepalive_loop, global_singleton=True)
        if self.sql.sqlite_has_active_ignores(self.botId):
            logger.info("⏳ Active ignores found. Starting cleanup thread...")
            self._start_worker("ignore_cleanup", target=self.cleanup_ignores)
        self.message_queue = Queue(maxsize=1000)
        self.message_delay = s.message_delay
        self._start_worker("message_sender", target=self._message_worker, global_singleton=False)
        self._start_worker("services_auth", target=self._services_auth_watchdog_loop, global_singleton=True)
        self.thread_check_for_changed_nick = None
        self._migrate_env_on_startup()
        if s.autoUpdateEnabled:
            self._start_worker("auto_update", target=self.auto_update_check_loop, global_singleton=True)

        if self.sql.sqlite_isBossOwner(self.botId) > 0:
            self.unbind_hello = True
        else:
            self.unbind_hello = False
        reactor.addSystemEventTrigger(
            'before', 'shutdown',
            lambda: self.graceful_shutdown("Reactor shutdown")
        )
        self.dcc = DCCManager(
            self,
            public_ip=(getattr(s, "dcc_public_ip", "") or None),
            fixed_port=getattr(s, "dcc_listen_port", None),
            port_range=getattr(s, "dcc_port_range", (50000, 52000)),
            idle_timeout=getattr(s, "dcc_idle_timeout", 600),
            allow_unauthed=bool(getattr(s, "dcc_allow_unauthed", False)),
        )

        try:
            from core.log import register_dcc_callback
            register_dcc_callback(self.dcc.broadcast_log_to_humans)
            logger.info("✅ DCC log streaming registered")
        except Exception as e:
            logger.error(f"Failed to register DCC log callback: {e}")

        # Initialize ban expiration manager
        self.ban_expiration_manager = BanExpirationManager(self)

        # Initialize plugin system
        try:
            from core.plugin_manager import init_plugin_system
            self.plugin_manager = init_plugin_system(self)
            logger.info("✅ Plugin system initialized")
        except Exception as e:
            logger.error(f"Failed to init plugin system: {e}")


    def _cooldown_ok(self, key: str, seconds: float) -> bool:
        """
        Hard rate-limit: permite executarea unei acțiuni max 1 dată la `seconds`.
        """
        t = time.time()
        last = self._cooldowns.get(key, 0.0)
        if (t - last) >= seconds:
            self._cooldowns[key] = t
            return True
        return False

    def _debounce(self, key: str, delay: float, fn, *args, **kwargs):
        """
        Debounce: dacă se cheamă de 100 ori, rulează o singură dată după `delay`
        secunde de la ultima chemare.
        """
        try:
            dc = self._debounce_calls.get(key)
            if dc and dc.active():
                dc.reset(delay)
                return
        except Exception:
            pass

        self._debounce_calls[key] = reactor.callLater(delay, fn, *args, **kwargs)

    def _refresh_channel_state(self, channel: str):
        """
        Refresh "costisitor": MODE + banlist.
        Protejat de cooldown în caz că e chemat din multe locuri.
        """
        if not channel or not channel.startswith("#"):
            return

        # Hard-cooldown pe refresh complet (anti flood)
        if not self._cooldown_ok(f"refresh_state:{channel}", 25.0):
            return

        try:
            self.sendLine(f"MODE {channel}")
            self.sendLine(f"MODE {channel} +b")
        except Exception:
            pass

    def _schedule_refresh_channel_state(self, channel: str, delay: float = 5.0):
        """
        Cheamă refresh cu debounce (anti burst).
        """
        if not channel or not channel.startswith("#"):
            return
        self._debounce(f"refresh_state:{channel}", delay, self._refresh_channel_state, channel)

    def _init_worker_registry(self):
        self._workers = {}
        self._timers = set()

    def _call_later(self, delay, func, *args, **kwargs):
        dc = reactor.callLater(delay, func, *args, **kwargs)
        self._timers.add(dc)
        return dc

    def _cancel_all_timers(self):
        for dc in list(self._timers):
            try:
                if dc.active():
                    dc.cancel()
            except Exception:
                pass
            finally:
                self._timers.discard(dc)

    def graceful_shutdown(self, reason="Shutdown"):
        """Graceful shutdown - run only once"""

        # CRITICAL: Check dacă deja rulează
        with self._shutdown_lock:
            if self.shutting_down:
                return

            self.shutting_down = True

        if hasattr(self, 'factory'):
            self.factory.shutting_down = True
            if hasattr(self.factory, 'stopTrying'):
                try:
                    self.factory.stopTrying()
                except Exception as e:
                    logger.error(f"Error calling stopTrying: {e}")
        # -----------------------------------------------------------

        logger.info(f"=" * 80)
        logger.info(f"🛑 GRACEFUL SHUTDOWN INITIATED: {reason}")
        logger.info(f"=" * 80)

        try:
            # 1. Send QUIT
            if self.connected:
                try:
                    quit_msg = f"QUIT :{reason}"
                    try:
                        self.sendLine(quit_msg)
                    except TypeError:
                        self.sendLine(quit_msg.encode('utf-8'))

                    logger.info("📤 Sent QUIT to IRC server")
                    time.sleep(0.5)
                except Exception as e:
                    logger.warning(f"Could not send QUIT: {e}")

            # 2. Oprește heartbeat
            try:
                self._stop_heartbeat_loop()
                logger.info("✅ Heartbeat stopped")
            except Exception as e:
                logger.error(f"Error stopping heartbeat: {e}")

            # 3. Oprește Ban Expiration Manager
            try:
                if hasattr(self, 'ban_expiration_manager'):
                    self.ban_expiration_manager.stop()
            except Exception as e:
                logger.error(f"Error stopping ban manager: {e}")

            try:
                if hasattr(self, '_workers'):
                    worker_count = len(self._workers)
                    logger.info(f"🔄 Stopping {worker_count} workers...")

                    # Workers care pot fi lenți (ex: requesturi HTTP)
                    slow_workers = ['known_users', 'auto_update', 'botlink']

                    for name in list(self._workers.keys()):
                        try:
                            timeout = 2.0
                            self._stop_worker(name, join_timeout=timeout)
                            logger.debug(f"  ✓ Stopped: {name}")
                        except Exception as e:
                            logger.warning(f"  ✗ Failed to stop {name}: {e}")

                    logger.info(f"✅ All workers stopped")
            except Exception as e:
                logger.error(f"Error stopping workers: {e}")

            # 5. Cancel timers
            try:
                self._cancel_all_timers()
                logger.info("✅ All timers cancelled")
            except Exception as e:
                logger.error(f"Error cancelling timers: {e}")

            # 6. Flush message queue
            try:
                if hasattr(self, 'message_queue'):
                    pending = self.message_queue.qsize()
                    if pending > 0:
                        timeout = time.time() + 2.0
                        while not self.message_queue.empty() and time.time() < timeout:
                            try:
                                self.message_queue.get_nowait()
                                self.message_queue.task_done()
                            except:
                                break
            except Exception as e:
                logger.error(f"Error flushing queue: {e}")

            # 7. Salvează uptime
            try:
                if hasattr(self, 'sql') and hasattr(self, 'botId'):
                    import threading
                    fake_stop = threading.Event()
                    fake_stop.set()

                    self.sql.sqlite_update_uptime(
                        self,
                        self.botId,
                        stop_event=fake_stop,
                        beat=lambda: None
                    )
                    logger.info("✅ Uptime saved to database")
            except Exception as e:
                logger.error(f"Error saving uptime: {e}")

            # 8. Închide SQL
            try:
                if hasattr(self, 'sql'):
                    self.sql.close_connection()
                    logger.info("✅ SQL connection closed")
            except Exception as e:
                logger.error(f"Error closing SQL: {e}")

            # 9. Notify monitor
            try:
                if hasattr(self, 'monitorId') and hasattr(self, 'hmac_secret'):
                    from core.monitor_client import send_monitor_offline
                    send_monitor_offline(self.monitorId, self.hmac_secret)
            except Exception as e:
                logger.error(f"Error notifying monitor: {e}")

            for handler in logging.getLogger().handlers:
                try:
                    handler.flush()
                except:
                    pass

            # B) Trimitem MANUAL mesajul final direct prin socket (bypass logging lent)
            final_msg = (
                f"\n{'=' * 60}\n"
                f"✅ GRACEFUL SHUTDOWN COMPLETE\n"
                f"{'=' * 60}\n"
            )

            has_active_dcc = False
            if hasattr(self, 'dcc') and self.dcc:
                for nick, session in list(self.dcc.sessions.items()):
                    if session and session.transport:
                        try:
                            # Scriem direct bytes pe fir
                            session.transport.write((final_msg + "\r\n").encode('utf-8'))
                            has_active_dcc = True
                        except:
                            pass
            if has_active_dcc:
                time.sleep(4.0)
            else:
                time.sleep(0.5)

            try:
                if hasattr(self, 'dcc_log_handler') and self.dcc_log_handler:
                    self.dcc_log_handler.close()
                    logger.info("✅ DCC log handler closed")
            except Exception as e:
                logger.error(f"Error closing DCC log handler: {e}")

            # 12. Închide efectiv conexiunile DCC (ULTIMUL PAS)
            try:
                if hasattr(self, 'dcc'):
                    logger.info("✅ DCC closed (all sessions)")
                    self.dcc.shutdown(force=True)
            except Exception as e:
                logger.error(f"Error stopping DCC: {e}")

            # Final flush
            for handler in logging.getLogger().handlers:
                try:
                    handler.flush()
                except:
                    pass

        except Exception as e:
            logger.error(f"❌ Error during graceful shutdown: {e}", exc_info=True)

    def _services_auth_watchdog_loop(self, stop_event, beat):
        """
        Periodic services auth watchdog:
        - după split/reconnect, dacă services zic "session still active" sau nu răspund,
          botul va reîncerca AUTH/IDENTIFY la interval.
        """
        import time

        # intervalul “mare” între încercări (secunde)
        interval = int(getattr(s, "auth_watchdog_interval", 60))
        # tick intern, ca să putem opri rapid thread-ul
        tick = 2

        logger.info(f"🛡️ Services auth watchdog started (interval={interval}s)")

        while not stop_event.is_set():
            beat()

            try:
                # dacă nu suntem conectați complet, nu încercăm nimic
                if not getattr(self, "connected", False):
                    for _ in range(max(1, interval)):
                        if stop_event.is_set():
                            break
                        time.sleep(1)
                    continue

                any_auth_configured = (
                        (getattr(s, "quakenet_auth_enabled", False) and getattr(s, "quakenet_username", "") and getattr(
                            s, "quakenet_password", "")) or
                        (getattr(s, "undernet_auth_enabled", False) and getattr(s, "undernet_username", "") and getattr(
                            s, "undernet_password", "")) or
                        (getattr(s, "nickserv_login_enabled", False) and getattr(s, "nickserv_password", ""))
                )

                if not any_auth_configured:
                    # nimic de făcut
                    for _ in range(interval):
                        if stop_event.is_set():
                            break
                        time.sleep(1)
                    continue

                # dacă deja e autentificat -> doar dormim
                if getattr(self, "authenticated", False):
                    for _ in range(interval):
                        if stop_event.is_set():
                            break
                        time.sleep(1)
                    continue

                # dacă așteptăm deja reply la auth -> nu spamăm
                if getattr(self, "nickserv_waiting", False):
                    for _ in range(min(10, interval)):
                        if stop_event.is_set():
                            break
                        time.sleep(1)
                    continue

                # rate-limit: max 1 încercare la `interval` secunde
                if hasattr(self, "_cooldown_ok"):
                    if not self._cooldown_ok("services_auth_attempt", float(interval)):
                        for _ in range(tick):
                            if stop_event.is_set():
                                break
                            time.sleep(1)
                        continue

                logger.warning("🔄 Auth watchdog: re-trying services authentication...")
                self.perform_authentication()

            except Exception as e:
                logger.error(f"Auth watchdog error: {e}", exc_info=True)

            # sleep “mic” ca să fie responsive
            for _ in range(tick):
                if stop_event.is_set():
                    break
                time.sleep(1)

        logger.info("🛡️ Services auth watchdog stopped")

    def _start_worker(self, name: str, target, daemon: bool = True, global_singleton: bool = True):
        import inspect
        # singleton global: dacă deja rulează, nu-l mai porni
        if global_singleton and hasattr(self, "_workers") and name in self._workers:
            tw = self._workers.get(name)
            try:
                if tw and tw.is_alive():
                    return tw
            except Exception:
                pass

        pol = _WORKER_POLICY.get(name) or _WORKER_POLICY["*"]
        supervise = bool(pol.get("supervise", True))
        provide_signals = bool(pol.get("provide_signals", False))
        heartbeat_timeout = pol.get("heartbeat_timeout", None)

        # auto-detect: dacă target are semnătura (stop_event, beat), forțăm provide_signals=True
        try:
            sig = inspect.signature(target)
            if len(sig.parameters) >= 2:
                provide_signals = True
        except Exception:
            pass

        tw = ThreadWorker(
            target=target,
            name=name,
            supervise=supervise,
            provide_signals=provide_signals,
            heartbeat_timeout=heartbeat_timeout
        )
        tw.daemon = daemon
        tw.start()

        if not hasattr(self, "_workers"):
            self._workers = {}
        self._workers[name] = tw
        return tw

    def _stop_worker(self, name: str, join_timeout: float = 1.0):
        tw = self._workers.get(name)
        if not tw:
            return
        try:
            get_event(name).set()
        except Exception:
            pass
        try:
            if getattr(tw, "is_alive", lambda: False)():
                tw.join(timeout=join_timeout)
        except Exception:
            pass
        self._workers.pop(name, None)

    def _on_reactor(self, func, *args, **kwargs):
        from twisted.internet import reactor
        reactor.callFromThread(func, *args, **kwargs)

    def irc_msg(self, channel: str, message: str):
        self._on_reactor(self.msg, channel, message)

    def irc_sendline(self, line: str):
        self._on_reactor(self.sendLine, line.encode("utf-8"))

    def irc_ERROR(self, prefix, params):
        msg = " ".join(params) if params else ""
        self.logger.error(f"❌ IRC ERROR from server: {msg}")

    def _monitor_init_worker(self):
        try:
            server_str = f"{self.server}:{self.port}" if self.server and self.port else "unknown"
            version = getattr(self, "version", "unknown")

            creds = ensure_enrollment(self.nickname, version, server_str)
            if not creds:
                logger.info("⚠️ Monitor enrollment pending/failed; monitoring disabled for now.")
                self.monitor_enabled = False
                return

            self.monitorId = creds[
                "bot_id"]  # de pus alt nume pentru a adauga in monitor, coincide cu botId din baza de date
            self.hmac_secret = creds["hmac_secret"]
            self.monitor_enabled = True
            logger.info(f"✅ Monitor enrolled (bot_id={self.monitorId[:8]}...). Starting heartbeat.")
            # start heartbeat pe threadpool tot cu deferToThread
            reactor.callFromThread(self._start_heartbeat_loop)

        except Exception as e:
            import traceback
            logger.error(f"❌ Monitor init error: {e}")
            traceback.logger.info_exc()
            self.monitor_enabled = False

    def _start_monitor_init_async(self):
        if getattr(self, "_monitor_init_started", False):
            return
        self._monitor_init_started = True
        d = deferToThread(self._monitor_init_worker)
        d.addErrback(lambda f: logger.info(f"❌ monitor_init errback: {f.getErrorMessage()}"))

    def _collect_metrics(self):
        import psutil
        ram_mb = 0.0
        if psutil:
            try:
                ram_mb = psutil.Process().memory_info().rss / (1024 * 1024)
            except Exception:
                pass
        try:
            server_str = f"{self.server}:{self.port}" if self.server and self.port else "unknown"
        except Exception:
            server_str = "unknown"
        payload = {
            "bot_id": str(self.monitorId),
            "nickname": self.nickname,
            "version": getattr(self, "version", "unknown"),
            "system": f"{platform.system()} {platform.release()}",
            "cpu_model": platform.processor(),
            "server": server_str,
            "ram_mb": float(f"{ram_mb:.2f}"),
            "ip": getattr(self, "public_ip", None),
        }
        return payload

    def _start_heartbeat_loop(self):
        """Start heartbeat loop with proper worker management"""
        # Oprește worker-ul existent dacă există
        if hasattr(self, '_hb_worker_name'):
            try:
                self._stop_worker(self._hb_worker_name, join_timeout=2.0)
            except Exception as e:
                logger.debug(f"Error stopping old heartbeat worker: {e}")

        # Nume unic pentru worker (bazat pe id-ul instanței)
        self._hb_worker_name = f"heartbeat_{id(self)}"

        def _loop(stop_event, beat):
            """
            Heartbeat loop cu support pentru stop_event și beat.
            CRITICAL: Verifică stop_event ÎNAINTE de connected!
            """
            interval = 30
            backoff = interval

            logger.debug(f"Heartbeat loop started for {self._hb_worker_name}")

            # Loop principal - VERIFICĂ stop_event PRIMUL
            while not stop_event.is_set():
                # Apoi verifică dacă suntem conectați
                if not getattr(self, "monitor_enabled", False) or not self.connected:
                    logger.debug("Heartbeat stopping: not connected or monitoring disabled")
                    break

                try:
                    payload = self._collect_metrics()
                    ok = send_heartbeat(self.monitorId, self.hmac_secret, payload)

                    if ok:
                        backoff = interval
                        beat()  # Signal că suntem alive
                    else:
                        backoff = min(backoff * 2, 300)
                        logger.debug(f"Heartbeat failed, backoff={backoff}s")
                except Exception as e:
                    logger.error(f"Heartbeat error: {e}", exc_info=True)
                    backoff = min(backoff * 2, 300)

                # Interruptible sleep - permite oprire imediată
                if stop_event.wait(timeout=backoff):
                    logger.debug("Heartbeat stopping: stop signal received")
                    break

            logger.debug("Heartbeat loop finished")

        # Pornește worker-ul cu supervizare
        self._start_worker(
            self._hb_worker_name,
            target=_loop,
            global_singleton=False  # NU singleton - per-instance
        )

        logger.debug(f"Heartbeat worker started: {self._hb_worker_name}")

    def _stop_heartbeat_loop(self):
        """Stop heartbeat loop and worker properly - prevents restart loops"""
        if not hasattr(self, '_hb_worker_name'):
            logger.debug("No heartbeat worker to stop")
            return

        worker_name = self._hb_worker_name

        try:
            logger.debug(f"Stopping heartbeat worker: {worker_name}")

            # CRITICAL: Oprește worker-ul (și implicit setează stop_event)
            self._stop_worker(worker_name, join_timeout=3.0)

            # Șterge numele pentru a preveni opriri duplicate
            if hasattr(self, '_hb_worker_name'):
                delattr(self, '_hb_worker_name')

            logger.info("🛑 Heartbeat loop stopped completely")

        except Exception as e:
            logger.error(f"Error stopping heartbeat: {e}", exc_info=True)

    def _get_ident_host_from_channel(self, channel: str, nick: str):
        for c, n, ident, host, *_ in self.channel_details:
            if c == channel and n == nick:
                return ident, host
        return None, None

    def _get_any_ident_host(self, nick: str):
        for c, n, ident, host, *_ in self.channel_details:
            if n == nick:
                return ident, host
        return None, None

    def _remove_user_from_channel(self, channel: str, nick: str):

        nick = nick.lower()
        self.channel_details = [
            row for row in self.channel_details
            if not (row[0].lower() == channel and row[1].lower() == nick)
        ]

    def join_channel(self, name: str):
        if not name:
            return

        # MODIFICARE: Verificăm dacă canalul este banat temporar
        if name in self.banned_channels:
            logger.info(f"⛔ Skipping join for {name} (temporarily ignored due to BAN/KEY/INVITE error).")
            return

        self.pending_join_requests[name.lower()] = name
        self.join(name)

    def _safe_cache_delete(self, cache_obj, key):
        """Delete key from dict / cache objects without crashing if unsupported."""
        if cache_obj is None or key is None:
            return

        try:
            # dict-like
            if hasattr(cache_obj, "pop"):
                try:
                    cache_obj.pop(key, None)
                    return
                except TypeError:
                    # some pop() implementations may not accept default
                    try:
                        cache_obj.pop(key)
                        return
                    except Exception:
                        pass

            # common cache APIs
            for meth in ("delete", "remove", "discard", "invalidate"):
                fn = getattr(cache_obj, meth, None)
                if callable(fn):
                    try:
                        fn(key)
                        return
                    except Exception:
                        pass

            # last resort: if it's a dict-like with __contains__ and supports assignment removal
            if hasattr(cache_obj, "__contains__") and key in cache_obj:
                try:
                    # some caches expose internal dict as .cache or .data
                    inner = getattr(cache_obj, "cache", None) or getattr(cache_obj, "data", None)
                    if inner and hasattr(inner, "pop"):
                        inner.pop(key, None)
                except Exception:
                    pass

        except Exception:
            # never allow cache cleanup to crash the bot
            pass

    def _clean_user_memory(self, nick, channel=None, is_quit=False):
        """
        Curăță urmele unui user din memorie.
        :param nick: Nickname-ul userului
        :param channel: Canalul specific (pentru PART/KICK). None pentru QUIT (toate).
        :param is_quit: Dacă e True, șterge userul de peste tot (QUIT).
        """
        if is_quit:
            # Scoatem userul de peste tot
            self.channel_details = [
                row for row in self.channel_details
                if len(row) > 1 and row[1] != nick
            ]
        elif channel:
            self.channel_details = [
                row for row in self.channel_details
                if not (len(row) > 1 and row[0] == channel and row[1] == nick)
            ]
        if is_quit:
            self.known_users = {
                (c, n) for (c, n) in self.known_users if n != nick
            }
        elif channel:
            self.known_users.discard((channel, nick))

        # 3. Curățare HOST_TO_NICKS (Leak-ul principal)
        # Trebuie să căutăm în tot dicționarul unde apare nick-ul
        if hasattr(self, 'host_to_nicks'):
            empty_hosts = []
            for host, nicks in self.host_to_nicks.items():
                if nick in nicks:
                    nicks.discard(nick)
                    if not nicks:  # Dacă lista e goală, marcăm hostul pentru ștergere
                        empty_hosts.append(host)
            for host in empty_hosts:
                del self.host_to_nicks[host]

        # 4. Curățare LOGGED_IN_USERS (Sesiuni agățate)
        # Dacă userul dă QUIT, îl scoatem din sesiunea activă
        if is_quit and hasattr(self, 'logged_in_users'):
            for uid, data in list(self.logged_in_users.items()):
                # Curățăm nick-ul din setul de nicks al userului
                if "nicks" in data and nick in data["nicks"]:
                    data["nicks"].discard(nick)
                if data.get("nick") == nick:
                    pass
        if is_quit:
            keys_to_del = [k for k in self.user_cache.keys() if k[0] == nick]
            for k in keys_to_del:
                del self.user_cache[k]

    def userQuit(self, user, quitMessage):
        nick = user.split('!', 1)[0] if user else ""
        logger.info(f"⬅️ QUIT: {nick} ({quitMessage})")

        # Plugin hooks (QUIT)
        pm = getattr(self, "plugin_manager", None)
        if pm:
            ident = None
            host = None

            if user and "!" in user and "@" in user:
                try:
                    ident, host = user.split("!", 1)[1].split("@", 1)
                except Exception:
                    ident, host = None, None

            pm.dispatch_hook(
                "QUIT",
                nick=nick,
                ident=ident,
                host=host,
                message=quitMessage
            )

        # 🧹 CURĂȚENIE GENERALĂ
        self._clean_user_memory(nick, is_quit=True)

    def _set_user_modes(self):
        """
        Set user modes after connection

        Example modes:
        - +x: Hide IP/hostname (Undernet, QuakeNet)
        - +i: Invisible (don't show in global WHO)
        - +B: Bot flag
        - +w: Wallops (receive oper messages)
        """
        user_modes = getattr(s, 'user_modes', '').strip()

        if not user_modes:
            return

        try:
            # Validare simplă - modes trebuie să înceapă cu + sau -
            if not user_modes.startswith(('+', '-')):
                logger.warning(f"⚠️  Invalid user modes format: {user_modes} (must start with + or -)")
                return

            # Trimite MODE command
            self.sendLine(f"MODE {self.nickname} {user_modes}")
            logger.info(f"🎭 Setting user modes: {user_modes}")

        except Exception as e:
            logger.error(f"❌ Failed to set user modes: {e}")

    def signedOn(self):
        # start stats system
        init_stats_system(self)

        logger.info("Signed on to the server")
        # load commands
        self.load_commands()
        try:
            if self.connected:
                self._start_monitor_init_async()
        except Exception as e:
            logger.info(f"⚠️ Monitor init failed: {e}")
            self.monitor_enabled = False
            self.user_cache.clear()
        if self.known_users:
            self.known_users.clear()
        self.connected = True
        self.current_connect_time = time.time()
        self.sendLine("AWAY :" + s.away)
        self._set_user_modes()
        # Start ban expiration manager
        if hasattr(self, 'ban_expiration_manager'):
            self.ban_expiration_manager.start()
        any_auth_configured = (
                (getattr(s, "quakenet_auth_enabled", False) and s.quakenet_username and s.quakenet_password) or
                (getattr(s, "undernet_auth_enabled", False) and s.undernet_username and s.undernet_password) or
                (getattr(s, "nickserv_login_enabled", False) and getattr(s, "nickserv_password", ""))
        )

        if any_auth_configured:
            self.perform_authentication()
            logger.info("⏳ Waiting for services auth before joining channels.")
            return
        else:
            logger.info("No auth configured.")

        self._join_channels()

    def lineReceived(self, line):
        try:
            line = line.decode("utf-8")
        except UnicodeDecodeError:
            return
        super().lineReceived(line)

    def connectionLost(self, reason):
        # shutdown stats system
        shutdown_stats_system()

        """Handle connection loss with proper cleanup order"""
        logger.info(f"🔌 Disconnected from IRC: {reason}.")

        if getattr(self, '_shutting_down', False):
            return

        # CRITICAL: Oprește heartbeat ÎNAINTE de a seta connected=False
        # Altfel, loop-ul se termină dar supervizorul încearcă restart
        self._stop_heartbeat_loop()

        # APOI setăm connected=False
        v.connected = False
        self.connected = False

        # Notify monitor that bot is offline
        try:
            if hasattr(self, 'monitorId') and hasattr(self, 'hmac_secret'):
                from core.monitor_client import send_monitor_offline
                send_monitor_offline(self.monitorId, self.hmac_secret)
                logger.info("✅ Monitor notified of offline status")
        except Exception as e:
            logger.error(f"Error notifying monitor of offline status: {e}")

        # Oprește ban expiration manager
        if hasattr(self, 'ban_expiration_manager'):
            try:
                self.ban_expiration_manager.stop()
            except Exception as e:
                logger.error(f"Error stopping ban manager: {e}")

        # Oprește DCC (dar păstrează sesiunile pentru jump/reload)
        if hasattr(self, 'dcc'):
            self.dcc.shutdown()

        # Oprește workers specifici acestei conexiuni
        per_connection_workers = ['message_sender', 'logged_users']

        for worker_name in per_connection_workers:
            if hasattr(self, '_workers') and worker_name in self._workers:
                try:
                    self._stop_worker(worker_name, join_timeout=2.0)
                    logger.debug(f"Stopped per-connection worker: {worker_name}")
                except Exception as e:
                    logger.error(f"Error stopping worker {worker_name}: {e}")

        # Cancel timers
        self._cancel_all_timers()

    # --- Conexiune socket Twisted ---
    def connectionMade(self):
        super().connectionMade()
        self.connected = True

    def _ensure_channel_canonical_in_db(self, requested_name: str, canonical_name: str):

        if self.sql.sqlite_channel_exists_exact(self.botId, canonical_name):
            return

        old = self.sql.sqlite_find_channel_case_insensitive(self.botId, canonical_name)
        if old and old != canonical_name:
            self.sql.sqlite_update_channel_name(self.botId, old, canonical_name)
            return

        self.sql.sqlite3_addchan(canonical_name, self.username, self.botId)

    def joined(self, channel):
        import time
        from twisted.internet import reactor

        logger.info(f"✅ Joined channel: {channel}")

        channel = channel or ""
        if not channel.startswith("#"):
            return

        # Add channel to list if not present
        if channel not in self.channels:
            self.channels.append(channel)

        # Remove from pending rejoin list
        if hasattr(self, "rejoin_pending") and channel in self.rejoin_pending:
            del self.rejoin_pending[channel]

        if hasattr(self, "_rejoin_state") and isinstance(self._rejoin_state, dict):
            st = self._rejoin_state.get(channel)
            if st:
                # rejoin reușit => reset attempts/backoff și anulează pending call dacă există
                try:
                    pc = st.get("pending_call")
                    if pc and pc.active():
                        pc.cancel()
                except Exception:
                    pass
                st["attempts"] = 0
                st["next_delay"] = 10.0
                st["pending_call"] = None
                self._rejoin_state[channel] = st

        # ÎMBUNĂTĂȚIRE: Auto-salvează canalul în baza de date dacă nu există
        try:
            if not self.sql.sqlite_validchan(channel):
                self.sql.sqlite3_addchan(channel, self.username, self.botId)
                logger.info(f"📋 Auto-saved channel {channel} to database")
            else:
                if self.sql.sqlite_is_channel_suspended(channel):
                    logger.info(f"📋 Channel {channel} exists but is suspended")
        except Exception as e:
            logger.error(f"❌ Failed to auto-save channel {channel}: {e}")

        # ✅ Ensure channel_info exists
        if hasattr(self, "channel_info") and channel not in self.channel_info:
            self.channel_info[channel] = {
                'modes': '',
                'bans': [],
                'topic': '',
                'creation_time': None,
                'last_updated': time.time()
            }

        # ----------------------------------------------------------
        # 1) Refresh WHO + MODE + BANLIST (anti-flood)
        # ----------------------------------------------------------
        def _do_join_refresh():
            # Hard cooldown: nu trimitem WHO/MODE la fiecare rejoin burst
            # (poți ajusta timpii)
            if hasattr(self, "_cooldown_ok"):
                if not self._cooldown_ok(f"join_refresh:{channel}", 30.0):
                    return

            try:
                # WHO (membership populate)
                self.sendLine(f"WHO {channel}")
            except Exception:
                pass

            # MODE + MODE +b (folosește helper dacă există)
            try:
                if hasattr(self, "_schedule_refresh_channel_state"):
                    self._schedule_refresh_channel_state(channel, delay=1.0)
                else:
                    self.sendLine(f"MODE {channel}")
                    self.sendLine(f"MODE {channel} +b")
            except Exception:
                pass

        # Debounce: dacă joined e chemat de mai multe ori rapid, rulează 1 singură dată
        if hasattr(self, "_debounce"):
            self._debounce(f"join_refresh:{channel}", 1.0, _do_join_refresh)
        else:
            _do_join_refresh()

        # ----------------------------------------------------------
        # 2) Delayed ban check (anti-thread spam)
        # ----------------------------------------------------------
        if hasattr(self, 'ban_expiration_manager'):

            def _delayed_ban_check():
                try:
                    # Hard cooldown: nu rula check heavy prea des
                    if hasattr(self, "_cooldown_ok"):
                        if not self._cooldown_ok(f"ban_check:{channel}", 45.0):
                            return

                    has_op = False
                    try:
                        has_op = self._has_channel_op(channel)
                    except Exception:
                        has_op = False

                    if has_op:
                        logger.info(f"🔍 Checking users in {channel} for active bans (bot has +o)")
                        try:
                            self.ban_expiration_manager.check_channel_users_on_join(channel)
                        except Exception as e:
                            logger.error(f"ban_expiration_manager.check_channel_users_on_join error: {e}")
                    else:
                        if not hasattr(self, "_queue_pending_ban_check"):
                            logger.info(f"ℹ️ No pending-ban queue helper, skipping queued bans for {channel}")
                            return

                        members = []
                        for row in getattr(self, "channel_details", []):
                            if not isinstance(row, (list, tuple)) or not row:
                                continue
                            if str(row[0]).lower() != channel.lower():
                                continue

                            n = row[1] if len(row) > 1 else None
                            i = row[2] if len(row) > 2 else None
                            h = row[3] if len(row) > 3 else None
                            rn = row[5] if len(row) > 5 else None

                            if n:
                                members.append((n, i, h, rn))

                        queued = 0
                        for n, i, h, rn in members:
                            self._queue_pending_ban_check(
                                channel,
                                n,
                                i or "*",
                                h or "*",
                                rn,
                            )
                            queued += 1

                        if queued:
                            logger.info(f"🕓 No +o on {channel}, queued {queued} users for ban re-check once we get op.")

                except Exception as e:
                    logger.error(f"delayed_ban_check error for {channel}: {e}", exc_info=True)

            # În loc de thread + sleep (care poate porni de N ori),
            # facem debounce + callLater (un singur check per burst).
            if hasattr(self, "_debounce"):
                # rulează după 2 sec de la ultimul joined()
                self._debounce(f"ban_check:{channel}", 2.0, _delayed_ban_check)
            else:
                # fallback dacă n-ai debounce
                reactor.callLater(2.0, _delayed_ban_check)

    def userJoined(self, user, channel):
        # Parse user info
        nick = user.split('!')[0] if '!' in user else user
        ident = user.split('!')[1].split('@')[0] if '!' in user and '@' in user else ''
        host = user.split('@')[1] if '@' in user else ''

        # Plugin hooks (JOIN)
        pm = getattr(self, "plugin_manager", None)
        if pm:
            pm.dispatch_hook(
                "JOIN",
                channel=channel,
                nick=nick,
                ident=ident,
                host=host
            )

        if nick == self.nickname:
            return

        if not hasattr(self, 'ban_expiration_manager'):
            return

        # helper local: programează procesarea queue-ului în mod safe
        def schedule_ban_processing(delay: float = 1.0):
            # fără op n-are sens
            if not self._has_channel_op(channel):
                return

            # debounce = batch pentru multe join-uri
            self._debounce(
                key=f"banq_process:{channel}",
                delay=delay,
                fn=self._process_pending_ban_checks_for_channel,
                channel=channel
            )

        # ---------------------------------------------------------------------
        # ETAPA 1: Fast Check (ident/host imediat)
        # ---------------------------------------------------------------------
        try:
            self._queue_pending_ban_check(
                channel,
                nick,
                ident or "*",
                host or "*",
                None,  # Realname necunoscut momentan
            )

            # ✅ NU procesa imediat, ci debounce (anti burst)
            schedule_ban_processing(delay=1.0)

        except Exception as e:
            logger.error(f"[DEBUG] Error in immediate ban check: {e}")

        # ---------------------------------------------------------------------
        # ETAPA 2: WHO (realname) - încă o verificare, dar tot batch-uită
        # ---------------------------------------------------------------------
        if hasattr(self, "get_user_info_async"):

            def on_who_result(info):
                if not info:
                    return  # Userul a ieșit sau eroare

                realname = info.get('realname')
                u_ident = info.get('ident') or ident
                u_host = info.get('host') or host

                try:
                    self._queue_pending_ban_check(
                        channel,
                        nick,
                        u_ident or "*",
                        u_host or "*",
                        realname
                    )

                    # ✅ tot debounce (dar puțin mai rapid după WHO)
                    schedule_ban_processing(delay=0.5)

                except Exception as e:
                    logger.error(f"[DEBUG] Error in post-WHO ban check: {e}")

            def on_who_err(fail):
                pass

            # ✅ Anti-flood și pentru WHO: max 1 request / nick / canal pe 20s
            # (dacă cineva face rejoin spam)
            who_key = f"who:{channel}:{nick.lower()}"
            if self._cooldown_ok(who_key, 20.0):
                d = self.get_user_info_async(nick, timeout=10)
                d.addCallback(on_who_result)
                d.addErrback(on_who_err)

    def userLeft(self, user, channel):
        """
        Apelată când un user (sau botul) părăsește un canal (PART).
        """
        # Parse ident/host dacă sunt disponibile
        ident = None
        host = None
        # Parsare nick
        nick = user.split('!')[0] if '!' in user else user

        if "!" in user and "@" in user:
            try:
                ident, host = user.split("!", 1)[1].split("@", 1)
            except Exception:
                ident, host = None, None

        # Plugin hooks (PART)
        pm = getattr(self, "plugin_manager", None)
        if pm:
            try:
                # dacă ai reason la PART în alt parametru, pune-l aici.
                pm.dispatch_hook(
                    "PART",
                    channel=channel,
                    nick=nick,
                    ident=ident,
                    host=host,
                    reason="",  # dacă ai mesajul de PART, înlocuiește aici
                )
            except Exception as e:
                logger.error(f"Plugin hook PART error: {e}")

        logger.info(f"⬅️ PART (Left): {nick} from {channel}")

        # ==========================================================
        # CAZ 1: BOTUL A PĂRĂSIT CANALUL
        # ==========================================================
        if nick == self.nickname:
            logger.warning(f"📉 I left {channel}. Wiping all data for this channel.")

            # 1. Curățăm channel_details
            self.channel_details = [
                row for row in self.channel_details
                if len(row) > 0 and row[0] != channel
            ]

            # 2. Curățăm known_users
            self.known_users = {
                (c, n) for (c, n) in self.known_users
                if c != channel
            }

            # 3. Curățăm pending ban checks
            if hasattr(self, 'pending_ban_checks'):
                self.pending_ban_checks.pop(channel, None)

            # 4. Curățăm channel_info (topic / modes / bans cache)
            if hasattr(self, "channel_info"):
                real_key = next(
                    (k for k in self.channel_info.keys() if k.lower() == channel.lower()),
                    None
                )
                if real_key:
                    self.channel_info.pop(real_key, None)

            # 5. Curățăm channel_op_state
            if hasattr(self, "channel_op_state"):
                self.channel_op_state.pop(channel, None)

            return

        # ==========================================================
        # CAZ 2: UN USER NORMAL A PĂRĂSIT CANALUL
        # ==========================================================
        if hasattr(self, '_clean_user_memory'):
            # Folosim helper-ul dacă există
            self._clean_user_memory(nick, channel=channel, is_quit=False)
            return

        # ==========================================================
        # FALLBACK MANUAL (DACĂ NU EXISTĂ HELPER)
        # ==========================================================

        # Extragem hostul userului (dacă există) pentru cleanup eficient
        user_host = None
        for row in self.channel_details:
            if len(row) > 3 and row[0] == channel and row[1] == nick:
                user_host = row[3]
                break

        # 1. Scoatere din channel_details
        self.channel_details = [
            row for row in self.channel_details
            if not (len(row) > 1 and row[0] == channel and row[1] == nick)
        ]

        # 2. Scoatere din known_users
        self.known_users.discard((channel, nick))

        # 3. Curățare host_to_nicks (fără scan global inutil)
        if hasattr(self, 'host_to_nicks'):
            if user_host and user_host in self.host_to_nicks:
                self.host_to_nicks[user_host].discard(nick)
                if not self.host_to_nicks[user_host]:
                    del self.host_to_nicks[user_host]
            else:
                # fallback vechi (doar dacă nu știm hostul)
                empty_hosts = []
                for h, nicks in self.host_to_nicks.items():
                    if nick in nicks:
                        nicks.discard(nick)
                        if not nicks:
                            empty_hosts.append(h)
                for h in empty_hosts:
                    del self.host_to_nicks[h]

    def userKicked(self, kickee, channel, kicker, message):
        """
        Apelată când ALT user primește kick.
        """
        # Ignorăm dacă cumva Twisted apelează asta pentru bot (deși are kickedFrom)
        if kickee == self.nickname:
            return

        # Normalize kicker nick (dacă vine cu ident/host)
        kicker_nick = kicker.split('!')[0] if isinstance(kicker, str) and '!' in kicker else kicker

        # 1. Recuperăm info pentru SEEN înainte să ștergem userul
        ident = ""
        host = ""

        # Căutăm în cache-ul local (channel_details)
        # Format row: [channel, nick, ident, host, ...]
        for row in self.channel_details:
            if len(row) > 3 and row[0] == channel and row[1] == kickee:
                ident = row[2] or ""
                host = row[3] or ""
                break

        pm = getattr(self, "plugin_manager", None)
        if pm:
            try:
                pm.dispatch_hook(
                    "KICK",
                    channel=channel,
                    target=kickee,  # nick-ul celui dat afară
                    kicker=kicker_nick,  # cine a dat kick
                    message=message,  # reason/text
                    ident=ident,
                    host=host,
                )
            except Exception as e:
                logger.error(f"Plugin hook KICK error: {e}")

        # 3. Log
        logger.info(f"👢 KICK: {kickee} was kicked from {channel} by {kicker_nick} ({message})")

        # 4. Curățăm memoria userului (folosind helper-ul robust)
        if hasattr(self, '_clean_user_memory'):
            self._clean_user_memory(kickee, channel=channel, is_quit=False)
            return

        # ==========================================================
        # FALLBACK MANUAL (DACĂ NU EXISTĂ HELPER)
        # ==========================================================

        # 4.1 Scoatere din channel_details
        self.channel_details = [
            r for r in self.channel_details
            if not (len(r) > 1 and r[0] == channel and r[1] == kickee)
        ]

        # 4.2 Scoatere din known_users
        self.known_users.discard((channel, kickee))

        # 4.3 Curățare host_to_nicks (dacă avem host)
        if hasattr(self, 'host_to_nicks'):
            if host and host in self.host_to_nicks:
                self.host_to_nicks[host].discard(kickee)
                if not self.host_to_nicks[host]:
                    del self.host_to_nicks[host]
            else:
                # fallback dacă n-avem host (scan minim)
                empty_hosts = []
                for h, nicks in self.host_to_nicks.items():
                    if kickee in nicks:
                        nicks.discard(kickee)
                        if not nicks:
                            empty_hosts.append(h)
                for h in empty_hosts:
                    del self.host_to_nicks[h]

        # 4.4 Curățăm și din pending bans (dacă era în coadă să fie verificat)
        if hasattr(self, 'pending_ban_checks'):
            chan_pending = self.pending_ban_checks.get(channel)
            if chan_pending:
                chan_pending.pop((kickee or "").lower(), None)

    def kickedFrom(self, channel, kicker, message):
        """
        Apelată când BOTUL primește kick.
        Trebuie să șteargă TOATE datele despre acel canal și să încerce rejoin,
        dar cu anti-flood (cooldown + backoff).
        """
        from twisted.internet import reactor
        import time

        logger.warning(f"⚠️ I was kicked from {channel} by {kicker} ({message})")

        channel = channel or ""
        if not channel.startswith("#"):
            return

        # ----------------------------------------------------------
        # 0) Init anti-flood state (o singură dată)
        # ----------------------------------------------------------
        if not hasattr(self, "_rejoin_state"):
            # channel -> dict(last_ts, attempts, next_delay, pending_call)
            self._rejoin_state = {}

        st = self._rejoin_state.get(channel) or {
            "last_ts": 0.0,
            "attempts": 0,
            "next_delay": 10.0,
            "pending_call": None,
        }

        now = time.time()

        # Hard cooldown: dacă tocmai ai fost kicked acum câteva secunde, nu spama
        if (now - st["last_ts"]) < 5.0:
            logger.warning(f"[rejoin] Kick burst on {channel} -> ignored (cooldown)")
            st["last_ts"] = now
            self._rejoin_state[channel] = st
            return

        st["last_ts"] = now
        st["attempts"] += 1

        # Exponential backoff (max 10 min)
        # 10s, 20s, 40s, 80s, 160s, 320s, 600s...
        if st["attempts"] == 1:
            st["next_delay"] = 10.0
        else:
            st["next_delay"] = min(st["next_delay"] * 2.0, 600.0)

        # Dacă există deja un rejoin programat, nu mai programa altul
        try:
            pc = st.get("pending_call")
            if pc and pc.active():
                logger.warning(
                    f"[rejoin] Rejoin already scheduled for {channel} in {pc.getTime() - reactor.seconds():.1f}s")
                self._rejoin_state[channel] = st
                return
        except Exception:
            st["pending_call"] = None

        self._rejoin_state[channel] = st

        # ----------------------------------------------------------
        # 1) Curățăm TOATĂ memoria legată de canal
        # ----------------------------------------------------------
        self.channel_details = [
            row for row in self.channel_details
            if len(row) > 0 and row[0] != channel
        ]

        self.known_users = {
            (c, n) for (c, n) in self.known_users
            if c != channel
        }

        if hasattr(self, 'pending_ban_checks'):
            self.pending_ban_checks.pop(channel, None)

        # ✅ curățăm și channel_info (topic/modes/bans cache)
        if hasattr(self, "channel_info"):
            real_key = next((k for k in self.channel_info.keys() if k.lower() == channel.lower()), None)
            if real_key:
                self.channel_info.pop(real_key, None)

        # ✅ curățăm op-state
        if hasattr(self, "channel_op_state"):
            self.channel_op_state.pop(channel, None)

        # ----------------------------------------------------------
        # 2) Note / pending list
        # ----------------------------------------------------------
        if hasattr(self, 'addChannelToPendingList'):
            try:
                self.addChannelToPendingList(channel, f"kicked by {kicker} with reason: {message}")
            except Exception:
                pass

        # ----------------------------------------------------------
        # 3) Rejoin (cu backoff)
        # ----------------------------------------------------------
        delay = float(st["next_delay"])

        def _do_rejoin():
            # clear pending_call
            try:
                st2 = self._rejoin_state.get(channel)
                if st2:
                    st2["pending_call"] = None
                    self._rejoin_state[channel] = st2
            except Exception:
                pass

            try:
                logger.warning(f"[rejoin] Attempt {st['attempts']} -> JOIN {channel} (delay={delay:.1f}s)")
                self.join(channel)
            except Exception as e:
                logger.error(f"[rejoin] Failed to JOIN {channel}: {e}")

        st["pending_call"] = reactor.callLater(delay, _do_rejoin)
        self._rejoin_state[channel] = st

        logger.warning(f"[rejoin] Scheduled JOIN for {channel} in {delay:.1f}s (attempt {st['attempts']})")

    def irc_NICK(self, prefix, params):
        """
        Gestionează schimbarea de nume la nivel raw.
        Actualizează channel_details ȘI self.nickname înainte de orice altceva.
        """
        old_nick = prefix.split('!')[0]
        new_nick = params[0]

        try:
            # 1. ACTUALIZARE CRITICĂ ÎN MEMORIE (channel_details)
            affected_channels = []
            old_lower = old_nick.lower()

            for row in self.channel_details:
                # row: [channel, nick, ident, host, privs, realname, userId]
                if len(row) > 1 and row[1].lower() == old_lower:
                    row[1] = new_nick
                    affected_channels.append(row[0])

            # 2. DETECTARE SELF-RENAME & SYNC
            if old_nick.lower() == self.nickname.lower():
                self.nickname = new_nick
                if hasattr(self, 'factory'):
                    self.factory.nickname = new_nick

                # Forțăm sincronizarea OP-ului
                for chan in affected_channels:
                    self.sendLine(f"WHO {chan}")  # Va declanșa irc_unknown -> Update OP
                    if hasattr(self, "_refresh_channel_state"):
                        self._debounce(f"refresh:{chan}", 2.0, self._refresh_channel_state, chan)

            # 3. APELĂM LOGICA SECUNDARĂ (Pluginuri, Cache, Banuri)
            # [FIX ERROR]: Nu mai trimitem affected_channels aici pentru că modulul stats nu acceptă 3 argumente
            self.userRenamed(old_nick, new_nick)

        except Exception as e:
            logger.error(f"Error in irc_NICK: {e}", exc_info=True)

    def userRenamed(self, oldnick, newnick, affected_channels=None):
        """
        Se ocupă de pluginuri, cache-uri și ban checks.
        """
        logger.info(f"🔄 User Renamed: {oldnick} → {newnick}")

        # 1. Plugin Hooks
        pm = getattr(self, "plugin_manager", None)
        if pm:
            try:
                pm.dispatch_hook("NICK", oldnick=oldnick, newnick=newnick)
            except:
                pass

        old_lower = oldnick.lower()
        new_lower = newnick.lower()

        # 2. Actualizare Cache-uri Secundare

        # Known Users - Reconstruim setul
        if any(k[1].lower() == old_lower for k in self.known_users):
            new_known = set()
            for chan, nick in self.known_users:
                if nick.lower() == old_lower:
                    new_known.add((chan, newnick))
                else:
                    new_known.add((chan, nick))
            self.known_users = new_known

        # User ID Cache
        keys_to_update = [k for k in self.user_cache.keys() if k[0].lower() == old_lower]
        for old_key in keys_to_update:
            uid = self.user_cache.pop(old_key)
            self.user_cache[(newnick, old_key[1])] = uid

        # Logged In Users
        for uid, data in self.logged_in_users.items():
            if data.get("nick", "").lower() == old_lower:
                data["nick"] = newnick
            if "nicks" in data:
                nicks_list = list(data["nicks"])
                for n in nicks_list:
                    if n.lower() == old_lower:
                        data["nicks"].remove(n)
                        data["nicks"].add(newnick)

        # 3. Verificare Banuri (Doar dacă nu e botul)
        # [FIX ERROR]: Recalculăm affected_channels dacă lipsește
        if not affected_channels:
            affected_channels = []
            # Deoarece irc_NICK a rulat deja, userul are deja numele NOU în listă
            for row in self.channel_details:
                if len(row) > 1 and row[1].lower() == new_lower:
                    affected_channels.append(row[0])

        if newnick.lower() != self.nickname.lower() and hasattr(self, 'ban_expiration_manager'):
            for chan in affected_channels:
                try:
                    self._queue_pending_ban_check(chan, newnick, "*", "*", None)
                    if self._has_channel_op(chan):
                        self._process_pending_ban_checks_for_channel(chan)
                except:
                    pass

    # add channel to pending list
    def addChannelToPendingList(self, channel, reason):

        if channel in self.rejoin_pending:
            return
        self.rejoin_pending[channel] = {
            'attempts': 0,
            'delay': 5,
            'feedback': channel,
            'reason': reason
        }

    def _remove_from_pending(self, nick, channel=None):
        nick_l = nick.lower()

        if channel:
            ch = self.pending_ban_checks.get(channel)
            if ch and nick_l in ch:
                del ch[nick_l]
                if not ch:
                    self.pending_ban_checks.pop(channel, None)
            return

        for chan, users in list(self.pending_ban_checks.items()):
            if nick_l in users:
                del users[nick_l]
            if not users:
                self.pending_ban_checks.pop(chan, None)

    def _join_channels(self):
        if self.newbot[0] == 0:
            for channel in s.channels:
                self.join_channel(channel)
                self.channels.append(channel)
        else:
            stored_channels = self.sql.sqlite3_channels(self.botId)
            for channel in stored_channels:
                if not self.sql.sqlite_is_channel_suspended(channel[0]):
                    self.join_channel(channel[0])
                    self.channels.append(channel[0])
                    logger.info(f"Joining {channel[0]} ..")
                else:
                    self.notOnChannels.append(channel[0])

    def cleanup_ignores(self):
        stop_ev = get_event("ignore_cleanup")
        while not stop_ev.is_set():
            self.sql.sqlite_cleanup_ignores()
            if not self.sql.sqlite_has_active_ignores(self.botId):
                logger.info("🛑 No more active ignores. Cleanup thread exiting.")
                self.ignore_cleanup_started = False
                break
            time.sleep(60)

    def login_nickserv(self):
        """Legacy method - redirects to universal auth"""
        self.perform_authentication()

    def perform_authentication(self):
        """
        Universal authentication method supporting:
        - NickServ (Libera.Chat, OFTC, etc.)
        - Q (QuakeNet)
        - X (Undernet)
        """
        # Check QuakeNet Q auth
        if s.quakenet_auth_enabled and s.quakenet_username and s.quakenet_password:
            self.login_quakenet_q()
            self._set_auth_timeout()  # ← ADAUGĂ TIMEOUT
            return

        # Check Undernet X auth
        if s.undernet_auth_enabled and s.undernet_username and s.undernet_password:
            self.login_undernet_x()
            self._set_auth_timeout()  # ← ADAUGĂ TIMEOUT
            return

        # Fall back to NickServ
        if not s.nickserv_login_enabled or not s.nickserv_password:
            return

        try:
            self.sendLine(f"PRIVMSG {s.nickserv_nick} :IDENTIFY {s.nickserv_botnick} {s.nickserv_password}")
            logger.info(f"🔐 Sent IDENTIFY to {s.nickserv_nick}")
            self.nickserv_waiting = True
            self.auth_method = 'nickserv'
            self.authenticated = False
            self._set_auth_timeout()  # ← ADAUGĂ TIMEOUT
        except Exception as e:
            logger.error(f"❌ Failed to IDENTIFY to {s.nickserv_nick}: {e}")

    def login_quakenet_q(self):
        """
        Authenticate with QuakeNet Q service

        Format: /msg Q@CServe.quakenet.org AUTH username password
        """
        try:
            service_host = "Q@CServe.quakenet.org"
            auth_msg = f"AUTH {s.quakenet_username} {s.quakenet_password}"

            self.sendLine(f"PRIVMSG {service_host} :{auth_msg}")
            logger.info(f"🔐 Sent AUTH to Q@CServe.quakenet.org")

            # Set waiting flags
            self.nickserv_waiting = True
            self.auth_method = 'q'
            self.authenticated = False
            self.auth_service_username = s.quakenet_username

        except Exception as e:
            logger.error(f"❌ Failed to AUTH to QuakeNet Q: {e}")

    def login_undernet_x(self):
        """
        Authenticate with Undernet X service

        Format: /msg X@channels.undernet.org LOGIN username password
        """
        try:
            service_host = "X@channels.undernet.org"
            auth_msg = f"LOGIN {s.undernet_username} {s.undernet_password}"

            self.sendLine(f"PRIVMSG {service_host} :{auth_msg}")
            logger.info(f"🔐 Sent LOGIN to X@channels.undernet.org")

            # Set waiting flags
            self.nickserv_waiting = True
            self.auth_method = 'x'
            self.authenticated = False
            self.auth_service_username = s.undernet_username

        except Exception as e:
            logger.error(f"❌ Failed to LOGIN to Undernet X: {e}")

    def _set_auth_timeout(self):
        from twisted.internet import reactor

        timeout_seconds = getattr(s, 'auth_timeout_seconds', 30)

        if self.auth_timeout_call and self.auth_timeout_call.active():
            self.auth_timeout_call.cancel()

        # Setează nou timeout
        self.auth_timeout_call = reactor.callLater(
            timeout_seconds,
            self._auth_timeout_handler
        )


    def _auth_timeout_handler(self):
        """
        Handler pentru timeout de autentificare
        Apelat dacă nu s-a primit răspuns de la serviciul de auth în timpul alocat
        """
        if not self.authenticated and self.nickserv_waiting:
            auth_method = getattr(self, "auth_method", "unknown")

            logger.warning(f"⏰ Authentication timeout after 30s!")
            logger.warning(f"   Method: {auth_method}")
            logger.warning(f"   No response from auth service - proceeding anyway")

            # Resetează flags
            self.nickserv_waiting = False
            self.authenticated = False

            # Join channels oricum
            logger.info("➡️  Joining channels without authentication")
            self._join_channels()
        else:
            # Deja autentificat sau nu mai așteaptă - ignore
            logger.debug("Auth timeout handler called but no longer waiting")

    def start_ignore_cleanup_if_needed(self):
        if not self.ignore_cleanup_started:
            if self.sql.sqlite_has_active_ignores(self.botId):
                logger.info("⏳ Active ignores found. Starting cleanup thread...")
                self.thread_ignore_cleanup = ThreadWorker(target=self.cleanup_ignores, name="ignore_cleanup")
                self.thread_ignore_cleanup.start()
                self.ignore_cleanup_started = True

    # -------------------------------------------------------------------------
    # NON-BLOCKING WHO SYSTEM
    # -------------------------------------------------------------------------

    def get_user_info_async(self, nick, timeout=5):
        """
        Returnează un Deferred care va conține info despre user (nick, ident, host, realname).
        NU blochează botul.
        """
        # 1. Verificăm mai întâi cache-ul local (channel_details) pentru viteză
        # Dacă userul e deja cunoscut, returnăm direct rezultatul (fără thread)
        from twisted.internet.defer import succeed

        # Căutăm în listele de canale
        for row in self.channel_details:
            # row format: [channel, nick, ident, host, role, realname]
            if len(row) > 5 and row[1].lower() == nick.lower():
                # Am găsit userul în cache!
                user_data = {
                    'nick': row[1],
                    'ident': row[2],
                    'host': row[3],
                    'realname': row[5]
                }
                return succeed(user_data)

        # 2. Dacă nu e în cache, pornim un thread pentru WHO
        return deferToThread(self._thread_wait_for_who, nick, timeout)

    def _thread_wait_for_who(self, nick, timeout):
        """
        Această funcție rulează într-un thread separat.
        Aici avem voie să folosim time.sleep!
        """
        import time

        # Curățăm variabila temporară unde stocăm rezultatul (trebuie definită în __init__ sau gestionată aici)
        self._temp_who_result = None

        # Trimitem comanda WHO în thread-ul principal (thread-safe)
        reactor.callFromThread(self.sendLine, f"WHO {nick}")

        start_time = time.time()
        while time.time() - start_time < timeout:
            # Așteptăm ca serverul să răspundă și parserul să populeze _temp_who_result
            # (Trebuie să modifici irc_RPL_WHOREPLY să pună datele aici)

            if hasattr(self, '_temp_who_result') and self._temp_who_result:
                if self._temp_who_result['nick'].lower() == nick.lower():
                    return self._temp_who_result

            time.sleep(0.1)  # Aici e OK să dormim, suntem în thread secundar

        return None  # Timeout

    def whois_sync(self, nick, timeout=5):
        """
        Perform a WHOIS lookup and wait synchronously for reply.
        Returns dict with { 'nick':..., 'user':..., 'host':..., 'realname':... } or None on timeout.
        """
        self.pending_whois[nick] = None
        self.sendLine(f"WHOIS {nick}")

        start = time.time()
        while time.time() - start < timeout:
            if self.pending_whois[nick] is not None:
                return self.pending_whois.pop(nick)
            time.sleep(0.1)  # small delay to avoid busy loop
        # timeout
        self.pending_whois.pop(nick, None)
        return None

    def _message_worker(self):
        stop_ev = get_event("message_sender")
        while not stop_ev.is_set():
            try:
                channel, message = self.message_queue.get(timeout=1)
                if channel and message:
                    reactor.callFromThread(self.msg, channel, message)
                time.sleep(self.message_delay)
            except queue.Empty:
                continue

    def check_private_flood_prot(self, host):
        if self.sql.sqlite_is_ignored(self.botId, host):
            return True

        limit, interval = map(int, s.private_flood_limit.split(":"))
        now = time.time()

        self.flood_tracker[host].append(now)
        while self.flood_tracker[host] and now - self.flood_tracker[host][0] > interval:
            self.flood_tracker[host].popleft()

        if len(self.flood_tracker[host]) > limit:
            logger.info(f"⚠️ Flood detected from {host}, blacklisting...")
            reason = f"Flooding bot (>{limit}/{interval}s)"
            self.sql.sqlite_add_ignore(self, self.botId, host, s.private_flood_time * 60, reason)
            return True

        return False

    def check_command_access(self, channel, nick, host, flag_id, feedback=None):
        flags_needed = self.get_flags(flag_id)
        if not flags_needed or str(flags_needed).strip() in ("-", ""):
            return {
                "sql": self.sql,
                "userId": None,
                "handle": nick,  # fallback
                "lhost": self.get_hostname(nick, host, 0),
                "public": True
            }
        lhost = self.get_hostname(nick, host, 0)
        info = self.sql.sqlite_handle(self.botId, nick, lhost)

        if info:
            userId = info[0]
            handle = info[1]
        else:
            userId = None
            for uid, data in self.logged_in_users.items():
                if isinstance(data, dict) and lhost in data.get("hosts", []):
                    userId = uid
                    break
            if not userId:
                return None
            handle = self.logged_in_users[userId].get("nick", nick)  # fallback to nick if missing

        if not self.check_access(channel, userId, flags_needed):
            return None
        stored_hash = self.sql.sqlite_get_user_password(userId)
        if not stored_hash:
            self.send_message(nick,
                              "🔐 You currently have access but no password set. Please use `pass <password>` in private to secure your account. After that use `auth <password> to authenticate.`")
        if not self.is_logged_in(userId, lhost):
            return None

        return {
            "sql": self.sql,
            "userId": userId,
            "handle": handle,
            "lhost": lhost
        }

    def _check_logged_users_loop(self, stop_event, beat):
        full_interval = max(1, int(3600 * getattr(s, "autoDeauthTime", 1)))
        try:
            while not stop_event.is_set():
                beat()

                if not self.logged_in_users:
                    logger.info("✅ No more logged-in users. Monitor thread exiting.")
                    stop_event.set()
                    break

                if not getattr(self, "channels", None):
                    logger.debug("ℹ️ No joined channels; skipping logged_users cleanup this cycle.")
                    if stop_event.wait(min(full_interval, 30)):
                        break
                    continue

                try:
                    self.clean_logged_users()
                except Exception as e:
                    logger.info(f"⚠️ Exception in user cleaner thread: {e}")

                if stop_event.wait(min(full_interval, 30)):
                    break
        finally:
            self.thread_check_logged_users_started = False

    def clean_logged_users(self, silent=False):
        """
        Curăță logged_in_users pe baza hosturilor VĂZUTE pe canale,
        dar respectând autoDeauthTime (în ore, din config).
        Nu mai deloghează instant dacă WHO întârzie sau dacă userul e doar temporar nevăzut.
        """
        try:
            auto_hours = getattr(s, "autoDeauthTime", 1)
            try:
                auto_hours = float(auto_hours)
            except Exception:
                auto_hours = 1.0
            # timp maxim de absență permis (în secunde)
            max_absent = max(60.0, auto_hours * 3600.0)
        except Exception:
            max_absent = 3600.0  # fallback 1 oră

        now = time.time()

        # 1) Construim set cu toate host-urile ONLINE (normalizate la fel ca la auth)
        online_hosts = set()
        for row in list(self.channel_details):
            if not isinstance(row, (list, tuple)) or len(row) < 4:
                continue
            chan, nick, ident, host = row[:4]
            raw = f"{ident}@{host}"
            lhost = self.get_hostname(nick, raw, 0)
            online_hosts.add(lhost)

        to_remove = []

        # 2) Verificăm fiecare user logat
        for userId, data in list(self.logged_in_users.items()):
            hosts = list(data.get("hosts", []))
            last_seen = data.setdefault("last_seen", {})
            keep_hosts = []

            for h in hosts:
                if h in online_hosts:
                    # hostul este vizibil acum pe un canal -> marcat ca „văzut acum”
                    last_seen[h] = now
                    keep_hosts.append(h)
                else:
                    # nu e vizibil acum -> vedem de când nu l-am mai văzut
                    last_ts = last_seen.get(h, now)
                    if now - last_ts < max_absent:
                        # încă în perioada de grație -> păstrăm host-ul
                        keep_hosts.append(h)

            if keep_hosts:
                data["hosts"] = keep_hosts
                data["last_seen"] = last_seen
            else:
                to_remove.append(userId)

        # 3) Delogăm userii la care toate hosturile au expirat
        for userId in to_remove:
            self.logged_in_users.pop(userId, None)
            if not silent:
                logger.info(f"🔒 Auto-logout (netsplit or left): userId={userId}")

    def logoutOnQuit(self, user):
        nick = user
        matching = [u for u in self.channel_details if u[1] == nick]

        if not matching:
            return

        last_seen = matching[0]
        ident = last_seen[2]
        host = last_seen[3]
        formatted_host = self.get_hostname(nick, f"{ident}@{host}", 0)

        to_remove = []

        for userId, data in self.logged_in_users.items():
            hosts = data["hosts"]
            if formatted_host in hosts:
                hosts.remove(formatted_host)
                if not hosts:
                    to_remove.append(userId)
                logger.info(f"🔒 Logout on QUIT: {nick} (userId={userId}) from {formatted_host}")

        for uid in to_remove:
            del self.logged_in_users[uid]

    def get_logged_nick(self, userId):
        if userId in self.logged_in_users:
            return self.logged_in_users[userId].get("nick")
        return None

    def modeChanged(self, user, channel, set, modes, args):

        sign = "+" if set else "-"
        if channel not in self.channels:
            return

        mode_map = {
            'o': '@',  # operator
            'v': '+',  # voice
            'h': '%',  # half-op
            'a': '&',  # admin
            'q': '~',  # owner
        }

        for i, mode_char in enumerate(modes):
            if i >= len(args):
                break

            nick = args[i]

            if mode_char in mode_map:
                privilege_char = mode_map[mode_char]
                self.user_update_status(channel, nick, privilege_char, set)

            if mode_char == 'o' and nick.lower() == self.nickname.lower():
                if hasattr(self, "_on_self_op_mode"):
                    try:
                        self._on_self_op_mode(channel, set)
                    except Exception as e:
                        if hasattr(self, "logger"):
                            self.logger.error(f"modeChanged/_on_self_op_mode error on {channel}: {e}")

    def irc_TOPIC(self, prefix, params):
        """
        :Nick!u@h TOPIC #chan :new topic
        Twisted trimite params de obicei: [#chan, "new topic"] sau [#chan, ":new topic"]
        """
        try:
            if len(params) < 2:
                return
            channel = params[0]
            if not channel.startswith("#"):
                return

            topic = params[1]
            if topic.startswith(":"):
                topic = topic[1:]

            if hasattr(self, "channel_info"):
                if channel not in self.channel_info:
                    self.channel_info[channel] = {
                        'modes': '', 'bans': [], 'topic': '',
                        'creation_time': None, 'last_updated': time.time()
                    }

                self.channel_info[channel]['topic'] = topic
                self.channel_info[channel]['last_updated'] = time.time()
        except Exception as e:
            if hasattr(self, "logger"):
                self.logger.error(f"irc_TOPIC error: {e}", exc_info=True)

    def irc_ERR_BANNEDFROMCHAN(self, prefix, params):
        channel = params[1]

        # Verificăm dacă avem deja un retry programat pentru acest canal
        # (Ca să evităm spamul de timer-e dacă serverul trimite eroarea de 2 ori rapid)
        if not hasattr(self, "_ban_retry_active"):
            self._ban_retry_active = set()

        if channel in self._ban_retry_active:
            return

        logger.warning(f"⛔ BANNED from {channel}. Will retry joining in 60 seconds...")

        # 1. Oprim rejoin-ul rapid standard (cel de câteva secunde) ca să nu facem flood
        if hasattr(self, 'rejoin_pending') and channel in self.rejoin_pending:
            del self.rejoin_pending[channel]

        # 2. Ne asigurăm că NU e în lista de canale ignorate (banned_channels)
        # Dacă e acolo, botul ar putea refuza să dea join intern.
        if hasattr(self, 'banned_channels'):
            self.banned_channels.discard(channel)

        # 3. Marcăm faptul că așteptăm minutul (ca să nu intrăm iar în if-ul de sus)
        self._ban_retry_active.add(channel)

        # 4. Definim funcția care va rula peste 1 minut
        def _retry_join_task():
            # Scoatem marcajul, ca să putem procesa o nouă eroare dacă join-ul eșuează iar
            self._ban_retry_active.discard(channel)

            logger.info(f"🔄 Retrying join on {channel} (Anti-Ban 60s timer)...")
            self.join(channel)

        # 5. Programăm execuția
        from twisted.internet import reactor
        reactor.callLater(60, _retry_join_task)

    def irc_ERR_CHANNELISFULL(self, prefix, params):
        channel = params[1]
        logger.warning(f"⛔ Channel {channel} is FULL. Pausing auto-join for 5 minutes.")
        self.banned_channels.add(channel)

        if channel in self.rejoin_pending:
            del self.rejoin_pending[channel]

        from twisted.internet import reactor
        reactor.callLater(300, lambda: self.banned_channels.discard(channel))

    def irc_ERR_BADCHANNELKEY(self, prefix, params):
        channel = params[1]
        logger.error(f"🔑 Bad key for {channel}. Stopping join attempts permanently (until restart).")
        self.banned_channels.add(channel)

        if channel in self.rejoin_pending:
            del self.rejoin_pending[channel]

    def irc_ERR_INVITEONLYCHAN(self, prefix, params):
        channel = params[1]
        logger.warning(f"📩 {channel} is INVITE ONLY. Pausing auto-join for 5 minutes.")
        self.banned_channels.add(channel)

        if channel in self.rejoin_pending:
            del self.rejoin_pending[channel]

        from twisted.internet import reactor
        reactor.callLater(300, lambda: self.banned_channels.discard(channel))

    def irc_ERR_NOSUCHCHANNEL(self, prefix, params):
        logger.info(f"Error: No such channel {params[1]}")

    def irc_ERR_CANNOTSENDTOCHAN(self, prefix, params):
        logger.info(f"Error: Cannot send to channel {params[1]}")

    def irc_INVITE(self, prefix, params):
        inviter = prefix.split('!')[0]
        logger.info(f"Received invitation from {inviter} to join {params[1]}")
        if params[1] in self.channels:
            if params[1] in self.notOnChannels:
                self.join_channel(params[1])

    def irc_unknown(self, prefix, command, params):

        # =========================================================================
        # PROCESARE MODURI ȘI BANURI DE CANAL
        # =========================================================================
        # 324 / RPL_CHANNELMODEIS: Răspuns la MODE #channel
        if command in ['324', 'RPL_CHANNELMODEIS']:
            try:
                if len(params) >= 3:
                    channel = params[1]
                    modes = params[2]
                    mode_args = params[3:] if len(params) > 3 else []

                    if channel not in self.channel_info:
                        self.channel_info[channel] = {
                            'modes': '', 'bans': [], 'topic': '',
                            'creation_time': None, 'last_updated': time.time()
                        }

                    self.channel_info[channel]['modes'] = modes
                    self.channel_info[channel]['mode_args'] = mode_args
                    self.channel_info[channel]['last_updated'] = time.time()

            except Exception as e:
                logger.error(f"Error processing channel modes: {e}")
            return

        # 367 / RPL_BANLIST: Un ban din listă
        if command in ['367', 'RPL_BANLIST']:
            try:
                if len(params) >= 3:
                    channel = params[1]
                    banmask = params[2]
                    setter = params[3] if len(params) > 3 else 'unknown'
                    timestamp = params[4] if len(params) > 4 else None

                    if channel not in self.channel_info:
                        self.channel_info[channel] = {
                            'modes': '', 'bans': [], 'topic': '',
                            'creation_time': None, 'last_updated': time.time()
                        }

                    ban_entry = {
                        'mask': banmask,
                        'setter': setter,
                        'timestamp': timestamp
                    }

                    if ban_entry not in self.channel_info[channel]['bans']:
                        self.channel_info[channel]['bans'].append(ban_entry)

                    logger.debug(f"ðŸš« Ban list for {channel}: {banmask} (set by {setter})")
            except Exception as e:
                logger.error(f"Error processing ban list: {e}")
            return

        # 368 / RPL_ENDOFBANLIST: Sfârșitul listei de banuri
        if command in ['368', 'RPL_ENDOFBANLIST']:
            try:
                if len(params) >= 2:
                    channel = params[1]
                    logger.debug(f"âœ… Received complete ban list for {channel}")
            except Exception as e:
                logger.error(f"Error processing end of ban list: {e}")
            return

        # 329 / RPL_CREATIONTIME: Timpul de creare a canalului
        if command in ['329', 'RPL_CREATIONTIME']:
            try:
                if len(params) >= 3:
                    channel = params[1]
                    creation_time = params[2]

                    if channel not in self.channel_info:
                        self.channel_info[channel] = {
                            'modes': '', 'bans': [], 'topic': '',
                            'creation_time': None, 'last_updated': time.time()
                        }

                    self.channel_info[channel]['creation_time'] = creation_time
                    logger.debug(f"ðŸ•' Channel {channel} created at: {creation_time}")
            except Exception as e:
                logger.error(f"Error processing creation time: {e}")
            return

        if command in ['332', 'RPL_TOPIC']:
            try:
                # params: [myNick, #chan, :topic...]
                if len(params) >= 3:
                    channel = params[1]
                    topic = params[2]
                    if topic.startswith(":"):
                        topic = topic[1:]

                    if channel not in self.channel_info:
                        self.channel_info[channel] = {
                            'modes': '', 'bans': [], 'topic': '',
                            'creation_time': None, 'last_updated': time.time()
                        }

                    self.channel_info[channel]['topic'] = topic
                    self.channel_info[channel]['last_updated'] = time.time()
            except Exception as e:
                logger.error(f"Error processing topic (332): {e}")
            return

        # 331 / RPL_NOTOPIC: canal fără topic
        if command in ['331', 'RPL_NOTOPIC']:
            try:
                if len(params) >= 2:
                    channel = params[1]
                    if channel not in self.channel_info:
                        self.channel_info[channel] = {
                            'modes': '', 'bans': [], 'topic': '',
                            'creation_time': None, 'last_updated': time.time()
                        }
                    self.channel_info[channel]['topic'] = ''
                    self.channel_info[channel]['last_updated'] = time.time()
            except Exception as e:
                logger.error(f"Error processing no-topic (331): {e}")
            return

        if command == 'PONG':
            return

        if command == "PRIVMSG":
            target, message = params
            nick, ident, host, hostmask = self.parse_prefix(prefix)
            lhost = self.get_hostname(nick, f"{ident}@{host}", 0)
            if self.check_private_flood_prot(lhost):
                return
            if message.startswith("\x01VERSION\x01"):
                user = prefix.split('!')[0]
                self.ctcpQuery_VERSION(user, message)
                return

            # Handle CTCP DCC CHAT offers
            if message.startswith("\x01DCC "):
                try:
                    raw = message.strip("\x01").split()
                    if len(raw) >= 5 and raw[1].upper() == "CHAT":
                        _chat_word = raw[2].lower()
                        ip_token = raw[3]
                        port_token = raw[4]
                        hostmask = f"{ident}@{host}"
                        if not self.dcc.ensure_authed(nick, hostmask):
                            self.send_message(nick, "⛔ DCC denied: not authenticated.")
                            return
                        self.dcc.accept_offer(nick, ip_token, int(port_token), feedback=nick)
                        return
                except Exception as e:
                    self.send_message(nick, f"⚠️ Bad DCC offer: {e}")
                    return

        if command == "RPL_WHOREPLY":
            try:
                if len(params) < 8:
                    return

                wchannel = params[1]
                wident = params[2]
                whost = params[3]
                wnickname = params[5]
                wstatus = params[6]
                wrealname = params[7]

                host = f"{wnickname}!{wident}@{whost}"
                lhost = self.get_hostname(wnickname, f"{wident}@{whost}", 0)

                # 1) remember which nicks we've seen from this logical host
                self.host_to_nicks[lhost].add(wnickname)

                # 2) if someone is already logged on this host, attach the nick
                existing_uid = self.get_logged_in_user_by_host(lhost)
                if existing_uid:
                    self.logged_in_users[existing_uid].setdefault("nicks", set()).add(wnickname)

                privilege_chars = {'@', '+', '%', '&', '~'}
                privileges = ''.join(sorted(c for c in wstatus if c in privilege_chars))

                key = (wnickname, whost)  # cache key: (nick, host-only)
                userId = self.user_cache.get_valid(key)
                if userId is None:
                    info = self.sql.sqlite_handle(self.botId, wnickname, host)  # host = nick!ident@host
                    userId = info[0] if info else None
                    self.user_cache.set(key, userId)

                if (wchannel, wnickname) not in self.known_users:
                    self.channel_details.append([
                        wchannel, wnickname, wident, whost, privileges, wrealname, userId
                    ])
                    self.known_users.add((wchannel, wnickname))

                if wnickname == self.nickname:
                    return

                if userId and not self.is_logged_in(userId, lhost):
                    stored_hash = self.sql.sqlite_get_user_password(userId)
                    if not stored_hash:
                        return
                    autologin_setting = self.sql.sqlite_get_user_setting(self.botId, userId, 'autologin')
                    if autologin_setting is not None and autologin_setting == "0":
                        return

                    if userId not in self.logged_in_users:
                        self.logged_in_users[userId] = {"hosts": [], "nick": wnickname, "nicks": set()}

                    if lhost not in self.logged_in_users[userId]["hosts"]:
                        self.logged_in_users[userId]["hosts"].append(lhost)

                    nicks_on_host = self.host_to_nicks.get(lhost, set()) or {wnickname}
                    self.logged_in_users[userId]["nicks"].update(nicks_on_host)
                    self.logged_in_users[userId]["nick"] = wnickname

                    # ✅ pornește workerul logged_users cu policy (are beat)
                    self._start_worker("logged_users", target=self._check_logged_users_loop, global_singleton=True)

                else:
                    if userId and self.is_logged_in(userId, lhost):
                        self.logged_in_users[userId].setdefault("nicks", set()).add(wnickname)

                # -----------------------------------------------------------
                # 3) Integrare cu Async WHO System (MODIFICAT)
                # -----------------------------------------------------------
                try:
                    # A. Salvăm datele în variabila pe care o monitorizează thread-ul nou
                    # Aceasta permite funcției get_user_info_async să primească răspunsul
                    self._temp_who_result = {
                        "nick": wnickname,
                        "ident": wident,
                        "host": whost,
                        "realname": wrealname,
                        "channel": wchannel,
                        "status": wstatus,
                    }

                    # B. Păstrăm logica veche (Legacy) pentru compatibilitate
                    # Dacă alte module încă folosesc who_queue, să nu le stricăm
                    nick_l = (wnickname or "").lower()
                    if hasattr(self, "who_queue") and hasattr(self, "who_replies"):
                        if nick_l in self.who_queue:
                            self.who_replies[nick_l] = self._temp_who_result
                except Exception:
                    pass
                # -----------------------------------------------------------

                # 4) Integrare cu pending_ban_checks:
                #    - completăm ident/host/realname din WHO
                #    - dacă avem deja +o pe canal, verificăm acum banurile pentru userul ăsta
                if hasattr(self, 'ban_expiration_manager') and hasattr(self, 'pending_ban_checks'):
                    pending_users = self.pending_ban_checks.get(wchannel, {})
                    if pending_users:
                        nick_l = (wnickname or "").lower()
                        data = pending_users.get(nick_l)
                        if data:
                            # actualizează datele din WHO
                            data["ident"] = wident or data.get("ident") or "*"
                            data["host"] = whost or data.get("host") or "*"
                            if wrealname:
                                data["realname"] = wrealname

                            has_op = False
                            try:
                                has_op = self._has_channel_op(wchannel)
                            except Exception:
                                has_op = False

                            if has_op:
                                try:
                                    self.ban_expiration_manager.check_user_against_bans(
                                        wchannel,
                                        data.get("nick") or wnickname,
                                        data.get("ident") or "*",
                                        data.get("host") or "*",
                                        data.get("realname") or None,
                                    )
                                    pending_users.pop(nick_l, None)
                                    logger.info(f"🔄 Re-checked ban for {wnickname} after WHO completion")
                                except Exception as e:
                                    if hasattr(self, 'logger'):
                                        self.logger.error(f"Re-check ban failed for {wnickname}: {e}")

            except Exception as e:
                if hasattr(self, "logger"):
                    self.logger.error(f"irc_unknown/RPL_WHOREPLY error: {e}", exc_info=True)

    def irc_ERR_NICKNAMEINUSE(self, prefix, params):
        if self.nick_already_in_use == 1 and self.recover_nick_timer_start is False:
            self.setNick(s.altnick)
            self.recover_nick_timer_start = True
            return
        elif self.recover_nick_timer_start:
            return
        else:
            logger.info(f"Nickname {s.nickname} is already in use, switching to alternative nick..")
            self.nick_already_in_use = 1
            self.nickname = s.altnick
            if self.nickname != s.nickname:  # start doar dacă e alt nick
                self._start_worker("recover_nick", target=self._recover_nick_loop, global_singleton=False)

    # check if logged
    def is_logged_in(self, userId, host):
        return userId in self.logged_in_users and host in self.logged_in_users[userId]["hosts"]

    # schedule for rejoining channel
    def _schedule_rejoin(self, channel):
        if channel not in self.rejoin_pending:
            return

        delay = self.rejoin_pending[channel]['delay']
        self._call_later(delay, self._attempt_rejoin, channel)

    # attempt rejoin
    def _attempt_rejoin(self, channel):
        if channel not in self.rejoin_pending:
            return

        entry = self.rejoin_pending[channel]
        entry['attempts'] += 1

        if entry['attempts'] > s.maxAttemptRejoin:
            logger.info(f"❌ Failed to rejoin {channel} after {s.maxAttemptRejoin} attempts. Suspending the channel.")
            entry = self.rejoin_pending.get(channel, {})
            reason = entry.get('reason', 'unknown')
            self.sql.sqlite_auto_suspend_channel(channel, reason)
            self.notOnChannels.append(channel)
            del self.rejoin_pending[channel]
            return
        logger.info(f"🔄 Rejoin attempt {entry['attempts']} for {channel}...")
        self.join_channel(channel)

    def load_commands(self):

        self.commands = []
        for cmd in command_definitions:
            func = getattr(commands, f"cmd_{cmd['name']}", None)
            if callable(func):
                self.commands.append({
                    'name': cmd['name'],
                    'description': cmd['description'],
                    'flags': cmd['flags'],
                    'proc': func,
                    'id': cmd['id']
                })
            else:
                logger.info(f"[WARN] Function cmd_{cmd['name']} not found in commands.py")

    def parse_prefix(self, prefix):
        if "!" in prefix and "@" in prefix:
            nick, rest = prefix.split("!", 1)
            ident, host = rest.split("@", 1)
            return nick, ident, host, f"{nick}!{ident}@{host}"
        return None, None, None, prefix

    def set_mode(self, channel, se, mode, user):
        sign = "+" if se else "-"
        self.sendLine(f"MODE {channel} {sign}{mode} {user}")

    def user_update_status(self, channel, nick, status_char, set_mode):
        """
        Actualizează permisiunile interne (ex: @, +) când se primește un MODE.
        Folosește comparare case-insensitive pentru a evita desincronizarea.
        """
        target_nick = nick.lower()
        target_chan = channel.lower()

        for user_details in self.channel_details:
            # user_details structura: [channel, nick, ident, host, privs, ...]
            if len(user_details) < 2:
                continue

            # Comparăm totul cu litere mici
            if str(user_details[0]).lower() == target_chan and str(user_details[1]).lower() == target_nick:
                current = user_details[4] or ""

                if set_mode:
                    # Adăugăm permisiunea dacă nu există deja
                    if status_char not in current:
                        user_details[4] = ''.join(sorted(current + status_char))
                        # Logare debug doar pentru OP (ca să vedem că a mers)
                        if status_char == '@' or status_char == '~':
                            logger.info(f"👑 Privileges updated for {nick} on {channel}: +{status_char}")
                else:
                    # Scoatem permisiunea
                    user_details[4] = current.replace(status_char, '')
                    if status_char == '@' or status_char == '~':
                        logger.info(f"🔽 Privileges updated for {nick} on {channel}: -{status_char}")

                return True
        return False

    def user_on_channel(self, channel, nick):
        for user_details in self.channel_details:
            if user_details[0] == channel and user_details[1] == nick:
                return True
        return False

    def _handle_user_mode(self, channel, feedback, nick, host, msg, mode, enable, flag):
        result = self.check_command_access(channel, nick, host, flag, feedback)
        if not result:
            return

        args = msg.split() if msg.strip() else [nick]

        if not self.user_is_op(self.nickname, channel):
            self.send_message(feedback,
                              "⚠️ I need operator privileges (+o) on this channel to set modes. Please ensure I am opped via ChanServ.")
            return
        mode_to_symbol = {
            'q': '~',
            'o': '@',
            'v': '+',
            'h': '%',
            'a': '&',
        }
        symbol = mode_to_symbol.get(mode)
        if not symbol:
            self.send_message(feedback, f"❌ Invalid mode: +{mode}")
            return

        for arg in args:
            current_modes = self._get_user_modes(channel, arg)

            if enable and symbol in current_modes:
                self.send_message(feedback, f"ℹ️ {arg} already has mode +{mode}.")
                continue
            elif not enable and symbol not in current_modes:
                self.send_message(feedback, f"ℹ️ {arg} does not have mode +{mode}.")
                continue

            self.set_mode(channel, enable, mode, arg)

    def _get_user_modes(self, channel, nick):
        for user in self.channel_details:
            if user[0] == channel and user[1] == nick:
                return user[4] if isinstance(user[4], str) else ""
        return ""

    def noticed(self, user, channel, message):
        """
        Handle NOTICE messages from services (NickServ, Q, X)
        Detects successful/failed authentication
        """
        nick = user.split("!")[0] if user else ""
        message_lower = message.lower()

        # Only process if waiting for auth
        if not getattr(self, "nickserv_waiting", False):
            return

        auth_method = getattr(self, "auth_method", "nickserv")

        # QuakeNet Q service
        if auth_method == 'q' and nick.upper() == 'Q':
            # Success patterns for Q
            success_patterns = [
                "you are now logged in as",
                "you are now authed as",
                "authentication successful"
            ]

            # Failure patterns for Q
            failure_patterns = [
                "username or password incorrect",
                "incorrect password",
                "access denied",
                "authentication failed"
            ]

            # Already logged patterns
            already_patterns = [
                "you are already logged in",
                "you are already authed"
            ]

            if any(pattern in message_lower for pattern in success_patterns):
                logger.info(f"✅ QuakeNet Q authentication successful!")
                logger.info(f"   Logged in as: {getattr(self, 'auth_service_username', 'unknown')}")
                self.nickserv_waiting = False
                self.authenticated = True
                if self.auth_timeout_call and self.auth_timeout_call.active():
                    self.auth_timeout_call.cancel()
                self._join_channels()

            elif any(pattern in message_lower for pattern in already_patterns):
                logger.info(f"✅ Already authenticated with QuakeNet Q")
                self.nickserv_waiting = False
                self.authenticated = True
                if self.auth_timeout_call and self.auth_timeout_call.active():
                    self.auth_timeout_call.cancel()
                self._join_channels()

            elif any(pattern in message_lower for pattern in failure_patterns):
                logger.error(f"❌ QuakeNet Q authentication FAILED!")
                logger.error(f"   Message: {message}")
                self.nickserv_waiting = False
                self.authenticated = False
                if self.auth_timeout_call and self.auth_timeout_call.active():
                    self.auth_timeout_call.cancel()
                self._join_channels()

        # Undernet X service
        elif auth_method == 'x' and nick.upper() == 'X':
            # Success patterns for X
            success_patterns = [
                "authentication successful",
                "you are now logged in",
                "logged you in as"
            ]

            # Failure patterns for X
            failure_patterns = [
                "password incorrect",
                "incorrect password",
                "access denied",
                "authentication failed",
                "invalid username or password"
            ]

            # Already logged patterns
            already_patterns = [
                "you are already logged in",
                "already authenticated"
            ]

            if any(pattern in message_lower for pattern in success_patterns):
                logger.info(f"✅ Undernet X authentication successful!")
                logger.info(f"   Logged in as: {getattr(self, 'auth_service_username', 'unknown')}")
                self.nickserv_waiting = False
                self.authenticated = True
                self._join_channels()

            elif any(pattern in message_lower for pattern in already_patterns):
                logger.info(f"✅ Already authenticated with Undernet X")
                self.nickserv_waiting = False
                self.authenticated = True
                self._join_channels()

            elif any(pattern in message_lower for pattern in failure_patterns):
                logger.error(f"❌ Undernet X authentication FAILED!")
                logger.error(f"   Message: {message}")
                self.nickserv_waiting = False
                self.authenticated = False
                logger.info("➡️ Falling back to main channel only.")
                for chan in s.channels:
                    self.join(chan)
                    self.channels.append(chan)

        # Traditional NickServ
        elif nick.lower() == s.nickserv_nick.lower():
            if any(keyword in message_lower for keyword in
                   ["you are now identified", "has been successfully identified"]):
                logger.info("✅ NickServ identification successful (via NOTICE).")
                self.nickserv_waiting = False
                self.authenticated = True
                self._join_channels()

            elif any(keyword in message_lower for keyword in
                     ["password incorrect", "authentication failed", "is not a registered"]):
                logger.error("❌ NickServ identification failed (via NOTICE).")
                self.nickserv_waiting = False
                self.authenticated = False
                logger.info("➡️ Falling back to main channel only.")
                for chan in s.channels:
                    self.join(chan)
                    self.channels.append(chan)


    def privmsg(self, user, channel, msg):
        nick = user.split("!")[0]
        host = user.split("!")[1]
        ident = user.split("!", 1)[1].split("@", 1)[0]
        args = msg.split()
        command = ""
        feedback = channel
        target_channel = channel
        is_private = (channel.lower() == self.nickname.lower())
        is_nick_cmd = False
        is_other_chan = False
        hostmask = f"{ident}@{host}" if ident and host else host
        lhost = self.get_hostname(nick, f"{hostmask}", 0)

        if not args:
            return

        # if is private message
        if is_private:
            if self.check_private_flood_prot(lhost):
                return
            feedback = nick
            if args[0].startswith(s.char):
                command = args[0][1:]
                if len(args) > 1 and args[1].startswith("#"):
                    target_channel = args[1]
                    is_other_chan = True
            else:
                command = args[0]
                if len(args) > 1 and args[1].startswith("#"):
                    target_channel = args[1]
                    is_other_chan = True

        # if is public command
        else:
            # Plugin hooks (PRIVMSG)
            pm = getattr(self, "plugin_manager", None)
            if pm:
                try:
                    pm.dispatch_hook(
                        "PRIVMSG",
                        channel=channel,
                        feedback=feedback,
                        nick=nick,
                        ident=ident,
                        host=host,
                        message=msg,
                        user=user,
                        is_private=is_private,
                        target_channel=target_channel,
                    )
                except Exception:
                    logger.error("PRIVMSG hook dispatch error", exc_info=True)
            if args[0].startswith(s.char):
                command = args[0][1:]
                if len(args) > 1 and args[1].startswith("#"):
                    target_channel = args[1]
                    is_other_chan = True
            elif args[0].lower() == self.nickname.lower():
                is_nick_cmd = True
                if len(args) > 1:
                    command = args[1]
                    if len(args) > 2 and args[2].startswith("#"):
                        target_channel = args[2]
                        is_other_chan = True
                else:
                    return

        if self.check_private_flood_prot(lhost):
            return

        # ✅ CHECK PLUGIN COMMANDS FIRST
        if hasattr(self, 'plugin_manager'):
            from core.plugin_manager import handle_plugin_command

            # Calculate message pentru plugin
            if is_nick_cmd:
                joined_args = ' '.join(args[3:] if is_other_chan else args[2:])
            elif is_other_chan or is_private:
                joined_args = ' '.join(args[2:] if is_other_chan else args[1:])
            else:
                joined_args = ' '.join(args[1:])

            # Try plugin commands first
            if handle_plugin_command(self, target_channel, feedback, nick, host, command, joined_args):
                return  # Command handled by plugin

        if self.valid_command(command):
            proc = self.get_process(command)
            if is_nick_cmd:
                joined_args = ' '.join(args[3:] if is_other_chan else args[2:])
            elif is_other_chan or is_private:
                joined_args = ' '.join(args[2:] if is_other_chan else args[1:])
            else:
                joined_args = ' '.join(args[1:])

            try:
                if hasattr(proc, '__self__') and proc.__self__ is not None:
                    proc(target_channel, feedback, nick, host, joined_args)
                else:
                    proc(self, target_channel, feedback, nick, host, joined_args)
            except Exception as e:
                self.send_message(feedback, f"🛠️ Execution error → {e}")

    def send_message(self, channel, message):
        try:
            if hasattr(self, "dcc") and channel and not channel.startswith("#"):
                sess = self.dcc.sessions.get(channel.lower())
                if sess and sess.transport:
                    for part in self.split_irc_message_strings(message):
                        sess.transport.send_text(part)
                    return
        except Exception:
            pass

        # fallback normal: trimite pe IRC
        for part in self.split_irc_message_strings(message):
            if part.strip():
                self.message_queue.put((channel, part))

    def send_notice(self, channel, message):
        for line in message.split("\n"):
            if line.strip():
                self.sendLine(f"NOTICE {channel} :{line}".encode('utf-8'))

    def get_time(self):
        return int(reactor.seconds())

    def format_duration(self, seconds):
        return str(datetime.timedelta(seconds=int(seconds)))

    # split message in parts (for lists, with separator)
    def split_irc_message_parts(self, entries, separator=", ", max_length=450):
        messages = []
        current_msg = ""

        for entry in entries:
            if len(current_msg) + len(entry) + len(separator) > max_length:
                messages.append(current_msg.strip(separator))
                current_msg = ""
            current_msg += entry + separator

        if current_msg:
            messages.append(current_msg.strip(separator))

        return messages

    # split message in strings
    def split_irc_message_strings(self, message, limit=s.message_max_chars):
        lines = []
        for paragraph in message.split("\n"):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            while len(paragraph) > limit:
                split_at = paragraph.rfind(' ', 0, limit)
                if split_at == -1:
                    split_at = limit
                lines.append(paragraph[:split_at])
                paragraph = paragraph[split_at:].lstrip()
            if paragraph:
                lines.append(paragraph)
        return lines

    def ctcpQuery_VERSION(self, user, message, a):
        user = user.split('!')[0]
        from core.update import read_local_version
        self.notice(user, "\x01VERSION " + f" I am running BlackBoT v{read_local_version()} — Powered by Python 🐍")

    # thread to recover main nick when available
    def recover_nickname(self):
        stop_ev = get_event("recover_nick")
        while not stop_ev.is_set():
            if self.nickname == s.nickname:
                break
            if self.nick_already_in_use == 1:
                self.setNick(s.nickname)
            time.sleep(5)

    def _recover_nick_loop(self, stop_event, beat):
        import time
        from twisted.internet import reactor

        RETRY_INTERVAL = 30
        logger.info("🔄 RecoverNick worker started.")

        while not stop_event.is_set():
            desired_nick = getattr(self.factory, 'nickname', None)

            if not desired_nick or self.nickname == desired_nick:
                logger.info(f"✅ Nick recovery complete. Current: {self.nickname}")
                reactor.callFromThread(self._stop_worker, "recover_nick")
                return  # Ieșim din thread, iar main thread va face curățenia finală

            logger.debug(f"🔄 RecoverNick: Attempting swap {self.nickname} -> {desired_nick}")
            reactor.callFromThread(self.setNick, desired_nick)

            for _ in range(RETRY_INTERVAL):
                if stop_event.is_set(): return
                beat()

                if self.nickname == desired_nick:
                    # La fel și aici, dacă reușim în timpul pauzei
                    logger.info(f"✅ Nick recovered during wait. Stopping worker.")
                    reactor.callFromThread(self._stop_worker, "recover_nick")
                    return

                time.sleep(1.0)

    def check_access(self, channel, userId, flags):
        sql_instance = self.sql
        if sql_instance.sqlite_has_access_flags(self.botId, userId, flags):
            return True
        if sql_instance.sqlite_has_access_flags(self.botId, userId, flags, channel):
            return True
        return False

    def restart(self, reason="Restarting...", from_autoupdate=False):
        """Restart the bot gracefully"""
        import os
        import sys
        import subprocess
        from pathlib import Path
        from twisted.internet import reactor

        logger.info(f"🔄 RESTART command received: {reason}")

        try:
            instance_name = os.getenv('BLACKBOT_INSTANCE_NAME', self.nickname)
            env_file = Path(f"instances/{instance_name}/.env")
            if env_file.exists():
                logger.debug(f"🔄 Syncing config for instance: {instance_name}")
                import core.config_updater as config_updater
                config_updater.update_env_file(env_file, instance_name)
        except Exception as e_cfg:
            logger.debug(f"Config sync skipped: {e_cfg}")

        try:
            # Send QUIT
            if self.connected:
                try:
                    self.sendLine(f"QUIT :{reason}".encode('utf-8'))
                    logger.info("📤 Sent QUIT to IRC server")
                    time.sleep(0.5)
                except:
                    pass

            # Cleanup workers (dar NU DCC - îl păstrăm pentru seamless restart)
            self._stop_heartbeat_loop()

            if hasattr(self, 'ban_expiration_manager'):
                self.ban_expiration_manager.stop()

            # Oprește doar workers per-connection, NU cei globali
            per_connection_workers = ['message_sender', 'logged_users']
            for name in per_connection_workers:
                if hasattr(self, '_workers') and name in self._workers:
                    try:
                        self._stop_worker(name, join_timeout=1.0)
                    except:
                        pass

            self._cancel_all_timers()

            # Salvează uptime
            if hasattr(self, 'sql') and hasattr(self, 'botId'):
                try:
                    self.sql.sqlite_update_uptime(self, self.botId, None, None)
                except:
                    pass

            logger.info("✅ Cleanup complete, starting new process...")

        except Exception as e:
            logger.error(f"Error during restart cleanup: {e}")

        # Pornește procesul nou
        reactor.callLater(2.0, self._restart_process, from_autoupdate)

    def _migrate_env_on_startup(self):
        """
        Migrează .env la pornirea bot-ului (one-time check)
        Adaugă Q/X/user_modes dacă lipsesc
        """
        from pathlib import Path

        try:
            base_dir = Path(__file__).parent.resolve()
            env_file = base_dir / ".env"

            if not env_file.exists():
                logger.debug("No .env file found - skipping migration")
                return

            # Citește conținut
            env_content = env_file.read_text(encoding='utf-8')

            # Verifică dacă are nevoie de migrare
            needs_migration = (
                    ("BLACKBOT_QUAKENET_AUTH_ENABLED" not in env_content) or
                    ("BLACKBOT_UNDERNET_AUTH_ENABLED" not in env_content) or
                    ("BLACKBOT_USER_MODES" not in env_content)
            )

            if not needs_migration:
                logger.debug("✅ .env already up-to-date")
                return

            logger.info("🔄 Migrating .env with new authentication settings...")

            lines = env_content.splitlines()
            insert_index = None

            # Caută un punct bun după NickServ
            for i, line in enumerate(lines):
                if "BLACKBOT_NICKSERV_PASSWORD" in line:
                    insert_index = i + 1
                    break
            if insert_index is None:
                for i, line in enumerate(lines):
                    if "BLACKBOT_NICKSERV_ENABLED" in line or "BLACKBOT_NICKSERV_LOGIN_ENABLED" in line:
                        insert_index = i + 1
                        break
            if insert_index is None:
                insert_index = len(lines)

            # Construiește DOAR ce lipsește
            new_auth_section_lines = []

            # QuakeNet Q (dacă lipsește)
            if "BLACKBOT_QUAKENET_AUTH_ENABLED" not in env_content:
                new_auth_section_lines.extend([
                    "",
                    "# QuakeNet Q Authentication",
                    "BLACKBOT_QUAKENET_AUTH_ENABLED=false",
                    "BLACKBOT_QUAKENET_USERNAME=",
                    "BLACKBOT_QUAKENET_PASSWORD=",
                ])

            # Undernet X (dacă lipsește)
            if "BLACKBOT_UNDERNET_AUTH_ENABLED" not in env_content:
                new_auth_section_lines.extend([
                    "",
                    "# Undernet X Authentication",
                    "BLACKBOT_UNDERNET_AUTH_ENABLED=false",
                    "BLACKBOT_UNDERNET_USERNAME=",
                    "BLACKBOT_UNDERNET_PASSWORD=",
                ])

            # User Modes (dacă lipsește)
            if "BLACKBOT_USER_MODES" not in env_content:
                new_auth_section_lines.extend([
                    "",
                    "# User Modes (set after connection)",
                    "BLACKBOT_USER_MODES=",
                ])

            # Adaugă o linie goală la final
            if new_auth_section_lines:
                new_auth_section_lines.append("")

            # Inserează ca linii separate
            lines[insert_index:insert_index] = new_auth_section_lines

            # Backup
            backup_file = env_file.with_suffix(env_file.suffix + ".pre-migration")
            if not backup_file.exists():
                backup_file.write_text(env_content, encoding="utf-8")
                logger.info(f"💾 Backup saved: {backup_file.name}")

            # Scrie înapoi
            new_content = "\n".join(lines).rstrip() + "\n"
            env_file.write_text(new_content, encoding="utf-8")

            logger.info("✅ .env migrated successfully")
            logger.info("   New fields: Q/X auth + user_modes")

        except Exception as e:
            logger.warning(f"⚠️  .env migration failed: {e}")
            logger.info("Bot will continue normally - migration can be done manually")

    def _restart_process(self, from_autoupdate: bool = False):
        """
        Actually restart the process

        Args:
            from_autoupdate: True dacă restart vine din auto-update
                             (verifică și instalează requirements)
        """
        import os
        import sys
        import subprocess
        import time
        import logging
        from pathlib import Path

        instance = os.getenv("BLACKBOT_INSTANCE_NAME", "main")
        base_dir = Path(__file__).resolve().parent
        script = base_dir / "BlackBoT.py"
        env = os.environ.copy()

        logger.info("🔄 Restarting BlackBoT process...")

        try:
            # ✅ AUTO-UPDATE: Verifică requirements DOAR dacă e din auto-update
            if from_autoupdate:
                logger.info("🔄 Performing auto-update from GitHub...")

                # Git pull
                try:
                    git_result = subprocess.run(
                        ["git", "pull"],
                        cwd=base_dir,
                        capture_output=True,
                        text=True,
                        timeout=30
                    )

                    if git_result.returncode == 0:
                        if "Already up to date" in git_result.stdout:
                            logger.info("✅ Already up to date")
                        else:
                            logger.info("✅ Auto-update successful")
                            logger.debug(f"Git output: {git_result.stdout}")
                    else:
                        logger.warning(f"⚠️  Git pull warning: {git_result.stderr}")

                except subprocess.TimeoutExpired:
                    logger.error("❌ Git pull timeout (30s)")
                except FileNotFoundError:
                    logger.error("❌ Git not found - cannot auto-update")
                except Exception as e:
                    logger.error(f"❌ Auto-update failed: {e}", exc_info=True)

                # Best-effort dependency check DOAR pentru auto-update
                logger.info("🔄 Checking dependencies after auto-update...")
                try:
                    # Import ensure_packages cu fallback
                    try:
                        from core.deps import ensure_packages
                    except ImportError:
                        sys.path.insert(0, str(base_dir))
                        from deps import ensure_packages  # type: ignore

                    requirements_file = base_dir / "requirements.txt"
                    if requirements_file.exists():
                        ok, details = ensure_packages(requirements_file=requirements_file)

                        total = details.get('total', 0)
                        installed = details.get('newly_installed', 0)
                        failed = details.get('failed', 0)

                        if ok:
                            if installed > 0:
                                logger.info(f"✅ Installed {installed}/{total} new packages")
                            else:
                                logger.info(f"✅ All {total} packages already available")
                        else:
                            logger.warning(f"⚠️  Package installation partial: {installed} ok, {failed} failed")
                            logger.info("Continuing with restart - Launcher will verify packages on start")
                    else:
                        logger.warning("⚠️  requirements.txt not found")

                except Exception as e:
                    logger.warning(f"Dependency check skipped: {e}")
                    logger.info("Continuing with restart - Launcher will verify packages on start")
            else:
                # ✅ RESTART MANUAL: Skip dependency check
                logger.info("📝 Manual restart - skipping dependency check")

            # ✅ AUTO-MIGRATE .env (indiferent de tipul de restart)
            try:
                logger.info("🔄 Checking for .env migration...")

                # Prefer .env din instanță dacă există (ca să nu migrezi globalul din greșeală)
                instance_env = base_dir / "instances" / instance / ".env"
                global_env = base_dir / ".env"
                env_file = instance_env if instance_env.exists() else global_env

                if env_file.exists():
                    env_content = env_file.read_text(encoding="utf-8")

                    needs_migration = (
                            ("BLACKBOT_QUAKENET_AUTH_ENABLED" not in env_content) or
                            ("BLACKBOT_UNDERNET_AUTH_ENABLED" not in env_content) or
                            ("BLACKBOT_USER_MODES" not in env_content)
                    )

                    if needs_migration:
                        logger.info(f"📝 Migrating {env_file.name} with new authentication settings...")

                        lines = env_content.splitlines()
                        insert_index = None

                        # Caută un punct bun după NickServ
                        for i, line in enumerate(lines):
                            if "BLACKBOT_NICKSERV_PASSWORD" in line:
                                insert_index = i + 1
                                break
                        if insert_index is None:
                            for i, line in enumerate(lines):
                                if "BLACKBOT_NICKSERV_ENABLED" in line or "BLACKBOT_NICKSERV_LOGIN_ENABLED" in line:
                                    insert_index = i + 1
                                    break
                        if insert_index is None:
                            insert_index = len(lines)

                        # ✅ Construiește DOAR ce lipsește
                        new_auth_section_lines = []

                        # QuakeNet Q (dacă lipsește)
                        if "BLACKBOT_QUAKENET_AUTH_ENABLED" not in env_content:
                            new_auth_section_lines.extend([
                                "",
                                "# QuakeNet Q Authentication",
                                "BLACKBOT_QUAKENET_AUTH_ENABLED=false",
                                "BLACKBOT_QUAKENET_USERNAME=",
                                "BLACKBOT_QUAKENET_PASSWORD=",
                            ])

                        # Undernet X (dacă lipsește)
                        if "BLACKBOT_UNDERNET_AUTH_ENABLED" not in env_content:
                            new_auth_section_lines.extend([
                                "",
                                "# Undernet X Authentication",
                                "BLACKBOT_UNDERNET_AUTH_ENABLED=false",
                                "BLACKBOT_UNDERNET_USERNAME=",
                                "BLACKBOT_UNDERNET_PASSWORD=",
                            ])

                        # User Modes (dacă lipsește)
                        if "BLACKBOT_USER_MODES" not in env_content:
                            new_auth_section_lines.extend([
                                "",
                                "# User Modes (set after connection)",
                                "BLACKBOT_USER_MODES=",
                            ])

                        # Adaugă o linie goală la final
                        if new_auth_section_lines:
                            new_auth_section_lines.append("")

                        # Inserează ca linii separate (nu ca un singur string mare)
                        lines[insert_index:insert_index] = new_auth_section_lines

                        backup_file = env_file.with_suffix(env_file.suffix + ".pre-migration")
                        if not backup_file.exists():
                            backup_file.write_text(env_content, encoding="utf-8")
                            logger.info(f"💾 Backup saved: {backup_file.name}")

                        new_content = "\n".join(lines).rstrip() + "\n"
                        env_file.write_text(new_content, encoding="utf-8")
                        logger.info("✅ .env migrated successfully")
                    else:
                        logger.info("✅ .env already up-to-date")

            except Exception as e:
                logger.warning(f"⚠️  .env migration failed: {e}", exc_info=True)

            logger.info("🚀 Executing restart...")

        except Exception as e:
            logger.error(f"🔥 Restart pre-flight crashed: {e}", exc_info=True)

        logger.info(f"🚀 Launching new process: {script}")

        if os.name == "nt":
            # Windows
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            CREATE_NO_WINDOW = 0x08000000

            creationflags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW

            subprocess.Popen(
                [sys.executable, str(script)],
                cwd=str(base_dir),
                env=env,
                creationflags=creationflags
            )
        else:
            # Linux/Unix
            subprocess.Popen(
                [sys.executable, str(script)],
                cwd=str(base_dir),
                env=env,
                start_new_session=True  # Detach from parent
            )

        logger.info("✅ New process launched, exiting old process...")

        # --- FIX: PAUZĂ PENTRU DRAIN DCC ---
        for handler in logging.getLogger().handlers:
            try:
                handler.flush()
            except Exception:
                pass

        if hasattr(self, 'dcc') and self.dcc:
            msg = "\n🚀 Handover complete. Old process dying in 2s...\r\n"
            for nick, session in list(getattr(self.dcc, "sessions", {}).items()):
                if session and getattr(session, "transport", None):
                    try:
                        session.transport.write(msg.encode('utf-8'))
                    except Exception:
                        pass

        time.sleep(2.0)
        # -----------------------------------

        os._exit(0)

    # die process
    def die(self, reason="Killed by !die"):
        """Kill the bot gracefully"""
        import sys
        import time
        import logging
        from pathlib import Path
        from twisted.internet import reactor

        logger.info(f"💀 DIE command received: {reason}")

        # Graceful shutdown CU închidere SQL
        self.graceful_shutdown(reason)

        # Șterge PID file
        try:
            instance = os.getenv("BLACKBOT_INSTANCE_NAME", "main")
            base_dir = Path(__file__).resolve().parent
            pid_path = base_dir / "instances" / instance / f"{instance}.pid"

            if pid_path.exists():
                pid_path.unlink()
                logger.info(f"🗑️  Removed PID file: {pid_path}")
        except Exception as e:
            logger.warning(f"Could not remove PID file: {e}")

        # MODIFICARE: NU mai folosi callLater - oprește INSTANT
        logger.info("👋 Goodbye!")

        # Flush logs
        for handler in logging.getLogger().handlers:
            try:
                handler.flush()
            except:
                pass

        time.sleep(2.0)

        if reactor.running:
            reactor.callFromThread(reactor.stop)


    def get_process(self, command):
        filtered_dicts = [my_dict for my_dict in self.commands if command in my_dict.values()]
        line = filtered_dicts[0]
        return line.get('proc')

    def get_flags(self, id):
        filtered_dicts = [my_dict for my_dict in self.commands if id in my_dict.values()]
        line = filtered_dicts[0]
        return line.get('flags')

    def valid_command(self, command):
        found = False
        for line in self.commands:
            if command in line.values():
                found = True
                break
        return found

    def get_hostname(self, nick, host, host_type):
        try:
            raw = str(host or "").strip()
            rest = raw.split("!", 1)[1] if "!" in raw else raw  # "ident@host" dacă era "nick!ident@host"

            # --- helpers pentru upgrade ---
            def _upgrade_from_dcc() -> str | None:
                try:
                    dcc = getattr(self, "dcc", None)
                    if not dcc:
                        return None
                    sess = dcc.sessions.get(nick.lower())
                    if not sess:
                        return None
                    hm = sess.meta.get("hostmask") or sess.meta.get("irc_host")
                    if not hm:
                        return None
                    val = hm.split("!", 1)[1] if "!" in hm else hm
                    return val if "@" in val and not val.endswith("@*") else None
                except Exception:
                    return None

            def _upgrade_from_login() -> str | None:
                try:
                    for _, info in (getattr(self, "logged_in_users", {}) or {}).items():
                        if (info.get("nick") or "").lower() == (nick or "").lower():
                            hosts = info.get("hosts") or []
                            if not hosts:
                                continue
                            raw = str(hosts[0])
                            val = raw.split("!", 1)[1] if "!" in raw else raw
                            return val if "@" in val and not val.endswith("@*") else None
                except Exception:
                    return None

            needs_upgrade = ("@" not in rest) or rest.endswith("@*") or rest == "*@*"
            if needs_upgrade:
                upgraded = _upgrade_from_dcc() or _upgrade_from_login()
                if upgraded:
                    rest = upgraded

            # dacă tot e invalid, fabricăm minimul sigur
            if "@" not in rest:
                ident, host_only = "*", "*"
            else:
                ident, host_only = rest.split("@", 1)

            formats = {
                1: f"*!*@{host_only}",
                2: f"*!{ident}@{host_only}",
                3: f"{nick}!{ident}@{host_only}",
                4: f"{nick}!*@*",
                5: f"*!{ident}@*",
            }
            if host_type > 0 and host_type in formats:
                return formats[host_type]
            else:
                default_type = getattr(s, "default_hostname", 1)
                return formats.get(default_type, f"*!{ident}@{host_only}")

        except Exception:
            return "*!*@*"

    # channel privileges
    def user_is_voice(self, nick, chan):
        for arr in self.channel_details:
            if arr[0] == chan and arr[1] == nick and '+' in arr[4]:
                return True
        return False

    def user_is_op(self, nick, chan):
        chan_lower = chan.lower()
        nick_lower = nick.lower()

        for arr in self.channel_details:
            if (arr[0].lower() == chan_lower and
                    arr[1].lower() == nick_lower and
                    '@' in (arr[4] or "")):
                return True
        return False

    def user_is_halfop(self, nick, chan):
        chan_lower = chan.lower()
        nick_lower = nick.lower()

        for arr in self.channel_details:
            if (arr[0].lower() == chan_lower and
                    arr[1].lower() == nick_lower and
                    '%' in (arr[4] or "")):
                return True
        return False

    def user_is_admin(self, nick, chan):
        chan_lower = chan.lower()
        nick_lower = nick.lower()

        for arr in self.channel_details:
            if (arr[0].lower() == chan_lower and
                    arr[1].lower() == nick_lower and
                    '&' in (arr[4] or "")):
                return True
        return False

    def user_is_owner(self, nick, chan):
        chan_lower = chan.lower()
        nick_lower = nick.lower()

        for arr in self.channel_details:
            if (arr[0].lower() == chan_lower and
                    arr[1].lower() == nick_lower and
                    '~' in (arr[4] or "")):
                return True
        return False

    def user_has_no_status(self, nick, chan):
        for arr in self.channel_details:
            if arr[0] == chan and arr[1] == nick and not arr[4]:
                return True
        return False

    def get_logged_in_user_by_host(self, host):
        for userId, data in self.logged_in_users.items():
            if isinstance(data, dict) and host in data["hosts"]:
                return userId
        return None

    def cleanup_known_users(self):
        stop_ev = get_event("known_users")
        while not stop_ev.is_set():
            time.sleep(1800)

            active_pairs = {(ch, nick) for ch, nick, *_ in self.channel_details}
            before = len(self.known_users)
            self.known_users = {
                (ch, nick) for (ch, nick) in self.known_users if (ch, nick) in active_pairs
            }
            after = len(self.known_users)
            removed = before - after
            if removed > 0:
                logger.info(f"🧹 Cleaned known_users: {removed} offline users removed, {after} still active")

    def _sql_keepalive_loop(self):
        """
        SQL connection keepalive loop
        Face ping periodic la database pentru a preveni connection timeout
        Rulează la fiecare 5 minute
        """
        from core.threading_utils import get_event
        import time

        stop_ev = get_event("sql_keepalive")
        interval = 300  # 5 minute

        logger.info("🔄 SQL keepalive started (ping every 5 minutes)")

        while not stop_ev.is_set():
            try:
                # Ping simplu la database
                self.sql.sqlite3_execute("SELECT 1")
                logger.debug("✅ SQL keepalive ping OK")

            except Exception as e:
                logger.error(f"❌ SQL keepalive ping failed: {e}")

                # Încearcă să reseteze connection pool
                try:
                    logger.warning("🔄 Resetting SQL connection pool...")
                    self.sql.close_connection()
                    logger.info("✅ SQL connection pool reset - will reconnect on next query")
                except Exception as reset_error:
                    logger.error(f"Failed to reset connection pool: {reset_error}")

            # Sleep cu verificare pentru graceful shutdown
            for _ in range(interval):
                if stop_ev.is_set():
                    break
                time.sleep(1)

        logger.info("SQL keepalive stopped")

    def rehash(self, channel, feedback, nick, host):
        import importlib
        import sys
        import gc
        import traceback

        if not self.check_command_access(channel, nick, host, '8', feedback):
            return

        def _short_tb():
            tb = traceback.format_exc(limit=3)
            short = " | ".join((tb or "").strip().splitlines()[-3:])
            return (short[:400]) if short else "unknown error"

        try:
            self.send_message(feedback, "🔁 Reloading core modules...")
            old_modules = {}
            modules_to_reload = [
                'core.commands',
                'core.update',
                'core.SQL',
                'core.Variables',
                'core.environment_config',
                'core.dcc',
                'core.log',
                'core.nettools',
                'core.monitor_client',
                'core.optimized_cache',
                'core.ban_expiration_manager',
                'core.dcc_log_handler',
                'core.nettools',
                'core.threading_utils',
            ]
            for mod_name in modules_to_reload:
                if mod_name in sys.modules:
                    old_modules[mod_name] = sys.modules[mod_name]

            if hasattr(self, 'commands'):
                self.commands.clear()

            if hasattr(self, '_command_cache'):
                self._command_cache.clear()

            # Forțează GC înainte de reload
            gc.collect()

            failed = []
            reloaded = []

            for mod_name in modules_to_reload:
                try:
                    old_mod = old_modules.get(mod_name)
                    if old_mod:
                        for attr_name in list(vars(old_mod).keys()):
                            if not attr_name.startswith('__'):
                                try:
                                    delattr(old_mod, attr_name)
                                except:
                                    pass
                    if mod_name in sys.modules:
                        importlib.reload(sys.modules[mod_name])
                    else:
                        importlib.import_module(mod_name)

                    reloaded.append(mod_name)

                except Exception:
                    self.send_message(feedback, f"⚠️ Module not loaded: {mod_name} → {_short_tb()}")
                    failed.append(mod_name)
            for _ in range(3):
                collected = gc.collect()
            try:
                self.load_commands()
            except Exception:
                self.send_message(feedback, f"⚠️ load_commands failed → {_short_tb()}")
            try:
                self.sql.selfbot = self
            except Exception:
                pass

            # =================================================================
            # FIX DUPLICATE CHANNEL_DETAILS
            # =================================================================
            self.send_message(feedback, "🧹 Flushing user memory & Resyncing...")

            # 1. Ștergem listele vechi ca să nu se adauge peste ele
            self.channel_details = []
            self.known_users = set()
            self.channel_info = {}
            # (Nu ștergem logged_in_users ca să nu delogăm userii)

            # 2. Cerem serverului lista proaspătă (WHO)
            # Asta va repopula channel_details prin funcția irc_unknown (RPL_WHOREPLY)
            if hasattr(self, 'channels'):
                for chan in self.channels:
                    self.sendLine(f"WHO {chan}")
                    self.sendLine(f"MODE {chan}")
            # =================================================================

            if failed:
                self.send_message(feedback, f"ℹ️ Reloaded: {len(reloaded)} ok, {len(failed)} failed.")
            self.send_message(feedback, f"✅ Reloaded successfully. 🧹 Memory cleanup completed.")

        except Exception as e:
            self.send_message(feedback, f"❌ Critical Rehash Error: {_short_tb()}")

    def auto_update_check_loop(self):
        import core.update as update
        stop_ev = get_event("auto_update")
        while not stop_ev.is_set():
            try:
                if not s.autoUpdateEnabled:
                    break
                startup_version = self.version
                disk_version = update.read_local_version()
                remote_version = update.fetch_remote_version()
                if disk_version > startup_version:
                    logger.info(
                        f"📄 Another instance updated {startup_version} → {disk_version}. Restarting to load new code...")
                    self.restart("📄 Loading updated code from disk", from_autoupdate=True)
                    break

                if remote_version and remote_version > disk_version:
                    logger.info(f"🔄 Update found → {remote_version} > {disk_version}")
                    update.update_from_github(self, "Auto-update")
                    self.restart("🔄 Auto-updated", from_autoupdate=True)
                    break
            except Exception as e:
                logger.error(f"⚠️ Auto-update thread error: {e}")
            time.sleep(s.autoUpdateInterval * 60)

    def _has_channel_op(self, channel: str) -> bool:
        try:
            mynick = self.nickname.lower()
            channel_lower = channel.lower()

            for row in self.channel_details:
                if not isinstance(row, (list, tuple)) or len(row) < 5:
                    continue

                if (str(row[0]).lower() == channel_lower and
                        str(row[1]).lower() == mynick):

                    priv = str(row[4] or "")
                    if any(p in priv for p in ("@", "~", "&")):
                        return True
                    else:
                        # Bot găsit dar fără privilege - continuă la fallback
                        break
        except Exception:
            pass

        # Fallback: verifică channel_op_state
        return bool(self.channel_op_state.get(channel, False))

    def _prune_pending_ban_checks(self):
        now = time.time()
        total = 0
        for chan, users in list(self.pending_ban_checks.items()):
            for nick_l, data in list(users.items()):
                if now - data.get("ts", now) > self.pending_ban_max_age:
                    del users[nick_l]
            if not users:
                self.pending_ban_checks.pop(chan, None)
            else:
                total += len(users)

        # dacă tot e peste limita globală, tăiem din cei mai vechi
        if total > self.pending_ban_global_max:
            # construim o listă (chan, nick_l, ts) și o sortăm
            entries = []
            for chan, users in self.pending_ban_checks.items():
                for nick_l, data in users.items():
                    entries.append((chan, nick_l, data.get("ts", 0)))
            entries.sort(key=lambda x: x[2])  # cei mai vechi primii

            to_remove = total - self.pending_ban_global_max
            for chan, nick_l, _ in entries[:to_remove]:
                ch_dict = self.pending_ban_checks.get(chan, {})
                if nick_l in ch_dict:
                    del ch_dict[nick_l]
                if not ch_dict:
                    self.pending_ban_checks.pop(chan, None)

    def _queue_pending_ban_check(self, channel: str, nick: str,
                                 ident: str | None, host: str | None,
                                 realname: str | None):
        channel = channel or ""
        if not channel.startswith("#"):
            return

        now = time.time()
        self._prune_pending_ban_checks()

        chan_dict = self.pending_ban_checks.setdefault(channel, {})
        if len(chan_dict) >= self.pending_ban_max_per_channel:
            # dacă canalul e plin, ștergem cel mai vechi din el
            oldest_nick = None
            oldest_ts = now
            for n_l, data in chan_dict.items():
                ts = data.get("ts", now)
                if ts <= oldest_ts:
                    oldest_ts = ts
                    oldest_nick = n_l
            if oldest_nick is not None:
                chan_dict.pop(oldest_nick, None)

        nick_l = nick.lower()
        chan_dict[nick_l] = {
            "nick": nick,
            "ident": ident or "*",
            "host": host or "*",
            "realname": realname or "",
            "ts": now,
        }

    def _process_pending_ban_checks_for_channel(self, channel: str, max_per_batch: int = 50):
        if not hasattr(self, "ban_expiration_manager"):
            return

        chan_dict = self.pending_ban_checks.get(channel)
        if not chan_dict:
            return

        import time
        now = time.time()
        to_delete = []
        count = 0

        # sortăm după ts ca să luăm pe cei mai vechi întâi
        for nick_l, data in sorted(chan_dict.items(), key=lambda kv: kv[1].get("ts", 0)):
            # 1. Verificăm expirarea
            if now - data.get("ts", now) > self.pending_ban_max_age:
                to_delete.append(nick_l)
                continue

            if count >= max_per_batch:
                break

            nick = data["nick"]

            if nick == self.nickname:
                to_delete.append(nick_l)
                continue

            ident = data.get("ident") or "*"
            host = data.get("host") or "*"
            realname = data.get("realname") or None

            try:
                self.ban_expiration_manager.check_user_against_bans(
                    channel, nick, ident, host, realname
                )
            except Exception as e:
                if hasattr(self, 'logger'):
                    self.logger.error(f"pending ban check failed for {nick} on {channel}: {e}")

            to_delete.append(nick_l)
            count += 1

        # Curățăm userii procesați
        for nick_l in to_delete:
            chan_dict.pop(nick_l, None)

        if not chan_dict:
            self.pending_ban_checks.pop(channel, None)

    def _on_self_op_mode(self, channel: str, is_set: bool):
        """
        Apelată când botul primește sau pierde +o pe un canal.
        Dacă tocmai a primit +o → procesează coză de useri strânși cât timp n-a avut op.
        """
        channel = channel or ""
        if not channel.startswith("#"):
            return

        self.channel_op_state[channel] = bool(is_set)

        try:
            mynick_lower = self.nickname.lower()
            for row in self.channel_details:
                if (len(row) >= 5 and
                        str(row[0]).lower() == channel.lower() and
                        str(row[1]).lower() == mynick_lower):
                    # row[4] = privileges string
                    current_priv = str(row[4] or "")

                    if is_set:
                        if '@' not in current_priv:
                            row[4] = '@' + current_priv
                    else:
                        if '@' in current_priv:
                            row[4] = current_priv.replace('@', '')
                    break
        except Exception as e:
            logger.error(f"Error updating channel_details for op change: {e}")

        if is_set:
            # ✅ debounce refresh modes/banlist (anti-burst)
            # dacă vine spam de MODE/+o, refresh se face o singură dată după 5s
            self._debounce(
                key=f"op_refresh:{channel}",
                delay=5.0,
                fn=self._refresh_channel_state,
                channel=channel
            )

            self._debounce(
                key=f"op_pending_checks:{channel}",
                delay=1.0,
                fn=self._process_pending_ban_checks_for_channel,
                channel=channel
            )

    def irc_MODE(self, prefix, params):
        try:
            if len(params) < 2: return
            target, modes = params[0], params[1]
            args = list(params[2:])
            if not target.startswith("#"): return

            channel = target
            # Inițializare cache canal
            if channel not in self.channel_info:
                self.channel_info[channel] = {"modes": "", "bans": [], "topic": "", "last_updated": time.time()}

            status_map = {'q': '~', 'a': '&', 'o': '@', 'h': '%', 'v': '+'}
            # Moduri care consumă argumente
            always_arg = set(status_map.keys()) | {'b', 'e', 'I', 'k'}

            adding = True
            current_modes = list((self.channel_info[channel].get("modes") or "").replace("+", ""))

            for ch in modes:
                if ch == "+": adding = True; continue
                if ch == "-": adding = False; continue

                param = None
                takes_arg = (ch in always_arg) or (ch == 'l' and adding)

                if takes_arg:
                    if args:
                        param = args.pop(0)
                    else:
                        continue

                # 1. Actualizare Status User (+o, +v, etc)
                if ch in status_map and param:
                    # Actualizăm statusul intern
                    self.user_update_status(channel, param, status_map[ch], adding)

                    # DETECTARE SELF-OP (Când primește botul OP)
                    if ch == "o" and param.lower() == self.nickname.lower():
                        if adding:
                            logger.info(f"[mode] I GOT +o on {channel}")

                            # --- AICI ESTE FIX-UL PENTRU BANURI ---
                            # Imediat ce avem OP, procesăm coada de banuri
                            try:
                                if hasattr(self, '_process_pending_ban_checks_for_channel'):
                                    logger.info(f"🔨 OP received on {channel}. Processing pending bans...")
                                    self._process_pending_ban_checks_for_channel(channel)
                            except Exception as e:
                                logger.error(f"Failed to process pending bans on OP: {e}")
                            # --------------------------------------

                        else:
                            logger.info(f"[mode] I LOST +o on {channel}")

                # 2. Banuri (+b)
                elif ch == "b" and param:
                    bans = self.channel_info[channel]["bans"]
                    pl = param.lower()
                    if adding:
                        if not any((x.get("mask") or "").lower() == pl for x in bans):
                            bans.append({"mask": param, "setter": "unknown", "timestamp": None})
                    else:
                        self.channel_info[channel]["bans"] = [x for x in bans if (x.get("mask") or "").lower() != pl]

                # 3. Moduri canal simple (+nt, etc)
                elif not takes_arg:
                    if adding:
                        if ch not in current_modes: current_modes.append(ch)
                    else:
                        if ch in current_modes: current_modes.remove(ch)

            self.channel_info[channel]["modes"] = "+" + "".join(sorted(current_modes))

        except Exception as e:
            logger.error(f"irc_MODE error: {e}", exc_info=True)

    def channel_has_mode(self, channel, mode_char):
        """
        Verifică dacă un canal are un anumit mod setat

        Args:
            channel: Numele canalului
            mode_char: Caracterul modului (ex: 's', 'p', 'i')

        Returns:
            bool: True dacă modul este setat
        """
        if not hasattr(self, 'channel_info') or channel not in self.channel_info:
            return False

        modes = self.channel_info[channel].get('modes', '')
        return mode_char in modes.replace('+', '')


    def is_secret_channel(self, channel):
        """
        Verifică dacă un canal este secret (+s)
        """
        return self.channel_has_mode(channel, 's')


    def get_channel_modes(self, channel):
        """
        Returnează modurile unui canal

        Returns:
            str: Moduri (ex: '+nts') sau '' dacă nu sunt disponibile
        """
        if not hasattr(self, 'channel_info') or channel not in self.channel_info:
            return ''
        return self.channel_info[channel].get('modes', '')


    def get_channel_bans(self, channel):
        """
        Returnează lista de banuri a unui canal

        Returns:
            list: Listă de dicționare cu {'mask', 'setter', 'timestamp'}
        """
        if not hasattr(self, 'channel_info') or channel not in self.channel_info:
            return []
        return self.channel_info[channel].get('bans', [])




class BotFactory(protocol.ReconnectingClientFactory):
    def __init__(self, nickname, realname):
        self.nickname = nickname
        self.realname = realname
        self.away = s.away
        self.server = None
        self.port = None
        self.initialDelay = 1.0
        self.maxDelay = 60.0
        self.factor = 1.5
        self.jitter = 0.1
        self.shutting_down = False

    def buildProtocol(self, addr):
        global current_instance
        bot = Bot(self.nickname, self.realname)
        bot.factory = self
        bot.server = self.server
        bot.port = self.port
        current_instance = bot
        try:
            self.resetDelay()  # conexiune reușită → reset backoff
        except Exception:
            pass
        return bot

    def rotate_and_connect(self):
        host, port, vhost = server_next_round_robin()
        delay = getattr(s, "reconnectDelaySeconds", 10)
        if self.shutting_down:
            return
        logger.info(
            f"🔁 Reconnecting to {host}:{port} (vhost={vhost}) in {delay}s ..."
        )
        reactor.callLater(delay, self.connect_to, host, port, vhost)

    def connect_to(self, host, port, vhost):
        self.server = vhost
        self.port = int(port)

        # Folosim SOURCE_IP doar ca IP local; port 0 => OS alege random
        bind_addr = None
        source_ip = getattr(s, "sourceIP", "") or ""
        if source_ip:
            bind_addr = (source_ip, 0)  # port 0 = "alege tu, OS"

        if s.ssl_use:
            sslContext = ssl.ClientContextFactory()
            if bind_addr:
                reactor.connectSSL(host, int(port), self, sslContext, bindAddress=bind_addr)
            else:
                reactor.connectSSL(host, int(port), self, sslContext)
        else:
            if bind_addr:
                reactor.connectTCP(host, int(port), self, bindAddress=bind_addr)
            else:
                reactor.connectTCP(host, int(port), self)

    def clientConnectionLost(self, connector, reason):
        if self.shutting_down:
            logger.info("🔌 Connection closed cleanly (Shutdown Mode).")
            return
        logger.info(f"Connection lost: {reason}. Rotating server & reconnecting...")
        v.connected = False
        self.rotate_and_connect()

    def clientConnectionFailed(self, connector, reason):
        if self.shutting_down:
            return
        logger.info(f"Connection failed: {reason}. Rotating server & retrying...")
        v.connected = False
        self.rotate_and_connect()

def server_has_port(server):
    if len(server.split()) > 1:
        if server.split()[1].isdigit():
            return [server.split()[0], server.split()[1]]
        else:
            return [server.split()[0], s.port]
    else:
        return [server.split()[0], s.port]


def host_resolve(host):
    flag = -1
    vserver = host

    # dacă host e deja IP literal
    try:
        ipaddress.IPv4Address(host)
        flag = 0
    except ipaddress.AddressValueError:
        pass
    try:
        ipaddress.IPv6Address(host)
        flag = 1
    except ipaddress.AddressValueError:
        pass

    # vrem să știm dacă avem un SOURCE IPv6 setat
    prefer_ipv6 = False
    try:
        if getattr(s, "sourceIP", ""):
            ipaddress.IPv6Address(s.sourceIP)
            prefer_ipv6 = True
    except ipaddress.AddressValueError:
        pass

    if flag == -1:
        # host este nume (nu IP literal)
        if prefer_ipv6:
            # ÎNTÂI încercăm IPv6
            try:
                results = socket.getaddrinfo(host, None, socket.AF_INET6)
                ipv6_addresses = [r[4][0] for r in results if r[1] == socket.SOCK_STREAM]
                if ipv6_addresses:
                    return [1, ipv6_addresses[0], vserver]
            except socket.gaierror:
                pass

            # dacă nu merge IPv6, încercăm IPv4 ca fallback
            try:
                ip_address = socket.gethostbyname(host)
                return [0, ip_address, vserver]
            except socket.gaierror:
                return -1
        else:
            # comportament „clasic”: IPv4 -> IPv6
            try:
                ip_address = socket.gethostbyname(host)
                return [0, ip_address, vserver]
            except socket.gaierror:
                pass
            try:
                results = socket.getaddrinfo(host, None, socket.AF_INET6)
                ipv6_addresses = [r[4][0] for r in results if r[1] == socket.SOCK_STREAM]
                if ipv6_addresses:
                    return [1, ipv6_addresses[0], vserver]
                return -1
            except socket.gaierror:
                return -1
    else:
        # host e deja IP (v4 sau v6)
        return [flag, host, vserver]


def server_connect(first_server):  # connect to server
    global servers_order

    check_if_port = server_has_port(first_server)
    first_server = check_if_port[0]
    port = int(check_if_port[1])

    irc_server = host_resolve(first_server)
    if irc_server == -1:
        if not s.servers[servers_order]:
            return 0
        return 1
    else:
        # irc_server = [flag, ip, vhost]
        server = irc_server[1]
        vhost = irc_server[2]
    return [server, port, vhost]


def server_choose_to_connect():
    global servers_order
    valid_server = False
    while not valid_server:  # try to connect to one of the servers from the list
        first_server = s.servers[servers_order]
        connect = server_connect(first_server)
        if connect == 0:
            logger.info(f"No more servers to try. Ending bot.")
            server = -1
            break
        elif connect == 1:
            logger.info(f"Invalid server {first_server} from the list, trying another one..")
            servers_order += 1
            if servers_order >= len(s.servers):
                servers_order = 0
        else:
            valid_server = True
            server = connect
    return [server[0], server[1], server[2]]


def server_next_round_robin():
    global servers_order
    if not s.servers:
        raise RuntimeError("No servers configured.")
    servers_order = (servers_order + 1) % len(s.servers)
    return server_choose_to_connect()


class ClientSSLContext(ssl.ClientContextFactory):
    def getContext(self):
        ctx = ssl.ClientContextFactory.getContext(self)
        cert = s.ssl_cert_file
        key = s.ssl_key_file

        if cert and key and os.path.isfile(cert) and os.path.isfile(key):
            try:
                ctx.use_certificate_file(cert)
                ctx.use_privatekey_file(key)
                logger.debug("🔐 Loaded SSL certificate and key for mutual TLS.")
            except Exception as e:
                logger.error(f"❌ Failed to load SSL cert/key: {e}")
                sys.exit(1)
        return ctx


def setup_signal_handlers(bot_instance):
    """Setup signal handlers for graceful shutdown"""
    import signal
    import sys

    def signal_handler(signum, frame):
        signal_name = signal.Signals(signum).name
        logger.info(f"⚠️  Received signal: {signal_name}")

        try:
            # Graceful shutdown
            bot_instance.graceful_shutdown(f"Signal {signal_name}")

            # Stop reactor
            from twisted.internet import reactor
            if reactor.running:
                reactor.stop()

            sys.exit(0)
        except Exception as e:
            logger.error(f"Error in signal handler: {e}")
            sys.exit(1)

    # Register handlers
    signal.signal(signal.SIGINT, signal_handler)  # Ctrl+C
    signal.signal(signal.SIGTERM, signal_handler)  # kill command

    # Windows nu suportă SIGHUP
    if hasattr(signal, 'SIGHUP'):
        signal.signal(signal.SIGHUP, signal_handler)  # Terminal closed

    logger.info("✅ Signal handlers installed (SIGINT, SIGTERM)")

if __name__ == '__main__':
    from pathlib import Path
    import os

    instance = os.getenv("BLACKBOT_INSTANCE_NAME", "main")
    base_dir = Path(__file__).resolve().parent
    try:
        pid_path = base_dir / "instances" / instance / f"{instance}.pid"
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        with open(pid_path, "w", encoding="utf-8", errors="replace") as f:
            f.write(str(os.getpid()))
    except Exception as e:
        logger.warning(f"[PID] Failed to update pid file: {e!r}")

    old_source = s.sourceIP

    if not s.servers:
        logger.error("❌ No servers in list to connect to.")
        exit(1)

    host, port, vhost = server_choose_to_connect()
    factory = BotFactory(s.nickname, s.realname)
    factory.connect_to(host, port, vhost)


    def setup_signals_when_ready():
        if current_instance:
            setup_signal_handlers(current_instance)
        else:
            # Retry dacă bot-ul nu e încă creat
            reactor.callLater(1.0, setup_signals_when_ready)

    reactor.callLater(1.0, setup_signals_when_ready)
    logger.debug(f"🚀 BlackBoT started successfully! Connecting to {host}:{port}")
    reactor.run()