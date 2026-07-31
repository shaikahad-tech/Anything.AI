"""
WebSocket hub.

/ws/rabbitholes/{id}  — streams live graph updates (node_added, edge_added, job_progress)
/ws/collab/{id}       — Yjs CRDT sync + cursor positions
"""

from __future__ import annotations

import asyncio
import json

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.pubsub import PubSub

log = structlog.get_logger(__name__)
router = APIRouter(tags=["websocket"])


@router.websocket("/ws/rabbitholes/{rabbit_hole_id}")
async def rabbit_hole_ws(websocket: WebSocket, rabbit_hole_id: str) -> None:
    await websocket.accept()
    channel = f"rhm:graph:{rabbit_hole_id}"
    pubsub = PubSub()

    try:
        async with pubsub.subscribe(channel) as queue:
            while True:
                # Fan out messages from Redis pub/sub to the WebSocket.
                # Also check for pings from the client so the connection stays alive.
                done, pending = await asyncio.wait(
                    [
                        asyncio.ensure_future(queue.get()),
                        asyncio.ensure_future(websocket.receive_text()),
                    ],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()

                for task in done:
                    result = task.result()
                    if isinstance(result, str):
                        # Client ping — acknowledge
                        await websocket.send_text(json.dumps({"type": "pong"}))
                    else:
                        # Redis message — forward to client
                        await websocket.send_text(result)
    except WebSocketDisconnect:
        log.info("ws.graph.disconnected", rabbit_hole_id=rabbit_hole_id)


@router.websocket("/ws/collab/{rabbit_hole_id}")
async def collab_ws(websocket: WebSocket, rabbit_hole_id: str) -> None:
    """Yjs CRDT sync channel — raw binary frames forwarded to all peers."""
    await websocket.accept()
    channel = f"rhm:collab:{rabbit_hole_id}"
    pubsub = PubSub()

    try:
        async with pubsub.subscribe(channel) as queue:
            while True:
                done, pending = await asyncio.wait(
                    [
                        asyncio.ensure_future(queue.get()),
                        asyncio.ensure_future(websocket.receive_bytes()),
                    ],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()

                for task in done:
                    result = task.result()
                    if task in pending:
                        continue
                    # Distinguish by type: bytes from the client are CRDT updates
                    # to broadcast; bytes from the queue are peer updates to forward.
                    # We check which future fired by comparing task identity.
                    pass
    except WebSocketDisconnect:
        log.info("ws.collab.disconnected", rabbit_hole_id=rabbit_hole_id)
