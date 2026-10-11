"""End-to-end: run the CLI on each example project and read the JSON report."""

import json
import os
import subprocess
import sys

import pytest

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")


def run_cli(project, *args, tmp_path):
    report = tmp_path / "report.json"
    proc = subprocess.run(
        [sys.executable, "-m", "routeprobe.cli", "run", "--report", str(report), *args],
        cwd=os.path.join(EXAMPLES, project),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert report.exists(), proc.stdout + proc.stderr
    return proc.returncode, json.loads(report.read_text())


def statuses(report):
    return {r["name"]: r["status"] for r in report["results"]}


def test_fastapi(tmp_path):
    code, report = run_cli("fastapi_app", tmp_path=tmp_path)
    found = statuses(report)
    assert code == 1
    assert found["GET /stats"] == "fail"
    assert found["POST /items · empty body"] == "pass"
    checks = {r["name"]: r["check"] for r in report["results"]}
    assert checks["GET /stats"] == "server_error"
    assert checks["POST /items · empty body"] == "invalid_body"
    assert checks["PATCH /items · undeclared method"] == "method_not_allowed"
    assert checks["pen_exists"] == ""
    assert found["pen_exists"] == found["unknown_item_is_404"] == found["create_then_read"] == "pass"
    assert [n for n, s in found.items() if s == "fail"] == ["GET /stats"]
    get_order = next(r for r in report["results"] if r["name"] == "GET /orders/{order_id}")
    assert get_order["request"]["query"] == {"verbose": True}


def test_flask_factory(tmp_path):
    code, report = run_cli("flask_app", "--app", "app:create_app()", tmp_path=tmp_path)
    found = statuses(report)
    assert code == 1
    assert found["PUT /users/{user_id}"] == "fail"
    assert "KeyError" in next(r for r in report["results"] if r["name"] == "PUT /users/{user_id}")["message"]
    routes = {r["path"]: r for r in report["routes"]}
    assert routes["/users/{user_id}"]["handlers"]["PUT"].endswith("UserView.put")


def test_flask_read_only_skips_writes(tmp_path):
    code, report = run_cli("flask_app", "--app", "app:create_app()", "--read-only", tmp_path=tmp_path)
    found = statuses(report)
    assert code == 0
    assert found["PUT /users/{user_id}"] == found["POST /users"] == "skip"


def test_slow_check_warns_without_failing(tmp_path):
    code, report = run_cli("flask_app", "--app", "app:create_app()", "--checks", "slow", "--slow-ms", "0", tmp_path=tmp_path)
    assert code == 0
    assert {r["status"] for r in report["results"]} == {"warn"}
    assert {r["check"] for r in report["results"]} == {"slow"}
    # One request per method: the slow check reuses it, it does not send another one.
    assert len(report["results"]) == sum(len(r["methods"]) for r in report["routes"])


def test_django_uses_test_database(tmp_path):
    code, report = run_cli("django_project", tmp_path=tmp_path)
    found = statuses(report)
    assert code == 1
    assert found["POST /api/report/{year}/ · empty body"] == "fail"
    # The product created by POST is updated then deleted: the test database works.
    assert found["PUT /api/products/{pk}/"] == "pass"
    routes = {r["path"]: r for r in report["routes"]}
    assert routes["/api/products/{pk}/"]["handlers"]["DELETE"] == "shop.views.ProductViewSet.destroy"
    assert routes["/api/ping/"]["view"] == "shop.views.ping"
    assert routes["/legacy/"]["methods_known"] is True
    assert not any(p.startswith("/admin") for p in routes)
    assert not os.path.exists(os.path.join(EXAMPLES, "django_project", "db.sqlite3"))


@pytest.mark.parametrize("project,args", [("fastapi_app", []), ("flask_app", ["--app", "app:create_app()"]), ("django_project", [])])
def test_routes_json(project, args):
    proc = subprocess.run(
        [sys.executable, "-m", "routeprobe.cli", "routes", "--json", *args],
        cwd=os.path.join(EXAMPLES, project), capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)


def test_unknown_app_is_a_clean_error(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "routeprobe.cli", "run", "--app", "nope:app"],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 2
    assert "Cannot load the application" in proc.stderr
