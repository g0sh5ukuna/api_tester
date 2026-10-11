from __future__ import annotations

import re

from ..models import Param, Request, Route
from .base import Adapter, dotted_name, merge_routes, sort_methods

_CONVERTER = re.compile(r"\{(\w+):\w+\}")
_PATH_PARAM = re.compile(r"\{(\w+)\}")

_FORMAT_TYPES = {"uuid": "uuid"}


def _param_type(schema: dict) -> str:
    if schema.get("format") in _FORMAT_TYPES:
        return _FORMAT_TYPES[schema["format"]]
    kind = schema.get("type")
    if kind is None:
        for option in schema.get("anyOf", []):
            if option.get("type") not in (None, "null"):
                return _param_type(option)
    return kind or "string"


class FastAPIAdapter(Adapter):
    framework = "fastapi"

    def __init__(self, app, label: str):
        super().__init__(label)
        self.app = app
        self._client = None

    def discover(self) -> list[Route]:
        from fastapi.routing import APIRoute

        try:
            spec = self.app.openapi()
        except Exception:
            spec = {}
        components = spec.get("components", {})
        routes = []
        for api_route in self.app.routes:
            if not isinstance(api_route, APIRoute):
                continue
            methods = sort_methods(set(api_route.methods) - {"HEAD", "OPTIONS"})
            path = _CONVERTER.sub(r"{\1}", api_route.path)
            operations = spec.get("paths", {}).get(api_route.path_format, {})
            params: dict[str, Param] = {}
            bodies = {}
            for method in methods:
                operation = operations.get(method.lower(), {})
                for raw in operation.get("parameters", []):
                    if raw.get("in") not in ("path", "query"):
                        continue
                    schema = raw.get("schema", {})
                    params.setdefault(
                        raw["name"],
                        Param(
                            raw["name"],
                            raw["in"],
                            _param_type(schema),
                            required=raw.get("required", raw["in"] == "path"),
                            schema=schema,
                        ),
                    )
                content = operation.get("requestBody", {}).get("content", {})
                if "application/json" in content:
                    bodies[method] = content["application/json"].get("schema", {})
            # Routes excluded from the schema still expose their path parameters.
            for name in _PATH_PARAM.findall(path):
                params.setdefault(name, Param(name, "path"))
            view = dotted_name(api_route.endpoint)
            routes.append(
                Route(
                    path=path,
                    methods=methods,
                    view=view,
                    framework=self.framework,
                    name=api_route.name,
                    params=list(params.values()),
                    body_schemas=bodies,
                    components=components,
                    handlers={m: view for m in methods},
                )
            )
        return merge_routes(routes)

    def start(self) -> None:
        import warnings

        with warnings.catch_warnings():
            # Starlette warns that it prefers httpx2; the test client works with httpx.
            warnings.simplefilter("ignore")
            from fastapi.testclient import TestClient

        # Entering the client runs the lifespan (startup/shutdown) of the app.
        self._client = TestClient(self.app, raise_server_exceptions=True)
        self._client.__enter__()

    def stop(self) -> None:
        if self._client is not None:
            self._client.__exit__(None, None, None)
            self._client = None

    def _send(self, request: Request):
        kwargs = {"params": request.query or None, "headers": request.headers or None}
        if request.data is not None:
            kwargs["data"] = request.data
        elif request.json is not None:
            kwargs["json"] = request.json
        response = self._client.request(request.method, request.path, **kwargs)
        return response.status_code, dict(response.headers), response.content
