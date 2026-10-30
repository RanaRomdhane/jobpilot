"""Background-task bookkeeping shared by the API (create, read, stream) and the worker (report).

State lives in the `task_run` table; every change is also published on the Redis channel
`task:<id>` so the API can stream it to the dashboard over SSE.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from jobpilot_core.enums import TaskStatus
from jobpilot_core.models import TaskRun

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

TERMINAL_STATUSES = frozenset({TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED})


ALL_TASKS_CHANNEL = "tasks:all"


def task_channel(task_id: uuid.UUID | str) -> str:
    return f"task:{task_id}"


class TaskEvent(BaseModel):
    id: uuid.UUID
    kind: str
    status: TaskStatus
    progress: float
    message: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None

    @classmethod
    def from_row(cls, row: TaskRun) -> TaskEvent:
        return cls(
            id=row.id,
            kind=row.kind,
            status=row.status,
            progress=row.progress,
            message=row.message,
            result=row.result,
            error=row.error,
        )


async def create_task_run(
    session: AsyncSession, kind: str, params: dict[str, Any] | None = None
) -> TaskRun:
    row = TaskRun(
        id=uuid.uuid4(), kind=kind, status=TaskStatus.QUEUED, progress=0.0, params=params or {}
    )
    session.add(row)
    await session.commit()
    return row


class TaskReporter:
    """Used inside worker jobs to persist and broadcast progress."""

    def __init__(
        self, sessionmaker: async_sessionmaker[AsyncSession], redis: Redis, task_id: uuid.UUID
    ) -> None:
        self._sessionmaker = sessionmaker
        self._redis = redis
        self.task_id = task_id

    async def _update(self, **fields: Any) -> TaskEvent:
        async with self._sessionmaker() as session:
            row = await session.get(TaskRun, self.task_id)
            if row is None:
                raise LookupError(f"task_run {self.task_id} not found")
            for k, v in fields.items():
                setattr(row, k, v)
            await session.commit()
            event = TaskEvent.from_row(row)
        payload = event.model_dump_json()
        await self._redis.publish(task_channel(self.task_id), payload)
        await self._redis.publish(ALL_TASKS_CHANNEL, payload)
        return event

    async def start(self, message: str | None = None) -> TaskEvent:
        return await self._update(
            status=TaskStatus.RUNNING, started_at=datetime.now(UTC), message=message
        )

    async def progress(self, progress: float, message: str | None = None) -> TaskEvent:
        return await self._update(progress=max(0.0, min(1.0, progress)), message=message)

    async def succeed(
        self, result: dict[str, Any] | None = None, message: str | None = None
    ) -> TaskEvent:
        return await self._update(
            status=TaskStatus.SUCCEEDED,
            progress=1.0,
            result=result,
            message=message,
            finished_at=datetime.now(UTC),
        )

    async def fail(self, error: str) -> TaskEvent:
        return await self._update(
            status=TaskStatus.FAILED, error=error[:2000], finished_at=datetime.now(UTC)
        )


def decode_event(raw: bytes | str) -> TaskEvent:
    return TaskEvent.model_validate(json.loads(raw))
