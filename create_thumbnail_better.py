# Standard library imports
import os
import tempfile
import subprocess
import json
import re

# Local import
from gemini import ask_gemini_with_video

def create_thumbnail_better(clip_path: str, streamer_name: str):
    video_title = os.path.splitext(os.path.basename(clip_path))[0]  # Extract title from filename

    # Helper to obtain duration using ffprobe
    def _get_duration_seconds(path: str) -> float:
        """Return duration of video in seconds using ffprobe (requires ffmpeg installed)."""
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    path,
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            return float(result.stdout.strip())
        except Exception:
            # If we can't detect duration, assume it's short enough and skip trimming
            return 0.0

    # Trim to 10 minutes (600 seconds) if necessary
    TRIM_THRESHOLD = 600  # seconds
    temp_trim_path = None
    use_path = clip_path

    duration = _get_duration_seconds(clip_path)
    if duration and duration > TRIM_THRESHOLD:
        # Create temporary file for trimmed video
        fd, temp_path = tempfile.mkstemp(suffix=".mp4")
        os.close(fd)  # We only need the path; ffmpeg will write to it
        temp_trim_path = temp_path

        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    clip_path,
                    "-t",
                    str(TRIM_THRESHOLD),
                    "-c",
                    "copy",
                    temp_trim_path,
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
            use_path = temp_trim_path
        except subprocess.CalledProcessError as e:
            # If trimming fails, fall back to original video
            print(f"Warning: failed to trim video, using full clip. Error: {e}")
            # Clean up temp file if it exists
            if os.path.exists(temp_trim_path):
                os.remove(temp_trim_path)
            temp_trim_path = None

    prompt = f"""You've been given a youtube video and a title.
    
    First, watch the video fully through and understand the content and its purpose.
    Then, watch it again and identify ALL the most important core moments throughout the video.
    
    Next, for each important moment you identified, consider it as a potential thumbnail candidate:
    - Evaluate the visual quality and clarity of the content at that timestamp
    - Check if the content is engaging and represents the video well
    - Ensure it's not facecam or other non-core content
    - Verify the content would make a compelling thumbnail
    
    Then, compare ALL your candidate thumbnails and select the BEST one based on:
    1. Visual quality and clarity (high resolution, not blurry)
    2. Content importance and relevance to the video
    3. Thumbnail appeal and engagement potential
    4. Technical suitability (good lighting, clear details)
    
    MAKE SURE NOT TO GET {streamer_name}'S FACECAM IN THE THUMBNAIL.
    The aspect ratio of the thumbnail MUST be 16:9.
    MAKE SURE THE CONTENT IN THE THUMBNAIL IS HIGH QUALITY AND CLEAR, SO DON'T CHOOSE ANY LOWRES CONTENT.
    
    Return the EXACT timestamp along with x,y, width, height for the BEST candidate in JSON format.
    Example:
    {{
        "description": "This is a description of the content and why it's the best choice among all candidates",
        "timestamp": "00:00:00",
        "x": 0,
        "y": 0,
        "width": 100,
        "height": 100
    }}

    The video title is: {video_title}

    Before returning the final thumbnail, follow this evaluation process in your THINKING:
    1. List at least 3-5 potential thumbnail candidates with their timestamps
    2. For each candidate, evaluate: visual quality, content relevance, and thumbnail appeal
    3. Compare all candidates side by side
    4. Select the best one based on overall quality and appeal
    5. Double-check: Is this thumbnail high quality and clear?
    6. Double-check: Does it contain the most important and engaging content?
    7. Double-check: Does it contain what I THINK it does? I must verify my assessment.
    8. Double-chec: Does it contain the facecam of the streamer {streamer_name}? IT BETTER NOT.
    8. If all checks pass, return the best thumbnail. If not, reconsider your candidates.
    """
    try:
        response = ask_gemini_with_video(use_path, prompt,model="gemini-2.5-pro")
        print(response)
        # ------------------------------------------------------------
        # Parse JSON from Gemini response
        # ------------------------------------------------------------
        try:
            clip_info = json.loads(response)
        except json.JSONDecodeError:
            # Try to locate first JSON block in the response
            match = re.search(r"\{.*\}", response, re.DOTALL)
            if not match:
                raise ValueError("Could not parse JSON data from Gemini response")
            clip_info = json.loads(match.group(0))

        timestamp = clip_info.get("timestamp")
        # Ensure numeric values are ints (Gemini may return strings)
        def _to_int(val):
            try:
                return int(float(val))
            except Exception:
                return None

        x = _to_int(clip_info.get("x"))
        y = _to_int(clip_info.get("y"))
        width = _to_int(clip_info.get("width"))
        height = _to_int(clip_info.get("height"))

        if None in (timestamp, x, y, width, height):
            raise ValueError("Gemini response missing required keys: timestamp, x, y, width, height")

        # Ensure width and height are even numbers (some codecs require this)
        if width and height:
            width -= width % 2
            height -= height % 2

        # ------------------------------------------------------------
        # Extract the thumbnail using ffmpeg
        # ------------------------------------------------------------
        output_dir = os.path.dirname(clip_path)
        # Sanitize title to filesystem-friendly name
        safe_title = re.sub(r"[^A-Za-z0-9_.-]", "_", video_title)
        thumbnail_path = os.path.join(output_dir, f"{safe_title}_thumbnail.jpg")

        def _run_ffmpeg(cmd_list):
            """Helper to run ffmpeg and print stderr on failure."""
            result = subprocess.run(cmd_list, capture_output=True, text=True)
            if result.returncode != 0:
                print("ffmpeg stderr:\n", result.stderr)
                return False
            return True

        # Primary attempt with crop filter
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",  # overwrite
            "-ss", timestamp,
            "-i", use_path,
            "-vframes", "1",
            "-vf", f"crop={width}:{height}:{x}:{y}",
            thumbnail_path,
        ]

        success = _run_ffmpeg(ffmpeg_cmd)

        # Fallback: try without crop (full frame)
        if not success:
            print("Retrying thumbnail extraction without crop filter…")
            ffmpeg_cmd_no_crop = [
                "ffmpeg",
                "-y",
                "-ss", timestamp,
                "-i", use_path,
                "-vframes", "1",
                thumbnail_path,
            ]
            success = _run_ffmpeg(ffmpeg_cmd_no_crop)

        if not success:
            print("Failed to extract thumbnail after retries.")
            thumbnail_path = None

        # Print both the parsed response and location of thumbnail
        print("Gemini response parsed:", clip_info)
        if thumbnail_path:
            print(f"Thumbnail saved to: {thumbnail_path}")

        return clip_info
    finally:
        # Remove temporary trimmed file if it was created
        if temp_trim_path and os.path.exists(temp_trim_path):
            try:
                os.remove(temp_trim_path)
            except OSError as e:
                print(f"Warning: failed to delete temp file {temp_trim_path}: {e}")

def process_all_videos(folder: str = "finishedvideos"):
    """Run create_thumbnail_better on every video file in the folder."""
    supported_ext = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
    for fname in os.listdir(folder):
        full_path = os.path.join(folder, fname)
        if not os.path.isfile(full_path):
            continue
        ext = os.path.splitext(fname)[1].lower()
        if ext not in supported_ext:
            continue

        # Skip if a thumbnail already exists
        base_title = os.path.splitext(fname)[0]
        safe_title = re.sub(r"[^A-Za-z0-9_.-]", "_", base_title)
        thumb_path = os.path.join(folder, f"{safe_title}_thumbnail.jpg")
        # if os.path.exists(thumb_path):
        #     print(f"Thumbnail already exists for {fname}, skipping...")
        #     continue

        print(f"\n=== Processing {fname} ===")
        try:
            create_thumbnail_better(full_path, "xQc")
        except Exception as e:
            print(f"Error processing {fname}: {e}")

if __name__ == "__main__":
    process_all_videos()
