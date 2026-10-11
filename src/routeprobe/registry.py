"""Decorators used in a routeprobe_tests.py file to add your own test logic.

    import routeprobe as rp

    @rp.setup
    def login(ctx):
        token = ctx.client.post("/auth/login", json={...}).json["token"]
        ctx.headers["Authorization"] = f"Bearer {token}"

    @rp.data("POST /items")
    def item(ctx):
        return {"json": {"name": "Pen", "price": 2}}

    @rp.test(view="shop.views.ItemViewSet.retrieve", params={"pk": 1})
    def item_has_price(res, ctx):
        assert res.status == 200
        assert "price" in res.json
"""

from __future__ import annotations

import fnmatch
import importlib.util
import os
from dataclasses import dataclass, field
from typing import Any, Callable

from .adapters.base import path_regex
from .models import Route

KNOWN_METHODS = {"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"}
REQUEST_KEYS = ("params", "query", "json", "data", "headers")


@dataclass
class Target:
    """Selects routes by method, path template, concrete path, glob or view."""

    method: str | None = None
    path: str | None = None
    view: str | None = None

    @classmethod
    def parse(cls, spec: str | None = None, method: str | None = None, view: Any = None) -> Target:
        path = None
        if spec:
            first, _, rest = spec.strip().partition(" ")
            if first.upper() in KNOWN_METHODS and rest:
                method = method or first
                path = rest.strip()
            else:
                path = spec.strip()
        if view is not None and not isinstance(view, str):
            # A function or class from the project: use its dotted name.
            inner = getattr(view, "cls", None) or getattr(view, "view_class", None) or view
            view = f"{inner.__module__}.{inner.__qualname__}"
        return cls(method.upper() if method else None, path, view)

    def match_path(self, route: Route) -> str | None:
        """Return the path to request (template or concrete) if the target selects `route`."""
        if self.path is None:
            return route.path
        if self.path == route.path:
            return route.path
        if "*" in self.path or "?" in self.path:
            return route.path if fnmatch.fnmatchcase(route.path, self.path) else None
        if path_regex(route.path).match(self.path):
            return self.path  # concrete path such as /items/42
        return None

    def match_view(self, route: Route, method: str) -> bool:
        """`shop.views.ItemViewSet`, `ItemViewSet`, `ItemViewSet.list` or `get_item` all select a handler."""
        if self.view is None:
            return True
        handler = route.handlers.get(method) or route.view
        wanted, parts = self.view.split("."), handler.split(".")
        return any(parts[i : i + len(wanted)] == wanted for i in range(len(parts) - len(wanted) + 1))

    def select(self, routes: list[Route]) -> list[tuple[Route, str, str]]:
        """(route, method, path) for each request the target designates."""
        selected = []
        for route in routes:
            path = self.match_path(route)
            if path is None:
                continue
            # An explicit method is used even if the route does not declare it (to test refusals).
            methods = [self.method] if self.method else route.methods
            for method in methods:
                if self.match_view(route, method):
                    selected.append((route, method, path))
        # A concrete path can match several templates (/items/new and /items/{id}): keep the literal one.
        if self.path and not any(c in self.path for c in "*?{"):
            literal = [s for s in selected if s[0].path == self.path]
            if literal:
                return literal
        return selected

    def __str__(self) -> str:
        parts = [p for p in (self.method, self.path) if p]
        if self.view:
            parts.append(f"view={self.view}")
        return " ".join(parts) or "*"


@dataclass
class CustomTest:
    name: str
    fn: Callable
    target: Target | None  # None for a scenario
    request: dict[str, Any] = field(default_factory=dict)


@dataclass
class DataOverride:
    target: Target
    value: Callable | dict[str, Any]


@dataclass
class Registry:
    setups: list[Callable] = field(default_factory=list)
    teardowns: list[Callable] = field(default_factory=list)
    data: list[DataOverride] = field(default_factory=list)
    tests: list[CustomTest] = field(default_factory=list)
    excluded: list[Target] = field(default_factory=list)


_current = Registry()


def current() -> Registry:
    return _current


def setup(fn: Callable) -> Callable:
    """Run `fn(ctx)` once before any probe: log in, create fixtures, set ctx.headers."""
    _current.setups.append(fn)
    return fn


def teardown(fn: Callable) -> Callable:
    """Run `fn(ctx)` once after all probes."""
    _current.teardowns.append(fn)
    return fn


def data(spec: str | None = None, *, method: str | None = None, view: Any = None) -> Callable:
    """Decorate `fn(ctx) -> dict` giving params, query, json, data or headers for the matching routes.

    The values replace the generated ones, for generated probes and custom tests alike.
    """
    target = Target.parse(spec, method, view)

    def decorator(fn: Callable) -> Callable:
        _current.data.append(DataOverride(target, fn))
        return fn

    return decorator


def override(spec: str | None = None, *, method: str | None = None, view: Any = None, **values: Any) -> None:
    """Static form of @data: rp.override("POST /items", json={"name": "Pen"})."""
    _check_request_keys(values)
    _current.data.append(DataOverride(Target.parse(spec, method, view), values))


def test(
    spec: str | None = None,
    *,
    method: str | None = None,
    view: Any = None,
    name: str | None = None,
    **request: Any,
) -> Callable:
    """Decorate `fn(res, ctx)`: routeprobe sends the request of each matching route, then calls fn.

    `spec` is "METHOD /path", "/path", a glob ("/items/*") or a concrete path ("GET /items/42").
    `view` is the view function or class, or its dotted name ("shop.views.ItemViewSet.list").
    Keyword arguments (params, query, json, data, headers) change the request.
    A failed assert marks the test as failed.
    """
    if spec is None and view is None:
        raise TypeError("rp.test needs a route ('GET /items') or view=...; use @rp.scenario for free-form tests")
    _check_request_keys(request)
    target = Target.parse(spec, method, view)

    def decorator(fn: Callable) -> Callable:
        _current.tests.append(CustomTest(name or fn.__name__, fn, target, request))
        return fn

    return decorator


def scenario(fn: Callable | None = None, *, name: str | None = None) -> Callable:
    """Decorate `fn(ctx)` for a multi-step test that sends its own requests with ctx.client."""

    def decorator(func: Callable) -> Callable:
        _current.tests.append(CustomTest(name or func.__name__, func, None))
        return func

    return decorator(fn) if fn is not None else decorator


def exclude(*specs: str, view: Any = None) -> None:
    """Remove routes from the generated probes: rp.exclude("/admin/*", "POST /logout")."""
    for spec in specs:
        _current.excluded.append(Target.parse(spec))
    if view is not None:
        _current.excluded.append(Target.parse(None, None, view))


def _check_request_keys(values: dict[str, Any]) -> None:
    unknown = set(values) - set(REQUEST_KEYS)
    if unknown:
        raise TypeError(f"unknown request option(s) {sorted(unknown)}; expected {list(REQUEST_KEYS)}")


def load_tests(paths: list[str]) -> Registry:
    """Execute the test files into a fresh registry and return it."""
    global _current
    _current = Registry()
    for index, path in enumerate(paths):
        if not os.path.exists(path):
            raise FileNotFoundError(f"test file not found: {path}")
        spec = importlib.util.spec_from_file_location(f"_routeprobe_tests_{index}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return _current
