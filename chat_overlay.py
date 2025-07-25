#!/usr/bin/env python3
"""
chat_overlay.py  ‑  **Lean single-purpose version**

Runs in two steps:
1.  Renders a scrolling chat strip (10 fps) for the given clip.
2.  Horizontally stacks the strip onto the right side of the clip with FFmpeg.

The script is *opinionated* on performance and stability:
• No hardware-acceleration probing – always uses plain libx264.
• Only one code-path; no "fast", "turbo", benchmark or composite modes.
• Defaults are hard-wired to the current xQc test VOD so you can simply run

    $ python chat_overlay.py

and get `xqc_part_of_vod/20250627-185557_with_chat.mp4`.

The public helper `overlay_chat_on_video()` keeps the old signature so
`index.py` continues to work, but most arguments are now ignored.
"""
from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from moviepy.editor import VideoClip, VideoFileClip
from PIL import Image, ImageDraw, ImageFont
import requests
from io import BytesIO
import re
import uuid
import time
import gc  # added to allow explicit memory cleanup
import sys  # added to detect run-direct invocation and control side-effects
import re

EMOJI_URL_BASE = "https://twemoji.maxcdn.com/v/latest/72x72/{}.png"
EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map symbols
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "]", flags=re.UNICODE)

# ---------------------------------------------------------------------------
# CONSTANTS & DEFAULTS – adjust here only                                  
# ---------------------------------------------------------------------------
DEFAULT_VIDEO = Path("xqc_part_of_vod") / "20250627-185557.mp4"
DEFAULT_CHAT_DIR = Path("xqc_part_of_vod")
DEFAULT_CHAT_WIDTH = 400
FPS_CHAT = 30      # chat strip fps (further reduced from 5 to speed up rendering)
UPDATE_INTERVAL = 1 / FPS_CHAT
FONT_SIZE = 22
MESSAGE_DURATION = 8.0

# ---------------------------------------------------------------------------
# Simple helpers
# ---------------------------------------------------------------------------

def _safe_font(size: int) -> ImageFont.FreeTypeFont:
    for name in ["arial.ttf", "DejaVuSans.ttf", "DejaVuSans-Bold.ttf"]:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _hex_to_rgb(hex_str: str, fallback=(119, 44, 232)) -> Tuple[int, int, int]:
    try:
        hex_str = hex_str.lstrip("#")
        if len(hex_str) != 6:
            return fallback
        r, g, b = (int(hex_str[i : i + 2], 16) for i in (0, 2, 4))
        return fallback if all(v > 240 for v in (r, g, b)) else (r, g, b)
    except Exception:
        return fallback


# ---------------------------------------------------------------------------
# Chat overlay renderer (optimised but self-contained)
# ---------------------------------------------------------------------------

def _render_chat_strip(
    video_height: int,
    chat_width: int,
    chat_messages: List[Dict],
    seventv_map: Dict[str, str],
    duration: float,
    font: ImageFont.FreeTypeFont,
    to_file: bool = True,
) -> str:
    """Render the scrolling chat to a temporary MP4 and return its path."""

    line_height = font.getmetrics()[0] + font.size // 3
    max_lines = max(1, (video_height - 20) // line_height)

    emote_px = 28
    line_height = max(line_height, emote_px + 4)

    emote_cache: Dict[str, "Emote"] = {}

    # ------------------------------------------------------------------
    # Pre-compute expensive per-message data so make_frame is lightweight
    # ------------------------------------------------------------------

    for m in chat_messages:
        # Cached colour & prefix (must set prefix before measuring)
        m["_colour"] = _hex_to_rgb(m.get("color", "#fff"))
        name = m.get("display_name", "user")
        m["_prefix"] = f"{name}: "
        prefix_width = font.getlength(m["_prefix"])

        # Pre-tokenise message into (token, is_emote) at character level, preserving emotes
        raw_tokens: List[Tuple[str, bool]] = []
        msg = m.get("message", "")
        i = 0
        while i < len(msg):
            matched = False
            # try to match any emote name at current position
            for name, url in seventv_map.items():
                if msg.startswith(name, i):
                    # only match emote if it's standalone (spaces or start/end)
                    prev_char = msg[i-1] if i > 0 else ' '
                    end = i + len(name)
                    next_char = msg[end] if end < len(msg) else ' '
                    if prev_char.isspace() and next_char.isspace():
                        raw_tokens.append((url, True))
                        i += len(name)
                        matched = True
                        break
            if not matched:
                # check for normal Unicode emoji and replace with Twemoji
                emoji_match = EMOJI_PATTERN.match(msg, i)
                if emoji_match:
                    emoji_char = emoji_match.group(0)
                    cps = "-".join(f"{ord(ch):x}" for ch in emoji_char)
                    raw_tokens.append((EMOJI_URL_BASE.format(cps), True))
                    i += len(emoji_char)
                    continue
                raw_tokens.append((msg[i], False))
                i += 1

        # Wrap tokens by true widths (use estimated emote width for wrapping)
        wrapped_tokens: List[List[Tuple[str, bool]]] = []
        line_buf: List[Tuple[str, bool]] = []
        current_width = prefix_width
        gutter = emote_px + 10  # dynamic gutter: emote height + padding
        max_width = chat_width - gutter
        for tok, is_emote in raw_tokens:
            if is_emote:
                tok_w = emote_px  # use standard emote width for wrapping
            else:
                tok_w = font.getlength(tok)

            # wrap if this token would overflow
            if line_buf and current_width + tok_w > max_width:
                wrapped_tokens.append(line_buf)
                line_buf = [(tok, is_emote)]
                current_width = tok_w         # reset width (no prefix on continuations)
            else:
                line_buf.append((tok, is_emote))
                current_width += tok_w
        if line_buf:
            wrapped_tokens.append(line_buf)

        # Store for rendering and layout
        m["_tokens"]  = wrapped_tokens
        m["_wrapped"] = ["".join(tok for tok, _ in line) for line in wrapped_tokens]
        m["_height"]  = len(wrapped_tokens) * line_height

    class Emote:
        """Holds all frames + per-frame durations for an animated emote."""
        __slots__ = ("frames", "durations", "total")

        def __init__(self, frames: List[Image.Image], durations: List[float]):
            self.frames = frames or [Image.new("RGBA", (emote_px, emote_px), (255, 0, 255, 0))]
            self.durations = [max(d or 0.1, 0.01) for d in durations] or [0.1]
            self.total = sum(self.durations)

        def frame_at(self, t: float) -> Image.Image:
            mod = t % self.total
            acc = 0.0
            for img, dur in zip(self.frames, self.durations):
                acc += dur
                if mod < acc:
                    return img
            return self.frames[-1]

    def _load_emote(url: str) -> Emote:
        if url in emote_cache:
            return emote_cache[url]

        try:
            res = requests.get(url, timeout=10)
            res.raise_for_status()
            img = Image.open(BytesIO(res.content))

            frames: List[Image.Image] = []
            durations: List[float] = []

            try:
                while True:
                    # Preserve aspect ratio: scale so height == emote_px
                    w, h = img.size
                    scale = emote_px / h if h else 1.0
                    new_w = max(1, int(round(w * scale)))
                    frame = img.convert("RGBA").resize((new_w, emote_px), Image.LANCZOS)
                    frames.append(frame)
                    durations.append(img.info.get("duration", 100) / 1000.0)
                    img.seek(img.tell() + 1)
            except EOFError:
                pass

            if not frames:
                # Single-frame image – resize while preserving aspect ratio
                w, h = img.size
                scale = emote_px / h if h else 1.0
                new_w = max(1, int(round(w * scale)))
                frames = [img.convert("RGBA").resize((new_w, emote_px), Image.LANCZOS)]
                durations = [0.1]

            emo = Emote(frames, durations)
        except Exception:
            emo = Emote([Image.new("RGBA", (emote_px, emote_px), (255, 0, 255, 0))], [1.0])

        emote_cache[url] = emo
        return emo



    bg = np.full((video_height, chat_width, 3), 30, np.uint8)

    def make_frame(t: float):
        frame = bg.copy()
        # Select all messages that should have appeared by now (t >= delay)
        visible_msgs = [m for m in chat_messages if t >= m["delay"]]

        # Ensure every visible message has static base & emote list (failsafe)
        for m in visible_msgs:
            if "_base" in m and "_emotes" in m:
                continue
            base = Image.new("RGBA", (chat_width, m["_height"]), (0, 0, 0, 0))
            d = ImageDraw.Draw(base)
            emote_pos = []
            y_line = 0
            for i, line in enumerate(m["_tokens"]):
                x = 0
                if i == 0:
                    d.text((x, y_line), m["_prefix"], font=font, fill=m["_colour"])
                    x += font.getlength(m["_prefix"])
                for tok, is_e in line:
                    if is_e:
                        emo = _load_emote(tok)
                        emote_pos.append((int(x), int(y_line), emo))
                        x += emo.frames[0].width + 2
                    else:
                        d.text((x, y_line), tok, font=font, fill=(255, 255, 255))
                        x += font.getlength(tok)
                y_line += line_height
            m["_base"], m["_emotes"] = base, emote_pos

        img = Image.fromarray(frame)

        # Compute starting y so newest messages appear at the bottom
        y = video_height - (sum(m["_height"] for m in visible_msgs)) - 10

        for m in visible_msgs:
            # Paste static text layer
            img.paste(m["_base"], (10, y), m["_base"])
            # Draw animated emotes for this timestamp
            for ex, ey, emo in m["_emotes"]:
                frame_img = emo.frame_at(t)
                img.paste(frame_img, (10 + ex, y + ey), frame_img)
            y += m["_height"]
        return np.array(img)

    chat_clip = VideoClip(make_frame, duration=duration).set_fps(FPS_CHAT)

    if not to_file:
        return chat_clip

    out_path = str(Path("temp") / f"chat_strip_{os.getpid()}_{uuid.uuid4().hex}.mp4")
    Path("temp").mkdir(exist_ok=True)
    chat_clip.write_videofile(
        out_path,
        fps=FPS_CHAT,
        codec="libx264",
        preset="ultrafast",
        ffmpeg_params=["-crf", "25", "-pix_fmt", "yuv420p", "-tune", "fastdecode,zerolatency"],
        audio=False,
        verbose=True,
        logger="bar",
        threads=min(8, os.cpu_count() or 4),
    )
    return out_path

# ---------------------------------------------------------------------------
# Public helper (signature unchanged for index.py compatibility)
# ---------------------------------------------------------------------------

def overlay_chat_on_video(
    video_path: str,
    chat_replay_dir: str = "chat_replays",
    output_path: Optional[str] = None,
    twitch_id: str = "494543675",  # kept for index.py but unused now
    **_ignored,
) -> str:
    """Render chat sidebar and return the combined video path."""

    video = Path(video_path)
    if not video.exists():
        raise FileNotFoundError(video)

    # locate chat log
    chat_log = Path(chat_replay_dir) / f"{video.name}.txt"
    if not chat_log.exists():
        chat_log = Path(chat_replay_dir) / f"{video.stem}.txt"
        if not chat_log.exists():
            raise FileNotFoundError(chat_log)

    # parse chat lines
    messages: List[Dict] = []
    for line in chat_log.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            messages.append(ast.literal_eval(line))
        except Exception:
            continue
    if not messages:
        raise RuntimeError("No chat messages parsed")

    # fetch simple 7TV emote map for the hard-coded channel (only once per run)
    try:
        r = requests.get(f"https://7tv.io/v3/users/twitch/{twitch_id}", timeout=10)
        data = r.json().get("emote_set", {}).get("emotes", [])
        seventv = {e["name"]: f"https://cdn.7tv.app/emote/{e['id']}/3x.webp" for e in data}
    except Exception:
        seventv = {}

    # video meta
    clip = VideoFileClip(str(video))
    duration = clip.duration
    width, height = clip.w, clip.h
    clip.close()

    font = _safe_font(FONT_SIZE)

    # Build the chat strip clip (no intermediate file)
    chat_clip = _render_chat_strip(height, DEFAULT_CHAT_WIDTH, messages, seventv, duration, font, to_file=False)

    # Determine output path
    if output_path is None:
        output_path = str(video.with_name(f"{video.stem}_with_chat.mp4"))

    # Spawn FFmpeg process that will read raw chat frames from stdin
    print(f"⚙️  Running FFmpeg hstack (pipe) for {video.name} …")

    filter_threads = str(max(1, (os.cpu_count() or 4) // 2))

    ffmpeg_cmd = [
        "ffmpeg", "-y",
        # raw chat video from stdin
        "-f", "rawvideo",
        "-pix_fmt", "rgb24",
        "-s", f"{DEFAULT_CHAT_WIDTH}x{height}",
        "-r", str(FPS_CHAT),
        "-i", "pipe:0",
        # main video (assumed ~30 fps)
        "-i", str(video),
        # threading for filters
        "-filter_threads", filter_threads,
        "-filter_complex_threads", filter_threads,
        # filter graph: duplicate chat to 30 fps, stack, convert to yuv420p
        "-filter_complex",
        (
            "[0:v]fps=30,format=rgb24[chat];"
            "[1:v]setpts=PTS-STARTPTS[main];"
            "[main][chat]hstack=inputs=2,format=yuv420p[stack]"
        ),
        "-map", "[stack]",
        "-map", "1:a?",  # copy audio from main if present
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "fastdecode,zerolatency",
        "-crf", "30",
        "-c:a", "copy",
        "-threads", str(min(8, os.cpu_count() or 4)),
        output_path,
    ]

    proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE)

    # Stream chat frames to FFmpeg
    for frame in chat_clip.iter_frames(fps=FPS_CHAT, dtype="uint8"):
        proc.stdin.write(frame.tobytes())

    # --- release large numpy buffers held by MoviePy -------------------------
    chat_clip.close()
    del chat_clip
    gc.collect()

    proc.stdin.close()
    proc.wait()

    if proc.returncode != 0:
        raise RuntimeError("FFmpeg hstack failed")

    print(f"✅ FFmpeg hstack FINISHED for {video.name}")

    # Skip deletion when this script is executed directly (e.g. for quick testing)
    if not getattr(sys, "_chat_overlay_run_direct", False):
        try:
            chat_log.unlink()
        except Exception:
            pass
    return output_path

# ---------------------------------------------------------------------------
# CLI convenience – hard-wired defaults                          
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    # Usage:
    #   python chat_overlay.py <video_path> [chat_dir] [output_path] [twitch_id]
    if len(sys.argv) < 2:
        # Auto-discover the newest MP4 inside ./rendertesting when no arguments are provided
        rt_dir = Path("rendertesting")
        try:
            src = max(rt_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
        except ValueError:
            # Fallback to old default if nothing is found
            src = DEFAULT_VIDEO
        chat_dir = rt_dir
        out_path = None
        tw_id = "494543675"
    else:
        src = Path(sys.argv[1])
        chat_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_CHAT_DIR
        out_path = sys.argv[3] if len(sys.argv) > 3 else None
        tw_id = sys.argv[4] if len(sys.argv) > 4 else "494543675"

    # Flag that we're running this module as a script so the helper can adjust behaviour
    sys._chat_overlay_run_direct = True

    print(f"🎬 Processing {src} …")
    out = overlay_chat_on_video(str(src), str(chat_dir), out_path, tw_id)
    size_mb = Path(out).stat().st_size / (1024 * 1024)
    print(f"✅ Done → {out}  ({size_mb:.1f} MB)")