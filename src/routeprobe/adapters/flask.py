from __future__ import annotations

import re

from ..models import Param, Request, Route
from .base import Adapter, dotted_name, merge_routes, sort_methods

# <name>, <int:name>, <any(a, b):name>
_RULE_PARAM = re.compile(r"<(?:(\w+)(?:\(([^)]*)\))?:)?(\w+)>")

_CONVERTER_TYPES = {
    "int": "integer",
    "float": "number",
    "uuid": "uuid",
    "path": "path",
    "string": "string",
    "default": "string",
}


class FlaskAdapter(Adapter):
    framework = "flask"

    def __init__(self, app, label: str):
        super().__init__(label)
        self.app = app
        self._client = None
        self._propagate = None

    def discover(self) -> list[Route]:
        routes = []
        for rule in self.app.url_map.iter_rules():
            if rule.endpoint == "static" or rule.endpoint.endswith(".static"):
                continue
            params = []
            for converter, args, name in _RULE_PARAM.findall(rule.rule):
                schema = None
                if converter == "any" and args:
                    choices = [a.strip().strip("'\"") for a in args.split(",")]
                    schema = {"type": "string", "enum": choices}
                params.append(Param(name, "path", _CONVERTER_TYPES.get(converter or "default", "string"), schema=schema))
            path = _RULE_PARAM.sub(lambda m: "{" + m.group(3) + "}", rule.rule)
            view_func = self.app.view_functions.get(rule.endpoint)
            view_class = getattr(view_func, "view_class", None)
            view = dotted_name(view_class or view_func)
            methods = sort_methods(set(rule.methods or ()) - {"HEAD", "OPTIONS"})
            handlers = {
                m: f"{view}.{m.lower()}" if view_class and hasattr(view_class, m.lower()) else view
                for m in methods
            }
            routes.append(
                Route(
                    path=path,
                    methods=methods,
                    view=view,
                    framework=self.framework,
                    name=rule.endpoint,
                    params=params,
                    handlers=handlers,
                )
            )
        return merge_routes(routes)

    def start(self) -> None:
        # Let exceptions reach routeprobe so the report shows the traceback.
        self._propagate = self.app.config.get("PROPAGATE_EXCEPTIONS")
        self.app.config["PROPAGATE_EXCEPTIONS"] = True
        self._client = self.app.test_client()

    def stop(self) -> None:
        self.app.config["PROPAGATE_EXCEPTIONS"] = self._propagate
        self._client = None

    def _send(self, request: Request):
        kwargs = {"method": request.method, "query_string": request.query or None, "headers": request.headers or None}
        if request.data is not None:
            kwargs["data"] = request.data
        elif request.json is not None:
            kwargs["json"] = request.json
        response = self._client.open(request.path, **kwargs)
        return response.status_code, dict(response.headers), response.get_data()
