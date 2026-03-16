from __future__ import annotations

import math
import os
import time
from pathlib import Path

import objc
from AppKit import (
    NSApplication,
    NSBackingStoreBuffered,
    NSImage,
    NSMakeRect,
    NSStatusWindowLevel,
    NSWindow,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowStyleMaskBorderless,
)
from Foundation import NSTimer

from .api import command_queue, emit_event, start_server, update_status
from .assets import list_animations
from .gifdecode import decode_gif
from .hitview import AlphaHitImageView, set_interaction_callbacks

# Seconds of user inactivity before we consider them "idle"
_IDLE_THRESHOLD = 120.0
_DEFAULT_CONTROL_TIMEOUT_S = 45.0
_BASE_MOVE_SPEED_PX_S = 140.0


class PixelPetController(objc.python_method):
    pass


class PixelPetController(objcNSObject := objc.lookUpClass("NSObject")):
    def init(self):
        self = objc.super(PixelPetController, self).init()
        if self is None:
            return None

        self._anims = {a.name: a.path for a in list_animations()}
        self._available = sorted(self._anims.keys())
        self._available_poses = [name for name in ("sit", "rest", "lie_down", "groom", "play") if name in self._anims]
        if not self._available_poses and self._available:
            self._available_poses = [self._available[0]]

        self._available_motions = ["idle", "stop", "move_to"]
        for motion_name in ("walk", "walk_left", "walk_right", "walk_top"):
            if motion_name in self._anims and motion_name not in self._available_motions:
                self._available_motions.append(motion_name)

        self._fallback_pose = "rest" if "rest" in self._available else "sit"
        if self._fallback_pose not in self._available and self._available:
            self._fallback_pose = self._available[0]

        self._body_state: dict = {
            "pose": self._fallback_pose,
            "motion": "idle",
            "speed": 1.0,
            "target_x": None,
            "target_y": None,
        }
        self._fallback_active = True
        self._control_timeout_s = float(os.environ.get("PIXELPET_CONTROL_TIMEOUT_S", _DEFAULT_CONTROL_TIMEOUT_S))
        self._last_control_ts = 0.0
        self._state_hold_until: float | None = None
        self._last_motion_tick_ts = time.time()

        self._current_action: str | None = None
        self._frames = []
        self._frame_i = 0
        self._last_input_ts = time.time()
        self._event_monitors = []

        self._idle_state: bool = False
        self._window = None
        self._image_view = None
        return self

    def applicationDidFinishLaunching_(self, _notif):
        self._setup_window()
        self._setup_input_monitors()

        # Register interaction callbacks for click/drag semantics.
        set_interaction_callbacks(
            on_pointer_down=self._on_pointer_down,
            on_drag_finish=self._on_drag_finish,
        )

        # Start REST API in a background thread.
        self._api_port = start_server()
        print(f"[api] http://127.0.0.1:{self._api_port}/status", flush=True)

        # 10Hz body runtime tick
        self._decision_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.1, self, objc.selector(self.decisionTick_, signature=b"v@:@"), None, True
        )

        self._render_next_frame()

    def _on_pointer_down(self, data: dict):
        """Pointer down on the pet body."""
        self._last_input_ts = time.time()
        emit_event(
            "user_pointer_down",
            pos=data.get("pos"),
            action=self._current_action,
        )

    def _on_drag_finish(self, data: dict):
        """Called when drag/click gesture on pet body ends."""
        self._last_input_ts = time.time()
        start_pos = data.get("start_pos")
        end_pos = data.get("end_pos")
        moved = bool(data.get("moved"))
        duration_ms = data.get("duration_ms")

        if moved:
            emit_event(
                "user_drag_start",
                pos=start_pos,
                action=self._current_action,
            )
            emit_event(
                "user_drag_end",
                from_pos=start_pos,
                to_pos=end_pos,
                duration_ms=duration_ms,
                action=self._current_action,
            )
            # User drag has priority over motion commands at this instant.
            self._body_state["motion"] = "idle"
            self._body_state["target_x"] = None
            self._body_state["target_y"] = None
            return

        emit_event(
            "user_click",
            pos=end_pos or start_pos,
            action=self._current_action,
        )

    def _setup_input_monitors(self):
        """Track user activity so we can decide when we're "idle"."""

        def bump(_event):
            self._last_input_ts = time.time()

        try:
            from AppKit import (
                NSEvent,
                NSEventMaskKeyDown,
                NSEventMaskLeftMouseDown,
                NSEventMaskLeftMouseDragged,
                NSEventMaskLeftMouseUp,
                NSEventMaskMouseMoved,
                NSEventMaskRightMouseDown,
            )

            mask = (
                NSEventMaskMouseMoved
                | NSEventMaskLeftMouseDown
                | NSEventMaskLeftMouseDragged
                | NSEventMaskLeftMouseUp
                | NSEventMaskRightMouseDown
                | NSEventMaskKeyDown
            )

            mon = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(mask, bump)
            if mon is not None:
                self._event_monitors.append(mon)
        except Exception:
            pass

    def _setup_window(self):
        from AppKit import NSScreen

        size = 100.0
        margin = 16.0
        screen = NSScreen.mainScreen()
        frame = screen.visibleFrame() if screen is not None else None
        if frame is None:
            x, y = 200.0, 200.0
        else:
            x = frame.origin.x + frame.size.width - size - margin
            y = frame.origin.y + margin

        rect = NSMakeRect(x, y, size, size)
        w = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect,
            NSWindowStyleMaskBorderless,
            NSBackingStoreBuffered,
            False,
        )
        w.setOpaque_(False)
        w.setBackgroundColor_(objc.nil)
        w.setLevel_(NSStatusWindowLevel)
        w.setHasShadow_(False)
        w.setIgnoresMouseEvents_(False)
        w.setMovableByWindowBackground_(False)
        w.setMovable_(True)
        w.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
        )

        iv = AlphaHitImageView.alloc().initWithFrame_(rect)
        iv.setImageScaling_(0)
        w.setContentView_(iv)
        w.makeKeyAndOrderFront_(None)

        self._window = w
        self._image_view = iv

    def _visible_bounds(self):
        from AppKit import NSScreen

        screen = NSScreen.mainScreen()
        return screen.visibleFrame() if screen is not None else None

    def _current_position(self) -> dict | None:
        if self._window is None:
            return None
        frame = self._window.frame()
        return {"x": round(frame.origin.x), "y": round(frame.origin.y)}

    def _apply_body_patch(self, patch: dict) -> None:
        if "hold_seconds" in patch:
            try:
                hold_seconds = float(patch["hold_seconds"])
            except Exception:
                hold_seconds = 0.0
            self._state_hold_until = time.time() + max(0.0, hold_seconds)
        else:
            # Any new patch without hold_seconds switches back to persistent mode.
            self._state_hold_until = None

        if "pose" in patch and patch["pose"] in self._available_poses:
            self._body_state["pose"] = patch["pose"]

        if "speed" in patch:
            try:
                speed = float(patch["speed"])
            except Exception:
                speed = self._body_state.get("speed", 1.0)
            self._body_state["speed"] = max(0.1, min(5.0, speed))

        if "motion" in patch:
            motion = patch["motion"]
            if motion == "stop":
                motion = "idle"
            if motion in self._available_motions:
                self._body_state["motion"] = motion

        if "target_x" in patch:
            self._body_state["target_x"] = float(patch["target_x"])
        if "target_y" in patch:
            self._body_state["target_y"] = float(patch["target_y"])

        if self._body_state.get("motion") == "move_to":
            if self._body_state.get("target_x") is None or self._body_state.get("target_y") is None:
                self._body_state["motion"] = "idle"
        else:
            self._body_state["target_x"] = None
            self._body_state["target_y"] = None

    def _apply_fallback_idle(self) -> None:
        self._body_state["pose"] = self._fallback_pose
        self._body_state["motion"] = "idle"
        self._body_state["speed"] = 1.0
        self._body_state["target_x"] = None
        self._body_state["target_y"] = None

    def _motion_tick(self, dt: float) -> None:
        if self._window is None:
            return

        motion = self._body_state.get("motion", "idle")
        if motion in {"idle", "stop"}:
            return

        bounds = self._visible_bounds()
        if bounds is None:
            return

        frame = self._window.frame()
        speed = _BASE_MOVE_SPEED_PX_S * float(self._body_state.get("speed", 1.0))
        step = max(0.0, speed * max(0.0, dt))
        dx = 0.0
        dy = 0.0

        if motion == "walk_left":
            dx = -step
        elif motion == "walk_right":
            dx = step
        elif motion == "walk_top":
            dy = step
        elif motion == "walk":
            dx = step * 0.7
        elif motion == "move_to":
            tx = self._body_state.get("target_x")
            ty = self._body_state.get("target_y")
            if tx is None or ty is None:
                self._body_state["motion"] = "idle"
                return
            vx = float(tx) - float(frame.origin.x)
            vy = float(ty) - float(frame.origin.y)
            dist = math.hypot(vx, vy)
            if dist <= 1.0:
                self._body_state["motion"] = "idle"
                self._body_state["target_x"] = None
                self._body_state["target_y"] = None
                return
            scale = min(1.0, step / dist) if dist > 0 else 0.0
            dx = vx * scale
            dy = vy * scale
        else:
            return

        min_x = float(bounds.origin.x)
        max_x = float(bounds.origin.x + bounds.size.width - frame.size.width)
        min_y = float(bounds.origin.y)
        max_y = float(bounds.origin.y + bounds.size.height - frame.size.height)
        nx = max(min_x, min(float(frame.origin.x) + dx, max_x))
        ny = max(min_y, min(float(frame.origin.y) + dy, max_y))

        if abs(nx - float(frame.origin.x)) < 0.01 and abs(ny - float(frame.origin.y)) < 0.01:
            return

        frame.origin.x = nx
        frame.origin.y = ny
        self._window.setFrame_display_(frame, False)

    def _resolve_action_for_state(self) -> str | None:
        pose = self._body_state.get("pose")
        motion = self._body_state.get("motion", "idle")

        if motion in {"walk", "walk_left", "walk_right", "walk_top"} and motion in self._anims:
            return motion

        if motion == "move_to":
            if self._window is not None:
                frame = self._window.frame()
                tx = self._body_state.get("target_x")
                ty = self._body_state.get("target_y")
                if tx is not None and ty is not None:
                    dx = float(tx) - float(frame.origin.x)
                    dy = float(ty) - float(frame.origin.y)
                    if abs(dx) > abs(dy):
                        if dx < 0 and "walk_left" in self._anims:
                            return "walk_left"
                        if dx > 0 and "walk_right" in self._anims:
                            return "walk_right"
                    if dy > 0 and "walk_top" in self._anims:
                        return "walk_top"
                    if "walk" in self._anims:
                        return "walk"

        if pose in self._anims:
            return pose
        if self._fallback_pose in self._anims:
            return self._fallback_pose
        if self._available:
            return self._available[0]
        return None

    def _body_state_snapshot(self) -> dict:
        return {
            "pose": self._body_state.get("pose"),
            "motion": self._body_state.get("motion"),
            "speed": round(float(self._body_state.get("speed", 1.0)), 3),
            "target_x": self._body_state.get("target_x"),
            "target_y": self._body_state.get("target_y"),
        }

    def decisionTick_(self, _timer):
        now = time.time()
        idle_seconds = now - self._last_input_ts

        # --- Idle / active state transitions ---
        currently_idle = idle_seconds > _IDLE_THRESHOLD
        if currently_idle and not self._idle_state:
            emit_event("idle_timeout", idle_seconds=round(idle_seconds, 1))
            self._idle_state = True
        elif not currently_idle and self._idle_state:
            emit_event("user_active")
            self._idle_state = False

        # --- OpenClaw body-state patch queue ---
        latest_patch: dict | None = None
        try:
            while True:
                latest_patch = command_queue.get_nowait()
        except Exception:
            pass

        if latest_patch is not None:
            self._apply_body_patch(latest_patch)
            self._last_control_ts = now
            if self._fallback_active:
                self._fallback_active = False
                emit_event("control_resumed", source="openclaw")

        hold_seconds_remaining: float | None = None
        if self._state_hold_until is not None:
            hold_seconds_remaining = max(0.0, self._state_hold_until - now)
            if hold_seconds_remaining <= 0.0:
                self._state_hold_until = None
                hold_seconds_remaining = None
                if not self._fallback_active:
                    self._fallback_active = True
                    self._apply_fallback_idle()
                    emit_event("control_fallback", pose=self._fallback_pose, reason="hold_expired")

        last_control_age = None if self._last_control_ts <= 0 else (now - self._last_control_ts)
        stale = self._last_control_ts <= 0 or (last_control_age is not None and last_control_age > self._control_timeout_s)
        if stale and not self._fallback_active:
            self._fallback_active = True
            self._apply_fallback_idle()
            emit_event("control_fallback", pose=self._fallback_pose, reason="timeout")

        dt = max(0.0, now - self._last_motion_tick_ts)
        self._last_motion_tick_ts = now
        self._motion_tick(dt)

        action = self._resolve_action_for_state()
        if action is not None and action != self._current_action:
            source = "fallback" if self._fallback_active else "openclaw"
            emit_event("action_changed", from_action=self._current_action, to=action, source=source)
            self._load_action(action)

        update_status(
            self._current_action,
            self._available,
            self._api_port,
            available_poses=self._available_poses,
            available_motions=self._available_motions,
            body_state=self._body_state_snapshot(),
            fallback_active=self._fallback_active,
            controller="openclaw",
            last_control_age=last_control_age if last_control_age is not None else 0.0,
            hold_seconds_remaining=hold_seconds_remaining,
            position=self._current_position(),
            idle_seconds=round(idle_seconds, 1),
            idle_state=self._idle_state,
        )

    def _load_action(self, action: str):
        path: Path = self._anims[action]
        self._frames = decode_gif(path)
        self._frame_i = 0
        self._current_action = action

        try:
            if getattr(self, "_render_timer", None) is not None:
                self._render_timer.invalidate()
                self._render_timer = None
        except Exception:
            self._render_timer = None

        if self._frames:
            target = 100
            frame = self._window.frame()
            frame.size.width = target
            frame.size.height = target
            self._window.setFrame_display_(frame, True)
            self._image_view.setFrame_(NSMakeRect(0, 0, target, target))

        self._render_next_frame()

    def _render_next_frame(self):
        if not self._frames:
            action = self._resolve_action_for_state()
            if action is not None:
                self._load_action(action)
            return

        f = self._frames[self._frame_i]
        from AppKit import NSBitmapImageRep

        rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
            None,
            f.width,
            f.height,
            8,
            4,
            True,
            False,
            "NSDeviceRGBColorSpace",
            f.width * 4,
            32,
        )

        mv = memoryview(rep.bitmapData())
        mv[: len(f.rgba_bytes)] = f.rgba_bytes

        img = NSImage.alloc().initWithSize_((f.width, f.height))
        img.addRepresentation_(rep)
        self._last_rep = rep
        self._last_img = img
        self._image_view.setImage_(img)

        self._frame_i = (self._frame_i + 1) % max(1, len(self._frames))
        delay = f.duration_ms / 1000.0

        try:
            if getattr(self, "_render_timer", None) is not None:
                self._render_timer.invalidate()
                self._render_timer = None
        except Exception:
            self._render_timer = None

        sel = objc.selector(self.renderTimerFire_, signature=b"v@:@")
        self._render_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            delay, self, sel, None, False,
        )

    def renderTimerFire_(self, _timer):
        self._render_next_frame()


def main():
    app = NSApplication.sharedApplication()
    controller = PixelPetController.alloc().init()
    app.setDelegate_(controller)
    app.run()
