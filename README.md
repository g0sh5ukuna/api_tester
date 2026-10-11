# routeprobe

**English** · [Français](README.fr.md)

Test every route of a **Django (DRF)**, **Flask** or **FastAPI** API while you develop, without writing a test first.

routeprobe loads your application in-process, discovers its routes and the HTTP methods each view declares, builds a plausible request for each one (from the OpenAPI schema, the DRF serializer or the URL converters) and checks that nothing crashes. You add your own test logic, bound to a route or to a view, in a plain Python file.

```
$ routeprobe run
routeprobe · fastapi · main:app · 4 routes

  PASS  custom pen_exists                                    200     5.5 ms
  PASS  GET /items                                           200     0.9 ms
  FAIL  GET /stats                                           500     0.9 ms  server error 500: ZeroDivisionError: float division by zero
  PASS  POST /items                                          201     1.1 ms
  PASS  POST /items · empty body                             422     0.9 ms
  PASS  PATCH /items · undeclared method                     405     0.7 ms

Failures

  GET /stats
      Traceback (project frames):
        File ".../main.py", line 50, in stats
          return {"average": total / len([i for i in ITEMS.values() if i["price"] > 100])}
      ZeroDivisionError: float division by zero
```

## Install

In the virtual environment of the project you want to test:

```bash
pip install -e /path/to/api_tester        # local install
pip install "routeprobe[fastapi]"         # once published; the extra adds httpx for FastAPI's TestClient
```

routeprobe has no dependency of its own: it uses the framework already installed in your project. Python 3.10+.

## Use

Run the commands from the root of your project.

```bash
routeprobe routes                 # list routes, methods and the view handling each one
routeprobe run                    # generated probes + your tests; exit code 1 on failure
routeprobe ui                     # local web panel on http://127.0.0.1:8765
routeprobe init                   # create a routeprobe_tests.py template
```

The application is found automatically:

| Framework | Detection | Explicit |
|---|---|---|
| Django | `manage.py` in the current folder | `--app mysite.settings` |
| FastAPI / Flask | tries `main:app`, `app:app`, `app.main:app`, `src.main:app`, `api:app`, `wsgi:app`, `application:app` | `--app module:app` or `--app "module:create_app()"` |

Useful options of `run`:

| Option | Effect |
|---|---|
| `--read-only` | never send POST, PUT, PATCH or DELETE |
| `--only PATTERN` | keep routes whose path or view matches (`/api/*`, `ItemViewSet`) |
| `--exclude PATTERN` | skip routes (repeatable) |
| `--no-auto` / `--no-custom` | run only your tests / only the generated probes |
| `--checks server_error,invalid_body` | choose the generated checks |
| `--report report.json` | write a JSON report (for CI) |
| `--no-test-db` | Django: use the configured database instead of a temporary one |
| `--include-admin` | Django: also probe `/admin/` |
| `-v` | print tracebacks inline |

## Generated checks

For every route and every method its view declares:

| Check | Sends | Fails when |
|---|---|---|
| `server_error` | a request filled with sample values | the response is 5xx or the view raises |
| `invalid_body` | `{}` to a method whose body has required fields | the view crashes (5xx) or accepts it (2xx) |
| `method_not_allowed` | a method the route does not declare | the view crashes or accepts it |

Responses slower than `--slow-ms` (1000 by default) are reported as warnings.

Where the methods and bodies come from:

- **FastAPI**: routes, path and query parameters, and JSON bodies from the OpenAPI schema.
- **Django**: `path()` and `re_path()` patterns, including `include()` and DRF routers. Methods come from class-based views, DRF `ViewSet` actions, `@api_view` and `@require_http_methods`; JSON bodies come from the DRF `serializer_class`. A plain function view without a method decorator is assumed to handle GET.
- **Flask**: rules, converters (`<int:id>`, `<uuid:...>`, `<any(a,b):...>`) and `MethodView`. Flask has no body schema: give real bodies with `@rp.data`.

## Your own tests

Create `routeprobe_tests.py` at the root of the project (`routeprobe init`), or pass `--tests FILE`.

```python
import routeprobe as rp


@rp.setup
def login(ctx):
    """Runs once before everything. Headers set here are sent with every request."""
    res = ctx.client.post("/api/token/", json={"username": "demo", "password": "demo"})
    ctx.headers["Authorization"] = f"Bearer {res.json['access']}"


@rp.data("POST /api/products/")
def product(ctx):
    """Real values for a route, used by the generated probes and by your tests."""
    return {"json": {"name": "Pen", "price": "2.00"}}


@rp.data("DELETE /api/products/{pk}/")
def disposable_product(ctx):
    created = ctx.client.post("/api/products/", json={"name": "Temp", "price": "1.00"})
    return {"params": {"pk": created.json["id"]}}


@rp.test("GET /api/products/{pk}/", params={"pk": 1})
def product_has_price(res, ctx):
    """Bound to a route: routeprobe sends the request, you check the response."""
    assert res.status == 200, res.text
    assert "price" in res.json


@rp.test(view="shop.views.ProductViewSet")
def viewset_never_crashes(res, ctx):
    """Bound to a view: runs once per method of the view (list, create, retrieve...)."""
    assert res.status < 500


@rp.test(view="ProductViewSet.destroy", headers={"Authorization": ""})
def delete_requires_auth(res, ctx):
    assert res.status in (401, 403)


@rp.scenario
def create_then_read(ctx):
    """Free-form test sending its own requests."""
    created = ctx.client.post("/api/products/", json={"name": "Ruler", "price": "1.00"})
    assert created.status == 201, created.text
    ctx.store["product_id"] = created.json["id"]
    assert ctx.client.get(f"/api/products/{created.json['id']}/").status == 200


rp.exclude("POST /api/logout/", "/api/internal/*")
```

### Selecting routes

The first argument of `@rp.test`, `@rp.data`, `rp.override` and `rp.exclude` accepts:

- `"GET /items/{id}"`: method and route template;
- `"/items/{id}"`: every method the route declares;
- `"GET /items/42"`: a concrete path, matched against the templates;
- `"/api/*"`: a glob;
- `"PATCH /items"`: a method the route does not declare, to test that it is refused.

`view=` accepts the function or class itself, or a dotted name: full (`shop.views.ProductViewSet`), short (`ProductViewSet`) or one handler (`ProductViewSet.list`, `UserView.put`, `get_item`).

### API

| Name | Signature | Role |
|---|---|---|
| `@rp.setup` / `@rp.teardown` | `fn(ctx)` | run once before / after all probes |
| `@rp.data(target)` | `fn(ctx) -> dict` | replace generated values: `params`, `query`, `json`, `data`, `headers` |
| `rp.override(target, **values)` | | static form of `@rp.data` |
| `@rp.test(target, view=..., **request)` | `fn(res, ctx)` | test bound to routes; `params`, `query`, `json`, `data`, `headers` change the request |
| `@rp.scenario` | `fn(ctx)` | free-form test |
| `rp.exclude(*targets, view=...)` | | keep routes out of the generated probes |

- `res`: `status`, `json` (parsed body or `None`), `text`, `body`, `headers`, `elapsed_ms`, `error` (traceback when the view raised).
- `ctx`: `client` (`get`, `post`, `put`, `patch`, `delete`, `request`, each accepting `query`, `json`, `data` and `headers`), `headers` (sent with every request), `store` (a dict shared between tests), `route` and `method`.

A failed `assert` marks the test as failed and shows the assert line. Any other exception marks it as an error.

Order of execution: setup, your tests and scenarios, then the generated probes (GET first, DELETE last), then teardown.

## Configuration

Instead of repeating options, add to `pyproject.toml`:

```toml
[tool.routeprobe]
app = "app:create_app()"
tests = ["tests/routeprobe_tests.py"]
exclude = ["/admin/*", "POST /auth/logout"]
read_only = false
test_db = true
checks = ["server_error", "invalid_body", "method_not_allowed"]
slow_ms = 500
```

Command-line options take precedence.

## Web panel

`routeprobe ui` opens a page listing every route with its methods. Click a method to load a ready-to-send request (body, query and headers pre-filled, editable), send it and read the response, including the traceback when the view crashes. **Run probes** runs everything and marks each method with a green, orange or red dot. The test file is reloaded at each run, so edit it without restarting the panel. To pick up changes to your application code, restart the panel.

The panel listens on `127.0.0.1` only. It rejects requests whose `Host` or `Origin` is not local, accepts only JSON posts, and serves a strict Content-Security-Policy, so another website open in your browser cannot drive it.

## Safety

- **Django**: probes run against a temporary test database created from your migrations, then destroyed. Pass `--no-test-db` only if you know what you are doing.
- **Flask and FastAPI**: routeprobe cannot switch the database for you. Generated POST, PUT, PATCH and DELETE requests reach the database your app is configured with. Point `--app` to a factory configured for tests (`--app "app:create_app('testing')"`), or use `--read-only`.
- In the panel, the application stays loaded between runs: data created or deleted by one run is still there at the next.

## Use in CI

```yaml
- run: pip install -e . routeprobe
- run: routeprobe run --report routeprobe-report.json
```

Exit codes: `0` everything passed, `1` at least one failure or error, `2` usage or loading error.

## Develop routeprobe

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/python -m pytest
```

`examples/` holds one small application per framework, each with a deliberate bug that routeprobe must find.

## License

MIT
