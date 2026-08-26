"""Making the MCP layer tell the same story the dispatch layer tells.

`tools.py` makes an ungranted tool's refusal byte-identical to a nonexistent tool's. That is
true of `Toolset.dispatch`, and until this module existed it was NOT true of the server a
caller actually talks to, which is the only place it matters.

The gap, found by writing a client in another language and asking it what it saw. The SDK's
tool manager answers an unregistered name with `ToolError(f"Unknown tool: {name}")`. That text
differs from this repository's refusal and, worse, it echoes the name back. So over MCP a
caller could ask for `series.catalogue` and for `series.cataloguz` and get two different
answers, which is exactly the oracle the dispatch layer was built to avoid. All the care taken
over one message was undone by a second message nobody had looked at.

This middleware closes it, and it has to be middleware rather than a tool wrapper because
`ServerMiddleware` runs "before any validation, lookup, or handshake". Anything later has
already been through the SDK's own lookup and lost.

Two methods are intercepted and nothing else:

  tools/call   the caller's budget is charged FIRST, then a name it may not call, for either
               reason, is refused with the one message, before the SDK ever looks it up.
  tools/list   is filtered to what this caller may call, because a tool it cannot use is a
               tool it should not be told about.

THE CHARGE MOVED HERE, AND THAT WAS A DEFECT THIS MIDDLEWARE INTRODUCED. `gateway.py` charges
before it looks a tool up, on the argument that a free refusal is the same leak as a
descriptive one. This middleware then began refusing before the gateway was ever reached, so
over MCP a refused call cost nothing: measured at two hundred refusals for free while a granted
tool stopped at forty-five.

Being precise about what that did and did not do, because the first report overstated it. It
was NOT an existence oracle: an ungranted name and a nonexistent one were equally free, so a
caller still could not tell them apart. What it was is a stated invariant that had become
false, and an unlimited free probe of the namespace.

So there is now exactly ONE charge point and it is here, at the boundary every tools/call
crosses. The tools no longer charge, because two charge points is how a granted call gets
billed twice.

Source: ECB statistics.
"""

from __future__ import annotations

from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.types import CallToolResult, TextContent

from quenchz.budget import FairBudget
from quenchz.gateway import OVER_BUDGET
from quenchz.tools import NO_SUCH_TOOL, Toolset

__all__ = ["ConcealTheSurface"]


def _granted_scopes() -> frozenset[str]:
    token = get_access_token()
    return frozenset(token.scopes) if token is not None else frozenset()


def _name_from(params: Any) -> str | None:
    """The tool name out of raw, unvalidated params. Middleware runs before validation."""
    if isinstance(params, dict):
        name = params.get("name")
        return name if isinstance(name, str) else None
    return getattr(params, "name", None)


class ConcealTheSurface:
    """A `ServerMiddleware` that refuses uniformly and lists selectively."""

    def __init__(self, toolset: Toolset, budget: FairBudget) -> None:
        self._tools = toolset
        self._budget = budget

    async def __call__(self, ctx: Any, call_next: Any) -> Any:
        if ctx.method == "tools/call":
            token = get_access_token()
            caller = token.client_id if token is not None else ""
            if not self._budget.request(caller).admitted:
                # Charged before the name is looked at, so every call costs the same whether
                # or not it is served. See the module docstring.
                return CallToolResult(
                    content=[TextContent(type="text", text=OVER_BUDGET)], is_error=True
                )

            name = _name_from(ctx.params)
            if name is None or not self._tools.may_call(name, _granted_scopes()):
                # Identical for a name that does not exist and a name this caller was never
                # granted. It does not repeat the name it was asked for, because a refusal
                # that echoes the request is a refusal that confirms the request.
                return CallToolResult(
                    content=[TextContent(type="text", text=NO_SUCH_TOOL)], is_error=True
                )

        if ctx.method == "tools/list":
            return self._filter(await call_next(ctx))

        return await call_next(ctx)

    def _filter(self, result: Any) -> Any:
        """Drop every tool this caller may not call, from whichever shape the SDK returned.

        The first version of this method read `result.tools` and did nothing when that
        attribute was absent, which is what the SDK actually returns: a plain dict. It
        therefore passed the full list through untouched while looking, in the diff and in the
        source, exactly like a working filter. A guard that silently does nothing is worse
        than no guard at all, because nobody goes looking for it. So both shapes are handled
        and a third one RAISES rather than falling through to a quiet success.
        """
        visible = set(self._tools.visible_to(_granted_scopes()))

        if isinstance(result, dict):
            if "tools" not in result:
                raise RuntimeError(
                    f"tools/list returned a dict with no 'tools' key: {list(result)}"
                )
            result["tools"] = [
                tool
                for tool in result["tools"]
                if (tool.get("name") if isinstance(tool, dict) else tool.name) in visible
            ]
            return result

        tools = getattr(result, "tools", None)
        if tools is None:
            raise RuntimeError(
                f"tools/list returned {type(result).__name__}, which this filter does not "
                f"understand; refusing to pass an unfiltered tool list to a caller"
            )
        result.tools = [tool for tool in tools if tool.name in visible]
        return result
