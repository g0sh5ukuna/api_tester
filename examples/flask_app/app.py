"""Example Flask API. Bug on purpose: PUT /users/<id> reads a missing key from the body."""

from flask import Flask, abort, jsonify, request
from flask.views import MethodView


def create_app():
    app = Flask(__name__)
    users = {1: {"id": 1, "name": "Ada"}}

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.route("/users", methods=["GET", "POST"])
    def users_collection():
        if request.method == "POST":
            body = request.get_json(silent=True) or {}
            if "name" not in body:
                abort(400)
            user = {"id": max(users) + 1, "name": body["name"]}
            users[user["id"]] = user
            return user, 201
        return jsonify(list(users.values()))

    class UserView(MethodView):
        def get(self, user_id):
            return users.get(user_id) or abort(404)

        def put(self, user_id):
            body = request.get_json(silent=True) or {}
            users[user_id]["name"] = body["name"]  # KeyError when "name" is missing or user unknown
            return users[user_id]

    app.add_url_rule("/users/<int:user_id>", view_func=UserView.as_view("user"))
    return app
