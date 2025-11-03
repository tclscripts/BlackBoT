from __future__ import annotations
from typing import Optional, Dict, Tuple
from dataclasses import dataclass, field
import socket, time, ipaddress, random
from twisted.internet import reactor, protocol
from twisted.protocols.basic import LineReceiver
import settings as s

DEFAULT_IDLE = 600  # seconds
CHANNEL_REQUIRED = {"op","deop","voice","devoice","hop","hdeop","say","cycle","add","delacc","userlist"}

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
    outbound_offer: bool = False   # True if we created a listener and expect inbound connect
    listening_port: Optional[object] = None  # IPort object from reactor.listenTCP
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

    def lineReceived(self, line: bytes):
        text = line.decode(errors="ignore")
        self.manager.on_line(self.peer_nick, text)

    def connectionLost(self, reason):
        self.manager.on_disconnected(self.peer_nick)

    def send_text(self, text: str):
        try:
            self.sendLine(text.encode("utf-8"))
        except Exception:
            pass

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
        # For a fixed-port listener we might not know the peer yet:
        # bind this inbound connection to the oldest pending offer (FIFO),
        # otherwise keep it anonymous ("?") until on_connected fallback.
        nick = self.peer_nick or self.manager._claim_pending_offer() or "?"
        return DCCChatProtocol(self.manager, nick)

class DCCManager:
    """
    DCC CHAT manager with optional SINGLE FIXED PORT.
    If fixed_port is provided, a persistent listener is opened once and reused.
    Supports multiple parallel DCC sessions and a FIFO queue for pending offers.
    """

    def __init__(self, bot, *, public_ip: Optional[str] = None, fixed_port: Optional[int] = None,
                 port_range: Tuple[int, int] = (50000, 52000), idle_timeout: int = DEFAULT_IDLE,
                 allow_unauthed: bool = False):
        self.bot = bot
        self.sessions: Dict[str, DCCSession] = {}
        self.public_ip = public_ip or self._best_local_v4()
        self.fixed_port = fixed_port
        self.listener_port_obj = None  # IPort if fixed listener active
        self.port_min, self.port_max = port_range
        self.idle_timeout = idle_timeout
        self.allow_unauthed = allow_unauthed

        # Pending offers FIFO (for fixed port, many offers in parallel)
        self.pending_offers = []  # [{"nick": str, "ts": float}]
        self.pending_ttl = 120    # seconds

        if self.fixed_port:
            self._ensure_fixed_listener()

    # ---------- lifecycle ----------
    def shutdown(self):
        for nick, s in list(self.sessions.items()):
            self._close_session(nick)
        self.sessions.clear()
        if self.listener_port_obj:
            try:
                self.listener_port_obj.stopListening()
            except Exception:
                pass
            self.listener_port_obj = None

    # ---------- utils ----------
    def _best_local_v4(self) -> str:
        try:
            ip = getattr(self.bot, "public_ip", None) or getattr(self.bot, "sourceIP", None)
        except Exception:
            ip = None
        if not ip:
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

    def _ensure_fixed_listener(self):
        if self.listener_port_obj or not self.fixed_port:
            return
        try:
            factory = DCCListenFactory(self)
            self.listener_port_obj = reactor.listenTCP(int(self.fixed_port), factory, interface="0.0.0.0")
            self.bot.public_ip = self.public_ip
        except Exception as e:
            self.bot.send_message(self.bot.nickname, f"❌ DCC: cannot open fixed port {self.fixed_port}: {e}")
            self.listener_port_obj = None

    def _gc_pending_offers(self):
        now = time.time()
        self.pending_offers = [p for p in self.pending_offers if now - p["ts"] <= self.pending_ttl]

    def _claim_pending_offer(self) -> Optional[str]:
        """Return the nick for the oldest pending offer (and remove it)."""
        if not self.pending_offers:
            return None
        return self.pending_offers.pop(0).get("nick")

    def _peek_pending_offer(self) -> Optional[str]:
        """Return (without removing) the oldest pending offer's nick, or None."""
        if not self.pending_offers:
            return None
        return self.pending_offers[0].get("nick")

    def ensure_authed(self, nick: str, hostmask: str) -> bool:
        if self.allow_unauthed:
            return True
        try:
            lhost = self.bot.get_hostname(nick, hostmask, 0)
            uid = self.bot.get_logged_in_user_by_host(lhost)
            return bool(uid)
        except Exception:
            return False

    # ---------- outward offer ----------
    def offer_chat(self, nick: str, *, feedback: str):
        port = self._pick_port()
        if self.fixed_port:
            self._ensure_fixed_listener()
            port = int(self.fixed_port)
            port_obj = self.listener_port_obj
        else:
            try:
                factory = DCCListenFactory(self, nick)
                port_obj = reactor.listenTCP(port, factory, interface="0.0.0.0")
            except Exception as e:
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

        # Register pending offer for FIFO binding on fixed port
        self._gc_pending_offers()
        self.pending_offers.append({"nick": nick, "ts": time.time()})

        ctcp = f"\x01DCC CHAT chat {ip_int} {port}\x01"
        self.bot.send_message(feedback, f"📨 Sent DCC CHAT offer to {nick} ({ip}:{port})")
        self.bot.sendLine(f"PRIVMSG {nick} :{ctcp}")

    # ---------- inbound offer ----------
    def accept_offer(self, nick: str, ip_or_int: str, port: int, *, feedback: str):
        try:
            host = str(int_to_ip(int(ip_or_int))) if str(ip_or_int).isdigit() else ip_or_int
        except Exception:
            host = ip_or_int

        # Prefer the IRC-visible host for access checks
        real_host = (
            self._bind_to_authenticated_host(nick)
            or self._best_seen_host(nick)
            or host
        )

        self.bot.send_message(
            feedback, f"🔗 Connecting to DCC {nick} at {host}:{port} (IRC host: {real_host}) ..."
        )

        factory = DCCChatFactory(self, nick)
        reactor.connectTCP(host, int(port), factory)

        self.sessions[nick.lower()] = DCCSession(
            peer_nick=nick,
            outbound_offer=False,
            meta={
                "ip": host,
                "port": str(port),
                "irc_host": real_host,  # store the IRC-visible host for access
                "hostmask": f"*@" + real_host if "@" not in real_host else real_host,
            },
        )

    # ---------- protocol callbacks ----------
    def on_connected(self, nick: str, proto: DCCChatProtocol):
        # If nick is still unknown, try to claim the oldest pending offer now
        if nick == "?":
            claimed = self._claim_pending_offer()
            if claimed:
                nick = claimed
                proto.peer_nick = nick

        self._gc_pending_offers()

        key = nick.lower()
        sdata = self.sessions.get(key) or DCCSession(peer_nick=nick)
        self.sessions[key] = sdata
        sdata.transport = proto
        sdata.touch()

        # Bind a real hostmask so access checks work
        hm = self._bind_to_authenticated_host(nick) or self._best_seen_host(nick) or "*@*"
        sdata.meta["hostmask"] = hm

        self.bot.send_message(nick, "[DCC] connected. You can now type commands or chat.")

    def on_line(self, nick: str, text: str):
        key = nick.lower()
        sdata = self.sessions.get(key)
        if sdata:
            sdata.touch()

        line = (text or "").strip()
        if not line:
            return

        # Treat lines starting with prefix or bot's nick as commands
        if line.startswith(s.char) or line.lower().startswith(self.bot.nickname.lower()):
            self._dispatch_as_privmsg(nick, line)
            return

        # Otherwise, simple echo into DCC
        self.bot.send_message(nick, f"[DCC←{nick}] {text}")

    def on_disconnected(self, nick: str):
        key = nick.lower()
        s = self.sessions.pop(key, None)
        if s and s.listening_port and not self.fixed_port:
            try:
                s.listening_port.stopListening()
            except Exception:
                pass
        self.bot.send_message(nick, f"[DCC] disconnected.")

    def on_failed(self, nick: str, reason: str):
        self.bot.send_message(nick, f"[DCC] connection failed: {reason}")
        self._close_session(nick)

    # ---------- admin ----------
    def send_text(self, nick: str, text: str) -> bool:
        key = nick.lower()
        s = self.sessions.get(key)
        if not s or not s.transport:
            return False
        try:
            s.transport.send_text(text)
            s.touch()
            return True
        except Exception:
            return False

    def list_sessions(self) -> Dict[str, Dict[str, str]]:
        out = {}
        for k, s in self.sessions.items():
            out[k] = {
                "nick": s.peer_nick,
                "age": str(s.age()),
                "last": str(int(time.time() - s.last_activity)),
                "state": "open" if s.transport else ("listening" if s.outbound_offer else "connecting"),
                "ip": s.meta.get("ip", ""),
                "port": s.meta.get("port", ""),
            }
        return out

    def _close_session(self, nick: str):
        key = nick.lower()
        s = self.sessions.pop(key, None)
        if not s:
            return
        if s.transport:
            try:
                s.transport.transport.loseConnection()
            except Exception:
                pass
        if s.listening_port and not self.fixed_port:
            try:
                s.listening_port.stopListening()
            except Exception:
                pass

    def close(self, nick: str) -> bool:
        if nick.lower() not in self.sessions:
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
        self.bot.privmsg(user_prefix, self.bot.nickname, line)

    def _inject_channel_if_needed(self, line: str, nick: str) -> str:
        """
        If the command needs a channel but none was provided, try to
        inject one automatically if there is a single common channel.
        Otherwise, prompt the user to specify it.
        """
        tokens = line.split()
        if not tokens:
            return line

        # detect command form: !cmd or BlackBoT cmd
        if tokens[0].startswith(s.char):
            cmd = tokens[0][1:].lower()
            args = tokens[1:]
            if cmd in CHANNEL_REQUIRED and (not args or not args[0].startswith("#")):
                commons = self._common_channels(nick)
                if len(commons) == 1:
                    return f"{tokens[0]} {commons[0]}{' ' + ' '.join(args) if args else ''}"
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
            # "BlackBoT op ..." style
            if len(tokens) >= 2 and tokens[0].lower() == self.bot.nickname.lower():
                cmd = tokens[1].lower()
                args = tokens[2:]
                if cmd in CHANNEL_REQUIRED and (not args or not args[0].startswith("#")):
                    commons = self._common_channels(nick)
                    if len(commons) == 1:
                        return f"{tokens[0]} {tokens[1]} {commons[0]}{' ' + ' '.join(args) if args else ''}"
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
                # row: [channel, nick, ident, host, privileges, realname, userId]
                if row and len(row) > 1 and (row[1] or "").lower() == nick.lower():
                    chans.add(row[0])
        except Exception:
            pass
        present = set(getattr(self.bot, "channels", []) or [])
        return sorted(chans & present, key=str.lower)

    # ---------- identity helpers ----------
    def _bind_to_authenticated_host(self, nick: str) -> str | None:
        for uid, info in (getattr(self.bot, "logged_in_users", {}) or {}).items():
            if (info.get("nick") or "").lower() == nick.lower():
                hosts = info.get("hosts") or []
                if hosts:
                    raw = str(hosts[0])
                    if "!" in raw:
                        raw = raw.split("!", 1)[1]  # ident@host
                    if "@" not in raw:
                        raw = f"*@" + raw
                    return raw
        return None

    def _best_seen_host(self, nick: str) -> str | None:
        # Try live channel_details
        try:
            for row in getattr(self.bot, "channel_details", []):
                if row and len(row) >= 4 and (row[1] or "").lower() == nick.lower():
                    ident = row[2] or "*"
                    host  = row[3] or ""
                    if host and host != "*":
                        return f"{ident}@{host}"
        except Exception:
            pass
        # WHOIS fallback (if available)
        try:
            if hasattr(self.bot, "whois_sync"):
                info = self.bot.whois_sync(nick, timeout=5) or {}
                ident = info.get("user") or info.get("ident") or "*"
                host  = info.get("host") or ""
                if host and host != "*":
                    return f"{ident}@{host}"
        except Exception:
            pass
        return None
