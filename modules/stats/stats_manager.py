"""
IRC Statistics - Central Manager
=================================
Orchestrează întreg sistemul de statistici:
- Auto-install dependencies
- Initialize DB tables
- Start event capture
- Start aggregator thread
- Start API server thread
- Auto-hook IRC methods
"""

import sys
import threading
import time
from pathlib import Path
from core.log import get_logger
import socket

from modules.stats.stats_aggregator import (
    run_aggregation_periodic,
    run_pruning_periodic,
    get_pruner,
)

logger = get_logger("stats_manager")


class StatsManager:
    """
    Manager central pentru sistemul de statistici.
    Se inițializează o singură dată în BlackBoT.__init__
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

    def __init__(self, bot_instance=None):
        if self._initialized:
            return

        self.bot = bot_instance
        self.sql = None
        self.config = None

        self.aggregator_worker = None
        self.api_worker = None
        self.pruner_worker = None

        self.is_running = False
        self.dependencies_ok = False

        self._initialized = True
        logger.info("StatsManager initialized")

    def initialize(self, bot_instance):
        if self.is_running:
            logger.warning("Stats system already running")
            return True

        self.bot = bot_instance
        self.sql = bot_instance.sql

        logger.info("=" * 60)
        logger.info("Initializing IRC Statistics System")
        logger.info("=" * 60)

        try:
            if not self._load_config():
                logger.warning("Config load failed, using defaults")

            if not self.config.is_enabled():
                logger.info("Stats system is DISABLED in config")
                return False

            deps_ok = self._auto_install_dependencies()
            if not deps_ok:
                logger.warning("[WARNING] Dependencies not ready yet - will retry in background")
                self._schedule_deferred_init(bot_instance)
                return True

            return self._complete_initialization(bot_instance)

        except Exception as e:
            logger.error(f"Failed to initialize stats system: {e}", exc_info=True)
            return False

    def _legacy_initialize_rest(self):
        pass

    def _load_config(self):
        try:
            from modules.stats.stats_config import get_stats_config
            self.config = get_stats_config()

            logger.info("✓ Config loaded:")
            logger.info(f"  - Stats enabled: {self.config.is_enabled()}")
            logger.info(f"  - API enabled: {self.config.is_api_enabled()}")
            logger.info(f"  - API port: {self.config.get_api_port()}")
            logger.info(f"  - Aggregator enabled: {self.config.is_aggregator_enabled()}")
            logger.info(f"  - Aggregator interval: {self.config.get_aggregator_interval()}s")

            return True

        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            from modules.stats.stats_config import StatsConfig
            self.config = StatsConfig()
            return False

    def _auto_install_dependencies(self):
        try:
            from modules.stats.stats_autoinstall import check_stats_dependencies

            logger.info("Checking dependencies...")
            success = check_stats_dependencies()

            if success:
                logger.info("✓ All dependencies available")
                self.dependencies_ok = True
                return True

            logger.error("✗ Missing dependencies")
            logger.error("Launcher/Manager should install: fastapi, uvicorn, pydantic")
            logger.error("Stats system cannot start without these packages")
            return False

        except Exception as e:
            logger.error(f"Error during dependency check: {e}", exc_info=True)
            return False

    def _cleanup_old_threads(self):
        if self.aggregator_worker and hasattr(self.aggregator_worker, 'stop'):
            try:
                self.aggregator_worker.stop()
            except Exception:
                pass
        if self.api_worker and hasattr(self.api_worker, 'stop'):
            try:
                self.api_worker.stop()
            except Exception:
                pass

        if self.pruner_worker and hasattr(self.pruner_worker, 'stop'):
            try:
                self.pruner_worker.stop()
            except:
                pass
        self.aggregator_worker = None
        self.api_worker = None
        self.pruner_worker = None

    def _schedule_deferred_init(self, bot_instance):
        def deferred_init_worker():
            logger.info("[RETRY] Deferred stats init: waiting for dependencies...")

            max_retries = 10
            retry_delay = 30

            for attempt in range(1, max_retries + 1):
                try:
                    time.sleep(retry_delay)

                    logger.info(f"[RETRY] Retry {attempt}/{max_retries}: Checking dependencies...")

                    from modules.stats.stats_autoinstall import auto_install_stats_dependencies
                    success = auto_install_stats_dependencies()

                    if success:
                        logger.info("[OK] Dependencies now available - initializing stats...")

                        self.is_running = False
                        self._cleanup_old_threads()

                        result = self._complete_initialization(bot_instance)

                        if result:
                            logger.info("[OK] Deferred stats initialization SUCCESS!")
                            return

                        logger.warning(f"[WARNING] Initialization attempt {attempt} failed, will retry...")
                    else:
                        logger.debug(f"Dependencies not ready yet (attempt {attempt}/{max_retries})")

                except Exception as e:
                    logger.error(f"Error during deferred init attempt {attempt}: {e}")

            logger.error(f"[ERROR] Failed to initialize stats after {max_retries} attempts")

        thread = threading.Thread(
            target=deferred_init_worker,
            name="StatsDeferredInit",
            daemon=True
        )
        thread.start()
        logger.info("[SCHEDULED] Scheduled deferred stats initialization (will retry in background)")

    def _complete_initialization(self, bot_instance):
        try:
            if not self._create_tables():
                logger.error("Failed to create tables")
                return False

            if not self._init_event_capture():
                logger.error("Failed to init event capture")
                return False

            if not self._hook_irc_methods():
                logger.error("Failed to hook IRC methods")
                return False

            if self.config.is_aggregator_enabled():
                if not self._start_aggregator():
                    logger.warning("Failed to start aggregator")

                    # Start pruner
            if not self.start_pruner():
                logger.warning("Failed to start pruner worker (non-critical)")

            if self.config.is_api_enabled():
                if not self._start_api_server():
                    logger.warning("Failed to start API server")

            self.is_running = True
            logger.info("=" * 60)
            logger.info("[OK] IRC Statistics System started successfully")
            logger.info("=" * 60)

            return True

        except Exception as e:
            logger.error(f"Failed during initialization: {e}", exc_info=True)
            return False

    def _create_tables(self):
        try:
            from modules.stats.stats_schema import create_stats_tables

            logger.info("Creating stats tables...")
            create_stats_tables(self.sql)
            logger.info("✓ Stats tables created")
            return True

        except Exception as e:
            logger.error(f"Failed to create tables: {e}", exc_info=True)
            return False

    def _init_event_capture(self):
        try:
            from modules.stats import stats_events

            stats_events.init_stats_capture(
                self.sql,
                batch_size=self.config.get_batch_size(),
                flush_interval=self.config.get_flush_interval()
            )

            logger.info("✓ Event capture initialized")
            logger.info(f"  - Batch size: {self.config.get_batch_size()}")
            logger.info(f"  - Flush interval: {self.config.get_flush_interval()}s")
            return True

        except Exception as e:
            logger.error(f"Failed to init event capture: {e}", exc_info=True)
            return False

    def _hook_irc_methods(self):
        try:
            logger.info("Hooking IRC methods...")
            from modules.stats.stats_hooks import inject_stats_hooks
            inject_stats_hooks(self.bot)
            logger.info("✓ IRC methods hooked successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to hook IRC methods: {e}", exc_info=True)
            return False

    def _start_aggregator(self):
        try:
            from core.threading_utils import ThreadWorker
            from modules.stats import stats_aggregator

            interval = self.config.get_aggregator_interval()

            def aggregator_wrapper():
                stats_aggregator.run_aggregation_periodic(self.sql, interval)

            self.aggregator_worker = ThreadWorker(
                target=aggregator_wrapper,
                name="StatsAggregator",
                supervise=True
            )
            self.aggregator_worker.start()

            logger.info(f"✓ Aggregator started (interval: {interval}s)")
            return True

        except Exception as e:
            logger.error(f"Failed to start aggregator: {e}", exc_info=True)
            return False

    def start_pruner(self):

        try:
            # Check if enabled
            if not self.config.is_pruning_enabled():
                logger.info("Database pruning is disabled in config")
                return False

            # Stop existing pruner if running
            if self.pruner_worker and getattr(self.pruner_worker, "is_alive", lambda: False)():
                logger.warning("Pruner already running, stopping old instance...")
                try:
                    self.pruner_worker.stop()
                    time.sleep(1)
                except:
                    pass

            from core.threading_utils import ThreadWorker

            # Get config
            interval_days = self.config.get_pruning_interval_days()
            keep_months = self.config.get_pruning_keep_months()

            logger.info(f"Starting database pruner (interval: {interval_days} days, keep: {keep_months} months)")

            # Create worker
            self.pruner_worker = ThreadWorker(
                target=lambda: run_pruning_periodic(self.sql, interval_days, keep_months),
                name="stats_pruner",
                supervise=True
            )

            self.pruner_worker.start()

            logger.info("✅ Database pruner started successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to start database pruner: {e}", exc_info=True)
            return False

    def _is_port_free(self, host: str, port: int) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((host, int(port)))
            return True
        except OSError:
            return False

    def _wait_for_port(self, host: str, port: int, wait_total: float = 6.0, step: float = 0.25) -> bool:
        deadline = time.time() + wait_total
        while time.time() < deadline:
            if self._is_port_free(host, port):
                return True
            time.sleep(step)
        return self._is_port_free(host, port)

    def _pick_free_port(self, host: str, start_port: int, wait_total: float = 6.0) -> int:
        if self._wait_for_port(host, start_port, wait_total=wait_total, step=0.25):
            return int(start_port)

        p = int(start_port) + 1
        for _ in range(200):
            if self._is_port_free(host, p):
                return p
            p += 1

        return int(start_port)

    def _start_api_server(self):
        try:
            from modules.stats.stats_api_threaded import start_api_server_thread

            port = self.config.get_api_port()
            host = self.config.get_api_host()

            chosen_port = self._pick_free_port(host, port, wait_total=6.0)
            if chosen_port != port:
                logger.warning(f"⚠️ Stats API port {port} still busy after wait; switching to {chosen_port}")

            self.api_worker = start_api_server_thread(
                sql_instance=self.sql,
                bot_id=self.bot.botId,
                bot_instance=self.bot,  # ✅ CRITIC
                host=host,
                port=chosen_port
            )

            # ✅ log corect
            logger.info(f"✓ API server started on {host}:{chosen_port}")
            logger.info(f"  - API docs: http://localhost:{chosen_port}/docs")
            logger.info(f"  - Web UI: http://localhost:{chosen_port}/ui/{{channel}}")

            return True

        except Exception as e:
            logger.error(f"Failed to start API server: {e}", exc_info=True)
            return False

    def shutdown(self):
        if not self.is_running:
            return

        logger.info("Shutting down stats system...")

        try:
            from modules.stats import stats_events
            stats_events.flush_stats_events()
            logger.info("✓ Flushed pending events")
        except Exception as e:
            logger.error(f"Error flushing events: {e}")

        if self.aggregator_worker and getattr(self.aggregator_worker, "is_alive", lambda: False)():
            try:
                self.aggregator_worker.stop()
                logger.info("✓ Stopped aggregator")
            except Exception as e:
                logger.error(f"Error stopping aggregator: {e}")

        # Stop pruner
        if self.pruner_worker and getattr(self.pruner_worker, "is_alive", lambda: False)():
            try:
                self.pruner_worker.stop()
                logger.info("Database pruner stopped")
            except Exception as e:
                logger.error(f"Error stopping pruner: {e}")

        if self.api_worker:
            try:
                # SupervisedAPIServer are stop(); thread legacy are stop()
                if hasattr(self.api_worker, "stop"):
                    self.api_worker.stop()
                logger.info("✓ API server stop requested")
            except Exception as e:
                logger.error(f"Error stopping API: {e}")

        self.is_running = False
        logger.info("✓ Stats system shutdown complete")

    def get_status(self):
        try:
            from modules.stats import stats_events
            capture_stats = stats_events.get_capture_stats()
        except Exception:
            capture_stats = None

        def _alive(x):
            try:
                return bool(x and x.is_alive())
            except Exception:
                return False

        return {
            'running': self.is_running,
            'dependencies_ok': self.dependencies_ok,
            'aggregator_running': _alive(self.aggregator_worker),
            'api_running': _alive(self.api_worker),
            'api_port': self.config.get_api_port() if self.config else None,
            'capture_stats': capture_stats,
        }


_stats_manager = None

def get_stats_manager():
    global _stats_manager
    if _stats_manager is None:
        _stats_manager = StatsManager()
    return _stats_manager


def init_stats_system(bot_instance):
    manager = get_stats_manager()
    return manager.initialize(bot_instance)


def shutdown_stats_system():
    manager = get_stats_manager()
    manager.shutdown()


def get_stats_status():
    manager = get_stats_manager()
    return manager.get_status()
