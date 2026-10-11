import routeprobe as rp


@rp.data("POST /items")
def new_item(ctx):
    return {"json": {"name": "Notebook", "price": 4.5, "tags": ["paper"]}}


@rp.data("DELETE /items/{item_id}")
def disposable_item(ctx):
    # Delete an item created for the occasion, never the Pen the other tests rely on.
    created = ctx.client.post("/items", json={"name": "Temp", "price": 1})
    return {"params": {"item_id": created.json["id"]}}


@rp.test("GET /items/{item_id}", params={"item_id": 1})
def pen_exists(res, ctx):
    assert res.status == 200
    assert res.json["name"] == "Pen"


@rp.test(view="main.get_item", params={"item_id": 999})
def unknown_item_is_404(res, ctx):
    assert res.status == 404


@rp.scenario
def create_then_read(ctx):
    created = ctx.client.post("/items", json={"name": "Ruler", "price": 1})
    assert created.status == 201, created.text
    item_id = created.json["id"]
    assert ctx.client.get(f"/items/{item_id}").json["name"] == "Ruler"
