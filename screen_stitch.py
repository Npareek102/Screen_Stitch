#!/usr/bin/env python3
"""
ScreenStitch — Lightweight Background Windows Utility
=====================================================
Batch-capture full-screen screenshots and auto-stitch them into a single
vertical storyboard image, pushed directly to the Windows clipboard (CF_DIB).

Hotkeys
-------
    F8          Capture current screen
    F9          Stitch all captures & copy to clipboard
    Shift+F8    Clear capture buffer (reset session)
    Esc         Exit the utility

Requirements
------------
    pip install mss Pillow pywin32 keyboard

License: MIT
"""

from __future__ import annotations

import hashlib
import io
import sys
import threading
from typing import List, Optional

import keyboard
import mss
import win32clipboard
import winsound
from PIL import Image, ImageDraw, ImageFont


# ═══════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION — tweak these values to taste
# ═══════════════════════════════════════════════════════════════════════════════

# ── Hotkeys ───────────────────────────────────────────────────────────────────
CAPTURE_KEY   = "f8"            # capture the screen
STITCH_KEY    = "f9"            # stitch + clipboard
RESET_KEYS    = "shift+f8"      # clear session buffer
EXIT_KEY      = "esc"           # quit

# ── Monitor Selection ─────────────────────────────────────────────────────────
#    0 = entire virtual screen (all monitors combined)
#    1 = primary monitor only   2, 3, … = specific monitor index
CAPTURE_MONITOR = 0

# ── Layout & Grid ─────────────────────────────────────────────────────────────
COLUMNS          = 2                    # 2 = 2-column grid, 1 = vertical ribbon, "auto" = 1-col if < 4, 2-col if >= 4
AUTO_THRESHOLD   = 4                    # frame count threshold when COLUMNS = "auto"
SEPARATOR_SIZE   = 10                   # px gap between panes (horizontal & vertical)
SEPARATOR_HEIGHT = SEPARATOR_SIZE       # backward compatibility alias
SEPARATOR_COLOR  = (33, 37, 43)         # #21252B — dark-mode divider

# ── Auto-Downscaling ─────────────────────────────────────────────────────────
MAX_PANE_WIDTH   = 1440                 # max width per individual pane (px; 0 to disable)
MAX_STITCH_WIDTH = MAX_PANE_WIDTH       # backward compatibility alias

# ── Index Badges ──────────────────────────────────────────────────────────────
BADGE_ENABLED = True                    # draw numbered badges on each pane
BADGE_DIAMETER = 30                     # badge circle diameter (px)
BADGE_BG      = (0, 122, 204)          # badge fill  (#007ACC — VS Code blue)
BADGE_FG      = (255, 255, 255)        # badge text  (white)
BADGE_MARGIN  = 12                      # offset from top-left corner of pane

# ── Audio Cues (frequency Hz, duration ms) ────────────────────────────────────
TONE_CAPTURE    = (1200, 75)            # crisp high tone
TONE_DUPLICATE  = (400, 150)            # low warning tone
TONE_ERROR      = (400, 150)            # same low tone for empty-buffer, etc.
TONE_STITCH_LO  = (880, 100)           # ascending two-tone chord — note 1
TONE_STITCH_HI  = (1320, 150)          # ascending two-tone chord — note 2

# ── Duplicate Detection ──────────────────────────────────────────────────────
HASH_THUMB_SIZE = 16                    # thumbnail edge (px) for fast hashing


# ═══════════════════════════════════════════════════════════════════════════════
#  LOGGING — gracefully no-ops when running as .pyw (no console)
# ═══════════════════════════════════════════════════════════════════════════════

def log(msg: str) -> None:
    """Write to stdout when a console exists; silently skip for .pyw."""
    try:
        if sys.stdout is not None:
            print(msg, flush=True)
    except (OSError, AttributeError):
        pass


# ═══════════════════════════════════════════════════════════════════════════════
#  AUDIO — non-blocking beeps via daemon threads
# ═══════════════════════════════════════════════════════════════════════════════

def _beep(freq: int, dur: int) -> None:
    """Fire-and-forget system beep on a background thread."""
    threading.Thread(target=winsound.Beep, args=(freq, dur), daemon=True).start()


def beep_capture() -> None:
    _beep(*TONE_CAPTURE)

def beep_duplicate() -> None:
    _beep(*TONE_DUPLICATE)

def beep_error() -> None:
    _beep(*TONE_ERROR)

def beep_stitch() -> None:
    """Two-tone ascending chord (played sequentially on one thread)."""
    def _chord():
        winsound.Beep(*TONE_STITCH_LO)
        winsound.Beep(*TONE_STITCH_HI)
    threading.Thread(target=_chord, daemon=True).start()

def beep_reset(had_frames: bool) -> None:
    """Capture-tone if frames were discarded; duplicate-tone if already empty."""
    (_beep(*TONE_CAPTURE) if had_frames else _beep(*TONE_DUPLICATE))


# ═══════════════════════════════════════════════════════════════════════════════
#  DUPLICATE DETECTION — fast perceptual hash via down-sampled MD5
# ═══════════════════════════════════════════════════════════════════════════════

def _image_hash(img: Image.Image) -> str:
    """
    Down-sample to a tiny thumbnail and MD5 its raw pixels.
    Fast enough for real-time use; resilient to minor DPI / anti-alias jitter.
    """
    thumb = img.resize(
        (HASH_THUMB_SIZE, HASH_THUMB_SIZE), Image.Resampling.NEAREST
    ).convert("RGB")
    return hashlib.md5(thumb.tobytes()).hexdigest()


# ═══════════════════════════════════════════════════════════════════════════════
#  CAPTURE SESSION — thread-safe screenshot buffer
# ═══════════════════════════════════════════════════════════════════════════════

class CaptureSession:
    """Stores queued screenshots and provides atomic drain / clear ops."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buffer: List[Image.Image] = []
        self._last_hash: Optional[str] = None

    # ── mutators ──────────────────────────────────────────────────────────

    def add(self, img: Image.Image) -> bool:
        """
        Enqueue a capture.
        Returns True on success, False if the image is a duplicate of the
        most recent frame (same perceptual hash).
        """
        h = _image_hash(img)
        with self._lock:
            if h == self._last_hash:
                return False
            self._buffer.append(img)
            self._last_hash = h
            return True

    def pop_all(self) -> List[Image.Image]:
        """Atomically drain and return every queued frame."""
        with self._lock:
            frames = list(self._buffer)
            self._buffer.clear()
            self._last_hash = None
            return frames

    def clear(self) -> int:
        """Discard all frames. Returns the count that were discarded."""
        with self._lock:
            n = len(self._buffer)
            self._buffer.clear()
            self._last_hash = None
            return n

    # ── queries ───────────────────────────────────────────────────────────

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._buffer)


# ═══════════════════════════════════════════════════════════════════════════════
#  SCREEN CAPTURE — low-latency grab via mss
# ═══════════════════════════════════════════════════════════════════════════════

def grab_screen() -> Image.Image:
    """
    Capture the selected monitor using `mss` for speed.
    Returns an RGB PIL Image.
    """
    with mss.mss() as sct:
        monitor = sct.monitors[CAPTURE_MONITOR]
        raw = sct.grab(monitor)
        return Image.frombytes("RGB", raw.size, raw.rgb)


# ═══════════════════════════════════════════════════════════════════════════════
#  IMAGE STITCHING — vertical storyboard with separators & badges
# ═══════════════════════════════════════════════════════════════════════════════

def _load_badge_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Attempt to load a clean system TrueType font; fall back to default."""
    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf",   # Segoe UI Bold
        "C:/Windows/Fonts/segoeui.ttf",     # Segoe UI
        "C:/Windows/Fonts/arialbd.ttf",     # Arial Bold
        "C:/Windows/Fonts/arial.ttf",       # Arial
        "C:/Windows/Fonts/calibrib.ttf",    # Calibri Bold
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def stitch_images(frames: List[Image.Image]) -> Image.Image:
    """
    Concatenate *frames* into a storyboard canvas (1-column or 2-column grid).

    Features:
      • 2-column grid arrangement (or 1-column if configured / single frame)
      • Dark-mode separator bars between panes (horizontal & vertical)
      • Proportional downscale per pane to MAX_PANE_WIDTH
      • Numbered index badges (1, 2, 3...) in the top-left of each pane
    """
    if not frames:
        raise ValueError("No frames to stitch")

    # ── 1. Down-scale any oversized frames per pane ───────────────────────
    scaled: List[Image.Image] = []
    for img in frames:
        w, h = img.size
        if MAX_PANE_WIDTH and w > MAX_PANE_WIDTH:
            ratio = MAX_PANE_WIDTH / w
            new_size = (MAX_PANE_WIDTH, int(h * ratio))
            img = img.resize(new_size, Image.Resampling.LANCZOS)
        scaled.append(img)

    # ── 2. Determine column count ─────────────────────────────────────────
    if COLUMNS == "auto":
        num_cols = 2 if len(scaled) >= AUTO_THRESHOLD else 1
    else:
        num_cols = min(max(1, int(COLUMNS)), len(scaled))

    num_rows = (len(scaled) + num_cols - 1) // num_cols

    # ── 3. Calculate grid dimensions ──────────────────────────────────────
    col_widths = [0] * num_cols
    for idx, img in enumerate(scaled):
        c = idx % num_cols
        col_widths[c] = max(col_widths[c], img.width)

    row_heights = [0] * num_rows
    for idx, img in enumerate(scaled):
        r = idx // num_cols
        row_heights[r] = max(row_heights[r], img.height)

    canvas_w = sum(col_widths) + SEPARATOR_SIZE * (num_cols - 1)
    canvas_h = sum(row_heights) + SEPARATOR_SIZE * (num_rows - 1)

    # ── 4. Create canvas & paste panes ────────────────────────────────────
    canvas = Image.new("RGB", (canvas_w, canvas_h), SEPARATOR_COLOR)
    draw   = ImageDraw.Draw(canvas)

    # Pre-load font once
    font = _load_badge_font(BADGE_DIAMETER - 10) if BADGE_ENABLED else None

    for idx, img in enumerate(scaled):
        r = idx // num_cols
        c = idx % num_cols

        # X position: sum of preceding column widths + separators
        col_x = sum(col_widths[:c]) + c * SEPARATOR_SIZE
        # Centre pane within its column slot if widths vary
        x_offset = col_x + (col_widths[c] - img.width) // 2

        # Y position: sum of preceding row heights + separators
        row_y = sum(row_heights[:r]) + r * SEPARATOR_SIZE
        # Centre pane vertically within its row slot if heights vary
        y_offset = row_y + (row_heights[r] - img.height) // 2

        canvas.paste(img, (x_offset, y_offset))

        # ── Draw index badge ──────────────────────────────────────────────
        if BADGE_ENABLED and font is not None:
            _draw_badge(draw, font, idx + 1, x_offset, y_offset)

    return canvas


def _draw_badge(
    draw: ImageDraw.ImageDraw,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    number: int,
    pane_x: int,
    pane_y: int,
) -> None:
    """Render a small numbered circle badge at the top-left of a pane."""
    r  = BADGE_DIAMETER // 2
    cx = pane_x + BADGE_MARGIN + r
    cy = pane_y + BADGE_MARGIN + r

    # Filled circle
    draw.ellipse(
        [cx - r, cy - r, cx + r, cy + r],
        fill=BADGE_BG,
    )

    # Centred label
    label = str(number)
    bbox  = font.getbbox(label)
    tw    = bbox[2] - bbox[0]
    th    = bbox[3] - bbox[1]
    draw.text(
        (cx - tw // 2, cy - th // 2 - bbox[1]),
        label,
        fill=BADGE_FG,
        font=font,
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  CLIPBOARD — push image as CF_DIB (no temp files)
# ═══════════════════════════════════════════════════════════════════════════════

def copy_image_to_clipboard(img: Image.Image) -> None:
    """
    Write a PIL Image to the Windows clipboard as a Device Independent Bitmap
    (CF_DIB).  The BMP is serialised entirely in memory — nothing touches disk.
    """
    buf = io.BytesIO()
    img.save(buf, format="BMP")
    bmp_bytes = buf.getvalue()

    # BMP file header is 14 bytes; CF_DIB expects just the DIB portion.
    dib_data = bmp_bytes[14:]

    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_DIB, dib_data)
    finally:
        win32clipboard.CloseClipboard()


# ═══════════════════════════════════════════════════════════════════════════════
#  HOTKEY HANDLERS — guarded by a reentrant gate to prevent overlap
# ═══════════════════════════════════════════════════════════════════════════════

# Shared state
session = CaptureSession()
_op_lock = threading.Lock()          # prevents concurrent capture / stitch ops
_exit_event = threading.Event()      # signalled by on_exit → unblocks main()


def on_capture() -> None:
    """F8 — grab the screen and enqueue it (unless it's a duplicate)."""
    # Ignore if Shift is held (that combo belongs to on_reset)
    if keyboard.is_pressed("shift"):
        return

    if not _op_lock.acquire(blocking=False):
        return                       # another op already running; skip quietly
    try:
        img = grab_screen()
        if session.add(img):
            beep_capture()
            log(f"  [+] Captured frame #{session.count}")
        else:
            beep_duplicate()
            log("  [~] Duplicate frame — ignored")
    except Exception as exc:
        beep_error()
        log(f"  [!] Capture error: {exc}")
    finally:
        _op_lock.release()


def on_stitch() -> None:
    """F9 — stitch all queued frames into one image → clipboard."""
    if not _op_lock.acquire(blocking=False):
        return
    try:
        frames = session.pop_all()
        if not frames:
            beep_error()
            log("  [!] Buffer is empty — nothing to stitch")
            return

        log(f"  [*] Stitching {len(frames)} frame(s) …")
        merged = stitch_images(frames)
        copy_image_to_clipboard(merged)
        beep_stitch()
        log(f"  [✓] Clipboard loaded  ({merged.width}×{merged.height} px)")
    except Exception as exc:
        beep_error()
        log(f"  [!] Stitch error: {exc}")
    finally:
        _op_lock.release()


def on_reset() -> None:
    """Shift+F8 — clear the capture buffer without touching the clipboard."""
    n = session.clear()
    beep_reset(had_frames=n > 0)
    log(f"  [⟳] Buffer cleared  ({n} frame(s) discarded)")


def on_exit() -> None:
    """Esc — gracefully shut down."""
    log("\n  [×] ScreenStitch shutting down …")
    _exit_event.set()


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN — register hotkeys and block until exit
# ═══════════════════════════════════════════════════════════════════════════════

BANNER = r"""
  ╔══════════════════════════════════════════════════╗
  ║            S C R E E N S T I T C H               ║
  ║     Background Screenshot Stitcher for Windows    ║
  ╚══════════════════════════════════════════════════╝

   F8 ·········· Capture screen
   F9 ·········· Stitch & copy to clipboard
   Shift+F8 ···· Clear buffer
   Esc ········· Exit

   Listening for hotkeys …
"""


def main() -> None:
    log(BANNER)

    # Register global hotkeys (non-suppressing so other apps still get keys)
    keyboard.add_hotkey(CAPTURE_KEY, on_capture, suppress=False)
    keyboard.add_hotkey(STITCH_KEY,  on_stitch,  suppress=False)
    keyboard.add_hotkey(RESET_KEYS,  on_reset,   suppress=False)
    keyboard.add_hotkey(EXIT_KEY,    on_exit,     suppress=False)

    # Block until Esc sets the exit event
    _exit_event.wait()

    # Clean up
    keyboard.unhook_all()
    log("  Goodbye.\n")


if __name__ == "__main__":
    main()
