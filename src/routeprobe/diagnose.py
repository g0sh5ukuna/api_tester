"""Turn a failure into a precise message: values behind a failed assert, rejected fields, how to fix."""

from __future__ import annotations

import ast
import inspect
import re
import textwrap
from typing import Any

from .models import Request, Response, Route

_MAX_REPR = 160
# Nodes that can run arbitrary code or change state: never evaluated again.
_UNSAFE = (ast.Call, ast.NamedExpr, ast.Lambda, ast.Await, ast.Yield, ast.YieldFrom,
           ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


# -- failed asserts -------------------------------------------------------------------

def explain_assert(exc: AssertionError) -> str:
    """`assert res.status == 200` -> `assert res.status == 200 · res.status = 404`.

    Re-evaluates the sub-expressions of the failed assert in the frame where it failed.
    Expressions containing a call are left out: evaluating them again could change state.
    """
    tb = exc.__traceback__
    while tb and tb.tb_next:
        tb = tb.tb_next
    if tb is None:
        return ""
    frame = tb.tb_frame
    try:
        lines, first = inspect.getsourcelines(frame.f_code)
    except (OSError, TypeError):
        return ""
    source = textwrap.dedent("".join(lines))
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ""
    lineno = tb.tb_lineno - max(first, 1) + 1
    node = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.Assert) and n.lineno <= lineno <= (n.end_lineno or n.lineno)),
        None,
    )
    if node is None:
        return ""
    statement = "assert " + _one_line(ast.get_source_segment(source, node.test) or "")
    values = []
    for expr in _interesting(node.test):
        text = _one_line(ast.get_source_segment(source, expr) or "")
        if not text or text in (v[0] for v in values):
            continue
        try:
            value = eval(compile(ast.Expression(expr), "<assert>", "eval"), frame.f_globals, frame.f_locals)
        except Exception as err:
            value = f"<{type(err).__name__}: {err}>"
            values.append((text, value))
            continue
        values.append((text, _short_repr(value)))
    if not values:
        return statement
    return statement + " · " + ", ".join(f"{text} = {value}" for text, value in values)


def _interesting(test: ast.expr) -> list[ast.expr]:
    """Operands worth showing: what is compared, not the literals."""
    if isinstance(test, ast.Compare):
        candidates = [test.left, *test.comparators]
    elif isinstance(test, ast.BoolOp):
        return [e for v in test.values for e in _interesting(v)]
    elif isinstance(test, ast.UnaryOp):
        return _interesting(test.operand)
    else:
        candidates = [test]
    return [part for c in candidates for part in _safe_parts(c)]


def _safe_parts(node: ast.expr) -> list[ast.expr]:
    """`node` if it can be evaluated again, else its safe operands: len(res.json) -> res.json."""
    if isinstance(node, ast.Constant):
        return []
    if _safe(node):
        return [node]
    if isinstance(node, ast.Call):
        parts = [*node.args, *(k.value for k in node.keywords)]
        if isinstance(node.func, ast.Attribute):
            parts.insert(0, node.func.value)  # res.json.get("x") -> res.json
        return [p for part in parts for p in _safe_parts(part)]
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
        # Only the iterables: the other names are bound inside the comprehension.
        return [p for gen in node.generators for p in _safe_parts(gen.iter)]
    if isinstance(node, (ast.Lambda, ast.NamedExpr, ast.Await, ast.Yield, ast.YieldFrom)):
        return []
    return [p for child in ast.iter_child_nodes(node) if isinstance(child, ast.expr) for p in _safe_parts(child)]


def _safe(node: ast.AST) -> bool:
    return not any(isinstance(n, _UNSAFE) for n in ast.walk(node))


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _short_repr(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= _MAX_REPR else text[: _MAX_REPR - 1] + "…"


# -- rejected requests ----------------------------------------------------------------

_LOGIN_URL = re.compile(r"log-?in|sign-?in|/accounts/|/auth/|connexion", re.IGNORECASE)
LOGIN_HINT = ("redirected to the login page: create a user and log in once in @rp.setup "
              "(post to the login URL with ctx.client; the session cookie is kept)")


def is_login_redirect(response: Response) -> bool:
    """3xx towards a login page (@login_required, LoginRequiredMixin...): the view did not run."""
    if not 300 <= response.status < 400:
        return False
    location = next((v for k, v in response.headers.items() if k.lower() == "location"), "")
    return bool(_LOGIN_URL.search(location))


def field_errors(response: Response) -> list[str]:
    """Validation errors from a FastAPI (422) or DRF (400) body: ["price: must be > 0", ...]."""
    body = response.json
    errors: list[str] = []
    if isinstance(body, dict) and isinstance(body.get("detail"), list):
        # FastAPI / pydantic: {"detail": [{"loc": ["body", "price"], "msg": "..."}]}
        for item in body["detail"]:
            if isinstance(item, dict):
                loc = [str(p) for p in item.get("loc", []) if p not in ("body",)]
                errors.append(f"{'.'.join(loc) or 'body'}: {item.get('msg', item)}")
    elif isinstance(body, dict) and isinstance(body.get("detail"), str):
        errors.append(body["detail"])
    elif isinstance(body, dict):
        # DRF: {"price": ["A valid number is required."], "non_field_errors": [...]}
        for name, messages in body.items():
            if isinstance(messages, list) and all(isinstance(m, str) for m in messages):
                errors.append(f"{name}: {' '.join(messages)}")
            elif isinstance(messages, str):
                errors.append(f"{name}: {messages}")
    elif isinstance(body, list) and all(isinstance(m, str) for m in body):
        errors.extend(body)
    return errors


def hint(route: Route | None, request: Request | None, response: Response | None) -> str:
    """How to make a 4xx response go away, or why it is expected."""
    if response is None or route is None or request is None:
        return ""
    status = response.status
    target = f'"{request.method} {route.path}"'
    if is_login_redirect(response):
        return LOGIN_HINT
    if status in (401, 403):
        return ("authentication or permission required: log in once with @rp.setup "
                "and set ctx.headers (Authorization, cookie...)")
    if status == 404:
        if route.path_params:
            values = ", ".join(f'"{p.name}": …' for p in route.path_params)
            return (f"no object behind the generated parameters {_params(request, route)}: "
                    f"give real ones with @rp.data({target}) returning {{\"params\": {{{values}}}}}")
        return "the view answered 404 for this exact URL: check the URL prefix or what the view looks up"
    if status in (400, 422):
        errors = field_errors(response)
        if errors:
            return (f"request data rejected ({'; '.join(errors[:4])}): "
                    f"give valid values with @rp.data({target})")
        if request.method in ("POST", "PUT", "PATCH") and route.method_body_unknown(request.method):
            return (f"the framework does not describe this body, so none was sent: "
                    f"give one with @rp.data({target}) returning {{\"json\": {{...}}}}")
        return f"request rejected without field details: give valid values with @rp.data({target})"
    if status == 405:
        return "the method is declared but refused: check the methods the view really accepts"
    if status == 415:
        return f"unsupported content type: the view may expect form data, use @rp.data({target}) returning {{\"data\": {{...}}}}"
    if status == 429:
        return "rate limited: disable throttling in the test settings"
    return ""


def _params(request: Request, route: Route) -> str:
    # Recover the generated values from the filled path, for the message.
    actual = request.path.strip("/").split("/")
    template = route.path.strip("/").split("/")
    pairs = [f"{t[1:-1]}={a}" for t, a in zip(template, actual) if t.startswith("{") and t.endswith("}")]
    return "(" + ", ".join(pairs) + ")" if pairs else ""
