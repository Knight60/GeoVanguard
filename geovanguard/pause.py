"""Pause / resume of a running job.

A ``PauseControl`` is shared between whoever presses the button (any thread)
and the pipeline: at its next checkpoint the pipeline suspends the worker
processes (CPU drops to zero at once — the running tasks are frozen, not
lost), then waits; resume continues exactly where it stopped.  Cancel stays
possible while paused.  Long jobs (days) can so give the machine back for a
while without starting over.
"""
import threading


class PauseControl(object):

    def __init__(self):
        self._paused = threading.Event()

    def pause(self):
        self._paused.set()

    def resume(self):
        self._paused.clear()

    def toggle(self):
        if self._paused.is_set():
            self.resume()
        else:
            self.pause()
        return self.is_paused()

    def is_paused(self):
        return self._paused.is_set()
