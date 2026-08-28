"""The committed demo output and the card's numbers, checked against this repository.

WHY THIS FILE ARRIVED LATE. `scripts/capture_evidence.py` has said since it was written that
"`tests/test_docs.py` fails when what is committed stops matching a live run, so staleness is a
red build rather than a quiet lie on a public page". There was no such file, and nothing in this
suite referenced the captured demo or the card's numbers at all. The sentence described an
enforcement that had never existed, in the script whose whole job is to stop a public page going
stale.

It was found while building a sibling repository from this one's pattern. Both halves are fixed:
the guard is here, and the docstring now names it.

Both artefacts are published. `docs/evidence/demo.txt` is the terminal block on
pnx89.github.io/QUENCHZ and `docs/evidence/facts.json` supplies the figures beside it.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
EVIDENCE = REPO / "docs" / "evidence"
DEMO = EVIDENCE / "demo.txt"
FACTS = EVIDENCE / "facts.json"


def facts() -> dict[str, object]:
    return dict(json.loads(FACTS.read_text(encoding="utf-8")))


def test_the_committed_demo_output_is_what_the_demo_prints_now() -> None:
    """Byte for byte, against a live run.

    The demo replays recorded upstream responses and shares one budget between two callers, so
    it is deterministic and this comparison is exact. Two consecutive runs were compared before
    this test was written, rather than assuming it.
    """
    result = subprocess.run(
        [sys.executable, "examples/two_callers.py"],
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == DEMO.read_text(encoding="utf-8"), (
        "docs/evidence/demo.txt is not what the demo prints. It is published on the Pages card, "
        "so regenerate it deliberately:\n  uv run python scripts/capture_evidence.py"
    )


def test_the_demo_output_is_not_empty_or_an_error() -> None:
    """Separate, because a comparison of two empty files also passes."""
    text = DEMO.read_text(encoding="utf-8")
    assert len(text.splitlines()) > 20, "the captured demo is too short to be the real thing"
    assert "Traceback" not in text, "the captured demo output contains a traceback"


def test_the_card_states_the_python_test_total() -> None:
    """Collected in a subprocess rather than counted.

    The card's figure is the PYTHON total. The TypeScript checks are a separate proof in a
    separate language, and folding them into one number would make the card claim a suite that
    does not exist in either place.
    """
    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "-o", "addopts=", "--collect-only", "-q"],
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=300,
    )
    total = re.search(r"^(\d+) tests? collected", collected.stdout, re.M)
    assert total, f"pytest reported no collection total:\n{collected.stdout[-400:]}"
    assert facts()["tests"] == int(total.group(1)), (
        f"the card says {facts()['tests']} tests and pytest collects {total.group(1)}. "
        f"Regenerate with scripts/capture_evidence.py"
    )


def test_the_card_claims_only_the_python_versions_ci_tests() -> None:
    """Read from the matrix, so the card cannot advertise support nothing runs."""
    workflow = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    versions = sorted({v for v in re.findall(r'"(3\.\d+)"', workflow)}, key=lambda v: int(v[2:]))
    assert versions, "no Python versions in the CI matrix"
    assert facts()["python"] == f"{versions[0]} to {versions[-1]}"


def test_the_card_names_the_version_this_package_declares() -> None:
    """A card naming a release that does not match the package is a card about another build."""
    from quenchz import __version__

    assert facts()["release"] == f"v{__version__}"


def test_every_fact_the_card_needs_is_present_and_the_count_is_asserted() -> None:
    """Five keys, counted, so a sixth cannot arrive unchecked by the tests above."""
    recorded = facts()
    assert set(recorded) == {"tests", "python", "release", "captured", "runUrl"}
    assert isinstance(recorded["tests"], int) and recorded["tests"] > 0
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(recorded["captured"])), recorded["captured"]
