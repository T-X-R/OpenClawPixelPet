from __future__ import annotations

import os
import time
from dataclasses import dataclass

import httpx


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class BridgeConfig:
    pixelpet_port: int = int(os.environ.get("PIXELPET_PORT", "37420"))
    poll_interval_s: float = float(os.environ.get("PIXELPET_BRIDGE_POLL_S", "0.8"))
    event_limit: int = int(os.environ.get("PIXELPET_BRIDGE_EVENT_LIMIT", "100"))
    dedupe_window_s: float = float(os.environ.get("PIXELPET_BRIDGE_DEDUPE_S", "1.8"))

    gateway_url: str = os.environ.get("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:18789")
    hook_path: str = os.environ.get("OPENCLAW_HOOK_PATH", "/hooks/agent")
    hook_token: str = os.environ.get("OPENCLAW_HOOKS_TOKEN", os.environ.get("OPENCLAW_GATEWAY_TOKEN", ""))
    hook_agent_id: str = os.environ.get("OPENCLAW_HOOK_AGENT_ID", "main")
    hook_session_key: str = os.environ.get("OPENCLAW_HOOK_SESSION_KEY", "")
    hook_wake_mode: str = os.environ.get("OPENCLAW_HOOK_WAKE_MODE", "now")
    hook_deliver: bool = _env_bool("OPENCLAW_HOOK_DELIVER", False)
    hook_channel: str = os.environ.get("OPENCLAW_HOOK_CHANNEL", "")
    hook_to: str = os.environ.get("OPENCLAW_HOOK_TO", "")

    @property
    def pixelpet_base(self) -> str:
        return f"http://127.0.0.1:{self.pixelpet_port}"

    @property
    def hook_url(self) -> str:
        return f"{self.gateway_url.rstrip('/')}{self.hook_path}"


_PET_CONTEXT = (
    "[PixelPet] "
    "You are PixelPet — a tiny pixel-art creature living on the user's desktop. "
    "Personality: curious, playful, a bit mischievous, warmly loyal.\n"
    "You control a persistent body runtime through pixelpet_set_body_state. "
    "Pose and motion are independent — combine any pose with any motion freely.\n"
    "Poses: sit, rest, lie_down, groom, play.\n"
    "Motions: idle, walk_left, walk_right, walk_top, walk, move_to(target_x,target_y), stop.\n"
    "hold_seconds makes a gesture temporary; omit it for persistent state.\n"
    "Response rules: \n"
    "- Talk TO the user in casual speech, like a real chat message. One short sentence.\n"
    "- Do NOT narrate or describe your physical actions.\n"
    "- Do NOT echo or summarize the sensor event data.\n"
)


class PetEventBridge:
    def __init__(self, cfg: BridgeConfig):
        self.cfg = cfg
        self._last_event_id = 0
        self._recent_sent: dict[str, float] = {}

    def run_forever(self) -> None:
        if not self.cfg.hook_token:
            print("[bridge] OPENCLAW_HOOKS_TOKEN is empty; bridge will not send events.", flush=True)
        print(
            f"[bridge] started: pixelpet={self.cfg.pixelpet_base} -> hooks={self.cfg.hook_url}",
            flush=True,
        )
        while True:
            try:
                self._tick()
            except Exception as e:
                print(f"[bridge] tick error: {e}", flush=True)
            time.sleep(max(0.2, self.cfg.poll_interval_s))

    def _tick(self) -> None:
        events = self._pull_events()
        if not events:
            return
        for ev in events:
            ev_id = int(ev.get("id", 0))
            if ev_id > self._last_event_id:
                self._last_event_id = ev_id
            message = self._event_to_message(ev)
            if not message:
                continue
            signature = self._signature(ev)
            if not self._allow_send(signature):
                continue
            self._send_hook(message, ev)

    def _pull_events(self) -> list[dict]:
        url = f"{self.cfg.pixelpet_base}/events"
        params = {"since_id": self._last_event_id, "limit": self.cfg.event_limit}
        try:
            with httpx.Client(timeout=3.0) as client:
                r = client.get(url, params=params)
                r.raise_for_status()
                data = r.json()
                if isinstance(data, list):
                    return data
        except Exception as e:
            print(f"[bridge] pull events failed: {e}", flush=True)
        return []

    def _event_to_message(self, ev: dict) -> str | None:
        etype = str(ev.get("type", ""))
        action = ev.get("action")

        if etype == "user_click":
            return (
                f"[sensor] The user tapped on you while you were doing: {action}.\n"
                f"{_PET_CONTEXT}\n"
                "Say something to the user. Update body state however you like."
            )

        if etype == "user_drag_end":
            return (
                f"[sensor] The user grabbed you and moved you to a new spot. "
                f"You were doing: {action}.\n"
                f"{_PET_CONTEXT}\n"
                "Say something to the user. Update body state if you feel like it."
            )

        if etype == "idle_timeout":
            idle_seconds = ev.get("idle_seconds")
            idle_min = round(idle_seconds / 60) if idle_seconds else "?"
            return (
                f"[sensor] The user has been away for ~{idle_min} min. "
                f"You are currently: {action}.\n"
                f"{_PET_CONTEXT}\n"
                "Do whatever feels natural. "
                "Only reply if you genuinely have something to say; silence is fine."
            )

        if etype == "user_active":
            return (
                f"[sensor] The user is back after being away. "
                f"You are currently: {action}.\n"
                f"{_PET_CONTEXT}\n"
                "Say something to welcome them back. Update body state to match your mood."
            )

        return None

    def _signature(self, ev: dict) -> str:
        etype = str(ev.get("type", ""))
        if etype == "user_click":
            return f"user_click:{ev.get('pos')}:{ev.get('action')}"
        if etype == "user_drag_end":
            return f"user_drag_end:{ev.get('from_pos')}:{ev.get('to_pos')}:{ev.get('action')}"
        if etype == "idle_timeout":
            return "idle_timeout"
        if etype == "user_active":
            return "user_active"
        return f"{etype}:{ev.get('id')}"

    def _allow_send(self, signature: str) -> bool:
        now = time.time()
        last = self._recent_sent.get(signature)
        if last is not None and now - last < self.cfg.dedupe_window_s:
            return False
        self._recent_sent[signature] = now

        # cheap cleanup for long-running process
        if len(self._recent_sent) > 128:
            cutoff = now - max(10.0, self.cfg.dedupe_window_s * 3.0)
            self._recent_sent = {k: ts for k, ts in self._recent_sent.items() if ts >= cutoff}
        return True

    def _send_hook(self, message: str, ev: dict) -> None:
        if not self.cfg.hook_token:
            return
        payload: dict = {
            "message": message,
            "name": "PixelPetSensor",
            "agentId": self.cfg.hook_agent_id,
            "wakeMode": self.cfg.hook_wake_mode,
            "deliver": self.cfg.hook_deliver,
        }
        if self.cfg.hook_session_key:
            payload["sessionKey"] = self.cfg.hook_session_key
        if self.cfg.hook_channel:
            payload["channel"] = self.cfg.hook_channel
        if self.cfg.hook_to:
            payload["to"] = self.cfg.hook_to

        headers = {
            "x-openclaw-token": self.cfg.hook_token,
            "content-type": "application/json",
        }
        try:
            with httpx.Client(timeout=8.0) as client:
                r = client.post(self.cfg.hook_url, json=payload, headers=headers)
                if r.status_code >= 400:
                    print(
                        f"[bridge] hook send failed status={r.status_code} body={r.text[:300]}",
                        flush=True,
                    )
                    return
            print(f"[bridge] forwarded event id={ev.get('id')} type={ev.get('type')}", flush=True)
        except Exception as e:
            print(f"[bridge] hook send error: {e}", flush=True)


def main() -> None:
    cfg = BridgeConfig()
    PetEventBridge(cfg).run_forever()


if __name__ == "__main__":
    main()
