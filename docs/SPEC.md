# REFLEX — screen-native visual skill compiler

Demonstrate once → compile to a visual state machine → replay fast with ZERO LLM calls →
pause instead of guess → Claude repairs → repair becomes permanent.

## 1. Repository reuse map

| Need | Existing code | Status |
|---|---|---|
| Mouse/keyboard execution | `organism/kernel/motor.py` — sole emitter, DPI-proof 0-65535 space, move/settle/click phases, SAFE_STOP, single-pending, journaled | REUSE as-is |
| Journaling | `organism/journal/journal.py` — append-only, fsync, torn-line safe, **measured 1000/1000** | REUSE (extend event set) |
| Screen capture | `mss` (installed) for recording + matching; ffmpeg ddagrab tape exists for 24/7 later | NEW thin wrapper |
| Input recording | `pynput` (installed this session) global listeners | NEW `recorder.py` |
| Visual matching | `cv2` (installed) — template matching (TM_CCOEFF_NORMED) grayscale multi-scale | NEW `matcher.py` |
| Model calls | `~/.claude/scripts/sub.mjs` pattern: `claude -p` with image paths | REUSE pattern in `repair.py` |
| Mock application | `organism/calib/calib_app.py` patterns (tkinter, ground-truth hits.jsonl) | NEW `mock_app.py` (benchmark spec) |
| Emergency stop | SAFE_STOP file honored by motor before every emission | REUSE |
| Idle gating | `run_when_idle.py` pattern | REUSE for benchmark runs |

## 2. Smallest vertical slice

`reflex.py record <name>` → do the workflow → Ctrl+Alt+F10 stops →
`reflex.py compile <name>` → `reflex.py run <name>` replays it.

- Recorder: mss ring at ~12fps during recording + pynput hooks; every click/key stores
  frame_before/frame_after PNG refs + normalized cursor.
- Segmenter+compiler (one pass): merge moves into clicks, coalesce keystrokes into
  type-steps, drop idle gaps; per click cut a 64px target crop + 220px context crop from
  frame_before at the click point.
- Runtime: per state — wait until target matches on the live screen (multi-scale template,
  threshold 0.87) → motor click at MATCHED position (never recorded position) → verify
  transition = frame changed in expected region OR next state's anchor appears → advance.
  Timeouts pause the run (BLOCKED), never guess.
- Repair: on BLOCKED, write repair package (state, expected crop, live frame, history) →
  `claude -p` with images → one repair plan JSON → cautious execution → patch saved as
  `skills/<name>/v<n+1>.json`, old version kept.

## 3. Files to modify
- `organism/journal/journal.py` — add events: `recording_started/stopped`,
  `skill_compiled`, `state_matched`, `state_blocked`, `repair_plan`, `skill_patched`.

## 4. New files
- `organism/reflex/recorder.py` · `compiler.py` · `matcher.py` · `runtime.py` ·
  `repair.py` · `mock_app.py` · `reflex.py` (CLI)
- `organism/skills/<name>/` — trajectory.jsonl, frames/, v1.json…

## 5. Local benchmark workflow (mock_app.py)
Window at randomized position; menu bar → dropdown item → form with 3 fields → NEXT →
second screen with rows (target row randomized) → row click opens modal → CONFIRM →
1-3s randomized spinner → DONE banner. One run in ~15 steps. Optional surprise modal
injected via cmd.json (the repair test). Minor color jitter per launch. Ground truth to
hits.jsonl exactly like calib_app.

## 6. Baseline measurements (current architecture, measured this week)
- eyes.mjs serial loop: **~25-30s per action** (cold `claude -p` per look; 40-step run
  blew a 590s timeout). LLM calls per action: **≥1** — the number Reflex takes to 0.
- Motor mechanical precision (last night, ground-truth scored): **96/100 static clicks,
  257ms/click**, 0 refused; 4 misses = calib-app occlusion bug, not scatter.
- UIA job lane: minutes per form, breaks per wording variant — the negative control.

Gates stay as specced; nothing is claimed without a benchmark table from hits.jsonl + journal.
