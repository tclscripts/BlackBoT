
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
from pathlib import Path

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
from core import seen
from core.threading_utils import get_event
from core.dcc import DCCManager
from core.optimized_cache import SmartTTLCache
from core.monitor_client import ensure_enrollment, send_heartbeat, send_monitor_offline
from core.log import get_logger, install_excepthook, log_session_banner


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
    "monitor":       {"supervise": True, "provide_signals": True,  "heartbeat_timeout": 60.0},
    "logged_users":  {"supervise": True, "provide_signals": True,  "heartbeat_timeout": 90.0},
    "uptime":        {"supervise": True, "provide_signals": True,  "heartbeat_timeout": 120.0},
    "manual_update": {"supervise": False, "provide_signals": False},
    "botlink": {"supervise": True, "provide_signals": True, "heartbeat_timeout": 90.0},
    "*":             {"supervise": True, "provide_signals": False}
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
        self.version = _load_version()
        self.monitor_enabled = None
        self.hmac_secret = None
        self.commands = None
        self.current_connect_time = 0
        self.current_start_time = 0
        self.recover_nick_timer_start = False
        self.connected = False  # devine True în connectionMade, False în connectionLost
        self.channel_details = []
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
        self.ignore_cleanup_started = False
        self._hb_stop = None
        self.nick_already_in_use = 0
        self.channels = []
        self.multiple_logins = s.multiple_logins
        self.notOnChannels = []
        # rejoin channel list
        self.rejoin_pending = {}
        self.pending_join_requests = {}
        # logged users
        self.logged_in_users = {}
        self.clean_logged_users(silent=True)
        self.thread_check_logged_users_started = False
        self.known_users = set()  # (channel, nick)
        self.user_cache = SmartTTLCache(
            maxlen=2000, ttl=6 * 3600, adaptive_ttl=True,
            background_cleanup=False, enable_stats=True
        )
        self.flood_tracker = defaultdict(lambda: deque())
        self.current_start_time = time.time()
        self.sqlite3_database = s.sqlite3_database
        self.sql = SQLManager.get_instance()
        self.newbot = self.sql.sqlite3_bot_birth(self.username, self.nickname, self.realname,
                                                 self.away)
        self.botId = self.newbot[1]
        self.host_to_nicks = defaultdict(set)
        self._start_worker(
            "uptime",
            target=lambda se, b: self.sql.sqlite_update_uptime(self, self.botId, se, b),
            global_singleton=True
        )
        self._start_worker("known_users", target=self.cleanup_known_users, global_singleton=True)
        if self.sql.sqlite_has_active_ignores(self.botId):
            logger.info("⏳ Active ignores found. Starting cleanup thread...")
            self._start_worker("ignore_cleanup", target=self.cleanup_ignores)
        self.message_queue = queue.Queue()
        self.message_delay = s.message_delay
        self._start_worker("message_sender", target=self._message_worker, global_singleton=False)
        self.thread_check_for_changed_nick = None
        if s.autoUpdateEnabled:
            self._start_worker("auto_update", target=self.auto_update_check_loop, global_singleton=True)

        if self.sql.sqlite_isBossOwner(self.botId) > 0:
            self.unbind_hello = True
        else:
            self.unbind_hello = False
        reactor.addSystemEventTrigger(
            'before', 'shutdown',
            partial(send_monitor_offline, self.monitorId, self.hmac_secret)
        )
        self.dcc = DCCManager(
            self,
            public_ip=(getattr(s, "dcc_public_ip", "") or None),
            fixed_port=getattr(s, "dcc_listen_port", None),
            port_range=getattr(s, "dcc_port_range", (50000, 52000)),
            idle_timeout=getattr(s, "dcc_idle_timeout", 600),
            allow_unauthed=bool(getattr(s, "dcc_allow_unauthed", False)),
        )

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

    def irc_RPL_WHOISUSER(self, prefix, params):
        # params: [me, nick, user, host, '*', realname]
        _, nick, user, host, _, realname = params
        self.pending_whois[nick] = {
            'nick': nick,
            'user': user,
            'host': host,
            'realname': realname
        }

    def irc_RPL_ENDOFWHOIS(self, prefix, params):
        nick = params[1]
        if nick not in self.pending_whois:
            self.pending_whois[nick] = None

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
        if getattr(self, "_hb_thread", None):
            return

        self._hb_stop = threading.Event()

        def _loop():
            interval = 30
            backoff = interval
            while getattr(self, "monitor_enabled", False) and self.connected and not self._hb_stop.is_set():
                try:
                    payload = self._collect_metrics()
                    ok = send_heartbeat(self.monitorId, self.hmac_secret, payload)
                    if ok:
                        backoff = interval
                    else:
                        backoff = min(backoff * 2, 300)
                        logger.debug(f"Heartbeat loop backoff (next={backoff}s)")
                except Exception:
                    backoff = min(backoff * 2, 300)
                self._hb_stop.wait(backoff)

        self._start_worker("heartbeat", target=_loop, global_singleton=False)

    def _stop_heartbeat_loop(self):
        try:
            if getattr(self, "_hb_stop", None):
                self._hb_stop.set()
        except Exception:
            pass
        logger.info("🛑 Heartbeat loop stopped.")

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
        self.pending_join_requests[name.lower()] = name
        self.join(name)

    def userQuit(self, user, message):
        if self.channel_details:
            self.channel_details = [
                arr for arr in self.channel_details if arr[1].lower() != user.lower()
            ]
        self.logoutOnQuit(user)
        nick = user.split('!')[0]
        ident, host = self._get_any_ident_host(nick)
        seen.on_quit(self.sql, self.botId, nick, message, ident=ident, host=host)


    def signedOn(self):
        logger.info("Signed on to the server")
        # load commands
        self.load_commands()
        try:
            # pornește monitorul doar dacă suntem efectiv conectați
            if self.connected:
                self._start_monitor_init_async()
        except Exception as e:
            logger.info(f"⚠️ Monitor init failed: {e}")
            self.monitor_enabled = False
            self.user_cache.clear()
        if self.known_users:
            logger.info("🔄 Resetting user caches.")
            self.known_users.clear()
        self.connected = True
        self.current_connect_time = time.time()
        self.sendLine("AWAY :" + s.away)
        if s.nickserv_login_enabled:
            self.login_nickserv()
            if s.require_nickserv_ident:
                logger.info("⏳ Waiting for NickServ identification before joining channels.")
                return
        self._join_channels()

    def lineReceived(self, line):
        try:
            line = line.decode("utf-8")
        except UnicodeDecodeError:
            return
        super().lineReceived(line)

    # --- Conexiune socket Twisted ---
    def connectionMade(self):
        super().connectionMade()
        self.connected = True

    def connectionLost(self, reason):

        try:
            if hasattr(self, "dcc"):
                self.dcc.shutdown()
        except Exception:
            pass

        try:
            send_monitor_offline(self.monitorId, self.hmac_secret)
        except Exception:
            pass
        super().connectionLost(reason)

        self.connected = False

        self._stop_heartbeat_loop()
        self._stop_worker("message_sender")
        self._stop_worker("recover_nick")
        self._stop_worker("logged_users")

        self.monitor_enabled = False
        try:
            self._cancel_all_timers()
        except Exception:
            pass

        logger.info(
            f"🔌 Disconnected from IRC: {reason}. Heartbeat & per-connection workers stopped; global workers kept alive.")

    def _ensure_channel_canonical_in_db(self, requested_name: str, canonical_name: str):

        if self.sql.sqlite_channel_exists_exact(self.botId, canonical_name):
            return

        old = self.sql.sqlite_find_channel_case_insensitive(self.botId, canonical_name)
        if old and old != canonical_name:
            self.sql.sqlite_update_channel_name(self.botId, old, canonical_name)
            return

        self.sql.sqlite3_addchan(canonical_name, self.username, self.botId)

    def joined(self, channel):
        if self.notOnChannels and channel.lower() in (c.lower() for c in self.notOnChannels):
            self.notOnChannels.remove(channel)

        logger.info(f"Joined channel {channel}")

        if channel in self.rejoin_pending:
            del self.rejoin_pending[channel]
        try:
            requested = self.pending_join_requests.pop(channel.lower(), None)
            self._ensure_channel_canonical_in_db(requested_name=requested, canonical_name=channel)
        except Exception as e:
            logger.info(f"[WARN] DB sync on join failed for {channel}: {e}")

        self.sendLine(f"WHO {channel}")

    def userJoined(self, user, channel):

        if self.channel_details:
            self.sendLine(f"WHO {user}")

        ident = user.split('!')[1].split('@')[0] if '!' in user and '@' in user else ''
        host = user.split('@')[1] if '@' in user else ''
        seen.on_join(self.sql, self.botId, channel, user, ident, host)

    def userLeft(self, user, channel):

        nick = user.split('!')[0]
        ident, host = self._get_ident_host_from_channel(channel, nick)
        seen.on_part(self.sql, self.botId, channel, nick, ident=ident, host=host, reason="left")
        self._remove_user_from_channel(channel, nick)

    def userKicked(self, kicked, channel, kicker, message):

        ident, host = self._get_ident_host_from_channel(channel, kicked)
        seen.on_kick(self.sql, self.botId, channel, kicked, kicker, message, ident=ident, host=host)
        self._remove_user_from_channel(channel, kicked)

    def kickedFrom(self, channel, kicker, message):

        if channel.lower() in (c.lower() for c in self.notOnChannels):
            self.notOnChannels.append(channel)
        self.channel_details = [arr for arr in self.channel_details if channel in arr]
        self.addChannelToPendingList(channel, f"kicked by {kicker} with reason: {message}")
        self._schedule_rejoin(channel)

    def userRenamed(self, oldnick, newnick):
        logger.info(f"🔄 Nick change detected: {oldnick} → {newnick}")
        seen.on_nick_change(self.sql, self.botId, oldnick, newnick)

        for user in self.channel_details:
            if user[1] == oldnick:
                user[1] = newnick

        updated_known = set()
        for chan, nick in self.known_users:
            if nick == oldnick:
                updated_known.add((chan, newnick))
            else:
                updated_known.add((chan, nick))
        self.known_users = updated_known

        updated_cache = {}
        for (nick, host), userId in self.user_cache.items():
            if nick == oldnick:
                updated_cache[(newnick, host)] = userId
            else:
                updated_cache[(nick, host)] = userId
        self.user_cache = updated_cache

        for data in self.logged_in_users.values():
            if isinstance(data, dict) and data.get("nick") == oldnick:
                data["nick"] = newnick

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
        if not s.nickserv_login_enabled or not s.nickserv_password:
            return
        try:
            self.sendLine(f"PRIVMSG {s.nickserv_nick} :IDENTIFY {s.nickserv_botnick} {s.nickserv_password}")
            logger.info(f"🔐 Sent IDENTIFY to {s.nickserv_nick}")
            self.nickserv_waiting = True
        except Exception as e:
            logger.info(f"❌ Failed to IDENTIFY to {s.nickserv_nick}: {e}")

    def start_ignore_cleanup_if_needed(self):
        if not self.ignore_cleanup_started:
            if self.sql.sqlite_has_active_ignores(self.botId):
                logger.info("⏳ Active ignores found. Starting cleanup thread...")
                self.thread_ignore_cleanup = ThreadWorker(target=self.cleanup_ignores, name="ignore_cleanup")
                self.thread_ignore_cleanup.start()
                self.ignore_cleanup_started = True

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
        info = self.sql.sqlite_handle(self.botId, nick, host)

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
        to_remove = []
        for userId, data in self.logged_in_users.items():
            hosts = data["hosts"]
            updated_hosts = [h for h in hosts if
                             any(h in self.get_hostname(u[1], f"{u[2]}@{u[3]}", 0) for u in self.channel_details)]
            if not updated_hosts:
                to_remove.append(userId)
            else:
                self.logged_in_users[userId] = {
                    **data,
                    "hosts": updated_hosts
                }
        for userId in to_remove:
            del self.logged_in_users[userId]
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
            if mode_char in mode_map and i < len(args):
                nick = args[i]
                privilege_char = mode_map[mode_char]
                self.user_update_status(channel, nick, privilege_char, set)

    def irc_ERR_BANNEDFROMCHAN(self, prefix, params):
        logger.info(f"Error: Banned from channel {params[1]}")
        if params[1].lower() in (c.lower() for c in self.notOnChannels):
            self.notOnChannels.append(params[1])
            self.addChannelToPendingList(params[1], f"banned on {params[1]}")
        self._schedule_rejoin(params[1])

    def irc_ERR_CHANNELISFULL(self, prefix, params):
        logger.info(f"Error: Channel {params[1]} is full")
        if params[1].lower() in (c.lower() for c in self.notOnChannels):
            self.notOnChannels.append(params[1])
            self.addChannelToPendingList(params[1], f"{params[1]} is full, cannot join")
        self._schedule_rejoin(params[1])

    def irc_ERR_BADCHANNELKEY(self, prefix, params):
        logger.info(f"Error: Bad channel key for {params[1]}")
        if params[1].lower() in (c.lower() for c in self.notOnChannels):
            self.notOnChannels.append(params[1])
            self.addChannelToPendingList(params[1], f"invalid channel key (+k) for {params[1]}")
        self._schedule_rejoin(params[1])

    def irc_ERR_INVITEONLYCHAN(self, prefix, params):
        logger.info(f"Error: Invite-only channel {params[1]}")
        if params[1].lower() in (c.lower() for c in self.notOnChannels):
            self.notOnChannels.append(params[1])
            self.addChannelToPendingList(params[1], f"invite only on {params[1]}")
        self._schedule_rejoin(params[1])

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

            if wnickname == s.nickname:
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
                self._start_worker("recover_nick", target=self.recover_nickname, global_singleton=False)

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
        for user_details in self.channel_details:
            if user_details[0] == channel and user_details[1] == nick:
                current = user_details[4] or ""
                if set_mode:
                    if status_char not in current:
                        user_details[4] = ''.join(sorted(current + status_char))
                else:
                    user_details[4] = current.replace(status_char, '')
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
        nick = user.split("!")[0] if user else ""
        if nick.lower() == s.nickserv_nick.lower() and getattr(self, "nickserv_waiting", True):
            if any(keyword in message.lower() for keyword in
                   ["you are now identified", "has been successfully identified"]):
                logger.info("✅ NickServ identification successful (via NOTICE).")
                self.nickserv_waiting = False
                self._join_channels()
            elif any(keyword in message.lower() for keyword in
                     ["password incorrect", "authentication failed", "is not a registered"]):
                logger.info("❌ NickServ identification failed (via NOTICE).")
                self.nickserv_waiting = False
                logger.info("➡️ Falling back to main channel only.")
                for chan in s.channels:
                    self.join(chan)
                    self.channels.append(chan)
                    self.send_message(chan, "❌ NickServ identification failed. Limited channel access.")

    def privmsg(self, user, channel, msg):
        nick = user.split("!")[0]
        host = user.split("!")[1]
        args = msg.split()
        command = ""
        feedback = channel
        target_channel = channel
        is_private = (channel.lower() == self.nickname.lower())
        is_nick_cmd = False
        is_other_chan = False
        lhost = self.get_hostname(nick, f"{host}", 0)

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

    def check_access(self, channel, userId, flags):
        sql_instance = self.sql
        if sql_instance.sqlite_has_access_flags(self.botId, userId, flags):
            return True
        if sql_instance.sqlite_has_access_flags(self.botId, userId, flags, channel):
            return True
        return False

    def restart(self, reason="Restarting..."):
        try:
            self.sendLine(f"QUIT :{reason}")
        except Exception:
            pass

        reactor.callLater(2.0, self._restart_process)

    def _restart_process(self):
        import os
        import sys
        import subprocess
        from pathlib import Path

        instance = os.getenv("BLACKBOT_INSTANCE_NAME", "main")
        base_dir = Path(__file__).resolve().parent
        script = base_dir / "BlackBoT.py"
        env = os.environ.copy()
        env["BLACKBOT_INSTANCE_NAME"] = instance

        if os.name == "nt":
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
            subprocess.Popen(
                [sys.executable, str(script)],
                cwd=str(base_dir),
                env=env
            )
        os._exit(0)

    # die process
    def die(self, reason="Killed by !die"):
        import os
        import sys
        from pathlib import Path
        from twisted.internet import reactor

        try:
            self.sendLine(f"QUIT :{reason}")
        except Exception:
            pass

        instance = os.getenv("BLACKBOT_INSTANCE_NAME", "main")
        base_dir = Path(__file__).resolve().parent
        pid_path = base_dir / "instances" / instance / f"{instance}.pid"

        try:
            if pid_path.exists():
                pid_path.unlink()
        except Exception:
            pass

        try:
            reactor.stop()
        except Exception:
            pass
        os._exit(0)

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
                default_type = getattr(s, "default_hostname", 2)
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
        for arr in self.channel_details:
            if arr[0] == chan and arr[1] == nick and '@' in arr[4]:
                return True
        return False

    def user_is_halfop(self, nick, chan):
        for arr in self.channel_details:
            if arr[0] == chan and arr[1] == nick and '%' in arr[4]:
                return True
        return False

    def user_is_admin(self, nick, chan):
        for arr in self.channel_details:
            if arr[0] == chan and arr[1] == nick and '&' in arr[4]:
                return True
        return False

    def user_is_owner(self, nick, chan):
        for arr in self.channel_details:
            if arr[0] == chan and arr[1] == nick and '~' in arr[4]:
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
                'core.seen',
                'core.dcc',
                'core.log',
                'core.nettools',
                'core.monitor_client',
                'core.optimized_cache'
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

            if failed:
                self.send_message(feedback, f"ℹ️ Reloaded: {len(reloaded)} ok, {len(failed)} failed.")
            self.send_message(feedback, f"✅ Reloaded successfully. 🧹 Memory cleanup completed.")

        except Exception as e:
            self.send_message(feedback, f"❌ Reload failed: {e}")

    def auto_update_check_loop(self):
        import core.update as update
        stop_ev = get_event("auto_update")
        while not stop_ev.is_set():
            try:
                if not s.autoUpdateEnabled:
                    break
                local_version = update.read_local_version()
                remote_version = update.fetch_remote_version()
                if remote_version and remote_version > local_version:
                    logger.info(f"🔄 Update found → {remote_version} > {local_version}")
                    update.update_from_github(self, "Auto-update")
                    self.restart("🔁 Auto-updated")
                    break
            except Exception as e:
                logger.error(f"⚠️ Auto-update thread error: {e}")
            time.sleep(s.autoUpdateInterval * 60)


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
        logger.info(f"🔁 Reconnecting to {host}:{port} (vhost={vhost}) ...")
        self.connect_to(host, port, vhost)

    def connect_to(self, host, port, vhost):
        self.server = vhost
        self.port = int(port)

        # dacă avem SOURCE_IP setat, îl folosim ca bindAddress
        bind_addr = None
        if getattr(s, "sourceIP", ""):
            try:
                bind_port = int(getattr(s, "sourcePort", 0) or 0)
            except ValueError:
                bind_port = 0
            bind_addr = (s.sourceIP, bind_port)

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
        logger.info(f"Connection lost: {reason}. Rotating server & reconnecting...")
        v.connected = False
        self.rotate_and_connect()

    def clientConnectionFailed(self, connector, reason):
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
        t = irc_server[0]      # 0 = v4, 1 = v6
        server = irc_server[1] # IP-ul (v4 sau v6)
        vhost = irc_server[2]

    if t == 1:
        ssocket = socket.AF_INET6
    else:
        ssocket = socket.AF_INET

    if len(old_source) > 0 and s.sourceIP:
        src_ip = s.sourceIP
        src_port = int(getattr(s, "sourcePort", 0) or 0)

        if ":" in src_ip:
            bind_addr = (src_ip, src_port, 0, 0)   # IPv6
        else:
            bind_addr = (src_ip, src_port)         # IPv4

        # tuple corectă pentru connect (IPv4/IPv6)
        if ssocket == socket.AF_INET6:
            connect_addr = (server, port, 0, 0)
        else:
            connect_addr = (server, port)

        with socket.socket(ssocket, socket.SOCK_STREAM) as sk:
            sk.bind(bind_addr)
            sk.connect(connect_addr)

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
    logger.debug(f"🚀 BlackBoT started successfully! Connecting to {host}:{port}")
    reactor.run()
