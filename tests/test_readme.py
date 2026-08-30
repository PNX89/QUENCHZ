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

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
README = (REPO / "README.md").read_text(encoding="utf-8")
CASSETTES = REPO / "data" / "cassettes"

# Fenced blocks come out before any bracket is looked at. The response body on the first
# screenful is JSON, where `["2026-03-30", 1.1484]` is a pair of numbers to a reader and a
# bracket span to a regular expression.
FENCED = re.compile(r"```.*?```", re.DOTALL)

# One level of nesting, because that is what a badge is: an image link inside a link.
BRACKETED = re.compile(r"!?\[(?:[^\[\]]|\[[^\[\]]*\])*\]")


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


def test_the_readme_states_the_1990_count_the_certificate_really_produces() -> None:
    """NUMBER. The figure the fourth absence exists for, recomputed rather than typed.

    261 is stated three times in that paragraph and nothing recomputed any of them. It is the
    weekday count of 1990, which is what `reconstruct` reports as expected when it is given no
    lower bound, and it is the number that made BEFORE_THE_SERIES necessary in the first place.

    Every integer in the paragraph is compared, rather than one being searched for across the
    page: a document this long carries any three digit number somewhere.
    """
    from quenchz.coverage import reconstruct

    weekdays_in_1990 = reconstruct(
        datetime.date(1990, 1, 1),
        datetime.date(1990, 12, 31),
        set(),
        datetime.datetime(2026, 8, 26, 12, 0, tzinfo=datetime.UTC),
    ).expected_observations

    paragraph = next(p for p in README.split("\n\n") if "window in 1990" in p)
    figures = {int(n) for n in re.findall(r"\b\d+\b", paragraph)} - {1990}
    assert figures == {weekdays_in_1990}, (
        f"the 1990 paragraph states {sorted(figures)} where the certificate produces "
        f"{weekdays_in_1990}"
    )


def test_the_readme_states_the_number_of_checks_the_typescript_proof_declares() -> None:
    """NUMBER. "Sixteen checks" was typed here and nothing in either language could see it move.

    `prove.ts` counts its checks as it goes, so deleting one printed "15 of 15 checks passed"
    and exited 0: a smaller proof reporting success, with the page still claiming the old
    figure. It declares the total now and fails when its own count disagrees. This reads that
    declaration and requires the page to state the same number.

    Searched inside the section that makes the claim, and required to be the only such claim
    there, because a page this long contains any two digit number somewhere.
    """
    prove = (REPO / "clients" / "typescript" / "src" / "prove.ts").read_text(encoding="utf-8")
    declared = re.search(r"const DECLARED_CHECKS = (\d+);", prove)
    assert declared, "prove.ts no longer declares how many checks it makes"

    section = re.search(
        r"^## Proving it from outside Python\n(.*?)(?=^## )", README, re.MULTILINE | re.DOTALL
    )
    assert section, "the page no longer has the section that states this"
    assert re.findall(r"\b(\d+) checks\b", section.group(1)) == [declared.group(1)], (
        f"the page does not state the {declared.group(1)} checks prove.ts declares"
    )


def test_the_readme_counts_the_publication_times_the_way_its_own_table_does() -> None:
    """NUMBER. Three times, and how many of them a client finds in the payload.

    The sentence used to say "only two of them are visible to a client", which the table cannot
    settle either way: one time is in the payload and the other two are in the vendor's prose,
    so what "visible" covers was the reader's guess. It states what the table states now, and
    both figures are counted out of the table rather than read from the sentence.
    """
    section = re.search(
        r"^## What the vendor writes down and the payload does not\n(.*?)(?=^## )",
        README,
        re.MULTILINE | re.DOTALL,
    )
    assert section, "the page no longer has the section that states this"
    rows = [
        line for line in section.group(1).splitlines() if line.startswith("| ") and "CET" in line
    ]
    in_the_payload = [line for line in rows if "in the payload" in line]

    words = {1: "one", 2: "two", 3: "three", 4: "four"}
    assert rows and set(words) >= {len(rows), len(in_the_payload)}, (
        f"{len(rows)} times are tabulated and this test knows no word for that many"
    )
    sentence = section.group(1).split("\n\n")[0]
    assert f"{words[len(rows)]} times" in sentence, sentence
    assert f"only {words[len(in_the_payload)]} of them" in sentence, sentence


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
        # THIS COMPARES THE COMMAND AND NOT ITS ARGUMENTS, which is the whole of what it can do:
        # a port and a path are a reader's choice and cannot be matched against a fixed list.
        # It is not a check on what the arguments say, and reading it as one is how the page
        # came to print a `--tokens-file` the server refuses. The arguments are run for real by
        # `test_the_server_command_the_readme_prints_actually_starts_the_server` below.
        return re.sub(r"\s+", " ", command.split("--port")[0].split("--tokens-file")[0]).strip()

    known = {normalise(c) for c in gates | from_ci | scripts | modules}
    shown = re.findall(r"^\$?\s*((?:uv|npm) (?:run|sync|ci|test) .*)$", README, re.MULTILINE)
    assert shown, "the README invites the reader to run nothing at all"
    for command in shown:
        assert normalise(command) in known, (
            f"the README shows a command nothing here runs: {command}"
        )


def test_the_server_command_the_readme_prints_actually_starts_the_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """COMMAND. The printed line, run with its own arguments and only the serving replaced.

    The page told a reader to start the server with `--tokens-file tokens.json`, and
    `interop_server.main` refuses any tokens path that resolves inside the repository, because a
    bearer token in the working tree is one `git add -A` away from being published. So the one
    command on the page that starts anything exited 1 and started nothing. The guard is right
    and the instruction was wrong.

    The test above could not see it: `normalise` cuts a shown command at `--tokens-file`, so the
    half being compared was the half that works. This runs the whole line from the checkout
    root, which is where a reader following the page is standing.

    `uvicorn.run` is replaced rather than a port being bound. What is being checked happens
    strictly before the server exists, and binding a port would make the suite depend on what
    else is running on the machine.
    """
    import shlex

    import uvicorn

    from quenchz.interop_server import main

    shown = re.findall(r"^uv run python -m quenchz\.interop_server .+$", README, re.MULTILINE)
    assert len(shown) == 1, f"expected one server command on the page and found {len(shown)}"

    words = shlex.split(shown[0])
    argv = words[words.index("quenchz.interop_server") + 1 :]
    tokens = pathlib.Path(dict(zip(argv[::2], argv[1::2], strict=True))["--tokens-file"])

    monkeypatch.chdir(REPO)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: None)
    tokens.unlink(missing_ok=True)
    try:
        try:
            code = main(argv)
        except SystemExit as refused:
            raise AssertionError(
                f"the command the README prints does not run: {refused}"
            ) from refused
        assert code == 0
        assert tokens.exists(), "the command returned 0 and wrote no tokens file"
    finally:
        # Short-lived and minted by an issuer that dies with this process, and still not
        # something to leave lying about.
        tokens.unlink(missing_ok=True)


def test_every_path_and_link_in_the_readme_resolves() -> None:
    """REFERENCE. A path pointing at nothing is a rename somebody did not finish."""
    for target in re.findall(r"\]\(([^)]+)\)", README):
        if target.startswith("https://"):
            continue
        assert not target.startswith("http://"), f"insecure link: {target}"
        assert (REPO / target.split("#")[0]).exists(), target


def test_every_bracketed_reference_in_the_readme_is_actually_a_link() -> None:
    """REFERENCE. A shortcut reference with no definition renders as brackets, not as a link.

    Four of these shipped, and one of them was the loudest call to action on the page: "if you
    only open one file, open [src/quenchz/coverage.py]", written as a shortcut reference with no
    definition anywhere in the file. Every GFM parser renders that as literal square brackets,
    so every link to a sibling repository worked and not one line of this repository's own
    source did.

    Two tests looked like they covered it and neither could. One asserts the headline path
    exists on disk, the other resolves inline `](...)` targets. Between them they proved the
    path was real and never that it was a link.
    """
    prose = FENCED.sub("", README)
    defined = set(re.findall(r"^\[([^\]]+)\]:", prose, re.MULTILINE))

    literal = []
    for match in BRACKETED.finditer(prose):
        rest = prose[match.end() :]
        # A definition sits at the start of its own line. THE COLON IS NOT ENOUGH ON ITS OWN,
        # which is how the first version of this test passed on the very line it was written
        # for: "open [`src/quenchz/coverage.py`]: it is the certificate below" is a colon in
        # prose, and skipping every `]:` skipped the defect.
        starts_a_line = match.start() == 0 or prose[match.start() - 1] == "\n"
        if rest.startswith("(") or (rest.startswith(":") and starts_a_line):
            continue
        # `[text][label]` names its label next. `[label]` and `[label][]` are the label itself.
        collated = re.match(r"\[([^\[\]]*)\]", rest)
        label = collated.group(1) if collated and collated.group(1) else match.group().strip("![]")
        if label not in defined:
            literal.append(match.group())

    assert literal == [], (
        f"these render as literal square brackets rather than as links: {literal}. A reference "
        f"needs either an inline (target) or a matching [label]: definition."
    )


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


# Phrases nobody writes except to concede something, each anchored on word boundaries so that
# none of them can be satisfied from inside a longer word. "never" and a bare "no" are left out
# on purpose: "it never stops" and "no matter the payload" are both boasts.
ADMISSIONS = (
    r"\bdoes not\b",
    r"\bdo not\b",
    r"\bcannot\b",
    r"\bno claim is made\b",
    r"\bnothing here\b",
    r"\bis not the same as\b",
)


def _admits_something(passage: str) -> bool:
    """Whether a passage concedes a limit, rather than merely containing the letters of one.

    No word list can show that a page is honest. This shows only that it says something
    limiting, which is the part a check can carry, and it is written so that it can go red.
    """
    return any(re.search(phrase, passage, re.IGNORECASE) for phrase in ADMISSIONS)


def test_the_admission_check_cannot_be_satisfied_by_prose_that_admits_nothing() -> None:
    """The guard below, pointed at a page with no limit anywhere on it.

    The first version tested `any(word in quarter.lower() for word in ("not", "no ", "does not",
    "never", "cannot"))`. Those are bare substrings, so "not" matched inside "Annotations",
    "another" and the certificate's own `not_yet_published` field, which the page prints in its
    own code block. It could not have failed on any plausible README, which makes it decoration
    in the one file this repository holds up as proof that its claims are checked.
    """
    hype = (
        "QUENCHZ is a production grade MCP server. It is fast, complete and battle tested. "
        "Annotations everywhere, no matter the payload. It handles everything another agent "
        "throws at it, and it never stops."
    )
    assert not _admits_something(hype), (
        "a paragraph that concedes nothing at all passes this check, so it checks nothing"
    )
    assert _admits_something(
        "The budget is self-imposed. It does not correspond to any limit the vendor publishes."
    ), "a real concession fails this check, so it would refuse an honest page"


def test_the_page_states_a_limitation_before_it_stops_being_read() -> None:
    """N31. A limitation that lives at the bottom is a limitation nobody reaches."""
    assert _admits_something(README[: len(README) // 4]), (
        "nothing in the first quarter of the page admits a limit"
    )

    # The structural half, which no vocabulary can fake: the concessions are collected under
    # their own heading rather than being one clause somebody can delete.
    section = re.search(r"^## Limitations\n(.*?)(?=^## )", README, re.MULTILINE | re.DOTALL)
    assert section, "the page has no Limitations section of its own"
    bullets = re.findall(r"^- ", section.group(1), re.MULTILINE)
    assert len(bullets) >= 3, f"the Limitations section states {len(bullets)} limits"


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
