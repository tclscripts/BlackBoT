# core/threading_utils.py
import threading
import time
from core.log import get_logger

# ───────────────────────────────────────────────
# Global Logger
# ───────────────────────────────────────────────
logger = get_logger("threads")

# ───────────────────────────────────────────────
# IMPORTANT: Reload-safe globals
# ───────────────────────────────────────────────
# La importlib.reload(module), codul se re-executa in acelasi obiect module,
# deci daca aici faci `thread_stop_events = {}`, iti stergi registry-ul si thread-urile
# existente devin "not registered" -> se opresc singure.
#
# Solutie: pastrezi acelasi dict object daca exista deja.
thread_stop_events = globals().get("thread_stop_events")
if thread_stop_events is None or not isinstance(thread_stop_events, dict):
    thread_stop_events = {}
# IMPORTANT: re-atasam in globals (in caz de edge cases)
globals()["thread_stop_events"] = thread_stop_events


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
      - improved zombie thread cleanup

    Reload-safe:
      - NU suprascrie stop_event existent daca thread-ul exista deja
      - NU se opreste automat doar pentru ca registry-ul a fost golit accidental
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
          - dacă setat, consideră targetul „mort" dacă nu apelează beat() în această fereastră
        """
        super().__init__(name=name)
        self.name = name
        self.daemon = True
        self._target_func = target
        self._supervise = bool(supervise)
        self._provide_signals = bool(provide_signals)
        self._heartbeat_timeout = float(heartbeat_timeout) if heartbeat_timeout else None
        self._min_interval = max(0.1, float(min_interval))
        self._max_backoff = max(1.0, float(max_backoff))

        # per-worker control (reload-safe: nu suprascriem)
        # daca exista deja event, il refolosim
        ev = thread_stop_events.get(name)
        if ev is None:
            ev = threading.Event()
            thread_stop_events[name] = ev
        # la init nou, ne asiguram ca nu e setat din greseala
        # (dar nu in caz de reload cand thread-ul e deja live — totusi e safe sa clear aici
        # doar daca event-ul tocmai a fost creat)
        # Daca event-ul exista, NU il clearam agresiv.
        if ev is not None and name not in thread_stop_events:
            thread_stop_events[name].clear()

        self._last_beat = 0.0
        self._restart_count = 0
        self._backoff = self._min_interval
        self._child: threading.Thread | None = None
        self._zombies: list[threading.Thread] = []
        self._zombie_cap = 3
        self._zombie_cleanup_counter = 0

    # --- public API ---
    def stop(self):
        """Signal the thread to stop."""
        try:
            get_event(self.name).set()
        except Exception as e:
            logger.error("Error stopping thread '%s': %s", self.name, e)

    def reset(self):
        """Reset the thread stop flag."""
        try:
            get_event(self.name).clear()
        except Exception as e:
            logger.error("Error resetting thread '%s': %s", self.name, e)

    def should_stop(self):
        """
        Reload-safe:
        - daca lipseste event-ul (ex: dupa reload) NU oprim thread-ul,
          ci recreem event-ul si continuam.
        """
        try:
            ev = thread_stop_events.get(self.name)
            if ev is None:
                # Reload safety: recreate instead of stopping
                logger.warning("Thread '%s' not registered in stop events (reload-safe recreate)", self.name)
                ev = threading.Event()
                thread_stop_events[self.name] = ev
            return ev.is_set()
        except Exception as e:
            logger.error("Error checking stop flag for '%s': %s", self.name, e)
            # Fail safe: stop to avoid runaway
            return True

    # --- internals ---
    def _beat(self):
        self._last_beat = time.time()

    def _cleanup_zombies(self):
        if not self._zombies:
            return

        cleaned = []
        finished_count = 0

        for zombie in self._zombies:
            if not zombie.is_alive():
                try:
                    zombie.join(timeout=0.1)
                    finished_count += 1
                except Exception as e:
                    logger.debug("Error joining finished zombie thread: %s", e)
            else:
                cleaned.append(zombie)

        if finished_count > 0:
            logger.debug("ThreadWorker '%s': cleaned up %s finished zombie threads", self.name, finished_count)

        self._zombies = cleaned[-self._zombie_cap:]

        if len(self._zombies) >= self._zombie_cap:
            logger.warning(
                "ThreadWorker '%s' has %s active zombie threads (restart count: %s)",
                self.name, len(self._zombies), self._restart_count
            )

    def _spawn_child(self):
        """Pornește targetul într-un sub-thread (pentru supraveghere)."""
        stop_event = get_event(self.name)
        self._beat()

        def runner():
            try:
                if self._provide_signals:
                    self._target_func(stop_event, self._beat)
                else:
                    self._target_func()
            except Exception as e:
                logger.error("Unhandled exception in '%s.child': %s", self.name, e, exc_info=True)

        th = threading.Thread(target=runner, name=self.name + ".child", daemon=True)
        th.start()
        self._child = th
        self._restart_count += 1
        self._backoff = self._min_interval

        self._cleanup_zombies()

    def _child_alive(self) -> bool:
        return bool(self._child and self._child.is_alive())

    def _heartbeat_expired(self) -> bool:
        if self._heartbeat_timeout is None:
            return False
        if self._last_beat <= 0:
            return False
        return (time.time() - self._last_beat) > self._heartbeat_timeout

    def run(self):
        try:
            if not self._supervise:
                # compat vechi
                if self._target is not None:
                    super().run()
                else:
                    self._target_func()
                return

            self._spawn_child()

            while not self.should_stop():
                self._zombie_cleanup_counter += 1
                if self._zombie_cleanup_counter >= 10:
                    self._cleanup_zombies()
                    self._zombie_cleanup_counter = 0

                if not self._child_alive():
                    logger.debug("ThreadWorker '%s': child stopped; restarting", self.name)
                    self._spawn_child()

                if self._heartbeat_expired():
                    logger.warning("ThreadWorker '%s': heartbeat timeout; restarting", self.name)
                    get_event(self.name).set()
                    if self._child and self._child.is_alive():
                        self._zombies.append(self._child)
                        self._cleanup_zombies()
                    get_event(self.name).clear()
                    self._spawn_child()

                time.sleep(self._backoff)

        except Exception as e:
            logger.error("Unhandled exception in ThreadWorker supervisor '%s': %s", self.name, e, exc_info=True)
        finally:
            logger.debug("ThreadWorker '%s': performing final cleanup", self.name)

            if self._child and self._child.is_alive():
                try:
                    get_event(self.name).set()
                    self._child.join(timeout=2.0)
                    if self._child.is_alive():
                        logger.warning("Child thread '%s' did not stop gracefully", self._child.name)
                except Exception as e:
                    logger.error("Error stopping child thread: %s", e)

            for zombie in self._zombies:
                if zombie.is_alive():
                    try:
                        zombie.join(timeout=0.5)
                    except Exception:
                        pass
            self._zombies.clear()

            # NU scoatem event-ul din registry la final daca vrei persistenta peste reload,
            # dar aici e ok sa-l scoatem ca thread-ul chiar se inchide.
            thread_stop_events.pop(self.name, None)

            logger.debug("ThreadWorker stopped and cleaned up: %s", self.name)

    def get_stats(self):
        return {
            'name': self.name,
            'supervise': self._supervise,
            'is_alive': self.is_alive(),
            'child_alive': self._child_alive() if self._supervise else None,
            'restart_count': self._restart_count,
            'active_zombies': len([z for z in self._zombies if z.is_alive()]),
            'total_zombies': len(self._zombies),
            'last_beat': self._last_beat if self._heartbeat_timeout else None,
            'heartbeat_timeout': self._heartbeat_timeout,
        }
