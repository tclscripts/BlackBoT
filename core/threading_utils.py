import threading

# ───────────────────────────────────────────────
# Thread Worker with stop support
# ───────────────────────────────────────────────

thread_stop_events = {}


class ThreadWorker(threading.Thread):
    def __init__(self, target, name):
        super().__init__(target=target, name=name)
        self.name = name
        self.daemon = True
        thread_stop_events[name] = threading.Event()

    def run(self):
        try:
            super().run()
        finally:
            thread_stop_events.pop(self.name, None)

    def stop(self):
        thread_stop_events[self.name].set()

    def reset(self):
        thread_stop_events[self.name].clear()

    def should_stop(self):
        return thread_stop_events[self.name].is_set()
