# core/dcc_log_handler.py
"""
Custom logging handler that streams logs to DCC sessions in real-time.
Only sends logs to human users (not botlink connections).
"""

import logging
import threading
from typing import Set, Optional
from collections import deque


class DCCLogHandler(logging.Handler):
    """
    Custom logging handler that sends log records to DCC sessions.
    Thread-safe and prevents infinite loops from log messages generated
    during the send process itself.
    """

    def __init__(self, dcc_manager, level=logging.INFO):
        """
        Initialize the DCC log handler.

        Args:
            dcc_manager: DCCManager instance
            level: Minimum log level to stream (default: INFO)
        """
        super().__init__(level)
        self.dcc_manager = dcc_manager
        self._lock = threading.RLock()
        self._emitting = threading.local()  # Prevent recursion per-thread
        self._enabled = True

        # Buffer pentru când DCC nu e disponibil
        self._buffer = deque(maxlen=100)  # Keep last 100 messages
        self._buffer_lock = threading.Lock()

        # Formatare specifică pentru DCC
        formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-7s | %(name)s | %(message)s',
            datefmt='%H:%M:%S'
        )
        self.setFormatter(formatter)

    def emit(self, record: logging.LogRecord) -> None:
        """
        Emit a log record to all connected DCC sessions (non-botlink).

        Args:
            record: The log record to emit
        """
        # Prevent infinite recursion
        if getattr(self._emitting, 'active', False):
            return

        if not self._enabled:
            return

        try:
            self._emitting.active = True

            # Format the message
            msg = self.format(record)

            # Filter out DCC-related logs to prevent loops
            if self._should_skip_record(record):
                return

            # Buffer the message
            with self._buffer_lock:
                self._buffer.append(msg)

            # Send to all human DCC sessions
            self._send_to_dcc_sessions(msg)

        except Exception as e:
            # Use handleError to avoid infinite loops
            self.handleError(record)
        finally:
            self._emitting.active = False

    def _should_skip_record(self, record: logging.LogRecord) -> bool:
        """
        Determine if a log record should be skipped.
        Filters out logs from DCC module itself to prevent loops.

        Args:
            record: The log record to check

        Returns:
            True if record should be skipped
        """
        # Skip DCC internal logs
        if record.name.startswith('dcc'):
            return True

        # Skip logs about sending to DCC
        if 'send_text' in record.getMessage().lower():
            return True

        # Skip very verbose debug logs from specific modules
        if record.levelno < logging.INFO:
            skip_modules = ['twisted', 'asyncio', 'urllib3']
            if any(record.name.startswith(mod) for mod in skip_modules):
                return True

        return False

    def _send_to_dcc_sessions(self, msg: str) -> None:
        """
        Send message to all human (non-botlink) DCC sessions.

        Args:
            msg: The formatted log message
        """
        if not self.dcc_manager:
            return

        try:
            # Get all sessions
            sessions = getattr(self.dcc_manager, 'sessions', {})

            for nick, session in sessions.items():
                # Skip if no transport
                if not session or not session.transport:
                    continue

                # Skip botlink sessions
                if session.meta.get('botlink') == '1':
                    continue

                # Skip if explicitly disabled for this session
                if session.meta.get('log_streaming') == '0':
                    continue

                # Send the log message
                try:
                    session.transport.send_text(f"[LOG] {msg}")
                except Exception as e:
                    # Don't log errors about sending logs (infinite loop!)
                    pass

        except Exception:
            # Silently fail - we don't want logging to break the bot
            pass

    def send_buffered_logs(self, nick: str, max_lines: int = 20) -> int:
        """
        Send buffered logs to a specific DCC session.
        Useful when a user connects and wants to see recent activity.

        Args:
            nick: Nick of the DCC session
            max_lines: Maximum number of buffered lines to send

        Returns:
            Number of lines sent
        """
        with self._buffer_lock:
            if not self._buffer:
                return 0

            # Get last N messages
            messages = list(self._buffer)[-max_lines:]

        sent = 0
        for msg in messages:
            try:
                if self.dcc_manager.send_text(nick, f"[BUFFERED] {msg}"):
                    sent += 1
            except Exception:
                break

        return sent

    def enable_for_session(self, nick: str) -> bool:
        """
        Enable log streaming for a specific session.

        Args:
            nick: Nick of the DCC session

        Returns:
            True if enabled successfully
        """
        try:
            session = self.dcc_manager.sessions.get(nick.lower())
            if session:
                session.meta['log_streaming'] = '1'
                return True
        except Exception:
            pass
        return False

    def disable_for_session(self, nick: str) -> bool:
        """
        Disable log streaming for a specific session.

        Args:
            nick: Nick of the DCC session

        Returns:
            True if disabled successfully
        """
        try:
            session = self.dcc_manager.sessions.get(nick.lower())
            if session:
                session.meta['log_streaming'] = '0'
                return True
        except Exception:
            pass
        return False

    def is_enabled_for_session(self, nick: str) -> bool:
        """
        Check if log streaming is enabled for a session.

        Args:
            nick: Nick of the DCC session

        Returns:
            True if enabled (or default enabled)
        """
        try:
            session = self.dcc_manager.sessions.get(nick.lower())
            if session:
                # Default is enabled ('1') unless explicitly disabled ('0')
                return session.meta.get('log_streaming', '1') == '1'
        except Exception:
            pass
        return False

    def enable(self) -> None:
        """Enable the handler globally."""
        with self._lock:
            self._enabled = True

    def disable(self) -> None:
        """Disable the handler globally."""
        with self._lock:
            self._enabled = False

    def close(self) -> None:
        """Clean up resources."""
        self.disable()
        super().close()


def install_dcc_log_handler(dcc_manager, logger_name: Optional[str] = None,
                            level: int = logging.INFO) -> DCCLogHandler:
    """
    Install the DCC log handler on a logger.

    Args:
        dcc_manager: DCCManager instance
        logger_name: Name of logger to attach to (None = root logger)
        level: Minimum log level to stream

    Returns:
        The installed handler
    """
    handler = DCCLogHandler(dcc_manager, level=level)

    # Get the target logger
    target_logger = logging.getLogger(logger_name)

    # Add the handler
    target_logger.addHandler(handler)

    return handler


def remove_dcc_log_handler(logger_name: Optional[str] = None) -> None:
    """
    Remove all DCC log handlers from a logger.

    Args:
        logger_name: Name of logger (None = root logger)
    """
    target_logger = logging.getLogger(logger_name)

    # Remove all DCCLogHandler instances
    for handler in target_logger.handlers[:]:
        if isinstance(handler, DCCLogHandler):
            target_logger.removeHandler(handler)
            handler.close()