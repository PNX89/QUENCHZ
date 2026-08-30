"""Run the server for a client written in another language, and hand it the tokens.

Everything this repository claims about audience and about fairness is proved twice: once by
the Python suite against the objects directly, and once from outside the Python tree entirely,
by a TypeScript client on the official MCP SDK talking to this process over HTTP. The second
proof is the one that means anything to somebody who does not trust the first, because it
shares no code with the thing it is testing.

THE CLOCK IS FROZEN ON PURPOSE. The budget is given a `ManualClock` that nobody advances, so
the arithmetic is exact rather than approximately right: with a capacity of 60, half of it
reserved and two callers, each reserve is 15 and the spare is 30. The first caller can take its
own 15 and all 30 of the spare and not one request more, and the second caller can then take
its 15 whatever the first one did. A wall clock would refill mid-run and turn a proof into an
observation.

THE TOKENS ARE WRITTEN TO A FILE GIVEN ON THE COMMAND LINE AND NEVER TO THE REPOSITORY. They
are minted by an issuer whose key exists only in this process's memory, they expire in minutes,
and they are meaningless the moment it exits.

    uv run python -m quenchz.interop_server --port 8931 --tokens-file /tmp/quenchz-tokens.json

Source: ECB statistics.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import uvicorn

from quenchz.budget import ManualClock
from quenchz.issuer import RESOURCE, SOMEBODY_ELSE, Issuer
from quenchz.server import CALLERS, build_app

CAPACITY = 60
RESERVE_FRACTION = 0.5


def token_manifest(issuer: Issuer, url: str) -> dict[str, object]:
    """What the client needs, plus the parameters it must derive its expectations from.

    The expected admission counts are deliberately NOT included. The client is given the
    capacity, the reserve fraction and the caller list and has to work out 45 and 15 for
    itself, so that agreeing with this server is evidence rather than obedience.
    """
    alpha, beta = CALLERS[0], CALLERS[1]
    return {
        "url": url,
        "callers": {"greedy": alpha, "quiet": beta},
        "budget": {
            "capacity": CAPACITY,
            "reserveFraction": RESERVE_FRACTION,
            "callerCount": len(CALLERS),
            "clock": "frozen",
        },
        "tokens": {
            "greedyForThisResource": issuer.mint(client_id=alpha, scopes=["rates:read"]),
            "quietForThisResource": issuer.mint(client_id=beta, scopes=["rates:read"]),
            "forAnotherResource": issuer.mint(
                client_id=alpha, scopes=["rates:read"], audience=SOMEBODY_ELSE
            ),
            "forTwoResources": issuer.mint(
                client_id=alpha, scopes=["rates:read"], audience=[RESOURCE, SOMEBODY_ELSE]
            ),
            "withNoAudience": issuer.mint(
                client_id=alpha, scopes=["rates:read"], include_audience=False
            ),
            "withoutTheRatesScope": issuer.mint(client_id=alpha, scopes=["something:else"]),
        },
        "source": "ECB statistics.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8931)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--tokens-file", required=True, type=pathlib.Path)
    args = parser.parse_args(argv)

    if args.tokens_file.resolve().is_relative_to(pathlib.Path(__file__).resolve().parents[2]):
        # A token written inside the working tree is a token one `git add -A` away from being
        # published. It expires in minutes and its issuer dies with this process, and it still
        # has no business being here.
        raise SystemExit(f"refusing to write tokens inside the repository: {args.tokens_file}")

    issuer = Issuer()
    app = build_app(
        issuer,
        allowed_hosts=[args.host, f"{args.host}:*"],
        clock=ManualClock(),
    )

    manifest = token_manifest(issuer, f"http://{args.host}:{args.port}/mcp")
    args.tokens_file.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"tokens written to {args.tokens_file}", file=sys.stderr, flush=True)

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
