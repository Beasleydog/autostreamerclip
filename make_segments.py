import os
import glob
import subprocess
import tempfile
import shutil
from pathlib import Path
import sys
import time
from prompts.combine_clips_to_segments_prompt import build_combine_clips_to_segments_prompt
from gemini import ask_gemini
from segment_post_processor import SegmentPostProcessor


def read_all_responses(responses_dir):
    """Read all response files and combine their content"""
    all_content = []
    
    # Get all txt files in the responses directory
    response_files = glob.glob(os.path.join(responses_dir, "*.txt"))
    response_files.sort()  # Sort by filename to maintain order
    
    for file_path in response_files:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            all_content.append(content)
    
    return "\n\n".join(all_content)

def backup_gemini_segment_response(gemini_response: str, backup_folder: str = None):
    """Create a backup copy of the raw Gemini response for segment creation"""
    # Only backup if backup_folder is provided
    if not backup_folder:
        return False
        
    # Create backup folder if it doesn't exist
    os.makedirs(backup_folder, exist_ok=True)
    
    # Create filename with timestamp
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    backup_filename = f"gemini_segment_response_{timestamp}.txt"
    backup_path = os.path.join(backup_folder, backup_filename)
    
    # Save the raw response
    try:
        with open(backup_path, "w", encoding="utf-8") as f:
            f.write(f"Gemini Segment Response\n")
            f.write(f"Generated at: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"{'='*80}\n\n")
            f.write(gemini_response)
        
        print(f"💾 Backed up Gemini segment response to: {backup_path}")
        return True
    except Exception as e:
        print(f"⚠️  Failed to backup Gemini response: {e}")
        return False

def parse_gemini_response(response_text):
    """Parse Gemini's response to extract segments and clip information"""
    segments = []
    current_segment = None
    current_clips = []
    
    lines = response_text.strip().split('\n')
    i = 0
    
    while i < len(lines):
        line = lines[i].strip()
        
        if line == "START SEGMENT":
            # Start of a new segment
            if current_segment:
                segments.append({
                    'name': current_segment,
                    'clips': current_clips
                })
            
            current_clips = []
            i += 1
            # Next line should be SEGMENT_NAME
            if i < len(lines) and lines[i].startswith("SEGMENT_NAME:"):
                current_segment = lines[i].replace("SEGMENT_NAME:", "").strip()
            i += 1
            
        elif line == "START CLIPS":
            i += 1
            # Read clip information until END CLIPS
            while i < len(lines) and lines[i].strip() != "END CLIPS":
                clip_line = lines[i].strip()
                if clip_line.startswith("CLIP_FILE:"):
                    clip_file = clip_line.replace("CLIP_FILE:", "").strip()
                    i += 1
                    if i < len(lines) and lines[i].startswith("START:"):
                        start_time = lines[i].replace("START:", "").strip()
                        i += 1
                        if i < len(lines) and lines[i].startswith("END:"):
                            end_time = lines[i].replace("END:", "").strip()
                            current_clips.append({
                                'file': clip_file,
                                'start': start_time,
                                'end': end_time
                            })
                i += 1
            i += 1  # Skip END CLIPS
            
        elif line == "END SEGMENT":
            # End of current segment
            if current_segment:
                segments.append({
                    'name': current_segment,
                    'clips': current_clips
                })
                current_segment = None
                current_clips = []
            i += 1
        else:
            i += 1
    
    # Add the last segment if exists
    if current_segment:
        segments.append({
            'name': current_segment,
            'clips': current_clips
        })
    
    return segments

def time_to_seconds(time_str):
    """Convert a timestamp string to seconds.

    Accepted formats:
    1. HH:MM:SS (e.g., "01:05:30")
    2. MM:SS    (e.g., "05:30")
    3. Raw seconds as int or float (e.g., "600" or "600.8") – rounded to the nearest second.
    """

    time_str = time_str.strip()

    # Colon-delimited formats first
    if ':' in time_str:
        parts = time_str.split(':')
        if len(parts) == 2:
            minutes, seconds = parts
            return int(minutes) * 60 + int(seconds)
        elif len(parts) == 3:
            hours, minutes, seconds = parts
            return int(hours) * 3600 + int(minutes) * 60 + int(seconds)
    else:
        # Try raw seconds (int or float)
        try:
            return int(round(float(time_str)))
        except ValueError:
            pass

    # If we reach here, the format is unknown
    raise ValueError(f"Invalid time format: {time_str}")

def create_temp_clip(input_file, start_time, end_time, temp_dir):
    """Create a temporary clip from the specified time range"""
    # Create unique temp filename using start and end times (replace colons for filesystem safety)
    safe_start = start_time.replace(':', '_')
    safe_end = end_time.replace(':', '_')
    base_name = os.path.splitext(os.path.basename(input_file))[0]
    temp_output = os.path.join(temp_dir, f"temp_{base_name}_{safe_start}_to_{safe_end}.mp4")
    
    # Convert times to seconds
    start_seconds = time_to_seconds(start_time)
    end_seconds = time_to_seconds(end_time)
    duration = end_seconds - start_seconds
    
    # Get the total duration of the input file
    try:
        cmd = [
            'ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
            '-of', 'csv=p=0', input_file
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        total_duration = float(result.stdout.strip())
        
        # If end time is within 3 seconds of the total duration and start time is within 3 seconds of the total duration, use the full clip
        if end_seconds >= total_duration - 3 and start_seconds <= 3:
            print(f"Using full clip for {os.path.basename(input_file)} (end time {end_seconds}s vs total {total_duration}s)")
            # Copy the entire file
            cmd = [
                'ffmpeg', '-i', input_file,
                '-c', 'copy',  # Copy without re-encoding for speed
                '-y',  # Overwrite output file
                temp_output
            ]
        else:
            # Use ffmpeg to cut the clip
            cmd = [
                'ffmpeg',
                '-ss', str(start_seconds),
                '-i', input_file,
                '-t', str(duration),
                '-c', 'copy',  # Copy without re-encoding for speed
                '-y',  # Overwrite output file
                temp_output
            ]
    except subprocess.CalledProcessError:
        # Fallback to original cutting method if ffprobe fails
        cmd = [
            'ffmpeg',
            '-ss', str(start_seconds),
            '-i', input_file,
            '-t', str(duration),
            '-c', 'copy',  # Copy without re-encoding for speed
            '-y',  # Overwrite output file
            temp_output
        ]
    
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return temp_output
    except subprocess.CalledProcessError as e:
        # Decode and show FFmpeg output so the root cause is visible (e.g. ENOSPC – no space left on device)
        stdout = e.stdout.decode("utf-8", errors="ignore") if isinstance(e.stdout, (bytes, bytearray)) else (e.stdout or "")
        stderr = e.stderr.decode("utf-8", errors="ignore") if isinstance(e.stderr, (bytes, bytearray)) else (e.stderr or "")
        print("Error creating temp clip from FFmpeg:")
        print(stdout)
        print(stderr)
        return None

def combine_clips(clip_files, output_file):
    """Combine multiple clips into a single video file"""
    if not clip_files:
        return False
    
    # Create a temporary file list for ffmpeg
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        for clip_file in clip_files:
            f.write(f"file '{clip_file}'\n")
        file_list_path = f.name
    
    try:
        # Use ffmpeg to concatenate clips
        cmd = [
            'ffmpeg',
            '-f', 'concat',
            '-safe', '0',
            '-i', file_list_path,
            '-c', 'copy',  # Copy without re-encoding
            '-y',  # Overwrite output file
            output_file
        ]
        
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError as e:
        # Decode and show FFmpeg output so the root cause is visible (e.g. ENOSPC – no space left on device)
        stdout = e.stdout.decode("utf-8", errors="ignore") if isinstance(e.stdout, (bytes, bytearray)) else (e.stdout or "")
        stderr = e.stderr.decode("utf-8", errors="ignore") if isinstance(e.stderr, (bytes, bytearray)) else (e.stderr or "")
        print("Error combining clips with FFmpeg:")
        print(stdout)
        print(stderr)
        return False
    finally:
        # Clean up the temporary file list
        os.unlink(file_list_path)

def create_segments(segments, clips_dir, output_dir, responses_dir, config_path="configs/xqc.json", auto_upload=True, ignore_latest_clips=0):
    """Create video segments from the parsed segment data and clean up original files"""
    # Create output directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)
    
    # Initialize post processor
    post_processor = SegmentPostProcessor(config_path)
    
    # Load config to get video description
    import json
    video_description = None
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
            video_description = config.get('video_description')
            if video_description:
                video_description = video_description.replace('\\n', '\n')
    except Exception as e:
        print(f"⚠️ Could not load video description from config: {e}")
    
    # Track all clips that were successfully used in segments
    used_clips = set()
    
    for i, segment in enumerate(segments):
        print(f"Processing segment {i+1}/{len(segments)}: {segment['name']}")
        
        # Create a temporary directory for this segment
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_clips = []
            
            # Create temporary clips for each clip in the segment
            for clip_info in segment['clips']:
                clip_file = os.path.join(clips_dir, clip_info['file'])
                
                if not os.path.exists(clip_file):
                    print(f"Warning: Clip file not found: {clip_file}")
                    continue
                
                temp_clip = create_temp_clip(
                    clip_file,
                    clip_info['start'],
                    clip_info['end'],
                    temp_dir
                )
                
                if temp_clip:
                    temp_clips.append(temp_clip)
                    # Track this clip as used
                    used_clips.add(clip_info['file'])
            
            if temp_clips:
                # Create a safe filename for the segment
                safe_name = "".join(c for c in segment['name'] if c.isalnum() or c in (' ', '-', '_')).rstrip()
                safe_name = safe_name.replace(' ', '_')
                output_file = os.path.join(output_dir, f"{safe_name}.mp4")
                
                # Combine the temporary clips
                if combine_clips(temp_clips, output_file):
                    print(f"Successfully created segment: {output_file}")
                    
                    # Post-process the segment (thumbnail creation and YouTube upload)
                    print(f"📸 Post-processing segment...")
                    result = post_processor.process_segment(
                        video_path=output_file,
                        title=segment['name'],
                        create_thumbnail=True,
                        upload_to_youtube=auto_upload,
                        description=video_description
                    )
                    
                    if result['success'] or result['thumbnail_path']:
                        print(f"✨ Post-processing complete for: {segment['name']}")
                        if not auto_upload:
                            print(f"   (YouTube upload skipped)")
                    else:
                        print(f"⚠️  Post-processing had issues for: {segment['name']}")
                else:
                    print(f"Failed to create segment: {segment['name']}")
            else:
                print(f"No valid clips found for segment: {segment['name']}")
    
    # Clean up clips: delete all clips up to and including the latest used clip
    if used_clips:
        # Get all clip files in the directory, sorted by filename
        all_clip_files = glob.glob(os.path.join(clips_dir, "*.mp4"))
        all_clip_files.sort()
        all_clip_filenames = [os.path.basename(clip) for clip in all_clip_files]
        
        # Find the latest (highest index) clip that was used
        latest_used_index = -1
        for clip_filename in used_clips:
            try:
                index = all_clip_filenames.index(clip_filename)
                latest_used_index = max(latest_used_index, index)
            except ValueError:
                # Clip not found in directory (shouldn't happen but handle gracefully)
                continue
        
        if latest_used_index >= 0:
            # Delete all clips from index 0 to latest_used_index (inclusive)
            clips_to_delete = all_clip_filenames[:latest_used_index + 1]
            print(f"Deleting all clips up to and including the latest used clip (index {latest_used_index})...")
            print(f"This will delete {len(clips_to_delete)} clips and their corresponding text files")
            
            for clip_filename in clips_to_delete:
                # Delete the clip file
                clip_path = os.path.join(clips_dir, clip_filename)
                if os.path.exists(clip_path):
                    try:
                        os.remove(clip_path)
                        print(f"Deleted clip: {clip_filename}")
                    except Exception as e:
                        print(f"Error deleting clip {clip_filename}: {e}")
                
                # Delete the corresponding text file
                text_file = os.path.splitext(clip_filename)[0] + ".txt"
                text_path = os.path.join(responses_dir, text_file)
                if os.path.exists(text_path):
                    try:
                        os.remove(text_path)
                        print(f"Deleted text file: {text_file}")
                    except Exception as e:
                        print(f"Error deleting text file {text_file}: {e}")
            
            print("Cleanup completed!")
        else:
            print("No valid clips found to delete")
    else:
        print("No clips were used, skipping cleanup.")

def get_latest_clips(clips_dir, responses_folder, num_clips=2):
    """Get the latest N clip files from the clips directory that have matching responses"""
    # Get all mp4 files in the clips directory
    clip_files = glob.glob(os.path.join(clips_dir, "*.mp4"))
    
    # Sort by filename (assuming they have timestamps in the name)
    clip_files.sort()
    
    # Filter to only include clips that have matching responses
    if os.path.exists(responses_folder):
        matched_files = filter_matched_clips_and_responses(clips_dir, responses_folder)
        # Filter clip_files to only include matched ones
        clip_files = [f for f in clip_files if os.path.splitext(os.path.basename(f))[0] in matched_files]
    
    # Get the latest N clips
    if num_clips <= 0:
        latest_clips = []
    else:
        latest_clips = clip_files[-num_clips:] if len(clip_files) >= num_clips else clip_files
    
    # Return just the filenames (not full paths)
    return [os.path.basename(clip_file) for clip_file in latest_clips]

def _compute_segment_duration_seconds(segment):
    """Compute the total duration (sum of clip durations) for a segment"""
    total = 0
    for clip in segment.get('clips', []):
        try:
            start_sec = time_to_seconds(clip['start'])
            end_sec = time_to_seconds(clip['end'])
            if end_sec > start_sec:
                total += end_sec - start_sec
        except Exception as e:
            # If any duration fails to parse, skip that piece
            print(f"⚠️  Could not parse clip times for '{clip.get('file')}', skipping duration calc: {e}")
            continue
    return total

def filter_segments_with_latest_clips(segments, latest_clips):
    """Filter out segments that use any of the latest clips."""
    filtered_segments = []
    rejected_segments = []

    for segment in segments:
        # Determine if segment uses any of the latest clips
        uses_latest = any(clip_info['file'] in latest_clips for clip_info in segment['clips'])

        if uses_latest:
            # Calculate segment duration for logging
            duration_sec = _compute_segment_duration_seconds(segment)
            rejected_segments.append(segment)
            print(f"Rejecting segment '{segment['name']}' (duration {duration_sec/60:.1f} min) - uses latest clips")
        else:
            filtered_segments.append(segment)

    return filtered_segments, rejected_segments
def filter_matched_clips_and_responses(clips_dir, responses_folder):
    """Filter out clips without responses and responses without clips"""
    # Get all clip and response filenames
    clip_files = {os.path.splitext(os.path.basename(f))[0] for f in glob.glob(os.path.join(clips_dir, "*.mp4"))}
    response_files = {os.path.splitext(os.path.basename(f))[0] for f in glob.glob(os.path.join(responses_folder, "*.txt"))}
    
    # Find intersection - clips that have matching responses
    matched_files = clip_files & response_files
    
    if len(matched_files) < len(clip_files) or len(matched_files) < len(response_files):
        orphaned_clips = clip_files - matched_files
        orphaned_responses = response_files - matched_files
        print(f"🧹 Filtered out {len(orphaned_clips)} clips and {len(orphaned_responses)} responses without matches")
    
    return matched_files

def run_full_segment_creation(responses_folder, clips_dir, segments_dir, streamer_name, examples, gemini_backup_folder=None, auto_upload=True, config_path="configs/xqc.json", latest_clip_ignore_count: int = 2):
    """
    Run the full segment creation pipeline:
    - Read all response files
    - Call Gemini for segmentation
    - Parse Gemini response
    - Filter out segments using the latest 2 clips
    - Create segments and clean up used files
    
    Args:
        responses_folder: Directory containing analysis response files
        clips_dir: Directory containing video clips
        segments_dir: Directory where segments will be created
        streamer_name: Name of the streamer (used in prompts)
        examples: List of example content for prompts
        gemini_backup_folder: Optional directory to backup Gemini responses
        auto_upload: Whether to automatically upload segments to YouTube
        config_path: Path to configuration file for post-processing
        latest_clip_ignore_count: How many of the most-recent clips should be temporarily ignored when generating segments (set to 0 to allow using every clip).
    """
    # Check if there are at least 3 response files before proceeding
    response_files = glob.glob(os.path.join(responses_folder, "*.txt"))
    if len(response_files) < 3:
        print(f"Not enough response files to run segment creation (found {len(response_files)}, need at least 3). Skipping.")
        return

    #Check if there are at least 3 clips in the clips directory
    clip_files = glob.glob(os.path.join(clips_dir, "*.mp4"))
    if len(clip_files) < 3:
        print(f"Not enough clips to run segment creation (found {len(clip_files)}, need at least 3). Skipping.")
        return
    
    # Filter out clips and responses that don't have matches
    matched_files = filter_matched_clips_and_responses(clips_dir, responses_folder)
    if len(matched_files) < 3:
        print(f"Not enough matched clips/responses to run segment creation (found {len(matched_files)}, need at least 3). Skipping.")
        return
    
    try:
        print("🔄 Starting segment creation process...")
        # Read all response files
        print("📖 Reading analysis response files...")
        all_responses = read_all_responses(responses_folder)
        if not all_responses.strip():
            print("⚠️ No analysis responses found, skipping segment creation")
            return
        # Send to Gemini for segmentation
        print("🤖 Sending to Gemini for segmentation...")
        try:
            gemini_response = ask_gemini(
                build_combine_clips_to_segments_prompt(streamer_name, examples) + "\n\n" + all_responses
            )
            print("✅ Received response from Gemini")
            
            # Backup the raw Gemini response if backup folder is provided
            backup_gemini_segment_response(gemini_response, gemini_backup_folder)
            
        except Exception as e:
            print(f"❌ Error getting response from Gemini: {e}")
            return
        # Parse Gemini response
        print("🔍 Parsing Gemini response...")
        segments = parse_gemini_response(gemini_response)
        if not segments:
            print("⚠️ No segments found in Gemini response")
            return
        print(f"📊 Found {len(segments)} segments:")
        for i, segment in enumerate(segments):
            print(f"  {i+1}. {segment['name']} ({len(segment['clips'])} clips)")
        # Get latest clips and optionally filter segments
        if latest_clip_ignore_count > 0:
            print("🔍 Checking for segments using latest clips...")
            latest_clips = get_latest_clips(clips_dir, responses_folder, latest_clip_ignore_count)
            print(f"📹 Latest clips: {latest_clips}")
            filtered_segments, rejected_segments = filter_segments_with_latest_clips(segments, latest_clips)

            if rejected_segments:
                print(f"⏭️  Skipping segmentation run – Gemini used one of the newest {latest_clip_ignore_count} clips (activity still ongoing).")
                return

            print(f"✅ Proceeding with {len(filtered_segments)} segments (none touch newest clips)")
        else:
            filtered_segments = segments
        # Create video segments
        if filtered_segments:
            print("🎬 Creating video segments...")
            create_segments(filtered_segments, clips_dir, segments_dir, responses_folder, config_path=config_path, auto_upload=auto_upload)
            print("✅ Segment creation completed!")
        else:
            print("⚠️ No segments to create after filtering")
    except Exception as e:
        print(f"❌ Error in segment creation: {e}")

def main():
    # # Test parsing the specific Gemini response file
    # backup_file = "xqc_gemini_segment_responses_backup/gemini_segment_response_20250624_022804.txt"
    
    # if not os.path.exists(backup_file):
    #     print(f"❌ Backup file not found: {backup_file}")
    #     return
    
    # print("📖 Reading Gemini response file...")
    # with open(backup_file, 'r', encoding='utf-8') as f:
    #     response_content = f.read()
    
    # print("🔍 Parsing Gemini response...")
    # segments = parse_gemini_response(response_content)
    
    # if not segments:
    #     print("⚠️ No segments found in Gemini response")
    #     return
    
    # print(f"📊 Found {len(segments)} segments:")
    # for i, segment in enumerate(segments):
    #     print(f"  {i+1}. {segment['name']} ({len(segment['clips'])} clips)")
    #     # Print first few clips as example
    #     for j, clip in enumerate(segment['clips'][:3]):
    #         print(f"    - {clip['file']} ({clip['start']} - {clip['end']})")
    #     if len(segment['clips']) > 3:
    #         print(f"    ... and {len(segment['clips']) - 3} more clips")
    #     print()

    temp_clip = create_temp_clip(
                    "C:/Users/beasl/.stuff/fun/autostreamercliproundtwo/xqc_part_of_vod/fillvod_part003.mp4",
                    "00:31",
                    "00:32",
                    "C:/Users/beasl/.stuff/fun/autostreamercliproundtwo/tempclips"
                )
    print(temp_clip)

if __name__ == "__main__":
    main()