"""Build requests for each route, run the generated checks and the user's tests."""

from __future__ import annotations

import linecache
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import quote

from .adapters.base import Adapter
from .models import ERROR, FAIL, METHOD_ORDER, PASS, SKIP, WARN, WRITE_METHODS, Request, Response, Result, Route
from .registry import CustomTest, Registry, Target
from .schema import has_required_fields, sample, sample_param

ALL_CHECKS = ("server_error", "invalid_body", "method_not_allowed")


class Client:
    """Sends requests to the application in-process. ctx.headers are added to every request."""

    def __init__(self, adapter: Adapter, default_headers: dict[str, str]):
        self._adapter = adapter
        self._headers = default_headers

    def request(self, method: str, path: str, *, query=None, json=None, data=None, headers=None) -> Response:
        request = Request(method.upper(), path, dict(query or {}), {**self._headers, **(headers or {})}, json, data)
        return self._adapter.send(request)

    def get(self, path: str, **kwargs) -> Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> Response:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs) -> Response:
        return self.request("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs) -> Response:
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs) -> Response:
        return self.request("DELETE", path, **kwargs)


@dataclass
class Context:
    """Passed to setup, data and test functions."""

    client: Client
    headers: dict[str, str]
    store: dict[str, Any] = field(default_factory=dict)  # share values between tests (created ids...)
    route: Route | None = None
    method: str | None = None


def fill_path(template: str, params: dict[str, Any], route: Route | None = None) -> str:
    types = {p.name: p.type for p in route.path_params} if route else {}
    for name, value in params.items():
        safe = "/" if types.get(name) == "path" else ""
        template = template.replace("{" + name + "}", quote(str(value), safe=safe))
    return template


@dataclass
class Options:
    read_only: bool = False
    auto: bool = True
    custom: bool = True
    checks: tuple[str, ...] = ALL_CHECKS
    only: str | None = None  # glob or substring on path / view
    exclude: tuple[str, ...] = ()
    slow_ms: float = 1000.0


class Runner:
    def __init__(self, adapter: Adapter, registry: Registry, options: Options | None = None):
        self.adapter = adapter
        self.registry = registry
        self.options = options or Options()
        self.headers: dict[str, str] = {}
        self.ctx = Context(Client(adapter, self.headers), self.headers)
        self._routes: list[Route] | None = None

    # -- routes ------------------------------------------------------------------

    def routes(self) -> list[Route]:
        if self._routes is None:
            self._routes = [r for r in self.adapter.discover() if self._selected(r)]
        return self._routes

    def _selected(self, route: Route) -> bool:
        only = self.options.only
        if only and not (Target.parse(only).match_path(route) or only in route.path or only in route.view):
            return False
        return not any(Target.parse(spec).match_path(route) for spec in self.options.exclude)

    def _excluded(self, route: Route, method: str) -> bool:
        return any(
            t.match_path(route) and (t.method in (None, method)) and t.match_view(route, method)
            for t in self.registry.excluded
        )

    # -- requests ----------------------------------------------------------------

    def build_request(self, route: Route, method: str, path: str | None = None, extra: dict | None = None) -> Request:
        """The request routeprobe sends for `method` on `route`: samples, then @rp.data, then `extra`."""
        params = {p.name: sample_param(p.type, p.schema, route.components, p.name) for p in route.path_params}
        query = {
            p.name: sample_param(p.type, p.schema, route.components, p.name) for p in route.query_params if p.required
        }
        body = route.body_schemas.get(method)
        values: dict[str, Any] = {
            "params": params,
            "query": query,
            "json": sample(body, route.components) if body is not None else None,
            "data": None,
            "headers": {},
        }
        self.ctx.route, self.ctx.method = route, method
        for override in self.registry.data:
            if any(m == method for _, m, _ in override.target.select([route])):
                given = override.value(self.ctx) if callable(override.value) else override.value
                _merge(values, given or {})
        _merge(values, extra or {})
        return Request(
            method,
            fill_path(path or route.path, values["params"], route),
            values["query"],
            {**self.headers, **values["headers"]},
            values["json"],
            values["data"],
        )

    # -- run ---------------------------------------------------------------------

    def run(self, on_result: Callable[[Result], None] | None = None) -> list[Result]:
        results: list[Result] = []

        def emit(result: Result) -> None:
            results.append(result)
            if on_result:
                on_result(result)

        for fn in self.registry.setups:
            error = _call(fn, self.ctx)
            if error:
                emit(Result(f"setup {fn.__name__}", ERROR, "custom", message=error[0], detail=error[1]))
        if self.options.custom:
            for test in self.registry.tests:
                for result in self._run_custom(test):
                    emit(result)
        if self.options.auto:
            for result in self._run_auto():
                emit(result)
        for fn in self.registry.teardowns:
            error = _call(fn, self.ctx)
            if error:
                emit(Result(f"teardown {fn.__name__}", ERROR, "custom", message=error[0], detail=error[1]))
        return results

    def _run_auto(self):
        routes = self.routes()
        checks = self.options.checks
        for method in METHOD_ORDER:
            for route in routes:
                if method not in route.methods or self._excluded(route, method):
                    continue
                label = f"{method} {route.path}"
                if self.options.read_only and method in WRITE_METHODS:
                    yield Result(label, SKIP, "auto", route, message="write method skipped (--read-only)")
                    continue
                try:
                    request = self.build_request(route, method)
                except Exception as exc:
                    yield Result(label, ERROR, "auto", route, message=f"@rp.data failed: {exc}", detail=traceback.format_exc())
                    continue
                if "server_error" in checks:
                    yield self._check_server_error(label, route, request)
                if "invalid_body" in checks and method in route.body_schemas and has_required_fields(
                    route.body_schemas[method], route.components
                ):
                    yield self._check_invalid_body(route, request)
        if "method_not_allowed" in checks and not self.options.read_only:
            for route in routes:
                result = self._check_method_not_allowed(route)
                if result:
                    yield result

    def _check_server_error(self, label: str, route: Route, request: Request) -> Result:
        response = self.adapter.send(request)
        if response.status >= 500:
            return Result(label, FAIL, "auto", route, request, response, _server_error_message(response), response.error or "")
        if response.elapsed_ms > self.options.slow_ms:
            return Result(label, WARN, "auto", route, request, response, f"slow: {response.elapsed_ms:.0f} ms")
        return Result(label, PASS, "auto", route, request, response)

    def _check_invalid_body(self, route: Route, valid: Request) -> Result:
        request = Request(valid.method, valid.path, valid.query, valid.headers, {}, None)
        response = self.adapter.send(request)
        label = f"{valid.method} {route.path} · empty body"
        if response.status >= 500:
            return Result(label, FAIL, "auto", route, request, response, "crashes on an invalid body: " + _server_error_message(response), response.error or "")
        if response.status < 400:
            return Result(label, FAIL, "auto", route, request, response, f"accepted an empty body ({response.status}) although fields are required")
        return Result(label, PASS, "auto", route, request, response)

    def _check_method_not_allowed(self, route: Route) -> Result | None:
        if not route.methods_known:
            return None
        method = next((m for m in ("PATCH", "PUT", "DELETE", "POST") if m not in route.methods), None)
        if method is None or self._excluded(route, method):
            return None
        request = self.build_request(route, route.methods[0])
        request = Request(method, request.path, request.query, request.headers)
        response = self.adapter.send(request)
        label = f"{method} {route.path} · undeclared method"
        if response.status >= 500:
            return Result(label, FAIL, "auto", route, request, response, _server_error_message(response), response.error or "")
        if response.status < 400:
            return Result(label, FAIL, "auto", route, request, response, f"{method} is not declared but returned {response.status}")
        return Result(label, PASS, "auto", route, request, response)

    def _run_custom(self, test: CustomTest):
        if test.target is None:
            self.ctx.route = self.ctx.method = None
            error = _call(test.fn, self.ctx)
            yield _custom_result(test.name, error)
            return
        selected = test.target.select(self.routes())
        if not selected:
            yield Result(test.name, ERROR, "custom", message=f"no route matches {test.target}")
            return
        for route, method, path in selected:
            label = f"{test.name} [{method} {path}]" if len(selected) > 1 else test.name
            try:
                request = self.build_request(route, method, path, test.request)
            except Exception as exc:
                yield Result(label, ERROR, "custom", route, message=f"building the request failed: {exc}", detail=traceback.format_exc())
                continue
            response = self.adapter.send(request)
            error = _call(test.fn, response, self.ctx)
            result = _custom_result(label, error)
            result.route, result.request, result.response = route, request, response
            if result.status == PASS and response.status >= 500:
                result.message = f"passed, but the response is {response.status}"
                result.status = WARN
                result.detail = response.error or ""
            yield result


def _merge(values: dict[str, Any], given: dict[str, Any]) -> None:
    for key, value in given.items():
        if key in ("params", "query", "headers"):
            values[key] = {**values[key], **(value or {})}
        elif key in ("json", "data"):
            values[key] = value
        else:
            raise TypeError(f"unknown request option {key!r}; expected params, query, json, data or headers")


def _call(fn: Callable, *args) -> tuple[str, str, bool] | None:
    """Call fn; on exception return (message, traceback, is_assertion)."""
    try:
        fn(*args)
    except AssertionError as exc:
        return (str(exc) or _failed_assert_line(exc) or "assertion failed", traceback.format_exc(), True)
    except Exception as exc:
        return (f"{type(exc).__name__}: {exc}", traceback.format_exc(), False)
    return None


def _failed_assert_line(exc: BaseException) -> str:
    tb = exc.__traceback__
    while tb and tb.tb_next:
        tb = tb.tb_next
    if tb is None:
        return ""
    return linecache.getline(tb.tb_frame.f_code.co_filename, tb.tb_lineno).strip()


def _custom_result(name: str, error) -> Result:
    if error is None:
        return Result(name, PASS, "custom")
    message, detail, is_assertion = error
    return Result(name, FAIL if is_assertion else ERROR, "custom", message=message, detail=detail)


def _server_error_message(response: Response) -> str:
    if response.error:
        last = response.error.strip().splitlines()[-1]
        return f"server error {response.status}: {last}"
    return f"server error {response.status}"


__all__ = ["ALL_CHECKS", "Client", "Context", "Options", "Runner", "fill_path"]
