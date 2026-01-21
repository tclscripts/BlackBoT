# core/dcc.py
from __future__ import annotations
from typing import Optional, Dict, Tuple, List
from dataclasses import dataclass, field
import socket, time, ipaddress, random, threading
from twisted.internet import reactor, protocol
from twisted.protocols.basic import LineReceiver

from core.threading_utils import get_event
from core.threading_utils import ThreadWorker
from core import log
from core.environment_config import config

logger = log.get_logger("dcc")

DEFAULT_IDLE = 600  # seconds
CHANNEL_REQUIRED = {"op", "deop", "voice", "devoice", "hop", "hdeop", "say", "cycle", "add", "delacc", "userlist"}


def ip_to_int(ip: str) -> int:
    return int(ipaddress.IPv4Address(ip))


def int_to_ip(n: int) -> str:
    return str(ipaddress.IPv4Address(n))


@dataclass
class DCCSession:
    peer_nick: str
    transport: Optional[protocol.Protocol] = None
    started_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    outbound_offer: bool = False
    listening_port: Optional[object] = None
    meta: Dict[str, str] = field(default_factory=dict)

    def age(self) -> int:
        return int(time.time() - self.started_at)

    def touch(self) -> None:
        self.last_activity = time.time()


class DCCChatProtocol(LineReceiver):
    delimiter = b"\n"

    def __init__(self, manager: "DCCManager", peer_nick: str):
        self.manager = manager
        self.peer_nick = peer_nick

    def connectionMade(self):
        self.manager.on_connected(self.peer_nick, self)
        try:
            if self.manager._is_botlink_user(self.peer_nick):
                self.send_text(f"\x02BL:HELLO {self.manager.bot.nickname}\x02")
        except Exception:
            pass

    def lineReceived(self, line: bytes):
        try:
            text = line.decode(errors="ignore")
        except Exception:
            text = ""
        self.manager.on_line(self.peer_nick, text)

    def connectionLost(self, reason):
        # Notificăm managerul doar dacă conexiunea s-a pierdut real (socket closed)
        self.manager.on_disconnected(self.peer_nick)

    def send_text(self, text: str):
        try:
            self.sendLine(text.encode("utf-8"))
        except Exception as e:
            logger.warning(f"[proto] send_text error peer={self.peer_nick!r} err={e}")


class DCCChatFactory(protocol.ClientFactory):
    def __init__(self, manager: "DCCManager", peer_nick: str):
        self.manager = manager
        self.peer_nick = peer_nick

    def buildProtocol(self, addr):
        return DCCChatProtocol(self.manager, self.peer_nick)

    def clientConnectionFailed(self, connector, reason):
        self.manager.on_failed(self.peer_nick, f"connect failed: {reason}")


class DCCListenFactory(protocol.Factory):
    def __init__(self, manager: "DCCManager", peer_nick: str | None = None):
        self.manager = manager
        self.peer_nick = peer_nick

    def buildProtocol(self, addr):
        nick = self.peer_nick or self.manager._claim_pending_offer() or "?"
        return DCCChatProtocol(self.manager, nick)


class DCCManager:
    """
    Singleton Manager care rezistă la restartul instanței BlackBoT.
    Permite păstrarea conexiunilor DCC active în timpul unui !jump.
    """
    _instance = None
    _registry_lock = threading.RLock()
    _registry: "set[DCCManager]" = set()
    _global_worker_name = "botlink"
    _global_backoff_default = 5
    _global_last_run: float = 0.0

    # Flag pentru a verifica dacă singleton-ul a fost inițializat complet
    _initialized = False

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(DCCManager, cls).__new__(cls)
        return cls._instance

    def __init__(self, bot, *, public_ip: Optional[str] = None, fixed_port: Optional[int] = None,
                 port_range: Tuple[int, int] = (50000, 52000), idle_timeout: int = DEFAULT_IDLE,
                 allow_unauthed: bool = False):

        # Actualizăm referința la bot la fiecare re-init (pentru !jump)
        self.bot = bot

        # Dacă managerul e deja activ, NU resetăm sesiunile, doar actualizăm IP/Config
        if self._initialized:
            logger.info(f"ℹ️ [DCC] Sessions preserved for new bot instance: {bot.nickname}")
            if public_ip: self.public_ip = public_ip
            return

        # ─────────────────────────────────────────────────────────────────
        # Inițializare (o singură dată la pornirea procesului)
        # ─────────────────────────────────────────────────────────────────
        self.sessions: Dict[str, DCCSession] = {}
        self.public_ip = public_ip or self._best_local_v4()
        self.fixed_port = fixed_port
        self.listener_port_obj = None
        self.port_min, self.port_max = port_range
        self.idle_timeout = idle_timeout
        self.allow_unauthed = allow_unauthed
        self.active_ephemeral_port = None

        with self._registry_lock:
            self._registry.add(self)

        self.pending_offers: List[Dict[str, object]] = []
        self.pending_ttl = 120
        self.link_peers: set[str] = set()

        self._refresh_link_peers_from_db()

        self._peers_refresh_interval = int(getattr(config, "botlink_refresh_interval", 60))
        self._last_peers_refresh = 0.0

        logger.debug(
            f"[init] public_ip={self.public_ip} fixed_port={self.fixed_port} range=({self.port_min},{self.port_max}) allow_unauthed={self.allow_unauthed}"
        )

        if self.fixed_port:
            self._ensure_fixed_listener()

        try:
            interval = int(getattr(config, "botlink_autoconnect_interval", 30))
        except Exception:
            interval = 30
        self.start_global_botlink_autoconnect(interval=interval)

        self._initialized = True

    # ---------- lifecycle ----------
    def shutdown(self, force: bool = False):
        """
        Închide managerul DCC.
        IMPORTANT: Dacă force=False (default), NU închide conexiunile active.
        Acest lucru permite ca DCC-ul să supraviețuiască la !jump.
        """
        if not force:
            logger.info("ℹ️ [DCC] Shutdown called but ignored (preserving sessions for jump/reload).")
            return

        logger.debug("[shutdown] Force closing all sessions and listener")
        with self._registry_lock:
            self._registry.discard(self)
        for nick, sdata in list(self.sessions.items()):
            self._close_session(nick)
        self.sessions.clear()

        if self.listener_port_obj:
            try:
                self.listener_port_obj.stopListening()
            except Exception as e:
                logger.warning(f"[shutdown] stopListening error: {e}")
            self.listener_port_obj = None

        self._initialized = False

    # ---------- utils ----------
    def _best_local_v4(self) -> str:
        ip = None
        try:
            cand = getattr(self.bot, "public_ip", None)
        except Exception:
            cand = None
        if cand:
            try:
                ipaddress.IPv4Address(cand)
                return cand
            except ipaddress.AddressValueError:
                pass
        try:
            cand = getattr(self.bot, "sourceIP", None)
        except Exception:
            cand = None
        if cand:
            try:
                ipaddress.IPv4Address(cand)
                return cand
            except ipaddress.AddressValueError:
                pass
        try:
            sck = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sck.connect(("8.8.8.8", 80))
            ip = sck.getsockname()[0]
            sck.close()
        except Exception:
            ip = "127.0.0.1"
        return ip

    def _pick_port(self) -> int:
        return self.fixed_port or random.randint(self.port_min, self.port_max)

    def _ensure_fixed_listener(self, retry_count=0, max_retries=3, retry_delay=3):
        """
        Încearcă să deschidă portul fix DCC cu retry logic.

        Args:
            retry_count: Numărul curent de încercări
            max_retries: Numărul maxim de încercări (default: 3)
            retry_delay: Delay între încercări în secunde (default: 3)
        """
        if self.listener_port_obj or not self.fixed_port:
            return

        try:
            factory = DCCListenFactory(self)
            self.listener_port_obj = reactor.listenTCP(int(self.fixed_port), factory, interface="0.0.0.0")
            self.bot.public_ip = self.public_ip
            logger.info(f"✅ [listen] Fixed port {self.fixed_port} opened successfully on 0.0.0.0")

            # Clear any ephemeral port tracking since we're using fixed
            self.active_ephemeral_port = None

        except Exception as e:
            if retry_count < max_retries:
                retry_count += 1
                logger.warning(
                    f"⚠️ [listen] Cannot open fixed port {self.fixed_port}: {e} "
                    f"— Retry {retry_count}/{max_retries} in {retry_delay}s..."
                )

                # Programăm următoarea încercare
                reactor.callLater(
                    retry_delay,
                    self._ensure_fixed_listener,
                    retry_count,
                    max_retries,
                    retry_delay
                )
            else:
                # Am epuizat toate încercările, trecem la ephemeral ports
                logger.error(
                    f"❌ [listen] Failed to open fixed port {self.fixed_port} after {max_retries} retries: {e} "
                    f"— Falling back to ephemeral ports"
                )

                # Salvăm portul fix original pentru info
                failed_fixed_port = self.fixed_port

                # Dezactivăm fixed port
                self.listener_port_obj = None
                self.fixed_port = None

                # Alegem un port random din range și îl salvăm
                self.active_ephemeral_port = random.randint(self.port_min, self.port_max)

                try:
                    msg = (
                        f"❌ DCC: Cannot open fixed port {failed_fixed_port} after {max_retries} retries. "
                        f"Using ephemeral port {self.active_ephemeral_port} from range {self.port_min}-{self.port_max}"
                    )
                    if hasattr(self.bot, "message_queue"):
                        self.bot.send_message(self.bot.nickname, msg)
                    else:
                        reactor.callLater(2.0, lambda: hasattr(self.bot, "message_queue") and
                                                       self.bot.send_message(self.bot.nickname, msg))
                except Exception:
                    pass

    def _gc_pending_offers(self):
        now = time.time()
        before = len(self.pending_offers)
        self.pending_offers = [p for p in self.pending_offers if now - p["ts"] <= self.pending_ttl]
        after = len(self.pending_offers)
        if before != after:
            logger.debug(f"[fifo] GC pending_offers: {before} -> {after}")

    def _claim_pending_offer(self) -> Optional[str]:
        if not self.pending_offers:
            logger.debug("[fifo] claim: empty")
            return None
        claim = self.pending_offers.pop(0).get("nick")
        logger.debug(f"[fifo] claim: {claim!r}")
        return claim

    def _peek_pending_offer(self) -> Optional[str]:
        if not self.pending_offers:
            return None
        return self.pending_offers[0].get("nick")

    def _has_open_session(self, peer: str) -> bool:
        s = self.sessions.get(peer.lower())
        return bool(s and s.transport)

    def ensure_authed(self, nick: str, hostmask: str) -> bool:
        if self._is_botlink_user(nick):
            logger.debug(f"[auth] {nick!r} allowed via botlink peer")
            return True
        if self.allow_unauthed:
            logger.debug(f"[auth] allow_unauthed=True -> {nick!r} allowed")
            return True
        try:
            lhost = self.bot.get_hostname(nick, hostmask, 0)
            uid = self.bot.get_logged_in_user_by_host(lhost)
            ok = bool(uid)
            logger.debug(f"[auth] {nick!r} host={lhost} uid={uid} -> {ok}")
            return ok
        except Exception as e:
            logger.warning(f"[auth] error for nick={nick!r}: {e}")
            return False

    # ---------- outward offer ----------
    def offer_chat(self, nick: str, *, feedback: str):
        if self._is_botlink_user(nick) and self._has_open_session(nick):
            logger.debug(f"[offer] skip, already open with botlink peer {nick!r}")
            try:
                self.bot.send_message(self.bot.nickname, f"ℹ️ DCC with {nick} already open.")
            except Exception:
                pass
            return

        port = self._pick_port()
        if self.fixed_port:
            self._ensure_fixed_listener()
            port = int(self.fixed_port)
            port_obj = self.listener_port_obj
            logger.debug(f"[offer] fixed-port offer to {nick!r} at {self.public_ip}:{port}")
        else:
            try:
                factory = DCCListenFactory(self, nick)
                port_obj = reactor.listenTCP(port, factory, interface="0.0.0.0")
                logger.debug(f"[offer] ephemeral listener for {nick!r} on 0.0.0.0:{port}")
            except Exception as e:
                logger.error(f"[offer] listenTCP error for {nick!r}: {e}")
                self.bot.send_message(feedback, f"❌ Cannot open DCC listen port: {e}")
                return

        ip = self.public_ip
        ip_int = ip_to_int(ip)

        sess = DCCSession(
            peer_nick=nick,
            outbound_offer=True,
            listening_port=(None if self.fixed_port else port_obj),
            meta={"ip": ip, "port": str(port)},
        )
        self.sessions[nick.lower()] = sess
        logger.debug(f"[offer] session created for {nick!r} ip={ip} port={port} outbound_offer=True")

        self._gc_pending_offers()
        self.pending_offers = [p for p in self.pending_offers if (p.get("nick", "").lower() != nick.lower())]
        self.pending_offers.append({"nick": nick, "ts": time.time()})
        logger.debug(
            f"[fifo] appended offer nick={nick!r} size={len(self.pending_offers)} peek={self._peek_pending_offer()!r}")

        ctcp = f"\x01DCC CHAT chat {ip_int} {port}\x01"
        self.bot.send_message(feedback, f"📨 Sent DCC CHAT offer to {nick} ({ip}:{port})")
        self.bot.sendLine(f"PRIVMSG {nick} :{ctcp}")

    # ---------- inbound offer ----------
    def accept_offer(self, nick: str, ip_or_int: str, port: int, *, feedback: str):
        if self._is_botlink_user(nick) and self._has_open_session(nick):
            logger.debug(f"[accept] ignore inbound offer from {nick!r}: already open")
            try:
                self.bot.send_message(self.bot.nickname, f"ℹ️ Ignoring DCC offer from {nick}: link already open.")
            except Exception:
                pass
            return

        try:
            host = str(int_to_ip(int(ip_or_int))) if str(ip_or_int).isdigit() else ip_or_int
        except Exception:
            host = ip_or_int

        real_host = (
                self._bind_to_authenticated_host(nick)
                or self._best_seen_host(nick)
                or host
        )
        self.bot.send_message(
            feedback, f"🔗 Connecting to DCC {nick} at {host}:{port} (IRC host: {real_host}) ..."
        )

        factory = DCCChatFactory(self, nick)
        try:
            reactor.connectTCP(host, int(port), factory)
        except Exception as e:
            logger.error(f"[accept] connectTCP error nick={nick!r}: {e}")
            self.bot.send_message(feedback, f"❌ DCC connect error: {e}")
            return

        self.sessions[nick.lower()] = DCCSession(
            peer_nick=nick,
            outbound_offer=False,
            meta={
                "ip": host,
                "port": str(port),
                "irc_host": real_host,
                "hostmask": f"*@" + real_host if "@" not in real_host else real_host,
            },
        )

    def broadcast_log_to_humans(self, log_message: str) -> None:
        """
        Broadcast a log message to all human (non-botlink) DCC sessions.
        Called by the logging system via callback.

        Args:
            log_message: Formatted log message (already includes timestamp, level, etc.)
        """
        # Thread-local flag pentru a preveni infinite loops
        import threading
        if not hasattr(self, '_broadcast_lock'):
            self._broadcast_lock = threading.local()

        # Prevent recursion (dacă send_text generează un log)
        if getattr(self._broadcast_lock, 'active', False):
            return

        try:
            self._broadcast_lock.active = True

            # Trimite la toate sesiunile umane active
            for nick, session in list(self.sessions.items()):
                # Skip dacă nu are transport
                if not session or not session.transport:
                    continue

                # Skip botlink sessions (nu sunt umani)
                if session.meta.get('botlink') == '1':
                    continue

                # Skip dacă explicit disabled
                if session.meta.get('log_streaming') == '0':
                    continue

                # Trimite log-ul
                try:
                    session.transport.send_text(f"[LOG] {log_message}")
                except Exception:
                    # Silent fail - nu vrem să generăm mai multe loguri
                    pass

        except Exception:
            # Silent fail total - критical pentru a evita loops
            pass
        finally:
            self._broadcast_lock.active = False

    # ---------- protocol callbacks ----------
    def on_connected(self, nick: str, proto: DCCChatProtocol):
        if nick == "?":
            claimed = self._claim_pending_offer()
            if claimed:
                logger.debug(f"[bind] '?' mapped to {claimed!r} via FIFO claim")
                nick = claimed
                proto.peer_nick = nick
            else:
                logger.warning("[bind] inbound '?' but no pending offers to claim")

        self._gc_pending_offers()
        if self._is_botlink_user(nick):
            existing = self.sessions.get(nick.lower())
            if existing and existing.transport and existing.transport is not proto:
                logger.debug(f"[connected] duplicate botlink connection from {nick!r} -> closing new one")
                try:
                    proto.send_text("[DCC] duplicate botlink link; keeping the first connection.")
                    proto.transport.loseConnection()
                except Exception:
                    pass
                return

        key = nick.lower()
        sdata = self.sessions.get(key) or DCCSession(peer_nick=nick)
        self.sessions[key] = sdata
        sdata.transport = proto
        sdata.touch()

        try:
            peer = getattr(proto.transport, "getPeer", lambda: None)()
            ph = getattr(peer, "host", None)
            pp = getattr(peer, "port", None)
            if ph and not sdata.meta.get("ip"):
                sdata.meta["ip"] = str(ph)
            if pp and not sdata.meta.get("port"):
                sdata.meta["port"] = str(pp)
        except Exception:
            pass

        hm = self._bind_to_authenticated_host(nick) or self._best_seen_host(nick) or "*@*"
        if (not hm or hm.endswith("@*")) and sdata.meta.get("ip"):
            hm = f"*@" + sdata.meta["ip"]
        sdata.meta["hostmask"] = hm

        if self._is_botlink_user(nick):
            sdata.meta["botlink"] = "1"

        self.bot.send_message(nick, "[DCC] connected. You can now type commands or chat.")
        if sdata.meta.get("botlink") != "1":
            try:
                # Enable log streaming by default
                sdata.meta['log_streaming'] = '1'
                proto.send_text("ℹ️ Log streaming enabled for this session.")

                # Send buffered logs (last 10 lines)
                if hasattr(self.bot, 'dcc_log_handler') and self.bot.dcc_log_handler:
                    sent = self.bot.dcc_log_handler.send_buffered_logs(nick, max_lines=10)
                    if sent > 0:
                        proto.send_text(f"[INFO] Sent {sent} buffered log lines")
            except Exception as e:
                logger.warning(f"Failed to enable log streaming for {nick}: {e}")

    def on_line(self, nick: str, text: str):
        key = nick.lower()
        sess = self.sessions.get(key)
        if sess:
            sess.touch()

        line = (text or "").strip()
        if not line:
            return

        if line.startswith("\x02BL:HELLO ") and line.endswith("\x02"):
            announced = line[len("\x02BL:HELLO "):-1].strip()
            if announced:
                if sess and getattr(sess, "transport", None):
                    try:
                        self._rename_session(nick, announced, sess.transport)
                        nick = announced
                        key = nick.lower()
                        sess = self.sessions.get(key)
                    except Exception as e:
                        logger.debug(f"[handshake] rename error for {nick!r} -> {announced!r}: {e}")

                if sess and getattr(sess, "transport", None):
                    try:
                        peer = getattr(sess.transport.transport, "getPeer", lambda: None)()
                        ph = getattr(peer, "host", None)
                        pp = getattr(peer, "port", None)
                        if ph and not sess.meta.get("ip"):
                            sess.meta["ip"] = str(ph)
                        if pp and not sess.meta.get("port"):
                            sess.meta["port"] = str(pp)
                    except Exception:
                        pass

                if sess and self._is_botlink_user(nick):
                    sess.meta["botlink"] = "1"
            return

        if line.startswith("[BL/MSG]"):
            logger.debug(f"[botlink] ctrl from={nick!r} text={line!r}")
            return

        if sess and sess.meta.get("botlink") == "1":
            return

        if line.startswith(config.char) or line.lower().startswith(self.bot.nickname.lower()):
            self._dispatch_as_privmsg(nick, line)
            return

        # Echo simplu
        self.bot.send_message(nick, f"[DCC←{nick}] {line}")

    def on_disconnected(self, nick: str):
        """
        Apelat când socketul DCC crapă.
        Dacă utilizatorul închide fereastra DCC, aceasta se execută.
        Dacă botul dă !jump, aceasta NU ar trebui să se execute (socketul rămâne viu).
        """
        key = nick.lower()
        s = self.sessions.pop(key, None)
        if s and s.listening_port and not self.fixed_port:
            try:
                s.listening_port.stopListening()
            except Exception as e:
                logger.warning(f"[disconnect] stopListening error for {nick!r}: {e}")

        # Trimitem mesaj doar dacă botul e conectat la IRC
        if hasattr(self.bot, "send_message"):
            try:
                self.bot.send_message(nick, f"[DCC] disconnected.")
            except:
                pass

    def on_failed(self, nick: str, reason: str):
        logger.error(f"[failed] nick={nick!r} reason={reason}")
        try:
            self.bot.send_message(nick, f"[DCC] connection failed: {reason}")
        except:
            pass
        self._close_session(nick)

    # ---------- admin ----------
    def send_text(self, nick: str, text: str) -> bool:
        key = nick.lower()
        sdata = self.sessions.get(key)
        if not sdata or not sdata.transport:
            logger.debug(f"[send_text] no transport for {nick!r}")
            return False
        try:
            sdata.transport.send_text(text)
            sdata.touch()
            return True
        except Exception as e:
            logger.warning(f"[send_text] error to={nick!r}: {e}")
            return False

    def list_sessions(self) -> Dict[str, Dict[str, str]]:
        out = {}
        for k, sdata in self.sessions.items():
            out[k] = {
                "nick": sdata.peer_nick,
                "age": str(sdata.age()),
                "last": str(int(time.time() - sdata.last_activity)),
                "state": "open" if sdata.transport else ("listening" if sdata.outbound_offer else "connecting"),
                "ip": sdata.meta.get("ip", ""),
                "port": sdata.meta.get("port", ""),
            }
        return out

    def _close_session(self, nick: str):
        key = nick.lower()
        sdata = self.sessions.pop(key, None)
        if not sdata:
            return
        if sdata.transport:
            try:
                sdata.transport.transport.loseConnection()
            except Exception as e:
                logger.warning(f"[close] loseConnection error for {nick!r}: {e}")
        if sdata.listening_port and not self.fixed_port:
            try:
                sdata.listening_port.stopListening()
            except Exception as e:
                logger.warning(f"[close] stopListening error for {nick!r}: {e}")

    def close(self, nick: str) -> bool:
        if nick.lower() not in self.sessions:
            logger.debug(f"[close] session not found for {nick!r}")
            return False
        self._close_session(nick)
        return True

    # ---------- command pipeline integration ----------
    def _dispatch_as_privmsg(self, nick: str, text: str):
        sess = self.sessions.get(nick.lower())
        hostmask = None
        if sess:
            hostmask = sess.meta.get("hostmask") or sess.meta.get("irc_host")
        if not hostmask:
            hostmask = self._bind_to_authenticated_host(nick) or "*@*"
        user_prefix = f"{nick}!{hostmask}"
        line = self._inject_channel_if_needed((text or "").strip(), nick)
        # Folosim try/except pentru că botul poate fi între conexiuni la !jump
        try:
            self.bot.privmsg(user_prefix, self.bot.nickname, line)
        except Exception as e:
            logger.error(f"[DCC] Failed to dispatch command (bot not ready?): {e}")

    def _inject_channel_if_needed(self, line: str, nick: str) -> str:
        tokens = line.split()
        if not tokens:
            return line
        if tokens[0].startswith(config.char):
            cmd = tokens[0][1:].lower()
            args = tokens[1:]
            if cmd in CHANNEL_REQUIRED and (not args or not args[0].startswith("#")):
                commons = self._common_channels(nick)
                if len(commons) == 1:
                    injected = f"{tokens[0]} {commons[0]}{' ' + ' '.join(args) if args else ''}"
                    logger.debug(f"[inject] injected channel {commons[0]} for {nick!r} -> {injected!r}")
                    return injected
                elif len(commons) > 1:
                    self.bot.send_message(
                        nick,
                        "ℹ️ This command requires a channel. Example: !op #channel [nick]. "
                        f"Common channels: {', '.join(sorted(commons))}"
                    )
                else:
                    self.bot.send_message(
                        nick,
                        "ℹ️ This command requires a channel. "
                        "Example: !op #channel [nick]. No common channels detected."
                    )
        else:
            if len(tokens) >= 2 and tokens[0].lower() == self.bot.nickname.lower():
                cmd = tokens[1].lower()
                args = tokens[2:]
                if cmd in CHANNEL_REQUIRED and (not args or not args[0].startswith("#")):
                    commons = self._common_channels(nick)
                    if len(commons) == 1:
                        injected = f"{tokens[0]} {tokens[1]} {commons[0]}{' ' + ' '.join(args) if args else ''}"
                        logger.debug(f"[inject] injected channel {commons[0]} for {nick!r} -> {injected!r}")
                        return injected
                    elif len(commons) > 1:
                        self.bot.send_message(
                            nick,
                            "ℹ️ This command requires a channel. "
                            f"Example: {self.bot.nickname} op #channel [nick]. "
                            f"Common channels: {', '.join(sorted(commons))}"
                        )
                    else:
                        self.bot.send_message(
                            nick,
                            "ℹ️ This command requires a channel. "
                            f"Example: {self.bot.nickname} op #channel [nick]. No common channels detected."
                        )
        return line

    def _common_channels(self, nick: str):
        chans = set()
        try:
            for row in getattr(self.bot, "channel_details", []):
                if row and len(row) > 1 and (row[1] or "").lower() == nick.lower():
                    chans.add(row[0])
        except Exception:
            pass
        present = set(getattr(self.bot, "channels", []) or [])
        commons = sorted(chans & present, key=str.lower)
        logger.debug(f"[common] for {nick!r} -> {commons}")
        return commons

    # ---------- identity helpers ----------
    def _bind_to_authenticated_host(self, nick: str) -> str | None:
        try:
            for uid, info in (getattr(self.bot, "logged_in_users", {}) or {}).items():
                if (info.get("nick") or "").lower() == nick.lower():
                    hosts = info.get("hosts") or []
                    if hosts:
                        raw = str(hosts[0])
                        if "!" in raw:
                            raw = raw.split("!", 1)[1]
                        if "@" not in raw:
                            raw = f"*@" + raw
                        return raw
        except Exception:
            pass
        return None

    def _best_seen_host(self, nick: str) -> str | None:
        try:
            for row in getattr(self.bot, "channel_details", []):
                if row and len(row) >= 4 and (row[1] or "").lower() == nick.lower():
                    ident = row[2] or "*"
                    host = row[3] or ""
                    if host and host != "*":
                        return f"{ident}@{host}"
        except Exception:
            pass
        try:
            if hasattr(self.bot, "whois_sync"):
                info = self.bot.whois_sync(nick, timeout=5) or {}
                ident = info.get("user") or info.get("ident") or "*"
                host = info.get("host") or ""
                if host and host != "*":
                    return f"{ident}@{host}"
        except Exception:
            pass
        return None

    # ---------- botlink helpers ----------
    def add_link_peer(self, nick: str):
        self.link_peers.add(nick.lower())

    def del_link_peer(self, nick: str):
        if nick.lower() in self.link_peers:
            self.link_peers.remove(nick.lower())

    def list_link_peers(self) -> List[str]:
        lst = sorted(self.link_peers, key=str.lower)
        return lst

    def _is_botlink_user(self, nick: str) -> bool:
        if nick.lower() in self.link_peers:
            return True
        try:
            uid = None
            uid = getattr(self.bot.sql, "sqlite_get_user_by_handle", lambda *a, **k: None)(self.bot.botId, nick)
            if not uid:
                for row in getattr(self.bot, "channel_details", []):
                    if row and len(row) >= 4 and (row[1] or "").lower() == nick.lower():
                        info = self.bot.sql.sqlite_handle(self.bot.botId, nick, f"{row[2] or '*'}@{row[3] or '*'}")
                        uid = info[0] if info else None
                        break
            if uid:
                settings = self.bot.sql.sqlite_get_user_settings(self.bot.botId, uid) or {}
                return str(settings.get("botlink", "0")) == "1"
        except Exception as e:
            logger.debug(f"[botlink] _is_botlink_user error: {e}")
        return False

    def send_botmsg(self, nick: str, text: str) -> bool:
        ok = self.send_text(nick, f"[BL/MSG] {text}")
        return ok

    def broadcast_botmsg(self, text: str) -> int:
        n = 0
        for peer in list(self.link_peers):
            if self.send_botmsg(peer, text):
                n += 1
        return n

    def _peer_endpoint_from_db(self, peer: str) -> Optional[tuple[str, int]]:
        try:
            sql = self.bot.sql
            botId = self.bot.botId
            uid = sql.sqlite_get_user_id_by_name(botId, peer)
            if not uid:
                return None
            settings = sql.sqlite_get_user_settings(botId, uid) or {}
            ip = settings.get("botlink_ip")
            port = settings.get("botlink_port")
            if ip and port:
                return ip, int(port)
        except Exception as e:
            logger.warning("[botlink] endpoint lookup failed for %r: %s", peer, e)
        return None

    def has_endpoint(self, peer: str) -> bool:
        return self._peer_endpoint_from_db(peer) is not None

    def session_state(self, peer: str) -> str:
        s = self.sessions.get(peer.lower())
        if not s:
            return "none"
        if s.transport:
            return "open"
        if s.outbound_offer or s.listening_port or self.fixed_port:
            return "listening"
        return "connecting"

    def force_connect(self, peer: str, *, feedback: Optional[str] = None) -> bool:
        if self._is_botlink_user(peer) and self._has_open_session(peer):
            return True

        ep = self._peer_endpoint_from_db(peer)
        if not ep:
            if feedback:
                self.bot.send_message(feedback, f"❌ Missing endpoint for {peer} (set botlink_ip/port).")
            logger.debug("[botlink] no endpoint for %r", peer)
            return False

        ip, port = ep
        try:
            if feedback:
                self.bot.send_message(feedback, f"🔌 Forcing outbound connect to {peer} at {ip}:{port} ...")

            factory = DCCChatFactory(self, peer)
            reactor.connectTCP(ip, int(port), factory)

            key = peer.lower()
            sess = self.sessions.get(key)
            if not sess:
                sess = DCCSession(peer_nick=peer, outbound_offer=False,
                                  meta={"ip": ip, "port": str(port), "botlink": "1", "hostmask": f"*@{ip}"})
                self.sessions[key] = sess
            else:
                sess.outbound_offer = False
                sess.meta.update({"ip": ip, "port": str(port), "botlink": "1", "hostmask": f"*@{ip}"})
            sess.meta["last_fc_ts"] = str(time.time())
            return True
        except Exception as e:
            logger.warning("[botlink] force connect failed to %s: %s", peer, e)
            if feedback:
                self.bot.send_message(feedback, f"❌ Force connect failed: {e}")
            return False

    def _rename_session(self, old_key: str, new_nick: str, proto: DCCChatProtocol):
        old_key = old_key.lower()
        new_key = new_nick.lower()
        if old_key == new_key:
            return
        sdata = self.sessions.get(old_key)
        if not sdata or sdata.transport is not proto:
            return

        exist = self.sessions.get(new_key)
        if exist and exist.transport and exist.transport is not proto:
            try:
                proto.send_text("[DCC] duplicate botlink link; keeping the first connection.")
                proto.transport.loseConnection()
            except Exception:
                pass
            return

        sdata.peer_nick = new_nick
        self.sessions[new_key] = sdata
        try:
            del self.sessions[old_key]
        except KeyError:
            pass

        try:
            proto.peer_nick = new_nick
        except Exception:
            pass

        if self._is_botlink_user(new_nick):
            sdata.meta["botlink"] = "1"
            try:
                proto.send_text(f"\x02BL:HELLO {self.bot.nickname}\x02")
            except Exception:
                pass

    def _maintain_botlink(self):
        if (time.time() - getattr(self, "_last_peers_refresh", 0)) >= max(10, self._peers_refresh_interval):
            self._refresh_link_peers_from_db()
            self._last_peers_refresh = time.time()

        now = time.time()
        self._gc_pending_offers()

        for peer in list(self.link_peers):
            low = peer.lower()
            sess = self.sessions.get(low)

            if sess and sess.transport:
                is_bot = (sess.meta.get("botlink") == "1")
                if is_bot:
                    idle = now - (sess.last_activity or sess.started_at)
                    try:
                        sess.transport.send_text("PING")
                        sess.touch()
                    except Exception:
                        try:
                            self._close_session(peer)
                        except:
                            pass
                        continue
                    if self.idle_timeout and idle > self.idle_timeout:
                        try:
                            self._close_session(peer)
                        except:
                            pass
                continue

            if self.has_endpoint(peer):
                last_fc = 0.0
                if sess and isinstance(sess.meta, dict):
                    last_fc = float(sess.meta.get("last_fc_ts", "0") or 0)
                if (now - last_fc) >= 20:
                    if not sess:
                        self.sessions[low] = DCCSession(peer_nick=peer, outbound_offer=False, meta={})
                        sess = self.sessions[low]
                    sess.meta["last_fc_ts"] = str(now)
                    self.force_connect(peer)
                continue

            can_offer = True
            last_offer = 0.0
            if sess and isinstance(sess.meta, dict):
                last_offer = float(sess.meta.get("last_offer_ts", "0") or 0)
                can_offer = (now - last_offer) >= 20

            if can_offer:
                if not sess:
                    self.sessions[low] = DCCSession(peer_nick=peer, outbound_offer=True, meta={})
                    sess = self.sessions[low]
                sess.meta["last_offer_ts"] = str(now)
                self.pending_offers = [p for p in self.pending_offers if p.get("nick", "").lower() != low]
                self.pending_offers.append({"nick": peer, "ts": now})
                try:
                    self.offer_chat(peer, feedback=self.bot.nickname)
                except Exception:
                    pass

        allow = {p.lower() for p in self.link_peers}
        for nick, s in list(self.sessions.items()):
            if s.meta.get("botlink") == "1" and (nick not in allow) and not self.allow_unauthed:
                try:
                    self._close_session(nick)
                except:
                    pass

    def _refresh_link_peers_from_db(self) -> None:
        try:
            sql = getattr(self.bot, "sql", None)
            botId = getattr(self.bot, "botId", None)
            if not sql or botId is None:
                return
            rows = sql.sqlite_select("""
                                     SELECT u.username
                                     FROM users u
                                              JOIN USERSSETTINGS s
                                                   ON s.userId = u.id AND s.botId = u.botId
                                     WHERE u.botId = ?
                                       AND s.setting = 'botlink'
                                       AND (s.settingValue = '1' OR LOWER(s.settingValue) = 'true')
                                     """, (botId,))
            before = set(self.link_peers)
            self.link_peers = {(r[0] or "").lower() for r in rows if r and r[0]}
            if self.link_peers != before:
                logger.debug("[botlink] peers refreshed from DB: %s", sorted(self.link_peers))
        except Exception as e:
            logger.warning("[botlink] refresh from DB failed: %s", e)

    @classmethod
    def start_global_botlink_autoconnect(cls, *, interval: int = 30):
        for t in threading.enumerate():
            if t.name in (cls._global_worker_name, f"{cls._global_worker_name}.child") and getattr(t, "is_alive",
                                                                                                   lambda: False)():
                return

        def _target(stop_event, beat):
            cls._global_loop(stop_event, beat, interval)

        tw = ThreadWorker(
            target=_target,
            name=cls._global_worker_name,
            supervise=True,
            provide_signals=True,
            heartbeat_timeout=90.0
        )
        tw.daemon = True
        tw.start()

    @classmethod
    def stop_global_botlink_autoconnect(cls):
        try:
            get_event(cls._global_worker_name).set()
        except Exception:
            pass

    @classmethod
    def _global_loop(cls, stop_event, beat, interval: int):
        backoff = interval
        while not stop_event.is_set():
            started = time.time()
            try:
                beat()
                with cls._registry_lock:
                    managers = list(cls._registry)

                for mgr in managers:
                    try:
                        mgr._ensure_fixed_listener()
                        mgr._gc_pending_offers()
                        if hasattr(mgr, "_maintain_botlink"):
                            mgr._maintain_botlink()
                        beat()
                    except Exception as e:
                        logger.warning("[global] tick error for %r: %s", getattr(mgr.bot, "nickname", mgr), e)

                backoff = interval
            except Exception as e:
                logger.warning("[global] loop error: %s", e)
                backoff = min(max(cls._global_backoff_default, int(backoff * 1.5)), max(60, interval))

            elapsed = time.time() - started
            delay = max(1, backoff - int(elapsed))
            if stop_event.wait(delay):
                break


def broadcast_log_to_dcc(message: str):
    def _safe_broadcast():
        with DCCManager._registry_lock:
            managers = list(DCCManager._registry)

        if not managers:
            return

        for manager in managers:
            if not manager.sessions:
                continue

            for session in list(manager.sessions.values()):
                if not session.transport:
                    continue
                if session.meta.get("botlink") == "1":
                    continue
                try:
                    payload = (message + "\r\n").encode('utf-8', errors='replace')
                    session.transport.write(payload)
                except Exception:
                    pass

    reactor.callFromThread(_safe_broadcast)


log.register_dcc_callback(broadcast_log_to_dcc)