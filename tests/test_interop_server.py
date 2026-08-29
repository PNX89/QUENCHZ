"""The entry point that writes bearer tokens to disk, and the guard nothing was watching.

`src/quenchz/interop_server.py` had no test of any kind. The one grep hit for its name anywhere
in `tests/` was a string inside a list of README command lines, which is a check that the README
quotes the command, not that the command does anything.

That mattered most for one five line guard. `main` refuses to write its tokens file anywhere
inside the repository, because a bearer token in the working tree is one `git add -A` away from
being published in a public repository. The TypeScript harness always passes a path under a
temporary directory, so it never crosses the guard either: deleting the guard broke nothing,
anywhere, and the failure it prevents is silent by construction.

The tokens are short-lived and their issuer dies with the process. That is an argument for not
panicking, not an argument for committing them.
"""

from __future__ import annotations

import json
import pathlib

import pytest
import uvicorn

from quenchz.interop_server import main, token_manifest
from quenchz.issuer import Issuer

REPO = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def never_actually_serve(monkeypatch: pytest.MonkeyPatch) -> None:
    """`uvicorn.run` blocks, so without this a regression HANGS instead of failing.

    Learned by doing it. Deleting the guard and running the suite produced no failures and no
    output: the refusal tests fell through into a real server and the whole run was killed on a
    timeout, so the mutation looked survivable when it was not. A test that hangs on the
    regression it exists for is barely better than one that passes, because the first thing
    anybody does with a hanging suite is skip it.

    It also left a bearer token in the repository root, which is precisely the outcome the guard
    exists to prevent, delivered by the experiment meant to check whether the guard mattered.
    """
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: None)


@pytest.mark.parametrize(
    "target",
    ["tokens.json", "data/tokens.json", "docs/evidence/tokens.json", "tests/tokens.json"],
)
def test_it_refuses_to_write_tokens_anywhere_inside_the_repository(target: str) -> None:
    """Four paths, because the guard resolves rather than string-matches.

    A relative path, a path inside a directory that is committed, and a path inside the tests
    themselves are all the same mistake, and a check comparing prefixes as text would let at
    least one of them through.
    """
    assert not (REPO / target).exists(), (
        f"{target} is already present, so this test cannot tell a refusal from a no-op"
    )
    with pytest.raises(SystemExit) as refusal:
        main(["--tokens-file", str(REPO / target)])
    assert "refusing to write tokens inside the repository" in str(refusal.value)
    assert not (REPO / target).exists(), "the refusal happened after the file was written"


def test_a_relative_path_resolved_into_the_repository_is_refused_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`Path("tokens.json")` is inside the repository when the shell is, which is the usual case.

    The guard calls `.resolve()` first for exactly this reason, and a version that skipped it
    would pass every test above while failing the one way somebody would actually do it: typing
    a bare filename in the checkout they are working in.
    """
    monkeypatch.chdir(REPO)
    with pytest.raises(SystemExit, match="refusing to write tokens"):
        main(["--tokens-file", "tokens.json"])


def test_a_path_outside_the_repository_is_accepted(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A guard that refuses every path is an outage, and this one guards the only entry point.

    `uvicorn.run` is replaced rather than the server being started and killed. Binding a port in
    a unit test makes the suite depend on what else is running on the machine, and the thing
    being tested here happens strictly before the server exists.
    """
    served: dict[str, object] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: served.update(kwargs, app=app))

    target = tmp_path / "tokens.json"
    assert main(["--tokens-file", str(target), "--port", "8931"]) == 0
    assert target.exists(), "the guard refused a path outside the repository"

    written = json.loads(target.read_text(encoding="utf-8"))
    assert "tokens" in written
    assert served["port"] == 8931, "the server was not reached, so the write is all that ran"


def test_the_tokens_are_written_before_the_server_starts_listening(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ordering the TypeScript harness depends on, asserted rather than assumed.

    `run-proofs.mjs` waits for the tokens file and then connects. If the write ever moved after
    `uvicorn.run`, which blocks, the file would never appear and the harness would hang rather
    than fail, which is the worst shape of failure to debug from a CI log.
    """
    target = tmp_path / "tokens.json"
    existed_when_the_server_started: list[bool] = []
    monkeypatch.setattr(
        uvicorn,
        "run",
        lambda app, **kwargs: existed_when_the_server_started.append(target.exists()),
    )
    main(["--tokens-file", str(target)])
    assert existed_when_the_server_started == [True]


def test_every_token_the_proofs_reach_for_is_in_the_manifest() -> None:
    """Read from `prove.ts` rather than typed here, so the two cannot drift apart quietly.

    A manifest that lost a key would not raise in the client. `manifest.tokens.whatever` is
    `undefined`, the header becomes `Bearer undefined`, the server refuses it, and a proof
    expecting a refusal PRINTS OK. That is the failure worth guarding: not a crash, an
    agreement reached for the wrong reason.
    """
    import re

    prove = (REPO / "clients" / "typescript" / "src" / "prove.ts").read_text(encoding="utf-8")
    wanted = set(re.findall(r"tokens(?:\.|\[['\"])([a-zA-Z]+)", prove))
    assert wanted, "no token name was found in prove.ts, so this test is checking nothing"

    tokens = token_manifest(Issuer(), "http://127.0.0.1:8931/mcp")["tokens"]
    assert isinstance(tokens, dict)
    assert wanted <= set(tokens), (
        f"prove.ts reaches for tokens nobody mints: {wanted - set(tokens)}"
    )
    assert all(isinstance(value, str) and value.count(".") == 2 for value in tokens.values())


def test_the_manifest_mints_one_token_nothing_consumes() -> None:
    """A small finding, asserted rather than tidied away, because tidying it is a judgement.

    `withoutTheRatesScope` is minted into a manifest written to disk and is referenced by
    nothing: not by `prove.ts`, not by any Python test. It is a valid bearer token for this
    server with a scope that grants no tool, so it is harmless, and it is also the kind of
    leftover that later reads as evidence of a proof that was never written.

    Recorded here rather than deleted. Deleting it is right only if nobody intended a scope
    refusal proof, and that is not a call this test can make. If one is written, this test
    fails and is removed in the same change.
    """
    prove = (REPO / "clients" / "typescript" / "src" / "prove.ts").read_text(encoding="utf-8")
    assert "withoutTheRatesScope" not in prove, (
        "prove.ts now uses this token, so delete this test and let the one above cover it"
    )
    assert "withoutTheRatesScope" in token_manifest(Issuer(), "http://x/mcp")["tokens"]  # type: ignore[operator]


def test_the_manifest_withholds_the_numbers_the_client_must_derive() -> None:
    """The client works out 45 and 15 from capacity and reserve, so agreement is evidence.

    Searched over everything EXCEPT the tokens, and the first version of this did not do that:
    a JWT is a long base64 string that contains "45" and "15" by chance, so the naive search
    failed against a perfectly correct manifest. A test that cannot pass is not a strict test,
    it is a broken one.
    """
    manifest = dict(token_manifest(Issuer(), "http://127.0.0.1:8931/mcp"))
    del manifest["tokens"]
    flattened = json.dumps(manifest)

    assert "45" not in flattened and "15" not in flattened, (
        "the manifest now states the admission counts the client is supposed to derive, which "
        "turns the interop proof from evidence into obedience"
    )
    assert '"capacity": 60' in flattened
    assert '"reserveFraction": 0.5' in flattened
    assert '"callerCount": 2' in flattened
