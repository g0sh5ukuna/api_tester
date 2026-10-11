from routeprobe.models import Route
from routeprobe.registry import Target


def route(path, methods, view="shop.views.ItemViewSet", handlers=None):
    return Route(path, methods, view, "django", handlers=handlers or {})


ITEMS = route("/items/", ["GET", "POST"], handlers={"GET": "shop.views.ItemViewSet.list", "POST": "shop.views.ItemViewSet.create"})
ITEM = route("/items/{pk}/", ["GET", "DELETE"], handlers={"GET": "shop.views.ItemViewSet.retrieve", "DELETE": "shop.views.ItemViewSet.destroy"})
NEW = route("/items/new/", ["GET"], view="shop.views.new_item", handlers={"GET": "shop.views.new_item"})
ROUTES = [ITEMS, ITEM, NEW]


def selected(target):
    return [(r.path, m, p) for r, m, p in target.select(ROUTES)]


def test_method_and_template():
    assert selected(Target.parse("GET /items/{pk}/")) == [("/items/{pk}/", "GET", "/items/{pk}/")]


def test_path_without_method_selects_every_declared_method():
    assert selected(Target.parse("/items/")) == [("/items/", "GET", "/items/"), ("/items/", "POST", "/items/")]


def test_concrete_path():
    assert selected(Target.parse("DELETE /items/42/")) == [("/items/{pk}/", "DELETE", "/items/42/")]


def test_literal_route_wins_over_template():
    assert selected(Target.parse("GET /items/new/")) == [("/items/new/", "GET", "/items/new/")]


def test_glob():
    assert len(selected(Target.parse("/items/*"))) == 5


def test_view_by_class_short_name_and_action():
    assert len(selected(Target.parse(view="ItemViewSet"))) == 4
    assert selected(Target.parse(view="ItemViewSet.destroy")) == [("/items/{pk}/", "DELETE", "/items/{pk}/")]
    assert selected(Target.parse(view="shop.views.new_item")) == [("/items/new/", "GET", "/items/new/")]


def test_view_does_not_match_partial_segment():
    assert selected(Target.parse(view="Item")) == []


def test_explicit_undeclared_method_is_kept():
    assert selected(Target.parse("PATCH /items/")) == [("/items/", "PATCH", "/items/")]
