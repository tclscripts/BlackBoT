import time
from core import Variables as v
import threading
import os
from datetime import datetime
from core.threading_utils import ThreadWorker
import re, shlex
from core.environment_config import config
from fnmatch import fnmatchcase
from modules.stats.stats_user_analytics import get_analytics
from core.log import get_logger

logger = get_logger("commands")

def cmd_unban(self, channel, feedback, nick, host, msg):
    """
    Usage:
      !unban <nick>          → resolve nick (WHO) & remove ALL bans matching that user
      !unban <mask|regex>    → remove specific matching ban mask from DB
      !unban id:<number>     → remove specific ban by database ID
    """
    import shlex
    import re
    from fnmatch import fnmatchcase

    # --- 1. Permission Check ---
    if not self.check_command_access(channel, nick, host, '40', feedback):
        return

    # --- 2. Arg Parse ---
    try:
        tokens = shlex.split(msg)
    except Exception:
        tokens = msg.split()

    if not tokens:
        self.send_message(feedback, f"Usage: {config.char}unban <nick|mask|id:N> [-g]")
        return

    target = tokens[0]
    opts = tokens[1:]
    is_global = False

    for t in opts:
        if t.lower() in ("-g", "--global"):
            is_global = True

    # ---------------------------------------------------------------------
    # HELPERE INTERNE (Fixul tău este inclus aici)
    # ---------------------------------------------------------------------

    def _normalize_banmask_for_mode(ban_mask: str) -> str:
        if not ban_mask: return ""
        return ban_mask.split(":", 1)[0] if ":" in ban_mask else ban_mask

    def _ban_matches_user(ban_mask, ban_type, u_nick, u_ident, u_host, u_real):
        u_full = f"{u_nick}!{u_ident}@{u_host}"
        b_type = (ban_type or "mask").lower()

        if b_type == "regex":
            try:
                rx = re.compile(ban_mask)
                if rx.search(u_full): return True
                if u_real and rx.search(f"{u_full}:{u_real}"): return True
            except:
                pass
            return False
        else:
            main_mask = ban_mask.split(":", 1)[0]
            if not fnmatchcase(u_full.lower(), main_mask.lower()):
                return False
            if ":" in ban_mask:
                req_real = ban_mask.split(":", 1)[1]
                if not fnmatchcase((u_real or "").lower(), req_real.lower()):
                    return False
            return True

    # --- Helperul modificat de tine ---
    def _perform_db_removal(ban_ids_to_remove, resolved_target_name=None):
        # Numele pe care îl afișăm în mesajul de confirmare
        display_name = resolved_target_name if resolved_target_name else target

        if not ban_ids_to_remove:
            self.send_message(feedback, f"ℹ️ No matching bans found for '{display_name}'.")
            return

        removed_count = 0

        if is_global:
            auth_uid = self.get_logged_in_user_by_host(host)
            if not auth_uid:
                self.send_message(feedback, "❌ Authenticate first to remove global bans.")
                return
            max_flag = self.sql.sqlite_get_max_flag(self.botId, auth_uid)
            if max_flag not in ("N", "n", "m"):
                self.send_message(feedback, "❌ Insufficient access for global bans.")
                return

        for b_id, b_mask, b_scope in ban_ids_to_remove:
            if is_global and b_scope != "GLOBAL":
                continue

            try:
                is_local = (b_scope == "LOCAL")
                self.sql.sqlite_remove_ban_by_id(b_id, local=is_local)
                removed_count += 1

                if channel and channel.startswith("#"):
                    has_op = True
                    if hasattr(self, "_has_channel_op"):
                        try:
                            has_op = self._has_channel_op(channel)
                        except:
                            pass

                    if has_op:
                        clean_mask = _normalize_banmask_for_mode(b_mask)
                        self.sendLine(f"MODE {channel} -b {clean_mask}")

            except Exception as e:
                if hasattr(self, 'logger'):
                    self.logger.error(f"Failed to unban ID {b_id}: {e}")

        if removed_count > 0:
            self.send_message(feedback, f"✅ Removed {removed_count} ban(s) matching '{display_name}'.")
        else:
            self.send_message(feedback,
                              f"ℹ️ Found matches for '{display_name}' but failed to remove (check perms/scope).")

    # ---------------------------------------------------------------------
    # RAMURI DE EXECUȚIE (Aici se oprea codul tău, am adăugat restul)
    # ---------------------------------------------------------------------

    # BRANCH 1: Remove by ID
    if target.lower().startswith("id:"):
        try:
            bid = int(target.split(":", 1)[1])
            self.sql.sqlite_remove_ban_by_id(bid, local=not is_global)
            self.send_message(feedback, f"✅ Ban ID {bid} removed.")
        except Exception as e:
            self.send_message(feedback, f"❌ Error removing ID: {e}")
        return

    # BRANCH 2: Remove by Mask (Cu Smart Match pentru :realname)
    looks_like_mask = any(c in target for c in ('!', '@', '*', '?', ':')) or target.startswith('^')

    if looks_like_mask:
        try:
            active_bans = self.sql.sqlite_get_active_bans_for_channel(self.botId, channel)
        except Exception as e:
            self.send_message(feedback, f"SQL Error: {e}")
            return

        to_remove = []
        for row in active_bans:
            scope = row[0]
            bid = row[1]
            bmask = row[6]

            # A. Match EXACT
            if bmask == target:
                to_remove.append((bid, bmask, scope))
                continue

            # B. Match "Smart" (ignora :realname din DB daca nu e dat in comanda)
            if ':' in bmask and ':' not in target:
                simple_mask_db = bmask.split(':', 1)[0]
                if simple_mask_db == target:
                    to_remove.append((bid, bmask, scope))

        _perform_db_removal(to_remove)
        return

    # BRANCH 3: Remove by Nick (Async WHO)

    def on_who_result(info):
        if not info:
            # Cache fallback
            if hasattr(self, "channel_details"):
                for row in self.channel_details:
                    if len(row) > 3 and (row[1] or "").lower() == target.lower():
                        info = {
                            'nick': row[1], 'ident': row[2], 'host': row[3],
                            'realname': row[5] if len(row) > 5 else ""
                        }
                        break

        if not info:
            self.send_message(feedback, f"❌ Could not resolve address for '{target}'. Try unbanning by mask.")
            return

        u_ident = info.get("ident", "*")
        u_host = info.get("host", "*")
        u_real = info.get("realname", "")

        # Construim numele complet pentru mesajul de succes
        resolved_full_host = f"{target}!{u_ident}@{u_host}"

        try:
            active_bans = self.sql.sqlite_get_active_bans_for_channel(self.botId, channel)
        except Exception as e:
            self.send_message(feedback, f"SQL Error: {e}")
            return

        to_remove = []
        for row in active_bans:
            scope = row[0]
            bid = row[1]
            bmask = row[6]
            btype = row[7]

            if _ban_matches_user(bmask, btype, target, u_ident, u_host, u_real):
                to_remove.append((bid, bmask, scope))

        _perform_db_removal(to_remove, resolved_target_name=resolved_full_host)

    def on_who_error(failure):
        self.send_message(feedback, f"⚠️ Error resolving nick: {failure.getErrorMessage()}")

    # Lansare Async
    if hasattr(self, "get_user_info_async"):
        d = self.get_user_info_async(target, timeout=5.0)
        d.addCallback(on_who_result)
        d.addErrback(on_who_error)
        # IMPORTANT: RETURN pentru a opri orice alt cod vechi
        return
    else:
        self.send_message(feedback, "⚠️ Async WHO system missing.")

    # ---------------------------------------------------------------------
    # LOGICA DE RAMIFICARE
    # ---------------------------------------------------------------------

    # A) Ștergere după ID
    if target.lower().startswith("id:"):
        try:
            bid = int(target.split(":", 1)[1])
            self.sql.sqlite_remove_ban_by_id(bid, local=not is_global)
            self.send_message(feedback, f"✅ Ban ID {bid} removed.")
        except Exception as e:
            self.send_message(feedback, f"❌ Error removing ID: {e}")
        return

    # B) Ștergere după Mască / Regex
    looks_like_mask = any(c in target for c in ('!', '@', '*', '?', ':')) or target.startswith('^')

    if looks_like_mask:
        try:
            active_bans = self.sql.sqlite_get_active_bans_for_channel(self.botId, channel)
        except Exception as e:
            self.send_message(feedback, f"SQL Error: {e}")
            return

        to_remove = []
        for row in active_bans:
            scope = row[0]
            bid = row[1]
            bmask = row[6]
            if bmask == target:
                to_remove.append((bid, bmask, scope))

        _perform_db_removal(to_remove)
        return

    # C) Ștergere după Nick (Async WHO)

    def on_who_result(info):
        # Fallback cache
        if not info:
            if hasattr(self, "channel_details"):
                for row in self.channel_details:
                    if len(row) > 3 and (row[1] or "").lower() == target.lower():
                        info = {
                            'nick': row[1], 'ident': row[2], 'host': row[3],
                            'realname': row[5] if len(row) > 5 else ""
                        }
                        break

        if not info:
            self.send_message(feedback,
                              f"❌ Could not resolve address for '{target}' (User offline?). Try unbanning by mask.")
            return

        u_ident = info.get("ident", "*")
        u_host = info.get("host", "*")
        u_real = info.get("realname", "")

        # Construim string-ul complet care va apărea în mesajul de succes
        # Exemplu: dannt!~login@193.231.148.210
        resolved_full_host = f"{target}!{u_ident}@{u_host}"

        try:
            active_bans = self.sql.sqlite_get_active_bans_for_channel(self.botId, channel)
        except Exception as e:
            self.send_message(feedback, f"SQL Error: {e}")
            return

        to_remove = []
        for row in active_bans:
            scope = row[0]
            bid = row[1]
            bmask = row[6]
            btype = row[7]

            if _ban_matches_user(bmask, btype, target, u_ident, u_host, u_real):
                to_remove.append((bid, bmask, scope))

        # Pasăm hostul complet către funcția de ștergere
        _perform_db_removal(to_remove, resolved_target_name=resolved_full_host)

    def on_who_error(failure):
        self.send_message(feedback, f"⚠️ Error resolving nick: {failure.getErrorMessage()}")

    # Lansare Async
    if hasattr(self, "get_user_info_async"):
        d = self.get_user_info_async(target, timeout=5.0)
        d.addCallback(on_who_result)
        d.addErrback(on_who_error)
    else:
        self.send_message(feedback, "⚠️ Async WHO system missing.")


def cmd_ban(self, channel, feedback, nick, host, msg):
    """
    Usage:
      !ban <nick|mask|regex> [-regex] [-sticky] [-d 1h30m] [-reason "text"] [-g]
    """
    import re
    import shlex
    import time
    from twisted.internet import reactor

    # --- 1. Small Helpers ---

    def _parse_duration_to_seconds(spec: str):
        if not spec:
            return None
        total = 0
        for qty, unit in re.findall(r"(\d+)\s*(h|m|s)", spec.lower()):
            n = int(qty)
            if unit == "h":
                total += n * 3600
            elif unit == "m":
                total += n * 60
            else:
                total += n
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
        if ':' in ban_mask and ('!' in ban_mask or '@' in ban_mask):
            return ban_mask.split(':', 1)[0]
        looks_regex = ban_mask.startswith('^') or ban_mask.endswith('$')
        if looks_regex and member:
            ident = member.get('ident') or '*'
            mhost = member.get('host') or '*'
            return f"*!{ident}@{mhost}"
        return ban_mask

    def _iter_channel_members(chan: str):
        rows = getattr(self, 'channel_details', [])
        for row in rows:
            if not isinstance(row, (list, tuple)) or not row:
                continue
            if row[0] != chan:
                continue
            # row structure: [channel, nick, ident, host, privileges, realname, userId]
            n = row[1] if len(row) > 1 else None
            ident = row[2] if len(row) > 2 else None
            h = row[3] if len(row) > 3 else None
            _priv = row[4] if len(row) > 4 else None
            real = row[5] if len(row) > 5 else None
            uid = row[6] if len(row) > 6 else None
            yield {
                'nick': n, 'ident': ident, 'host': h,
                'realname': real, 'userId': uid, 'priv': _priv
            }

    # --- 2. Permission Check ---
    if not self.check_command_access(channel, nick, host, '33', feedback):
        return

    if not msg:
        self.send_message(feedback, "Usage: !ban <nick|mask|regex> [-regex] [-sticky] [-d 1h] [-reason \"text\"] [-g]")
        return

    # --- 3. Argument Parsing ---
    try:
        tokens = shlex.split(msg)
    except Exception:
        tokens = msg.split()

    target = tokens[0]
    opts = tokens[1:]

    duration = None
    sticky = False
    reason = None
    is_regex = False
    is_global = False

    i = 0
    reason_tokens = []
    duration_spec = None

    def _is_new_option(tok: str) -> bool:
        if not tok.startswith('-'): return False
        tl = tok.lower()
        return tl in ('-g', '--global', '-sticky', '--sticky', '-regex', '--regex', '-reason') or \
            tl.startswith('-d') or tl.startswith('-t') or tl.startswith('-reason=')

    while i < len(opts):
        t = opts[i]
        tl = t.lower()
        if tl in ('-g', '--global'):
            is_global = True
        elif tl in ('-sticky', '--sticky'):
            sticky = True
        elif tl in ('-regex', '--regex'):
            is_regex = True
        elif tl.startswith('-d') or tl.startswith('-t'):
            if len(t) > 2 and t[2] == '=':
                duration_spec = t[3:]
            elif len(t) > 2:
                duration_spec = t[2:]
            elif i + 1 < len(opts):
                duration_spec = opts[i + 1];
                i += 1
        elif tl.startswith('-reason='):
            reason_tokens = [t.split('=', 1)[1]]
        elif tl == '-reason':
            i += 1
            while i < len(opts):
                nt = opts[i]
                if _is_new_option(nt):
                    i -= 1;
                    break
                reason_tokens.append(nt)
                i += 1
        i += 1

    if duration_spec:
        duration = _parse_duration_to_seconds(duration_spec)
    if reason_tokens:
        reason = " ".join(reason_tokens).strip() or None

    store_channel = None if is_global or not (channel and channel.startswith('#')) else channel

    if is_global:
        userId = self.get_logged_in_user_by_host(self.get_hostname(nick, host, 0))
        if not userId:
            self.send_message(feedback, "❌ Authenticate first for global bans.")
            return
        issuer_flag = self.sql.sqlite_get_max_flag(self.botId, userId)
        if issuer_flag not in ("N", "n", "m"):
            self.send_message(feedback, "❌ Insufficient access for global bans.")
            return

    # --- 4. Finalize Ban Logic ---
    def _finalize_ban(ban_mask: str, is_regex_local: bool):
        # Caută setter ID
        try:
            host_mask = self.get_hostname(nick, host, 0)
            info = self.sql.sqlite_handle(self.botId, nick, host_mask)
            setter_userId = info[0] if info else None
        except Exception:
            setter_userId = None

        # Adaugă în DB
        created_id = self.sql.sqlite_add_ban(
            botId=self.botId,
            channel=store_channel,
            setter_userId=setter_userId,
            setter_nick=nick,
            ban_mask=ban_mask,
            ban_type=('regex' if is_regex_local else 'mask'),
            sticky=sticky,
            reason=reason,
            duration_seconds=duration
        )

        # Aplică pe canal
        applied = 0
        queued = 0

        if channel and channel.startswith('#'):
            # Compilează matcher
            rx = None
            if is_regex_local:
                try:
                    rx = _safe_compile_regex(ban_mask)
                except:
                    pass
            else:
                rx = _mask_to_re(ban_mask)

            has_op = True
            if hasattr(self, "_has_channel_op"):
                try:
                    has_op = self._has_channel_op(channel)
                except:
                    has_op = True

            # Iterează userii și aplică
            for m in _iter_channel_members(channel):
                m_full = f"{m['nick']}!{m['ident']}@{m['host']}"
                m_full_real = f"{m_full}:{m['realname']}" if m.get('realname') else m_full

                matched = False
                if rx:
                    if rx.search(m_full_real) or rx.search(m_full) if is_regex_local else (
                            rx.match(m_full_real) or rx.match(m_full)):
                        matched = True

                if not matched: continue

                # MODE +b
                mode_mask = _normalize_banmask_for_mode(ban_mask, m)
                if has_op:
                    self.sendLine(f"MODE {channel} +b {mode_mask}")
                    self.sendLine(f"KICK {channel} {m['nick']} :Banned ({reason or 'no reason'})")
                    applied += 1
                else:
                    if hasattr(self, "_queue_pending_ban_check"):
                        self._queue_pending_ban_check(channel, m['nick'], m.get('ident') or '*', m.get('host') or '*',
                                                      m.get('realname'))
                        queued += 1

        # Mark applied
        try:
            table = 'BANS_LOCAL' if store_channel else 'BANS_GLOBAL'
            self.sql.sqlite_mark_ban_applied(table, created_id)
        except:
            pass

        # Register timer
        if hasattr(self, 'ban_expiration_manager') and duration:
            expires_at = int(time.time()) + duration
            self.ban_expiration_manager.on_ban_added(created_id, expires_at)

        # Raportează
        scope = "global" if not store_channel else f"on {store_channel}"
        dur_str = f", expires in {duration}s" if duration else ", permanent"
        extra = f" (applied: {applied}, queued: {queued})" if (applied or queued) else ""

        self.send_message(feedback, f"✅ Ban stored (id={created_id}) {scope}{dur_str}{extra}")

    # --- 5. Execution Branches ---

    # A) Regex
    if is_regex:
        try:
            _safe_compile_regex(target)
        except ValueError as e:
            self.send_message(feedback, f"Invalid regex: {e}")
            return
        _finalize_ban(target, True)
        return

    # B) Mask (*!@)
    looks_like_mask = any(c in target for c in ('!', '@', ':', '*', '?'))
    if looks_like_mask:
        _finalize_ban(target, False)
        return

    # C) Nick (Async WHO)
    # NU folosi ThreadWorker aici, e deja async!

    def on_who_result(info):
        if not info:
            self.send_message(feedback, f"❌ {target} is not online (WHO returned no results).")
            return

        ident = info.get("ident") or "*"
        hostpart = info.get("host") or "*"
        realname = info.get("realname") or ""

        # Construim masca
        raw_host = f"{ident}@{hostpart}"
        base_mask = self.get_hostname(target, raw_host, 0)  # Folosește logica ta de netmask

        if realname.strip():
            ban_mask_local = f"{base_mask}:{realname}"
        else:
            ban_mask_local = base_mask

        _finalize_ban(ban_mask_local, False)

    def on_who_error(failure):
        self.send_message(feedback, f"⚠️ WHO failed for {target}: {failure.getErrorMessage()}")

    # Lansăm cererea (Non-blocking)
    if hasattr(self, "get_user_info_async"):
        d = self.get_user_info_async(target, timeout=5.0)
        d.addCallback(on_who_result)
        d.addErrback(on_who_error)
    else:
        self.send_message(feedback, "⚠️ Async WHO not available.")


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
            prefix = f"ℹ️ {config.char}{target['name']} — " if i == 0 else "   "
            self.send_message(feedback, prefix + line.strip())
        return

    public_names, local_names, global_names = [], [], []
    for c in cmds:
        name = c.get("name")
        if not name:
            continue
        flags = (c.get("flags") or "").strip()
        entry = f"{config.char}{name}"

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
    import os
    import threading
    import time
    from collections import defaultdict

    result = self.check_command_access(channel, nick, host, '30', feedback)
    if not result:
        return

    now = time.time()
    process = psutil.Process(os.getpid())

    # ─────────── Sistem ───────────
    uptime = now - process.create_time()
    formatted_uptime = self.format_duration(uptime)
    cpu_percent = process.cpu_percent(interval=None)
    mem_info = process.memory_info()
    rss_mb = mem_info.rss / (1024 * 1024)
    total_mem_mb = psutil.virtual_memory().total / (1024 * 1024)
    system = platform.system()
    release = platform.release()
    cpu_model = platform.processor()

    # ─────────── ThreadWorker groups ───────────
    threads = threading.enumerate()
    groups = defaultdict(lambda: {"supervisor": None, "child": None})

    for t in threads:
        name = getattr(t, "name", "")
        base = name[:-6] if name.endswith(".child") else name
        if name.endswith(".child"):
            groups[base]["child"] = t
        else:
            groups[base]["supervisor"] = t

    noisy_prefixes = ("PoolThread-twisted", "ThreadPoolExecutor")

    def is_noisy(n):
        return any(n.startswith(pfx) for pfx in noisy_prefixes)

    unique_threads = []
    for base in sorted(groups.keys(), key=str.lower):
        if not base or is_noisy(base) or base == "MainThread":
            continue
        g = groups[base]
        sup_alive = g["supervisor"] and g["supervisor"].is_alive()
        child_alive = g["child"] and g["child"].is_alive()
        if g["child"]:
            if sup_alive and child_alive:
                status = "✅"
            elif sup_alive:
                status = "⚠️"
            else:
                status = "❌"
            unique_threads.append(f"{base}({status})")
        else:
            unique_threads.append(base)

    threads_line = " · ".join(unique_threads)
    num_threads = len(unique_threads)

    # ─────────── Stare internă ───────────
    users_logged = len(self.logged_in_users)
    known_users = len(self.known_users)
    user_cache = len(self.user_cache)
    pending_rejoins = len(self.rejoin_pending)
    channel_info = len(self.channel_details)

    # ─────────── Net I/O + Load ───────────
    net_io = psutil.net_io_counters()
    net_up = net_io.bytes_sent / (1024 ** 2)
    net_down = net_io.bytes_recv / (1024 ** 2)

    load_line = ""
    try:
        if hasattr(os, "getloadavg"):
            la1, la5, la15 = os.getloadavg()
            load_line = f"📈 Load Avg (1/5/15m): {la1:.2f} / {la5:.2f} / {la15:.2f}"
        else:
            cpu1 = psutil.cpu_percent(interval=1.0)
            load_line = f"📈 CPU Load (1s): {cpu1:.1f}%"
    except Exception:
        pass

    system_line = (
        f"🧠 RAM: {rss_mb:.2f}MB / {total_mem_mb:.0f}MB | "
        f"🔄 CPU: {cpu_percent:.1f}% | "
        f"📶 Net I/O: ↑ {net_up:.1f}MB ↓ {net_down:.1f}MB"
    )
    if load_line:
        system_line += f" | {load_line}"

    # ─────────── BotLink + DCC info ───────────
    try:
        peers = []
        dcc_sessions = {}
        dcc_port = "?"
        dcc_ip = "?"
        stats_url = ""

        if hasattr(self, "dcc"):
            peers = sorted(list(self.dcc.list_link_peers()))
            dcc_sessions = self.dcc.list_sessions()

            # Get DCC port and IP
            if hasattr(self.dcc, 'fixed_port') and self.dcc.fixed_port:
                dcc_port = str(self.dcc.fixed_port)
            else:
                if hasattr(self.dcc, 'active_ephemeral_port') and self.dcc.active_ephemeral_port:
                    dcc_port = str(self.dcc.active_ephemeral_port)
                else:
                    ports = set()
                    try:
                        for s in (getattr(self.dcc, "sessions", {}) or {}).values():
                            if not s:
                                continue
                            p = None
                            try:
                                p = (getattr(s, "meta", {}) or {}).get("port")
                            except Exception:
                                p = None

                            if p and (
                                getattr(s, "outbound_offer", False)
                                or getattr(s, "listening_port", None) is not None
                                or getattr(s, "transport", None) is not None
                            ):
                                ports.add(str(p))
                    except Exception:
                        pass

                    if ports:
                        ports_sorted = sorted(ports, key=lambda x: int(x) if x.isdigit() else x)
                        if len(ports_sorted) <= 3:
                            dcc_port = ",".join(ports_sorted)
                        else:
                            dcc_port = f"{ports_sorted[0]} (+{len(ports_sorted) - 1} more)"
                    elif hasattr(self.dcc, 'port_min') and hasattr(self.dcc, 'port_max'):
                        dcc_port = f"ready ({self.dcc.port_min}-{self.dcc.port_max})"

            if hasattr(self.dcc, 'public_ip'):
                dcc_ip = self.dcc.public_ip

        # Get Stats API URL
        try:
            from core.environment_config import config
            stats_enabled = getattr(config, 'stats_api_enabled', False)

            if stats_enabled:
                stats_host = getattr(config, 'stats_api_host', '0.0.0.0')
                stats_port = getattr(config, 'stats_api_port', 8000)

                if stats_host in ('0.0.0.0', '::'):
                    display_ip = dcc_ip if dcc_ip != '?' else 'localhost'
                elif stats_host == '127.0.0.1':
                    display_ip = 'localhost'
                else:
                    display_ip = stats_host

                stats_url = f" | 🌐 Stats UI: http://{display_ip}:{stats_port}/ui"
        except Exception:
            pass

        peers_cnt = len(peers)
        open_cnt = sum(1 for s in dcc_sessions.values() if s.get("state") == "open")

        botlink_line = f"🔗 BotLink: {peers_cnt} peers ({open_cnt} open) | 🔌 DCC: {dcc_ip}:{dcc_port}{stats_url}"
    except Exception:
        try:
            if hasattr(self, "dcc"):
                dcc_port = getattr(self.dcc, 'fixed_port', '?')
                dcc_ip = getattr(self.dcc, 'public_ip', '?')
                botlink_line = f"🔗 BotLink: ? | 🔌 DCC: {dcc_ip}:{dcc_port}"
            else:
                botlink_line = f"🔗 BotLink: ? | 🔌 DCC: disabled"
        except Exception:
            botlink_line = None

    # ─────────── Cache Statistics (COMPACT: 3 in ONE line) ───────────
    cache_summary_line = None
    try:
        def _get_cache_stats(cache_obj):
            """Return (size, maxlen, hit_rate%, evict, exp) or None."""
            if not cache_obj:
                return None

            stats = None
            if hasattr(cache_obj, 'stats'):
                stats = cache_obj.stats
            elif hasattr(cache_obj, '_cache') and hasattr(cache_obj._cache, 'stats'):
                stats = cache_obj._cache.stats

            if not stats:
                return None

            maxlen = getattr(cache_obj, 'maxlen', None)
            if maxlen is None and hasattr(cache_obj, '_cache'):
                maxlen = getattr(cache_obj._cache, 'maxlen', None)
            if maxlen is None:
                maxlen = "?"

            size = getattr(stats, "size", 0)
            hr = (getattr(stats, "hit_rate", 0.0) or 0.0) * 100.0
            ev = getattr(stats, "evictions", 0)
            ex = getattr(stats, "expired", 0)
            return size, maxlen, hr, ev, ex

        uc = _get_cache_stats(self.user_cache)
        lc = _get_cache_stats(self.logged_in_users)
        hc = None
        if hasattr(self, "host_to_nicks"):
            hc = _get_cache_stats(self.host_to_nicks)

        parts = []
        if uc:
            size, mx, hr, ev, ex = uc
            parts.append(f"👤User {size}/{mx} H{hr:.1f}% E{ev} X{ex}")
        if lc:
            size, mx, hr, ev, ex = lc
            parts.append(f"🔐Logged {size}/{mx} H{hr:.1f}% E{ev} X{ex}")
        if hc:
            size, mx, hr, ev, ex = hc
            parts.append(f"🌐Host→Nick {size}/{mx} H{hr:.1f}% E{ev} X{ex}")

        if parts:
            cache_summary_line = "🧠 Cache: " + " | ".join(parts)

    except Exception as e:
        cache_summary_line = f"⚠️ Cache: error ({str(e)})"

    # ─────────── Compose mesaj ───────────
    msg_lines = [
        f"📊 *Advanced Status Report*",
        f"🔢 Threads ({num_threads}): {threads_line}",
        f"📌 Rejoin Queue: {pending_rejoins} | 📺 Channel Details: {channel_info}",
        system_line,
    ]

    if botlink_line:
        msg_lines.append(botlink_line)

    if cache_summary_line:
        msg_lines.append(cache_summary_line)
    else:
        msg_lines.append(f"👥 Logged Users: {users_logged} | 🧠 Known: {known_users} | 📝 User Cache: {user_cache}")

    # ─────────── Authentication Status ───────────
    auth_status_line = ""
    try:
        if getattr(self, 'authenticated', False):
            auth_method = getattr(self, 'auth_method', 'unknown')
            if auth_method == 'q':
                username = getattr(self, 'auth_service_username', 'unknown')
                auth_status_line = f"🔐 Auth: QuakeNet Q ({username}) ✅"
            elif auth_method == 'x':
                username = getattr(self, 'auth_service_username', 'unknown')
                auth_status_line = f"🔐 Auth: Undernet X ({username}) ✅"
            elif auth_method == 'nickserv':
                auth_status_line = f"🔐 Auth: NickServ ✅"
            else:
                auth_status_line = f"🔐 Auth: {auth_method} ✅"
        elif getattr(self, 'nickserv_waiting', False):
            auth_method = getattr(self, 'auth_method', 'unknown')
            if auth_method == 'q':
                auth_status_line = f"🔐 Auth: QuakeNet Q ⏳ (waiting...)"
            elif auth_method == 'x':
                auth_status_line = f"🔐 Auth: Undernet X ⏳ (waiting...)"
            else:
                auth_status_line = f"🔐 Auth: {auth_method} ⏳ (waiting...)"
        else:
            auth_status_line = "🔐 Auth: Not configured ⭕"
    except Exception:
        auth_status_line = "🔐 Auth: Unknown status"

    msg_lines.extend([
        auth_status_line,
        f"💻 System: {system} {release} | CPU: {cpu_model}",
        f"⏱️ Uptime: {formatted_uptime}",
    ])

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
        self.send_message(feedback, f"⚠️ Usage: {config.char}myset <{'|'.join(v.users_settings_change)}> <value>")
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
        self.send_message(feedback, f"⚠️ Usage: {config.char}update check | {config.char}update start")


def cmd_auth(self, channel, feedback, nick, host, msg):
    import bcrypt
    from twisted.internet import reactor

    parts = msg.strip().split()
    if not parts:
        self.send_message(feedback, "⚠️ Usage: auth <username> <password>")
        return

    # host original primit din privmsg: "ident@host"
    # îl normalizăm la masca folosită peste tot: *!*@host (sau ce ai în default_hostname)
    host_mask = self.get_hostname(nick, host, 0)

    # Save current host to trusted hosts
    if parts[0].lower() == "save":
        userId = self.get_logged_in_user_by_host(host_mask)
        if not userId:
            self.send_message(feedback, "❌ You are not logged in from this host.")
            return
        if self.sql.sqlite_check_user_host_exists(self.botId, userId, host_mask):
            self.send_message(feedback, "ℹ️ This host is already saved as one of your trusted logins.")
        else:
            self.sql.sqlite_add_user_host(self.botId, userId, host_mask)
            self.send_message(feedback, f"✅ Your current host `{host_mask}` has been saved.")
        return

    # Must auth in PM
    if feedback != nick:
        self.send_message(feedback, "❌ You must authenticate in private message.")
        return

    # Resolve username/userId + stored_hash
    if len(parts) == 1:
        # doar parola -> încercăm să rezolvăm userul după nick+host
        password = parts[0]
        info = self.sql.sqlite_handle(self.botId, nick, host_mask)
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
        # auth <username> <parola...>
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

    # dacă deja e logat din acest host → mesaj direct, fără thread nou
    if self.is_logged_in(userId, host_mask):
        self.send_message(feedback, "🔓 You are already logged in from this host.")
        return

    # Offload bcrypt to a background thread to avoid blocking IRC reactor
    def _do_auth_check():
        success_local = False
        try:
            # încă o verificare aici, ca să prindem cazul în care primul auth a reușit între timp
            if self.is_logged_in(userId, host_mask):
                reactor.callFromThread(
                    self.send_message,
                    feedback,
                    "🔓 You are already logged in from this host."
                )
                return

            ok = stored_hash and bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8"))
            if ok:
                # Update in-memory login state
                if userId not in self.logged_in_users:
                    self.logged_in_users[userId] = {"hosts": [], "nick": nick}
                if host_mask not in self.logged_in_users[userId]["hosts"]:
                    self.logged_in_users[userId]["hosts"].append(host_mask)
                self.logged_in_users[userId]["nick"] = nick

                # Cache (TTLCache if available)
                key = (nick, host_mask)
                if hasattr(self.user_cache, "set"):
                    self.user_cache.set(key, userId)
                else:
                    self.user_cache[key] = userId

                success_local = True
                reactor.callFromThread(
                    self.send_message,
                    feedback,
                    f"✅ Welcome, {username}. You are now logged in from {host_mask}."
                )

                # pornește monitorizarea logged_users o singură dată
                if not getattr(self, "thread_check_logged_users_started", False):
                    self.thread_check_logged_users_started = True
                    self.thread_check_logged_users = ThreadWorker(
                        target=self._check_logged_users_loop,
                        name="logged_users",
                        supervise=True,
                        provide_signals=True,
                        heartbeat_timeout=600,
                    )
                    self.thread_check_logged_users.start()
            else:
                reactor.callFromThread(self.send_message, feedback, "❌ Invalid username or password.")
        finally:
            # Persist login attempt result (host_mask în loc de host brut)
            self.sql.sqlite_log_login_attempt(self.botId, nick, host_mask, userId, success_local)

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

    try:
        if hasattr(self, "channel_info") and self.channel_info:
            real_key = next((k for k in self.channel_info.keys() if k.lower() == canonical.lower()), None)
            if real_key:
                del self.channel_info[real_key]
    except Exception:
        pass

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
                 f"ℹ️ Use {config.char}help to see available commands based on your access level.")
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
    self.restart("🔁 Restart by command")


def cmd_cycle(self, channel, feedback, nick, host, msg):
    if channel.lower() == self.nickname.lower():
        self.send_message(feedback, f"⚠️ Usage: {config.char}cycle <#channel>")
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
        self.send_message(feedback, f"⚠️ Usage: {config.char}add <role> <nick1> <nick2> ...")
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
        self.send_message(feedback, f"⚠️ Usage: {config.char}del <nick1> <nick2> ...")
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
        self.send_message(feedback, f"⚠️ Usage: {config.char}del <username>")
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

def cmd_info(self, channel, feedback, nick, host, msg):
    global thost_mask
    if channel.lower() == self.nickname.lower():
        self.send_message(feedback, f"⚠️ Usage: {config.char}info <#channel|user>")
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
    is_logged = userId in getattr(self, "logged_in_users", {})
    login_status = "🟢 Logged in" if is_logged else "🔴 Not logged in"

    # --- NEW: BotLink status (doar dacă userul e configurat drept peer botlink) ---
    botlink_tag = ""
    try:
        is_peer = False
        # 1) din setările din DB (botlink=1/true)
        if user_settings:
            val = str(user_settings.get("botlink", "0")).strip().lower()
            is_peer = val in ("1", "true", "yes")
        # 2) sau din allowlist-ul DCC (runtime)
        if hasattr(self, "dcc") and self.dcc and self.dcc._is_botlink_user(username):
            is_peer = True

        if is_peer and hasattr(self, "dcc") and self.dcc:
            sess = self.dcc.sessions.get(username.lower())
            linked = bool(sess and getattr(sess, "transport", None))
            botlink_tag = f" | {'🔗 Linked' if linked else '⛓️ Not linked'}"
    except Exception:
        # nu stricăm comanda dacă ceva e în neregulă
        pass
    # --- END NEW ---

    host_list = ', '.join(hosts) if hosts else "None"
    last_login = max((ts for _, _, ts in logins), default=None)
    last_login_str = last_login if last_login else "Never"
    global_str = f"{global_flag}" if global_flag else "N/A"
    local_str = f"{local_flag}" if local_flag else "N/A"

    settings_str = '\n'.join([f"➤ {k}: {v}" for k, v in (user_settings or {}).items()]) if user_settings else "None"

    added_by, last_modified_by = self.sql.sqlite_get_user_audit(self.botId, userId)

    self.send_message(feedback, f"👤 Info for user `{username}`:\n"
                                f"➤ Added on: {added_str}\n"
                                f"➤ Status: {login_status}{botlink_tag}\n"
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
    if not self.check_command_access(channel, nick, host, '34', feedback):
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
                f"Use {config.char}chat close to end it."
            )
            return
        self.dcc.offer_chat(nick, feedback=feedback)
        return

    # --- HELP / unknown ---
    self.send_message(
        feedback,
        f"Usage: {config.char}chat [open|list|close]  "
        f"→ open (default), list active sessions, or close your DCC session."
    )

def _resolve_user_id(self, peer: str) -> int | None:
    """
    Resolve userId for 'peer' even if not logged in.
    Try, in order:
      1) handle-only lookup
      2) channel-derived host (if visible)
      3) generic hostmask stored in DB (e.g. *!*@IP)
      4) sqlite_handle with generic peer!*@*
    """
    # 1) handle-only (most reliable if you know the account name)
    uid = self.sql.sqlite_get_user_by_handle(self.botId, peer)
    if uid:
        return uid

    # 2) if user is visible on a channel, use ident@host from WHO cache
    try:
        for row in getattr(self, "channel_details", []):
            # row: [channel, nick, ident, host, privileges, realname, userId]
            if row and len(row) >= 4 and (row[1] or "").lower() == peer.lower():
                ident = row[2] or "*"
                host  = row[3] or "*"
                info = self.sql.sqlite_handle(self.botId, peer, f"{ident}@{host}")
                if info:
                    return info[0]
                break
    except Exception:
        pass

    # 3) try to reuse a stored raw hostmask from USERSHOSTS, if any
    #    (common when your "info" shows *!*@IP)
    possible_masks = [
        f"{peer}!*@*",   # generic based on nick
        "*!*@" + peer,   # if caller passes a raw host/ip as 'peer'
    ]
    for m in possible_masks:
        try:
            uid = self.sql.sqlite_get_user_by_hostmask(self.botId, m)
            if uid:
                return uid
        except Exception:
            pass

    # 4) last resort: ask sqlite_handle with generic mask
    try:
        info = self.sql.sqlite_handle(self.botId, peer, f"{peer}!*@*")
        if info:
            return info[0]
    except Exception:
        pass

    return None

def cmd_botlink(self, channel, feedback, nick, host, msg):
    if not self.check_command_access(channel, nick, host, '35', feedback):  # access pentru botlink
        return

    tokens = (msg or "").split()
    if not tokens:
        self.send_message(feedback, f"Usage: {config.char}botlink add|del|list|connect|disconnect")
        return

    action = tokens[0].lower()

    # === ADD ===
    if action == "add":
        if len(tokens) < 4:
            self.send_message(feedback, f"Usage: {config.char}botlink add <nick> <ip> <port>")
            return

        peer_name, ip, port_s = tokens[1], tokens[2], tokens[3]

        # Validate IP
        import ipaddress
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            self.send_message(feedback, "❌ Invalid IP address.")
            return

        # Validate port
        try:
            port = int(port_s)
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            self.send_message(feedback, "❌ Invalid port (must be 1..65535).")
            return

        # Strict: user must already exist in DB by handle/username
        user_id = self.sql.sqlite_get_user_id_by_name(self.botId, peer_name)
        if not user_id:
            self.send_message(feedback, f"❌ User '{peer_name}' does not exist in database.")
            return

        # Persist settings
        self.sql.sqlite_update_user_setting(self.botId, user_id, "botlink", "1")
        self.sql.sqlite_update_user_setting(self.botId, user_id, "botlink_ip", ip)
        self.sql.sqlite_update_user_setting(self.botId, user_id, "botlink_port", str(port))

        # Track peer for DCC manager (so connect/list works)
        if hasattr(self, "dcc"):
            self.dcc.add_link_peer(peer_name)

        self.send_message(feedback, f"✅ '{peer_name}' added as botlink peer at {ip}:{port}.")
        return


    # === DELETE ===
    elif action == "del":
        if len(tokens) < 2:
            self.send_message(feedback, f"Usage: {config.char}botlink del <nick>")
            return

        target_nick = tokens[1]
        handle_info = self.sql.sqlite_handle(self.botId, target_nick, host)
        if not handle_info:
            self.send_message(feedback, f"❌ Cannot find user '{target_nick}' in DB.")
            return

        user_id = handle_info[0]
        for setting in ("botlink", "botlink_ip", "botlink_port"):
            self.sql.sqlite_update_user_setting(self.botId, user_id, setting, None)

        self.send_message(feedback, f"🗑️ Removed botlink peer '{target_nick}'")
        return

    if action == "list":
        # 1) Peer-ii marcați în DB (botlink=1)
        rows = self.sql.sqlite_select("""
                                      SELECT U.username
                                      FROM USERS U
                                               JOIN USERSSETTINGS S ON U.id = S.userId
                                      WHERE S.botId = ?
                                        AND S.setting = 'botlink'
                                        AND S.settingValue = '1'
                                      ORDER BY U.username COLLATE NOCASE
                                      """, [self.botId]) or []

        # colectăm linii frumoase + ne asigurăm că DCC știe de ei
        pretty = []
        for (uname,) in rows:
            # înregistrează peer-ul și în DCC manager (pt connect ulterior)
            if hasattr(self, "dcc"):
                self.dcc.add_link_peer(uname)

            uid = self.sql.sqlite_get_user_id_by_name(self.botId, uname)
            settings = self.sql.sqlite_get_user_settings(self.botId, uid) if uid else {}
            ip = (settings or {}).get("botlink_ip") or "?"
            port = (settings or {}).get("botlink_port") or "?"

            # status de link (din sesiuni)
            sess = self.dcc.sessions.get(uname.lower()) if hasattr(self, "dcc") else None
            if sess and getattr(sess, "transport", None):
                icon = "🔗"
                state = "open"
            elif sess and (sess.outbound_offer or sess.listening_port or self.dcc.fixed_port):
                icon = "⚠️"
                state = "waiting"
            else:
                icon = "⛔"
                state = "idle"

            pretty.append(f"{icon} {uname} ({ip}:{port}) — {state}")

        if pretty:
            for part in self.split_irc_message_parts(
                    ["Peers:\n" + "\n".join(pretty)], separator=""
            ):
                self.send_message(feedback, part)
        else:
            self.send_message(feedback, "Peers: (none)")

        # 2) Sesiuni DCC curente (detaliu tehnic)
        sessions = self.dcc.list_sessions() if hasattr(self, "dcc") else {}
        if sessions:
            parts = []
            for _, d in sessions.items():
                s_obj = self.dcc.sessions.get(d['nick'].lower()) if hasattr(self, "dcc") else None
                is_bot = (s_obj and s_obj.meta.get("botlink") == "1")
                bot_tag = " [BOT]" if is_bot else ""
                parts.append(f"{d['nick']}{bot_tag}[{d['state']}] {d['ip']}:{d['port']} "
                             f"age={d['age']}s idle={d['last']}s")
            for line in self.split_irc_message_parts(parts, separator=" | "):
                self.send_message(feedback, line)
        else:
            self.send_message(feedback, "(no DCC sessions)")
        return

    if action == "connect":
        peer = tokens[1] if len(tokens) >= 2 else None

        def _connect_one(p: str):
            # ne asigurăm că e în lista de peers și avem endpoint în DB
            self.dcc.add_link_peer(p)
            has_ep = self.dcc.has_endpoint(p)

            # trimitem oricum offer (standard DCC) – remote se poate conecta la noi
            self.dcc.offer_chat(p, feedback=feedback)

            # anti-stalemate:
            # dacă sesiunea este "listening" și avem endpoint, forțăm un outbound TCP
            state = self.dcc.session_state(p)
            if state == "listening" and has_ep:
                self.dcc.force_connect(p, feedback=feedback)

        if peer:
            if self.dcc._has_open_session(peer):
                if feedback:
                    try:
                        self.send_message(feedback, f"🔗 Already connected to {peer}.")
                    except Exception:
                        pass
                return True
            _connect_one(peer)
            self.send_message(feedback, f"🔗 Connecting to '{peer}' ...")
        else:
            peers = self.dcc.list_link_peers()
            if not peers:
                self.send_message(feedback, "ℹ️ No peers configured.")
                return
            for p in peers:
                _connect_one(p)
            self.send_message(feedback, "🔗 Connecting to all peers ...")
        return

    # === DISCONNECT ===
    if action == "disconnect":
        if not hasattr(self, "dcc"):
            self.send_message(feedback, "❌ DCC manager not available.")
            return
        target = tokens[1] if len(tokens) >= 2 else None

        def _purge_pending_for(p: str):
            try:
                self.dcc.pending_offers = [o for o in (self.dcc.pending_offers or []) if
                                            (o.get("nick", "").lower() != p.lower())]
            except Exception:
                pass

        def _disconnect_one(p: str):
            had = p.lower() in (self.dcc.sessions or {})
            ok = self.dcc.close(p)
            _purge_pending_for(p)
            if ok or had:
                self.send_message(feedback, f"🔌 Disconnected from '{p}'.")
            else:
                self.send_message(feedback, f"ℹ️ Not connected to '{p}'.")

        if not target or target.lower() in ("all", "*"):
            peers_set = {x.lower() for x in (self.dcc.list_link_peers() or [])}
            names = []
            for key, sess in list(self.dcc.sessions.items()):
                if not sess:
                    continue
                if sess.meta.get("botlink") == "1" or key in peers_set:
                        names.append(sess.peer_nick)
            if not names:
                self.send_message(feedback, "ℹ️ No botlink sessions to disconnect.")
                return
            for p in names:
                _disconnect_one(p)
            self.send_message(feedback, f"✅ Disconnected {len(names)} session(s).")
        else:
            _disconnect_one(target)
        return None

    else:
            self.send_message(feedback, f"Usage: {config.char}botlink add|del|list|connect|disconnect")
            return None


def _is_ip_or_hostname(text: str) -> bool:
    """
    Check if text is an IP address or resolvable hostname.
    If not, assume it's a nickname.

    This helper is used by cmd_ip, cmd_dns, cmd_ping.
    """
    from core import nettools

    # Check if it's an IP
    if nettools._is_ip(text):
        return True

    # Check if it looks like a hostname (contains dots or is localhost)
    if '.' in text or text.lower() == 'localhost':
        return True

    return False


def cmd_dns(self, channel, feedback, nick, host, msg):
    """
    !dns <host|ip|nick>

    Smart DNS lookup: hostname → A/AAAA, IP → rDNS (PTR)
    If nick is provided, does WHO lookup first.

    Examples:
      !dns google.com
      !dns 8.8.8.8
      !dns Enki  <- NEW: Does WHO lookup automatically
    """
    from core import nettools
    from twisted.internet.threads import deferToThread

    target = (msg or "").strip().split()[0] if msg else ""
    if not target:
        self.send_message(feedback, f"Usage: {config.char}dns <host|ip|nick>")
        return

    if _is_ip_or_hostname(target):
        # Direct lookup for IP/hostname
        def work():
            res = nettools.smart_dns(target)
            return nettools.format_smart_dns(res)

        def done(lines):
            for line in lines:
                self.send_message(feedback, line)

        deferToThread(work).addCallback(done)

    else:
        # WHO lookup for nick
        def on_who_result(info):
            if not info:
                # Cache fallback
                if hasattr(self, "channel_details"):
                    for row in self.channel_details:
                        if len(row) > 3 and (row[1] or "").lower() == target.lower():
                            info = {
                                'nick': row[1],
                                'host': row[3]
                            }
                            break

            if not info:
                self.send_message(feedback, f"❌ Cannot resolve user: {target}")
                return

            user_host = info.get('host', '')
            user_nick = info.get('nick', target)

            if not user_host:
                self.send_message(feedback, f"❌ No host found for {user_nick}")
                return

            # DNS lookup on resolved host
            def work():
                res = nettools.smart_dns(user_host)
                return nettools.format_smart_dns(res)

            def done(lines):
                if lines:
                    lines[0] = f"👤 {user_nick} ({user_host}): {lines[0]}"
                for line in lines:
                    self.send_message(feedback, line)

            deferToThread(work).addCallback(done)

        def on_who_error(failure):
            self.send_message(feedback, f"❌ Cannot resolve user: {target}")

        if hasattr(self, "get_user_info_async"):
            d = self.get_user_info_async(target, timeout=5.0)
            d.addCallback(on_who_result)
            d.addErrback(on_who_error)
        else:
            self.send_message(feedback, f"❌ WHO lookup not available. Try: {config.char}dns <host|ip>")


def cmd_ping(self, channel, feedback, nick, host, msg):
    """
    !ping <host|ip|nick>

    Ping a host (IPv4 or IPv6).
    If nick is provided, does WHO lookup first.

    Examples:
      !ping google.com
      !ping 8.8.8.8
      !ping Enki  <- NEW: Does WHO lookup automatically
    """
    from core import nettools
    from twisted.internet.threads import deferToThread

    target = (msg or "").strip().split()[0] if msg else ""
    if not target:
        self.send_message(feedback, f"Usage: {config.char}ping <host|ip|nick>")
        return

    if _is_ip_or_hostname(target):
        # Direct ping for IP/hostname
        def work():
            result = nettools.ping(target)
            return nettools.format_ping(result)

        def done(lines):
            for line in lines:
                self.send_message(feedback, line)

        deferToThread(work).addCallback(done)

    else:
        # WHO lookup for nick
        def on_who_result(info):
            if not info:
                # Cache fallback
                if hasattr(self, "channel_details"):
                    for row in self.channel_details:
                        if len(row) > 3 and (row[1] or "").lower() == target.lower():
                            info = {
                                'nick': row[1],
                                'host': row[3]
                            }
                            break

            if not info:
                self.send_message(feedback, f"❌ Cannot resolve user: {target}")
                return

            user_host = info.get('host', '')
            user_nick = info.get('nick', target)

            if not user_host:
                self.send_message(feedback, f"❌ No host found for {user_nick}")
                return

            # Ping resolved host
            def work():
                result = nettools.ping(user_host)
                return nettools.format_ping(result)

            def done(lines):
                if lines:
                    lines[0] = f"👤 {user_nick} ({user_host}): {lines[0]}"
                for line in lines:
                    self.send_message(feedback, line)

            deferToThread(work).addCallback(done)

        def on_who_error(failure):
            self.send_message(feedback, f"❌ Cannot resolve user: {target}")

        if hasattr(self, "get_user_info_async"):
            d = self.get_user_info_async(target, timeout=5.0)
            d.addCallback(on_who_result)
            d.addErrback(on_who_error)
        else:
            self.send_message(feedback, f"❌ WHO lookup not available. Try: {config.char}ping <host|ip>")


def cmd_asn(self, channel, feedback, nick, host, msg):
    """
    !asn <AS|ip|host|nick> [full]

    ASN information lookup.
    If nick is provided, does WHO lookup first.

    Examples:
      !asn AS15169         # ASN lookup
      !asn AS15169 full    # Detailed ASN info
      !asn 8.8.8.8         # IP to ASN
      !asn google.com      # Host to ASN
      !asn Enki            # NEW: WHO lookup then ASN
      !asn Enki full       # NEW: WHO lookup then full ASN
    """
    from core import nettools
    from twisted.internet.threads import deferToThread

    args = (msg or "").strip().split()
    if not args:
        self.send_message(feedback, f"Usage: {config.char}asn <AS|ip|host|nick> [full]")
        return

    target = args[0]
    want_full = (len(args) > 1 and args[1].lower() == "full")

    # =========================================================================
    # Helper: Check if target needs WHO lookup
    # =========================================================================

    def is_asn_or_ip_or_hostname(text: str) -> bool:
        """
        Check if text is:
        - AS number (AS12345 or 12345)
        - IP address
        - Hostname (has dots)

        If not, assume it's a nickname.
        """
        # Is it an AS number?
        if text.upper().startswith("AS"):
            return True

        # Is it just digits (AS number without "AS" prefix)?
        if text.isdigit():
            return True

        # Is it an IP?
        if nettools._is_ip(text):
            return True

        # Looks like hostname?
        if '.' in text or text.lower() == 'localhost':
            return True

        return False

    # =========================================================================
    # Main Logic
    # =========================================================================

    if is_asn_or_ip_or_hostname(target):
        # CASE 1: Direct ASN lookup (AS number, IP, or hostname)

        if want_full:
            def work_full():
                data = nettools.asn_full(target, sample=6)
                return nettools.format_asn_full(data)

            return deferToThread(work_full).addCallback(
                lambda lines: [self.send_message(feedback, l) for l in lines]
            )

        else:
            def work_short():
                res = nettools.asn_info(target)
                return nettools.format_asn(res)

            deferToThread(work_short).addCallback(
                lambda lines: [self.send_message(feedback, l) for l in lines]
            )

    else:
        # CASE 2: Target looks like a nick - do WHO lookup first

        def on_who_result(info):
            """Callback when WHO lookup succeeds"""
            if not info:
                # Try cache fallback
                if hasattr(self, "channel_details"):
                    for row in self.channel_details:
                        if len(row) > 3 and (row[1] or "").lower() == target.lower():
                            info = {
                                'nick': row[1],
                                'host': row[3]
                            }
                            break

            if not info:
                self.send_message(feedback, f"❌ Cannot resolve user: {target}")
                return

            user_host = info.get('host', '')
            user_nick = info.get('nick', target)

            if not user_host:
                self.send_message(feedback, f"❌ No host found for {user_nick}")
                return

            # Now run ASN lookup on the resolved host
            if want_full:
                def work_full():
                    data = nettools.asn_full(user_host, sample=6)
                    lines = nettools.format_asn_full(data)
                    # Prepend user info to first line
                    if lines:
                        lines[0] = f"👤 {user_nick} ({user_host}): {lines[0]}"
                    return lines

                deferToThread(work_full).addCallback(
                    lambda lines: [self.send_message(feedback, l) for l in lines]
                )

            else:
                def work_short():
                    res = nettools.asn_info(user_host)
                    lines = nettools.format_asn(res)
                    # Prepend user info to first line
                    if lines:
                        lines[0] = f"👤 {user_nick} ({user_host}): {lines[0]}"
                    return lines

                deferToThread(work_short).addCallback(
                    lambda lines: [self.send_message(feedback, l) for l in lines]
                )

        def on_who_error(failure):
            """Callback when WHO lookup fails"""
            self.send_message(feedback, f"❌ Cannot resolve user: {target}")

        # Launch async WHO lookup
        if hasattr(self, "get_user_info_async"):
            d = self.get_user_info_async(target, timeout=5.0)
            d.addCallback(on_who_result)
            d.addErrback(on_who_error)
        else:
            # Fallback if async WHO is not available
            self.send_message(feedback, f"❌ WHO lookup not available. Try: {config.char}asn <AS|ip|host>")
            return None


def cmd_user(self, channel, feedback, nick, host, msg):
    """
    !user <subcommand> <args>

    Subcommands:
      profile <nick> [days]    - Complete user profile
      sentiment <nick> [days]  - Sentiment analysis
      partners <nick> [days]   - Conversation partners
      active <nick> [days]     - Peak activity times
      compare <nick1> <nick2> [days] - Compare two users
      link <nick>              - Get analytics page link

    Example: !user profile alice 30
    """

    args = msg.strip().split() if msg else []

    if not args:
        self.send_message(feedback, f"{nick}: Usage: !user <subcommand> <args>")
        self.send_message(feedback, f"  Subcommands: profile, sentiment, partners, active, compare, link")
        return

    subcommand = args[0].lower()
    subargs = ' '.join(args[1:]) if len(args) > 1 else ''

    if subcommand == 'profile':
        _cmd_user_profile(self, channel, feedback, nick, host, subargs)

    elif subcommand == 'sentiment':
        _cmd_user_sentiment(self, channel, feedback, nick, host, subargs)

    elif subcommand == 'partners':
        _cmd_user_partners(self, channel, feedback, nick, host, subargs)

    elif subcommand == 'active':
        _cmd_user_active(self, channel, feedback, nick, host, subargs)

    elif subcommand == 'compare':
        _cmd_user_compare(self, channel, feedback, nick, host, subargs)

    elif subcommand == 'link':
        _cmd_user_link(self, channel, feedback, nick, host, subargs)

    else:
        self.send_message(feedback, f"{nick}: Unknown subcommand '{subcommand}'")
        self.send_message(feedback, f"  Available: profile, sentiment, partners, active, compare, link")


def _cmd_user_profile(self, channel, feedback, nick, host, args):
    """!user profile <nick> [days]"""

    parts = args.split() if args else []

    if not parts:
        self.send_message(feedback, f"{nick}: Usage: !user profile <nick> [days=30]")
        return

    target_nick = parts[0]
    days = int(parts[1]) if len(parts) > 1 else 30

    try:
        analytics = get_analytics(self.sql)
        profile = analytics.get_user_profile(self.botId, channel, target_nick, days)

        if 'error' in profile:
            self.send_message(feedback, f"{nick}: No data found for {target_nick}")
            return

        # Send summary
        self.send_message(feedback, f"{nick}: Profile for {target_nick} (last {days} days):")
        self.send_message(feedback, f"  📊 Activity: {profile['activity']['total_messages']} msgs, "
                                    f"{profile['activity']['avg_words_per_message']:.1f} avg words/msg")
        self.send_message(feedback, f"  💭 Sentiment: {profile['sentiment']['overall_sentiment'].upper()} "
                                    f"(score: {profile['sentiment']['average_score']:.2f})")

        if profile['patterns']['peak_hours']:
            peak = profile['patterns']['peak_hours'][0]
            self.send_message(feedback, f"  ⏰ Most active: {peak['time_label']} "
                                        f"({peak['percentage']:.1f}% of activity)")

        if profile['social']['top_conversation_partners']:
            top = profile['social']['top_conversation_partners'][0]
            self.send_message(feedback, f"  👥 Top partner: {top['partner']} "
                                        f"({top['total_interactions']} interactions)")

    except Exception as e:
        self.send_message(feedback, f"{nick}: Error: {e}")


def _cmd_user_sentiment(self, channel, feedback, nick, host, args):
    """!user sentiment <nick> [days]"""

    parts = args.split() if args else []

    if not parts:
        self.send_message(feedback, f"{nick}: Usage: !user sentiment <nick> [days=30]")
        return

    target_nick = parts[0]
    days = int(parts[1]) if len(parts) > 1 else 30

    try:
        analytics = get_analytics(self.sql)
        result = analytics.get_user_sentiment(self.botId, channel, target_nick, days)

        self.send_message(feedback, f"{nick}: Sentiment for {target_nick} (last {days} days):")
        self.send_message(feedback, f"  Overall: {result['overall_sentiment'].upper()} "
                                    f"(score: {result['average_score']:.2f})")
        self.send_message(feedback, f"  Breakdown: {result['positive_messages']} positive, "
                                    f"{result['negative_messages']} negative, "
                                    f"{result['neutral_messages']} neutral")
        self.send_message(feedback, f"  Total analyzed: {result['total_analyzed']} messages")

    except Exception as e:
        self.send_message(feedback, f"{nick}: Error: {e}")


def _cmd_user_partners(self, channel, feedback, nick, host, args):
    """!user partners <nick> [days]"""

    parts = args.split() if args else []

    if not parts:
        self.send_message(feedback, f"{nick}: Usage: !user partners <nick> [days=30]")
        return

    target_nick = parts[0]
    days = int(parts[1]) if len(parts) > 1 else 30

    try:
        analytics = get_analytics(self.sql)
        partners = analytics.get_conversation_partners(self.botId, channel, target_nick, days, limit=5)

        if not partners:
            self.send_message(feedback, f"{nick}: No conversation data found for {target_nick}")
            return

        self.send_message(feedback, f"{nick}: Top conversation partners for {target_nick}:")

        for i, p in enumerate(partners[:5], 1):
            self.send_message(feedback, f"  {i}. {p['partner']}: {p['total_interactions']} interactions "
                                        f"({p['replies_to']}→, {p['replies_from']}←)")

    except Exception as e:
        self.send_message(feedback, f"{nick}: Error: {e}")


def _cmd_user_active(self, channel, feedback, nick, host, args):
    """!user active <nick> [days]"""

    parts = args.split() if args else []

    if not parts:
        self.send_message(feedback, f"{nick}: Usage: !user active <nick> [days=30]")
        return

    target_nick = parts[0]
    days = int(parts[1]) if len(parts) > 1 else 30

    try:
        analytics = get_analytics(self.sql)

        # Get peak hours
        hours = analytics.get_user_peak_hours(self.botId, channel, target_nick, days)

        if not hours:
            self.send_message(feedback, f"{nick}: No activity data found for {target_nick}")
            return

        self.send_message(feedback, f"{nick}: Peak hours for {target_nick} (last {days} days):")

        for i, h in enumerate(hours[:5], 1):
            self.send_message(feedback, f"  {i}. {h['time_label']}: {h['messages']} msgs "
                                        f"({h['percentage']:.1f}% of activity)")

        # Get peak days
        days_data = analytics.get_user_peak_days(self.botId, channel, target_nick, max(4, days // 7))

        if days_data:
            top_day = days_data[0]
            self.send_message(feedback, f"  Most active day: {top_day['day_name']} "
                                        f"({top_day['percentage']:.1f}%)")

    except Exception as e:
        self.send_message(feedback, f"{nick}: Error: {e}")


def _cmd_user_compare(self, channel, feedback, nick, host, args):
    """!user compare <nick1> <nick2> [days]"""

    parts = args.split() if args else []

    if len(parts) < 2:
        self.send_message(feedback, f"{nick}: Usage: !user compare <nick1> <nick2> [days=30]")
        return

    nick1 = parts[0]
    nick2 = parts[1]
    days = int(parts[2]) if len(parts) > 2 else 30

    try:
        analytics = get_analytics(self.sql)
        result = analytics.compare_users(self.botId, channel, nick1, nick2, days)

        if 'error' in result:
            self.send_message(feedback, f"{nick}: {result['error']}")
            return

        self.send_message(feedback, f"{nick}: Comparing {nick1} vs {nick2} (last {days} days):")

        # Messages
        m = result['metrics']['total_messages']
        self.send_message(feedback, f"  📊 Messages: {nick1}={m[nick1]}, {nick2}={m[nick2]} "
                                    f"(winner: {m['winner']})")

        # Words
        w = result['metrics']['avg_words']
        self.send_message(feedback, f"  📝 Avg words: {nick1}={w[nick1]:.1f}, {nick2}={w[nick2]:.1f} "
                                    f"(winner: {w['winner']})")

        # Sentiment
        s = result['metrics']['sentiment']
        self.send_message(feedback, f"  💭 Sentiment: {nick1}={s[nick1]}, {nick2}={s[nick2]}")

    except Exception as e:
        self.send_message(feedback, f"{nick}: Error: {e}")


def _cmd_user_link(self, channel, feedback, nick, host, args):
    """!user link <nick>"""

    target_nick = args.strip() if args else ''

    if not target_nick:
        self.send_message(feedback, f"{nick}: Usage: !user link <nick>")
        return

    # Get API port from config
    try:
        from modules.stats.stats_config import get_stats_config
        config = get_stats_config()
        port = config.get_api_port()
    except:
        port = 8000

    url = f"http://localhost:{port}/user/{channel}/{target_nick}"
    self.send_message(feedback, f"{nick}: User analytics for {target_nick}: {url}")


def cmd_listplugins(self, channel, feedback, nick, host, msg):
    """
    Listează toate plugin-urile încărcate și disponibile.

    Usage: .listplugins
    """
    result = self.check_command_access(channel, nick, host, '10', feedback)
    if not result:
        return

    if not hasattr(self, 'plugin_manager'):
        self.send_message(feedback, "❌ Plugin system not initialized")
        return

    try:
        plugins = self.plugin_manager.list_plugins()

        if not plugins:
            self.send_message(feedback, "📦 No plugins found")
            return

        # Separate loaded vs failed
        loaded = [p for p in plugins if p.enabled and not p.error]
        failed = [p for p in plugins if p.error]

        self.send_message(feedback, f"📦 Plugins ({len(loaded)} loaded, {len(failed)} failed):")

        # Show loaded plugins
        for plugin in loaded:
            cmd_count = len(plugin.commands)
            version = plugin.version
            self.send_message(
                feedback,
                f"  ✅ {plugin.name} v{version} - {cmd_count} commands - {plugin.author}"
            )

        # Show failed plugins
        for plugin in failed:
            self.send_message(
                feedback,
                f"  ❌ {plugin.name} - Error: {plugin.error[:50]}"
            )

    except Exception as e:
        self.send_message(feedback, f"❌ Error: {e}")


def cmd_loadplugin(self, channel, feedback, nick, host, msg):
    """
    Încarcă un plugin.

    Usage: .loadplugin <plugin_name>

    Level: 8 (owner only)
    """
    result = self.check_command_access(channel, nick, host, '8', feedback)
    if not result:
        return

    if not hasattr(self, 'plugin_manager'):
        self.send_message(feedback, "❌ Plugin system not initialized")
        return

    if not msg or not msg.strip():
        self.send_message(feedback, "Usage: .loadplugin <plugin_name>")
        return

    plugin_name = msg.strip()

    try:
        # Find plugin file
        from pathlib import Path
        plugin_path = None

        for plugin_dir in self.plugin_manager.plugin_dirs:
            candidate = Path(plugin_dir) / f"{plugin_name}.py"
            if candidate.exists():
                plugin_path = candidate
                break

        if not plugin_path:
            self.send_message(feedback, f"❌ Plugin file not found: {plugin_name}.py")
            return

        # Load plugin
        self.send_message(feedback, f"⏳ Loading plugin: {plugin_name}...")

        success = self.plugin_manager.load_plugin(plugin_path)

        if success:
            metadata = self.plugin_manager.get_plugin_info(plugin_name)
            cmd_count = len(metadata.commands) if metadata else 0
            self.send_message(feedback, f"✅ Plugin '{plugin_name}' loaded ({cmd_count} commands)")
        else:
            metadata = self.plugin_manager.get_plugin_info(plugin_name)
            error = metadata.error if metadata else "Unknown error"
            self.send_message(feedback, f"❌ Failed to load plugin: {error}")

    except Exception as e:
        self.send_message(feedback, f"❌ Error: {e}")


def cmd_unloadplugin(self, channel, feedback, nick, host, msg):
    """
    Descarcă un plugin.

    Usage: .unloadplugin <plugin_name>

    Level: 8 (owner only)
    """
    result = self.check_command_access(channel, nick, host, '8', feedback)
    if not result:
        return

    if not hasattr(self, 'plugin_manager'):
        self.send_message(feedback, "❌ Plugin system not initialized")
        return

    if not msg or not msg.strip():
        self.send_message(feedback, "Usage: .unloadplugin <plugin_name>")
        return

    plugin_name = msg.strip()

    try:
        success = self.plugin_manager.unload_plugin(plugin_name)

        if success:
            self.send_message(feedback, f"✅ Plugin '{plugin_name}' unloaded")
        else:
            self.send_message(feedback, f"❌ Failed to unload plugin '{plugin_name}'")

    except Exception as e:
        self.send_message(feedback, f"❌ Error: {e}")


def cmd_reloadplugin(self, channel, feedback, nick, host, msg):
    """
    Reload un plugin (unload + load).

    Usage: .reloadplugin <plugin_name>

    Level: 8 (owner only)
    """
    result = self.check_command_access(channel, nick, host, '8', feedback)
    if not result:
        return

    if not hasattr(self, 'plugin_manager'):
        self.send_message(feedback, "❌ Plugin system not initialized")
        return

    if not msg or not msg.strip():
        self.send_message(feedback, "Usage: .reloadplugin <plugin_name>")
        return

    plugin_name = msg.strip()

    try:
        self.send_message(feedback, f"⏳ Reloading plugin: {plugin_name}...")

        success = self.plugin_manager.reload_plugin(plugin_name)

        if success:
            metadata = self.plugin_manager.get_plugin_info(plugin_name)
            cmd_count = len(metadata.commands) if metadata else 0
            self.send_message(feedback, f"✅ Plugin '{plugin_name}' reloaded ({cmd_count} commands)")
        else:
            self.send_message(feedback, f"❌ Failed to reload plugin '{plugin_name}'")

    except Exception as e:
        self.send_message(feedback, f"❌ Error: {e}")


def cmd_plugininfo(self, channel, feedback, nick, host, msg):
    """
    Afișează informații despre un plugin.

    Usage: .plugininfo <plugin_name>
    """
    result = self.check_command_access(channel, nick, host, '10', feedback)
    if not result:
        return

    if not hasattr(self, 'plugin_manager'):
        self.send_message(feedback, "❌ Plugin system not initialized")
        return

    if not msg or not msg.strip():
        self.send_message(feedback, "Usage: .plugininfo <plugin_name>")
        return

    plugin_name = msg.strip()

    try:
        metadata = self.plugin_manager.get_plugin_info(plugin_name)

        if not metadata:
            self.send_message(feedback, f"❌ Plugin not found: {plugin_name}")
            return

        # Format output
        lines = [
            f"📦 Plugin: {metadata.name}",
            f"  • Version: {metadata.version}",
            f"  • Author: {metadata.author}",
            f"  • Description: {metadata.description or 'N/A'}",
            f"  • Status: {'✅ Loaded' if metadata.enabled else '❌ Failed'}",
            f"  • Commands: {', '.join(metadata.commands) if metadata.commands else 'None'}",
        ]

        if metadata.dependencies:
            lines.append(f"  • Dependencies: {', '.join(metadata.dependencies)}")

        if metadata.loaded_at:
            lines.append(f"  • Loaded: {metadata.loaded_at.strftime('%Y-%m-%d %H:%M:%S')}")

        if metadata.error:
            lines.append(f"  • Error: {metadata.error}")

        for line in lines:
            self.send_message(feedback, line)

    except Exception as e:
        self.send_message(feedback, f"❌ Error: {e}")
