"""Locate the application under test and wrap it in the adapter of its framework."""

from __future__ import annotations

import importlib
import os
import sys

from .base import Adapter

# Tried in order when no --app is given and the project is not a Django project.
CANDIDATES = ("main:app", "app:app", "app.main:app", "src.main:app", "api:app", "wsgi:app", "application:app")


class LoadError(Exception):
    pass


def _framework_of(obj) -> str | None:
    roots = {cls.__module__.split(".")[0] for cls in type(obj).__mro__}
    if "fastapi" in roots or "starlette" in roots:
        return "fastapi"
    if "flask" in roots:
        return "flask"
    return None


def _import_object(spec: str):
    module_name, _, attr = spec.partition(":")
    module = importlib.import_module(module_name)
    call = attr.endswith("()")
    obj = module
    for part in attr.removesuffix("()").split("."):
        obj = getattr(obj, part)
    return obj() if call else obj


def _is_settings_module(name: str) -> bool:
    try:
        module = importlib.import_module(name)
    except Exception:
        return False
    return hasattr(module, "INSTALLED_APPS") or hasattr(module, "ROOT_URLCONF")


def load_adapter(
    app: str | None = None,
    framework: str | None = None,
    *,
    test_db: bool = True,
    include_admin: bool = False,
    cwd: str = ".",
) -> Adapter:
    """Build the adapter for `app` ("module:attr", "module:factory()" or a Django settings module)."""
    cwd = os.path.abspath(cwd)
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    from .django import find_settings_module

    settings = None
    if framework == "django":
        settings = app or os.environ.get("DJANGO_SETTINGS_MODULE") or find_settings_module(cwd)
    elif app is None and framework is None:
        settings = find_settings_module(cwd)
    elif app and ":" not in app and _is_settings_module(app):
        settings = app
    if settings:
        from .django import DjangoAdapter

        return DjangoAdapter(settings, settings, test_db=test_db, include_admin=include_admin)
    if framework == "django":
        raise LoadError("Django settings not found: pass --app mysite.settings or run from the folder of manage.py.")

    specs = [app] if app else list(CANDIDATES)
    errors = []
    for spec in specs:
        if ":" not in spec:
            errors.append(f"{spec}: expected 'module:attribute', for example main:app")
            continue
        try:
            obj = _import_object(spec)
        except ModuleNotFoundError as exc:
            # A missing candidate module is expected; a missing dependency inside the app is not.
            if app is None and exc.name == spec.partition(":")[0]:
                continue
            errors.append(f"{spec}: {exc}")
            continue
        except Exception as exc:
            errors.append(f"{spec}: {type(exc).__name__}: {exc}")
            continue
        kind = framework or _framework_of(obj)
        if kind == "fastapi":
            from .fastapi import FastAPIAdapter

            return FastAPIAdapter(obj, spec)
        if kind == "flask":
            from .flask import FlaskAdapter

            return FlaskAdapter(obj, spec)
        errors.append(f"{spec}: {type(obj).__name__} is not a Flask or FastAPI application")

    hint = "\n  ".join(errors) if errors else "no application found in " + ", ".join(CANDIDATES)
    raise LoadError(
        "Cannot load the application.\n  " + hint + "\nUse --app module:app (Flask/FastAPI) or --app mysite.settings (Django)."
    )


__all__ = ["Adapter", "LoadError", "load_adapter"]
