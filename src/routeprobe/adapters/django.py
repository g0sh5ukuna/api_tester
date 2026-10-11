from __future__ import annotations

import json
import os
import re
from urllib.parse import urlencode

from ..models import Param, Request, Route
from .base import Adapter, dotted_name, sort_methods

# <pk>, <int:pk> in path() routes
_ROUTE_PARAM = re.compile(r"<(?:(\w+):)?(\w+)>")
_DJANGO_CONVERTERS = {"int": "integer", "uuid": "uuid", "slug": "slug", "path": "path", "str": "string"}
_INTEGER_REGEX = {r"\d+", r"[0-9]+", r"\d+?", r"[0-9]+?"}
# Characters left in a re_path() pattern that routeprobe cannot turn into a literal path.
_REGEX_LEFTOVER = re.compile(r"[\\()\[\]*+?|{}^$]")


def _regex_to_template(regex: str) -> tuple[str, list[Param]]:
    """`^items/(?P<pk>[^/.]+)/$` -> (`items/{pk}/`, [pk])."""
    regex = regex.lstrip("^").rstrip("$")
    out, params, i = [], [], 0
    while i < len(regex):
        if regex.startswith("(?P<", i):
            end_name = regex.index(">", i)
            name = regex[i + 4 : end_name]
            depth, j = 1, end_name + 1
            while j < len(regex) and depth:
                if regex[j] == "\\":
                    j += 2
                    continue
                depth += {"(": 1, ")": -1}.get(regex[j], 0)
                j += 1
            inner = regex[end_name + 1 : j - 1]
            params.append(Param(name, "path", "integer" if inner in _INTEGER_REGEX else "string"))
            out.append("{" + name + "}")
            i = j
        elif regex[i] == "\\" and i + 1 < len(regex):
            out.append(regex[i + 1])
            i += 2
        else:
            out.append(regex[i])
            i += 1
    template = "".join(out).replace("/?", "/")
    return template, params


def _pattern_to_template(pattern) -> tuple[str, list[Param]]:
    from django.urls.resolvers import RegexPattern

    if isinstance(pattern, RegexPattern):
        return _regex_to_template(pattern.regex.pattern)
    route = str(pattern)
    params = [
        Param(name, "path", _DJANGO_CONVERTERS.get(conv or "str", "string"))
        for conv, name in _ROUTE_PARAM.findall(route)
    ]
    return _ROUTE_PARAM.sub(lambda m: "{" + m.group(2) + "}", route), params


def _function_methods(func) -> list[str] | None:
    """Methods allowed by @require_http_methods / @require_GET..., found in the decorator closure."""
    seen = set()
    while func is not None and id(func) not in seen:
        seen.add(id(func))
        code = getattr(func, "__code__", None)
        if code and "request_method_list" in code.co_freevars and func.__closure__:
            cell = func.__closure__[code.co_freevars.index("request_method_list")]
            return [m.upper() for m in cell.cell_contents]
        func = getattr(func, "__wrapped__", None)
    return None


def _view_info(callback) -> tuple[str, list[str], bool, dict[str, str], type | None]:
    """Return (view name, methods, methods_known, handlers, class) for a URL callback."""
    cls = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
    actions = getattr(callback, "actions", None)
    if cls is not None and cls.__qualname__.endswith("WrappedAPIView"):
        # DRF @api_view: name the decorated function, not the generated class.
        view = f"{cls.__module__}.{cls.__name__}"
        methods = sort_methods(m for m in cls.http_method_names if m not in ("head", "options", "trace") and hasattr(cls, m))
        return view, methods, True, {m: view for m in methods}, cls
    if cls is not None:
        view = dotted_name(cls)
        if actions:  # DRF ViewSet: {"get": "list", "post": "create"}
            methods = sort_methods(actions)
            handlers = {m.upper(): f"{view}.{a}" for m, a in actions.items()}
        else:
            names = [m for m in cls.http_method_names if m not in ("head", "options", "trace") and hasattr(cls, m)]
            methods = sort_methods(names)
            handlers = {m.upper(): f"{view}.{m}" for m in names}
        return view, methods, True, handlers, cls
    func = callback
    while hasattr(func, "__wrapped__"):
        func = func.__wrapped__
    view = dotted_name(func)
    methods = _function_methods(callback)
    known = methods is not None
    methods = sort_methods(methods or ["GET"])
    return view, methods, known, {m: view for m in methods}, None


# DRF field class name -> JSON schema
_DRF_FIELDS = {
    "IntegerField": {"type": "integer"},
    "FloatField": {"type": "number"},
    "DecimalField": {"type": "string", "example": "1.00"},
    "BooleanField": {"type": "boolean"},
    "NullBooleanField": {"type": "boolean"},
    "EmailField": {"type": "string", "format": "email"},
    "URLField": {"type": "string", "format": "uri"},
    "UUIDField": {"type": "string", "format": "uuid"},
    "DateField": {"type": "string", "format": "date"},
    "DateTimeField": {"type": "string", "format": "date-time"},
    "TimeField": {"type": "string", "format": "time"},
    "DurationField": {"type": "string", "example": "01:00:00"},
    "IPAddressField": {"type": "string", "format": "ipv4"},
    "SlugField": {"type": "string", "example": "test-slug"},
    "JSONField": {"type": "object"},
    "DictField": {"type": "object"},
    "HStoreField": {"type": "object"},
    "PrimaryKeyRelatedField": {"type": "integer"},
    "SlugRelatedField": {"type": "string"},
    "HyperlinkedRelatedField": {"type": "string", "format": "uri"},
}
_DRF_SKIPPED = {"HiddenField", "FileField", "ImageField", "SerializerMethodField", "ReadOnlyField"}


def _drf_field_schema(field) -> dict | None:
    name = type(field).__name__
    if name in _DRF_SKIPPED or getattr(field, "read_only", False):
        return None
    if hasattr(field, "child") and name.endswith(("ListSerializer", "ListField")):
        item = _drf_field_schema(field.child) or {}
        return {"type": "array", "items": item}
    if name == "ManyRelatedField":
        return {"type": "array", "items": _drf_field_schema(field.child_relation) or {"type": "integer"}}
    if hasattr(field, "fields") and not name.endswith("Field"):
        return drf_serializer_schema(field)
    choices = getattr(field, "choices", None)
    if choices:
        values = list(choices)
        return {"enum": values[:1] if name != "MultipleChoiceField" else [values[:1]]}
    schema = dict(_DRF_FIELDS.get(name, {"type": "string"}))
    for attr, key in (("max_length", "maxLength"), ("min_length", "minLength"), ("min_value", "minimum"), ("max_value", "maximum")):
        value = getattr(field, attr, None)
        if value is not None:
            schema[key] = value
    return schema


def drf_serializer_schema(serializer) -> dict:
    properties, required = {}, []
    for name, field in serializer.fields.items():
        schema = _drf_field_schema(field)
        if schema is None:
            continue
        properties[name] = schema
        if getattr(field, "required", False):
            required.append(name)
    return {"type": "object", "properties": properties, "required": required}


def _body_schemas(cls, methods: list[str]) -> dict[str, dict]:
    serializer_class = getattr(cls, "serializer_class", None) if cls else None
    if serializer_class is None:
        return {}
    try:
        base = drf_serializer_schema(serializer_class())
    except Exception:
        return {}
    bodies = {}
    for method in methods:
        if method in ("POST", "PUT"):
            bodies[method] = base
        elif method == "PATCH":
            bodies[method] = {**base, "required": []}
    return bodies


def find_settings_module(cwd: str = ".") -> str | None:
    """Read DJANGO_SETTINGS_MODULE from manage.py."""
    manage = os.path.join(cwd, "manage.py")
    if not os.path.exists(manage):
        return None
    with open(manage, encoding="utf-8") as fh:
        match = re.search(r"DJANGO_SETTINGS_MODULE['\"]\s*,\s*['\"]([\w.]+)['\"]", fh.read())
    return match.group(1) if match else None


class DjangoAdapter(Adapter):
    framework = "django"

    def __init__(self, settings_module: str, label: str, *, test_db: bool = True, include_admin: bool = False):
        super().__init__(label)
        os.environ["DJANGO_SETTINGS_MODULE"] = settings_module
        import django

        django.setup()
        self.test_db = test_db
        self.include_admin = include_admin
        self._client = None
        self._old_db_config = None
        self._quiet, self._levels = [], []

    def discover(self) -> list[Route]:
        from django.urls import get_resolver

        routes = []
        for template, params, pattern in self._walk(get_resolver().url_patterns, "", []):
            # DRF registers each route twice; the copy with a .{format} suffix is noise.
            if any(p.name == "format" for p in params):
                continue
            if _REGEX_LEFTOVER.search(re.sub(r"\{\w+\}", "", template)):
                continue
            view, methods, known, handlers, cls = _view_info(pattern.callback)
            routes.append(
                Route(
                    path="/" + template,
                    methods=methods,
                    view=view,
                    framework=self.framework,
                    name=pattern.name,
                    params=params,
                    body_schemas=_body_schemas(cls, methods),
                    methods_known=known,
                    handlers=handlers,
                )
            )
        return routes

    def _walk(self, patterns, prefix: str, params: list[Param]):
        from django.urls.resolvers import URLResolver

        for entry in patterns:
            template, own_params = _pattern_to_template(entry.pattern)
            if isinstance(entry, URLResolver):
                if entry.namespace == "admin" and not self.include_admin:
                    continue
                yield from self._walk(entry.url_patterns, prefix + template, params + own_params)
            else:
                yield prefix + template, params + own_params, entry

    def start(self) -> None:
        import logging

        from django.test import Client
        from django.test.utils import setup_databases, setup_test_environment

        # Django logs every 4xx/5xx with a traceback; routeprobe already reports them.
        self._quiet = [logging.getLogger(name) for name in ("django.request", "django.server")]
        self._levels = [logger.level for logger in self._quiet]
        for logger in self._quiet:
            logger.setLevel(logging.CRITICAL)

        setup_test_environment()  # adds "testserver" to ALLOWED_HOSTS, in-memory email
        if self.test_db:
            # Probes send POST/PUT/DELETE: never run them against the development database.
            self._old_db_config = setup_databases(verbosity=0, interactive=False)
        self._client = Client(raise_request_exception=True)

    def stop(self) -> None:
        from django.test.utils import teardown_databases, teardown_test_environment

        if self._old_db_config is not None:
            teardown_databases(self._old_db_config, verbosity=0)
            self._old_db_config = None
        teardown_test_environment()
        for logger, level in zip(self._quiet, self._levels):
            logger.setLevel(level)
        self._client = None

    def _send(self, request: Request):
        extra = {}
        if request.query:
            extra["QUERY_STRING"] = urlencode(request.query, doseq=True)
        if request.data is not None:
            body, content_type = urlencode(request.data, doseq=True), "application/x-www-form-urlencoded"
        elif request.json is not None:
            body, content_type = json.dumps(request.json), "application/json"
        else:
            body, content_type = "", "application/octet-stream"
        response = self._client.generic(
            request.method, request.path, body, content_type, headers=request.headers or None, **extra
        )
        content = b"".join(response.streaming_content) if response.streaming else response.content
        return response.status_code, dict(response.items()), content
