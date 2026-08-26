"""The README's claims, executed.

Every number, command, path and printed block on that page is checked against the thing it
describes. A README that was true when it was written and is false now is worse than one that
never claimed anything, because it reads as evidence right up until somebody checks it, and the
somebodies here are interviewers.

The four kinds named in the shared manifest are implemented below. FILE lives in
`test_doc_contract.py`, which is the half of the contract that is identical across the toolset.
"""

from __future__ import annotations

import csv
import datetime
import importlib.util
import io
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[1]
README = (REPO / "README.md").read_text(encoding="utf-8")
CASSETTES = REPO / "data" / "cassettes"


def test_the_readme_response_body_is_the_one_the_server_returns() -> None:
    """OUTPUT. The first screenful is presented as a real answer, so it is generated and diffed.

    The block is produced by `scripts/readme_block.py` against the committed cassette with a
    frozen clock. If the certificate ever gains, loses or renames a field, this fails rather
    than leaving the page describing a shape the server stopped returning.
    """
    # Loaded by path rather than imported: `scripts/` is a directory of runnable programs and
    # not a package, and making it one so a test could import it would be the test changing the
    # shape of the repository to suit itself.
    spec = importlib.util.spec_from_file_location(
        "readme_block", REPO / "scripts" / "readme_block.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    generated = module.block()
    blocks = re.findall(r"```json\n(.*?)```", README, re.DOTALL)
    assert blocks, "the README shows no JSON block, so the first screenful is not what it claims"
    assert generated.strip() in [b.strip() for b in blocks], (
        "the README's response body is not what the server returns today. Regenerate it with "
        "`uv run python scripts/readme_block.py` rather than editing the page by hand."
    )


def test_the_readme_coverage_block_matches_the_committed_cassette() -> None:
    """NUMBER. Every figure the page states about the data, recomputed from the data.

    None of these is read from a second document. The cassette is opened and counted here, so
    the page and the bytes cannot drift apart without this failing.
    """
    rows = list(
        csv.DictReader(
            io.StringIO((CASSETTES / "usd-eur-daily-full-history.body").read_bytes().decode())
        )
    )
    values = [r for r in rows if r["OBS_VALUE"]]
    placeholders = [r for r in rows if not r["OBS_VALUE"]]

    stated = {
        "rows": len(rows),
        "values": len(values),
        "placeholders": len(placeholders),
    }
    assert stated == {"rows": 7140, "values": 7078, "placeholders": 62}

    for number in ("7,140", "7,078", "62"):
        assert number in README, f"the README does not state {number}"

    # The Easter window, which is the whole argument of the first screenful.
    easter = list(
        csv.DictReader(
            io.StringIO((CASSETTES / "usd-eur-daily-easter-2026.body").read_bytes().decode())
        )
    )
    assert len(easter) == 8
    requested = (datetime.date(2026, 4, 10) - datetime.date(2026, 3, 30)).days + 1
    assert requested == 12

    # Checked against the printed certificate rather than by pattern-matching the prose. The
    # page says "twelve" and "eight" in words, which a digit search would miss, and a search
    # loose enough to find them in prose would match almost anything.
    printed = re.search(r"```json\n(.*?)```", README, re.DOTALL)
    assert printed, "no certificate is printed"
    assert f'"requested_calendar_days": {requested}' in printed.group(1)
    assert f'"delivered_observations": {len(easter)}' in printed.group(1)
    assert f'"expected_observations": {len(easter)}' in printed.group(1)


def test_the_readme_budget_numbers_are_the_ones_the_limiter_produces() -> None:
    """NUMBER. The fairness table, run rather than quoted."""
    from quenchz.budget import FairBudget, ManualClock, NaiveSharedBucket

    naive = NaiveSharedBucket(capacity=60, refill_per_second=60, clock=ManualClock())
    greedy_naive = sum(naive.request("greedy").admitted for _ in range(1000))
    quiet_naive = sum(naive.request("quiet").admitted for _ in range(60))

    fair = FairBudget(
        capacity=60, refill_per_second=60, callers=("greedy", "quiet"), clock=ManualClock()
    )
    greedy_fair = sum(fair.request("greedy").admitted for _ in range(1000))
    quiet_fair = sum(fair.request("quiet").admitted for _ in range(60))

    assert (greedy_naive, quiet_naive) == (60, 0)
    assert (greedy_fair, quiet_fair) == (45, 15)

    table = re.search(r"\| one shared bucket \|.*?\n\|.*?\n", README, re.DOTALL)
    assert table, "the README shows no fairness table"
    assert "| 60 |" in table.group() or " 60 " in table.group()
    assert "0 of 60" in README and "15 of 60" in README


def test_every_command_the_readme_shows_is_one_this_repository_runs() -> None:
    """COMMAND. A command a reader is invited to run must be one that exists."""
    workflow = (REPO / ".github" / "workflows" / "ci.yml").read_text("utf-8")
    gates = {
        "uv sync",
        "uv sync --dev",
        "uv run ruff check .",
        "uv run ruff format --check .",
        "uv run mypy",
        "uv run pytest",
    }
    from_ci = set(re.findall(r"^\s*-?\s*run: ((?:uv|npm) .+?)\s*$", workflow, re.MULTILINE))
    scripts = {f"uv run python {path.relative_to(REPO)}" for path in REPO.glob("scripts/*.py")}
    modules = {"uv run python -m quenchz.interop_server"}

    def normalise(command: str) -> str:
        return re.sub(r"\s+", " ", command.split("--port")[0].split("--tokens-file")[0]).strip()

    known = {normalise(c) for c in gates | from_ci | scripts | modules}
    shown = re.findall(r"^\$?\s*((?:uv|npm) (?:run|sync|ci|test) .*)$", README, re.MULTILINE)
    assert shown, "the README invites the reader to run nothing at all"
    for command in shown:
        assert normalise(command) in known, (
            f"the README shows a command nothing here runs: {command}"
        )


def test_every_path_and_link_in_the_readme_resolves() -> None:
    """REFERENCE. A path pointing at nothing is a rename somebody did not finish."""
    for target in re.findall(r"\]\(([^)]+)\)", README):
        if target.startswith("https://"):
            continue
        assert not target.startswith("http://"), f"insecure link: {target}"
        assert (REPO / target.split("#")[0]).exists(), target


def test_the_readme_names_a_headline_file_that_exists() -> None:
    """REFERENCE. An interviewer asked to pick a file picks the one named first."""
    first_screenful = README.split("## ")[0]
    named = re.findall(r"\[`([^`]+)`\]", first_screenful)
    assert named, "the first screenful names no file"
    for path in named:
        assert (REPO / path).exists(), path


def test_the_attribution_the_licence_requires_is_on_the_page() -> None:
    """The ECB permits this on the condition that the source is quoted. So it is quoted."""
    assert "Source: ECB statistics." in README


def test_the_page_states_a_limitation_before_it_stops_being_read() -> None:
    """N31. A limitation that lives at the bottom is a limitation nobody reaches."""
    quarter = README[: len(README) // 4]
    admissions = ("not", "no ", "does not", "never", "cannot")
    assert any(word in quarter.lower() for word in admissions), (
        "nothing in the first quarter of the page admits a limit"
    )


def test_the_page_does_not_claim_what_this_repository_must_never_claim() -> None:
    """The must-never-claim list, as a check on the text rather than a promise about it.

    Each entry bans a CLAIM and not a vocabulary. The page is free to say it does not do these
    things, which is why every pattern requires the asserting form.
    """
    lowered = README.lower()
    banned = {
        r"\bwe trade\b|\bplaces orders\b|\border placement\b": "trading or order placement",
        r"\bproduction identity provider\b|\bokta\b|\bentra id\b": "a real identity provider",
        r"\brequests per second\b|\bthroughput of\b|\bp99\b": "a latency or throughput figure",
        r"\border book\b|\bmatching engine\b|\bdepth reconstruction\b": "market microstructure",
        r"\bendorsed by the ecb\b|\bin partnership with the ecb\b": "an ECB relationship",
        r"\btradeable price\b|\btradable price\b": "reference rates as tradeable prices",
    }
    for pattern, what in banned.items():
        for hit in re.finditer(pattern, lowered):
            window = lowered[max(0, hit.start() - 90) : hit.end() + 90]
            assert any(n in window for n in (" not ", " no ", "never", "cannot", "without")), (
                f"the page appears to claim {what}: ...{window.strip()}..."
            )
