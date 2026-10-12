# ScreenStitch

> Lightweight background Windows utility for instant batch-screenshot capture and automated vertical stitching — straight to the clipboard.

---

## Quick Start

```powershell
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run
python screen_stitch.py
```

That's it. The utility prints a banner and starts listening for hotkeys in the background.

---

## Hotkeys

| Key | Action |
|---|---|
| **F8** | Capture the entire screen (instant, no overlay) |
| **F9** | Stitch all captures into one image → **clipboard** |
| **Shift + F8** | Clear the capture buffer (no clipboard change) |
| **Esc** | Exit ScreenStitch |

### Workflow

1. Navigate to the first screen you want to capture → press **F8** (high beep confirms).
2. Switch to the next app/window → press **F8** again. Repeat as needed.
3. Press **F9** — all frames are stitched vertically with dark separators and numbered badges, then pushed to the clipboard as a single image.
4. Go to your chat window / ticketing system → **Ctrl + V** to paste the entire storyboard.

---

## Audio Feedback

| Sound | Meaning |
|---|---|
| High beep (1200 Hz) | Capture succeeded |
| Low beep (400 Hz) | Duplicate frame rejected _or_ buffer already empty |
| Ascending two-tone | Stitch complete — image is on the clipboard |

---

## Silent Background Execution

### Option A — Rename to `.pyw`

Rename `screen_stitch.py` → `screen_stitch.pyw` and double-click to launch. Python runs it with `pythonw.exe` — **no console window** appears. Audio cues still work as feedback.

### Option B — Launch with `pythonw.exe`

```powershell
Start-Process pythonw.exe -ArgumentList "C:\path\to\screen_stitch.py" -WindowStyle Hidden
```

### Option C — Create a VBS Silent Launcher

Create `start_screenstitch.vbs`:

```vbs
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "pythonw.exe C:\path\to\screen_stitch.pyw", 0, False
```

Double-click the `.vbs` file — completely invisible launch.

---

## Auto-Start on Login

1. Press **Win + R** → type `shell:startup` → Enter.
2. Place a shortcut (or the `.vbs` launcher above) into the Startup folder.
3. ScreenStitch will now run silently every time you log in.

Alternatively, use **Task Scheduler**:

```powershell
$action  = New-ScheduledTaskAction -Execute "pythonw.exe" -Argument "C:\path\to\screen_stitch.pyw"
$trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "ScreenStitch" -Action $action -Trigger $trigger -Description "Background screenshot stitcher"
```

---

## Configuration

All tunables live at the top of `screen_stitch.py`. Key options:

| Variable | Default | Description |
|---|---|---|
| `COLUMNS` | `2` | `2` = 2-column grid, `1` = single vertical column, `"auto"` = 1 col if < 4, 2 cols if >= 4 |
| `AUTO_THRESHOLD` | `4` | Frame count threshold when `COLUMNS = "auto"` |
| `CAPTURE_MONITOR` | `0` | `0` = all monitors combined, `1` = primary only |
| `SEPARATOR_SIZE` | `10` | Pixel gap between stacked panes (horizontal & vertical) |
| `SEPARATOR_COLOR` | `#21252B` | Dark-mode divider fill |
| `MAX_PANE_WIDTH` | `1440` | Auto-downscale wider individual captures to this width (0 to disable) |
| `BADGE_ENABLED` | `True` | Draw numbered index badges on each pane |
| `BADGE_DIAMETER` | `30` | Badge circle size (px) |
| `BADGE_BG` | `#007ACC` | Badge fill colour |

---

## Architecture Notes

- **Capture engine:** `mss` (C-backed, ~15 ms per full-screen grab).
- **Duplicate gate:** Perceptual hash — 16×16 thumbnail → MD5. Fast and jitter-tolerant.
- **Thread safety:** A non-blocking `threading.Lock` gate prevents concurrent capture/stitch operations from corrupting the buffer.
- **Clipboard format:** `CF_DIB` via `pywin32` — the BMP is serialised entirely in memory. No temp files are written to disk.
- **Console-safe logging:** `log()` is a no-op when `sys.stdout is None` (`.pyw` mode).

---

## Troubleshooting

| Issue | Fix |
|---|---|
| `keyboard` hook fails | Run the script **as Administrator**, or from a non-elevated terminal matching your desktop session |
| Clipboard paste shows nothing | Ensure the target app supports bitmap paste (`CF_DIB`). Most browsers, Slack, Teams, etc. do. |
| Badge font looks wrong | The script tries Segoe UI Bold → Arial Bold → default. Install Segoe UI or edit `_load_badge_font()`. |
| Multi-monitor capture is too wide | Set `CAPTURE_MONITOR = 1` to capture only the primary display. |
