"""Dispatch, and one sentence for two different refusals.

A caller asking for a tool it has no scope for, and a caller asking for a tool that does not
exist, get back the same bytes. That is the whole design and it costs something real: a
legitimate caller who has genuinely mistyped a scope gets a less helpful message than they
could have had.

WHY IT IS WORTH THAT. The SDK's own `RequireAuthMiddleware` returns 403 with
`error_description` set to `f"Required scope: {required_scope}"`. It names the scope the
caller lacks, and in doing so confirms that the tool exists, that it is called what the caller
guessed, and exactly which grant would have worked. Ask it for a hundred plausible tool names
and the ones that answer "Required scope: x" are a map of the surface, drawn by the server,
for a caller that was never authorised to see it.

So this server does NOT enforce scope through `required_scopes`. It grants the transport no
required scopes at all and denies at dispatch instead, where both refusals can be made
identical. A test compares the two byte for byte, and a second test asserts that the
middleware's naming path is never configured.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["NO_SUCH_TOOL", "Tool", "ToolRefused", "Toolset"]

# One message, two situations. Never interpolate anything into it: a refusal that varied by
# even a length is a refusal that answers the question it was written to refuse.
NO_SUCH_TOOL = "no tool by that name is available to this caller"


class ToolRefused(Exception):
    """Raised for both an unknown tool and an ungranted one, carrying identical text."""

    def __init__(self) -> None:
        super().__init__(NO_SUCH_TOOL)


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    scope: str
    handler: Callable[..., Any]
    summary: str


class Toolset:
    """The dispatch surface. Nothing here consults the transport's own scope machinery."""

    def __init__(self, tools: list[Tool]) -> None:
        self._tools: dict[str, Tool] = {tool.name: tool for tool in tools}

    def names(self) -> list[str]:
        return sorted(self._tools)

    def visible_to(self, granted: frozenset[str]) -> list[str]:
        """What a caller may list. A tool it cannot call is a tool it cannot see."""
        return sorted(name for name, tool in self._tools.items() if tool.scope in granted)

    def may_call(self, name: str, granted: frozenset[str]) -> bool:
        """Whether this caller could call `name`, with no distinction between the two ways
        the answer can be no. Unknown name and ungranted scope both return False, and the
        caller of this method must not be able to tell which, because that is the whole point.
        """
        tool = self._tools.get(name)
        return tool is not None and tool.scope in granted

    def dispatch(self, name: str, granted: frozenset[str], arguments: Mapping[str, Any]) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            # Unknown name.
            raise ToolRefused
        if tool.scope not in granted:
            # Known name, ungranted scope. The caller must not be able to tell these apart,
            # so this raise is deliberately identical to the one above and takes no argument.
            raise ToolRefused
        return tool.handler(**arguments)
