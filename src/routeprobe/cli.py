from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import __version__
from .adapters import LoadError, load_adapter
from .config import DEFAULT_TESTS_FILE, default_tests, load_config
from .models import ERROR, FAIL
from .registry import load_tests
from .report import ConsoleReporter, write_json
from .runner import ALL_CHECKS, Options, Runner

TEMPLATE = '''"""Your own routeprobe tests. Run them with: routeprobe run"""

import routeprobe as rp


# Runs once before every probe: authenticate, create fixtures...
# @rp.setup
# def login(ctx):
#     res = ctx.client.post("/auth/login", json={"username": "demo", "password": "demo"})
#     ctx.headers["Authorization"] = f"Bearer {res.json['access']}"


# Real values for a route, used by generated probes and by your tests.
# @rp.data("POST /items")
# def new_item(ctx):
#     return {"json": {"name": "Pen", "price": 2}}


# A test bound to a route: routeprobe sends the request, you check the response.
# @rp.test("GET /items/{item_id}", params={"item_id": 1})
# def item_exists(res, ctx):
#     assert res.status == 200
#     assert res.json["id"] == 1


# A test bound to a view: every method of the view is tested.
# @rp.test(view="shop.views.ItemViewSet")
# def no_server_error(res, ctx):
#     assert res.status < 500, res.text


# A free-form scenario sending its own requests.
# @rp.scenario
# def create_then_read(ctx):
#     created = ctx.client.post("/items", json={"name": "Pen", "price": 2})
#     assert created.status == 201, created.text
#     item_id = created.json["id"]
#     assert ctx.client.get(f"/items/{item_id}").status == 200


# Routes the generated probes must not call.
# rp.exclude("POST /auth/logout", "/admin/*")
'''


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--app", help="Flask/FastAPI: module:app or module:create_app(); Django: mysite.settings")
    parser.add_argument("--framework", choices=("django", "flask", "fastapi"), help="skip framework detection")
    parser.add_argument("--include-admin", action="store_true", default=None, help="Django: also probe /admin")
    parser.add_argument("--no-test-db", dest="test_db", action="store_false", default=None,
                        help="Django: use the configured database instead of a temporary test database")
    parser.add_argument("--only", help="keep routes whose path or view matches (glob or substring)")
    parser.add_argument("--exclude", action="append", default=[], metavar="PATTERN", help="skip routes matching PATTERN")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="routeprobe", description="Test every route of a Django, Flask or FastAPI API.")
    parser.add_argument("--version", action="version", version=f"routeprobe {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    routes = sub.add_parser("routes", help="list the discovered routes and their methods")
    _common(routes)
    routes.add_argument("--json", action="store_true", help="print JSON")

    run = sub.add_parser("run", help="probe every route and run your tests")
    _common(run)
    run.add_argument("--tests", action="append", default=None, metavar="FILE", help=f"test file (default: {DEFAULT_TESTS_FILE})")
    run.add_argument("--read-only", action="store_true", default=None, help="never send POST, PUT, PATCH or DELETE")
    run.add_argument("--no-auto", action="store_true", help="run only your tests, not the generated probes")
    run.add_argument("--no-custom", action="store_true", help="run only the generated probes")
    run.add_argument("--checks", help=f"comma-separated generated checks (default: {','.join(ALL_CHECKS)})")
    run.add_argument("--slow-ms", type=float, default=None, help="warn when a response takes longer (default 1000)")
    run.add_argument("--report", metavar="FILE", help="write a JSON report")
    run.add_argument("-v", "--verbose", action="store_true", help="print full tracebacks inline")

    ui = sub.add_parser("ui", help="open the local web panel")
    _common(ui)
    ui.add_argument("--tests", action="append", default=None, metavar="FILE")
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--no-browser", action="store_true", help="do not open a browser")

    sub.add_parser("init", help=f"create a {DEFAULT_TESTS_FILE} template")
    return parser


def settings_from(args, config: dict) -> dict:
    """Merge pyproject.toml and command line; the command line wins."""
    def pick(name, default=None):
        value = getattr(args, name, None)
        return value if value is not None else config.get(name, default)

    checks = pick("checks", ALL_CHECKS)
    if isinstance(checks, str):
        checks = tuple(c.strip() for c in checks.split(",") if c.strip())
    unknown = set(checks) - set(ALL_CHECKS)
    if unknown:
        raise ValueError(f"unknown check(s) {sorted(unknown)}; available: {', '.join(ALL_CHECKS)}")
    tests = pick("tests")
    if isinstance(tests, str):
        tests = [tests]
    return {
        "app": pick("app"),
        "framework": pick("framework"),
        "test_db": pick("test_db", True),
        "include_admin": pick("include_admin", False),
        "tests": tests if tests is not None else default_tests(),
        "exclude": tuple(config.get("exclude", ())) + tuple(getattr(args, "exclude", ()) or ()),
        "read_only": pick("read_only", False),
        "checks": tuple(checks),
        "slow_ms": pick("slow_ms", 1000.0),
    }


def make_runner(settings: dict, args) -> Runner:
    adapter = load_adapter(
        settings["app"], settings["framework"], test_db=settings["test_db"], include_admin=settings["include_admin"]
    )
    registry = load_tests(settings["tests"])
    options = Options(
        read_only=settings["read_only"],
        auto=not getattr(args, "no_auto", False),
        custom=not getattr(args, "no_custom", False),
        checks=settings["checks"],
        only=args.only,
        exclude=settings["exclude"],
        slow_ms=settings["slow_ms"],
    )
    return Runner(adapter, registry, options)


def cmd_routes(args, settings) -> int:
    runner = make_runner({**settings, "tests": []}, args)
    routes = runner.routes()
    if args.json:
        print(json.dumps([r.to_dict() for r in routes], indent=2))
        return 0
    out = ConsoleReporter()
    out.header(runner.adapter.framework, runner.adapter.label, len(routes))
    out.routes(routes)
    return 0


def cmd_run(args, settings) -> int:
    runner = make_runner(settings, args)
    out = ConsoleReporter(verbose=args.verbose)
    routes = runner.routes()
    out.header(runner.adapter.framework, runner.adapter.label, len(routes))
    if runner.adapter.framework != "django" and not settings["read_only"] and runner.options.auto:
        out.write("  note: POST/PUT/PATCH/DELETE probes use the database configured in your app; "
                  "use --read-only or point --app to a test configuration.\n")
    started = time.perf_counter()
    with runner.adapter:
        results = runner.run(out.result)
    seconds = time.perf_counter() - started
    out.summary(results, seconds)
    if args.report:
        write_json(args.report, runner.adapter.framework, runner.adapter.label, routes, results, seconds)
        out.write(f"report written to {args.report}")
    return 1 if any(r.status in (FAIL, ERROR) for r in results) else 0


def cmd_init(_args) -> int:
    if os.path.exists(DEFAULT_TESTS_FILE):
        print(f"{DEFAULT_TESTS_FILE} already exists", file=sys.stderr)
        return 2
    with open(DEFAULT_TESTS_FILE, "w", encoding="utf-8") as fh:
        fh.write(TEMPLATE)
    print(f"created {DEFAULT_TESTS_FILE}. Then: routeprobe routes, routeprobe run, routeprobe ui")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "init":
        return cmd_init(args)
    try:
        settings = settings_from(args, load_config())
        if args.command == "routes":
            return cmd_routes(args, settings)
        if args.command == "run":
            return cmd_run(args, settings)
        from .ui.server import serve

        return serve(args, settings)
    except (LoadError, ValueError, FileNotFoundError) as exc:
        print(f"routeprobe: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
