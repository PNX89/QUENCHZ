"""The three ESCB reuse conditions, as checks rather than as a paragraph.

The ECB permits this repository's use of its statistics on three conditions, and `data/README.md`
says all three are tests here. That sentence was true of one of them. This file makes it true of
all three, because a licence condition that lives only in prose is a promise, and the difference
between a promise and a check is the whole argument this portfolio makes about README claims.

  1. THE SOURCE IS QUOTED.  "Source: ECB statistics." wherever a number is exposed.
  2. THE STATISTICS AND METADATA ARE NOT MODIFIED.  The strongest form available: every committed
     body is byte-identical to what arrived, checked against the SHA-256 the capture recorded.
     Until this existed the hash was dead metadata, written once and compared to nothing, so a
     hand-edited exchange rate would have been served as the vendor's own figure.
  3. NO THIRD-PARTY DATA.  Only EXR, which is the ECB's own compilation.
"""

from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
CASSETTES = REPO / "data" / "cassettes"
ATTRIBUTION = "Source: ECB statistics."


def index() -> list[dict[str, object]]:
    return list(json.loads((CASSETTES / "index.json").read_text()))


@pytest.mark.parametrize("entry", index(), ids=lambda e: str(e["name"]))
def test_every_committed_body_is_byte_identical_to_what_the_vendor_sent(
    entry: dict[str, object],
) -> None:
    """Condition two, and the one that was not enforced at all.

    A recorded response is only the unmodified statistic while nobody has touched it. The
    capture writes a SHA-256 for exactly this comparison and, until this test, nothing ever
    made it. Editing one digit of one rate would have passed every check in the repository.
    """
    body = (CASSETTES / f"{entry['name']}.body").read_bytes()
    assert hashlib.sha256(body).hexdigest() == entry["sha256"], (
        f"{entry['name']} no longer matches the hash recorded when it was captured. Either it "
        f"was edited, in which case it is no longer the vendor's statistic and may not be "
        f"redistributed, or it was recaptured without updating index.json."
    )
    assert len(body) == entry["bytes"], f"{entry['name']} byte count disagrees with the index"


def test_the_index_records_a_hash_for_every_body_and_a_body_for_every_hash() -> None:
    """A hash with no file, or a file with no hash, is an unchecked file."""
    named = {str(entry["name"]) for entry in index()}
    on_disk = {path.stem for path in CASSETTES.glob("*.body")}
    assert named == on_disk, f"index and disk disagree: {named ^ on_disk}"


def test_the_source_is_quoted_wherever_the_data_is_described() -> None:
    """Condition one. The licence fixes the wording, so the wording is asserted."""
    assert ATTRIBUTION in (CASSETTES / "../README.md").resolve().read_text()
    assert any(ATTRIBUTION in str(entry.get("source", "")) for entry in index())


def test_only_the_ecbs_own_compilation_is_committed() -> None:
    """Condition three. The right of free reuse does not extend to third-party data.

    Enforced by looking at what the recorded URLs actually asked for, rather than by trusting
    that a future capture will stay on EXR.
    """
    off_dataflow = [
        (entry["name"], entry["url"])
        for entry in index()
        if "/service/data/EXR/" not in str(entry["url"])
    ]
    assert off_dataflow == [], (
        f"these recordings are outside the EXR dataflow: {off_dataflow}. The ESCB reuse policy "
        f"states that the right of free reuse does not apply to third-party data."
    )


def test_the_capture_script_cannot_quietly_wander_off_the_dataflow() -> None:
    """The same condition, at the point where a new recording would be added."""
    source = (REPO / "scripts" / "capture.py").read_text()
    assert 'BASE = "https://data-api.ecb.europa.eu/service/data/EXR"' in source
    urls = [line for line in source.splitlines() if "https://" in line and "BASE" not in line]
    stray = [u for u in urls if "data-api.ecb.europa.eu" in u]
    assert stray == [], f"a URL bypassing BASE was added to the capture script: {stray}"
