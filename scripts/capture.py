"""Record ECB responses to disk, exactly as they arrived.

This never runs in CI. It runs on the build machine, the bytes are committed, and every
required job replays them. A required job that calls a third party is a job that turns red
for reasons that have nothing to do with the code.

WHY THE BYTES ARE STORED RAW. The ESCB reuse policy permits this on the condition that "the
statistics (including metadata) are not modified". A recorded response body IS the
unmodified statistic, so storing the bytes verbatim is not merely convenient, it is the
strongest available form of compliance. It also rules out the tempting shortcut of parsing
the CSV into something tidier and committing that instead, which would be a modification.

Source: ECB statistics.

    uv run python scripts/capture.py
"""

from __future__ import annotations

import datetime
import hashlib
import json
import pathlib
import sys

import httpx2

BASE = "https://data-api.ecb.europa.eu/service/data/EXR"
HERE = pathlib.Path(__file__).resolve().parents[1] / "data" / "cassettes"

# Each entry is one recorded exchange. The names are what the replay asks for, so they
# describe the QUESTION rather than the parameters.
RECORDINGS: dict[str, str] = {
    "usd-eur-daily-full-history": f"{BASE}/D.USD.EUR.SP00.A?format=csvdata",
    "usd-eur-daily-easter-2026": (
        f"{BASE}/D.USD.EUR.SP00.A?format=csvdata&startPeriod=2026-03-30&endPeriod=2026-04-10"
    ),
    "usd-eur-daily-one-weekend": (
        f"{BASE}/D.USD.EUR.SP00.A?format=csvdata&startPeriod=2026-08-22&endPeriod=2026-08-23"
    ),
    "usd-eur-daily-one-month": (
        f"{BASE}/D.USD.EUR.SP00.A?format=csvdata&startPeriod=2026-07-01&endPeriod=2026-07-31"
    ),
    "unknown-series-key": f"{BASE}/D.ZZZ.EUR.SP00.A?format=csvdata",
    # Narrowed to one month deliberately. The pathology is that a 200 carries SDMX XML
    # when CSV was asked for, and one month shows that as well as twenty-seven years do.
    # The response is recorded whole; narrowing the REQUEST is honest, truncating the
    # RESPONSE would modify the statistic and the licence forbids it.
    "format-that-does-not-exist": (
        f"{BASE}/D.USD.EUR.SP00.A?format=nonsense&startPeriod=2026-07-01&endPeriod=2026-07-31"
    ),
}


def record(name: str, url: str) -> dict[str, object]:
    with httpx2.Client(timeout=60.0, follow_redirects=True) as client:
        response = client.get(url)
    body = response.content
    (HERE / f"{name}.body").write_bytes(body)
    return {
        "name": name,
        "url": url,
        "status": response.status_code,
        "content_type": response.headers.get("content-type", ""),
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        # Every header the response carried, so the claim that no rate-limit header exists
        # is a claim a reader can check rather than one they have to believe.
        "response_headers": dict(response.headers),
        "captured_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "source": "Source: ECB statistics.",
    }


def main() -> int:
    HERE.mkdir(parents=True, exist_ok=True)
    index = [record(name, url) for name, url in RECORDINGS.items()]
    (HERE / "index.json").write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")
    for entry in index:
        print(f"  {entry['name']:<34} {entry['status']}  {entry['bytes']:>9} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
