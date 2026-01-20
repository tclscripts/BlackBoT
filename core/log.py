# ───────────────────────────────────────────────
# Core Bot Log module
# ───────────────────────────────────────────────

# core/log.py — global line-rotating logger (line-based rollover)

from __future__ import annotations
import os, time, logging, threading
from collections import deque
from core.environment_config import config

# MODIFICARE: Adaugam register_dcc_callback in __all__
__all__ = ["get_logger", "install_excepthook", "log_session_banner", "register_dcc_callback"]

_lock = threading.RLock()
_singleton_logger: logging.Logger | None = None

# ───────────────────────────────────────────────
# MODIFICARE START: Logica pentru DCC Broadcast
# ───────────────────────────────────────────────
_dcc_broadcast_callback = None


def register_dcc_callback(func):
    """
    Permite modulului DCC să se înregistreze pentru a primi loguri.
    """
    global _dcc_broadcast_callback
    _dcc_broadcast_callback = func


class DCCBroadcastHandler(logging.Handler):
    """
    Handler care trimite logurile formatate către funcția de callback din DCC.
    """

    def emit(self, record):
        global _dcc_broadcast_callback
        if _dcc_broadcast_callback:
            try:
                msg = self.format(record)
                _dcc_broadcast_callback(msg)
            except Exception:
                self.handleError(record)


# ───────────────────────────────────────────────
# MODIFICARE END
# ───────────────────────────────────────────────


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


class LineCountRotatingFileHandler(logging.FileHandler):
    """Rotates when line count exceeds a threshold.
    On rollover:
      - saves the *last* max_lines lines to a timestamped backup in the same dir
      - truncates the active file and continues logging
      - optionally prunes old backups beyond backup_count
    """

    def __init__(self, filename: str, mode: str = "a", encoding: str | None = "utf-8",
                 delay: bool = False, max_lines: int = 100_000, backup_count: int = 10):
        self.max_lines = int(max_lines)
        self.backup_count = int(backup_count)
        self._line_count = 0
        self._hlock = threading.RLock()
        _ensure_dir(os.path.dirname(filename) or ".")
        super().__init__(filename, mode=mode, encoding=encoding, delay=delay)
        try:
            # initialize line counter
            if os.path.exists(filename):
                with open(filename, "r", encoding=encoding, errors="ignore") as f:
                    self._line_count = sum(1 for _ in f)
        except Exception:
            self._line_count = 0

    def emit(self, record):
        try:
            msg = self.format(record)
            stream = self.stream
            if stream is None:
                # if delay is True, stream might be None
                stream = self._open()

            # Write and flush
            stream.write(msg + self.terminator)
            self.flush()

            # Increment line count
            with self._hlock:
                self._line_count += 1
                if self._line_count >= self.max_lines:
                    self.doRollover()
        except Exception:
            self.handleError(record)

    def doRollover(self):
        """Perform rollover by copying content to a timestamped file and truncating current."""
        if self.stream:
            self.stream.close()
            self.stream = None

        timestamp = time.strftime("%Y%m%d-%H%M%S")
        base, ext = os.path.splitext(self.baseFilename)
        backup_file = f"{base}_{timestamp}{ext}"

        try:
            # Rename current -> backup
            if os.path.exists(self.baseFilename):
                os.rename(self.baseFilename, backup_file)

            # Reopen current (empty)
            self.stream = self._open()
            self._line_count = 0

            # Cleanup old backups
            self._prune_backups(base, ext)

        except Exception:
            # if rename fails, try to reopen anyway
            pass

    def _prune_backups(self, base_path: str, ext: str):
        """Keep only the newest backup_count files."""
        if self.backup_count <= 0:
            return

        dir_name = os.path.dirname(base_path)
        base_name = os.path.basename(base_path)

        candidates = []
        for f in os.listdir(dir_name):
            # check if file looks like name_YYYYMMDD-HHMMSS.ext
            if f.startswith(base_name + "_") and f.endswith(ext):
                candidates.append(os.path.join(dir_name, f))

        candidates.sort(key=os.path.getmtime)

        while len(candidates) > self.backup_count:
            victim = candidates.pop(0)
            try:
                os.remove(victim)
            except OSError:
                pass


def get_logger(name: str = "bot") -> logging.Logger:
    global _singleton_logger
    with _lock:
        if _singleton_logger is not None:
            # child loggers
            if name != "bot" and not name.startswith("bot."):
                return _singleton_logger.getChild(name)
            return _singleton_logger
        logs_dir = getattr(config, "logs_dir", "logs")
        log_file = getattr(config, "log_file", "blackbot.log")
        max_lines = int(config.get("log_max_lines", 100000))
        backup_count = int(config.get("log_backup_count", 5))

        _ensure_dir(logs_dir)
        path = os.path.join(logs_dir, log_file)

        logger = logging.getLogger(name)
        logger.setLevel(getattr(logging, config.log_level.upper(), logging.INFO))
        logger.propagate = False  # do not duplicate to root

        fmt = logging.Formatter(
            fmt="%(asctime)s | %(levelname)s | %(threadName)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # 1. Handler-ul original pentru fisier
        fh = LineCountRotatingFileHandler(path, max_lines=max_lines, backup_count=backup_count)
        fh.setFormatter(fmt)
        logger.addHandler(fh)

        # ───────────────────────────────────────────────
        # MODIFICARE START: Atasam handler-ul DCC
        # ───────────────────────────────────────────────
        dcc_handler = DCCBroadcastHandler()
        dcc_handler.setFormatter(fmt)
        # Optional: daca vrei sa filtrezi nivelul pentru DCC (ex: doar INFO+)
        #dcc_handler.setLevel(logging.INFO)
        logger.addHandler(dcc_handler)
        # ───────────────────────────────────────────────
        # MODIFICARE END
        # ───────────────────────────────────────────────

        _singleton_logger = logger
        return logger


def install_excepthook(logger: logging.Logger | None = None) -> None:
    """Capture uncaught exceptions and write them to the logger."""
    if logger is None:
        logger = get_logger()

    import sys, traceback

    def _hook(exc_type, exc, tb):
        try:
            msg = "\n".join(traceback.format_exception(exc_type, exc, tb))
            logger.error("Uncaught exception:\n%s", msg)
        finally:
            # keep default behavior too
            sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _hook


def log_session_banner(logger: logging.Logger, title: str = "NEW SESSION", details: dict | None = None):
    """Write a clear separator at the beginning of a new run."""
    sep = "=" * 100
    logger.info(sep)
    logger.info(" %s  | pid=%s | started=%s", title, os.getpid(),
                time.strftime("%Y-%m-%d %H:%M:%S"))
    if details:
        for k, v in details.items():
            logger.info("   • %s: %s", k, v)
    logger.info(sep)