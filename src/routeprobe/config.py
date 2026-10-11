"""Read [tool.routeprobe] from pyproject.toml. Command-line options take precedence."""

from __future__ import annotations

import os
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    tomllib = None

DEFAULT_TESTS_FILE = "routeprobe_tests.py"

KEYS = {"app", "framework", "tests", "exclude", "read_only", "test_db", "include_admin", "checks", "slow_ms"}


def load_config(cwd: str = ".") -> dict[str, Any]:
    path = os.path.join(cwd, "pyproject.toml")
    if tomllib is None or not os.path.exists(path):
        return {}
    with open(path, "rb") as fh:
        section = tomllib.load(fh).get("tool", {}).get("routeprobe", {})
    unknown = set(section) - KEYS
    if unknown:
        raise ValueError(f"unknown key(s) in [tool.routeprobe]: {sorted(unknown)}")
    return section


def default_tests(cwd: str = ".") -> list[str]:
    path = os.path.join(cwd, DEFAULT_TESTS_FILE)
    return [path] if os.path.exists(path) else []
