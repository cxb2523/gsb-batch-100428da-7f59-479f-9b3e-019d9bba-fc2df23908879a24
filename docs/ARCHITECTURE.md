# ORGANISM — screen-native autonomy kernel

Interface is immutable: pixels in, mouse/keyboard out. No DOM, no accessibility tree, no
browser automation, no app APIs for task completion. Internal logging, inference, process
supervision and test infra are permitted.

## 1. Current architecture map (what exists today, 2026-07-31)

```
   [screen]                                    [input]
      │                                           ▲
      ▼                                           │
  operator :7788  ── /screenshot /windows ── /click /type /key /scroll
  (marco-operator, Python, pythonw run.py --serve --supervised)
      │                    ▲
      ▼                    │
  eyes.mjs  ─────── the serial loop ───────────────┘
  (screenshot → claude -p [vision] → batch of ≤10 actions → screenshot)
      │
  sub.mjs  (one brain lane: spawns `claude -p` per call, ~cold boot each time)

  Adjacent, working, out of kernel scope:
  · screen tape: ffmpeg ddagrab 24/7, 0.4% CPU  → capture substrate candidate
  · bus :7799 (bus.mjs)                          → event transport candidate
  · told.mjs attention ledger                    → notification discipline
  · UIA lane (ats-apply.mjs) + relay DOM lane (ats-dom.mjs) → job pipeline,
    KEEPS RUNNING in parallel; not part of this kernel, will not be extended.
```

The serial loop IS the anti-pattern the spec names: planner, grounding and motor are one
fused model call; capture blocks on demand; nothing verifies transitions except the next
model look; no staleness concept; no journal beyond flat logs.

## 2. Exact causes of latency and imprecision (each traced, last 24h)

| # | Cause | Evidence |
|---|-------|----------|
| L1 | Cold `claude -p` per look: ~25-30s/step | eyes.mjs runs 2026-07-30; 40-step run exceeded a 590s shell timeout (exit 143) |
| L2 | Look→act gap across turns: ~25s between reading a frame and clicking it | Greenhouse dropdown closed between turns; identical two clicks 700ms apart in one process worked first try |
| L3 | Blocking on-demand capture: every action waits for a fresh screenshot round-trip | /screenshot per batch in eyes.mjs; no ring, no frame IDs, no drop policy |
| P1 | No transition verification: click success assumed from HTTP 200 | apply.mjs logged `modal-opened` for a modal that never opened; Ashby refusal contained the "success" words "your application" |
| P2 | No staleness gate: actions fire against a screen that changed | Chrome autofill dropdown ate the NEXT click → "<name><autofilled email>" in the name field |
| P3 | Coordinate trust without revalidation | LinkedIn URL field at y≈1379 under the taskbar: typed 200-OK, field read back empty |
| P4 | Focus assumed, never verified | file-dialog run typed 75 chars into MY terminal (`is_foreground:false`); later `/foreground` returned null desktop-wide |
| P5 | Repeat-without-reobserve | seven fill/submit attempts on one Mercor posting → spam-flagged; nothing detected the loop |

Every one of these maps to a kernel component: L1-L3 → capture daemon + event-driven
planner; P1 → verifier; P2 → motor staleness gate; P3 → grounding revalidation; P4 → focus
verification in motor preconditions; P5 → recovery engine's repeat detector.

## 3. Retain / replace / delete

RETAIN (fits boundaries)
- operator :7788 input endpoints → wrapped by the motor daemon as its private backend.
  Motor is the ONLY caller for kernel work; everything else loses input permission.
- ffmpeg ddagrab pipeline (screen tape) → capture daemon substrate option B (option A: mss).
- sub.mjs → planner/grounding inference transport (internal infra, permitted).
- bus :7799 → event fanout for journal mirroring.
- feedback_operate_by_sight doctrine → becomes the verifier's contract.

REPLACE
- eyes.mjs serial loop → planner (event-driven, one intent at a time) + grounding service
  + motor daemon + verifier. eyes.mjs stays as a reference until gates pass, then retires.

DELETE (violations of the boundaries)
- Direct /click //type calls from any script other than the motor daemon (kernel scope).
- On-demand blocking /screenshot as the only capture path.
- Any planner path that emits raw coordinates.

OUT OF SCOPE, UNTOUCHED
- Job lanes (ats-apply.mjs, ats-dom.mjs, watchers, schtasks) — separate project, still
  running on schedule. UIA/DOM are banned in THIS kernel, not exorcised from the machine.

## 4. Migration sequence (test after every step)

1. **Journal** (journal/journal.py): append-only JSONL, all 14 event types. Test: write/read
   1k events, crash mid-write, verify last line integrity. ✅ built
2. **Calibration environment** (calib/calib_app.py): tkinter, static/small/edge/moving
   targets, text fields, modal, loading transitions; logs ground truth per interaction;
   exposes layout to the harness. Ground truth comes from the app's own hit handlers —
   the score is what the APP received, never what the motor claims. ✅ built
3. **Motor daemon** (kernel/motor.py): sole input emitter; normalized→physical transform;
   DPI calibration; single pending action; move/settle/click separation; staleness
   rejection; SAFE_STOP file honored before every emit; journals every input. ✅ built
   — Emergency stop: SAFE_STOP file at organism/journal/SAFE_STOP. A hotkey process
   (kernel/estop.py, Ctrl+Alt+F12) writes it; motor refuses while it exists. The kernel
   has no code path that removes it; only Marco deletes it.
4. **Capture daemon** (kernel/capture.py): 20-30 FPS mss loop, monotonic frame IDs, bounded
   ring, change/stability metrics, drops obsolete frames. Test: sustained FPS + drop count
   under load. ✅ built (in-process ring; shared-memory hand-off is step 6)
5. **Calibration gates**: 1,000 static / 1,000 edge / 500 small clicks, DPI transforms,
   stale-frame cancellation, target-moved, focus verification, unexpected modal. Numbers
   published from the calib app's ground-truth log only.
6. **World-state service + verifier** on the ring (frame diff regions, expected-transition
   classification). Then planner intents (semantic only) + grounding (region + confidence).
7. **Supervisor** (states, heartbeats, restarts, checkpoints) + recovery engine.
8. **Soak**: 8h observation, zero uncontrolled input after SAFE_STOP.

No gate is claimed without a measured run and journal evidence. Compilation is not success.
