# PixelPet

An AI-driven pixel-art desktop pet for macOS. PixelPet lives on your screen as a tiny animated creature that can be observed and controlled by an AI agent through a local REST API.

## Features

- Transparent, always-on-top pixel-art pet rendered from GIF sprite sheets
- Alpha hit-testing -- only the visible pet body is clickable/draggable, not the transparent background
- Body state model with independent **pose** and **motion** axes, plus timed `hold_seconds` for temporary gestures
- Automatic fallback to idle when the AI controller goes silent
- User interaction detection: click, drag, idle timeout, return from idle
- Optional event bridge that forwards pet sensor events to an [OpenClaw](https://openclaw.com) Gateway, enabling an AI agent to react to user interactions in real time

## Requirements

- macOS
- Python >= 3.11, < 3.12
- [uv](https://docs.astral.sh/uv/)

## Installation

```bash
git clone https://github.com/<your-username>/PixelPet.git
cd PixelPet
uv sync
```

## Usage

```bash
./scripts/start.sh    # start the pet in the background
./scripts/stop.sh     # stop
```

Once running, the pet appears near the bottom-right corner of your main display. You can drag it anywhere.

## How It Works

PixelPet runs an AppKit window with a 10 Hz decision loop. A FastAPI server runs in a background thread, accepting body-state commands via HTTP. The main loop consumes commands from a thread-safe queue, updates the pose/motion state, moves the window according to the active motion, resolves which GIF animation to play, and renders frames.

```
                          POST /body-state
  AI agent / script  ──────────────────────►  REST API (FastAPI)
                                                   │
                                              command_queue
                                                   │
                                                   ▼
                                             PixelPet main loop
                                            (AppKit + 10 Hz tick)
                                                   │
                                              emit_event()
                                                   │
                                                   ▼
                                             GET /events
  OpenClaw Bridge    ◄──────────────────────  (poll loop)
       │
       └──► OpenClaw Gateway webhook ──► AI agent reacts
```

When no control command arrives for 45 seconds (configurable via `PIXELPET_CONTROL_TIMEOUT_S`), or a `hold_seconds` timer expires, the pet falls back to a resting idle pose automatically.

## REST API

The pet listens on `http://127.0.0.1:37420` by default. Set `PIXELPET_PORT` to change it.

### `GET /status`

Returns the full state snapshot:

| Field | Description |
|-------|-------------|
| `action` | Currently playing animation name |
| `available` | All available animation names |
| `available_poses` | Settable pose names (`sit`, `rest`, `lie_down`, `groom`, `play`) |
| `available_motions` | Settable motion names (`idle`, `stop`, `walk`, `walk_left`, `walk_right`, `walk_top`, `move_to`) |
| `body_state` | Current `{pose, motion, speed, target_x, target_y}` |
| `fallback_active` | `true` if no active AI control |
| `hold_seconds_remaining` | Seconds left on a temporary hold, or `null` |
| `position` | Window position `{x, y}` in screen coordinates |
| `idle_seconds` | Seconds since last user input |
| `idle_state` | `true` if user is considered idle (> 120 s) |

### `GET /events`

Returns up to 50 recent events (oldest first). Supports `since_id` and `limit` query parameters for incremental polling.

### `POST /body-state`

Applies an incremental patch to the pet's body state. All fields are optional, but at least one must be provided.

```json
{
  "pose": "play",
  "motion": "walk_right",
  "speed": 1.5,
  "hold_seconds": 3.0
}
```

| Field | Type | Description |
|-------|------|-------------|
| `pose` | `string` | One of `available_poses` |
| `motion` | `string` | One of `available_motions`. `move_to` requires `target_x`/`target_y`. `stop` is an alias for `idle`. |
| `speed` | `float` | Movement speed multiplier, 0.1 -- 5.0 (default 1.0, base ~140 px/s) |
| `target_x`, `target_y` | `float` | Target screen coordinates for `move_to` motion |
| `hold_seconds` | `float` | Makes this state temporary; the pet reverts to fallback after the timer expires. Omit for persistent state. |

## Events

Events are stored in a circular buffer (last 50) and exposed via `GET /events`.

| Type | Key Fields | Trigger |
|------|------------|---------|
| `action_changed` | `from_action`, `to`, `source` | Animation switched |
| `user_pointer_down` | `pos` | User pressed down on pet body |
| `user_click` | `pos` | User clicked the pet |
| `user_drag_start` | `pos` | User started dragging the pet |
| `user_drag_end` | `from_pos`, `to_pos`, `duration_ms` | User finished dragging |
| `idle_timeout` | `idle_seconds` | User inactive for > 120 s |
| `user_active` | -- | User returned from idle |
| `control_resumed` | `source` | AI control resumed after fallback |
| `control_fallback` | `pose`, `reason` | Entered fallback (`timeout` or `hold_expired`) |

## OpenClaw Bridge (Optional)

The bridge is a separate process that polls the pet's event stream and forwards relevant events (click, drag, idle, user return) to an OpenClaw Gateway as webhook messages. This creates a feedback loop: user interacts with pet -> bridge notifies AI -> AI sends new body-state commands.

```bash
# 1. Copy and edit the config
cp config/bridge.example.env config/bridge.local.env

# 2. Start everything
./scripts/start_all.sh

# 3. Stop everything
./scripts/stop_all.sh
```

Or manage them independently:

```bash
./scripts/start_bridge.sh
./scripts/stop_bridge.sh
```

See `config/bridge.example.env` for all available configuration options.

## Adding Animations

Place GIF files in the `assets/` directory. The filename (without `.gif`) becomes the animation name. For example, `assets/sit.gif` registers as the `sit` pose.

Standard pose names: `sit`, `rest`, `lie_down`, `groom`, `play`

Standard motion names: `walk`, `walk_left`, `walk_right`, `walk_top`

## Project Structure

```
PixelPet/
├── app/
│   ├── main.py              # AppKit window, animation loop, decision tick
│   ├── api.py               # FastAPI REST server (runs in daemon thread)
│   ├── openclaw_bridge.py   # Event bridge to OpenClaw Gateway
│   ├── hitview.py           # Alpha hit-test NSImageView + drag/click handling
│   ├── gifdecode.py         # GIF -> RGBA frame decoder (Pillow)
│   └── assets.py            # Animation asset discovery
├── assets/                  # GIF sprite sheets (*.gif)
├── config/
│   └── bridge.example.env   # Bridge configuration template
├── scripts/                 # Start/stop shell scripts
└── pyproject.toml
```

## License

MIT
