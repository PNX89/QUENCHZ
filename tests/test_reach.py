"""A caller cannot learn what it may not use.

The assertion that carries this file is a byte comparison. Asking for a tool that exists but
is not granted, and asking for a tool that was never there, must produce the identical
sequence of bytes. Anything that differs, including a length, is an oracle.
"""

from __future__ import annotations

import pytest

from quenchz.tools import NO_SUCH_TOOL, Tool, ToolRefused, Toolset

GRANTED = frozenset({"rates:read"})


def _toolset() -> Toolset:
    return Toolset(
        [
            Tool("rates.window", "rates:read", lambda **_: "rates", "a window of rates"),
            Tool("series.catalogue", "series:list", lambda **_: "catalogue", "every series"),
        ]
    )


def test_an_ungranted_tool_and_a_missing_tool_refuse_identically() -> None:
    """The whole claim, as one comparison of bytes."""
    tools = _toolset()

    with pytest.raises(ToolRefused) as ungranted:
        tools.dispatch("series.catalogue", GRANTED, {})
    with pytest.raises(ToolRefused) as missing:
        tools.dispatch("series.cataloguz", GRANTED, {})

    left = str(ungranted.value).encode()
    right = str(missing.value).encode()
    assert left == right, "the two refusals differ, which makes the server an oracle"
    assert len(left) == len(right)
    assert left == NO_SUCH_TOOL.encode()


def test_the_refusal_never_repeats_the_name_it_was_asked_for() -> None:
    """A refusal that echoes the request is a refusal that confirms the request."""
    tools = _toolset()
    for name in ("series.catalogue", "something.entirely.invented", "rates.window.v2"):
        with pytest.raises(ToolRefused) as refused:
            tools.dispatch(name, GRANTED, {})
        assert name not in str(refused.value)
    assert "scope" not in NO_SUCH_TOOL, "naming the mechanism is naming the tool"


def test_a_tool_a_caller_cannot_call_is_a_tool_it_cannot_see() -> None:
    """Listing has to agree with dispatch, or the listing is the oracle instead."""
    tools = _toolset()
    assert tools.visible_to(GRANTED) == ["rates.window"]
    assert tools.visible_to(frozenset({"rates:read", "series:list"})) == [
        "rates.window",
        "series.catalogue",
    ]
    assert tools.visible_to(frozenset()) == []

    # And the tool it cannot see really does exist, so this is concealment and not absence.
    assert "series.catalogue" in tools.names()


def test_a_granted_tool_actually_runs() -> None:
    """The other direction. A server that refused everything would pass every test above."""
    assert _toolset().dispatch("rates.window", GRANTED, {}) == "rates"
    assert _toolset().dispatch("series.catalogue", frozenset({"series:list"}), {}) == "catalogue"
