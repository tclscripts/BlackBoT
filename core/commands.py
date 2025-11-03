import time
from core import update
import settings as s
from core import Variables as v
import threading
import os
from datetime import datetime
from core.threading_utils import ThreadWorker
import core.seen as seen
import re, shlex

def cmd_ban(self, channel, feedback, nick, host, msg):
    """
    Usage:
      !ban <nick|mask|regex> [-regex] [-sticky] [-d 1h30m] [-reason "text"] [-g]
    Examples:
      !ban badguy
      !ban *!*@bad.example -sticky -d 7d -reason "spam"
      !ban ^.*@.*\\.evil\\.com$ -regex -reason "malware hosts"
    """

    # --- small helpers (local) ---
    def _parse_duration_to_seconds(spec: str):
        if not spec:
            return None
        total = 0
        for qty, unit in re.findall(r"(\d+)\s*(h|m|s)", spec.lower()):
            n = int(qty)
            if unit == "h": total += n*3600
            elif unit == "m": total += n*60
            else: total += n
        return total or None

    def _safe_compile_regex(pattern: str):
        if not pattern or len(pattern) > 300:
            raise ValueError("regex pattern too long or empty")
        return re.compile(pattern)

    def _mask_to_re(mask: str):
        esc = re.escape(mask)
        esc = esc.replace(r'\*', '.*').replace(r'\?', '.')
        return re.compile(r'^' + esc + r'$')

    def _normalize_banmask_for_mode(ban_mask: str, member: dict | None):
        # strip :realname if present; MODE +b nu acceptă realname
        if ':' in ban_mask and ('!' in ban_mask or '@' in ban_mask):
            return ban_mask.split(':', 1)[0]
        # dacă e regex, încearcă fallback la *!ident@host (dacă avem member)
        looks_regex = ban_mask.startswith('^') or ban_mask.endswith('$')
        if looks_regex and member:
            ident = member.get('ident') or '*'
            mhost = member.get('host') or '*'
            return f"*!{ident}@{mhost}"
        return ban_mask

    def _iter_channel_members(chan: str):
        """
        Iterează membrii din self.channel_details și normalizează la dict.
        Structura rândului: [channel, nick, ident, host, privileges, realname, userId]
        """
        rows = getattr(self, 'channel_details', [])
        for row in rows:
            if not isinstance(row, (list, tuple)):
                continue
            if not row or row[0] != chan:
                continue

            n = row[1] if len(row) > 1 else None
            ident = row[2] if len(row) > 2 else None
            h = row[3] if len(row) > 3 else None
            _priv = row[4] if len(row) > 4 else None
            real = row[5] if len(row) > 5 else None
            uid = row[6] if len(row) > 6 else None

            yield {
                'nick': n,
                'ident': ident,
                'host': h,
                'realname': real,
                'userId': uid,
                'priv': _priv
            }

    # --- access check by command id (33 = ban) ---
    if not self.check_command_access(channel, nick, host, '33', feedback):
        return

    if not msg:
        self.send_message(feedback, "Usage: !ban <nick|mask|regex> [-regex] [-sticky] [-d 1h30m] [-reason \"text\"] [-g]")
        return

    # tokenize (acceptă ghilimele la -reason)
    try:
        tokens = shlex.split(msg)
    except Exception:
        tokens = msg.split()

    target = tokens[0]
    opts = tokens[1:]

    # parse flags
    duration = None
    sticky = False
    reason = None
    is_regex = False
    is_global = False

    # map of options to consume
    i = 0
    while i < len(opts):
        t = opts[i].lower()
        if t in ('-g', '--global'):
            is_global = True
            i += 1
            continue
        if t.startswith('-d') or t.startswith('-t'):
            # -d10m | -d=10m | -d 10m
            if '=' in opts[i]:
                val = opts[i].split('=', 1)[1]
                i += 1
            elif len(opts[i]) > 2:
                val = opts[i][2:]
                i += 1
            else:
                val = opts[i+1] if i+1 < len(opts) else None
                i += 2
            duration = _parse_duration_to_seconds(val) if val else None
            continue
        if t in ('-sticky', '--sticky'):
            sticky = True
            i += 1
            continue
        if t in ('-regex', '--regex'):
            is_regex = True
            i += 1
            continue
        if t.startswith('-reason='):
            reason = opts[i].split('=', 1)[1]
            i += 1
            continue
        if t == '-reason' and i+1 < len(opts):
            reason = opts[i+1]
            i += 2
            continue
        # unknown opt → skip
        i += 1

    # decide storage scope
    store_channel = None if is_global or not (channel and channel.startswith('#')) else channel

    # build ban_mask
    ban_mask = target
    if is_regex:
        try:
            _ = _safe_compile_regex(ban_mask)
        except ValueError as e:
            self.send_message(feedback, f"Invalid regex: {e}")
            return
    else:
        # dacă nu arată ca hostmask, tratează ca nick -> încearcă să obții ident/host
        looks_like_mask = any(c in target for c in ('!', '@', ':', '*', '?'))
        if not looks_like_mask:
            # întâi caută în runtime (în orice canal)
            ident_host = self._get_any_ident_host(target) if hasattr(self, '_get_any_ident_host') else (None, None)
            ident, hostpart = ident_host if ident_host else (None, None)

            if not ident or not hostpart:
                # încearcă WHOIS sync dacă există
                info = None
                if hasattr(self, 'whois_sync'):
                    try:
                        info = self.whois_sync(target, timeout=5)
                    except Exception:
                        info = None
                if info:
                    ident = info.get('user') or info.get('ident') or '*'
                    hostpart = info.get('host') or '*'
                    realname = info.get('realname') or ''
                else:
                    realname = ''

            else:
                # putem avea realname în cache (din WHO/RPL_WHOREPLY) — vezi channel_details
                realname = None
                # caută în channel_details ca să găsim realname
                for m in _iter_channel_members(channel) if channel and channel.startswith('#') else []:
                    if m['nick'].lower() == target.lower():
                        realname = m.get('realname')
                        break

            # construim masca clasică
            ident = ident or '*'
            hostpart = hostpart or '*'
            ban_mask = f"{target}!{ident}@{hostpart}"
            if realname:
                ban_mask = f"{ban_mask}:{realname}"
        # altfel, userul a dat deja o mască → o folosim ca atare

    # inserare în DB
    setter_userId = None
    try:
        info = self.sql.sqlite_handle(self.botId, nick, host)  # (userId, ...)
        setter_userId = info[0] if info else None
    except Exception:
        setter_userId = None

    created_id = self.sql.sqlite_add_ban(
        botId=self.botId,
        channel=store_channel,                 # None => BANS_GLOBAL
        setter_userId=setter_userId,
        setter_nick=nick,
        ban_mask=ban_mask,
        ban_type=('regex' if is_regex else 'mask'),
        sticky=sticky,
        reason=reason,
        duration_seconds=duration
    )

    # aplică imediat în canalul curent (dacă suntem într-un #channel)
    applied = 0
    if channel and channel.startswith('#'):
        # pregătește matcher
        if is_regex:
            try:
                rx = _safe_compile_regex(ban_mask)
            except ValueError:
                rx = None
        else:
            rx = _mask_to_re(ban_mask)

        for m in _iter_channel_members(channel):
            m_full = f"{m['nick']}!{m['ident']}@{m['host']}"
            m_full_real = f"{m_full}:{m['realname']}" if m.get('realname') else m_full

            matched = False
            if is_regex and rx:
                if rx.search(m_full_real) or rx.search(m_full):
                    matched = True
            elif not is_regex and rx:
                if rx.match(m_full_real) or rx.match(m_full):
                    matched = True

            if matched:
                mode_mask = _normalize_banmask_for_mode(ban_mask, m)
                try:
                    self.sendLine(f"MODE {channel} +b {mode_mask}")
                    self.sendLine(f"KICK {channel} {m['nick']} :Banned ({reason or 'no reason'})")
                    applied += 1
                except Exception:
                    pass

    # marchează aplicarea
    try:
        table = 'BANS_LOCAL' if store_channel else 'BANS_GLOBAL'
        self.sql.sqlite_mark_ban_applied(table, created_id)
    except Exception:
        pass

    scope = "global" if store_channel is None else f"on {store_channel}"
    self.send_message(feedback, f"✅ Ban stored (id={created_id}, {scope}). Applied to {applied} users immediately.")


def cmd_help(self, channel, feedback, nick, host, msg):
    msg = (msg or "").strip()

    override_channel = None
    tokens = msg.split()
    # 1) !help #chan ...  (respectăm override-ul explicit)
    if tokens and tokens[0].startswith("#") and channel.lower() == self.nickname.lower():
        override_channel = tokens[0]
        tokens = tokens[1:]

    # 2) Separator opțional: !help sep=,
    sep = " ; "
    for t in list(tokens):
        if t.lower().startswith("sep="):
            raw = t[4:]
            if raw:
                sep_char = raw[0]
                sep = f" {sep_char} "
            tokens.remove(t)
            break

    q = " ".join(tokens).strip().lower() if tokens else ""

    # ---------- PM/DCC local channel guess ----------
    # Dacă suntem în PM/DCC (channel == numele botului) și NU avem override,
    # încercăm să ghicim un canal comun cu userul.
    guessed_channel = None
    if not override_channel and channel.lower() == self.nickname.lower():
        try:
            user_chans = {row[0] for row in self.channel_details
                          if (row[1] or "").lower() == nick.lower()}
            bot_chans = set(self.channels or [])
            commons = sorted(user_chans & bot_chans, key=str.lower)
            if len(commons) == 1:
                guessed_channel = commons[0]
        except Exception:
            pass

    local_channel = override_channel or guessed_channel or channel

    # obține userId (hostul e deja „îmbunătățit” de get_hostname pentru DCC)
    handle_info = self.sql.sqlite_handle(self.botId, nick, host)
    userId = handle_info[0] if handle_info else None

    cmds = self.commands or []

    # helper acces
    def _has_access(cmd, local=False):
        flags = (cmd.get("flags") or "").strip()
        # Public
        if not flags or flags == "-":
            return True
        if not userId:
            return False
        if local and local_channel and str(local_channel).startswith("#"):
            return self.sql.sqlite_has_access_flags(self.botId, userId, flags, channel=local_channel)
        # global
        return self.sql.sqlite_has_access_flags(self.botId, userId, flags, channel=None)

    # Dacă s-a cerut detaliu pentru o comandă: !help op / help op
    if q and not q.startswith("#"):
        target = next((c for c in cmds if c.get("name", "").lower() == q), None)
        if not target:
            self.send_message(feedback, f"❓ '{q}' command doesn't exist.")
            return
        if not (_has_access(target, local=True) or _has_access(target, local=False)):
            self.send_message(feedback, f"⛔ You don't have access to '{target['name']}'.")
            return
        descr = target.get("description", "No description")
        for i, line in enumerate(descr.splitlines()):
            prefix = f"ℹ️ {s.char}{target['name']} — " if i == 0 else "   "
            self.send_message(feedback, prefix + line.strip())
        return

    public_names, local_names, global_names = [], [], []
    for c in cmds:
        name = c.get("name")
        if not name:
            continue
        flags = (c.get("flags") or "").strip()
        entry = f"{s.char}{name}"

        # Public
        if not flags or flags == "-":
            public_names.append(entry)
            continue
        # Local (pe canalul dedus/explicit)
        if _has_access(c, local=True):
            local_names.append(entry)
            continue
        # Global
        if _has_access(c, local=False):
            global_names.append(entry)

    if not (public_names or local_names or global_names):
        return

    if public_names:
        line = "📣 Public: " + sep.join(sorted(set(public_names), key=str.lower))
        for part in self.split_irc_message_parts([line], separator=""):
            self.send_message(feedback, part)

    # Afișăm Local dacă avem un #canal valid
    if local_channel and str(local_channel).startswith("#") and local_names:
        line = f"🏷️ Local ({local_channel}): " + sep.join(sorted(set(local_names), key=str.lower))
        for part in self.split_irc_message_parts([line], separator=""):
            self.send_message(feedback, part)
    else:
        # în PM/DCC, dacă există mai multe canale comune și userul are și comenzi locale,
        # oferim un hint explicit
        if channel.lower() == self.nickname.lower():
            try:
                user_chans = {row[0] for row in self.channel_details
                              if (row[1] or "").lower() == nick.lower()}
                bot_chans = set(self.channels or [])
                commons = sorted(user_chans & bot_chans, key=str.lower)
                if len(commons) > 1 and local_names:
                    self.send_message(
                        feedback,
                        f"ℹ️ For channel-scoped commands, use: !help #channel  (common: {', '.join(commons)})"
                    )
            except Exception:
                pass

    if global_names:
        line = "🌐 Global: " + sep.join(sorted(set(global_names), key=str.lower))
        for part in self.split_irc_message_parts([line], separator=""):
            self.send_message(feedback, part)


def cmd_status(self, channel, feedback, nick, host, msg):
    import psutil
    import platform
    result = self.check_command_access(channel, nick, host, '30', feedback)
    if not result:
        return

    now = time.time()
    process = psutil.Process(os.getpid())

    # Basic system info
    uptime = now - process.create_time()
    formatted_uptime = self.format_duration(uptime)
    cpu_percent = process.cpu_percent(interval=None)
    mem_info = process.memory_info()
    rss_mb = mem_info.rss / (1024 * 1024)
    total_mem_mb = psutil.virtual_memory().total / (1024 * 1024)
    system = platform.system()
    release = platform.release()
    cpu_model = platform.processor()

    # Thread info
    threads = threading.enumerate()
    num_threads = len(threads)
    thread_names = ", ".join(t.name for t in threads)

    # Internal memory states
    users_logged = len(self.logged_in_users)
    known_users = len(self.known_users)
    user_cache = len(self.user_cache)
    pending_rejoins = len(self.rejoin_pending)
    channel_info = len(self.channel_details)

    # Compose message
    msg_lines = [
        f"📊 *Advanced Status Report*",
        f"🔢 Threads: {num_threads} — {thread_names}",
        f"👥 Logged Users: {users_logged} | 🧠 Known: {known_users} | 🔐 Cache: {user_cache}",
        f"🔁 Rejoin Queue: {pending_rejoins} | 📺 Channel Details: {channel_info}",
        f"🧠 RAM: {rss_mb:.2f}MB / {total_mem_mb:.0f}MB | 🔄 CPU: {cpu_percent:.1f}%",
        f"💻 System: {system} {release} | CPU: {cpu_model}",
        f"⏱️ Uptime: {formatted_uptime}",
    ]

    for line in msg_lines:
        self.send_message(feedback, line)


def cmd_myset(self, channel, feedback, nick, host, msg):
    parts = msg.strip().split(None, 1)

    host_mask = self.get_hostname(nick, host, 0)

    info = self.sql.sqlite_handle(self.botId, nick, host_mask)
    userId = info[0] if info else self.get_logged_in_user_by_host(host_mask)

    if not userId or not self.is_logged_in(userId, host_mask):
        return

    if len(parts) < 2:
        self.send_message(feedback, f"⚠️ Usage: {s.char}myset <{'|'.join(v.users_settings_change)}> <value>")
        return

    setting, value = parts[0].lower(), parts[1].strip()

    if setting not in v.users_settings_change:
        self.send_message(feedback, f"❌ Invalid setting. Allowed: {', '.join(v.users_settings_change)}")
        return

    if setting == "autologin":
        value = value.lower()
        if value not in ["on", "off"]:
            self.send_message(feedback, "⚠️ autologin must be 'on' or 'off'")
            return
        value = "1" if value == "on" else "0"

    self.sql.sqlite_update_user_setting(self.botId, userId, setting, value)
    self.send_message(feedback, f"✅ Setting `{setting}` updated to: {value}")


def cmd_deauth(self, channel, feedback, nick, host, msg):
    host = self.get_hostname(nick, host, 0)

    userId = self.get_logged_in_user_by_host(host)
    if not userId:
        self.send_message(feedback, "ℹ️ You are not currently authenticated.")
        return

    if userId in self.logged_in_users:
        if host in self.logged_in_users[userId]["hosts"]:
            self.logged_in_users[userId]["hosts"].remove(host)
            if not self.logged_in_users[userId]["hosts"]:
                del self.logged_in_users[userId]
            self.send_message(feedback, "🔓 You have been deauthenticated successfully.")
            print(f"🔓 Manual logout: {nick} (userId={userId}) from {host}")
        else:
            self.send_message(feedback, "ℹ️ You are not logged in from this host.")
    else:
        self.send_message(feedback, "ℹ️ You are not currently authenticated.")


def cmd_update(self, channel, feedback, nick, host, msg):
    import core.update as update  # ca să fim siguri că modulul e accesibil

    result = self.check_command_access(channel, nick, host, '26', feedback)
    if not result:
        return

    arg = (msg or "").strip().lower()

    if arg == "check":
        local_version = update.read_local_version()
        remote_version = update.fetch_remote_version()
        if not remote_version:
            self.send_message(feedback, "❌ Unable to fetch remote version.")
            return
        if remote_version != local_version:
            self.send_message(feedback, f"🔄 Update available: {remote_version} (current: {local_version})")
        else:
            self.send_message(feedback, f"✅ Already up to date (version {local_version})")
        return

    elif arg == "start":
        local_version = update.read_local_version()
        remote_version = update.fetch_remote_version()
        if not remote_version:
            self.send_message(feedback, "❌ Unable to fetch remote version, update aborted.")
            return

        if remote_version == local_version:
            self.send_message(feedback, f"✅ Already up to date (version {local_version}). No update needed.")
            return

        # altfel pornește efectiv update-ul
        self.send_message(feedback, f"🔁 Starting update → {remote_version} (current: {local_version}) ...")
        ThreadWorker(target=lambda: update.update_from_github(self, feedback), name="manual_update").start()
        return

    else:
        self.send_message(feedback, f"⚠️ Usage: {s.char}update check | {s.char}update start")


def cmd_auth(self, channel, feedback, nick, host, msg):
    import bcrypt
    from twisted.internet import reactor

    parts = msg.strip().split()
    if not parts:
        self.send_message(feedback, "⚠️ Usage: auth <username> <password>")
        return

    host = self.get_hostname(nick, host, 0)

    # Save current host to trusted hosts
    if parts[0].lower() == "save":
        userId = self.get_logged_in_user_by_host(host)
        if not userId:
            self.send_message(feedback, "❌ You are not logged in from this host.")
            return
        if self.sql.sqlite_check_user_host_exists(self.botId, userId, host):
            self.send_message(feedback, "ℹ️ This host is already saved as one of your trusted logins.")
        else:
            self.sql.sqlite_add_user_host(self.botId, userId, host)
            self.send_message(feedback, f"✅ Your current host `{host}` has been saved.")
        return

    # Must auth in PM
    if feedback != nick:
        self.send_message(feedback, "❌ You must authenticate in private message.")
        return

    # Resolve username/userId + stored_hash
    if len(parts) == 1:
        password = parts[0]
        info = self.sql.sqlite_handle(self.botId, nick, host)
        if not info:
            self.send_message(feedback, "❌ No user found for this host. Use full `auth <user> <pass>`.")
            return
        userId = info[0]
        username = info[1]
        stored_hash = self.sql.sqlite_get_user_password(userId)
        if not stored_hash:
            self.send_message(
                nick,
                "🔐 You are a valid user but with no password set. Please use `pass <password>` in private to secure your account. "
                "After that use `auth [username] <password> to authenticate.`"
            )
            return
    elif len(parts) >= 2:
        username = parts[0]
        password = " ".join(parts[1:])
        userId = self.sql.sqlite_get_user_id_by_name(self.botId, username)
        if not userId:
            self.send_message(feedback, "❌ Invalid username or password.")
            return
        stored_hash = self.sql.sqlite_get_user_password(userId)
        if not stored_hash:
            self.send_message(
                nick,
                "🔐 You are a valid user but with no password set. Please use `pass <password>` in private to secure your account. "
                "After that use `auth <password> to authenticate.`"
            )
            return
    else:
        self.send_message(feedback, "⚠️ Usage: auth <username> <password>")
        return

    # Policy checks
    if self.multiple_logins == 0:
        self.send_message(feedback, "⚠️ Multiple logins are not allowed.")
        return

    if self.is_logged_in(userId, host):
        self.send_message(feedback, "🔓 You are already logged in from this host.")
        return

    # Offload bcrypt to a background thread to avoid blocking IRC reactor
    def _do_auth_check():
        success_local = False
        try:
            ok = stored_hash and bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8"))
            if ok:
                # Update in-memory login state
                if userId not in self.logged_in_users:
                    self.logged_in_users[userId] = {"hosts": [], "nick": nick}
                if host not in self.logged_in_users[userId]["hosts"]:
                    self.logged_in_users[userId]["hosts"].append(host)
                self.logged_in_users[userId]["nick"] = nick

                # Cache (TTLCache if available)
                key = (nick, host)
                if hasattr(self.user_cache, "set"):
                    self.user_cache.set(key, userId)
                else:
                    self.user_cache[key] = userId

                success_local = True
                reactor.callFromThread(
                    self.send_message,
                    feedback,
                    f"✅ Welcome, {username}. You are now logged in from {host}."
                )

                # Start logged_users monitor if not started
                if not getattr(self, "thread_check_logged_users_started", False):
                    self.thread_check_logged_users = ThreadWorker(
                        target=self._check_logged_users_loop, name="logged_users"
                    )
                    self.thread_check_logged_users.daemon = True
                    self.thread_check_logged_users.start()
                    self.thread_check_logged_users_started = True
            else:
                reactor.callFromThread(self.send_message, feedback, "❌ Incorrect username or password.")
        finally:
            # Persist login attempt result
            self.sql.sqlite_log_login_attempt(self.botId, nick, host, userId, success_local)

    ThreadWorker(target=_do_auth_check, name=f"auth_{nick}").start()


def cmd_newpass(self, channel, feedback, nick, host, msg):
    import bcrypt

    info = self.sql.sqlite_handle(self.botId, nick, host)
    if not info:
        return
    userId = info[0]
    if feedback != nick:
        self.send_message(feedback, "❌ You must change your password in a private message.")
        return

    if not msg or len(msg.strip()) < 4:
        self.send_message(feedback, "⚠️ Usage: newpass <your new password>")
        return

    current_pass = self.sql.sqlite_get_user_password(userId)

    if not current_pass:
        self.send_message(feedback, "⚠️ You don't have a password yet. Use `pass <password>` to set one.")
        return

    new_password = msg.strip().encode("utf-8")
    hashed = bcrypt.hashpw(new_password, bcrypt.gensalt())
    self.sql.sqlite_set_password(userId, hashed.decode("utf-8"))

    self.send_message(feedback, "✅ Your password has been updated.")


def cmd_pass(self, channel, feedback, nick, host, msg):
    import bcrypt

    info = self.sql.sqlite_handle(self.botId, nick, host)
    if not info:
        return
    userId = info[0]
    if feedback != nick:
        self.send_message(feedback, "❌ You must set your password in a private message.")
        return

    if not msg or len(msg.strip()) < 4:
        self.send_message(feedback, "⚠️ Usage: pass <your password>")
        return

    current_pass = self.sql.sqlite_get_user_password(info[0])

    if current_pass:
        self.send_message(feedback, "❌ You already have a password set. Use `newpass <new>` to change it.")
        return

    password = msg.strip().encode("utf-8")
    hashed = bcrypt.hashpw(password, bcrypt.gensalt())
    self.sql.sqlite_set_password(userId, hashed.decode("utf-8"))

    self.send_message(feedback, "✅ Password successfully set.")


def cmd_uptime(self, channel, feedback, nick, host, msg):
    import psutil
    import platform
    result = self.check_command_access(channel, nick, host, '10', feedback)
    if not result:
        return
    now = time.time()
    process = psutil.Process(os.getpid())

    # Times
    bot_uptime = self.format_duration(now - process.create_time())
    system_uptime = self.format_duration(now - psutil.boot_time())

    max_conn_time, max_uptime = self.sql.sqlite_get_bot_stats(self.botId)

    # RAM and CPU
    mem_info = process.memory_info()
    rss_mb = mem_info.rss / (1024 * 1024)
    cpu_percent = process.cpu_percent(interval=None)

    # total RAM
    total_mem_mb = psutil.virtual_memory().total / (1024 * 1024)

    # System
    system = platform.system()
    release = platform.release()
    cpu_model = platform.processor()

    msg = (
        f"🕒 Bot uptime: {bot_uptime} | 🖥️ System uptime: {system_uptime}\n"
        f"\n📈 Max uptime: {self.format_duration(max_uptime)} | 🔌 Max connect time: {self.format_duration(max_conn_time)}\n"
        f"📊 RAM: {rss_mb:.2f}MB / {total_mem_mb:.0f}MB | 🔄 CPU: {cpu_percent:.1f}%\n"
        f"💻 System: {system} {release} | CPU: {cpu_model}"
    )

    self.send_message(feedback, msg)


def cmd_version (self, channel, feedback, nick, host, msg):
    result = self.check_command_access(channel, nick, host, '20', feedback)
    if not result:
        return
    from core.update import read_local_version
    self.send_message(feedback, f"✨ You're running BlackBoT v{read_local_version()} — Powered by Python 🐍")


def cmd_channels(self, channel, feedback, nick, host, msg):
    result = self.check_command_access(channel, nick, host, '11', feedback)
    if not result:
        return

    channels = self.sql.sqlite3_channels(self.botId)
    if not channels:
        self.send_message(feedback, "🔍 No registered channels.")
        return
    joined_lower = {c.lower() for c in (self.channels or [])}

    entries = []
    for chan_row in channels:
        chan = chan_row[0]
        flags = []
        if self.sql.sqlite_is_channel_suspended(chan):
            flags.append("🔒suspended")
        else:
            on_channel = chan.lower() in joined_lower
            if not on_channel:
                flags.append("❌offline")
            else:
                # 3) e op doar dacă e pe canal
                if not self.user_is_op(self.nickname, chan):
                    flags.append("⚠️no-op")

        status = ",".join(flags) if flags else "✅ok"
        entries.append(f"{chan}:{status}")

    for msg_part in self.split_irc_message_parts(entries):
        self.send_message(feedback, msg_part)


def cmd_say(self, channel, feedback, nick, host, msg):  # say command
    result = self.check_command_access(channel, nick, host, '4', feedback)
    if not result:
        return
    if not result:
        return
    self.send_message(feedback, msg)


def cmd_addchan(self, channel, feedback, nick, host, msg):
    result = self.check_command_access(channel, nick, host, '2', feedback)
    if not result:
        return
    sql_instance = result['sql']
    checkIfValid = sql_instance.sqlite_validchan(channel)
    if checkIfValid:
        self.send_message(feedback, "The channel '{}' is already added".format(channel))
    else:
        self.join_channel(channel)  # join channel.
        self.channels.append(channel)
        sql_instance.sqlite3_addchan(channel, nick, self.botId)
        self.send_message(feedback, "Added channel '{}' in my database".format(channel))


def cmd_delchan(self, channel, feedback, nick, host, msg):
    result = self.check_command_access(channel, nick, host, '3', feedback)
    if not result:
        return
    sql_instance = result['sql']

    try:
        canonical = self.sql.sqlite_find_channel_case_insensitive(self.botId, channel) or channel
    except Exception:
        canonical = channel

    if not sql_instance.sqlite_validchan(canonical):
        self.send_message(feedback, f"The channel '{channel}' is not added in my database.")
        return

    try:
        if any(c.lower() == canonical.lower() for c in (self.channels or [])):
            self.part(canonical)
    except Exception:
        pass

    # 4) Curăță listele interne în mod case-insensitive
    def _remove_case_insensitive(lst, value):
        if not lst:
            return
        real = next((x for x in lst if x.lower() == value.lower()), None)
        if real:
            try:
                lst.remove(real)
            except ValueError:
                pass

    _remove_case_insensitive(self.channels, canonical)
    _remove_case_insensitive(self.notOnChannels, canonical)

    if self.channel_details:
        self.channel_details = [arr for arr in self.channel_details if (arr[0] or "").lower() != canonical.lower()]

    try:
        sql_instance.sqlite3_delchan(canonical, self.botId)
    except Exception as e:
        self.send_message(feedback, f"DB error while removing '{canonical}': {e}")
        return

    self.send_message(feedback, f"Removed channel '{canonical}' from my database")



def cmd_jump(self, channel, feedback, nick, host, msg):  # jump command
    result = self.check_command_access(channel, nick, host, '5', feedback)
    if not result:
        return

    self.send_message(feedback, "🔀 Jumping to next server...")

    try:
        self.sendLine(b"QUIT :jump")
    except Exception:
        pass

    from twisted.internet import reactor
    reactor.callLater(1.0, lambda: getattr(self.transport, "loseConnection", lambda: None)())


def cmd_hello(self, channel, feedback, nick, host, msg):
    if not self.unbind_hello:
        sql_instance = self.sql
        userId = sql_instance.sqlite_add_user(self.botId, nick, '')
        accessId = sql_instance.sqlite_get_access_id('N')
        hostname = self.get_hostname(nick, host, 0)
        sql_instance.sqlite_add_user_host(self.botId, userId, hostname)
        sql_instance.sqlite_add_global_access(self.botId, userId, accessId, self.nickname)
        self.msg(nick,
                 f"👋 Hello, {nick}! You are now recognized as my owner (God mode).")

        self.msg(nick,
                 f"🔑 Please set your password to protect this status:\n"
                 f"   /msg {self.nickname} pass <your_password>\n"
                 f"(you can later change it with: /msg {self.nickname} newpass <new_password>)")

        self.msg(nick,
                 f"📧 To enable password recovery, register an email address:\n"
                 f"   /msg {self.nickname} myset email <your_email>\n"
                 f"(make sure email delivery is configured correctly in settings)")

        self.msg(nick,
                 f"ℹ️ Use {s.char}help to see available commands based on your access level.")
        self.unbind_hello = True


def cmd_op(self, channel, feedback, nick, host, msg):
    self._handle_user_mode(channel, feedback, nick, host, msg, mode="o", enable=True, flag='8')


def cmd_deop(self, channel, feedback, nick, host, msg):
    self._handle_user_mode(channel, feedback, nick, host, msg, mode="o", enable=False, flag='16')


def cmd_voice(self, channel, feedback, nick, host, msg):
    self._handle_user_mode(channel, feedback, nick, host, msg, mode="v", enable=True, flag='15')


def cmd_devoice(self, channel, feedback, nick, host, msg):
    self._handle_user_mode(channel, feedback, nick, host, msg, mode="v", enable=False, flag='17')


def cmd_hop(self, channel, feedback, nick, host, msg):
    self._handle_user_mode(channel, feedback, nick, host, msg, mode="h", enable=True, flag='18')


def cmd_hdeop(self, channel, feedback, nick, host, msg):
    self._handle_user_mode(channel, feedback, nick, host, msg, mode="h", enable=False, flag='19')


def cmd_restart(self, channel, feedback, nick, host, msg):
    result = self.check_command_access(channel, nick, host, '6', feedback)
    if not result:
        return
    self.send_message(feedback, "Restarting...")
    threading.Timer(3, self.restart).start()


def cmd_cycle(self, channel, feedback, nick, host, msg):
    if channel.lower() == self.nickname.lower():
        self.send_message(feedback, f"⚠️ Usage: {s.char}cycle <#channel>")
        return
    result = self.check_command_access(channel, nick, host, '9', feedback)
    if not result:
        return
    sql_instance = result['sql']
    checkIfValid = sql_instance.sqlite_validchan(channel)
    if checkIfValid:
        if channel.lower() in (c.lower() for c in self.channels):
            self.part(channel, "Be back in 3 seconds")
            self.addChannelToPendingList(channel, f'cycle command by {nick}')
            self._schedule_rejoin(channel)


def cmd_rehash(self, channel, feedback, nick, host, msg):
    self.rehash(channel, feedback, nick, host)


def cmd_die(self, channel, feedback, nick, host, msg):
    result = self.check_command_access(channel, nick, host, '7', feedback)
    if not result:
        return
    self.send_message(feedback, "This is my end...")
    threading.Timer(3, self.die).start()


def cmd_add(self, channel, feedback, nick, host, msg):
    global exists
    result = self.check_command_access(channel, nick, host, '21', feedback)
    if not result:
        return

    args = msg.strip().split()
    if len(args) < 2:
        self.send_message(feedback, f"⚠️ Usage: {s.char}add <role> <nick1> <nick2> ...")
        return

    role = args[0].lower()
    role_map = {
        'voice': 'V',
        'op': 'O',
        'admin': 'A',
        'manager': 'M',
        'master': 'm',
        'owner': 'n',
        'boss': 'N'
    }

    if role not in role_map:
        self.send_message(feedback, f"❌ Invalid role '{role}'. Available roles: {', '.join(role_map)}")
        return

    flag = role_map[role]
    targets = args[1:]

    if not targets:
        self.send_message(feedback, "⚠️ Please specify at least one user.")
        return

    result = self.check_command_access(channel, nick, host, '9', feedback)
    if not result:
        return

    userId = result["userId"]
    issuer_flag = self.sql.sqlite_get_max_flag(self.botId, userId, channel)

    if not issuer_flag:
        self.send_message(feedback, "❌ Could not determine your access level.")
        return

    hierarchy = ['V', 'O', 'A', 'M', 'm', 'n', 'N']
    try:
        issuer_index = hierarchy.index(issuer_flag)
        target_index = hierarchy.index(flag)
    except ValueError:
        self.send_message(feedback, "❌ Internal hierarchy mismatch.")
        return

    if flag == 'N' and issuer_flag != 'n':
        self.send_message(feedback, "⛔ Only an OWNER (n) can grant BOSS OWNER (N).")
        return

    if target_index >= issuer_index:
        self.send_message(feedback, f"⛔ You cannot grant '{role}' access (same or higher than your own level).")
        return

    granted = []
    updated = []

    for target in targets:
        user_info = next((u for u in self.channel_details if u[1].lower() == target.lower()), None)
        if not user_info:
            self.send_message(feedback, f"⚠️ User '{target}' not found on this channel.")
            continue

        tnick = user_info[1]
        tident = user_info[2]
        thost = user_info[3]
        thost_mask = self.get_hostname(tnick, f"{tident}@{thost}", 0)

        db_info = self.sql.sqlite_handle(self.botId, tnick, f"{thost_mask}")
        exists = db_info
        target_userId = db_info[0] if db_info else self.sql.sqlite_create_user_with_host(self.botId, tnick, thost_mask)

        target_flag_global = self.sql.sqlite_get_max_flag(self.botId, target_userId)
        target_flag_local = self.sql.sqlite_get_max_flag(self.botId, target_userId, channel)
        all_flags = [f for f in [target_flag_global, target_flag_local] if f]

        if all_flags:
            try:
                target_flag = min(all_flags, key=lambda f: hierarchy.index(f))
                existing_index = hierarchy.index(target_flag)
                if existing_index >= issuer_index:
                    self.send_message(feedback,
                        f"⛔ Cannot change access for '{tnick}' (has same or higher level: {target_flag}).")
                    continue
            except ValueError:
                pass

        access_id = self.sql.sqlite_get_access_id(flag)

        if flag in ['n', 'N']:
            current_flag = self.sql.sqlite_get_max_flag(self.botId, target_userId)
            if current_flag:
                self.sql.sqlite_add_global_access(self.botId, target_userId, access_id, nick)
                updated.append(f"{tnick} ({thost_mask})")
            else:
                self.sql.sqlite_add_global_access(self.botId, target_userId, access_id, nick)
                granted.append(f"{tnick} ({thost_mask})")
        else:
            current_flag = self.sql.sqlite_get_max_flag(self.botId, target_userId, channel)
            if current_flag:
                self.sql.sqlite_add_channel_access(self.botId, channel, target_userId, access_id, nick)
                updated.append(f"{tnick} ({thost_mask})")
            else:
                self.sql.sqlite_add_channel_access(self.botId, channel, target_userId, access_id, nick)
                granted.append(f"{tnick} ({thost_mask})")

    if granted:
        self.send_message(feedback, f"✅ Granted {role} access to: {', '.join(granted)}")
        if not exists:
            for entry in granted:
                nick_part = entry.split(" ")[0]
                self.send_message(nick_part,
                              f"🔐 You've been granted '{role.upper()}' access on {channel}.\n"
                              f"📌 Please set your password using: pass <your_password>\n"
                              f"🟢 Then log in with: auth <your_username> <your_password>, After that you will be automatically loged in every time.\n")
    if updated:
        self.send_message(feedback, f"🔄 Updated access to {role} for: {', '.join(updated)}")


def cmd_userlist(self, channel, feedback, nick, host, msg):
    access = self.check_command_access(channel, nick, host, '22', feedback)
    if not access:
        return

    userId = access['userId']
    botId = self.botId

    args = msg.strip().split()
    target_channel = channel
    show_global = False

    if args:
        if args[0].lower() == "global":
            show_global = True
        elif args[0].startswith("#"):
            target_channel = args[0]
            if len(args) > 1 and args[1].lower() == "global":
                show_global = True

    user_flag = self.sql.sqlite_get_max_flag(botId, userId)
    is_global_admin = user_flag in ['N', 'n']

    if show_global:
        if not is_global_admin:
            self.send_message(feedback, "❌ Only users with global access can view global access list.")
            return
        users = self.sql.sqlite_list_all_global_access(botId)
        label = "🌍 Global Access List"
    else:
        if target_channel != channel and not is_global_admin:
            self.send_message(feedback, f"❌ You are not allowed to view access for {target_channel}.")
            return

        users = self.sql.sqlite_list_all_channel_access(botId, target_channel)
        label = f"📋 Access List for {target_channel}"

    if not users:
        self.send_message(feedback, f"🔍 No users with access found.")
        return

    lines = [f"{label}:"]
    for username, flag in users:
        lines.append(f"• {username} ({flag})")

    for part in self.split_irc_message_parts(lines[1:], separator="\n"):
        self.send_message(feedback, f"{lines[0]}\n{part}")


def cmd_delacc(self, channel, feedback, nick, host, msg):
    result = self.check_command_access(channel, nick, host, '23', feedback)
    if not result:
        return

    args = msg.strip().split()
    if not args:
        self.send_message(feedback, f"⚠️ Usage: {s.char}del <nick1> <nick2> ...")
        return

    sql = result["sql"]
    userId = result["userId"]
    issuer_flag = self.sql.sqlite_get_max_flag(self.botId, userId, channel)

    if not issuer_flag:
        self.send_message(feedback, "❌ Could not determine your access level.")
        return

    hierarchy = ['V', 'O', 'A', 'M', 'm', 'n', 'N']
    try:
        issuer_index = hierarchy.index(issuer_flag)
    except ValueError:
        self.send_message(feedback, "❌ Unknown issuer access level.")
        return

    removed = []
    skipped = []

    for target in args:
        user_info = next((u for u in self.channel_details if u[1].lower() == target.lower()), None)

        if not user_info:
            self.send_message(feedback, f"⚠️ User '{target}' not found on this channel.")
            continue

        tnick = user_info[1]
        tident = user_info[2]
        thost = user_info[3]
        thost_mask = self.get_hostname(tnick, f"{tident}@{thost}", 0)

        db_info = self.sql.sqlite_handle(self.botId, tnick, thost_mask)
        if not db_info:
            self.send_message(feedback, f"ℹ️ User '{tnick}' is not registered.")
            continue

        target_userId = db_info[0]
        target_flag = self.sql.sqlite_get_max_flag(self.botId, target_userId, channel)

        if not target_flag:
            self.send_message(feedback, f"ℹ️ User '{tnick}' has no access.")
            continue

        try:
            target_index = hierarchy.index(target_flag)
        except ValueError:
            target_index = 999

        if target_index >= issuer_index:
            skipped.append(tnick)
            continue

        if target_flag in ['n', 'N']:
            self.sql.sqlite_delete_global_access(self.botId, target_userId)
        else:
            self.sql.sqlite_delete_channel_access(self.botId, target_userId, channel)

        removed.append(tnick)

    if removed:
        self.send_message(feedback, f"🗑️ Removed access for: {', '.join(removed)}")
    if skipped:
        self.send_message(feedback, f"⛔ Skipped (higher or equal access): {', '.join(skipped)}")


def cmd_del(self, channel, feedback, nick, host, msg):
    result = self.check_command_access(channel, nick, host, '24', feedback)
    if not result:
        return

    args = msg.strip().split()
    if not args:
        self.send_message(feedback, f"⚠️ Usage: {s.char}del <username>")
        return

    userId = result["userId"]
    issuer_flag = self.sql.sqlite_get_max_flag(self.botId, userId, channel)

    if not issuer_flag:
        self.send_message(feedback, "❌ Could not determine your access level.")
        return

    hierarchy = ['V', 'O', 'A', 'M', 'm', 'n', 'N']
    try:
        issuer_index = hierarchy.index(issuer_flag)
    except ValueError:
        self.send_message(feedback, "❌ Unknown issuer access level.")
        return

    for username in args:
        target_id = self.sql.sqlite_get_user_id_by_name(self.botId, username)
        if not target_id:
            self.send_message(feedback, f"ℹ️ User '{username}' not found.")
            continue

        target_flag = self.sql.sqlite_get_max_flag(self.botId, target_id, channel)
        if not target_flag:
            target_flag = self.sql.sqlite_get_max_flag(self.botId, target_id)

        if target_flag:
            try:
                target_index = hierarchy.index(target_flag)
            except ValueError:
                target_index = 999
            if target_index >= issuer_index:
                self.send_message(feedback,
                                  f"⛔ You cannot delete user '{username}' (has same or higher level: {target_flag}).")
                continue

        self.sql.sqlite_delete_user(self.botId, target_id)
        self.send_message(feedback, f"🗑️ User '{username}' and all their data have been deleted.")

def cmd_seen(self, channel, feedback, nick, host, msg):
    result = self.check_command_access(channel, nick, host, '31', feedback)
    if not result:
        return

    pattern = (msg or "").strip().split()[0] if msg else ""
    if not pattern:
        self.send_message(feedback, f"Usage: {s.char}seen <nick|host|pattern>")
        return

    if (msg or "").strip().lower() == "-stats":
        ch = channel if channel.startswith("#") else None
        self.send_message(feedback, seen.format_seen_stats(self.sql, self.botId, ch))
        return

    channel_hint = channel if channel.startswith("#") else None

    # Pasează bot=self ca să poată verifica live dacă userul e pe canal chiar dacă nu are rând în SEEN
    text = seen.format_seen(self.sql, self.botId, pattern, channel_hint=channel_hint, bot=self)
    self.send_message(feedback, text)

def cmd_info(self, channel, feedback, nick, host, msg):
    global thost_mask
    if channel.lower() == self.nickname.lower():
        self.send_message(feedback, f"⚠️ Usage: {s.char}info <#channel|user>")
        return
    arg = msg.strip()

    result = self.check_command_access(channel, nick, host, '24', feedback)
    if not result:
        return

    requester_id = result["userId"]
    if not arg:
        chan_info = self.sql.sqlite_get_channel_info(self.botId, channel)
        if not chan_info:
            self.send_message(feedback, f"❌ Channel `{channel}` not found in database.")
            return

        if not (self.sql.sqlite_user_has_channel_access(self.botId, requester_id, channel) or
                self.sql.sqlite_user_has_global_access(self.botId, requester_id)):
            self.send_message(feedback, "⛔ You do not have permission to view this channel's info.")
            return

        added_by, added_time, status, comment = chan_info
        added_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(added_time)) if added_time else "unknown"
        suspended_info = f"❌ Suspended: {comment}" if status == 1 else "✅ Active"

        self.send_message(feedback, f"📌 Info for channel {channel}:\n"
                                    f"➤ Added by: {added_by}\n"
                                    f"➤ Added on: {added_str}\n"
                                    f"➤ Status: {suspended_info}")
        return

    user_info = next((u for u in self.channel_details if u[1].lower() == arg.lower()), None)
    if user_info:
        tnick = user_info[1]
        tident = user_info[2]
        thost = user_info[3]
        thost_mask = self.get_hostname(tnick, f"{tident}@{thost}", 0)
        db_info = self.sql.sqlite_handle(self.botId, tnick, thost_mask)
        if not db_info:
            self.send_message(feedback, f"❌ No registered user found for {arg}.")
            return
        userId, username = db_info
    else:
        userId = self.sql.sqlite_get_user_id_by_name(self.botId, arg)
        thost_mask = ""
        if not userId:
            self.send_message(feedback, f"❌ User `{arg}` not found in database.")
            return
        username = arg

    added_time = self.sql.sqlite_get_user_added_time(self.botId, userId)
    hosts = self.sql.sqlite_get_user_hosts(self.botId, userId)
    logins = self.sql.sqlite_get_user_logins(self.botId, userId)
    global_flag = self.sql.sqlite_get_max_flag(self.botId, userId)
    local_flag = self.sql.sqlite_get_max_flag(self.botId, userId, channel)
    user_settings = self.sql.sqlite_get_user_settings(self.botId, userId)

    added_str = datetime.fromtimestamp(added_time).strftime('%Y-%m-%d %H:%M:%S') if added_time else "Unknown"
    login_status = "🟢 Logged in" if self.is_logged_in(userId, thost_mask) else "🔴 Not logged in"
    host_list = ', '.join(hosts) if hosts else "None"
    last_login = max((ts for _, _, ts in logins), default=None)
    last_login_str = last_login if last_login else "Never"
    global_str = f"{global_flag}" if global_flag else "N/A"
    local_str = f"{local_flag}" if local_flag else "N/A"

    settings_str = '\n'.join([f"➤ {k}: {v}" for k, v in user_settings.items()]) if user_settings else "None"

    added_by, last_modified_by = self.sql.sqlite_get_user_audit(self.botId, userId)

    self.send_message(feedback, f"👤 Info for user `{username}`:\n"
                                f"➤ Added on: {added_str}\n"
                                f"➤ Status: {login_status}\n"
                                f"➤ Hosts: {host_list}\n"
                                f"➤ Logins: {len(logins)}, last: {last_login_str}\n"
                                f"➤ Access - Global: {global_str}, Local on {channel}: {local_str}\n"
                                f"➤ Added by: {added_by or '-'} / Last modified by: {last_modified_by or '-'}\n"
                                f"🔧 Settings:\n{settings_str}")

def cmd_chat(self, channel, feedback, nick, host, msg):
    """
    !chat            -> open (offer) a DCC chat to the caller (unless one already exists)
    !chat open       -> same as above
    !chat list       -> list active DCC sessions
    !chat close      -> close the caller's own DCC session (if any)
    """
    # use the same access flag you already used for chat (ex: '90')
    if not self.check_command_access(channel, nick, host, '90', feedback):
        return

    sub = (msg or "").strip().split()
    subcmd = (sub[0].lower() if sub else "open")

    # Helper: check caller's session
    key = nick.lower()
    sess = self.dcc.sessions.get(key)
    def _state(s):
        if not s:
            return "none"
        if s.transport:
            return "open"
        # waiting/listening on a port after sending an offer (or fixed port listener)
        if s.outbound_offer or s.listening_port or self.dcc.fixed_port:
            return "waiting"
        return "connecting"

    # --- LIST ---
    if subcmd in ("list", "ls"):
        items = self.dcc.list_sessions()
        if not items:
            self.send_message(feedback, "(no DCC sessions)")
            return
        parts = []
        for _, d in items.items():
            parts.append(f"{d['nick']}[{d['state']}] {d['ip']}:{d['port']} age={d['age']}s idle={d['last']}s")
        for line in self.split_irc_message_parts(parts, separator=" | "):
            self.send_message(feedback, line)
        return

    # --- CLOSE (caller’s own session) ---
    if subcmd in ("close", "end", "quit"):
        if not sess:
            self.send_message(feedback, "ℹ️ You don't have an active DCC chat.")
            return
        ok = self.dcc.close(nick)
        self.send_message(feedback, "✅ DCC closed" if ok else "❌ Could not close DCC")
        return

    # --- OPEN (default) ---
    if subcmd in ("open", "start") or not subcmd:
        if sess:
            self.send_message(
                feedback,
                f"ℹ️ You already have a DCC session ({_state(sess)}). "
                f"Use {s.char}chat close to end it."
            )
            return
        self.dcc.offer_chat(nick, feedback=feedback)
        return

    # --- HELP / unknown ---
    self.send_message(
        feedback,
        f"Usage: {s.char}chat [open|list|close]  "
        f"→ open (default), list active sessions, or close your DCC session."
    )



