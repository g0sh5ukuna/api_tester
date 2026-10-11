from __future__ import annotations

import json
import os
import sys
from collections import Counter

from .models import ERROR, FAIL, PASS, SKIP, WARN, Result, Route

_COLORS = {PASS: "32", FAIL: "31", ERROR: "35", SKIP: "90", WARN: "33"}
_METHOD_WIDTH = 7


def _use_color(stream) -> bool:
    return hasattr(stream, "isatty") and stream.isatty() and "NO_COLOR" not in os.environ


class ConsoleReporter:
    def __init__(self, stream=None, verbose: bool = False):
        self.stream = stream or sys.stdout
        self.color = _use_color(self.stream)
        self.verbose = verbose

    def _paint(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def write(self, text: str = "") -> None:
        print(text, file=self.stream, flush=True)

    def header(self, framework: str, label: str, routes: int) -> None:
        self.write(self._paint(f"routeprobe · {framework} · {label} · {routes} routes", "1"))
        self.write()

    def result(self, result: Result) -> None:
        tag = self._paint(f"{result.status.upper():<5}", _COLORS[result.status])
        status = f"{result.response.status}" if result.response else "   "
        elapsed = f"{result.response.elapsed_ms:7.1f} ms" if result.response else " " * 10
        kind = "" if result.kind == "auto" else self._paint("custom ", "36")
        width = 52 if result.kind == "auto" else 45
        line = f"  {tag} {kind}{result.name:<{width}} {status:>3} {elapsed}"
        if result.message and result.status != PASS:
            line += "  " + result.message
        self.write(line)
        if self.verbose and result.detail and result.status in (FAIL, ERROR):
            self.write(self._indent(result.detail))

    def routes(self, routes: list[Route]) -> None:
        for route in routes:
            methods = ",".join(route.methods)
            unknown = "" if route.methods_known else self._paint("  (methods not declared, GET assumed)", "33")
            self.write(f"  {methods:<22} {route.path:<45} {route.view}{unknown}")

    def summary(self, results: list[Result], seconds: float) -> None:
        failures = [r for r in results if r.status in (FAIL, ERROR)]
        if failures and not self.verbose:
            self.write()
            self.write(self._paint("Failures", "1"))
            for result in failures:
                self.write(f"\n  {self._paint(result.name, '1')}")
                if result.request:
                    self.write(f"    request : {result.request.method} {result.request.path}"
                               + (f"  query={result.request.query}" if result.request.query else "")
                               + (f"  json={json.dumps(result.request.json)[:200]}" if result.request.json is not None else ""))
                if result.response and not result.response.error:
                    self.write(f"    response: {result.response.status} {result.response.text[:300]}")
                if result.message:
                    self.write(f"    {result.message}")
                if result.detail:
                    self.write(self._indent(_tail(result.detail, 12)))
        counts = Counter(r.status for r in results)
        parts = [
            self._paint(f"{counts[s]} {label}", _COLORS[s])
            for s, label in ((PASS, "passed"), (FAIL, "failed"), (ERROR, "errors"), (WARN, "warnings"), (SKIP, "skipped"))
            if counts[s]
        ]
        self.write()
        self.write(" · ".join(parts or ["no probe"]) + f"  ({seconds:.2f} s)")

    def _indent(self, text: str) -> str:
        return "\n".join("      " + line for line in text.rstrip().splitlines())


def _tail(text: str, lines: int) -> str:
    rows = text.rstrip().splitlines()
    return "\n".join(rows[-lines:])


def write_json(path: str, framework: str, label: str, routes: list[Route], results: list[Result], seconds: float) -> None:
    counts = Counter(r.status for r in results)
    payload = {
        "framework": framework,
        "app": label,
        "duration_s": round(seconds, 3),
        "summary": {s: counts[s] for s in (PASS, FAIL, ERROR, WARN, SKIP)},
        "routes": [r.to_dict() for r in routes],
        "results": [r.to_dict() for r in results],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)
