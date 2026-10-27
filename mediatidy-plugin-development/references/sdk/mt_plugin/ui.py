"""Python constructors for MT UI v2; composition is ordinary Python."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any


def ref(path: str) -> dict:
    return {"$ref": path}


def expr(operation: str, *args: Any) -> dict:
    return {"$op": operation, "args": list(args)}


def page(*children: dict, **props: Any) -> dict:
    return {"version": "2", "content": list(children), **props}


def component(name: str, *children: dict, **props: Any) -> dict:
    node: dict = {"component": name, "props": props}
    if children:
        node["children"] = list(children)
    return node


def _constructor(name: str) -> Any:
    return lambda *children, **props: component(name, *children, **props)


_registry = json.loads(Path(__file__).with_name("ui-registry.json").read_text("utf-8"))
for _name in _registry["components"]:
    globals()[_name] = _constructor(_name)


def __getattr__(name: str) -> Any:
    if name in _registry["components"]:
        return globals()[name]
    raise AttributeError(name)
