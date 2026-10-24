"""T-13: the web process has no outbound client code. It never calls the internet,
the AI provider or a mail server; those live in the worker and the fetcher."""

import ast
from pathlib import Path

WEB_PACKAGE = Path(__file__).resolve().parents[2] / "src" / "learning_tracker" / "web"

BANNED = {
    "aiohttp",
    "anthropic",
    "http.client",
    "httpcore",
    "httpx",
    "httpx2",
    "requests",
    "smtplib",
    "socket",
    "urllib.request",
    "urllib3",
}


def banned_imports(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        # Dynamic imports could load any client by a computed name, so web has none.
        if isinstance(node, ast.Call) and _is_dynamic_import(node.func):
            found.append("dynamic import")
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]
        for name in names:
            if any(name == b or name.startswith(b + ".") for b in BANNED):
                found.append(name)
    return found


def _is_dynamic_import(func: ast.expr) -> bool:
    if isinstance(func, ast.Name):
        return func.id in {"__import__", "import_module"}
    return isinstance(func, ast.Attribute) and func.attr == "import_module"


def test_web_package_imports_no_outbound_client() -> None:
    sources = sorted(WEB_PACKAGE.rglob("*.py"))
    assert sources  # precondition: the package exists and has code

    offenders = {str(p): hits for p in sources if (hits := banned_imports(p.read_text()))}

    assert offenders == {}


def test_detector_fires_on_each_import_form() -> None:
    assert banned_imports("import httpx") == ["httpx"]
    assert banned_imports("import urllib.request") == ["urllib.request"]
    assert "urllib.request" in banned_imports("from urllib import request")
    assert "http.client" in banned_imports("from http import client")
    assert banned_imports("from anthropic import Anthropic") == ["anthropic", "anthropic.Anthropic"]
    assert banned_imports("import json, pathlib") == []


def test_detector_fires_on_dynamic_imports() -> None:
    assert banned_imports("import importlib\nimportlib.import_module('httpx')") == [
        "dynamic import"
    ]
    assert banned_imports("from importlib import import_module\nimport_module(name)") == [
        "dynamic import"
    ]
    assert banned_imports("__import__('socket')") == ["dynamic import"]
