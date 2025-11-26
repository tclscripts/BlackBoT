# core/ban_expiration_manager.py

import time
import threading
import re
from core.log import get_logger
from core.threading_utils import ThreadWorker, get_event

logger = get_logger("ban_manager")


def _normalize_banmask_for_mode(ban_mask: str, ident: str | None, host: str | None, ban_type: str):
    """
    Transformă ban_mask-ul din DB într-o mască validă pentru MODE +b.
    - taie :realname dacă există;
    - dacă e regex, cade pe *!ident@host.
    """
    ban_type = (ban_type or "mask").lower()
    mask = ban_mask or ""

    # dacă e de forma *!*@host:realname -> păstrăm doar *!*@host
    if ":" in mask and ("!" in mask or "@" in mask):
        mask = mask.split(":", 1)[0]

    # dacă e regex sau ban_type == "regex", nu putem da direct MODE +b cu regex:
    looks_regex = (ban_type == "regex") or mask.startswith("^") or mask.endswith("$")
    if looks_regex:
        ident = ident or "*"
        host = host or "*"
        mask = f"*!{ident}@{host}"

    return mask



class BanExpirationManager:
    """
    Manages ban expiration with a single optimized timer thread.
    Only monitors the nearest expiring ban to minimize system overhead.
    """

    def __init__(self, bot_instance):
        """
        Initialize the ban expiration manager.

        Args:
            bot_instance: Reference to the BlackBoT instance
        """
        self.bot = bot_instance
        self.sql = bot_instance.sql
        self.botId = bot_instance.botId

        # Thread control
        self._lock = threading.RLock()
        self._timer_thread = None
        self._next_expiration = None  # (ban_id, expires_at, table, channel)
        self._running = False

        logger.info("🕐 BanExpirationManager initialized")

    def start(self):
        """Start the ban expiration manager."""
        with self._lock:
            if self._running:
                logger.warning("BanExpirationManager already running")
                return

            self._running = True
            # Find the next ban to expire and start monitoring
            self._schedule_next_expiration()
            logger.info("✅ BanExpirationManager started")

    def stop(self):
        """Stop the ban expiration manager (idempotent)."""
        with self._lock:
            if not self._running:
                return

            self._running = False

            if self._timer_thread:
                try:
                    self._timer_thread.stop()
                except Exception as e:
                    logger.error(f"Error stopping timer thread: {e}")
                finally:
                    self._timer_thread = None

            logger.info("🛑 BanExpirationManager stopped")

    def _get_next_expiring_ban(self):
        """
        Find the ban with the nearest expiration time.

        Returns:
            tuple: (ban_id, expires_at, table, channel) or None if no expiring bans
        """
        try:
            now = int(time.time())

            # Query both local and global bans for the nearest expiration
            query = """
                    SELECT 'BANS_LOCAL' AS table_name, id, expires_at, channel
                    FROM BANS_LOCAL
                    WHERE botId = ? \
                      AND expires_at IS NOT NULL \
                      AND expires_at > ?
                    UNION ALL
                    SELECT 'BANS_GLOBAL' AS table_name, id, expires_at, NULL AS channel
                    FROM BANS_GLOBAL
                    WHERE botId = ? \
                      AND expires_at IS NOT NULL \
                      AND expires_at > ?
                    ORDER BY expires_at ASC LIMIT 1 \
                    """

            result = self.sql.sqlite_select(query, (self.botId, now, self.botId, now))

            if result:
                row = result[0]
                return (row[1], row[2], row[0], row[3])  # (id, expires_at, table, channel)

            return None

        except Exception as e:
            logger.error(f"Error getting next expiring ban: {e}", exc_info=True)
            return None

    def _schedule_next_expiration(self):
        """
        Schedule a timer for the next ban expiration.
        Cancels any existing timer and creates a new one.
        """
        with self._lock:
            # Stop existing timer if any
            if self._timer_thread:
                try:
                    self._timer_thread.stop()
                    self._timer_thread = None
                except Exception as e:
                    logger.error(f"Error stopping existing timer: {e}")

            if not self._running:
                return

            # Get the next ban to expire
            next_ban = self._get_next_expiring_ban()

            if not next_ban:
                logger.info("ℹ️ No bans with expiration times found")
                self._next_expiration = None
                return

            ban_id, expires_at, table, channel = next_ban
            self._next_expiration = next_ban

            # Calculate delay until expiration
            now = int(time.time())
            delay = max(0, expires_at - now)

            scope = f"channel {channel}" if channel else "global"
            logger.info(f"⏰ Scheduled ban expiration: ID={ban_id} ({scope}) in {delay}s")

            # Start timer thread
            self._timer_thread = ThreadWorker(
                target=lambda: self._expiration_timer_worker(ban_id, expires_at, table, channel),
                name=f"ban_expiration_{ban_id}"
            )
            self._timer_thread.start()

    def _expiration_timer_worker(self, ban_id, expires_at, table, channel):
        """
        Worker function that waits for a ban to expire and then removes it.

        Args:
            ban_id: ID of the ban to expire
            expires_at: Unix timestamp when the ban expires
            table: Table name ('BANS_LOCAL' or 'BANS_GLOBAL')
            channel: Channel name (for local bans) or None (for global bans)
        """
        try:
            # Wait until expiration time
            now = int(time.time())
            delay = expires_at - now

            if delay > 0:
                time.sleep(delay)

            # Check if we should still proceed (bot might have stopped)
            if not self._running:
                return

            # Expire the ban
            self._expire_ban(ban_id, table, channel)

            # Schedule the next expiration
            self._schedule_next_expiration()

        except Exception as e:
            logger.error(f"Error in expiration timer worker: {e}", exc_info=True)
            # Try to schedule next anyway
            self._schedule_next_expiration()

    def _expire_ban(self, ban_id, table, channel):
        """
        Expire a ban by removing it from the database and IRC channel.

        Args:
            ban_id: ID of the ban to expire
            table: Table name ('BANS_LOCAL' or 'BANS_GLOBAL')
            channel: Channel name (for local bans) or None (for global bans)
        """
        try:
            # Get ban details before removing
            if table == 'BANS_LOCAL':
                query = "SELECT ban_mask, ban_type, channel FROM BANS_LOCAL WHERE id = ?"
            else:
                query = "SELECT ban_mask, ban_type, NULL FROM BANS_GLOBAL WHERE id = ?"

            result = self.sql.sqlite_select(query, (ban_id,))

            if not result:
                logger.warning(f"Ban ID {ban_id} not found in {table}")
                return

            ban_mask, ban_type, ban_channel = result[0]

            # Remove from database
            if table == 'BANS_LOCAL':
                self.sql.sqlite3_update("DELETE FROM BANS_LOCAL WHERE id = ?", (ban_id,))
            else:
                self.sql.sqlite3_update("DELETE FROM BANS_GLOBAL WHERE id = ?", (ban_id,))

            scope = f"channel {ban_channel or channel}" if table == 'BANS_LOCAL' else "global"
            logger.info(f"⏱️ Ban expired: ID={ban_id} mask={ban_mask} ({scope})")

            # Remove ban from IRC channel(s)
            channels_to_unban = []

            if table == 'BANS_LOCAL' and ban_channel:
                channels_to_unban = [ban_channel]
            elif table == 'BANS_GLOBAL':
                # Global bans apply to all channels
                channels_to_unban = [ch for ch in self.bot.channels if ch.startswith('#')]

            # Remove MODE +b from channels
            for ch in channels_to_unban:
                try:
                    # For IRC MODE -b, we need a simple mask without realname
                    mode_mask = _normalize_banmask_for_mode(ban_mask)
                    self.bot.sendLine(f"MODE {ch} -b {mode_mask}")
                    logger.info(f"🔓 Removed ban {mode_mask} from {ch}")
                except Exception as e:
                    logger.error(f"Error removing ban from {ch}: {e}")

        except Exception as e:
            logger.error(f"Error expiring ban ID {ban_id}: {e}", exc_info=True)

    def on_ban_added(self, ban_id, expires_at):
        """
        Called when a new ban is added to check if we need to reschedule.

        Args:
            ban_id: ID of the newly added ban
            expires_at: Unix timestamp when the ban expires (or None if permanent)
        """
        if expires_at is None:
            return  # Permanent ban, no expiration

        with self._lock:
            # Check if this ban expires sooner than the current one
            if self._next_expiration is None:
                # No timer running, schedule this one
                self._schedule_next_expiration()
            else:
                current_expires = self._next_expiration[1]
                if expires_at < current_expires:
                    # This ban expires sooner, reschedule
                    logger.info(f"🔄 Rescheduling: new ban ID={ban_id} expires sooner")
                    self._schedule_next_expiration()

    def _mode_mask_for_user(self, ban_mask: str, ban_type: str, ident: str | None, host: str | None) -> str:
        """
        Construiește masca pentru MODE +b când avem deja user-ul:
        - taie :realname din ban_mask;
        - dacă ban-ul e regex, cade pe *!ident@host.
        """
        # mai întâi normalizăm ban_mask-ul (fără realname)
        mask = _normalize_banmask_for_mode(ban_mask)

        bt = (ban_type or "mask").lower()
        looks_regex = bt == "regex" or mask.startswith("^") or mask.endswith("$")
        if looks_regex:
            ident = ident or "*"
            host = host or "*"
            mask = f"*!{ident}@{host}"

        return mask

    def check_user_against_bans(self, channel, nick, ident, host, realname=None):
        """
        Check if a user matches any active bans and kick/ban them if so.

        Reguli:
          - ident poate fi "user" sau "~user"
          - masca *!user@host prinde:
                nick!user@host
                nick!~user@host
          - masca *!~user@host prinde DOAR:
                nick!~user@host
          - masca *!*@host:realname → se aplică doar dacă se potrivește și hostmask, și realname
        """
        try:
            bans = self.sql.sqlite_get_active_bans_for_channel(self.botId, channel)
            if not bans:
                return False

            nick = nick or "*"
            ident = ident or "*"
            host = host or "*"

            # Construim formele posibile de hostmask pentru user
            ident_core = ident.lstrip("~") or "*"

            user_full_forms = set()
            user_full_forms.add(f"{nick}!{ident}@{host}")  # exact cum e
            user_full_forms.add(f"{nick}!{ident_core}@{host}")  # fără ~
            user_full_forms.add(f"{nick}!~{ident_core}@{host}")  # cu ~

            # scoatem eventualele duplicate
            user_full_forms = {u for u in user_full_forms if u}

            if realname:
                user_full_real_forms = {u + ":" + realname for u in user_full_forms}
            else:
                user_full_real_forms = set()

            for ban_row in bans:
                scope = ban_row[0]  # 'GLOBAL' or 'LOCAL'
                ban_id = ban_row[1]
                ban_mask = ban_row[6]
                ban_type = (ban_row[7] or "mask").lower()
                reason = ban_row[9] or "no reason"
                table = 'BANS_GLOBAL' if scope == 'GLOBAL' else 'BANS_LOCAL'

                matched = False

                # -----------------------------
                # 1) REGEX BANS
                # -----------------------------
                if ban_type == "regex":
                    try:
                        rx = re.compile(ban_mask)
                    except Exception as e:
                        logger.error(f"Invalid regex in ban ID {ban_id}: {e}")
                        continue

                    # încercăm pe toate formele de user_full și user_full:realname
                    for u in list(user_full_forms) + list(user_full_real_forms):
                        if rx.search(u):
                            matched = True
                            break

                    # fallback: și pe realname simplu
                    if not matched and realname:
                        if rx.search(realname):
                            matched = True

                # -----------------------------
                # 2) MASK BANS CLASICE
                # -----------------------------
                else:
                    mask = ban_mask or ""

                    # Ban de forma host:realname
                    if ":" in mask:
                        host_part, real_part = mask.split(":", 1)

                        rx_host = self._mask_to_regex(host_part)

                        # host-ul trebuie să se potrivească pe cel puțin una din forme
                        host_ok = any(rx_host.match(u) for u in user_full_forms)

                        if host_ok:
                            if not realname:
                                # ban cere realname, dar noi nu-l știm încă -> nu aplicăm
                                logger.debug(
                                    f"[ban] ID={ban_id} on {channel}: host match pentru {nick}, "
                                    f"dar realname nu e încă disponibil."
                                )
                            else:
                                rx_real = self._mask_to_regex(real_part)
                                if rx_real.match(realname):
                                    matched = True
                                    logger.info(
                                        f"🎯 Ban matched host+realname for {nick} "
                                        f"in {channel}, ban_id={ban_id}, mask={ban_mask}"
                                    )

                    # Ban doar pe hostmask (fără :realname)
                    else:
                        rx = self._mask_to_regex(mask)
                        if any(rx.match(u) for u in user_full_forms):
                            matched = True
                            logger.info(
                                f"🎯 Ban matched host-only for {nick} "
                                f"in {channel}, ban_id={ban_id}, mask={ban_mask}"
                            )

                if not matched:
                    continue

                # Avem match → construim masca pentru MODE +b
                try:
                    mode_mask = _normalize_banmask_for_mode(ban_mask, ident, host, ban_type)
                except TypeError:
                    # fallback dacă funcția are semnătură veche
                    mode_mask = _normalize_banmask_for_mode(ban_mask)

                try:
                    self.bot.sendLine(f"MODE {channel} +b {mode_mask}")
                    self.bot.sendLine(f"KICK {channel} {nick} :Banned ({reason})")

                    # marcăm banul ca aplicat (statistică)
                    try:
                        self.sql.sqlite_mark_ban_applied(table, ban_id)
                    except Exception:
                        pass

                    scope_str = "global" if scope == 'GLOBAL' else f"local ({channel})"
                    logger.info(
                        f"🚫 Banned user {nick} in {channel} "
                        f"(matched {scope_str} ban ID={ban_id}, mask={ban_mask})"
                    )
                    return True

                except Exception as e:
                    logger.error(f"Error applying ban to {nick}: {e}")

            return False

        except Exception as e:
            logger.error(f"Error checking user against bans: {e}", exc_info=True)
            return False

    def _mask_to_regex(self, mask):
        """
        Convert IRC wildcard mask to regex pattern.

        Args:
            mask: IRC wildcard mask (e.g., "*!*@*.example.com")

        Returns:
            re.Pattern: Compiled regex pattern
        """
        esc = re.escape(mask)
        esc = esc.replace(r'\*', '.*').replace(r'\?', '.')
        return re.compile(r'^' + esc + r'$')

    def check_channel_users_on_join(self, channel):
        """
        Check all users in a channel when the bot joins.
        Kicks any users that match active bans.

        Args:
            channel: Channel name
        """
        try:
            logger.info(f"🔍 Checking users in {channel} for active bans")

            # Get all channel members
            members = []
            for row in self.bot.channel_details:
                if not isinstance(row, (list, tuple)) or not row:
                    continue
                if row[0] != channel:
                    continue

                member = {
                    'nick': row[1] if len(row) > 1 else None,
                    'ident': row[2] if len(row) > 2 else None,
                    'host': row[3] if len(row) > 3 else None,
                    'realname': row[5] if len(row) > 5 else None,
                }

                if member['nick']:
                    members.append(member)

            logger.info(f"ℹ️ Found {len(members)} users in {channel}")

            # Check each member against bans
            banned_count = 0
            for member in members:
                if self.check_user_against_bans(
                        channel,
                        member['nick'],
                        member['ident'] or '*',
                        member['host'] or '*',
                        member['realname']
                ):
                    banned_count += 1

            if banned_count > 0:
                logger.info(f"✅ Banned {banned_count} users in {channel}")

        except Exception as e:
            logger.error(f"Error checking channel users: {e}", exc_info=True)
