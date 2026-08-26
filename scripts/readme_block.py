"""Generate the response body the README opens on, so it is a run rather than a transcription.

The README's first screenful is presented as a real answer from this server. That is a claim,
and `test_readme.py` compares the fenced block against what this script produces now. A block
that was true when it was pasted and is false today is worse than no block, because it reads as
evidence right up until somebody runs it.

    uv run python scripts/readme_block.py

Source: ECB statistics.
"""

from __future__ import annotations

import datetime
import json
import sys

from quenchz.budget import FairBudget, ManualClock
from quenchz.gateway import Gateway
from quenchz.server import _toolset
from quenchz.upstream import CassetteTransport

# Frozen so the block is reproducible. A wall clock would put a different `body_completed_at`
# in the README on every run and the comparison would never hold twice.
WHEN = datetime.datetime(2026, 8, 26, 12, 0, tzinfo=datetime.UTC)
CASSETTE = "usd-eur-daily-easter-2026"
FROM, TO = datetime.date(2026, 3, 30), datetime.date(2026, 4, 10)


def block() -> str:
    gateway = Gateway(
        _toolset(),
        FairBudget(
            capacity=60, refill_per_second=60, callers=("agent-alpha",), clock=ManualClock()
        ),
        CassetteTransport(),
    )
    answer = gateway.rates_window(CASSETTE, FROM, TO, WHEN)

    # The observations are printed one pair per line rather than at the indent json.dumps
    # would give them. Eight observations become thirty-two lines under the default and push
    # the coverage block, which is the point of the whole example, off the first screenful.
    pairs = ",\n".join(f"    [{json.dumps(day)}, {value}]" for day, value in answer["observations"])
    rest = json.dumps({k: v for k, v in answer.items() if k != "observations"}, indent=2)
    return "{\n" + f'  "observations": [\n{pairs}\n  ],\n' + rest[2:]


def main() -> int:
    sys.stdout.write(block() + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
