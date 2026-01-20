"""
IRC Statistics - Auto Hooks
============================
Injectează automat capture logic în metodele IRC ale botului.
Folosește monkey-patching pentru a intercepta evenimente.

FIX:
- PRIVMSG în PM are "channel" = nick-ul botului (ex: BlackBoT).
  Normalizăm: dacă target-ul nu începe cu '#', îl tratăm ca PM => channel=None.
"""

import functools
from core.log import get_logger

logger = get_logger("stats_hooks")


def _normalize_channel_target(target):
    """
    Twisted: privmsg(self, user, channel, msg)
      - pentru canal: channel = '#channel'
      - pentru PM: channel = 'BotNick' (sau uneori alt target non-#)
    Noi vrem:
      - canal real => '#...'
      - PM => None (ca să nu apară ca "canal" în stats)
    """
    try:
        if not target:
            return None
        t = str(target)
        if t.startswith("#"):
            return t
        return None
    except Exception:
        return None


def create_hook_wrapper(original_method, capture_func, event_type):
    """
    Creează un wrapper care apelează original method + capture function.
    """
    @functools.wraps(original_method)
    def wrapper(self, *args, **kwargs):
        result = original_method(self, *args, **kwargs)

        try:
            capture_func(self, *args, **kwargs)
        except Exception as e:
            logger.error(f"Stats capture failed for {event_type}: {e}")

        return result

    return wrapper


def inject_stats_hooks(bot_instance):
    """
    Injectează hook-uri în metodele IRC ale botului.
    """
    from modules.stats import stats_events

    # ==========================================================================
    # Hook 1: privmsg (mesaje + actions)
    # ==========================================================================
    original_privmsg = bot_instance.privmsg

    def hooked_privmsg(self, user, channel, msg):
        # Call original
        result = original_privmsg(user, channel, msg)

        # Capture event
        try:
            nick = user.split('!', 1)[0]
            try:
                ident, host = user.split('!', 1)[1].split('@', 1)
            except Exception:
                ident, host = None, None

            # ✅ normalize: dacă nu e #channel => PM => channel=None
            normalized_channel = _normalize_channel_target(channel)

            # Check if it's an ACTION (/me)
            if msg.startswith('\x01ACTION'):
                action_msg = msg[8:-1] if len(msg) > 8 else msg[8:]
                stats_events.capture_action(self.botId, normalized_channel, nick, ident, host, action_msg)
            else:
                stats_events.capture_privmsg(self.botId, normalized_channel, nick, ident, host, msg)

        except Exception as e:
            logger.debug(f"Stats capture failed for privmsg: {e}")

        return result

    bot_instance.privmsg = functools.partial(hooked_privmsg, bot_instance)
    logger.debug("✓ Hooked privmsg")

    # ==========================================================================
    # Hook 2: userJoined (JOIN)
    # ==========================================================================
    original_userJoined = bot_instance.userJoined

    def hooked_userJoined(self, user, channel):
        result = original_userJoined(user, channel)

        try:
            nick = user.split('!', 1)[0]
            try:
                ident, host = user.split('!', 1)[1].split('@', 1)
            except Exception:
                ident, host = None, None

            stats_events.capture_join(self.botId, channel, nick, ident, host)

        except Exception as e:
            logger.debug(f"Stats capture failed for JOIN: {e}")

        return result

    bot_instance.userJoined = functools.partial(hooked_userJoined, bot_instance)
    logger.debug("✓ Hooked userJoined")

    # ==========================================================================
    # Hook 3: userLeft (PART)
    # ==========================================================================
    original_userLeft = bot_instance.userLeft

    def hooked_userLeft(self, user, channel):
        result = original_userLeft(user, channel)

        try:
            nick = user.split('!', 1)[0]
            try:
                ident, host = user.split('!', 1)[1].split('@', 1)
            except Exception:
                ident, host = None, None

            stats_events.capture_part(self.botId, channel, nick, ident, host, reason=None)

        except Exception as e:
            logger.debug(f"Stats capture failed for PART: {e}")

        return result

    bot_instance.userLeft = functools.partial(hooked_userLeft, bot_instance)
    logger.debug("✓ Hooked userLeft")

    # ==========================================================================
    # Hook 4: userQuit (QUIT)
    # ==========================================================================
    original_userQuit = bot_instance.userQuit

    def hooked_userQuit(self, user, quitMessage):
        result = original_userQuit(user, quitMessage)

        try:
            nick = user.split('!', 1)[0]
            try:
                ident, host = user.split('!', 1)[1].split('@', 1)
            except Exception:
                ident, host = None, None

            stats_events.capture_quit(self.botId, nick, ident, host, quitMessage)

        except Exception as e:
            logger.debug(f"Stats capture failed for QUIT: {e}")

        return result

    bot_instance.userQuit = functools.partial(hooked_userQuit, bot_instance)
    logger.debug("✓ Hooked userQuit")

    # ==========================================================================
    # Hook 5: userKicked (KICK)
    # ==========================================================================
    original_userKicked = bot_instance.userKicked

    def hooked_userKicked(self, kickee, channel, kicker, message):
        result = original_userKicked(kickee, channel, kicker, message)

        try:
            kicker_nick = kicker.split('!', 1)[0] if '!' in kicker else kicker
            kickee_nick = kickee.split('!', 1)[0] if '!' in kickee else kickee

            stats_events.capture_kick(self.botId, channel, kicker_nick, kickee_nick, message)

        except Exception as e:
            logger.debug(f"Stats capture failed for KICK: {e}")

        return result

    bot_instance.userKicked = functools.partial(hooked_userKicked, bot_instance)
    logger.debug("✓ Hooked userKicked")

    # ==========================================================================
    # Hook 6: userRenamed (NICK) - dacă există
    # ==========================================================================
    if hasattr(bot_instance, 'userRenamed'):
        original_userRenamed = bot_instance.userRenamed

        def hooked_userRenamed(self, oldname, newname):
            result = original_userRenamed(oldname, newname)

            try:
                stats_events.capture_nick(self.botId, oldname, newname)
            except Exception as e:
                logger.debug(f"Stats capture failed for NICK: {e}")

            return result

        bot_instance.userRenamed = functools.partial(hooked_userRenamed, bot_instance)
        logger.debug("✓ Hooked userRenamed")

    # ==========================================================================
    # Hook 7: topicUpdated (TOPIC) - dacă există
    # ==========================================================================
    if hasattr(bot_instance, 'topicUpdated'):
        original_topicUpdated = bot_instance.topicUpdated

        def hooked_topicUpdated(self, user, channel, newTopic):
            result = original_topicUpdated(user, channel, newTopic)

            try:
                nick = user.split('!', 1)[0] if user else 'unknown'
                stats_events.capture_topic(self.botId, channel, nick, newTopic)
            except Exception as e:
                logger.debug(f"Stats capture failed for TOPIC: {e}")

            return result

        bot_instance.topicUpdated = functools.partial(hooked_topicUpdated, bot_instance)
        logger.debug("✓ Hooked topicUpdated")

    # ==========================================================================
    # Hook 8: modeChanged (MODE) - dacă există
    # ==========================================================================
    if hasattr(bot_instance, 'modeChanged'):
        original_modeChanged = bot_instance.modeChanged

        def hooked_modeChanged(self, user, channel, set_mode, modes, args):
            result = original_modeChanged(user, channel, set_mode, modes, args)

            try:
                nick = user.split('!', 1)[0] if user else 'unknown'
                mode_change = f"{'+' if set_mode else '-'}{modes} {' '.join(args) if args else ''}"
                stats_events.capture_mode(self.botId, channel, nick, mode_change)
            except Exception as e:
                logger.debug(f"Stats capture failed for MODE: {e}")

            return result

        bot_instance.modeChanged = functools.partial(hooked_modeChanged, bot_instance)
        logger.debug("✓ Hooked modeChanged")

    logger.info("✓ All IRC method hooks injected successfully")


# =============================================================================
# Alternative: Decorator-based approach (nu e folosit acum)
# =============================================================================

def stats_capture(event_type):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            result = func(self, *args, **kwargs)

            try:
                import stats_events

                if event_type == 'PRIVMSG':
                    user, channel, msg = args[0], args[1], args[2]
                    nick = user.split('!', 1)[0]
                    ident, host = None, None
                    try:
                        ident, host = user.split('!', 1)[1].split('@', 1)
                    except Exception:
                        pass

                    normalized_channel = _normalize_channel_target(channel)

                    if msg.startswith('\x01ACTION'):
                        action_msg = msg[8:-1] if len(msg) > 8 else msg[8:]
                        stats_events.capture_action(self.botId, normalized_channel, nick, ident, host, action_msg)
                    else:
                        stats_events.capture_privmsg(self.botId, normalized_channel, nick, ident, host, msg)

            except Exception as e:
                logger.debug(f"Stats capture failed: {e}")

            return result

        return wrapper

    return decorator
