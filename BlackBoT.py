import datetime
import sys
import ipaddress
import ssl
import os
import queue
import logging
import Starter
import settings as s
import socket
from twisted.internet import protocol, ssl, reactor
from twisted.words.protocols import irc
from collections import defaultdict, deque


sys.path.append(os.path.join(os.path.dirname(__file__), 'core'))
from core import commands
from core import Variables as v
from core.commands_map import command_definitions
from core.threading_utils import ThreadWorker
from core.sql_manager import SQLManager
from core import seen
from core.threading_utils import get_event
from collections import OrderedDict
import time
import psutil
from core.monitor_client import ensure_enrollment, send_heartbeat
import platform
from twisted.internet.threads import deferToThread



class TTLCache(OrderedDict):
    def __init__(self, maxlen=2000, ttl=6 * 3600):
        super().__init__()
        self.maxlen = maxlen
        self.ttl = ttl

    def set(self, key, value):
        now = time.time()
        super().__setitem__(key, (value, now))
        self._trim(now)

    def get_valid(self, key):
        item = super().get(key)
        if not item:
            return None
        value, ts = item
        if time.time() - ts > self.ttl:
            super().__delitem__(key)
            return None
        # LRU: mută la final
        super().__delitem__(key)
        super().__setitem__(key, (value, ts))
        return value

    def _trim(self, now=None):
        now = now or time.time()
        # expiră intrările vechi
        for k, (v, ts) in list(self.items()):
            if now - ts > self.ttl:
                super().__delitem__(k)
        # ține doar ultimele maxlen (LRU)
        while len(self) > self.maxlen:
            self.popitem(last=False)


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
        self.version = _load_version()
        self.monitor_enabled = None
        self.hmac_secret = None
        self.commands = None
        self.current_connect_time = 0
        self.current_start_time = 0
        self.recover_nick_timer_start = False
        self.connected = False
        self.channel_details = []
        self.nickname = nickname
        self.realname = realname
        self.away = s.away
        self.server = None
        self.port = None
        self.username = s.username
        self.newbot = 0
        self.botId = 0
        self.nickserv_waiting = False
        self.ignore_cleanup_started = False
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
        self.user_cache = TTLCache(maxlen=2000, ttl=6 * 3600)
        self.flood_tracker = defaultdict(lambda: deque())
        self.current_start_time = time.time()
        self.sqlite3_database = s.sqlite3_database
        self.sql = SQLManager.get_instance()
        self.newbot = self.sql.sqlite3_bot_birth(self.username, self.nickname, self.realname,
                                                 self.away)

        self.botId = self.newbot[1]
        self.host_to_nicks = defaultdict(set)

        # ⏱️ Uptime update thread
        self.thread_update_uptime = ThreadWorker(
            target=lambda: self.sql.sqlite_update_uptime(self, self.botId),
            name="uptime"
        )
        self.thread_update_uptime.start()

        # 👥 Known users cleanup thread
        self.thread_known_users_cleanup = ThreadWorker(target=self.cleanup_known_users, name="known_users")
        self.thread_known_users_cleanup.start()

        # 🚫 Ignore cleanup thread (doar dacă sunt active)
        if self.sql.sqlite_has_active_ignores(self.botId):
            print("⏳ Active ignores found. Starting cleanup thread...")
            self.thread_ignore_cleanup = ThreadWorker(target=self.cleanup_ignores, name="ignore_cleanup")
            self.thread_ignore_cleanup.start()

        # 💬 Message sender thread
        self.message_queue = queue.Queue()
        self.message_delay = s.message_delay
        self.thread_message_sender = ThreadWorker(target=self._message_worker, name="message_sender")
        self.thread_message_sender.start()

        self.thread_check_for_changed_nick = ThreadWorker(target=self.recover_nickname, name="recover_nick")

        if s.autoUpdateEnabled:
            self.thread_auto_update = ThreadWorker(target=self.auto_update_check_loop, name="auto_update")
            self.thread_auto_update.start()

        if self.sql.sqlite_isBossOwner(self.botId) > 0:
            self.unbind_hello = True
        else:
            self.unbind_hello = False

    def _on_reactor(self, func, *args, **kwargs):
        from twisted.internet import reactor
        reactor.callFromThread(func, *args, **kwargs)

    def irc_msg(self, channel: str, message: str):
        self._on_reactor(self.msg, channel, message)

    def irc_sendline(self, line: str):
        self._on_reactor(self.sendLine, line.encode("utf-8"))

    def _monitor_init_worker(self):
        try:
            server_str = f"{self.server}:{self.port}" if self.server and self.port else "unknown"
            version = getattr(self, "version", "unknown")

            creds = ensure_enrollment(self.nickname, version, server_str)
            if not creds:
                print("⚠️ Monitor enrollment pending/failed; monitoring disabled for now.")
                self.monitor_enabled = False
                return

            self.monitorId = creds[
                "bot_id"]  # de pus alt nume pentru a adauga in monitor, coincide cu botId din baza de date
            self.hmac_secret = creds["hmac_secret"]
            self.monitor_enabled = True
            print(f"✅ Monitor enrolled (bot_id={self.monitorId[:8]}...). Starting heartbeat.")
            # start heartbeat pe threadpool tot cu deferToThread
            reactor.callFromThread(self._start_heartbeat_loop)

        except Exception as e:
            import traceback
            print(f"❌ Monitor init error: {e}")
            traceback.print_exc()
            self.monitor_enabled = False

    def _start_monitor_init_async(self):
        if getattr(self, "_monitor_init_started", False):
            return
        self._monitor_init_started = True
        # rulează worker-ul în threadpool-ul Twisted
        d = deferToThread(self._monitor_init_worker)
        d.addErrback(lambda f: print(f"❌ monitor_init errback: {f.getErrorMessage()}"))

    def _collect_metrics(self):
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

        def _loop():
            interval = 30
            backoff = interval
            while getattr(self, "monitor_enabled", False):
                try:
                    payload = self._collect_metrics()
                    ok = send_heartbeat(self.monitorId, self.hmac_secret, payload)
                    if ok:
                        backoff = interval
                    else:
                        backoff = min(backoff * 2, 300)
                except Exception:
                    backoff = min(backoff * 2, 300)
                time.sleep(backoff)

        self._hb_thread = ThreadWorker(target=_loop, name=f"heartbeat_{self.nickname}")
        self._hb_thread.daemon = True
        self._hb_thread.start()

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
        # logout on quit
        self.logoutOnQuit(user)
        nick = user.split('!')[0]
        ident, host = self._get_any_ident_host(nick)
        seen.on_quit(self.sql, self.botId, nick, message, ident=ident, host=host)

    def signedOn(self):
        print("Signed on to the server")
        # load commands
        self.load_commands()
        try:
            self._start_monitor_init_async()
        except Exception as e:
            print(f"⚠️ Monitor init failed: {e}")
            self.monitor_enabled = False
        # reset known users and user_cache
        if self.known_users:
            print("🔄 Resetting user caches.")
            self.known_users.clear()
            self.user_cache.clear()
        self.connected = True
        self.current_connect_time = time.time()
        self.sendLine("AWAY :" + s.away)
        if s.nickserv_login_enabled:
            self.login_nickserv()
            if s.require_nickserv_ident:
                print("⏳ Waiting for NickServ identification before joining channels.")
                return
        self._join_channels()

    def lineReceived(self, line):
        try:
            line = line.decode("utf-8")
        except UnicodeDecodeError:
            return
        super().lineReceived(line)

    def _ensure_channel_canonical_in_db(self, requested_name: str, canonical_name: str):

        if self.sql.sqlite_channel_exists_exact(self.botId, canonical_name):
            return

        old = self.sql.sqlite_find_channel_case_insensitive(self.botId, canonical_name)
        if old and old != canonical_name:
            self.sql.sqlite_update_channel_name(self.botId, old, canonical_name)
            return

        self.sql.sqlite3_addchan(canonical_name, self.username, self.botId)


    def joined(self, channel):
        if self.notOnChannels and channel in self.notOnChannels:
            self.notOnChannels.remove(channel)

        print(f"Joined channel {channel}")

        if channel in self.rejoin_pending:
            del self.rejoin_pending[channel]
        try:
            requested = self.pending_join_requests.pop(channel.lower(), None)
            self._ensure_channel_canonical_in_db(requested_name=requested, canonical_name=channel)
        except Exception as e:
            print(f"[WARN] DB sync on join failed for {channel}: {e}")

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
        
        if channel not in self.notOnChannels:
            self.notOnChannels.append(channel)
        self.channel_details = [arr for arr in self.channel_details if channel in arr]
        self.addChannelToPendingList(channel, f"kicked by {kicker} with reason: {message}")
        self._schedule_rejoin(channel)

    def userRenamed(self, oldnick, newnick):
        print(f"🔄 Nick change detected: {oldnick} → {newnick}")
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
                    print(f"Joining {channel[0]} ..")
                else:
                    self.notOnChannels.append(channel[0])

    def cleanup_ignores(self):
        stop_ev = get_event("ignore_cleanup")
        while not stop_ev.is_set():
            self.sql.sqlite_cleanup_ignores()
            if not self.sql.sqlite_has_active_ignores(self.botId):
                print("🛑 No more active ignores. Cleanup thread exiting.")
                self.ignore_cleanup_started = False
                break
            time.sleep(60)

    def login_nickserv(self):
        if not s.nickserv_login_enabled or not s.nickserv_password:
            return
        try:
            self.sendLine(f"PRIVMSG {s.nickserv_nick} :IDENTIFY {s.nickserv_botnick} {s.nickserv_password}")
            print(f"🔐 Sent IDENTIFY to {s.nickserv_nick}")
            self.nickserv_waiting = True
        except Exception as e:
            print(f"❌ Failed to IDENTIFY to {s.nickserv_nick}: {e}")

    def start_ignore_cleanup_if_needed(self):
        if not self.ignore_cleanup_started:
            if self.sql.sqlite_has_active_ignores(self.botId):
                print("⏳ Active ignores found. Starting cleanup thread...")
                self.thread_ignore_cleanup = ThreadWorker(target=self.cleanup_ignores, name="ignore_cleanup")
                self.thread_ignore_cleanup.start()
                self.ignore_cleanup_started = True

    def _message_worker(self):
        while True:
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
            print(f"⚠️ Flood detected from {host}, blacklisting...")
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

    # check logged users to deauth
    def _check_logged_users_loop(self):
        while True:
            if not self.logged_in_users:
                print("✅ No more logged-in users. Monitor thread exiting.")
                self.thread_check_logged_users_started = False
                break
            try:
                self.clean_logged_users()
            except Exception as e:
                print(f"⚠️ Exception in user cleaner thread: {e}")
            time.sleep(3600 * s.autoDeauthTime)

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
            print(f"🔒 Auto-logout (netsplit or left): userId={userId}")

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
                print(f"🔒 Logout on QUIT: {nick} (userId={userId}) from {formatted_host}")

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
        print(f"Error: Banned from channel {params[1]}")
        if params[1] not in self.notOnChannels:
            self.notOnChannels.append(params[1])
            self.addChannelToPendingList(params[1], f"banned on {params[1]}")
        self._schedule_rejoin(params[1])

    def irc_ERR_CHANNELISFULL(self, prefix, params):
        print(f"Error: Channel {params[1]} is full")
        if params[1] not in self.notOnChannels:
            self.notOnChannels.append(params[1])
            self.addChannelToPendingList(params[1], f"{params[1]} is full, cannot join")
        self._schedule_rejoin(params[1])

    def irc_ERR_BADCHANNELKEY(self, prefix, params):
        print(f"Error: Bad channel key for {params[1]}")
        if params[1] not in self.notOnChannels:
            self.notOnChannels.append(params[1])
            self.addChannelToPendingList(params[1], f"invalid channel key (+k) for {params[1]}")
        self._schedule_rejoin(params[1])

    def irc_ERR_INVITEONLYCHAN(self, prefix, params):
        print(f"Error: Invite-only channel {params[1]}")
        if params[1] not in self.notOnChannels:
            self.notOnChannels.append(params[1])
            self.addChannelToPendingList(params[1], f"invite only on {params[1]}")
        self._schedule_rejoin(params[1])

    def irc_ERR_NOSUCHCHANNEL(self, prefix, params):
        print(f"Error: No such channel {params[1]}")

    def irc_ERR_CANNOTSENDTOCHAN(self, prefix, params):
        print(f"Error: Cannot send to channel {params[1]}")

    def irc_INVITE(self, prefix, params):
        inviter = prefix.split('!')[0]
        print(f"Received invitation from {inviter} to join {params[1]}")
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

            # --- SAFE TTLCache access ---
            if not isinstance(self.user_cache, TTLCache):
                self.user_cache = TTLCache(maxlen=2000, ttl=6 * 3600)

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

            # 3) classic login + attach ALL nicks seen on the same host
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

                if not self.thread_check_logged_users_started:
                    self.thread_check_logged_users = ThreadWorker(
                        target=self._check_logged_users_loop, name="logged_users"
                    )
                    self.thread_check_logged_users.daemon = True
                    self.thread_check_logged_users.start()
                    self.thread_check_logged_users_started = True

            else:
                # 4) already logged on this host? just add the nick to the set
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
            print(f"Nickname {s.nickname} is already in use, switching to alternative nick..")
            self.nick_already_in_use = 1
            self.nickname = s.altnick
            if self.nickname != s.nickname:  # start doar dacă e alt nick
                self.thread_check_for_changed_nick.start()

    # check if logged
    def is_logged_in(self, userId, host):
        return userId in self.logged_in_users and host in self.logged_in_users[userId]["hosts"]

    # schedule for rejoining channel
    def _schedule_rejoin(self, channel):
        if channel not in self.rejoin_pending:
            return

        delay = self.rejoin_pending[channel]['delay']
        reactor.callLater(delay, self._attempt_rejoin, channel)

    # attempt rejoin
    def _attempt_rejoin(self, channel):
        if channel not in self.rejoin_pending:
            return

        entry = self.rejoin_pending[channel]
        entry['attempts'] += 1

        if entry['attempts'] > s.maxAttemptRejoin:
            print(f"❌ Failed to rejoin {channel} after {s.maxAttemptRejoin} attempts. Suspending the channel.")
            entry = self.rejoin_pending.get(channel, {})
            reason = entry.get('reason', 'unknown')
            self.sql.sqlite_auto_suspend_channel(channel, reason)
            self.notOnChannels.append(channel)
            del self.rejoin_pending[channel]
            return
        print(f"🔄 Rejoin attempt {entry['attempts']} for {channel}...")
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
                print(f"[WARN] Function cmd_{cmd['name']} not found in commands.py")

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
                print("✅ NickServ identification successful (via NOTICE).")
                self.nickserv_waiting = False
                self._join_channels()
            elif any(keyword in message.lower() for keyword in
                     ["password incorrect", "authentication failed", "is not a registered"]):
                print("❌ NickServ identification failed (via NOTICE).")
                self.nickserv_waiting = False
                print("➡️ Falling back to main channel only.")
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

    # CTCP version reply
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

    # restart bot process
    def restart(self, reason="Restarting..."):
        self.sendLine(f"QUIT :{reason}")
        reactor.callLater(2.0, self._restart_process)

    def _restart_process(self):
        reactor.stop()
        python = sys.executable
        os.execl(python, python, *sys.argv)

    # die process
    def die(self):
        reactor.stop()

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
        # host poate fi "ident@host" SAU "nick!ident@host"
        if "!" in host:
            _, rest = host.split("!", 1)  # rest = "ident@host"
        else:
            rest = host

        if "@" not in rest:
            return "Invalid format type"

        ident, host_only = rest.split("@", 1)

        formats = {
            1: f"*!*@{host_only}",
            2: f"*!{ident}@{host_only}",
            3: f"{nick}!{ident}@{host_only}",
            4: f"{nick}!*@*",
            5: f"*!{ident}@*",
        }

        if host_type > 0:
            return formats.get(host_type, "Invalid format type")
        else:
            return formats.get(getattr(s, "default_hostname", 2), "Invalid format type")

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
            active_nick_channel_pairs = set((c[0], c[1]) for c in self.channel_details)
            before = len(self.known_users)
            self.known_users.intersection_update(active_nick_channel_pairs)
            after = len(self.known_users)
            if before != after:
                print(f"🧹 Cleaned known_users: {before - after} entries removed")

    def rehash(self, channel, feedback, nick, host):
        import importlib
        import sys
        import gc

        result = self.check_command_access(channel, nick, host, '8', feedback)
        if not result:
            return

        try:
            self.send_message(feedback, "🔁 Reloading core modules...")

            # Clear old command references
            self.commands.clear()

            # Reload all important modules
            modules_to_reload = [
                'core.commands', 'core.update', 'core.SQL', 'core.Variables', 'settings', 'core.seen'
            ]
            for mod_name in modules_to_reload:
                mod = sys.modules.get(mod_name)
                if mod:
                    importlib.reload(mod)
                else:
                    self.send_message(feedback, f"⚠️ Module not loaded: {mod_name}")

            self.load_commands()

            self.sql.selfbot = self

            collected = gc.collect()

            self.send_message(feedback, f"✅ Reloaded successfully. 🧹 {collected} objects collected.")
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
                    print(f"🔄 Update found → {remote_version} > {local_version}")
                    update.update_from_github(self, "Auto-update")
                    self.restart("🔁 Auto-updated")
                    break
            except Exception as e:
                print(f"⚠️ Auto-update thread error: {e}")
            time.sleep(s.autoUpdateInterval * 60)


class BotFactory(protocol.ReconnectingClientFactory):
    def __init__(self, nickname, realname):
        self.nickname = nickname
        self.realname = realname
        self.away = s.away
        self.server = None
        self.port = None

    def buildProtocol(self, addr):
        global current_instance
        bot = Bot(self.nickname, self.realname)
        bot.factory = self
        bot.server = self.server
        bot.port = self.port
        current_instance = bot
        return bot

    def clientConnectionLost(self, connector, reason):
        global servers_order
        print(f"Connection lost: {reason}. Attempting to reconnect...")
        v.connected = False
        servers_order += 1
        if servers_order >= len(s.servers):
            servers_order = 0
        get_server = server_choose_to_connect()
        self.server = get_server[2]
        self.port = int(get_server[1])
        if s.ssl_use == 1:
            sslContext = ssl.ClientContextFactory()
            sslContext.method = ssl.PROTOCOL_TLSv1_2
            reactor.connectSSL(get_server[0], int(get_server[1]), BotFactory(s.nickname, s.realname), sslContext)
        else:
            reactor.connectTCP(get_server[0], int(get_server[1]), BotFactory(s.nickname, s.realname))


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
    # check if hostname is resolved as IPv6 or Ipv4
    resolved = 0
    if flag == -1:
        try:
            ip_address = socket.gethostbyname(host)
            resolved = 1
            return [0, ip_address, vserver]
        except socket.gaierror:
            pass
        if resolved == 0:
            try:
                results = socket.getaddrinfo(host, None, socket.AF_INET6)
                ipv6_addresses = [result[4][0] for result in results if result[1] == socket.SOCK_STREAM]
                return [1, ipv6_addresses[0], vserver]
            except socket.gaierror:
                return -1
        else:
            return -1
    else:
        return [flag, host, vserver]


def server_connect(first_server):  # connect to server
    global servers_order
    check_if_port = server_has_port(first_server)
    first_server = check_if_port[0]
    port = check_if_port[1]

    irc_server = host_resolve(first_server)
    if irc_server == -1:
        if not s.servers[servers_order]:
            return 0
        return 1
    else:
        t = irc_server[0]
        server = irc_server[1]
    if t == 1:
        ssocket = socket.AF_INET6
    else:
        ssocket = socket.AF_INET
    if len(Starter.old_source) > 0:
        with socket.socket(ssocket, socket.SOCK_STREAM) as sk:
            sk.bind((s.sourceIP, s.sourcePort))  # Bind to the desired source IP
            sk.connect((server, port))
    return [server, port, irc_server[2]]


def server_choose_to_connect():
    global servers_order
    valid_server = False
    while not valid_server:  # try to connect to one of the servers from the list
        first_server = s.servers[servers_order]
        connect = server_connect(first_server)
        if connect == 0:
            print(f"No more servers to try. Ending bot.")
            server = -1
            break
        elif connect == 1:
            print(f"Invalid server {first_server} from the list, trying another one..")
            servers_order += 1
            if servers_order >= len(s.servers):
                servers_order = 0
        else:
            valid_server = True
            server = connect
    return [server[0], server[1], server[2]]
