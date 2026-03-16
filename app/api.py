"""HTTP control API for PixelPet.

Runs in a background daemon thread so it never blocks the AppKit main run loop.
Commands are passed to the main thread via a thread-safe queue consumed in
decisionTick_.

Default port: 37420  (configurable via PIXELPET_PORT env var)
"""

from __future__ import annotations

import collections
import os
import queue
import threading
import time
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Shared state (written by main thread, read from any thread)
# ---------------------------------------------------------------------------

# Queue of body-state patches. Main thread pops and applies them in decisionTick_.
command_queue: queue.Queue[dict[str, Any]] = queue.Queue()

# Read-only snapshot updated by the main thread.
# Dict assignment is GIL-atomic in CPython — safe to read from any thread.
_status: dict = {
    "action": None,
    "available": [],
    "available_poses": [],
    "available_motions": [],
    "body_state": {},
    "fallback_active": True,
    "controller": "openclaw",
    "last_control_age": None,
    "hold_seconds_remaining": None,
    "port": None,
    "position": None,
    "idle_seconds": None,
    "idle_state": False,
}

# Circular event buffer — last 50 events, newest at the right.
_events: collections.deque = collections.deque(maxlen=50)
_event_seq: int = 0


def update_status(
    action: str | None,
    available: list[str],
    port: int,
    *,
    available_poses: list[str] | None = None,
    available_motions: list[str] | None = None,
    body_state: dict | None = None,
    fallback_active: bool | None = None,
    controller: str | None = None,
    last_control_age: float | None = None,
    hold_seconds_remaining: float | None = None,
    position: dict | None = None,
    idle_seconds: float | None = None,
    idle_state: bool | None = None,
) -> None:
    """Called from the AppKit main thread to refresh the status snapshot."""
    _status["action"] = action
    _status["available"] = available
    _status["port"] = port
    if available_poses is not None:
        _status["available_poses"] = available_poses
    if available_motions is not None:
        _status["available_motions"] = available_motions
    if body_state is not None:
        _status["body_state"] = body_state
    if fallback_active is not None:
        _status["fallback_active"] = bool(fallback_active)
    if controller is not None:
        _status["controller"] = controller
    if last_control_age is not None:
        _status["last_control_age"] = round(last_control_age, 1)
    if hold_seconds_remaining is not None:
        _status["hold_seconds_remaining"] = round(hold_seconds_remaining, 1)
    else:
        _status["hold_seconds_remaining"] = None
    if position is not None:
        _status["position"] = position
    if idle_seconds is not None:
        _status["idle_seconds"] = round(idle_seconds, 1)
    if idle_state is not None:
        _status["idle_state"] = bool(idle_state)


def emit_event(type: str, **data) -> None:
    """Append an event to the circular buffer. Thread-safe (GIL-atomic append)."""
    global _event_seq
    _event_seq += 1
    _events.append({"id": _event_seq, "ts": round(time.time(), 3), "type": type, **data})


# ---------------------------------------------------------------------------
# REST API (FastAPI)
# ---------------------------------------------------------------------------

app = FastAPI(
    title="PixelPet API",
    description="Control your AI-driven desktop pet body runtime.",
    version="3.0.0",
    docs_url=None,
    redoc_url=None,
)


class BodyStatePatch(BaseModel):
    pose: str | None = None
    motion: str | None = None
    speed: float | None = Field(default=None, ge=0.1, le=5.0)
    target_x: float | None = None
    target_y: float | None = None
    hold_seconds: float | None = Field(default=None, gt=0.0)


class BodyStateResponse(BaseModel):
    ok: bool
    body_state: dict


class StatusResponse(BaseModel):
    action: str | None
    available: list[str]
    available_poses: list[str] = []
    available_motions: list[str] = []
    body_state: dict = {}
    fallback_active: bool = True
    controller: str = "openclaw"
    last_control_age: float | None = None
    hold_seconds_remaining: float | None = None
    port: int
    position: dict | None = None
    idle_seconds: float | None = None
    idle_state: bool = False


@app.get("/status", response_model=StatusResponse, summary="Get current pet state")
def get_status() -> StatusResponse:
    return StatusResponse(**_status)


@app.get("/events", summary="Get recent pet events")
def get_events(
    since_id: int | None = Query(default=None, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict]:
    """Returns events in ascending order, optionally after a given event id."""
    events = list(_events)
    if since_id is not None:
        events = [ev for ev in events if int(ev.get("id", 0)) > since_id]
    if len(events) > limit:
        events = events[-limit:]
    return events


@app.post("/body-state", response_model=BodyStateResponse, summary="Set body state patch")
def set_body_state(req: BodyStatePatch) -> BodyStateResponse:
    if all(getattr(req, key) is None for key in ("pose", "motion", "speed", "target_x", "target_y", "hold_seconds")):
        raise HTTPException(status_code=422, detail="At least one field is required.")

    available_poses = _status.get("available_poses") or _status.get("available") or []
    if req.pose is not None and available_poses and req.pose not in available_poses:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown pose '{req.pose}'. Available: {available_poses}",
        )

    available_motions = _status.get("available_motions") or []
    if req.motion is not None and available_motions and req.motion not in available_motions:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown motion '{req.motion}'. Available: {available_motions}",
        )

    if req.motion == "move_to" and (req.target_x is None or req.target_y is None):
        raise HTTPException(status_code=422, detail="motion=move_to requires target_x and target_y.")

    patch = req.model_dump(exclude_none=True)
    command_queue.put(patch)

    merged = dict(_status.get("body_state") or {})
    merged.update(patch)
    if merged.get("motion") != "move_to":
        merged.pop("target_x", None)
        merged.pop("target_y", None)
    if merged.get("motion") in {"idle", "stop"}:
        merged.pop("target_x", None)
        merged.pop("target_y", None)
        if merged["motion"] == "stop":
            merged["motion"] = "idle"
    return BodyStateResponse(ok=True, body_state=merged)


# ---------------------------------------------------------------------------
# Server bootstrap
# ---------------------------------------------------------------------------

DEFAULT_PORT = 37420


def start_server(port: int | None = None) -> int:
    """Launch REST API (uvicorn) in a daemon thread. Returns the port in use."""
    port = port or int(os.environ.get("PIXELPET_PORT", DEFAULT_PORT))

    def _run() -> None:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")

    threading.Thread(target=_run, daemon=True, name="pixelpet-api").start()
    return port
