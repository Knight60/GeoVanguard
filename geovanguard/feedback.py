"""Feedback objects for running the pipeline outside QGIS.

The pipeline only needs the duck-typed subset of QgsProcessingFeedback:
isCanceled, pushInfo, pushWarning, reportError, setProgress, setProgressText.
"""
import signal
import sys
import time


class SilentFeedback(object):
    """Collects messages, prints nothing; cancel() stops the run."""

    def __init__(self):
        self._canceled = False
        self.log = []
        self.progress = 0.0

    def cancel(self):
        self._canceled = True

    def isCanceled(self):  # noqa: N802 (QGIS API name)
        return self._canceled

    def pushInfo(self, msg):  # noqa: N802
        self.log.append(msg)

    def pushWarning(self, msg):  # noqa: N802
        self.log.append('Warning: ' + msg)

    def reportError(self, msg, fatalError=False):  # noqa: N802,N803
        self.log.append('Error: ' + msg)

    def setProgress(self, percent):  # noqa: N802
        self.progress = percent

    def setProgressText(self, text):  # noqa: N802
        pass


class ConsoleFeedback(SilentFeedback):
    """Progress bar + step text on stderr, log lines on stdout.

    The first Ctrl+C asks the pipeline to stop at the next checkpoint (the work
    folder then resumes the run); a second Ctrl+C aborts immediately.
    """

    def __init__(self, stream=None, quiet=False, width=30):
        super(ConsoleFeedback, self).__init__()
        self.stream = stream or sys.stderr
        self.quiet = quiet
        self.width = width
        self.text = ''
        self._last_draw = 0.0
        self._bar_shown = False
        self._tty = hasattr(self.stream, 'isatty') and self.stream.isatty()

    # ── Ctrl+C → cancel ──────────────────────────────────────────────────────
    def install_interrupt_handler(self):
        def handler(signum, frame):
            if self._canceled:
                raise KeyboardInterrupt
            self.cancel()
            self._print('Stopping at the next checkpoint… (Ctrl+C again to abort)')
        try:
            signal.signal(signal.SIGINT, handler)
        except ValueError:           # not in the main thread
            pass

    # ── output ───────────────────────────────────────────────────────────────
    def _clear_bar(self):
        if self._bar_shown and self._tty:
            self.stream.write('\r' + ' ' * 120 + '\r')
            self.stream.flush()
        self._bar_shown = False

    def _print(self, msg):
        self._clear_bar()
        if not self.quiet:
            print(msg)
            sys.stdout.flush()
        self._draw(force=True)

    def _draw(self, force=False):
        if self.quiet or not self._tty:
            return
        now = time.time()
        if not force and now - self._last_draw < 0.2:
            return
        self._last_draw = now
        filled = int(round(self.width * self.progress / 100.0))
        line = '[{}{}] {:5.1f}% {}'.format('#' * filled, '-' * (self.width - filled),
                                          self.progress, self.text)
        self.stream.write('\r' + line[:119].ljust(119))
        self.stream.flush()
        self._bar_shown = True

    def pushInfo(self, msg):  # noqa: N802
        super(ConsoleFeedback, self).pushInfo(msg)
        self._print(msg)

    def pushWarning(self, msg):  # noqa: N802
        super(ConsoleFeedback, self).pushWarning(msg)
        self._print('Warning: ' + msg)

    def reportError(self, msg, fatalError=False):  # noqa: N802,N803
        super(ConsoleFeedback, self).reportError(msg, fatalError)
        self._print('Error: ' + msg)

    def setProgress(self, percent):  # noqa: N802
        self.progress = percent
        self._draw()

    def setProgressText(self, text):  # noqa: N802
        self.text = text
        if not self._tty and not self.quiet:
            # no live bar (log file / pipe): print the throttled step texts
            print('  ' + text)
            sys.stdout.flush()
        self._draw(force=True)

    def finish(self):
        self._clear_bar()
