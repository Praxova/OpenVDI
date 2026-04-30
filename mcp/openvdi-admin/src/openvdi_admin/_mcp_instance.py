"""The module-level FastMCP singleton.

Lives here (not in `server.py`) so that `_tool_wrapper.py` can
import it without pulling in `server.py`'s full broker-client
construction logic. Tool modules that test pytest collects in
isolation imported via the intent → tool → wrapper chain would
otherwise re-enter `server.py` mid-load and trip a circular
import.

No other code lives in this module — by design. Anything that
imports `mcp` from here should not depend on broker config /
auth / etc.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP


mcp = FastMCP("openvdi-admin")
