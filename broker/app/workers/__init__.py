"""Worker registry.

The `WORKERS` list is the source of truth for which workers the
WorkerRunner spawns. As workers ship across M4-08 through M4-13,
each prompt appends its class to this list.

For M4-07 the only registered worker is EchoWorker — a smoke that
exercises the framework. Subsequent prompts replace it:
  M4-08 → SessionMonitorWorker
  M4-09 → PoolProvisionerWorker
  M4-10 → TaskTrackerWorker
  M4-11 → HealthCheckerWorker
  M4-13 → AuditRetentionWorker

EchoWorker is removed from WORKERS once at least one real worker is
in the list; the class stays available for ad-hoc smokes.
"""
from app.workers.base import Worker, WorkerRunner
from app.workers.echo import EchoWorker

# Workers spawned at lifespan startup. Order doesn't matter — each
# is independent.
WORKERS: list[type[Worker]] = [EchoWorker]

__all__ = ["Worker", "WorkerRunner", "WORKERS", "EchoWorker"]
