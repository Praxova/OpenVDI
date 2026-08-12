"""Unit tests for ProxmoxProvider that need no live cluster.

The provider's HTTP plumbing (`_ProxmoxClient._request`) is patched so
httpx never touches the network; everything else is the real code path.
"""
from __future__ import annotations

from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest

from app.providers.base import ConsoleKind, NoVNCTicket
from app.providers.proxmox.provider import ProxmoxProvider
from app.providers.proxmox.types import make_vm_ref


def _build_provider() -> ProxmoxProvider:
    return ProxmoxProvider(
        api_url="https://10.0.0.2:8006",
        token_id="openvdi@pve!openvdi",
        token_secret="secret",
        verify_ssl=False,
    )


async def test_novnc_ticket_websocket_authority_is_api_endpoint():
    """The websocket authority must be the cluster API endpoint, not the
    VNC port; the raw VNC port travels only as the `port` query param.

    Regression: a URL of the form wss://{node}:{vnc_port}/... points the
    browser's TLS handshake at QEMU's raw RFB listener and closes with
    WebSocket code 1006.
    """
    provider = _build_provider()
    # vncproxy returns the raw VNC listener port (5900–5999) plus a
    # ticket; note the port here is deliberately NOT the API port.
    provider._client._request = AsyncMock(
        return_value={
            "port": 5901,
            "ticket": "PVEVNC:ABC/+=def",  # chars that must be url-encoded
            "password": "s3cret",
            "cert": "-----BEGIN CERTIFICATE-----\n...",
        }
    )

    ref = make_vm_ref("pia-dev", 5000)
    ticket = await provider.get_console_ticket(ref, ConsoleKind.NOVNC)

    assert isinstance(ticket, NoVNCTicket)
    parts = urlsplit(ticket.websocket_url)
    assert parts.scheme == "wss"
    # Authority is the API endpoint, never pia-dev:5901.
    assert parts.netloc == "10.0.0.2:8006"
    assert parts.path == "/api2/json/nodes/pia-dev/qemu/5000/vncwebsocket"

    query = parse_qs(parts.query)
    # The VNC port survives only as the query param.
    assert query["port"] == ["5901"]
    # Ticket is url-encoded exactly once (single decode recovers it,
    # and the raw '+' / '/' / '=' are not present literally in the query).
    assert query["vncticket"] == ["PVEVNC:ABC/+=def"]
    assert "PVEVNC:ABC/+=def" not in parts.query

    await provider.close()


async def test_novnc_ticket_ticket_not_double_encoded():
    """A percent sign in the ticket must be encoded once, not twice."""
    provider = _build_provider()
    provider._client._request = AsyncMock(
        return_value={"port": 5900, "ticket": "a%b", "password": "", "cert": None}
    )

    ref = make_vm_ref("node1", 5010)
    ticket = await provider.get_console_ticket(ref, ConsoleKind.NOVNC)

    query = parse_qs(urlsplit(ticket.websocket_url).query)
    # parse_qs decodes once. Double-encoding would yield 'a%b' -> 'a%25b'.
    assert query["vncticket"] == ["a%b"]

    await provider.close()
