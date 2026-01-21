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
        
        # Thread workers
        self.aggregator_worker = None
        self.api_worker = None
        
        # Status flags
        self.is_running = False
        self.dependencies_ok = False
        
        self._initialized = True
        logger.info("StatsManager initialized")
    
    def initialize(self, bot_instance):
        """
        Inițializare completă a sistemului de statistici.
        Apelat din BlackBoT.__init__ sau signedOn()
        
        Returns:
            bool: True dacă sistemul a pornit cu succes
        """
        if self.is_running:
            logger.warning("Stats system already running")
            return True
        
        self.bot = bot_instance
        self.sql = bot_instance.sql
        
        logger.info("=" * 60)
        logger.info("Initializing IRC Statistics System")
        logger.info("=" * 60)
        
        try:
            # Step 1: Load configuration
            if not self._load_config():
                logger.warning("Config load failed, using defaults")
            
            # Check if stats are enabled
            if not self.config.is_enabled():
                logger.info("Stats system is DISABLED in config")
                return False
            
            # Step 2: Auto-install dependencies
            deps_ok = self._auto_install_dependencies()
            if not deps_ok:
                logger.warning("[WARNING] Dependencies not ready yet - will retry in background")
                # DON'T return False - schedule deferred init instead!
                self._schedule_deferred_init(bot_instance)
                return True  # Return True so bot continues normally

            # Dependencies OK - continue with full initialization
            return self._complete_initialization(bot_instance)

        except Exception as e:
            logger.error(f"Failed to initialize stats system: {e}", exc_info=True)
            return False

    # LEGACY PATH - keeping old flow for reference but using _complete_initialization
    def _legacy_initialize_rest(self):
        """Old initialization steps - now in _complete_initialization"""
        pass # Not used anymore

    def _load_config(self):
        """Load configuration"""
        try:
            from modules.stats.stats_config import get_stats_config
            self.config = get_stats_config()

            logger.info(f"✓ Config loaded:")
            logger.info(f"  - Stats enabled: {self.config.is_enabled()}")
            logger.info(f"  - API enabled: {self.config.is_api_enabled()}")
            logger.info(f"  - API port: {self.config.get_api_port()}")
            logger.info(f"  - Aggregator enabled: {self.config.is_aggregator_enabled()}")
            logger.info(f"  - Aggregator interval: {self.config.get_aggregator_interval()}s")

            return True

        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            # Create default config object
            from modules.stats.stats_config import StatsConfig
            self.config = StatsConfig()
            return False

    def _auto_install_dependencies(self):
        """Verifică dependencies (instalarea se face de Launcher/Manager)"""
        try:
            from modules.stats.stats_autoinstall import check_stats_dependencies

            logger.info("Checking dependencies...")
            success = check_stats_dependencies()

            if success:
                logger.info("✓ All dependencies available")
                self.dependencies_ok = True
                return True
            else:
                logger.error("✗ Missing dependencies")
                logger.error("Launcher/Manager should install: fastapi, uvicorn, pydantic")
                logger.error("Stats system cannot start without these packages")
                return False

        except Exception as e:
            logger.error(f"Error during dependency check: {e}", exc_info=True)
            return False

    def _cleanup_old_threads(self):
        """Helper to ensure we don't leave zombie threads before restart"""
        if self.aggregator_worker and hasattr(self.aggregator_worker, 'stop'):
            try:
                self.aggregator_worker.stop()
            except:
                pass
        if self.api_worker and hasattr(self.api_worker, 'stop'):
            try:
                self.api_worker.stop()
            except:
                pass
        self.aggregator_worker = None
        self.api_worker = None

    def _schedule_deferred_init(self, bot_instance):
        """
        Schedule deferred initialization when dependencies aren't ready yet.
        This allows bot to start immediately, and stats will initialize later.
        """

        def deferred_init_worker():
            """Background worker that retries initialization"""
            logger.info("[RETRY] Deferred stats init: waiting for dependencies...")

            max_retries = 10
            retry_delay = 30  # 30 seconds between retries

            for attempt in range(1, max_retries + 1):
                try:
                    import time
                    time.sleep(retry_delay)

                    logger.info(f"[RETRY] Retry {attempt}/{max_retries}: Checking dependencies...")

                    # Try to install/check dependencies
                    from modules.stats.stats_autoinstall import auto_install_stats_dependencies
                    success = auto_install_stats_dependencies()

                    if success:
                        logger.info("[OK] Dependencies now available - initializing stats...")

                        # Reset state
                        self.is_running = False
                        self._cleanup_old_threads()

                        # Retry full initialization
                        result = self._complete_initialization(bot_instance)

                        if result:
                            logger.info("[OK] Deferred stats initialization SUCCESS!")
                            return
                        else:
                            logger.warning(f"[WARNING] Initialization attempt {attempt} failed, will retry...")
                    else:
                        logger.debug(f"Dependencies not ready yet (attempt {attempt}/{max_retries})")

                except Exception as e:
                    logger.error(f"Error during deferred init attempt {attempt}: {e}")

            logger.error(f"[ERROR] Failed to initialize stats after {max_retries} attempts")

        # Start background thread
        thread = threading.Thread(
            target=deferred_init_worker,
            name="StatsDeferredInit",
            daemon=True
        )
        thread.start()
        logger.info("[SCHEDULED] Scheduled deferred stats initialization (will retry in background)")

    def _complete_initialization(self, bot_instance):
        """
        Complete the initialization process (steps 3-7).
        Used both by normal init and deferred init.
        """
        try:
            # Step 3: Create database tables
            if not self._create_tables():
                logger.error("Failed to create tables")
                return False

            # Step 4: Initialize event capture
            if not self._init_event_capture():
                logger.error("Failed to init event capture")
                return False

            # Step 5: Hook IRC methods
            if not self._hook_irc_methods():
                logger.error("Failed to hook IRC methods")
                return False

            # Step 6: Start aggregator thread
            if self.config.is_aggregator_enabled():
                if not self._start_aggregator():
                    logger.warning("Failed to start aggregator")

            # Step 7: Start API server thread
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
        """Create database tables"""
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
        """Initialize event capture system"""
        try:
            from modules.stats import stats_events

            # Initialize with config
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
        """
        Hook IRC methods automat pentru a capta evenimente.
        Folosește monkey-patching pentru a injecta capture logic.
        """
        try:
            logger.info("Hooking IRC methods...")

            # Import hook injector
            from modules.stats.stats_hooks import inject_stats_hooks

            # Inject hooks în bot instance
            inject_stats_hooks(self.bot)

            logger.info("✓ IRC methods hooked successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to hook IRC methods: {e}", exc_info=True)
            return False

    def _start_aggregator(self):
        """Start aggregator thread"""
        try:
            from core.threading_utils import ThreadWorker
            from modules.stats import stats_aggregator

            interval = self.config.get_aggregator_interval()

            # ThreadWorker nu acceptă args - folosim wrapper function
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

        # 2) dacă nu se eliberează, caută următorul liber
        p = int(start_port) + 1
        for _ in range(200):
            if self._is_port_free(host, p):
                return p
            p += 1

        return int(start_port)

    def _start_api_server(self):
        """Start API server în thread separat"""
        try:
            from modules.stats.stats_api_threaded import start_api_server_thread

            port = self.config.get_api_port()
            host = self.config.get_api_host()

            chosen_port = self._pick_free_port(host, port, wait_total=6.0)

            if chosen_port != port:
                logger.warning(f"⚠️ Stats API port {port} still busy after wait; switching to {chosen_port}")

            self.api_worker = start_api_server_thread(
                sql_instance=self.sql,
                bot_id=self.bot.botId,  # ✅ botId-ul instanței care a pornit stats
                host=host,
                port=chosen_port
            )

            logger.info(f"✓ API server started on {host}:{port}")
            logger.info(f"  - API docs: http://localhost:{port}/docs")
            logger.info(f"  - Web UI: http://localhost:{port}/ui/{{channel}}")

            return True

        except Exception as e:
            logger.error(f"Failed to start API server: {e}", exc_info=True)
            return False

    def shutdown(self):
        """Shutdown stats system gracefully"""
        if not self.is_running:
            return

        logger.info("Shutting down stats system...")

        try:
            # Flush pending events
            from modules.stats import stats_events
            stats_events.flush_stats_events()
            logger.info("✓ Flushed pending events")
        except Exception as e:
            logger.error(f"Error flushing events: {e}")

        # Stop aggregator
        if self.aggregator_worker and self.aggregator_worker.is_alive():
            try:
                self.aggregator_worker.stop()
                logger.info("✓ Stopped aggregator")
            except Exception as e:
                logger.error(f"Error stopping aggregator: {e}")

        # Stop API server
        if self.api_worker and self.api_worker.is_alive():
            try:
                # API worker will stop when daemon=True and main thread exits
                logger.info("✓ API server will stop with main thread")
            except Exception as e:
                logger.error(f"Error stopping API: {e}")

        self.is_running = False
        logger.info("✓ Stats system shutdown complete")

    def get_status(self):
        """Get status info"""
        try:
            from modules.stats import stats_events
            capture_stats = stats_events.get_capture_stats()
        except:
            capture_stats = None

        return {
            'running': self.is_running,
            'dependencies_ok': self.dependencies_ok,
            'aggregator_running': self.aggregator_worker.is_alive() if self.aggregator_worker else False,
            'api_running': self.api_worker.is_alive() if self.api_worker else False,
            'api_port': self.config.get_api_port() if self.config else None,
            'capture_stats': capture_stats,
        }


# =============================================================================
# Global instance și helper functions
# =============================================================================

_stats_manager = None

def get_stats_manager():
    """Get global stats manager instance"""
    global _stats_manager
    if _stats_manager is None:
        _stats_manager = StatsManager()
    return _stats_manager


def init_stats_system(bot_instance):
    """
    Initialize stats system - apelat din BlackBoT.__init__

    Args:
        bot_instance: Instanța BlackBoT

    Returns:
        bool: True dacă a pornit cu succes
    """
    manager = get_stats_manager()
    return manager.initialize(bot_instance)


def shutdown_stats_system():
    """Shutdown stats system - apelat din BlackBoT cleanup"""
    manager = get_stats_manager()
    manager.shutdown()


def get_stats_status():
    """Get stats system status"""
    manager = get_stats_manager()
    return manager.get_status()
