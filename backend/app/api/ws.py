from __future__ import annotations

import asyncio
import contextlib

from fastapi import WebSocket


async def pump(ws: WebSocket, queue: asyncio.Queue) -> None:
    """Forward hub events to the socket until either side goes away."""

    async def sender() -> None:
        while True:
            await ws.send_json(await queue.get())

    async def receiver() -> None:
        while True:
            await ws.receive_text()  # we ignore client frames (keep-alive pings); raises on disconnect

    tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(BaseException):
                await t
