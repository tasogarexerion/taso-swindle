from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Any

from ..config import SwindleConfig


class SettingsStore:
    """Optional config persistence layer."""

    def __init__(self, path: str) -> None:
        self.path = path

    def save(self, config: SwindleConfig) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        data = asdict(config)
        data.pop("_specs_by_name", None)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)

    def load(self) -> dict[str, Any]:
        if not os.path.exists(self.path):
            return {}
        with open(self.path, "r", encoding="utf-8") as fh:
            return json.load(fh)
