"""Loads the manual application allow-list from config/applications.yaml.

Unlike pipeline discovery (account-wide, automatic - see handler.py), which
CloudFormation stacks count as an "application" is a human decision this
file exists to record: the account has no `Application` tag in use, and is
shared across many unrelated teams. Nothing outside this file is ever
scanned. Same resilience contract as registry.py: a missing or malformed
file behaves exactly like an empty one (no applications configured) rather
than crashing the monitor run.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List

import yaml

DEFAULT_APPLICATIONS_PATH = Path(__file__).resolve().parent.parent / "config" / "applications.yaml"


@dataclass(frozen=True)
class ApplicationConfig:
    id: str
    stack_name: str
    display_name: str


def load_applications(path: Path = DEFAULT_APPLICATIONS_PATH) -> List[ApplicationConfig]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return []
    except yaml.YAMLError:
        return []

    return [
        ApplicationConfig(id=entry["id"], stack_name=entry["stack_name"], display_name=entry.get("display_name", entry["id"]))
        for entry in raw.get("applications") or []
    ]
