"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from jobpilot_api.state import AppState


def get_state(request: Request) -> AppState:
    state: AppState = request.app.state.jobpilot
    return state


State = Annotated[AppState, Depends(get_state)]


async def get_session(state: State) -> AsyncIterator[AsyncSession]:
    async with state.sessionmaker() as session:
        yield session


Session = Annotated[AsyncSession, Depends(get_session)]
