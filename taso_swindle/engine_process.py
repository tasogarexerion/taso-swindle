from __future__ import annotations

import os
import queue
import shlex
import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import Optional


@dataclass
class EngineCommand:
    executable: str
    args: str = ""

    def as_argv(self) -> list[str]:
        tail = shlex.split(self.args) if self.args.strip() else []
        return [self.executable, *tail]


class EngineProcess:
    """
    Child engine process wrapper.

    Reference: nnue_proxy.py:252 Engine class
    - subprocess + reader thread + queue
    - line based send/recv
    - drain/close/restart helpers
    """

    def __init__(
        self,
        command: EngineCommand,
        cwd: Optional[str] = None,
        encoding: str = "utf-8",
    ) -> None:
        self.command = command
        self.cwd = cwd
        self.encoding = encoding

        self._proc: Optional[subprocess.Popen[str]] = None
        self._queue: queue.Queue[str] = queue.Queue()
        self._alive = False
        self._lock = threading.Lock()

    @property
    def alive(self) -> bool:
        return self._alive and self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        with self._lock:
            if self.alive:
                return

            exe = self.command.executable
            if not exe:
                raise FileNotFoundError("backend engine path is empty")
            if not os.path.isfile(exe):
                found = shutil.which(exe)
                if found is None:
                    raise FileNotFoundError(f"backend engine not found: {exe}")
                exe = found
                self.command = EngineCommand(executable=exe, args=self.command.args)

            argv = self.command.as_argv()
            self._proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                encoding=self.encoding,
                errors="replace",
                cwd=self.cwd,
            )
            self._alive = True
            threading.Thread(target=self._reader_loop, daemon=True).start()

    def _reader_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            self._alive = False
            return

        try:
            for line in proc.stdout:
                self._queue.put(line.rstrip("\n"))
        finally:
            self._alive = False

    def send(self, line: str) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            self._alive = False
            return

        try:
            proc.stdin.write(line + "\n")
            proc.stdin.flush()
        except Exception:
            self._alive = False

    def recv(self, timeout: float) -> Optional[str]:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain(self, limit: int = 200_000) -> int:
        count = 0
        while count < limit:
            try:
                _ = self._queue.get_nowait()
            except queue.Empty:
                break
            count += 1
        return count

    def close(self) -> None:
        proc = self._proc
        self._alive = False

        if proc is None:
            return

        try:
            self.send("quit")
        except Exception:
            pass

        try:
            proc.terminate()
        except Exception:
            pass

    def restart(self, command: Optional[EngineCommand] = None) -> None:
        self.close()
        if command is not None:
            self.command = command
        self.start()
