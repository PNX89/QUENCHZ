"""The dependency line, asserted rather than remembered.

The `mcp` SDK depends on `httpx2`. If anything in this tree ever imports plain `httpx`,
the repository is shipping two HTTP client stacks for one job: two connection pools, two
timeout vocabularies and two places to configure a proxy. That is the kind of thing that
is decided once, forgotten, and then discovered by whoever debugs a hung request.

This test is here from the first commit so the answer never has to be reconstructed.
"""

from __future__ import annotations

import pathlib
import re
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_httpx2_is_the_client_the_sdk_actually_asks_for() -> None:
    import httpx2  # noqa: F401
    import mcp  # noqa: F401


def test_nothing_declares_a_dependency_on_plain_httpx() -> None:
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text())
    names = declared["project"]["dependencies"] + declared["dependency-groups"]["dev"]
    offenders = [n for n in names if n.split("[")[0].split(">")[0].split("=")[0].strip() == "httpx"]
    assert offenders == [], f"plain httpx is declared alongside httpx2: {offenders}"


def test_every_third_party_module_this_tree_imports_is_declared() -> None:
    """Declared and imported must be the same set, in both directions.

    FastAPI was declared here and imported nowhere, left over from a specification written
    before it turned out the MCP SDK builds the ASGI application itself. A dependency nobody
    imports is a claim in the one file a reviewer reads to find out what a project is built on.
    And `httpx2` was the reverse: imported directly while arriving only as somebody else's
    transitive dependency, which is how a tree ends up pinned to a version it never chose.
    """
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text())
    runtime = {
        entry.split("[")[0].split(">")[0].split("=")[0].split(";")[0].strip().lower()
        for entry in declared["project"]["dependencies"]
    }
    dev = {
        entry.split("[")[0].split(">")[0].split("=")[0].split(";")[0].strip().lower()
        for entry in declared["dependency-groups"]["dev"]
    }

    stdlib = set(sys.stdlib_module_names)
    imported: set[str] = set()
    for path in list((ROOT / "src").rglob("*.py")) + list((ROOT / "scripts").glob("*.py")):
        for line in path.read_text().splitlines():
            match = re.match(r"^\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)", line)
            if match:
                imported.add(match.group(1).lower())
    imported -= stdlib | {"quenchz", "__future__"}

    # pyjwt is imported as `jwt`, and cryptography arrives through its `[crypto]` extra.
    aliases = {"jwt": "pyjwt", "cryptography": "pyjwt"}
    imported = {aliases.get(name, name) for name in imported}

    undeclared = sorted(imported - runtime - dev)
    assert undeclared == [], f"imported and not declared: {undeclared}"

    unused = sorted(runtime - imported)
    assert unused == [], (
        f"declared and imported nowhere: {unused}. Either use it or remove it; a dependency "
        f"nobody imports is a claim in the file a reviewer reads to learn what this is built on."
    )
