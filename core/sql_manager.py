# core/sql_manager.py
from core.SQL import SQL
from core.environment_config import config

class SQLManager:
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = SQL(config.sqlite3_database)
            cls._instance.sqlite3_createTables()
        return cls._instance