"""Route registration calls made inside a registrar function.

``get``/``post`` are framework registrars: the calls fail resolution (the
registrar lives in the framework), and the file imports a known framework —
the pass links the registrar function to the framework's import node.
"""

from flask import Flask


def health():
    return "ok"


def ready():
    return "ready"


def register(app):
    get("/health")(health)
    post("/ready")(ready)
    return app
