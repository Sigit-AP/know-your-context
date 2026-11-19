"""Run query/codeintel with a minimal pydantic stub to estimate lower‑bound RSS.

The stub provides just the names used by the contracts/domain/events models:
- BaseModel (stores kwargs as attrs, provides model_dump & model_dump_json)
- ConfigDict (simple dict subclass) – ignored at runtime
- Field (identity function returning the default value)
- model_validator (no‑op decorator)
- BeforeValidator, field_validator – no‑op decorators
- pydantic_core – empty module
"""

from __future__ import annotations

import json
import sys
from types import ModuleType
from pathlib import Path

# ----- minimal pydantic stub -------------------------------------------------
class BaseModel:
    def __init__(self, **data):
        for k, v in data.items():
            setattr(self, k, v)
    def model_dump(self, *_, **__):
        return {k: getattr(self, k) for k in self.__dict__}
    def model_dump_json(self, *_, **__):
        return json.dumps(self.model_dump())
    @classmethod
    def model_validate(cls, obj):  # used by pydantic v2 validation pipelines
        return cls(**obj)

def ConfigDict(**kwargs):  # simply returns the dict of config options
    return kwargs

def Field(*_, **kwargs):  # returns the default value when called as a descriptor
    return kwargs.get("default") if "default" in kwargs else None

def model_validator(*_, **__):
    def deco(fn):
        return fn
    return deco

def BeforeValidator(fn):  # no‑op decorator
    return fn

def field_validator(*_, **__):  # no‑op decorator returning identity
    def deco(fn):
        return fn
    return deco

# Create a stub pydantic module with the symbols above
pyd = ModuleType("pydantic")
for name in ["BaseModel", "ConfigDict", "Field", "model_validator", "BeforeValidator", "field_validator"]:
    setattr(pyd, name, globals()[name])
# expose submodules that the real library would have (they can be empty)
submods = ["_internal", "errors", "types", "json_schema", "config"]
for sub in submods:
    setattr(pyd, sub, ModuleType(f"pydantic.{sub}"))

# stub pydantic_core (empty module)
pyd_core = ModuleType("pydantic_core")

# inject into sys.modules before any import of the real library
sys.modules.setdefault("pydantic", pyd)
sys.modules.setdefault("pydantic_core", pyd_core)

# ----- run the CLI ----------------------------------------------------------
SRC = Path("/root/know-your-context/src")
sys.path.insert(0, str(SRC))
from rss import rss_mib, peak_rss_mib
from know_your_context.cli import main

mode = sys.argv[1] if len(sys.argv) > 1 else "query"
if mode == "query":
    argv = ["--store", "/root/know-your-context/know-your-context.sqlite3",
            "--output-root", "/root/know-your-context/know-your-context-projection",
            "query", '{"purpose": "What evidence labels exist for claims?", "token_budget": 512, "depth": 1}']
elif mode == "codeintel":
    argv = ["--store", "/root/know-your-context/know-your-context.sqlite3",
            "codeintel", "callers-of", "QueryRequest"]
else:
    raise SystemExit("unknown mode")

exit_code = main(argv)
print(f"EXIT={exit_code} PEAK_RSS={peak_rss_mib():.2f} MiB", file=sys.stderr)
