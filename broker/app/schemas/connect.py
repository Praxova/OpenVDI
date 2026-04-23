"""Connect-response ticket shapes for POST /me/desktops/{pool_id}/connect.

The ConsoleTicketRead alias is a degenerate union for v0 (noVNC only).
Adding SPICE/WebMKS/RDP later is a one-line widening: change the alias
to `NoVNCTicketRead | SpiceTicketRead | ...`.
"""
from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel


class NoVNCTicketRead(BaseModel):
    kind: Literal["novnc"] = "novnc"
    websocket_url: str
    password: str
    cert_pem: str | None = None


# v0: single console kind. Widens to a union when more are added.
ConsoleTicketRead = NoVNCTicketRead


class ConnectResponse(BaseModel):
    session_id: uuid.UUID
    desktop_name: str
    ticket: ConsoleTicketRead
