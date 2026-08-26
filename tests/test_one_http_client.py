"""The dependency line, asserted rather than remembered.

The `mcp` SDK depends on `httpx2`. If anything in this tree ever imports plain `httpx`,
the repository is shipping two HTTP client stacks for one job: two connection pools, two
timeout vocabularies and two places to configure a proxy. That is the kind of thing that
is decided once, forgotten, and then discovered by whoever debugs a hung request.

This test is here from the first commit so the answer never has to be reconstructed.
"""

from __future__ import annotations

import pathlib
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
