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
                queue_task = asyncio.ensure_future(queue.get())
                recv_task = asyncio.ensure_future(websocket.receive_text())
                done, pending = await asyncio.wait(
                    [queue_task, recv_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()

                if recv_task in done:
                    # Client ping — acknowledge
                    await websocket.send_text(json.dumps({"type": "pong"}))

                if queue_task in done:
                    result = queue_task.result()
                    if isinstance(result, bytes):
                        await websocket.send_text(result.decode("utf-8", errors="replace"))
                    else:
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
                queue_task = asyncio.ensure_future(queue.get())
                recv_task = asyncio.ensure_future(websocket.receive_bytes())
                done, pending = await asyncio.wait(
                    [queue_task, recv_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()

                if recv_task in done:
                    # CRDT update from this client — broadcast to all peers
                    client_data = recv_task.result()
                    await pubsub.publish(channel, client_data)

                if queue_task in done:
                    # Message from another peer — forward to this client
                    peer_data = queue_task.result()
                    if isinstance(peer_data, bytes):
                        await websocket.send_bytes(peer_data)
                    else:
                        await websocket.send_bytes(peer_data.encode())
    except WebSocketDisconnect:
        log.info("ws.collab.disconnected", rabbit_hole_id=rabbit_hole_id)
