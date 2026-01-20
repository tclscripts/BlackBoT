# core/sql_manager.py
import threading
from core.SQL import SQL
from core.environment_config import config


class SQLManager:
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls):
        """Thread-safe singleton pattern with double-checked locking"""
        if cls._instance is None:
            with cls._lock:
                # Double-checked locking to prevent race conditions
                if cls._instance is None:
                    cls._instance = SQL(config.sqlite3_database)
                    cls._instance.sqlite3_createTables()
        return cls._instance

    @classmethod
    def reset_instance(cls):
        """Reset singleton instance (useful for testing or reconnection)"""
        with cls._lock:
            if cls._instance is not None:
                try:
                    cls._instance.close_connection()
                except:
                    pass
                cls._instance = None