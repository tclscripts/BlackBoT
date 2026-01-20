"""
IRC Statistics - Supervised Threaded API Server
================================================
Production-ready API server cu supervisor pattern, auto-restart și health checks.

Features:
✅ Supervisor pattern - monitorizează și repornește serverul dacă crashuiește
✅ Health checks - verifică periodic că serverul răspunde
✅ Auto-restart cu exponential backoff
✅ Circuit breaker - oprește restart după prea multe failures consecutive
✅ Crash reporting și metrics
✅ Graceful shutdown
✅ Thread-safe state management
"""

import threading
import time
import requests
from datetime import datetime
from typing import Optional, Dict, Any
from enum import Enum
from core.log import get_logger

logger = get_logger("stats_api_threaded")


# =============================================================================
# Server State Management
# =============================================================================

class ServerState(Enum):
    """Stările posibile ale API server-ului"""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    UNHEALTHY = "unhealthy"
    RESTARTING = "restarting"
    FAILED = "failed"


class ServerMetrics:
    """Thread-safe metrics pentru API server"""

    def __init__(self):
        self._lock = threading.Lock()
        self.total_starts = 0
        self.total_crashes = 0
        self.total_restarts = 0
        self.consecutive_failures = 0
        self.last_start_time = None
        self.last_crash_time = None
        self.last_health_check_time = None
        self.last_health_check_success = False
        self.uptime_seconds = 0.0

    def record_start(self):
        """Record server start"""
        with self._lock:
            self.total_starts += 1
            self.last_start_time = time.time()

    def record_crash(self):
        """Record server crash"""
        with self._lock:
            self.total_crashes += 1
            self.consecutive_failures += 1
            self.last_crash_time = time.time()

    def record_restart(self):
        """Record restart attempt"""
        with self._lock:
            self.total_restarts += 1

    def record_successful_run(self):
        """Reset consecutive failures after successful run"""
        with self._lock:
            self.consecutive_failures = 0

    def record_health_check(self, success: bool):
        """Record health check result"""
        with self._lock:
            self.last_health_check_time = time.time()
            self.last_health_check_success = success

    def update_uptime(self):
        """Update uptime if server is running"""
        with self._lock:
            if self.last_start_time:
                self.uptime_seconds = time.time() - self.last_start_time

    def get_snapshot(self) -> Dict[str, Any]:
        """Thread-safe snapshot of metrics"""
        with self._lock:
            return {
                "total_starts": self.total_starts,
                "total_crashes": self.total_crashes,
                "total_restarts": self.total_restarts,
                "consecutive_failures": self.consecutive_failures,
                "last_start_time": self.last_start_time,
                "last_crash_time": self.last_crash_time,
                "last_health_check_time": self.last_health_check_time,
                "last_health_check_success": self.last_health_check_success,
                "uptime_seconds": self.uptime_seconds,
            }


# =============================================================================
# API Server Thread (Base Worker)
# =============================================================================

class APIServerThread(threading.Thread):
    """
    Base API server thread.
    Supervised de SupervisedAPIServer.
    """

    def __init__(self, sql_instance, bot_id: int, host='0.0.0.0', port=8000):
        super().__init__(name="StatsAPIServer", daemon=True)

        self.sql = sql_instance
        self.bot_id = int(bot_id)
        self.host = host
        self.port = port
        self.server = None
        self.should_stop = False
        self._state_lock = threading.Lock()
        self._state = ServerState.STOPPED

    def get_state(self) -> ServerState:
        """Thread-safe state getter"""
        with self._state_lock:
            return self._state

    def set_state(self, state: ServerState):
        """Thread-safe state setter"""
        with self._state_lock:
            old_state = self._state
            self._state = state
            if old_state != state:
                logger.debug(f"API server state: {old_state.value} -> {state.value}")

    def run(self):
        """Run API server în acest thread"""

        try:
            self.set_state(ServerState.STARTING)

            import uvicorn
            from modules.stats import stats_api
            from modules.stats.stats_api import app

            # Set bot ID
            stats_api.DEFAULT_BOT_ID = self.bot_id

            logger.info(
                f"Starting API server on {self.host}:{self.port} "
                f"(bot_id={self.bot_id})"
            )

            # Fix for Windows: sys.stdout can be None
            import sys
            if sys.stdout is None:
                sys.stdout = open('nul' if sys.platform == 'win32' else '/dev/null', 'w')

            # Configure uvicorn
            config = uvicorn.Config(
                app,
                host=self.host,
                port=self.port,
                log_level="error",
                access_log=False,
                use_colors=False,
            )

            self.server = uvicorn.Server(config)

            # Mark as running before starting
            self.set_state(ServerState.RUNNING)

            # Run server (blocking)
            logger.info(f"✅ API server started successfully")
            self.server.run()

            # If we reach here, server stopped normally
            if not self.should_stop:
                logger.warning("API server stopped unexpectedly")
                self.set_state(ServerState.UNHEALTHY)
            else:
                logger.info("API server stopped gracefully")
                self.set_state(ServerState.STOPPED)

        except ImportError as e:
            logger.error(f"❌ Failed to import API dependencies: {e}")
            logger.error("Make sure fastapi and uvicorn are installed")
            self.set_state(ServerState.FAILED)

        except OSError as e:
            if "Address already in use" in str(e):
                logger.error(f"❌ Port {self.port} is already in use!")
                logger.error("Try changing STATS_API_PORT or stop the other service")
            else:
                logger.error(f"❌ Failed to start API server: {e}")
            self.set_state(ServerState.FAILED)

        except Exception as e:
            logger.error(f"❌ API server crashed: {e}", exc_info=True)
            self.set_state(ServerState.FAILED)

    def stop(self):
        """Stop server gracefully"""
        logger.info("Stopping API server...")
        self.should_stop = True

        if self.server:
            try:
                self.server.should_exit = True
                logger.debug("API server stop signal sent")
            except Exception as e:
                logger.error(f"Error stopping API server: {e}")


# =============================================================================
# Health Checker
# =============================================================================

class APIHealthChecker:
    """
    Verifică periodic dacă API server-ul răspunde.
    """

    def __init__(self, host: str, port: int, check_interval: int = 30):
        self.host = host
        self.port = port
        self.check_interval = check_interval
        self.endpoint = f"http://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}/docs"

    def check_health(self) -> bool:
        """
        Verifică dacă server-ul răspunde.

        Returns:
            bool: True dacă server-ul e healthy
        """
        try:
            response = requests.get(self.endpoint, timeout=5)

            if response.status_code == 200:
                logger.debug("✅ Health check passed")
                return True
            else:
                logger.warning(f"⚠️ Health check failed: status {response.status_code}")
                return False

        except requests.exceptions.ConnectionError:
            logger.warning(f"⚠️ Health check failed: connection refused")
            return False

        except requests.exceptions.Timeout:
            logger.warning(f"⚠️ Health check failed: timeout")
            return False

        except Exception as e:
            logger.warning(f"⚠️ Health check failed: {e}")
            return False


# =============================================================================
# Supervised API Server (Main Component)
# =============================================================================

class SupervisedAPIServer:
    """
    Supervisor care monitorizează și repornește API server-ul dacă crashuiește.

    Features:
    - Auto-restart cu exponential backoff
    - Health checks periodice
    - Circuit breaker (stop după prea multe failures)
    - Metrics tracking
    - Thread-safe operations
    """

    def __init__(
        self,
        sql_instance,
        bot_id: int,
        host: str = '0.0.0.0',
        port: int = 8000,
        enable_health_checks: bool = True,
        health_check_interval: int = 30,
        max_consecutive_failures: int = 5,
        restart_delay_base: float = 1.0,
        restart_delay_max: float = 60.0,
    ):
        self.sql = sql_instance
        self.bot_id = bot_id
        self.host = host
        self.port = port

        # Configuration
        self.enable_health_checks = enable_health_checks
        self.health_check_interval = health_check_interval
        self.max_consecutive_failures = max_consecutive_failures
        self.restart_delay_base = restart_delay_base
        self.restart_delay_max = restart_delay_max

        # State
        self._lock = threading.RLock()
        self._should_stop = False
        self._server_thread: Optional[APIServerThread] = None
        self._supervisor_thread: Optional[threading.Thread] = None
        self._health_checker_thread: Optional[threading.Thread] = None

        # Metrics
        self.metrics = ServerMetrics()

        # Health checker
        self.health_checker = APIHealthChecker(host, port, health_check_interval)

        logger.info(
            f"SupervisedAPIServer initialized "
            f"(max_failures={max_consecutive_failures}, "
            f"health_checks={enable_health_checks})"
        )

    # =========================================================================
    # Main Control Methods
    # =========================================================================

    def start(self) -> bool:
        """
        Start supervised API server.

        Returns:
            bool: True dacă a pornit cu succes
        """
        with self._lock:
            if self._supervisor_thread and self._supervisor_thread.is_alive():
                logger.warning("Supervisor already running")
                return False

            self._should_stop = False

            # Start supervisor thread
            self._supervisor_thread = threading.Thread(
                target=self._supervisor_loop,
                name="APIServerSupervisor",
                daemon=True
            )
            self._supervisor_thread.start()

            # Wait for initial start
            time.sleep(1.0)

            # Check if started successfully
            if self._server_thread and self._server_thread.is_alive():
                logger.info("✅ Supervised API server started")

                # Start health checker if enabled
                if self.enable_health_checks:
                    self._start_health_checker()

                return True
            else:
                logger.error("❌ Failed to start API server")
                return False

    def stop(self, timeout: float = 10.0):
        """
        Stop supervised API server gracefully.

        Args:
            timeout: Max time to wait for shutdown
        """
        logger.info("Stopping supervised API server...")

        with self._lock:
            self._should_stop = True

        # Stop server thread
        if self._server_thread:
            self._server_thread.stop()
            self._server_thread.join(timeout=timeout)

        # Stop supervisor thread
        if self._supervisor_thread:
            self._supervisor_thread.join(timeout=timeout)

        # Stop health checker
        if self._health_checker_thread:
            self._health_checker_thread.join(timeout=timeout)

        logger.info("✅ Supervised API server stopped")

    # =========================================================================
    # Supervisor Loop
    # =========================================================================

    def _supervisor_loop(self):
        """
        Main supervisor loop.
        Monitorizează server-ul și îl repornește dacă moare.
        """
        logger.info("🔍 Supervisor started")

        restart_delay = self.restart_delay_base

        while True:
            with self._lock:
                if self._should_stop:
                    logger.info("Supervisor stopping...")
                    break

            try:
                # Check if server thread exists and is alive
                needs_restart = False

                if self._server_thread is None:
                    logger.info("No server thread, starting...")
                    needs_restart = True

                elif not self._server_thread.is_alive():
                    state = self._server_thread.get_state()

                    if state == ServerState.FAILED:
                        logger.warning(f"⚠️ Server thread died (state: {state.value})")
                        self.metrics.record_crash()
                        needs_restart = True

                    elif state == ServerState.STOPPED and not self._should_stop:
                        logger.warning("⚠️ Server stopped unexpectedly")
                        self.metrics.record_crash()
                        needs_restart = True

                # Circuit breaker: stop retrying după prea multe failures
                if needs_restart:
                    if self.metrics.consecutive_failures >= self.max_consecutive_failures:
                        logger.error(
                            f"❌ Circuit breaker triggered: "
                            f"{self.metrics.consecutive_failures} consecutive failures. "
                            f"Stopping automatic restarts."
                        )
                        break

                    # Restart cu exponential backoff
                    logger.info(f"🔄 Restarting server in {restart_delay:.1f}s...")
                    time.sleep(restart_delay)

                    self._restart_server()

                    # Exponential backoff
                    restart_delay = min(restart_delay * 2, self.restart_delay_max)

                else:
                    # Server is healthy, reset delay
                    if self._server_thread and self._server_thread.is_alive():
                        restart_delay = self.restart_delay_base
                        self.metrics.update_uptime()

                # Check every second
                time.sleep(1.0)

            except Exception as e:
                logger.error(f"❌ Supervisor error: {e}", exc_info=True)
                time.sleep(5.0)

        logger.info("🔍 Supervisor stopped")

    def _restart_server(self):
        """Restart server thread"""
        try:
            # Stop old thread if exists
            if self._server_thread:
                self._server_thread.stop()
                self._server_thread.join(timeout=5.0)

            # Create and start new thread
            self._server_thread = APIServerThread(
                self.sql,
                self.bot_id,
                self.host,
                self.port
            )

            self._server_thread.start()
            self.metrics.record_start()
            self.metrics.record_restart()

            # Wait a bit and check if started
            time.sleep(2.0)

            if self._server_thread.is_alive():
                state = self._server_thread.get_state()

                if state == ServerState.RUNNING:
                    logger.info("✅ Server restarted successfully")
                    self.metrics.record_successful_run()
                else:
                    logger.warning(f"⚠️ Server started but state is {state.value}")
            else:
                logger.error("❌ Server failed to start after restart")

        except Exception as e:
            logger.error(f"❌ Failed to restart server: {e}", exc_info=True)

    # =========================================================================
    # Health Checker
    # =========================================================================

    def _start_health_checker(self):
        """Start health checker thread"""
        self._health_checker_thread = threading.Thread(
            target=self._health_check_loop,
            name="APIHealthChecker",
            daemon=True
        )
        self._health_checker_thread.start()
        logger.info(f"🏥 Health checker started (interval={self.health_check_interval}s)")

    def _health_check_loop(self):
        """Health check loop"""
        # Wait for server to fully start
        time.sleep(5.0)

        while True:
            with self._lock:
                if self._should_stop:
                    break

            try:
                # Perform health check
                is_healthy = self.health_checker.check_health()
                self.metrics.record_health_check(is_healthy)

                # Update server state based on health
                if self._server_thread:
                    current_state = self._server_thread.get_state()

                    if is_healthy and current_state == ServerState.UNHEALTHY:
                        self._server_thread.set_state(ServerState.RUNNING)
                        logger.info("✅ Server recovered (health check passed)")

                    elif not is_healthy and current_state == ServerState.RUNNING:
                        self._server_thread.set_state(ServerState.UNHEALTHY)
                        logger.warning("⚠️ Server unhealthy (health check failed)")

            except Exception as e:
                logger.error(f"❌ Health check error: {e}", exc_info=True)

            time.sleep(self.health_check_interval)

    # =========================================================================
    # Status & Monitoring
    # =========================================================================

    def get_status(self) -> Dict[str, Any]:
        """
        Get comprehensive status info.

        Returns:
            dict: Status information
        """
        with self._lock:
            server_alive = self._server_thread.is_alive() if self._server_thread else False
            server_state = self._server_thread.get_state() if self._server_thread else ServerState.STOPPED
            supervisor_alive = self._supervisor_thread.is_alive() if self._supervisor_thread else False

        metrics = self.metrics.get_snapshot()

        # Calculate uptime
        uptime_str = "N/A"
        if metrics["last_start_time"]:
            uptime_sec = time.time() - metrics["last_start_time"]
            hours = int(uptime_sec // 3600)
            minutes = int((uptime_sec % 3600) // 60)
            seconds = int(uptime_sec % 60)
            uptime_str = f"{hours}h {minutes}m {seconds}s"

        return {
            "server_alive": server_alive,
            "server_state": server_state.value,
            "supervisor_alive": supervisor_alive,
            "should_stop": self._should_stop,
            "uptime": uptime_str,
            "health_checks_enabled": self.enable_health_checks,
            "metrics": metrics,
            "config": {
                "host": self.host,
                "port": self.port,
                "bot_id": self.bot_id,
                "max_consecutive_failures": self.max_consecutive_failures,
                "health_check_interval": self.health_check_interval,
            }
        }

    def is_healthy(self) -> bool:
        """Quick health check"""
        if not self._server_thread:
            return False

        return (
            self._server_thread.is_alive() and
            self._server_thread.get_state() == ServerState.RUNNING
        )


# =============================================================================
# Convenience Functions (Backward Compatible)
# =============================================================================

def start_api_server_thread(
    sql_instance,
    bot_id: int,
    host: str = '0.0.0.0',
    port: int = 8000,
    supervised: bool = True,
    **supervisor_kwargs
) -> Optional[SupervisedAPIServer]:
    """
    Pornește API server într-un thread separat.

    Args:
        sql_instance: SQL manager instance
        bot_id: Bot ID pentru DEFAULT_BOT_ID
        host: Host address (default: 0.0.0.0)
        port: Port number (default: 8000)
        supervised: Use supervised mode (recommended, default: True)
        **supervisor_kwargs: Additional args pentru SupervisedAPIServer

    Returns:
        SupervisedAPIServer instance sau None dacă a eșuat
    """

    if supervised:
        # Use supervised mode (recommended)
        server = SupervisedAPIServer(
            sql_instance,
            bot_id,
            host,
            port,
            **supervisor_kwargs
        )

        if server.start():
            logger.info(f"✅ Supervised API server started on {host}:{port}")
            logger.info(f"  → API docs: http://localhost:{port}/docs")
            logger.info(f"  → Web UI: http://localhost:{port}/ui/{{channel}}")
            return server
        else:
            logger.error("❌ Failed to start supervised API server")
            return None

    else:
        # Legacy mode (fără supervision)
        logger.warning("⚠️ Starting API server without supervision (not recommended)")

        api_thread = APIServerThread(sql_instance, bot_id, host, port)
        api_thread.start()

        time.sleep(0.5)

        if api_thread.is_alive():
            logger.info(f"✅ API server started on {host}:{port}")
            return api_thread
        else:
            logger.error("❌ Failed to start API server")
            return None


# =============================================================================
# Testing
# =============================================================================

if __name__ == "__main__":
    print("Testing Supervised API Server...")
    print("=" * 70)

    # Mock SQL
    class MockSQL:
        def sqlite_select(self, query, params=None):
            return []

    sql = MockSQL()

    # Start supervised server
    server = start_api_server_thread(
        sql_instance=sql,
        bot_id=1,
        host='localhost',
        port=8000,
        supervised=True,
        max_consecutive_failures=3,
        health_check_interval=10
    )

    if server:
        print("\n✅ Server running!")
        print(f"   API: http://localhost:8000/docs")
        print("\nPress Ctrl+C to stop...\n")

        try:
            while True:
                time.sleep(5)

                # Print status every 5 seconds
                status = server.get_status()
                print(f"[{datetime.now().strftime('%H:%M:%S')}] "
                      f"State: {status['server_state']}, "
                      f"Uptime: {status['uptime']}, "
                      f"Crashes: {status['metrics']['total_crashes']}")

        except KeyboardInterrupt:
            print("\n\nStopping server...")
            server.stop()
            print("✅ Done!")

    else:
        print("❌ Failed to start server")