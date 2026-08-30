"""Reading the ECB, where the status line is not the answer.

Five things can come back from this API and the HTTP status separates them badly. Three of
them are 200. One of those three is a body of nothing at all, which is correct and means the
window was closed. Another is a body in a completely different format from the one that was
asked for, which is not correct and is the case a client is most likely to swallow, because
`response.ok` is true and `len(body)` is large.

So NO 200 IS TRUSTED ON ITS STATUS LINE. The three outcomes that share it are told apart by
what the body contains, and a test asserts that against the recorded responses.

The wider claim used to be made here, that nothing branches on the status alone, and it was not
true: 404, 400 and 5xx are decided before the body is looked at, which is correct. A body sent
alongside a failure is not evidence about anything, and the same CSV under four statuses gives
four different outcomes on purpose. The test named as proof of the wider claim only ever
exercised the 200 family, so it could not have seen the gap.

    200 + CSV with rows      observations
    200 + zero bytes         an empty window, and this is a normal answer
    200 + XML                the vendor ignored the format that was asked for
    404 + JSON               no such series
    400 + HTML               the parameters were rejected, and note the body is not JSON

Source: ECB statistics.
"""

from __future__ import annotations

import csv
import datetime
import hashlib
import io
import json
import pathlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

__all__ = [
    "CassetteTransport",
    "CorruptedRecording",
    "Outcome",
    "Reading",
    "Transport",
    "read",
]

CASSETTES = pathlib.Path(__file__).resolve().parents[2] / "data" / "cassettes"


class Outcome(StrEnum):
    OBSERVATIONS = "observations"
    EMPTY_WINDOW = "empty_window"
    WRONG_FORMAT = "wrong_format"
    UNKNOWN_SERIES = "unknown_series"
    REJECTED_PARAMETERS = "rejected_parameters"
    #: THE OUTCOME THAT WAS MISSING, and its absence had a cost. Every status that was not 200,
    #: 400 or 404 fell through to REJECTED_PARAMETERS, so a caller was told its parameters were
    #: wrong when the vendor was down. The vendor's own documented status table lists 500, 501
    #: and 503, so this is not a hypothetical: it is a documented case classified as the
    #: caller's fault, which sends whoever is on call to read the request instead of the status
    #: page.
    VENDOR_UNAVAILABLE = "vendor_unavailable"
    #: 304 fell through to REJECTED_PARAMETERS as well, on a comment that argued 304 was "the
    #: caller's side of the line" using a reason that only covers 4xx. A 304 answers a
    #: conditional request, and this client sends none anywhere in this tree: no
    #: `If-None-Match`, no `If-Modified-Since`. So this is undetected rather than reachable
    #: today, and named here rather than left inside REJECTED_PARAMETERS so that the day it is
    #: reached, whether from a future conditional request or a misbehaving proxy, it is not
    #: reported to a caller as its own mistake.
    NOT_MODIFIED = "not_modified"


@dataclass(frozen=True, slots=True)
class RawResponse:
    """What a transport hands back. Bytes, not a parsed object, on purpose."""

    status: int
    content_type: str
    body: bytes


@dataclass(frozen=True, slots=True)
class Reading:
    """One classified response.

    `placeholders` are dates that arrived as a row with no value, which is how this vendor
    said "closed" until May 2012. They are reported separately rather than folded into
    `observations` or silently dropped, because a caller that counts rows and a caller that
    counts values get different answers and both deserve to know which they have.
    """

    outcome: Outcome
    observations: dict[datetime.date, float]
    placeholders: frozenset[datetime.date]
    detail: str


class Transport(Protocol):
    def fetch(self, name: str) -> RawResponse: ...


class CorruptedRecording(Exception):
    """A body on disk that is not the body that was recorded.

    Its own class, rather than a ValueError, because the caller's choices differ: a rejected
    parameter is the caller's problem and this is the machine's. Nothing here can recover from
    it, and the only honest response is to stop.
    """


class CassetteTransport:
    """Replays a recorded response by name. No network, in CI or anywhere else.

    THE HASH IS CHECKED HERE AND NOT ONLY IN THE SUITE, and the distinction is the whole point
    of this class. `tests/test_licence.py` hashes every committed body on every run, so a
    hand-edited rate cannot reach main: that protects the REPOSITORY. It does nothing for a
    checkout, where an interrupted write, a full disk or a half-finished sync leaves a body
    truncated after CI has already passed.

    Measured on a copy of the cassette directory with one body cut 155 bytes short: the last
    rate came back as 1.14 instead of 1.1485, a 74 basis point error, served under
    `"source": "ECB statistics."` with a coverage certificate reporting nothing missing. A
    zero-byte body came back as an empty series with no error at all. Both are worse than a
    crash, because a number that is merely wrong is a number somebody will act on.
    """

    def __init__(self, directory: pathlib.Path | None = None) -> None:
        self._dir = directory or CASSETTES
        index = self._dir / "index.json"
        if not index.exists():
            # THE DEFAULT IS RIGHT FOR A CHECKOUT AND IMPOSSIBLE FROM A WHEEL, so the error says
            # so rather than surfacing as a FileNotFoundError three frames down inside
            # build_server. CASSETTES is `parents[2] / "data" / "cassettes"`, which is the
            # repository root when this package is imported from src and is
            # `<prefix>/lib/python3.x/data/cassettes` when it is imported from site-packages.
            # The recordings are vendor bytes kept for a licence argument, not package data, so
            # the wheel does not carry them and tests/test_licence.py holds that decision.
            raise FileNotFoundError(
                f"no cassette index at {index}. These recordings live in the repository rather "
                f"than in the wheel, so an installed copy has to be given a directory: "
                f"CassetteTransport(directory=...)"
            )
        self._index = {entry["name"]: entry for entry in json.loads(index.read_text())}
        # THE VERIFIED BYTES, NOT THE VERIFIED NAME, and the difference is what the check above
        # is worth after the first call. Remembering the name still re-read the file every time
        # and skipped only the digest, so a body that went bad while the process was up was
        # served unverified for the life of that process. That is not a hypothetical shape: a
        # half-finished sync completes while something is running, and what is running here is a
        # long-lived uvicorn. Holding the bytes hashes the 1.4 MB full history once, serves every
        # later replay from memory, and drops the per-call read as well.
        self._verified: dict[str, bytes] = {}

    def names(self) -> list[str]:
        return sorted(self._index)

    def fetch(self, name: str) -> RawResponse:
        try:
            entry = self._index[name]
        except KeyError:
            # A generator that quietly returns nothing for an unknown input is a generator
            # that hides a typo until somebody debugs an empty result set.
            raise KeyError(
                f"no recording named {name!r}; recorded: {', '.join(sorted(self._index))}"
            ) from None
        body = self._verified.get(name)
        if body is None:
            body = (self._dir / f"{name}.body").read_bytes()
            self._check(name, entry, body)
            self._verified[name] = body
        return RawResponse(
            status=int(entry["status"]),
            content_type=str(entry["content_type"]),
            body=body,
        )

    def _check(self, name: str, entry: dict[str, object], body: bytes) -> None:
        """Length first, then the digest.

        The length is not a redundant cheap version of the hash. It is what makes the failure
        message useful: "1,479,269 bytes where 1,479,424 were recorded" tells a reader the file
        was truncated, and a digest mismatch alone would send them looking for an edit.
        """
        recorded_bytes = int(entry["bytes"])  # type: ignore[call-overload]
        if len(body) != recorded_bytes:
            raise CorruptedRecording(
                f"{name}.body holds {len(body):,} bytes and {recorded_bytes:,} were recorded. "
                f"This is a local file, so the likely cause is an interrupted write or an "
                f"unfinished sync rather than an edit"
            )
        digest = hashlib.sha256(body).hexdigest()
        if digest != entry["sha256"]:
            raise CorruptedRecording(
                f"{name}.body is the right length and hashes to {digest[:16]}, where "
                f"{str(entry['sha256'])[:16]} was recorded. The bytes have been changed"
            )


def _looks_like_sdmx_xml(body: bytes) -> bool:
    return body.lstrip()[:5] == b"<?xml"


def read(response: RawResponse) -> Reading:
    """Classify a response by what it contains, never by its status alone."""
    body = response.body

    empty: frozenset[datetime.date] = frozenset()

    if response.status == 404:
        return Reading(Outcome.UNKNOWN_SERIES, {}, empty, _reason_from_json(body))
    if response.status == 400:
        # The body here is HTML, not JSON, which is worth saying out loud because a client
        # written against the 404 shape will throw a decode error on this one.
        return Reading(Outcome.REJECTED_PARAMETERS, {}, empty, "the vendor rejected the parameters")
    if response.status >= 500:
        return Reading(
            Outcome.VENDOR_UNAVAILABLE,
            {},
            empty,
            f"the vendor answered {response.status}, which is its problem and not the request's",
        )
    if response.status == 304:
        # Documented by the vendor and structurally unreachable by this client: a 304 answers a
        # conditional request, and nothing in this tree sends one. Named on its own rather than
        # folded into the arm below, because that arm's justification is "a 4xx is a statement
        # about the request", which is true of 406 and false of a 3xx.
        return Reading(
            Outcome.NOT_MODIFIED,
            {},
            empty,
            "the vendor reported no change to a request that was never conditional",
        )
    if response.status != 200:
        # 406 and anything else the vendor documents. Still the caller's side of the line, since
        # a 4xx is a statement about the request, and named as unexpected rather than as
        # "rejected parameters" because this branch does not know which parameter.
        return Reading(
            Outcome.REJECTED_PARAMETERS, {}, empty, f"unexpected status {response.status}"
        )

    # Everything below here is an HTTP 200, and they are not the same thing at all.
    if not body.strip():
        return Reading(Outcome.EMPTY_WINDOW, {}, empty, "no observation falls inside the window")
    if _looks_like_sdmx_xml(body):
        return Reading(
            Outcome.WRONG_FORMAT,
            {},
            empty,
            f"asked for csvdata and received XML at status 200, {len(body)} bytes",
        )

    rows = list(csv.DictReader(io.StringIO(body.decode("utf-8"))))
    if not rows or "TIME_PERIOD" not in (rows[0] or {}):
        return Reading(Outcome.WRONG_FORMAT, {}, empty, "a 200 whose body is not the expected CSV")

    observations: dict[datetime.date, float] = {}
    placeholders: set[datetime.date] = set()
    for row in rows:
        day = datetime.date.fromisoformat(row["TIME_PERIOD"])
        value = row.get("OBS_VALUE")
        if value:
            observations[day] = float(value)
        else:
            placeholders.add(day)

    detail = f"{len(observations)} observations"
    if placeholders:
        detail += f", and {len(placeholders)} rows carrying no value at all"
    return Reading(Outcome.OBSERVATIONS, observations, frozenset(placeholders), detail)


def _reason_from_json(body: bytes) -> str:
    try:
        parsed = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return "no such series"
    detail = parsed.get("detail") if isinstance(parsed, dict) else None
    return str(detail) if detail else "no such series"
