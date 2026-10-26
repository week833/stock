"""Small terminal progress display for long-running analysis stages."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import math
import sys
import threading
import time


_CURRENT = ContextVar("tw_next_day_progress", default=None)
_BAR_WIDTH = 20


@dataclass
class _StageState:
    label: str
    total: int | None
    unit: str
    heartbeat: bool
    started: float
    completed: int = 0
    detail: str | None = None
    frame: int = 0


class ProgressTask:
    """Counter yielded by :func:`stage`. Updates never affect analysis values."""

    def __init__(self, session=None, state=None):
        self._session = session
        self._state = state

    def update(self, completed, detail=None):
        if self._session is None:
            return
        self._session.update(self._state, completed, detail)

    def advance(self, amount=1, detail=None):
        if self._session is None:
            return
        self._session.advance(self._state, amount, detail)


class _ProgressSession:
    def __init__(self, stream, interval):
        self.stream = stream
        self.interval = interval
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._stack = []
        self._broken = False
        self._tty = False
        self._last_width = 0
        self._last_emit = 0.0
        try:
            self._tty = bool(stream.isatty())
        except Exception:
            self._tty = False
        self._thread = threading.Thread(target=self._heartbeat, name="stock-progress", daemon=True)
        self._thread.start()

    def _heartbeat(self):
        while not self._stop.wait(self.interval):
            with self._lock:
                state = self._stack[-1] if self._stack else None
                if state is not None and state.heartbeat:
                    self._render_locked(state, force=False)

    def _line(self, state, status="執行中", elapsed=None):
        age = max(0.0, time.monotonic() - state.started) if elapsed is None else elapsed
        minutes, seconds = divmod(int(age), 60)
        time_text = f"耗時 {minutes} 分 {seconds:02d} 秒" if minutes else f"耗時 {seconds} 秒"
        if state.total is None:
            if status == "執行中":
                spinner = "|/-\\"[state.frame % 4]
                body = f"{status} {state.label} [{spinner}] 執行中；{time_text}"
            else:
                body = f"{status} {state.label}；{time_text}"
        else:
            done = max(0, state.completed)
            total = max(0, state.total)
            if status == "執行中":
                percent = min(99, int(done * 100 / total)) if total else 0
            else:
                percent = 100 if status == "完成" else None
            if total:
                filled = min(_BAR_WIDTH, int(done * _BAR_WIDTH / total))
            else:
                filled = 0
            if status != "完成":
                filled = min(_BAR_WIDTH - 1, filled)
            bar = "[" + "#" * filled + "." * (_BAR_WIDTH - filled) + "]"
            count = f"{done}/{total}"
            if state.unit:
                count += state.unit
            progress_text = count if percent is None else f"{count} {percent}%"
            body = f"{status} {state.label} {bar} {progress_text}；{time_text}"
        if state.detail:
            body += "；" + str(state.detail).replace("\r", " ").replace("\n", " ")
        return body

    def _write_locked(self, text, newline):
        if self._broken:
            return
        try:
            if self._tty:
                padded = text.ljust(self._last_width)
                self.stream.write("\r" + padded + ("\n" if newline else ""))
                self._last_width = 0 if newline else len(padded)
            else:
                self.stream.write(text + "\n")
            self.stream.flush()
            self._last_emit = time.monotonic()
        except (OSError, UnicodeError, ValueError):
            self._broken = True

    def _render_locked(self, state, force):
        current = time.monotonic()
        if not force and current - self._last_emit < self.interval:
            state.frame += 1
            return
        self._write_locked(self._line(state), newline=not self._tty)
        state.frame += 1

    def push(self, label, total, unit, heartbeat):
        state = _StageState(str(label), total, str(unit or ""), bool(heartbeat), time.monotonic())
        with self._lock:
            self._stack.append(state)
            self._render_locked(state, force=True)
        return ProgressTask(self, state)

    def update(self, state, completed, detail):
        try:
            value = int(completed)
        except (TypeError, ValueError, OverflowError):
            return
        with self._lock:
            if state not in self._stack:
                return
            state.completed = value
            state.detail = None if detail is None else str(detail)

    def advance(self, state, amount, detail):
        try:
            amount = int(amount)
        except (TypeError, ValueError, OverflowError):
            return
        with self._lock:
            if state not in self._stack:
                return
            state.completed += amount
            if detail is not None:
                state.detail = str(detail)

    def finish(self, state, status):
        with self._lock:
            if state not in self._stack:
                return
            is_top = self._stack[-1] is state
            self._stack.remove(state)
            elapsed = max(0.0, time.monotonic() - state.started)
            self._write_locked(self._line(state, status=status, elapsed=elapsed), newline=True)
            if is_top and self._stack:
                parent = self._stack[-1]
                self._render_locked(parent, force=True)

    def close(self):
        self._stop.set()
        if self._thread is not threading.current_thread():
            self._thread.join(timeout=max(0.1, self.interval + 0.1))


@contextmanager
def progress_session(enabled=True, stream=None, interval=5.0):
    """Start one throttled heartbeat for a command; nested stages share it."""
    if not enabled:
        token = _CURRENT.set(None)
        try:
            yield None
        finally:
            _CURRENT.reset(token)
        return
    try:
        interval = float(interval)
    except (TypeError, ValueError, OverflowError):
        interval = 5.0
    if not math.isfinite(interval) or interval <= 0:
        interval = 5.0
    active = _CURRENT.get()
    if active is not None:
        token = _CURRENT.set(active)
        try:
            yield active
        finally:
            _CURRENT.reset(token)
        return
    session = _ProgressSession(sys.stderr if stream is None else stream, interval)
    token = _CURRENT.set(session)
    try:
        yield session
    finally:
        _CURRENT.reset(token)
        session.close()


@contextmanager
def stage(label, total=None, unit="", heartbeat=True):
    """Report one nested stage. The yielded task accepts ``update``/``advance``."""
    if total is not None:
        try:
            total = int(total)
        except (TypeError, ValueError, OverflowError):
            total = None
        if total is not None and total < 0:
            total = None
    session = _CURRENT.get()
    if session is None:
        yield ProgressTask()
        return
    task = session.push(label, total, unit, heartbeat)
    try:
        yield task
    except KeyboardInterrupt:
        session.finish(task._state, "中止")
        raise
    except BaseException:
        session.finish(task._state, "失敗")
        raise
    else:
        if total is not None:
            task.update(total)
        session.finish(task._state, "完成")
