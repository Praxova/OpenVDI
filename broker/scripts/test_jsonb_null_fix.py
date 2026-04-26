"""M2-09-fix verification — JSONB null vs SQL NULL on session end.

Drives `transition_to_ended` against live Postgres for one session row,
then queries the column to confirm it landed as SQL NULL (not JSON
literal `null`).

Run this with each combination of fixes applied/reverted to verify the
defense-in-depth claim:

    cd broker
    ../.venv/bin/python -m scripts.test_jsonb_null_fix

Reads connection params from .env (same as the broker). Requires the
fixtures seeded by the surrounding M2-09-fix smoke shell — a desktop
row at id 00000000-0000-0000-0000-0000000000d1.
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from app.database import async_session_factory, dispose_engine
from app.models import Session, SessionStatus
from app.services.session_tracker import transition_to_ended


_DESKTOP_ID = "00000000-0000-0000-0000-0000000000d1"


async def main() -> int:
    # 1. Insert a fresh ACTIVE session with populated connection_info.
    async with async_session_factory() as session:
        await session.execute(text("DELETE FROM sessions"))
        row = Session(
            desktop_id=_DESKTOP_ID,
            username="alice",
            protocol="novnc",
            status=SessionStatus.ACTIVE,
            connection_info={
                "kind": "novnc",
                "websocket_url": "wss://example",
                "password": "secret",
            },
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        sid = row.id

    # 2. Drive transition_to_ended on a fresh session (so we exercise
    #    whatever the current code on disk does, not stale state).
    async with async_session_factory() as session:
        row = await session.get(Session, sid)
        await transition_to_ended(session, row)
        await session.commit()

    # 3. Inspect the column directly via raw SQL — bypasses the ORM
    #    binding so we see what actually landed.
    async with async_session_factory() as session:
        result = (
            await session.execute(
                text(
                    """
                    SELECT
                        connection_info IS NULL AS is_sql_null,
                        connection_info::text = 'null' AS is_json_null,
                        connection_info::text AS raw_text
                    FROM sessions WHERE id = :sid
                    """
                ),
                {"sid": sid},
            )
        ).one()

    is_sql_null, is_json_null, raw_text = result
    print(f"  connection_info IS NULL          : {is_sql_null}")
    print(f"  connection_info::text == 'null'  : {is_json_null}")
    print(f"  connection_info::text            : {raw_text!r}")

    if is_sql_null and not is_json_null:
        print("PASS — column is SQL NULL")
        await dispose_engine()
        return 0
    if is_json_null and not is_sql_null:
        print("FAIL — column is JSON literal null (the bug reproduces)")
        await dispose_engine()
        return 1
    print("UNEXPECTED — neither SQL NULL nor JSON null")
    await dispose_engine()
    return 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
