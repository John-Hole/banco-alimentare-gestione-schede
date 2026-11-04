"""Utility per eseguire lavoro pesante su thread secondari aggiornando la UI in modo sicuro.

Il thread di lavoro non tocca mai i widget Tk: i risultati (e i messaggi di avanzamento) vengono
consegnati al thread principale tramite polling con `widget.after(...)`.
"""

import queue
import threading
import tkinter as tk
from typing import Any, Callable, Optional


class BackgroundTask:
    """Esegue `work(progress)` su un thread daemon e invoca `on_done(result, error)` sul thread UI.

    - `progress(*args)` è chiamabile dal thread di lavoro e inoltra `on_progress(*args)` sul thread UI.
    - `cancel_event` può essere letto dal lavoro per interrompersi in modo cooperativo.
    """

    def __init__(
        self,
        widget: tk.Misc,
        work: Callable[[Callable[..., None]], Any],
        on_done: Callable[[Any, Optional[BaseException]], None],
        on_progress: Optional[Callable[..., None]] = None,
        poll_ms: int = 60,
    ):
        self.widget = widget
        self.work = work
        self.on_done = on_done
        self.on_progress = on_progress
        self.poll_ms = poll_ms
        self.cancel_event = threading.Event()
        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._result: Any = None
        self._error: Optional[BaseException] = None
        self._finished = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> "BackgroundTask":
        self._thread.start()
        self._schedule_poll()
        return self

    def cancel(self):
        self.cancel_event.set()

    def _progress(self, *args):
        self._queue.put(args)

    def _run(self):
        try:
            self._result = self.work(self._progress)
        except BaseException as e:  # noqa: BLE001 - l'errore viene consegnato alla UI
            self._error = e
        finally:
            self._finished.set()

    def _schedule_poll(self):
        try:
            self.widget.after(self.poll_ms, self._poll)
        except tk.TclError:
            pass  # widget distrutto

    def _poll(self):
        finished = self._finished.is_set()  # letto PRIMA dello svuotamento coda: nessun messaggio perso
        try:
            while True:
                args = self._queue.get_nowait()
                if self.on_progress:
                    try:
                        self.on_progress(*args)
                    except tk.TclError:
                        return
        except queue.Empty:
            pass

        if not finished:
            self._schedule_poll()
            return
        try:
            self.on_done(self._result, self._error)
        except tk.TclError:
            pass
