# routeprobe

[English](README.md) · **Français**

Testez chaque route d'une API **Django (DRF)**, **Flask** ou **FastAPI** pendant que vous développez, sans écrire de test d'abord.

```
$ routeprobe run
routeprobe · fastapi · main:app · 4 routes

  PASS  custom pen_exists                                    200     5.5 ms
  PASS  GET /items                                           200     0.9 ms
  FAIL  GET /stats                                           500     0.9 ms  server error 500: ZeroDivisionError: float division by zero
  PASS  POST /items                                          201     1.1 ms
  PASS  POST /items · corps vide                             422     0.9 ms
  PASS  PATCH /items · méthode non déclarée                    405     0.7 ms

Échecs

  GET /stats
      Retraces (frames du projet):
        File ".../main.py", line 50, in stats
          return {"average": total / len([i for i in ITEMS.values() if i["price"] > 100])}
      ZeroDivisionError: float division à virgule par zéro
```

## Installation

Installez routeprobe **dans l'environnement virtuel du projet à tester** : il importe votre application, il lui faut donc les mêmes paquets. C'est pour cela que `pipx install` ou `uv tool install` ne conviennent pas.

routeprobe n'a aucune dépendance propre : il utilise le framework déjà installé dans votre projet. Python 3.10+.

### Projet géré avec pip

```bash
source .venv/bin/activate
pip install git+https://github.com/g0sh5ukuna/api_tester.git
routeprobe run
```

### Projet géré avec uv

Un environnement créé par `uv venv` ne contient pas `pip`. Taper `pip` appelle alors le pip du système, que Debian ou Ubuntu refusent avec `error: externally-managed-environment`. Passez par uv :

```bash
# Gardé dans le projet : ajouté aux dépendances de dev du pyproject.toml, survit à `uv sync`
uv add --dev "routeprobe @ git+https://github.com/g0sh5ukuna/api_tester.git"
uv run routeprobe run

# Essai rapide, sans modifier pyproject.toml : retiré au prochain `uv sync`
uv pip install git+https://github.com/g0sh5ukuna/api_tester.git
uv run routeprobe run
```

### Autres cas

```bash
pip install -e /path/to/api_tester                                    # clone local, pour travailler sur routeprobe
pip install "routeprobe[fastapi] @ git+https://github.com/g0sh5ukuna/api_tester.git"   # ajoute httpx si le TestClient de FastAPI le réclame
```

N'utilisez jamais `--break-system-packages` : il installe dans le Python du système, hors de votre projet.

## Utilisation

Exécutez les commandes depuis la racine de votre projet.

```bash
routeprobe routes                 # liste des routes, méthodes et vue gérant chacune
routeprobe run                    # probes générés + vos tests ; code de sortie 1 en cas d'échec
routeprobe ui                     # panneau web local sur http://127.0.0.1:8765
routeprobe init                   # crée un modèle routeprobe_tests.py
```

L'application est détectée automatiquement :

| Framework | Détection | Explicite |
|---|---|---|
| Django | `manage.py` dans le dossier courant | `--app mysite.settings` |
| FastAPI / Flask | essaie `main:app`, `app:app`, `app.main:app`, `src.main:app`, `api:app`, `wsgi:app`, `application:app` | `--app module:app` ou `--app "module:create_app()"` |

Options utiles de `run` :

| Option | Effet |
|---|---|
| `--read-only` | n'envoie jamais de POST, PUT, PATCH ou DELETE |
| `--only PATTERN` | conserve les routes dont le chemin ou la vue correspond (`/api/*`, `ItemViewSet`) |
| `--exclude PATTERN` | skip routes (répétable) |
| `--no-auto` / `--no-custom` | exécute uniquement vos tests / uniquement les probes générés |
| `--checks server_error,slow` | choisis les vérifications générées |
| `--report report.json` | écrit un rapport JSON (pour CI) |
| `--no-test-db` | Django : utilise la base de données configurée au lieu d'une temporaire |
| `--include-admin` | Django : aussi tester `/admin/` |
| `-v` | affiche les tracebacks en ligne |

## Vérifications générées

Pour chaque route et chaque méthode que sa vue déclare :

| Vérifie | Envoie | Échec quand |
|---|---|---|
| `server_error` | une requête remplie avec des valeurs d'échantillon | la réponse est 5xx ou la vue lève |
| `slow` | rien de plus : il chronomètre la requête de `server_error` | la réponse dépasse `--slow-ms` (avertissement, pas un échec) |
| `invalid_body` | `{}` à une méthode dont le corps a des champs requis | la vue plante (5xx) ou l'accepte (2xx) |
| `method_not_allowed` | une méthode que la route ne déclare pas | la vue plante ou l'accepte |

`--slow-ms` vaut 1000 par défaut. Chaque résultat porte le nom de sa vérification (`check` dans le rapport JSON, une étiquette dans le panneau).

D'où proviennent les méthodes et les corps :

- **FastAPI** : routes, paramètres de chemin et de query, et corps JSON depuis le schéma OpenAPI.
- **Django** : modèles `path()` et `re_path()`, incluant `include()` et les routers DRF. Méthodes issues des vues de classe, des `ViewSet` DRF, de `@api_view` et `@require_http_methods` ; corps JSON issus du `serializer_class` DRF. Une vue fonctionnelle plain sans décodeur de méthode est supposée gérer GET.
- **Flask** : règles, convertisseurs (`<int:id>`, `<uuid:...>`, `<any(a,b):...>`) et `MethodView`. Flask n'a aucun schéma de corps : fournissez des corps réels avec `@rp.data`.

## Vos propres tests

Créez `routeprobe_tests.py` à la racine du projet (`routeprobe init`), ou passez `--tests FILE`.

```python
import routeprobe as rp


@rp.setup
def login(ctx):
    """S'exécute une fois avant tout. Les en-têtes définis ici sont envoyés avec chaque requête."""
    res = ctx.client.post("/api/token/", json={"username": "demo", "password": "demo"})
    ctx.headers["Authorization"] = f"Bearer {res.json['access']}


@rp.data("POST /api/products/")
def product(ctx):
    """Valeurs réelles pour une route, utilisées par les probes générés et par vos tests."""
    return {"json": {"name": "Pen", "price": "2.00"}}


@rp.data("DELETE /api/products/{pk}/")
def disposable_product(ctx):
    created = ctx.client.post("/api/products/", json={"name": "Temp", "price": "1.00"})
    return {"params": {"pk": created.json["id"]}}


@rp.test("GET /api/products/{pk}/", params={"pk": 1})
def product_has_price(res, ctx):
    """Liée à une route : routeprobe envoie la requête, vous vérifiez la réponse."""
    assert res.status == 200, res.text
    assert "price" in res.json


@rp.test(view="shop.views.ProductViewSet")
def viewset_never_crashes(res, ctx):
    """Liée à une vue : s'exécute une fois par méthode de la vue (list, create, retrieve...)."""
    assert res.status < 500


@rp.test(view="ProductViewSet.destroy", headers={"Authorization": ""})
def delete_requires_auth(res, ctx):
    assert res.status in (401, 403)


@rp.scenario
def create_then_read(ctx):
    """Test libre envoyant ses propres requêtes."""
    created = ctx.client.post("/api/products/", json={"name": "Ruler", "price": "1.00"})
    assert created.status == 201, created.text
    ctx.store["product_id"] = created.json["id"]
    assert ctx.client.get(f"/api/products/{created.json['id']}/").status == 200


rp.exclude("POST /api/logout/", "/api/internal/*")
```

### Sélection de routes

Le premier argument de `@rp.test`, `@rp.data`, `rp.override` et `rp.exclude` accepte :

- `"GET /items/{id}"` : méthode et modèle de route ;
- `"/items/{id}"` : chaque méthode que la route déclare ;
- `"GET /items/42"` : un chemin concret, correspondance avec les modèles ;
- `"/api/*"` : un motif glob ;
- `"PATCH /items"` : une méthode que la route ne déclare pas, pour tester qu'elle est refusée.

`view=` accepte la fonction ou la classe elle-même, ou un nom en notation pointée : complet (`shop.views.ProductViewSet`), court (`ProductViewSet`) ou un gestionnaire (`ProductViewSet.list`, `UserView.put`, `get_item`).

## API

| Nom | Signature | Rôle |
|---|---|---|
| `@rp.setup` / `@rp.teardown` | `fn(ctx)` | s'exécute une fois avant / après tous les probes |
| `@rp.data(target)` | `fn(ctx) -> dict` | remplace les valeurs générées : `params`, `query`, `json`, `data`, `headers` |
| `rp.override(target, **values)` | | forme statique de `@rp.data` |
| `@rp.test(target, view=..., **request)` | `fn(res, ctx)` | test lié à des routes ; `params`, `query`, `json`, `data`, `headers` changent la requête |
| `@rp.scenario` | `fn(ctx)` | test libre |
| `rp.exclude(*targets, view=...)` | | conserve les routes en dehors des probes générés |

- `res` : `status`, `json` (corps parsed ou `None`), `text`, `body`, `headers`, `elapsed_ms`, `error` (traceback quand la vue a levé).
- `ctx` : `client` (`get`, `post`, `put`, `patch`, `delete`, `request`, chacune acceptant `query`, `json`, `data` et `headers`), `headers` (envoyés avec chaque requête), `store` (un dictionnaire partagé entre tests), `route` et `method`.

L'échec d'un `assert` marque le test comme échoué et montre la ligne d'assertion. Toute autre exception le marque comme erreur.

Ordre d'exécution : setup, vos tests et scenarios, puis les probes générés (GET en premier, DELETE en dernier), puis teardown.

## Configuration

Au lieu de répéter des options, ajoutez à `pyproject.toml` :

```toml
[tool.routeprobe]
app = "app:create_app()"
tests = ["tests/routeprobe_tests.py"]
exclude = ["/admin/*", "POST /auth/logout"]
read_only = false
test_db = true
checks = ["server_error", "slow", "invalid_body", "method_not_allowed"]
slow_ms = 500
```

Les options de ligne de commande prennent le dessus.

## Panneau web

`routeprobe ui` ouvre une page listant chaque route avec ses méthodes. Cliquez sur une méthode pour charger une requête prête à être envoyée (corps, query et en-têtes pré-remplis, modifiables), envoyez-la et lisez la réponse, incluant le traceback si la vue plante. **Exécuter les probes** exécute tout et marque chaque méthode avec un point vert, orange ou rouge. Le fichier de test est rechargé à chaque exécution, donc modifiez-le sans redémarrer le panneau. Pour récupérer les modifications de votre code application, redémarrez le panneau.

L'écouteur du panneau est sur `127.0.0.1` uniquement. Il refuse les requêtes dont l'hôte ou l'origine n'est pas local, accepte uniquement des posts JSON, et sert un Content-Security-Policy strict, de sorte qu'un autre site ouvert dans votre navigateur ne peut pas le piloter.

## Sécurité

- **Django** : les probes s'exécutent contre une base de données de test temporelle créée à partir de vos migrations, puis détruite. Passez `--no-test-db` uniquement si vous savez ce que vous faites.
- **Flask et FastAPI** : routeprobe ne peut pas changer la base de données pour vous. Les requêtes POST, PUT, PATCH et DELETE générées atteignent la base de données configurée avec votre application. Pointez `--app` vers une usine configurée pour les tests (`--app "app:create_app('testing')"`), ou utilisez `--read-only`.

Dans le panneau, l'application reste chargée entre les exécutions : des données créées ou supprimées par une exécution sont encore présentes au suivant.

## Utilisation en CI

```yaml
- run: pip install -e . routeprobe
- run: routeprobe run --report routeprobe-report.json
```

Codes de sortie : `0` tout a passé, `1` au moins un échec ou erreur, `2` erreur d'utilisation ou de chargement.

## Développer routeprobe

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/python -m pytest
```

`examples/` contient une petite application par framework, chacune avec un bug intentionnel que routeprobe doit trouver.

## License

MIT