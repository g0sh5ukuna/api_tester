from __future__ import annotations

import os
import re
import time
import traceback
from abc import ABC, abstractmethod
from typing import Any

from ..models import METHOD_ORDER, Request, Response, Route


def sort_methods(methods) -> list[str]:
    order = {m: i for i, m in enumerate(METHOD_ORDER)}
    return sorted({m.upper() for m in methods}, key=lambda m: order.get(m, len(order)))


def dotted_name(obj: Any) -> str:
    module = getattr(obj, "__module__", None) or "?"
    qualname = getattr(obj, "__qualname__", None) or getattr(obj, "__name__", repr(obj))
    return f"{module}.{qualname}"


_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def merge_routes(routes: list[Route]) -> list[Route]:
    """Join routes declared once per method on the same path (FastAPI, Flask) into one route."""
    merged: dict[str, Route] = {}
    for route in routes:
        first = merged.get(route.path)
        if first is None:
            merged[route.path] = route
            continue
        first.methods = sort_methods(first.methods + route.methods)
        first.handlers.update(route.handlers)
        first.body_schemas.update(route.body_schemas)
        known = {p.name for p in first.params}
        first.params += [p for p in route.params if p.name not in known]
        views = list(dict.fromkeys(first.handlers.values()))
        first.view = views[0] if len(views) == 1 else ", ".join(views)
    return list(merged.values())


def short_traceback(exc: BaseException) -> str:
    """Traceback limited to the frames of the project under test, plus the exception line."""
    frames = traceback.extract_tb(exc.__traceback__)
    ignored = ("site-packages", "dist-packages", f"{os.sep}routeprobe{os.sep}", f"{os.sep}lib{os.sep}python")
    own = [f for f in frames if not any(part in f.filename for part in ignored)]
    lines = ["Traceback (project frames):\n"] + traceback.format_list(own or frames[-3:])
    return "".join(lines + traceback.format_exception_only(type(exc), exc))


def path_regex(template: str) -> re.Pattern[str]:
    """`/items/{id}` -> regex matching `/items/42`."""
    parts = _PLACEHOLDER.split(template)
    pattern = "".join(re.escape(p) if i % 2 == 0 else "[^/]+" for i, p in enumerate(parts))
    return re.compile(f"^{pattern}$")


class Adapter(ABC):
    """Discovers the routes of one application and sends requests to it in-process."""

    framework: str = ""

    def __init__(self, label: str):
        self.label = label  # how the app was located, for reports

    @abstractmethod
    def discover(self) -> list[Route]: ...

    def start(self) -> None:
        """Prepare the app for requests (test client, test database...)."""

    def stop(self) -> None:
        """Release what start() created."""

    @abstractmethod
    def _send(self, request: Request) -> tuple[int, dict[str, str], bytes]: ...

    def send(self, request: Request) -> Response:
        started = time.perf_counter()
        try:
            status, headers, body = self._send(request)
        except Exception as exc:
            # The view raised: report it as a server error with its traceback.
            elapsed = (time.perf_counter() - started) * 1000
            return Response(500, {}, b"", elapsed, error=short_traceback(exc))
        return Response(status, headers, body, (time.perf_counter() - started) * 1000)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
