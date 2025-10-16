# core/sql_manager.py
from core.SQL import SQL
import settings as s

class SQLManager:
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = SQL(s.sqlite3_database)
            cls._instance.sqlite3_createTables()
        return cls._instance