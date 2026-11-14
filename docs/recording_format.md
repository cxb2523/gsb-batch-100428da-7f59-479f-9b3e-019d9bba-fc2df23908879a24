# Recording format

A recording is a JSON file:

```json
{
  "version": "1.0",
  "events": [
    {"delay": 0.42, "type": "click", "x": 640, "y": 360, "button": "Button.left",
     "anchor": {"path": "assets/step_0000.png", "conf": 0.9, "timeout": 4.0,
                "ox": 24, "oy": 24, "w": 48, "h": 48}},
    {"delay": 0.11, "type": "click", "x": 800, "y": 420, "button": "Button.left",
     "anchor_error": {"code": "grab_failed", "detail": "OSError: ..."}},
    {"delay": 0.05, "type": "key", "key": "a"}
  ]
}
```

## Click anchors

Every click attempts to capture a small anchor image around the pointer. The
event is always kept; only the anchor may be absent.

- `anchor` is present when the image was captured and copied next to the
  recording as `assets/step_XXXX.png`.
- `anchor_error` is present when no usable anchor could be produced. `code` is
  one of:

  | code | meaning |
  | --- | --- |
  | `grab_failed` | screen capture failed |
  | `save_failed` | writing the temporary anchor image failed |
  | `assets_dir_failed` | the assets directory could not be created |
  | `capture_failed` | any other unexpected capture error |
  | `copy_failed` | the image could not be copied into `assets/` at save time |

  `detail` carries the original exception text.

A click with `anchor_error` is replayed by the failsafe policy
(`fallback_to_absolute`): use the recorded `x`/`y`, or skip the step when the
failsafe is disabled. The debug output names the reason, e.g.

```
[run] missing anchor (grab_failed: OSError: ...); failsafe -> recorded (x,y)=(800,420) step=1
[run] anchor image missing: /path/assets/step_0001.png; no failsafe, skipping step=1
```

## Stopping

Stopping with F8 or `Recorder.stop()` drains the listener threads before the
event list is snapshotted, so clicks and keys already delivered by the OS are
included in the saved recording.
