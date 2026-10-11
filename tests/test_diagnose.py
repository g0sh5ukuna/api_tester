import json

from routeprobe.diagnose import explain_assert, field_errors, hint
from routeprobe.models import Param, Request, Response, Route


class Res:
    status = 404
    json = {"name": "Pen", "items": [1, 2]}


def failure(fn):
    try:
        fn()
    except AssertionError as exc:
        return exc
    raise AssertionError("expected a failure")


def test_compare_shows_actual_value():
    res = Res()
    exc = failure(lambda: _assert_status(res))
    assert explain_assert(exc) == "assert res.status == 200 · res.status = 404"


def _assert_status(res):
    assert res.status == 200


def test_subscript_and_multiline():
    res = Res()

    def check():
        assert (
            res.json["name"]
            == "Stylo"
        )

    assert explain_assert(failure(check)) == "assert res.json[\"name\"] == \"Stylo\" · res.json[\"name\"] = 'Pen'"


def test_calls_are_not_evaluated_but_their_arguments_are():
    res = Res()
    calls = []

    def count(value):
        calls.append(value)
        return len(value)

    def check():
        assert count(res.json["items"]) > 5

    text = explain_assert(failure(check))
    assert text == "assert count(res.json[\"items\"]) > 5 · res.json[\"items\"] = [1, 2]"
    assert len(calls) == 1  # only the original call ran


def _response(status, body):
    return Response(status, {}, json.dumps(body).encode(), 1.0)


def test_field_errors_fastapi_and_drf():
    fastapi = _response(422, {"detail": [{"loc": ["body", "price"], "msg": "Input should be greater than 0"}]})
    assert field_errors(fastapi) == ["price: Input should be greater than 0"]
    drf = _response(400, {"title": ["This field is required."], "non_field_errors": ["Bad pair."]})
    assert field_errors(drf) == ["title: This field is required.", "non_field_errors: Bad pair."]


ROUTE = Route("/items/{pk}/", ["GET", "POST"], "shop.views.ItemViewSet", "django", params=[Param("pk", "path")])


def test_hints():
    get = Request("GET", "/items/1/")
    assert "@rp.setup" in hint(ROUTE, get, _response(401, {"detail": "no"}))
    missing = hint(ROUTE, get, _response(404, {"detail": "Not found."}))
    assert "(pk=1)" in missing and '@rp.data("GET /items/{pk}/")' in missing
    post = Request("POST", "/items/1/")
    assert "title: This field is required." in hint(ROUTE, post, _response(400, {"title": ["This field is required."]}))
    assert "none was sent" in hint(ROUTE, post, _response(400, "plain"))
    assert hint(ROUTE, get, _response(200, {})) == ""
