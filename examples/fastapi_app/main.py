"""Example FastAPI API. Bug on purpose: GET /stats divides by zero when there is no item."""

from uuid import UUID

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI()
ITEMS: dict[int, dict] = {1: {"id": 1, "name": "Pen", "price": 2.0}}


class ItemIn(BaseModel):
    name: str = Field(min_length=1)
    price: float = Field(gt=0)
    tags: list[str] = []


@app.get("/items")
def list_items(limit: int = 10):
    return list(ITEMS.values())[:limit]


@app.post("/items", status_code=201)
def create_item(item: ItemIn):
    item_id = max(ITEMS, default=0) + 1
    ITEMS[item_id] = {"id": item_id, **item.model_dump()}
    return ITEMS[item_id]


@app.get("/items/{item_id}")
def get_item(item_id: int):
    if item_id not in ITEMS:
        raise HTTPException(404, "item not found")
    return ITEMS[item_id]


@app.delete("/items/{item_id}", status_code=204)
def delete_item(item_id: int):
    ITEMS.pop(item_id, None)


@app.get("/orders/{order_id}")
def get_order(order_id: UUID, verbose: bool):
    return {"id": str(order_id), "verbose": verbose}


@app.get("/stats")
def stats():
    total = sum(i["price"] for i in ITEMS.values())
    return {"average": total / len([i for i in ITEMS.values() if i["price"] > 100])}
