"""What happens when the bytes on this machine are not the bytes that were recorded.

`tests/test_licence.py` already hashes every committed body on every run, and that is a check on
the REPOSITORY: a hand-edited rate cannot reach main. It does nothing for a checkout, and a
checkout is where the interesting damage happens. An interrupted write, a full disk or a
half-finished sync leaves a body short long after CI has passed.

WHAT THAT USED TO PRODUCE, measured on a copy of the cassette directory before this was fixed:

    intact               2026-07-31 = 1.1485, 23 delivered
    truncated 155 bytes  2026-07-31 = 1.14,   23 delivered, no_such_observation 0
    zero bytes           no observations at all, no error, expected 23

A 74 basis point error served under `"source": "ECB statistics."` beside a coverage certificate
reporting nothing missing. That is worse than a crash: a number that is merely wrong is a number
somebody acts on, and the certificate this repository exists to provide was actively vouching
for it.
"""

from __future__ import annotations

import json
import pathlib
import shutil

import pytest

from quenchz.upstream import CassetteTransport, CorruptedRecording

REPO = pathlib.Path(__file__).resolve().parents[1]
CASSETTES = REPO / "data" / "cassettes"
NAME = "usd-eur-daily-one-month"


@pytest.fixture
def copied(tmp_path: pathlib.Path) -> pathlib.Path:
    """A COPY, never the committed directory.

    A test that corrupts a tracked file to prove a point leaves the tree dirty when it is
    interrupted, and the next person to run `git status` finds a rewritten exchange rate.
    """
    directory = tmp_path / "cassettes"
    shutil.copytree(CASSETTES, directory)
    return directory


def test_the_intact_recording_is_served(copied: pathlib.Path) -> None:
    """A check that refuses everything is not a check, it is an outage."""
    response = CassetteTransport(directory=copied).fetch(NAME)
    assert response.status == 200
    assert len(response.body) > 0


def test_a_truncated_body_is_refused_rather_than_served_as_a_shorter_rate(
    copied: pathlib.Path,
) -> None:
    """The failure the transcript above describes, as a test.

    Truncation is the realistic corruption. It leaves a file that parses, whose last row is a
    number, and whose value is wrong in a way no schema check can see.
    """
    body = copied / f"{NAME}.body"
    original = body.read_bytes()
    body.write_bytes(original[:-155])

    with pytest.raises(CorruptedRecording) as refusal:
        CassetteTransport(directory=copied).fetch(NAME)
    message = str(refusal.value)
    assert f"{len(original) - 155:,} bytes" in message
    assert f"{len(original):,} were recorded" in message
    assert "interrupted write" in message, (
        "the message does not suggest a cause, so a reader's first guess is that somebody edited "
        "the file, which sends them to the wrong place"
    )


def test_an_empty_body_is_refused_rather_than_read_as_an_empty_series(
    copied: pathlib.Path,
) -> None:
    """The quietest one. A zero-byte file used to come back as a window with no rates in it."""
    (copied / f"{NAME}.body").write_bytes(b"")
    with pytest.raises(CorruptedRecording):
        CassetteTransport(directory=copied).fetch(NAME)


def test_an_edit_that_keeps_the_length_is_caught_by_the_digest(copied: pathlib.Path) -> None:
    """Length alone would be a check somebody could walk straight past.

    Changing one rate to another of the same width leaves the byte count identical, which is
    exactly what a deliberate edit looks like.
    """
    body = copied / f"{NAME}.body"
    original = body.read_bytes()
    edited = original.replace(b"1.1485", b"9.9999", 1)
    assert len(edited) == len(original) and edited != original, (
        "this fixture no longer contains the rate this test edits, so it is not testing an "
        "equal-length change any more"
    )
    body.write_bytes(edited)

    with pytest.raises(CorruptedRecording) as refusal:
        CassetteTransport(directory=copied).fetch(NAME)
    assert "The bytes have been changed" in str(refusal.value)


def test_the_length_and_the_digest_are_reported_differently(copied: pathlib.Path) -> None:
    """Two failures that need two different remedies should not read the same.

    A short file means look at the disk. A file of the right length that hashes wrong means look
    at whoever wrote it. Collapsing them into one message costs a reader the diagnosis.
    """
    body = copied / f"{NAME}.body"
    original = body.read_bytes()

    body.write_bytes(original[:-10])
    with pytest.raises(CorruptedRecording) as truncated:
        CassetteTransport(directory=copied).fetch(NAME)

    body.write_bytes(original.replace(b"1.1485", b"9.9999", 1))
    with pytest.raises(CorruptedRecording) as edited:
        CassetteTransport(directory=copied).fetch(NAME)

    assert str(truncated.value) != str(edited.value)
    assert "were recorded" in str(truncated.value)
    assert "hashes to" in str(edited.value)


def test_a_body_is_hashed_once_however_often_it_is_replayed(copied: pathlib.Path) -> None:
    """The full history body is 1.4 MB and the gateway replays it per call.

    Recording which names have been verified is what keeps this check from being the reason
    somebody turns it off. Asserted by corrupting the file AFTER a successful fetch: the second
    fetch is served from the already-verified name, which is the behaviour being claimed.
    """
    transport = CassetteTransport(directory=copied)
    first = transport.fetch(NAME)
    (copied / f"{NAME}.body").write_bytes(b"")
    second = transport.fetch(NAME)
    assert second.body == b"", (
        "the second fetch re-read and re-verified the file, so the hash is being computed on "
        "every replay and this cache does not exist"
    )
    assert len(first.body) > 0


def test_every_committed_recording_passes_its_own_check() -> None:
    """The whole directory, through the real transport, on the real files.

    The tests above prove the check can fail. This one proves the committed corpus passes it,
    which is the half that stops a broken check from being invisible.
    """
    transport = CassetteTransport()
    names = transport.names()
    assert names, "no recordings were found at all"
    for name in names:
        assert transport.fetch(name).body is not None

    index = json.loads((CASSETTES / "index.json").read_text(encoding="utf-8"))
    assert {entry["name"] for entry in index} == set(names)
