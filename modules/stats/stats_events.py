"""
IRC Statistics - Event Capture Module
======================================
Captează evenimente IRC și le scrie în IRC_EVENTS table.
Hook-ul se apelează din BlackBoT.py la fiecare eveniment relevant.

FIX IMPORTANT (SQLite existing schema):
- În DB-ul tău, IRC_EVENTS.channel este încă NOT NULL (tabelă veche).
  Ca să nu crape la QUIT/NICK/global/PM, convertim channel=None -> "" (empty string)
  înainte de insert.

Nota:
- Aggregatorul tău deja ignoră event['channel'] falsy, deci "" e perfect safe.
"""

import time
import threading
from collections import deque
from core.log import get_logger

logger = get_logger("stats_events")


def _normalize_channel_for_db(channel):
    """
    DB compat: dacă channel e None, îl transformăm în "" (empty string)
    ca să evităm NOT NULL constraint failures pe tabele vechi.
    """
    if channel is None:
        return ""
    try:
        return str(channel)
    except Exception:
        return ""


class StatsEventCapture:
    """
    Singleton pentru capturarea evenimentelor IRC în DB.
    Thread-safe, cu batching pentru performanță.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, sql_instance=None, batch_size=50, flush_interval=5.0):
        if self._initialized:
            return

        self.sql = sql_instance
        self._initialized = True

        # Batch queue pentru insert-uri (performance optimization)
        self._event_queue = deque(maxlen=1000)  # max 1000 events în queue
        self._batch_lock = threading.Lock()
        self._batch_size = batch_size  # flush la N evenimente (configurable)
        self._last_flush = time.time()
        self._flush_interval = flush_interval  # sau flush la X secunde (configurable)

        # Stats cache (opțional, pentru debugging)
        self._events_captured = 0
        self._events_flushed = 0

    def set_sql(self, sql_instance):
        """Set SQL instance (dacă nu a fost setat la init)"""
        self.sql = sql_instance

    def capture_event(self, botId, channel, event_type, nick,
                     ident=None, host=None, message=None,
                     target_nick=None, reason=None, mode_change=None):
        """
        Captură un eveniment IRC și îl adaugă în queue.

        Args:
            botId: ID bot
            channel: #channel sau None pentru global events
            event_type: PRIVMSG|ACTION|JOIN|PART|QUIT|KICK|NICK|TOPIC|MODE
            nick: nickname-ul userului
            ident: ident (user)
            host: hostname
            message: mesajul (pentru PRIVMSG/ACTION/TOPIC)
            target_nick: pentru KICK/NICK (kicked user sau new nick)
            reason: pentru KICK/PART/QUIT
            mode_change: pentru MODE (+o/-o etc)
        """
        if not self.sql:
            logger.warning("StatsEventCapture: SQL instance not set, event ignored")
            return

        ts = int(time.time())

        # Pre-calculate metrics pentru PRIVMSG/ACTION
        metrics = self._calculate_metrics(message) if message else {}

        # ✅ normalize channel for DB safety (None -> "")
        channel_db = _normalize_channel_for_db(channel)

        event = {
            'botId': botId,
            'channel': channel_db,
            'ts': ts,
            'event_type': event_type,
            'nick': nick,
            'ident': ident,
            'host': host,
            'message': message,
            'target_nick': target_nick,
            'reason': reason,
            'mode_change': mode_change,
            **metrics
        }

        with self._batch_lock:
            self._event_queue.append(event)
            self._events_captured += 1

            # Auto-flush dacă batch e plin sau a trecut prea mult timp
            should_flush = (
                len(self._event_queue) >= self._batch_size or
                (time.time() - self._last_flush) >= self._flush_interval
            )

        if should_flush:
            self.flush()

    def flush(self):
        """
        Scrie toate evenimentele din queue în DB.
        Poate fi apelat manual sau automat când batch-ul e plin.
        """
        if not self.sql:
            return

        with self._batch_lock:
            if not self._event_queue:
                return

            # Extrage toate evenimentele din queue
            events_to_insert = list(self._event_queue)
            self._event_queue.clear()
            self._last_flush = time.time()

        # INSERT batch (fără lock, pentru a nu bloca noi capture-uri)
        try:
            insert_sql = """
                INSERT INTO IRC_EVENTS (
                    botId, channel, ts, event_type, nick, ident, host,
                    message, target_nick, reason, mode_change,
                    words, chars, is_question, is_exclamation, has_url, is_caps
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """

            for event in events_to_insert:
                # ✅ extra safety: ensure channel is never None at insert
                channel_db = _normalize_channel_for_db(event.get('channel'))

                params = (
                    event['botId'],
                    channel_db,
                    event['ts'],
                    event['event_type'],
                    event['nick'],
                    event.get('ident'),
                    event.get('host'),
                    event.get('message'),
                    event.get('target_nick'),
                    event.get('reason'),
                    event.get('mode_change'),
                    event.get('words', 0),
                    event.get('chars', 0),
                    event.get('is_question', 0),
                    event.get('is_exclamation', 0),
                    event.get('has_url', 0),
                    event.get('is_caps', 0),
                )
                self.sql.sqlite3_insert(insert_sql, params)

            self._events_flushed += len(events_to_insert)

        except Exception as e:
            logger.error(f"Failed to flush events to DB: {e}", exc_info=True)

    def _calculate_metrics(self, message):
        """
        Pre-calculează metrics pentru un mesaj.
        Similar cu calculate_message_metrics din stats_schema.py
        """
        if not message:
            return {
                'words': 0,
                'chars': 0,
                'is_question': 0,
                'is_exclamation': 0,
                'has_url': 0,
                'is_caps': 0,
            }

        words = len(message.split())
        chars = len(message)

        # URL detection
        has_url = 1 if ('http://' in message or 'https://' in message or 'www.' in message) else 0

        # Question/exclamation
        is_question = 1 if '?' in message else 0
        is_exclamation = 1 if '!' in message else 0

        # CAPS detection (>50% uppercase letters)
        letters = [c for c in message if c.isalpha()]
        if letters:
            uppercase_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
            is_caps = 1 if uppercase_ratio > 0.5 else 0
        else:
            is_caps = 0

        return {
            'words': words,
            'chars': chars,
            'is_question': is_question,
            'is_exclamation': is_exclamation,
            'has_url': has_url,
            'is_caps': is_caps,
        }

    def get_stats(self):
        """Debug info despre evenimente capturate"""
        return {
            'captured': self._events_captured,
            'flushed': self._events_flushed,
            'queue_size': len(self._event_queue),
        }


# =============================================================================
# Helper functions pentru easy usage în BlackBoT.py
# =============================================================================

_stats_capture = None

def init_stats_capture(sql_instance, batch_size=50, flush_interval=5.0):
    """Initialize stats capture cu SQL instance și config parameters"""
    global _stats_capture
    _stats_capture = StatsEventCapture(sql_instance, batch_size, flush_interval)
    logger.info(f"Stats capture initialized globally (batch={batch_size}, flush={flush_interval}s)")


def capture_privmsg(botId, channel, nick, ident, host, message):
    """Helper pentru PRIVMSG events"""
    if _stats_capture:
        _stats_capture.capture_event(
            botId=botId,
            channel=channel,
            event_type='PRIVMSG',
            nick=nick,
            ident=ident,
            host=host,
            message=message
        )


def capture_action(botId, channel, nick, ident, host, message):
    """Helper pentru ACTION (/me) events"""
    if _stats_capture:
        _stats_capture.capture_event(
            botId=botId,
            channel=channel,
            event_type='ACTION',
            nick=nick,
            ident=ident,
            host=host,
            message=message
        )


def capture_join(botId, channel, nick, ident, host):
    """Helper pentru JOIN events"""
    if _stats_capture:
        _stats_capture.capture_event(
            botId=botId,
            channel=channel,
            event_type='JOIN',
            nick=nick,
            ident=ident,
            host=host
        )


def capture_part(botId, channel, nick, ident, host, reason=None):
    """Helper pentru PART events"""
    if _stats_capture:
        _stats_capture.capture_event(
            botId=botId,
            channel=channel,
            event_type='PART',
            nick=nick,
            ident=ident,
            host=host,
            reason=reason
        )


def capture_quit(botId, nick, ident, host, reason=None):
    """Helper pentru QUIT events (global, nu per-channel)"""
    if _stats_capture:
        _stats_capture.capture_event(
            botId=botId,
            channel=None,  # va deveni "" la insert
            event_type='QUIT',
            nick=nick,
            ident=ident,
            host=host,
            reason=reason
        )


def capture_kick(botId, channel, kicker_nick, kicked_nick, reason=None):
    """Helper pentru KICK events"""
    if _stats_capture:
        _stats_capture.capture_event(
            botId=botId,
            channel=channel,
            event_type='KICK',
            nick=kicker_nick,
            target_nick=kicked_nick,
            reason=reason
        )


def capture_nick(botId, old_nick, new_nick):
    """Helper pentru NICK change events"""
    if _stats_capture:
        _stats_capture.capture_event(
            botId=botId,
            channel=None,  # va deveni "" la insert
            event_type='NICK',
            nick=old_nick,
            target_nick=new_nick
        )


def capture_topic(botId, channel, nick, new_topic):
    """Helper pentru TOPIC change events"""
    if _stats_capture:
        _stats_capture.capture_event(
            botId=botId,
            channel=channel,
            event_type='TOPIC',
            nick=nick,
            message=new_topic
        )


def capture_mode(botId, channel, nick, mode_change):
    """Helper pentru MODE change events"""
    if _stats_capture:
        _stats_capture.capture_event(
            botId=botId,
            channel=channel,
            event_type='MODE',
            nick=nick,
            mode_change=mode_change
        )


def flush_stats_events():
    """Manual flush al event queue"""
    if _stats_capture:
        _stats_capture.flush()


def get_capture_stats():
    """Debug stats despre event capture"""
    if _stats_capture:
        return _stats_capture.get_stats()
    return None
