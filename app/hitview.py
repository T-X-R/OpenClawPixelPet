from __future__ import annotations

import time
from typing import Callable, Optional

import objc
from AppKit import NSEvent, NSImageView

# Module-level interaction callbacks — set by main.py after the window is created.
_on_pointer_down: Optional[Callable[[dict], None]] = None
_on_drag_finish: Optional[Callable[[dict], None]] = None


def set_interaction_callbacks(
    *,
    on_pointer_down: Callable[[dict], None] | None = None,
    on_drag_finish: Callable[[dict], None] | None = None,
) -> None:
    """Register callbacks for pointer and drag interactions on the pet."""
    global _on_pointer_down, _on_drag_finish
    _on_pointer_down = on_pointer_down
    _on_drag_finish = on_drag_finish


class AlphaHitImageView(NSImageView):
    """Only treat clicks on non-transparent pixels as hits.

    This enables: transparent background + only pet body draggable/clickable.
    """

    def _screen_mouse_pos(self) -> dict[str, float]:
        point = NSEvent.mouseLocation()
        return {"x": float(point.x), "y": float(point.y)}

    def hitTest_(self, point):
        img = self.image()
        if img is None:
            return None

        # Convert point into view-local pixel coordinates
        bounds = self.bounds()
        w = int(bounds.size.width)
        h = int(bounds.size.height)
        if w <= 0 or h <= 0:
            return None

        x = int(point.x)
        y = int(point.y)
        if x < 0 or y < 0 or x >= w or y >= h:
            return None

        rep = img.bestRepresentationForRect_context_hints_(bounds, None, None)
        if rep is None:
            return None

        # NSBitmapImageRep: samples are [r,g,b,a] typically; we just need alpha.
        try:
            color = rep.colorAtX_y_(x, y)
            if color is None:
                return None
            alpha = float(color.alphaComponent())
        except Exception:
            # If we can't sample, fall back to normal hit behavior
            return objc.super(AlphaHitImageView, self).hitTest_(point)

        if alpha <= 0.05:
            return None

        return objc.super(AlphaHitImageView, self).hitTest_(point)

    def mouseDown_(self, event):
        win = self.window()
        if win is None:
            return

        start_ts = time.time()
        frame = win.frame()
        start_pos = {"x": round(frame.origin.x), "y": round(frame.origin.y)}

        if _on_pointer_down is not None:
            try:
                _on_pointer_down({"pos": start_pos, "ts": round(start_ts, 3)})
            except Exception:
                pass

        self._drag_state = {
            "start_ts": start_ts,
            "start_pos": start_pos,
            "start_mouse": self._screen_mouse_pos(),
            "moved": False,
        }

    def mouseDragged_(self, event):
        win = self.window()
        state = getattr(self, "_drag_state", None)
        if win is None or state is None:
            return

        current_mouse = self._screen_mouse_pos()
        dx = current_mouse["x"] - state["start_mouse"]["x"]
        dy = current_mouse["y"] - state["start_mouse"]["y"]

        frame = win.frame()
        frame.origin.x = state["start_pos"]["x"] + dx
        frame.origin.y = state["start_pos"]["y"] + dy
        win.setFrame_display_(frame, False)

        if max(abs(dx), abs(dy)) >= 2:
            state["moved"] = True

    def mouseUp_(self, event):
        win = self.window()
        state = getattr(self, "_drag_state", None)
        self._drag_state = None
        if win is None or state is None:
            return

        end_ts = time.time()
        end_frame = win.frame()
        end_pos = {"x": round(end_frame.origin.x), "y": round(end_frame.origin.y)}
        moved = bool(state.get("moved"))

        if _on_drag_finish is not None:
            try:
                _on_drag_finish(
                    {
                        "start_pos": state["start_pos"],
                        "end_pos": end_pos,
                        "moved": moved,
                        "duration_ms": round((end_ts - state["start_ts"]) * 1000, 1),
                        "ts": round(end_ts, 3),
                    }
                )
            except Exception:
                pass
