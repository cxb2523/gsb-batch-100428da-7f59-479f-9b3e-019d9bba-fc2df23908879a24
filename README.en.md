# Yuketang Auto Player

A Selenium-based auto player for **Yuketang (雨课堂 / Rain Classroom)** course videos:
it drives a real browser (Edge or Chrome), finds the episodes you haven't finished,
plays them, and moves on — no manual clicking.

> ⚠️ **For learning Selenium / personal convenience only.** It cannot do your homework,
> and using it may violate your school's rules and Yuketang's Terms of Service.
> See [Disclaimer](#disclaimer) before use.

[中文说明（README.md）](README.md) · [Full manual (Chinese)](使用说明.md)

---

## Quick start

### Option A — prebuilt EXE (no Python needed)

**1. Download the EXE**
Open [Releases](https://github.com/34Avin34/yuketang_autoplayer/releases), open the latest
`v1.0.0`, and click `YuketangAutoPlayer.exe` under "Assets".
(Not `Source code (zip)` — that's the source; use Option B for that.)

**2. Put it in a dedicated folder**
e.g. create `D:\yuketang` and move the EXE there.

- Avoid `C:\Program Files` — it needs admin rights and `config.ini` cannot be written there.
- Avoid the Downloads folder — cleanup tools like to delete it.

**3. Run it once**
A black console flashes and closes by itself — **that is expected**: it just created
`config.ini` next to the EXE (that single file holds all settings).

**4. Fill in the course URL**
Right-click `config.ini` → "Open with" → Notepad. Find this line:

```ini
; the line above starting with ';' is a comment — leave it alone
course_url = https://www.yuketang.cn/ai-workspace/lms-graph/xxxx/video/xxxxx?node_id=xxxx
```

Replace **everything after the `=`** with the course URL you copied:

- keep the `course_url = ` prefix and the `=`;
- **no quotes** around the URL;
- save it, and make sure the file is still named `config.ini` (Notepad may append `.txt`).

**5. Run it again**
Edge opens → the course page loads → **scan the QR code** with WeChat / the Yuketang app.
Playback starts by itself after login.

- The console window next to it is the log (`播放中 xx% 实际2.0x`); minimize it, **don't close it**;
- the login is saved back into `config.ini`, so you only scan once;
- **don't click inside the browser window** — the script is driving it.

> The EXE is an unsigned PyInstaller build, so Windows may block it in one of two ways:
> - blue "**Windows protected your PC**" (SmartScreen) → click "More info → Run anyway";
> - "**Smart App Control** has blocked a potentially unsafe app" (Windows 11 Smart App
>   Control) → **there is no "run anyway" button**; use **Option B** instead, or turn that
>   feature off (it usually cannot be re-enabled without resetting Windows).
>
> Neither is a virus warning — it simply means the binary has no code-signing certificate.
> If unsure, just use Option B; the result is identical.

### Option B — from source

1. Install Python 3.9+ — tick **`Add python.exe to PATH`** during setup.
2. Install the dependency: `pip install -r requirements.txt`
3. Copy `config.ini.example` to `config.ini` and set `course_url` (same as above).
4. Run `启动.bat`, or `python main.py`.

### Both options

Scan the QR code once; the `sessionid` is saved back into `config.ini`, then playback
starts. You can minimize the window, but **don't move your mouse into it** — the script is
driving that browser.

> Just want to check your setup? `python main.py --list` (or `YuketangAutoPlayer.exe --list`)
> prints the pending episodes and plays nothing.

---

## Configuration (`config.ini`)

| Key | Default | Meaning |
|---|---|---|
| `headless` | `false` | `true` = run without a browser window. **QR login requires `false`** |
| `browser` | `auto` | `auto` / `chrome` / `edge`. `auto` prefers Edge (ships with Windows) |
| `course_url` | — | Course page URL, required, must start with `https://` |
| `cookie` | empty | `sessionid` value. Empty = scan the QR code on every run |
| `playback_rate` | `2` | Playback speed. `2` is the safest value the platform accepts; `4/8/16` also work but may not be credited |
| `restart_from_start` | `true` | `true` = replay unfinished episodes from 0:00; `false` = resume |
| `implicitly_wait` | `20` | Max seconds to wait for page elements |
| `max_minutes_per_video` | `180` | Give up on a single video after N minutes |

Restart the program after editing the config.

**Default behaviour**: half-watched episodes (pie icon) are played first, from the
beginning; finished ones (check icon / "已完成") are skipped; homework and text-only
materials are ignored.

---

## Commands

| Command | Purpose |
|---|---|
| `python main.py` | Start playing |
| `python main.py --list` | List pending episodes only |
| `python main.py --login` | Force a re-login and save the new sessionid |
| `python main.py --dump` | Save the course page structure to `debug/` (for bug reports) |
| `python main.py --self-test` | Only check that the browser can start |
| `python main.py --headless` | Headless mode (no QR login) |
| `python main.py --rate 1.5` | Override playback speed |

---

## How it works

- Detects three different Yuketang UIs: the 2025 "learning space" tree (`leaf-item`),
  the newer list (`study-unit`) and the legacy list (`leaf-detail`).
- Auto-selects the browser and lets Selenium Manager download the matching driver.
- Sets the speed by clicking the player's own speed menu; if that doesn't stick it pins
  `<video>.playbackRate` so the player cannot revert it.
- Self-healing: stalled stream → reload; broken source or frozen progress → restart the
  browser and retry; missing list → reopen the course page.
- After each episode it checks whether the platform's "completed" counter went up and
  warns you if it didn't.

---

## Credits / Inspiration

This project is **not** built from scratch:

- **[LetMeFly666/YuketangAutoPlayer](https://github.com/LetMeFly666/YuketangAutoPlayer)** —
  the original Selenium approach (drive a real browser, locate list items, force-play on
  pause, detect completion). This project is a substantial rewrite on top of that idea.
- **CSDN article "汕头大学雨课堂自动播放器 (YuketangAutoPlayer For STU)"** by 不想做小丑 —
  the tutorial that popularised the scenario.
- [Selenium](https://www.selenium.dev/).

Main differences from upstream: support for the 2025 "learning space" UI, automatic
browser/driver selection, speed pinning, replay-from-zero, self-healing, and the
completion-counter check.

### Why this had to be rewritten

The upstream implementation targets the **earlier course-list UIs** — the legacy
`leaf-detail` list and the `study-unit` list on `www.yuketang.cn`.

In 2025 Yuketang rolled out a new "learning space" (`ai-workspace/lms-graph`) that changed
quite a lot:

- the sidebar became a collapsible tree (`nav-item` / `leaf-item`) instead of a flat list;
- completion state became icons: **check = finished**, **pie = half-watched**, empty = not started;
- the player is now `xt_video_player`, which keeps its own speed state and keeps overwriting
  `video.playbackRate`;
- the login page moved to a redesigned `/web` QR page with a different URL and DOM.

Because of that, the upstream selectors, completion check, speed handling and login flow
**do not work on the new UI at all**. This project rewrites those parts for the new
interface while remaining compatible with the old one — it is a re-implementation of the
same idea, not a simple fork.

---

## AI assistance

This project was developed **with the help of an AI coding assistant**: code, debugging and
documentation were produced with ZCode (model `deepseek-flash`). Design decisions,
real-environment testing and the final review were done by the author.

Disclosed for transparency.

---

## Disclaimer

1. This project is for **learning browser automation (Selenium)** and for managing courses
   you are personally enrolled in.
2. It **cannot and should not** be used to complete homework, quizzes or exams.
3. Auto-playing courses may **violate your school's course requirements and Yuketang's
   Terms of Service**. You are solely responsible for any consequences (grades,
   attendance, account penalties).
4. No commercial use, no "course farming" services, no bulk account operation.
5. The author is not liable for any damage caused by using this project.
6. `sessionid` is your credential — never share it. This tool uploads none of your data.

**If you disagree with any of the above, please do not use this project.**

---

## License

LGPL-2.1, same as the upstream project. See [LICENSE](LICENSE).
