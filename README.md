# QUENCHZ

**A stdio MCP server gets four things free from having one caller that shares its context. Once
the transport is remote, each becomes a decision, and every one of them is taken here in the
open and proved from outside the Python tree.**

[![CI](https://github.com/PNX89/QUENCHZ/actions/workflows/ci.yml/badge.svg)](https://github.com/PNX89/QUENCHZ/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org/)
[![TypeScript client](https://img.shields.io/badge/client-TypeScript-3178c6)](clients/typescript)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Data: ECB](https://img.shields.io/badge/data-ECB%20Data%20Portal-003399)](https://data.ecb.europa.eu)

Serving MCP tools to callers you did not write. The four are **audience**, **reach**, **budget**
and **coverage**, and each is falsifiable by a test a reviewer can watch fail.

```json
{
  "observations": [
    ["2026-03-30", 1.1484],
    ["2026-03-31", 1.1498],
    ["2026-04-01", 1.1605],
    ["2026-04-02", 1.1525],
    ["2026-04-07", 1.1557],
    ["2026-04-08", 1.1706],
    ["2026-04-09", 1.1685],
    ["2026-04-10", 1.1711]
  ],
  "coverage": {
    "requested_from": "2026-03-30",
    "requested_to": "2026-04-10",
    "requested_calendar_days": 12,
    "expected_observations": 8,
    "delivered_observations": 8,
    "delivered_from": "2026-03-30",
    "delivered_to": "2026-04-10",
    "absent": {
      "target_closed": 4,
      "not_yet_published": 0,
      "no_such_observation": 0,
      "before_the_series": 0
    },
    "window_still_open": false,
    "body_completed_at": "2026-08-26T12:00:00Z",
    "source": "ECB statistics."
  },
  "source": "ECB statistics."
}
```

Twelve calendar days were asked for and eight came back, and **the response is complete**. Good
Friday, Easter Monday and the weekend are closures rather than gaps, and the vendor's payload
says nothing whatever about any of them. Telling the difference needs the publication calendar
and the wall clock, and that is what the `coverage` block is: [`src/quenchz/coverage.py`].

It does not model everything. It reconstructs three named causes of absence and a fourth for
dates before the series existed, and a fifth nobody has thought of would be counted as a genuine
gap. The certificate is a required field with no default, so a tool that returns observations
cannot answer without one.

**Source: ECB statistics.**

## Twelve days asked for, eight delivered

The certificate above is not a summary written beside the data. It is a required field on the
return, reconstructed from the publication calendar rather than read out of the response, because
the response does not contain it.

| field | what it answers |
|---|---|
| `requested_calendar_days` | what the caller asked for |
| `expected_observations` | what the calendar says was ever due |
| `delivered_observations` | what arrived |
| `absent.target_closed` | the market was shut |
| `absent.not_yet_published` | it is due today and is not out yet |
| `absent.no_such_observation` | it was due, it is late, and somebody should know |
| `absent.before_the_series` | nothing was ever due, because the series had not started |

The last one was added after a review asked for a window in 1990. The euro did not exist in 1990,
and the certificate answered `expected_observations: 261` with all 261 counted as genuine gaps,
which is the worst available answer: a confident report of 261 missing rates on days when none
was owed.

## A valid token that reaches nothing

A token minted by the trusted issuer, correctly signed, unexpired, carrying the right scope, and
made out to a different resource, reaches nothing here.

That check has to be written by hand. The SDK carries an RFC 8707 `resource` field on
`AccessToken` and never compares it to anything: `BearerAuthBackend` checks the bearer prefix,
truthiness and expiry, then passes `resource` through untouched. The SDK does tell the *issuer*
to audience-restrict the token it mints, and says nothing at all about the resource server
checking that it was. Two tests assert the SDK still behaves that way, so if a release starts
enforcing it this claim breaks loudly instead of quietly becoming untrue.

**A token whose audience is a list is refused too, and PyJWT would accept it.** RFC 7519 treats
audience as a membership test, so a token made out to this resource *and* another passes the
library. It does not pass here: a token that is also valid somewhere else is a token that
somewhere else can present at this door, which is the situation RFC 8707 exists to avoid.

A caller cannot learn what it may not use, either. A tool outside its granted scope is absent
from its listing, and asking for it anyway returns bytes identical to asking for a name that
never existed. That costs something real: somebody who genuinely mistyped a scope gets a less
helpful message than they could have had. It is worth it because the SDK's own scope denial
answers `Required scope: <name>`, which confirms the tool exists and names the grant that would
have worked, and a hundred guesses against that is a map of the surface drawn by the server for
a caller that was never allowed to see it.

## Start the server

```
uv sync --dev
uv run pytest
```

The server, with an in-process issuer that mints its own tokens into a file:

```
uv run python -m quenchz.interop_server --port 8931 --tokens-file tokens.json
```

The signing key is generated in memory and never touches disk. Every hostname uses the reserved
`.invalid` TLD from RFC 2606 and can never resolve.

## Two callers and one budget neither can see

The vendor documents no rate limit on any page, sends no rate-limit header on any response, and
does not list `429` among its status codes. So a reactive limiter here is not imperfect, it is
impossible: neither conventional signal exists, and you cannot back off from something you
cannot observe. The budget is therefore chosen in advance and is **self-imposed**. It does not
match anything the vendor enforces and no claim is made that it does.

The naive design is kept in the source so the difference is a measurement rather than an
assertion. Same arrival pattern, both designs:

| design | greedy admitted | quiet then admitted |
|---|---|---|
| one shared bucket | 60 | 0 of 60 |
| a reserve each, plus a shared spare | 45 | 15 of 60, exactly its reserve |

A refused call is charged too. If refusals were free, the care taken over making them identical
would be wasted: a caller would not need to read them at all, it would ask a hundred names and
watch which ones moved its own admitted rate.

## Proving it from outside Python

[`clients/typescript/src/prove.ts`] drives the running server with the official MCP TypeScript
SDK, in its own CI job. It shares no code with the thing it tests, which matters: the Python
suite and the Python server can agree with each other about something that is not true of the
protocol.

Sixteen checks. A token for this resource is admitted with 200; one for another resource, for two
resources, or for none is refused with 401 and a Bearer challenge. An ungranted tool and a
nonexistent one refuse with identical bytes. The bursting caller stops where the arithmetic says
it should and the quiet one still gets exactly its reserve.

That proof had a defect of its own worth recording. Its first version asked whether `connect()`
threw and treated any throw as a refusal, so when a deliberately broken build made the verifier
raise and the server answer 500, the proof reported success. A server answering 500 to everything
would have satisfied every audience check on the page. It reads the status now.

## What the vendor writes down and the payload does not

There are three times here and only two of them are visible to a client.

| time | what it is | where it lives |
|---|---|---|
| around 14:10 CET | the concertation between central banks | the vendor's prose |
| 14:15 CET | the moment the rate *refers to* | **in the payload**, as `TITLE_COMPL` |
| around 16:00 CET | when the rate is actually **published** | **the vendor's prose only** |

A client that reads the payload's own metadata and concludes the rate is available at 14:15 is
wrong by nearly two hours, and will call a not-yet-published day a real gap every afternoon. The
payload does not merely omit the constraint. It carries a different time that looks
authoritative and is the wrong one to use.

The absence encoding changed too, and that was found by writing a client that counted rows and
getting a number 62 larger than the one that counted values. The full series is **7,140 rows**
carrying **7,078 values** and **62 placeholders**: until May 2012 a closing day arrived as a row
with an empty value and `OBS_STATUS` of `H`, and after it, as no row at all. Same calendar, two
encodings, and only counting values is right in both eras.

## The four decisions, and what each one cost

**Audience is the gate, not the signature.** Cost: a token that is valid elsewhere is refused
here, so a caller holding a broadly scoped token has to go back to its issuer for a narrower one.

**Scope is denied at dispatch, never by the transport.** Cost: a legitimate caller with a
mistyped scope gets a refusal that tells it nothing.

**Each caller has a reserve nobody else can spend.** Cost: a lone caller gains nothing from it,
and a burst can no longer use the whole budget when the other caller happens to be idle.

**The certificate is required and has no default.** Cost: every tool that returns observations
has to compute one, including the ones where the answer is that nothing was missing.

## Limitations

- The issuer is local and in-process. **No claim is made about any real identity provider**, and
  nothing here has been near one.
- The budget is self-imposed. It does not correspond to any limit the vendor publishes, because
  the vendor publishes none.
- The certificate reconstructs four named causes. A fifth it has not thought of is counted as a
  genuine gap, which is the safe direction but is not the same as being complete.
- ECB reference rates are a daily published reference, **not a tradeable price**, and nothing
  here treats them as one. There is no order book, no matching engine and no execution path.
- The cassettes are dated. They prove what the vendor returned on the day they were recorded and
  nothing about what it returns today.
- Nothing here is endorsed by or affiliated with the ECB.

## Development

```
uv run pytest
uv run python scripts/readme_block.py
```

Every number and every block on this page is checked by `tests/test_readme.py` against the thing
it describes. The response body above is generated, not transcribed.

<!-- toolset:start -->

Part of the Q...Z toolset, all of it designing for the failure that does not announce itself:

- [QUACKZ](https://github.com/PNX89/QUACKZ), deflating a backtest that only looks good because
  it was picked out of two hundred.
- [QUOTEZ](https://github.com/PNX89/QUOTEZ), market data an agent can read and cannot act on.
- [QUELLZ](https://github.com/PNX89/QUELLZ), measuring what prompt-injection containment costs
  in utility as well as in attack rate.
- [QUIDZ](https://github.com/PNX89/QUIDZ), refusing the outbound payment that would have gone
  out twice.
- [QUESTZ](https://github.com/PNX89/QUESTZ), stopping a scraper before it writes a CSV from a
  page that changed shape.
- [QUIZZ](https://github.com/PNX89/QUIZZ), answering what a statistic said at the time, and
  refusing when it cannot.
- [QUARANTINEZ](https://github.com/PNX89/QUARANTINEZ), treating an outcome the venue never
  confirmed as terminal rather than as a retry.
- QUENCHZ, this one: deciding in the open what a tool server gets free while it is still
  somebody's subprocess.

<!-- toolset:end -->

## Licence

MIT. See [LICENSE](LICENSE).

The data is reused under the [ESCB policy on the reuse of statistics][policy], which permits it
free of charge on three conditions: the source is quoted, the statistics and metadata are not
modified, and no third-party data. All three are tests in [`tests/test_licence.py`], not
promises. **Source: ECB statistics.**

[policy]: https://www.ecb.europa.eu/stats/ecb_statistics/governance_and_quality_framework/html/usage_policy.en.html
