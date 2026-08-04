"""Give the agent a custom tool.

The smallest useful extension: declare a tool with ``define_tool`` and hand it
to ``tau.register_tool``. Tau builds the schema, validates the model's
arguments, applies defaults and turns whatever ``execute`` returns into a
``ToolResult`` — so the extension is left with just the one line that does the
actual work.

Drop this file (or the folder) in ``~/.tau/extensions/`` to load it globally,
or ``<project>/.tau/extensions/`` for one project.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from tau.extensions import define_tool

if TYPE_CHECKING:
    from tau.extensions import ExtensionAPI


# Give the agent a capability it lacks, then let it call the tool.
current_time = define_tool(
    name="current_time",
    description="Get the current time in any IANA timezone",
    # (type, description) -- the description is what the model reads to fill
    # the argument in, so it is worth writing properly. Add a third element to
    # give the parameter a default, which also makes it optional.
    parameters={"timezone": (str, "e.g. Europe/Vienna")},
    execute=lambda params: (
        datetime.now(ZoneInfo(params["timezone"])).strftime("%Y-%m-%d %I:%M:%S %p %Z"),
        {"timezone": params["timezone"]},  # second element becomes ToolResult.metadata
    ),
)


def register(tau: ExtensionAPI) -> None:
    tau.register_tool(current_time)
