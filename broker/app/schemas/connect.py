"""Connect-response ticket shapes for POST /me/desktops/{pool_id}/connect.

Each ticket variant is tagged with a `kind` discriminator — the portal's
renderer switches on that field to pick the right viewer. v0 only
produces `"novnc"` but the other three shapes are defined so new
providers (or a future Proxmox SPICE path) can serialize without
touching this module.

`ConsoleTicketRead` is the wire-side union; `ticket_to_wire()` converts
a provider-layer `ConsoleTicket` dataclass (from `providers.base`) into
the matching pydantic variant by dispatching on `.kind`.
"""
from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel

from app.providers.base import (
    ConsoleKind,
    ConsoleTicket,
    NoVNCTicket,
    RDPTicket,
    SpiceTicket,
    WebMKSTicket,
)


class NoVNCTicketRead(BaseModel):
    kind: Literal["novnc"] = "novnc"
    websocket_url: str
    password: str
    cert_pem: str | None = None


class SpiceTicketRead(BaseModel):
    kind: Literal["spice"] = "spice"
    host: str
    port: int
    tls_port: int | None = None
    password: str
    proxy: str | None = None


class WebMKSTicketRead(BaseModel):
    kind: Literal["webmks"] = "webmks"
    host: str
    port: int
    ticket: str


class RDPTicketRead(BaseModel):
    kind: Literal["rdp"] = "rdp"
    host: str
    port: int = 3389
    username: str | None = None
    password: str | None = None
    gateway_host: str | None = None
    gateway_token: str | None = None


# Discriminated union — pydantic picks the right variant on (de)serialize
# by inspecting the `kind` field. The tuple is exposed here so handlers
# declaring response_model=... pick up every shape automatically if a
# future provider starts issuing SPICE tickets.
ConsoleTicketRead = (
    NoVNCTicketRead | SpiceTicketRead | WebMKSTicketRead | RDPTicketRead
)


class ConnectResponse(BaseModel):
    session_id: uuid.UUID
    desktop_name: str
    ticket: ConsoleTicketRead


def ticket_to_wire(ticket: ConsoleTicket) -> ConsoleTicketRead:
    """Translate a provider-layer `ConsoleTicket` dataclass to the wire.

    Dispatch on `.kind` rather than `isinstance` so the conversion
    tolerates future provider implementations that subclass or return
    a new variant. A kind the broker doesn't know raises ValueError —
    the handler will surface it as 500 via M2-11's catch-all envelope,
    which is the right outcome for "provider returned something this
    broker version can't serialize".
    """
    if ticket.kind == ConsoleKind.NOVNC:
        assert isinstance(ticket, NoVNCTicket)  # narrow for the type checker
        return NoVNCTicketRead(
            websocket_url=ticket.websocket_url,
            password=ticket.password,
            cert_pem=ticket.cert_pem,
        )
    if ticket.kind == ConsoleKind.SPICE:
        assert isinstance(ticket, SpiceTicket)
        return SpiceTicketRead(
            host=ticket.host,
            port=ticket.port,
            tls_port=ticket.tls_port,
            password=ticket.password,
            proxy=ticket.proxy,
        )
    if ticket.kind == ConsoleKind.WEBMKS:
        assert isinstance(ticket, WebMKSTicket)
        return WebMKSTicketRead(
            host=ticket.host, port=ticket.port, ticket=ticket.ticket,
        )
    if ticket.kind == ConsoleKind.RDP:
        assert isinstance(ticket, RDPTicket)
        return RDPTicketRead(
            host=ticket.host,
            port=ticket.port,
            username=ticket.username,
            password=ticket.password,
            gateway_host=ticket.gateway_host,
            gateway_token=ticket.gateway_token,
        )
    raise ValueError(f"unknown console kind: {ticket.kind!r}")
