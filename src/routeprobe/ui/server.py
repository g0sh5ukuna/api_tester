"""Local web panel: `routeprobe ui`. Standard library only, bound to localhost.

The server is single-threaded on purpose: the app under test (Django test database, Flask and
FastAPI test clients) is only ever called from one thread.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
import webbrowser
from collections import Counter
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib import resources
from typing import Any

from ..adapters import load_adapter
from ..models import Request
from ..registry import Registry, load_tests
from ..report import coverage
from ..runner import Options, Runner

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class Panel:
    """Holds the application and the user's tests between requests of the panel."""

    def __init__(self, args, settings: dict):
        self.settings = settings
        self.adapter = load_adapter(
            settings["app"], settings["framework"], test_db=settings["test_db"], include_admin=settings["include_admin"]
        )
        self.only = args.only
        self.runner: Runner | None = None
        self.session_ready = False
        self.load_error: str | None = None

    def _fresh_runner(self, auto: bool = True, custom: bool = True, only: str | None = None) -> Runner:
        # Test files are re-read on every run so edits are picked up without restarting.
        try:
            registry = load_tests(self.settings["tests"])
            self.load_error = None
        except Exception as exc:
            registry = Registry()
            self.load_error = f"{type(exc).__name__}: {exc}"
        options = Options(
            read_only=self.settings["read_only"],
            auto=auto,
            custom=custom,
            checks=self.settings["checks"],
            only=only or self.only,
            exclude=self.settings["exclude"],
            slow_ms=self.settings["slow_ms"],
        )
        return Runner(self.adapter, registry, options)

    def routes(self) -> dict[str, Any]:
        runner = self._fresh_runner()
        routes = []
        for route in runner.routes():
            item = route.to_dict()
            samples = {}
            for method in route.methods:
                try:
                    samples[method] = runner.build_request(route, method).to_dict()
                except Exception as exc:
                    samples[method] = {"method": method, "path": route.path, "error": str(exc)}
            item["samples"] = samples
            routes.append(item)
        return {
            "framework": self.adapter.framework,
            "app": self.adapter.label,
            "tests": self.settings["tests"],
            "read_only": self.settings["read_only"],
            "load_error": self.load_error,
            "routes": routes,
        }

    def run(self, body: dict[str, Any]) -> dict[str, Any]:
        self.runner = self._fresh_runner(body.get("auto", True), body.get("custom", True), body.get("only") or None)
        started = time.perf_counter()
        results = self.runner.run()
        self.session_ready = True
        counts = Counter(r.status for r in results)
        return {
            "results": [r.to_dict() for r in results],
            "summary": dict(counts),
            "coverage": coverage(results),
            "seconds": round(time.perf_counter() - started, 3),
            "load_error": self.load_error,
        }

    def send(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.runner is None or not self.session_ready:
            # Run @rp.setup once so manual requests carry the same auth headers as the tests.
            self.runner = self._fresh_runner(auto=False, custom=False)
            self.runner.run()
            self.session_ready = True
        kind = body.get("bodyType", "json")
        raw = body.get("body") or ""
        payload_json = payload_data = None
        if raw.strip() and kind == "json":
            payload_json = json.loads(raw)
        elif raw.strip() and kind == "form":
            payload_data = json.loads(raw)
        request = Request(
            body["method"].upper(),
            body["path"],
            body.get("query") or {},
            {**self.runner.headers, **(body.get("headers") or {})},
            payload_json,
            payload_data,
        )
        return {"request": request.to_dict(), "response": self.adapter.send(request).to_dict(limit=200_000)}


def _make_handler(panel: Panel, allowed_hosts: set[str]):
    page = resources.files("routeprobe.ui").joinpath("index.html").read_text(encoding="utf-8")

    class Handler(BaseHTTPRequestHandler):
        server_version = "routeprobe"

        def log_message(self, *args):  # keep the terminal quiet
            pass

        def _reply(self, status: int, payload: Any, content_type: str = "application/json", extra=None) -> None:
            data = payload if isinstance(payload, bytes) else json.dumps(payload, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        def _trusted(self) -> bool:
            # Host check blocks DNS rebinding; Origin check blocks other sites posting to the panel.
            host = self.headers.get("Host", "")
            if host not in allowed_hosts:
                return False
            origin = self.headers.get("Origin")
            return origin is None or origin.split("://", 1)[-1] in allowed_hosts

        def do_GET(self):
            if not self._trusted():
                return self._reply(403, {"error": "forbidden host or origin"})
            if self.path in ("/", "/index.html"):
                nonce = secrets.token_urlsafe(16)
                csp = (
                    f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'unsafe-inline'; "
                    "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'"
                )
                html = page.replace("__NONCE__", nonce).encode()
                return self._reply(200, html, "text/html; charset=utf-8", {"Content-Security-Policy": csp})
            if self.path == "/favicon.ico":
                return self._reply(204, b"", "image/x-icon")
            if self.path == "/api/routes":
                return self._safe(panel.routes)
            self._reply(404, {"error": "not found"})

        def do_POST(self):
            if not self._trusted():
                return self._reply(403, {"error": "forbidden host or origin"})
            # A JSON content type forces a CORS preflight that this server never approves.
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self._reply(415, {"error": "expected application/json"})
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._reply(400, {"error": "invalid JSON"})
            actions = {"/api/run": panel.run, "/api/send": panel.send}
            if self.path not in actions:
                return self._reply(404, {"error": "not found"})
            return self._safe(actions[self.path], body)

        def _safe(self, fn, *args):
            try:
                return self._reply(200, fn(*args))
            except json.JSONDecodeError as exc:
                return self._reply(400, {"error": f"invalid JSON in the request body: {exc}"})
            except Exception as exc:
                return self._reply(500, {"error": f"{type(exc).__name__}: {exc}"})

    return Handler


def serve(args, settings: dict) -> int:
    panel = Panel(args, settings)
    host, port = args.host, args.port
    if host not in LOOPBACK:
        print(f"routeprobe: warning, the panel on {host} lets anyone on the network send requests to your app.")
    allowed = {f"{h}:{port}" for h in ("127.0.0.1", "localhost", "[::1]")} | {f"{host}:{port}"}
    server = HTTPServer((host, port), _make_handler(panel, allowed))
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}/"
    with panel.adapter:
        print(f"routeprobe · {panel.adapter.framework} · {panel.adapter.label}", flush=True)
        print(f"panel: {url}  (Ctrl+C to stop)", flush=True)
        if not args.no_browser:
            threading.Timer(0.5, webbrowser.open, (url,)).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print()
        finally:
            server.server_close()
    return 0
