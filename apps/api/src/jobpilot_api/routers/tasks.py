"""Background task status and live progress (Server-Sent Events)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request, status
from sse_starlette.sse import EventSourceResponse

from jobpilot_api.deps import Session, State
from jobpilot_api.schemas import TaskCreated
from jobpilot_core.models import TaskRun
from jobpilot_core.tasks import (
    TERMINAL_STATUSES,
    TaskEvent,
    create_task_run,
    decode_event,
    task_channel,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])

SSE_PING_S = 15


@router.post(
    "/ping",
    response_model=TaskCreated,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue a demo task that exercises worker + progress streaming",
)
async def enqueue_ping(state: State, session: Session) -> TaskCreated:
    row = await create_task_run(session, "ping")
    await state.arq.enqueue_job("ping", str(row.id), _job_id=str(row.id))
    return TaskCreated(task_id=str(row.id))


@router.get("/{task_id}", response_model=TaskEvent)
async def get_task(task_id: uuid.UUID, session: Session) -> TaskEvent:
    row = await session.get(TaskRun, task_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "task not found")
    return TaskEvent.from_row(row)


@router.get("/{task_id}/events", summary="Stream task progress as SSE until it finishes")
async def stream_task(task_id: uuid.UUID, request: Request, state: State) -> EventSourceResponse:
    async def events() -> AsyncIterator[dict[str, str]]:
        pubsub = state.redis.pubsub()
        # subscribe before reading the current state so no update can slip in between
        await pubsub.subscribe(task_channel(task_id))
        try:
            async with state.sessionmaker() as session:
                row = await session.get(TaskRun, task_id)
            if row is None:
                yield {"event": "error", "data": '{"detail":"task not found"}'}
                return
            current = TaskEvent.from_row(row)
            yield {"event": "task", "data": current.model_dump_json()}
            if current.status in TERMINAL_STATUSES:
                return
            while not await request.is_disconnected():
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg is None:
                    await asyncio.sleep(0)
                    continue
                event = decode_event(msg["data"])
                yield {"event": "task", "data": event.model_dump_json()}
                if event.status in TERMINAL_STATUSES:
                    return
        finally:
            await pubsub.unsubscribe(task_channel(task_id))
            await pubsub.aclose()  # type: ignore[no-untyped-call]

    return EventSourceResponse(events(), ping=SSE_PING_S)
