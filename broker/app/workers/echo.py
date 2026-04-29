"""EchoWorker — a no-op smoke worker.

Used by the M4-07 verification block and by the framework tests.
Logs one heartbeat per tick at INFO so an operator running the broker
in jwt mode + multi-broker can see leadership in the logs without
tailing DB state.

Real workers (M4-08 onward) replace EchoWorker in the WORKERS
registry. EchoWorker stays in the package as a smoke; remove from
WORKERS once at least one real worker is in the list.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI

from app.workers.base import Worker

logger = logging.getLogger(__name__)


class EchoWorker(Worker):
    name = "echo"
    interval_seconds = 10.0

    async def tick(self, app: FastAPI) -> None:
        logger.info("echo tick", extra={"worker": self.name})
