"""Data shared by adapters, runner, reporters and user tests."""

from __future__ import annotations

import json as _json
from dataclasses import dataclass, field
from typing import Any

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Generated probes run in this order so that DELETE does not remove data a GET expects.
METHOD_ORDER = ("GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE")


@dataclass
class Param:
    """A path or query parameter of a route."""

    name: str
    location: str  # "path" or "query"
    type: str = "string"  # string, integer, number, boolean, uuid, slug, path
    required: bool = True
    schema: dict[str, Any] | None = None


@dataclass
class Route:
    """One URL pattern of the application, with the HTTP methods its view declares."""

    path: str  # normalized template: /items/{item_id}
    methods: list[str]
    view: str  # dotted name of the view function or class
    framework: str
    name: str | None = None
    params: list[Param] = field(default_factory=list)
    # JSON schema of the request body, per method, when the framework exposes it.
    body_schemas: dict[str, dict[str, Any]] = field(default_factory=dict)
    # False when the view does not declare its methods (plain Django function view).
    methods_known: bool = True
    # Components used to resolve $ref in body schemas (OpenAPI).
    components: dict[str, Any] = field(default_factory=dict)
    # Dotted name of the function handling each method: views.ItemViewSet.list, views.get_item...
    handlers: dict[str, str] = field(default_factory=dict)

    @property
    def path_params(self) -> list[Param]:
        return [p for p in self.params if p.location == "path"]

    @property
    def query_params(self) -> list[Param]:
        return [p for p in self.params if p.location == "query"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "methods": self.methods,
            "view": self.view,
            "name": self.name,
            "framework": self.framework,
            "methods_known": self.methods_known,
            "params": [
                {"name": p.name, "in": p.location, "type": p.type, "required": p.required}
                for p in self.params
            ],
            "body": {m: True for m in self.body_schemas},
            "handlers": self.handlers,
        }


@dataclass
class Request:
    method: str
    path: str  # final URL path, parameters substituted
    query: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    json: Any = None
    data: Any = None  # form data, sent instead of json when set

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "path": self.path,
            "query": self.query,
            "headers": self.headers,
            "json": self.json,
            "data": self.data,
        }


@dataclass
class Response:
    """Framework-independent response. A view that raised gives status 500 and `error`."""

    status: int
    headers: dict[str, str]
    body: bytes
    elapsed_ms: float
    error: str | None = None  # traceback when the view raised

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    @property
    def json(self) -> Any:
        """Parsed JSON body, or None when the body is not JSON."""
        try:
            return _json.loads(self.body)
        except (ValueError, UnicodeDecodeError):
            return None

    def to_dict(self, limit: int = 4000) -> dict[str, Any]:
        text = self.text
        return {
            "status": self.status,
            "headers": self.headers,
            "body": text[:limit] + ("…" if len(text) > limit else ""),
            "elapsed_ms": round(self.elapsed_ms, 2),
            "error": self.error,
        }


PASS, FAIL, ERROR, SKIP, WARN = "pass", "fail", "error", "skip", "warn"


@dataclass
class Result:
    """Outcome of one probe: a generated check or a user test."""

    name: str
    status: str
    kind: str  # "auto" or "custom"
    route: Route | None = None
    request: Request | None = None
    response: Response | None = None
    message: str = ""
    detail: str = ""  # traceback or long explanation
    check: str = ""  # generated check that produced the result: server_error, slow...

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "kind": self.kind,
            "check": self.check,
            "route": self.route.to_dict() if self.route else None,
            "request": self.request.to_dict() if self.request else None,
            "response": self.response.to_dict() if self.response else None,
            "message": self.message,
            "detail": self.detail,
        }
