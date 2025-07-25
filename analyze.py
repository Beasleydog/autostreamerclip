import os
import glob
import subprocess
import sys
import time
from typing import Callable, Optional
from pathlib import Path
from prompts.simple_analyze_prompt import build_simple_analyze_prompt
from gemini import ask_gemini_with_video
import json
import gc
import argparse


LIGHT_REENCODE=False  # Legacy flag kept for compatibility; compression is disabled

def get_video_duration(video_path: str, retries: int = 5, delay: float = 2.0) -> float:
    """Get video duration in seconds using ffprobe with retry logic."""
    video_path = video_path.replace("\\", "/")
    
    for attempt in range(retries):
        try:
            result = subprocess.run([
                "ffprobe", "-v", "error", "-show_entries", 
                "format=duration", "-of", "csv=p=0", video_path
            ], capture_output=True, text=True, check=True)
            
            duration = float(result.stdout.strip())
            if attempt > 0:
                print(f"✅ ffprobe succeeded on attempt {attempt + 1}")
            return duration
            
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.strip() if e.stderr else "Unknown ffprobe error"
            
            if attempt < retries - 1:
                print(f"⚠️  ffprobe attempt {attempt + 1} failed: {error_msg}")
                print(f"   Retrying in {delay}s... ({retries - attempt - 1} attempts left)")
                time.sleep(delay)
            else:
                print(f"❌ ffprobe failed after {retries} attempts: {error_msg}")
                return -1
                
        except ValueError as e:
            print(f"Error parsing duration for {video_path}: {e}")
            return -1
    
    return -1


def is_valid_mp4_file(file_path: str) -> bool:
    """
    Check if an MP4 file is valid and complete using ffprobe.
    
    Args:
        file_path: Path to the MP4 file to validate
        
    Returns:
        bool: True if file is valid, False otherwise
    """
    try:
        # Try to get basic file info - this will fail if file is corrupted or incomplete
        result = subprocess.run([
            "ffprobe", "-v", "quiet", "-show_entries", 
            "format=duration", "-of", "csv=p=0", file_path
        ], capture_output=True, text=True, timeout=10)
        
        if result.returncode != 0:
            return False
            
        duration = float(result.stdout.strip())
        return duration > 0
        
    except (subprocess.CalledProcessError, ValueError, subprocess.TimeoutExpired):
        return False


def get_history_from_responses(responses_folder: str, make_history: Optional[Callable[[list[str]], str]] = None) -> str:
    """
    Read all existing response files and concatenate them to create history.
    
    Args:
        responses_folder: Folder containing previous response files
        
    Returns:
        str: Concatenated history from all previous responses
    """
    if not os.path.exists(responses_folder):
        return ""
    
    history_parts = []
    response_files = glob.glob(os.path.join(responses_folder, "*.txt"))
    
    # Sort files by creation time to maintain chronological order
    response_files.sort(key=os.path.getctime)
    
    for response_file in response_files:
        try:
            with open(response_file, "r", encoding="utf-8") as f:
                content = f.read()
                history_parts.append(content)
        except Exception as e:
            print(f"Warning: Could not read response file {response_file}: {e}")
            continue
    
    if make_history:
        return make_history(history_parts)
    else:
        return "\n\n" + "="*50 + "\n\n".join(history_parts) if history_parts else ""



def analyze_single_video(video_path: str, streamer_name: str, examples: list[str], responses_folder: str = "responses", make_history: Callable[[list[str]], str] = lambda _: "") -> bool:
    """
    Analyze a single video and save the result to the responses folder.
    
    Args:
        video_path: Path to the video file to analyze
        streamer_name: Name of the streamer
        examples: List of examples (kept for compatibility but not used with simple_analyze_prompt)
        responses_folder: Folder to save the analysis results (default: "responses")
    
    Returns:
        bool: True if analysis was successful, False otherwise
    """
    # Create responses folder if it doesn't exist
    os.makedirs(responses_folder, exist_ok=True)
    
    # Check if video file exists
    if not os.path.exists(video_path):
        print(f"Video file not found: {video_path}")
        return False
    
    video_name = os.path.basename(video_path)
    print(f"Processing: {video_name}")
    
    # # First validate the file is complete and readable
    # if not is_valid_mp4_file(video_path):
    #     print(f"❌ {video_name} - File validation failed (incomplete or corrupted)")
    #     return False
    
    # Check video duration with retry logic
    duration = get_video_duration(video_path)
    if duration == -1:
        print(f"❌ {video_name} - Could not get duration after retries")
        return False
        
    if duration < 30.0:
        print(f"⏭️  Skipping {video_name} - too short ({duration:.1f}s < 30s)")
        return False
    
    print(f"Duration: {duration:.1f}s - Analyzing...")
    
    try:
        # Get history from previous responses
        history = get_history_from_responses(responses_folder, make_history)
        if history:
            print(f"📚 Found history from {len(glob.glob(os.path.join(responses_folder, '*.txt')))} previous responses")
        
        # Fetch current Twitch category so the model has additional context
        category = _get_current_twitch_category(streamer_name)
        if category:
            print(f"📂 Current Twitch category detected: {category}")
        else:
            print("📂 Twitch category could not be determined – continuing without it")
        
        # Analyze the video using Gemini with simple analyze prompt, including category if available
        analysis = ask_gemini_with_video(
            video_path,
            build_simple_analyze_prompt(streamer_name, history, category),
            model="gemini-2.5-flash",
        )
        
        # Create individual response file
        response_filename = os.path.splitext(video_name)[0] + ".txt"
        response_path = os.path.join(responses_folder, response_filename)
        
        with open(response_path, "w", encoding="utf-8") as f:
            f.write(f"Clip: {video_name}\n")
            f.write(f"Duration: {duration:.1f}s\n")
            f.write(f"Analyzed at: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"{'='*80}\n")
            f.write(analysis)
            f.write(f"\n")
        
        print(f"✅ Analysis complete and saved to {response_path}")
        return True
        
    except Exception as e:
        print(f"❌ Error analyzing {video_name}: {e}")
        # Still create error file for record keeping
        response_filename = os.path.splitext(video_name)[0] + ".txt"
        response_path = os.path.join(responses_folder, response_filename)
        
        with open(response_path, "w", encoding="utf-8") as f:
            f.write(f"Clip: {video_name}\n")
            f.write(f"Duration: {duration:.1f}s\n")
            f.write(f"Analyzed at: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"ERROR: {e}\n")
            f.write(f"{'='*80}\n")
        
        return False
    finally:
        # Explicitly trigger garbage collection to free memory used by large objects
        gc.collect()


# --------------------------------------------------------------------------------------
# Helper utilities
# --------------------------------------------------------------------------------------


def _get_current_twitch_category(channel_name: str, retries: int = 3, delay: float = 2.0) -> str | None:
    """Return the current Twitch category/game for *channel_name* using Streamlink.

    This avoids the need for Twitch API credentials by leveraging Streamlink's
    ``--json`` flag which already queries Twitch's public endpoints internally.

    Args:
        channel_name: The Twitch channel (login name) whose category should be
            retrieved.
        retries: How many attempts to try if Streamlink returns no useful data.
        delay: Seconds to wait between retries.

    Returns:
        The category name if it could be determined, otherwise ``None``.
    """

    for attempt in range(retries):
        try:
            # Capture raw bytes to avoid UnicodeDecodeErrors on Windows consoles.
            result = subprocess.run(
                ["streamlink", "--json", f"twitch.tv/{channel_name}"],
                capture_output=True,
                text=False,  # get raw bytes
                timeout=15,
            )

            if result.returncode != 0 or result.stdout is None or len(result.stdout) == 0:
                stderr_msg = result.stderr.decode("utf-8", errors="ignore") if result.stderr else ""
                raise RuntimeError(stderr_msg or "Streamlink returned empty output")

            # Decode using UTF-8 but be tolerant of errors
            stdout_str = result.stdout.decode("utf-8", errors="ignore")

            data = json.loads(stdout_str)
            category = data.get("metadata", {}).get("category") or data.get("metadata", {}).get("game")

            if category:
                return category

            # If category not present, fallthrough to retry logic
            raise KeyError("Category not found in metadata")

        except Exception as e:
            if attempt < retries - 1:
                print(f"⚠️  Attempt {attempt + 1} to retrieve category failed: {e}. Retrying in {delay}s…")
                time.sleep(delay)
            else:
                print(f"❌ Could not retrieve Twitch category for {channel_name}: {e}")

    return None

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze a single clip (sub-process mode)")
    parser.add_argument("--clip", help="Path to the MP4 clip to analyze")
    parser.add_argument("--streamer", required=False, help="Streamer / channel name")
    parser.add_argument("--responses", default="responses", help="Folder to write analysis responses")
    parser.add_argument("--config", help="JSON config file that contains example_segment_titles", default=None)

    args, unknown = parser.parse_known_args()

    if args.clip:
        # Sub-process mode --------------------------------------------------
        ex_list = []
        if args.config and os.path.exists(args.config):
            try:
                with open(args.config, "r", encoding="utf-8") as cf:
                    cfg = json.load(cf)
                    ex_list = cfg.get("example_segment_titles", []) or []
            except Exception:
                pass

        streamer = args.streamer or os.path.splitext(os.path.basename(args.clip))[0]

        ok = analyze_single_video(args.clip, streamer, ex_list, args.responses)
        sys.exit(0 if ok else 1)

    # Legacy debug path -----------------------------------------------------
    print(_get_current_twitch_category("xqc"))