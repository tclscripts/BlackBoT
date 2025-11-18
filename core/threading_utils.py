# core/threading_utils.py
import threading
import time
from core.log import get_logger

# ───────────────────────────────────────────────
# Global Logger
# ───────────────────────────────────────────────
logger = get_logger("threads")

# ───────────────────────────────────────────────
# Thread Worker with stop support + supervision
# ───────────────────────────────────────────────

thread_stop_events = {}

def get_event(name: str) -> threading.Event:
    """Return an existing Event or create a new one if missing."""
    try:
        ev = thread_stop_events.get(name)
        if ev is None:
            ev = threading.Event()
            thread_stop_events[name] = ev
        return ev
    except Exception as e:
        logger.error("Error creating or retrieving event '%s': %s", name, e)
        raise

def stop_thread(name: str):
    """Signal a thread to stop by setting its Event flag."""
    try:
        get_event(name).set()
    except Exception as e:
        logger.warning("Failed to stop thread '%s': %s", name, e)

class ThreadWorker(threading.Thread):
    """
    Thread wrapper with:
      - safe stop/reset flags
      - optional supervision that restarts a target if it crashes or stops beating
      - optional heartbeat (target(stop_event, beat) → call beat() periodically)

    Backward compatible default: supervise=False, provide_signals=False
    """

    def __init__(self,
                 target,
                 name,
                 *,
                 supervise: bool = False,
                 provide_signals: bool = False,
                 heartbeat_timeout: float | None = None,
                 min_interval: float = 0.5,
                 max_backoff: float = 30.0):
        """
        target:
          - dacă provide_signals=False → target() fără argumente (compat)
          - dacă provide_signals=True  → target(stop_event, beat)
        supervise:
          - dacă True, rulează targetul într-un sub-thread și îl repornește la crash / heartbeat timeout
        heartbeat_timeout:
          - dacă setat, consideră targetul „mort” dacă nu apelează beat() în această fereastră
        """
        try:
            super().__init__(name=name)
            self.name = name
            self.daemon = True
            self._target_func = target
            self._supervise = bool(supervise)
            self._provide_signals = bool(provide_signals)
            self._heartbeat_timeout = float(heartbeat_timeout) if heartbeat_timeout else None
            self._min_interval = max(0.1, float(min_interval))
            self._max_backoff = max(1.0, float(max_backoff))

            # per-worker control
            thread_stop_events[name] = threading.Event()
            self._last_beat = 0.0
            self._restart_count = 0
            self._backoff = self._min_interval
            self._child: threading.Thread | None = None
            self._zombies: list[threading.Thread] = []
            self._zombie_cap = 3  # dacă targetul blochează și nu moare, limităm „zombi” rămăși
        except Exception as e:
            logger.error("Error initializing ThreadWorker '%s': %s", name, e)
            raise

    # --- public API ---
    def stop(self):
        """Signal the thread to stop."""
        try:
            thread_stop_events[self.name].set()
        except KeyError:
            logger.warning("Stop called for unknown thread: %s", self.name)
        except Exception as e:
            logger.error("Error stopping thread '%s': %s", self.name, e)

    def reset(self):
        """Reset the thread stop flag."""
        try:
            thread_stop_events[self.name].clear()
        except KeyError:
            logger.warning("Reset called for unknown thread: %s", self.name)
        except Exception as e:
            logger.error("Error resetting thread '%s': %s", self.name, e)

    def should_stop(self):
        """Check if the stop flag has been set."""
        try:
            return thread_stop_events[self.name].is_set()
        except KeyError:
            logger.warning("Thread '%s' not registered in stop events", self.name)
            return True
        except Exception as e:
            logger.error("Error checking stop flag for '%s': %s", self.name, e)
            return True

    # --- internals ---
    def _beat(self):
        self._last_beat = time.time()

    def _spawn_child(self):
        """Pornește targetul într-un sub-thread (pentru supraveghere)."""
        stop_event = get_event(self.name)
        self._beat()  # mark „alive” imediat

        def runner():
            try:
                if self._provide_signals:
                    self._target_func(stop_event, self._beat)
                else:
                    self._target_func()
            except Exception as e:
                logger.error("Unhandled exception in '%s.child': %s", self.name, e, exc_info=True)
            finally:
                # la final, dacă child iese, supervizorul va detecta și va re-pornit
                pass

        th = threading.Thread(target=runner, name=self.name + ".child", daemon=True)
        th.start()
        self._child = th
        self._restart_count += 1
        self._backoff = self._min_interval

    def _child_alive(self) -> bool:
        return bool(self._child and self._child.is_alive())

    def _heartbeat_expired(self) -> bool:
        if self._heartbeat_timeout is None:
            return False
        if self._last_beat <= 0:
            return False
        return (time.time() - self._last_beat) > self._heartbeat_timeout

    def run(self):
        """Supervizorul (sau execuția simplă, dacă supervise=False)."""
        try:
            if not self._supervise:
                # modul compatibil vechi — nu supraveghează
                super().run() if self._target is not None else self._target_func()
                return

            # supervizor: pornește sub-thread + buclă de monitorizare
            self._spawn_child()

            while not self.should_stop():
                # dacă child a murit → restart
                if not self._child_alive():
                    logger.debug("ThreadWorker '%s': child stopped; restarting", self.name)
                    self._spawn_child()

                # heartbeat timeout → încercăm să oprim child și repornim
                if self._heartbeat_expired():
                    logger.warning("ThreadWorker '%s': heartbeat timeout; restarting", self.name)
                    # semnal de oprire child; nu putem „kill”, dar îl marcăm zombie dacă nu moare
                    get_event(self.name).set()
                    # păstrăm referința pentru debugging dacă încă e activ
                    if self._child and self._child.is_alive():
                        self._zombies.append(self._child)
                        # prune zombies
                        self._zombies = [z for z in self._zombies if z.is_alive()][-self._zombie_cap:]
                    # resetăm event și relansăm
                    get_event(self.name).clear()
                    self._spawn_child()

                # pacing / backoff mic
                time.sleep(self._backoff)

        except Exception as e:
            logger.error("Unhandled exception in ThreadWorker supervisor '%s': %s", self.name, e, exc_info=True)
        finally:
            # cleanup
            thread_stop_events.pop(self.name, None)
            logger.debug("ThreadWorker stopped and cleaned up: %s", self.name)
