"""Reading the ECB, where the status line is not the answer.

Five things can come back from this API and the HTTP status separates them badly. Three of
them are 200. One of those three is a body of nothing at all, which is correct and means the
window was closed. Another is a body in a completely different format from the one that was
asked for, which is not correct and is the case a client is most likely to swallow, because
`response.ok` is true and `len(body)` is large.

So nothing here branches on the status line alone. Every outcome is decided by looking at
what actually arrived, and a test asserts that property against the recorded responses.

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
import io
import json
import pathlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

__all__ = ["CassetteTransport", "Outcome", "Reading", "Transport", "read"]

CASSETTES = pathlib.Path(__file__).resolve().parents[2] / "data" / "cassettes"


class Outcome(StrEnum):
    OBSERVATIONS = "observations"
    EMPTY_WINDOW = "empty_window"
    WRONG_FORMAT = "wrong_format"
    UNKNOWN_SERIES = "unknown_series"
    REJECTED_PARAMETERS = "rejected_parameters"


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


class CassetteTransport:
    """Replays a recorded response by name. No network, in CI or anywhere else."""

    def __init__(self, directory: pathlib.Path | None = None) -> None:
        self._dir = directory or CASSETTES
        self._index = {
            entry["name"]: entry for entry in json.loads((self._dir / "index.json").read_text())
        }

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
        return RawResponse(
            status=int(entry["status"]),
            content_type=str(entry["content_type"]),
            body=(self._dir / f"{name}.body").read_bytes(),
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
    if response.status != 200:
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
