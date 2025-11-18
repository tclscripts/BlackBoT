# ───────────────────────────────────────────────
# Core Bot Log module
# ───────────────────────────────────────────────

# core/log.py — global line-rotating logger (line-based rollover)

from __future__ import annotations
import os, time, logging, threading
from collections import deque
from core.environment_config import config

__all__ = ["get_logger", "install_excepthook", "log_session_banner"]

_lock = threading.RLock()
_singleton_logger: logging.Logger | None = None


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
            # initialize line counter cheaply
            with open(self.baseFilename, "r", encoding=encoding or "utf-8", errors="ignore") as f:
                # read in chunks to avoid loading entire file into memory
                self._line_count = sum(1 for _ in f)
        except FileNotFoundError:
            self._line_count = 0
        except Exception:
            # fall back if count fails
            self._line_count = 0

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)
        # Compute number of physical lines that will be written
        lines = msg.count("\n") + 1
        with self._hlock:
            if self._line_count + lines > self.max_lines:
                self._do_rollover()
                self._line_count = 0
            super().emit(record)
            self._line_count += lines

    def _do_rollover(self) -> None:
        # Close the current stream to ensure consistent read
        if self.stream:
            self.stream.flush()
            self.stream.close()
            self.stream = None

        # Tail last max_lines lines into backup file
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        base = os.path.basename(self.baseFilename)
        dirn = os.path.dirname(self.baseFilename)
        backup_name = os.path.join(dirn, f"{base}.{timestamp}.bak")

        try:
            dq: deque[str] = deque(maxlen=self.max_lines)
            with open(self.baseFilename, "r", encoding=self.encoding or "utf-8", errors="ignore") as src:
                for line in src:
                    dq.append(line)
            # write tail into backup
            with open(backup_name, "w", encoding="utf-8") as dst:
                dst.writelines(dq)
        except FileNotFoundError:
            # nothing to backup
            pass
        except Exception:
            # best-effort; never crash the bot on logging issues
            pass

        # Truncate original file (start fresh)
        try:
            with open(self.baseFilename, "w", encoding="utf-8") as dst:
                dst.write("")
        except Exception:
            pass

        # Reopen stream for subsequent emits
        self.stream = self._open()

        # Prune older backups
        if self.backup_count > 0:
            try:
                prefix = os.path.basename(self.baseFilename) + "."
                files = [f for f in os.listdir(dirn or ".") if f.startswith(prefix) and f.endswith(".bak")]
                files.sort(reverse=True)  # newest first (by timestamp in name)
                for old in files[self.backup_count:]:
                    try:
                        os.remove(os.path.join(dirn, old))
                    except Exception:
                        pass
            except Exception:
                pass


def get_logger(name: str = "blackbot") -> logging.Logger:
    """Return a global, preconfigured logger (singleton)."""
    global _singleton_logger
    with _lock:
        if _singleton_logger is not None:
            return _singleton_logger

        # Resolve settings with safe defaults
        try:
            logs_dir = getattr(config, "logs_dir", "logs")
            log_file = getattr(config, "log_file", "blackbot.log")
            max_lines = int(getattr(config, "log_max_lines", 100_000))
            backup_count = int(getattr(config, "log_backup_count", 10))
            level = getattr(config, "log_level", "INFO")
        except Exception:
            logs_dir, log_file, max_lines, backup_count, level = "logs", "blackbot.log", 100_000, 10, "INFO"

        _ensure_dir(logs_dir)
        path = os.path.join(logs_dir, log_file)

        logger = logging.getLogger(name)
        logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
        logger.propagate = False  # do not duplicate to root

        fmt = logging.Formatter(
            fmt="%(asctime)s | %(levelname)s | %(threadName)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        fh = LineCountRotatingFileHandler(path, max_lines=max_lines, backup_count=backup_count)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
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
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()))
    if details:
        # one key/value per line for readability
        for k, v in details.items():
            logger.info("   %s: %s", k, v)
    logger.info(sep)