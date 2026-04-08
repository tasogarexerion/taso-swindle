from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict
from datetime import datetime

from ..config import SwindleConfig
from .event_schema import DecisionEvent


class JsonlLogger:
    def __init__(self, config: SwindleConfig) -> None:
        self.config = config
        self._lock = threading.Lock()

    def log_decision(self, event: DecisionEvent) -> None:
        if not self.config.swindle_log_enable:
            return
        if self.config.swindle_log_format.upper() != "JSONL":
            return

        path = self._log_file_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)

        record = asdict(event)
        line = json.dumps(record, ensure_ascii=False)

        with self._lock:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def _log_file_path(self) -> str:
        ts = datetime.now().strftime("%Y%m%d")
        return os.path.join(self.config.swindle_log_path, f"taso-swindle-{ts}.jsonl")
