# REFLEX 100-run benchmark — 2026-08-01

| outcome | count |
|---|---|
| PERFECT (all fields exact, correct shuffled row, DONE) | 100 |
| MISROUTE (typed into a wrong field — the poison outcome) | 0 |
| BLOCKED (honest refusal, nothing wrong typed) | 0 |
| CRASH/other | 0 |

runs: 100 · replay seconds (perfect runs): min 4.9 / median 6.5 / max 9.1 · LLM calls per replay: 0
Every run: fresh demo -> compile -> relaunch at random window position, shuffled rows, random accent color -> replay from crops only -> scored by the mock's own ground-truth log.
