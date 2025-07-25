import datetime
import json
import os
import signal
import subprocess
import sys
import time
import argparse
import threading
import shutil
from pathlib import Path
import gc  # Added for explicit memory cleanup

from make_segments import run_full_segment_creation

from chat import (
    start_watching_chat,
    stop_watching_chat,
    update_on_ad_break,
    get_on_ad_break,
    dump_chat,
    note_ad_break_start,
    note_ad_break_end,
)

# Add parent directory to path to import analyze module
from analyze import analyze_single_video

from chat_overlay import overlay_chat_on_video

class RecorderManager:
    """Manages the Streamlink->FFmpeg recording pipeline with automatic restart capabilities."""
    
    def __init__(self, channel: str, quality: str, segment_sec: int, output_dir: str, 
                 responses_folder: str = None, segments_dir: str = None, recordings_times_dir: str = None, retry_sec: int = 30,processed_recordings_folder: str = "processed_recordings"):
        self.channel = channel
        self.quality = quality
        self.segment_sec = segment_sec
        self.output_dir = output_dir
        self.processed_recordings_folder = processed_recordings_folder
        self.responses_folder = responses_folder
        self.segments_dir = segments_dir
        self.recordings_times_dir = recordings_times_dir or "recordings_times"
        self.retry_sec = retry_sec
        
        # Coordination for stopping all workers when stream ends
        self.shutdown_event = threading.Event()
        
        # Process tracking
        self.sl_proc = None
        self.ff_proc = None
        self.recorder_thread = None
        # self.timestamp_watcher_thread = None
        self.should_stop = False
        self.is_recording = False
        
        # Create directories
        os.makedirs(output_dir, exist_ok=True)
        if responses_folder:
            os.makedirs(responses_folder, exist_ok=True)
        if segments_dir:
            os.makedirs(segments_dir, exist_ok=True)
        if processed_recordings_folder:
            os.makedirs(processed_recordings_folder, exist_ok=True)
        os.makedirs(self.recordings_times_dir, exist_ok=True)
    
    def build_streamlink_cmd(self):
        """Return the Streamlink CLI command that writes the stream to stdout."""
        return [
            "streamlink",
            f"twitch.tv/{self.channel}",
            self.quality,
            "--twitch-disable-ads",
            "--stdout",
        ]
    
    def build_ffmpeg_cmd(self, out_pattern: str):
        """Return the FFmpeg CLI command that splits stdin into segment files."""
        return [
            "ffmpeg",
            "-loglevel", "warning",
            "-hide_banner",
            "-y",
            "-i", "pipe:0",
            "-c", "copy",
            "-f", "segment",
            "-segment_time", str(self.segment_sec),
            "-segment_format", "mp4",
            "-segment_format_options", "movflags=faststart+frag_keyframe+empty_moov+default_base_moof",
            "-reset_timestamps", "1",
            "-avoid_negative_ts", "make_zero",
            "-strftime", "1",
            out_pattern,
        ]
    
    def clear_folders(self):
        """Clear recording and response folders."""
        folders_to_clear = [self.output_dir,self.processed_recordings_folder]
        if self.responses_folder:
            folders_to_clear.append(self.responses_folder)
        # Don't clear segments_dir - preserve created segments
        
        for folder in folders_to_clear:
            if os.path.exists(folder):
                try:
                    for filename in os.listdir(folder):
                        file_path = os.path.join(folder, filename)
                        if os.path.isfile(file_path):
                            os.remove(file_path)
                            print(f"🗑️  Removed file: {filename}")
                        elif os.path.isdir(file_path):
                            shutil.rmtree(file_path)
                            print(f"🗑️  Removed directory: {filename}")
                    print(f"🧹 Cleared folder: {folder}")
                except Exception as e:
                    print(f"⚠️  Error clearing folder {folder}: {e}")
    
    def terminate_processes(self):
        """Safely terminate Streamlink and FFmpeg processes."""
        for proc_name, proc in [("FFmpeg", self.ff_proc), ("Streamlink", self.sl_proc)]:
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                    print(f"🛑 {proc_name} terminated")
                except subprocess.TimeoutExpired:
                    proc.kill()
                    print(f"🛑 {proc_name} killed (forced)")
                except Exception as e:
                    print(f"⚠️  Error terminating {proc_name}: {e}")
    
    def start_recording_attempt(self):
        """Start a single recording attempt. Returns True if should continue, False if should stop."""
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}] Waiting for '{self.channel}' to go live …", flush=True)

        # Launch Streamlink
        sl_cmd = self.build_streamlink_cmd()
        try:
            self.sl_proc = subprocess.Popen(sl_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            print(f"🆕 Streamlink process STARTED (PID {self.sl_proc.pid})")
        except Exception as e:
            print(f"❌ Failed to start Streamlink: {e}")
            return True  # Continue trying

        if self.sl_proc.stdout is None:
            print("❌ Failed to attach to Streamlink stdout. Retrying …")
            return True

        # Give Streamlink a moment to check if stream is available
        time.sleep(2)
        
        # Check if we should stop
        if self.should_stop:
            return False

        # Check if Streamlink failed to find a stream
        if self.sl_proc.poll() is not None:
            stderr_output = self.sl_proc.stderr.read().decode() if self.sl_proc.stderr else ""
            if "No playable streams found" in stderr_output:
                print(f"Channel '{self.channel}' is not live. Retrying in {self.retry_sec}s …")
                return True
            else:
                print(f"❌ Streamlink failed: {stderr_output}")
                return True

        # Test FFmpeg with temp pattern
        temp_out_pattern = os.path.join(self.output_dir, "temp_test_%Y%m%d-%H%M%S.mp4")
        ff_cmd = self.build_ffmpeg_cmd(temp_out_pattern)
        
        try:
            self.ff_proc = subprocess.Popen(ff_cmd, stdin=self.sl_proc.stdout, stderr=subprocess.PIPE)
            print(f"🆕 FFmpeg TEST process STARTED (PID {self.ff_proc.pid})")
        except Exception as e:
            print(f"❌ Failed to start FFmpeg: {e}")
            self.terminate_processes()
            return True

        # Give FFmpeg a moment to start
        time.sleep(3)
        
        # Check if we should stop
        if self.should_stop:
            return False

        # Check if FFmpeg failed immediately
        if self.ff_proc.poll() is not None:
            ff_stderr = self.ff_proc.stderr.read().decode() if self.ff_proc.stderr else ""
            if "Invalid data found when processing input" in ff_stderr:
                print(f"❌ Streamlink connected but no valid stream data. Channel '{self.channel}' may be offline.")
                self._cleanup_temp_files()
                return True

        # If we get here, we have a valid stream!
        print(f"[{ts}] ✅ Valid stream detected! Recording to: {self.output_dir}")

        # Kill the test FFmpeg and restart with real pattern
        self.ff_proc.terminate()
        try:
            # Wait at most 5 s for a clean shutdown
            self.ff_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # Give up and force-kill so that the pipeline can continue
            print("⚠️  FFmpeg did not exit in time after initial test – killing process")
            self.ff_proc.kill()
            # Double-check we really reaped it
            try:
                self.ff_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                # In the very unlikely case the process is still around, just move on – the OS will reap it later
                pass
        self._cleanup_temp_files()

        # Clear relevant folders now that all temporary test files are gone and we're about to start a fresh recording
        self.clear_folders()

        # Start real recording
        out_pattern = os.path.join(self.output_dir, "%Y%m%d-%H%M%S.mp4")
        ff_cmd = self.build_ffmpeg_cmd(out_pattern)
        
        try:
            self.ff_proc = subprocess.Popen(ff_cmd, stdin=self.sl_proc.stdout)
            print(f"🆕 FFmpeg RECORDING process STARTED (PID {self.ff_proc.pid})")
            self.is_recording = True
            print(f"🔴 Recording started successfully")
            
            # Start watching chat now that the streamer is confirmed live
            start_watching_chat(self.channel)
            
            # Start monitoring Streamlink health
            self.monitor_streamlink_health()
            
        except Exception as e:
            print(f"❌ Failed to start real FFmpeg: {e}")
            self.terminate_processes()
            return True

        # Wait for processes to complete or crash
        while not self.should_stop:
            # Check if either process has died
            sl_alive = self.sl_proc.poll() is None
            ff_alive = self.ff_proc.poll() is None
            
            if not sl_alive or not ff_alive:
                sl_code = self.sl_proc.returncode if not sl_alive else "running"
                ff_code = self.ff_proc.returncode if not ff_alive else "running"
                print(f"📊 Stream ended - Streamlink: {sl_code}, FFmpeg: {ff_code}")
                # Signal all worker threads to stop processing
                self.shutdown_event.set()
                break
            
            time.sleep(1)  # Check every second

        # Recording loop ended – stop watching chat
        stop_watching_chat()
        self.is_recording = False
        self.terminate_processes()
        
        if not self.should_stop:
            print(f"⏳ Stream ended. Waiting {self.retry_sec}s before retry...")
        
        return not self.should_stop
    
    def _cleanup_temp_files(self):
        """Clean up temporary test files."""
        try:
            for temp_file in os.listdir(self.output_dir):
                if temp_file.startswith("temp_test_"):
                    os.remove(os.path.join(self.output_dir, temp_file))
        except Exception as e:
            print(f"⚠️  Error cleaning temp files: {e}")
    
    def run_recorder_loop(self):
        """Main recorder loop that handles restarts automatically."""
        print(f"🎥 Starting recorder manager for channel: {self.channel}")
        # Only clear the "stream-ended" event once, right before we start
        self.shutdown_event.clear()
        
        while not self.should_stop:
            try:
                # Don't clear the event again here—let it stay set until
                # all final clips have been processed
                should_continue = self.start_recording_attempt()
                if not should_continue:
                    break
                    
                if not self.should_stop:
                    print(f"⏳ Waiting {self.retry_sec}s before retry...")
                    time.sleep(self.retry_sec)
                    
            except Exception as e:
                print(f"❌ Unexpected error in recorder loop: {e}")
                self.terminate_processes()
                if not self.should_stop:
                    time.sleep(self.retry_sec)
        
        print("🛑 Recorder manager stopped")
    
    def start(self):
        """Start the recorder in a separate thread."""
        if self.recorder_thread and self.recorder_thread.is_alive():
            print("⚠️  Recorder already running")
            return
        
        self.should_stop = False
        
        # # Start timestamp watcher thread
        # self.timestamp_watcher_thread = threading.Thread(
        #     target=watch_for_new_files, 
        #     args=(self.output_dir, self.recordings_times_dir),
        #     daemon=True
        # )
        # self.timestamp_watcher_thread.start()
        
        # Start recorder thread
        print("🧵 Creating recorder_thread")
        self.recorder_thread = threading.Thread(target=self.run_recorder_loop, daemon=True, name="recorder_thread")
        self.recorder_thread.start()
        print("▶️  Recorder manager started (recorder_thread running)")
    
    def stop(self):
        """Stop the recorder and all processes."""
        print("🛑 Stopping recorder manager...")
        self.should_stop = True
        self.terminate_processes()
        
        # The recorder thread is already a daemon, so avoid holding up process exit.
        # Join briefly to allow cleanup, but don't block for long periods.
        if self.recorder_thread and self.recorder_thread.is_alive():
            self.recorder_thread.join(timeout=1)
        
    
    def restart(self):
        """Restart the recorder."""
        print("🔄 Restarting recorder manager...")
        self.stop()
        time.sleep(2)
        self.start()
    
    def get_status(self):
        """Get current status of the recorder."""
        return {
            'is_recording': self.is_recording,
            'thread_alive': self.recorder_thread.is_alive() if self.recorder_thread else False,
            'streamlink_alive': self.sl_proc.poll() is None if self.sl_proc else False,
            'ffmpeg_alive': self.ff_proc.poll() is None if self.ff_proc else False,
        }

    def monitor_streamlink_health(self):
        """Monitor Streamlink's stderr output to detect stream interruptions."""
        if not self.sl_proc or not self.sl_proc.stderr:
            return
        
        def read_stderr():
            try:
                while self.sl_proc and self.sl_proc.poll() is None and not self.should_stop:
                    line = self.sl_proc.stderr.readline()
                    if line:
                        line_str = line.decode('utf-8', errors='ignore').strip()
                        if line_str:
                            print(f"Streamlink: {line_str}")
                            
                            # Check for ad break detection
                            if 'detected advertisement break of' in line_str.lower():
                                import re
                                # Extract the number of seconds from the message
                                match = re.search(r'(\d+)\s*seconds?', line_str)
                                if match:
                                    ad_duration = int(match.group(1))
                                    if not get_on_ad_break():
                                        print(f"🛑 Ad break detected ({ad_duration}s) - pausing chat ingestion")
                                        update_on_ad_break(True)
                                        note_ad_break_start()
                                        def resume_chat():
                                            time.sleep(ad_duration)
                                            note_ad_break_end()
                                            update_on_ad_break(False)
                                            print("✅ Ad break ended - resuming chat ingestion")
                                        threading.Thread(target=resume_chat, daemon=True).start()
                    else:
                        time.sleep(0.1)
            except Exception as e:
                print(f"Error monitoring Streamlink stderr: {e}")
        
        # Start monitoring in a separate thread
        monitor_thread = threading.Thread(target=read_stderr, daemon=True)
        print("🧵 Creating monitor_thread for Streamlink stderr")
        monitor_thread.start()
        return monitor_thread

# def build_streamlink_cmd(channel: str, quality: str):
#     """Return the Streamlink CLI command that writes the stream to stdout."""
#     return [
#         "streamlink",
#         f"twitch.tv/{channel}",
#         quality,
#         "--twitch-disable-ads",  # skip ad segments so they don't stall the pipe
#         "--stdout",               # write MPEG‑TS bytes to stdout
#     ]


# def build_ffmpeg_cmd(out_pattern: str, segment_sec: int):
#     """Return the FFmpeg CLI command that splits stdin into <segment_sec>s files."""
#     return [
#         "ffmpeg",
#         "-loglevel", "warning",
#         "-hide_banner",
#         "-y",                 # overwrite if segment exists (rare, but okay)
#         "-i", "pipe:0",       # read Streamlink's stdout
#         "-c", "copy",         # no re-encode – just copy packets
#         "-f", "segment",
#         "-segment_time", str(segment_sec),
#         "-segment_format", "mp4",  # explicitly specify MP4 format for segments
#         "-segment_format_options", "movflags=faststart+frag_keyframe+empty_moov+default_base_moof",  # fragmented MP4 flags scoped to each segment
#         "-reset_timestamps", "1",  # each file starts at 0 so players seek correctly
#         "-avoid_negative_ts", "make_zero",  # handle negative timestamps that can cause issues
#         "-strftime", "1",           # allow %Y-%m-%d style patterns
#         out_pattern,
#     ]


def clear_folders(*folders):
    """
    Clear all files from the specified folders.
    
    Args:
        *folders: Variable number of folder paths to clear
    """
    for folder in folders:
        if os.path.exists(folder):
            try:
                # Remove all files in the folder
                for filename in os.listdir(folder):
                    file_path = os.path.join(folder, filename)
                    if os.path.isfile(file_path):
                        os.remove(file_path)
                        print(f"🗑️  Removed file: {filename}")
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                        print(f"🗑️  Removed directory: {filename}")
                print(f"🧹 Cleared folder: {folder}")
            except Exception as e:
                print(f"⚠️  Error clearing folder {folder}: {e}")
        else:
            print(f"📁 Folder doesn't exist, skipping: {folder}")


def run_recorder(channel: str, quality: str, segment_sec: int, output_dir: str, 
                responses_folder: str = None, segments_dir: str = None, retry_sec: int = 30,processed_recordings_folder: str = "processed_recordings"):
    """Legacy recorder function - now uses RecorderManager internally."""
    manager = RecorderManager(channel, quality, segment_sec, output_dir, responses_folder, segments_dir, "recordings_times", retry_sec,processed_recordings_folder)
    
    # Set up signal handlers
    def _terminate(signum, frame):
        manager.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, _terminate)
    signal.signal(signal.SIGTERM, _terminate)
    
    manager.start()
    
    # Keep main thread alive
    try:
        while manager.recorder_thread.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        manager.stop()


def is_valid_mp4_file(file_path: str) -> bool:
    """
    Check if an MP4 file is valid and complete using ffprobe.
    
    Args:
        file_path: Path to the MP4 file to validate
        
    Returns:
        bool: True if file is valid, False otherwise
    """
    try:
        # Try to get basic file info - this will fail if file is corrupted
        result = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries", 
            "format=duration", "-of", "csv=p=0", file_path
        ], capture_output=True, text=True, timeout=10)
        
        if result.returncode != 0:
            return False
            
        duration = float(result.stdout.strip())
        return duration > 0
        
    except (subprocess.CalledProcessError, ValueError, subprocess.TimeoutExpired):
        return False


def get_mp4_duration(file_path: str) -> float:
    try:
        result = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries", 
            "format=duration", "-of", "csv=p=0", file_path
        ], capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            duration_str = result.stdout.strip()
            if duration_str:
                return float(duration_str)
            else:
                print(f"⚠️  ffprobe returned empty duration for {os.path.basename(file_path)}")
                return 0.0
        else:
            print(f"⚠️  ffprobe failed for {os.path.basename(file_path)}: return code {result.returncode}")
            if result.stderr:
                print(f"   stderr: {result.stderr.strip()}")
            return 0.0
            
    except (subprocess.CalledProcessError, ValueError, subprocess.TimeoutExpired) as e:
        print(f"⚠️  Exception during ffprobe for {os.path.basename(file_path)}: {e}")
        return 0.0


def run_segment_creation(responses_folder: str, clips_dir: str, segments_dir: str, 
                        streamer_name: str, examples: list[str], auto_upload: bool = True, 
                        config_path: str = "configs/xqc.json", gemini_backup_folder: str = None, latest_clip_ignore_count: int = 2):
    """
    Wrapper to call the full segment creation pipeline from make_segments.py
    
    Args:
        responses_folder: Directory containing analysis response files
        clips_dir: Directory containing video clips
        segments_dir: Directory where segments will be created
        streamer_name: Name of the streamer
        examples: List of example content for prompts
        auto_upload: Whether to automatically upload segments to YouTube
        config_path: Path to configuration file for post-processing
        gemini_backup_folder: Optional directory to backup Gemini responses
    """
    run_full_segment_creation(
        responses_folder=responses_folder, 
        clips_dir=clips_dir, 
        segments_dir=segments_dir, 
        streamer_name=streamer_name, 
        examples=examples,
        gemini_backup_folder=gemini_backup_folder,
        auto_upload=auto_upload,
        config_path=config_path,
        latest_clip_ignore_count=latest_clip_ignore_count,
    )


def watch_and_analyze_clips(output_dir: str, streamer_name: str, examples: list[str], responses_folder: str = "responses",
                           segments_dir: str = "segments", auto_upload: bool = True, config_path: str = "configs/xqc.json",segment_length: int = 10,processed_recordings_folder: str = "processed_recordings",twitch_id: str = "494543675", shutdown_event: threading.Event = None):
    """
    Watch the output directory for new MP4 files and automatically analyze them.
    Also run segment creation after each successful clip analysis.
    
    Args:
        output_dir: Directory to watch for new MP4 files
        streamer_name: Name of the streamer for analysis context
        examples: List of example clip descriptions for analysis
        responses_folder: Folder to save analysis results
        segments_dir: Directory to save created segments
        auto_upload: Whether to automatically upload segments to YouTube
        config_path: Path to configuration file for post-processing
    """
    print(f"🔍 Starting clip watcher for directory: {output_dir}")
    print(f"📝 Analysis results will be saved to: {responses_folder}")
    print(f"🎬 Segments will be saved to: {segments_dir}")
    print(f"🔄 Segment creation will run after each clip analysis")
    if auto_upload:
        print(f"📤 YouTube upload: ENABLED")
    else:
        print(f"📤 YouTube upload: DISABLED")
    
    # Track files we've already processed
    processed_files = set()
    # Track files we're monitoring for completion
    monitoring_files = {}
    
    # Ensure the processed recordings directory exists
    os.makedirs(processed_recordings_folder, exist_ok=True)
    
    while True:
        try:
            # If shutdown_event is set (e.g., stream ended), we don't pause monitoring immediately.
            # Instead, we allow the loop to continue to process any final clips.
            # The segment creation logic is aware of the shutdown and will process all clips.
            if shutdown_event and shutdown_event.is_set():
                # Slow down the watcher when the stream is offline.
                time.sleep(5)
                
            # Get all MP4 files in the output directory
            mp4_files = list(Path(output_dir).glob("*.mp4"))
            
            for mp4_file in mp4_files:
                file_path = str(mp4_file)
                
                # Skip if we've already processed this file successfully
                if file_path in processed_files:
                    continue
                
                # Skip temp test files
                if "temp_test_" in mp4_file.name:
                    continue
                
                # Check if file is complete (not being written to)
                try:
                    current_time = time.time()
                    
                    # If we're not monitoring this file yet, start monitoring it
                    if file_path not in monitoring_files:
                        monitoring_files[file_path] = {
                            'start_time': current_time,
                            'last_size': os.path.getsize(file_path),
                            'last_mtime': os.path.getmtime(file_path),
                            'stable_since': None
                        }
                        print(f"📁 Started monitoring file: {mp4_file.name}")
                        continue
                    
                    # Get current file stats
                    if not os.path.exists(file_path):
                        # File was deleted, remove from monitoring
                        if file_path in monitoring_files:
                            del monitoring_files[file_path]
                        continue
                    
                    current_size = os.path.getsize(file_path)
                    current_mtime = os.path.getmtime(file_path)
                    
                    # Check if file size or modification time changed
                    if (current_size != monitoring_files[file_path]['last_size'] or 
                        current_mtime != monitoring_files[file_path]['last_mtime']):
                        # File is still changing, update monitoring data
                        monitoring_files[file_path]['last_size'] = current_size
                        monitoring_files[file_path]['last_mtime'] = current_mtime
                        monitoring_files[file_path]['stable_since'] = None

                        # # Get video duration using helper function
                        # duration = get_mp4_duration(file_path)
                        # if duration > 0:
                        #     print(f"Video length is {duration:.1f} seconds")
                        # else:
                        #     print(f"Video length unknown (unable to determine duration)")
                        continue
                    
                    # File size and mtime are stable, mark when stability started
                    if monitoring_files[file_path]['stable_since'] is None:
                        monitoring_files[file_path]['stable_since'] = current_time
                        print(f"⏳ File {mp4_file.name} size stabilized")
                        continue
                    
                    # Check if file has been stable for long enough.
                    # During a live stream, we wait 5 minutes to avoid processing clips during an ad break.
                    # When the stream is offline, a much shorter delay is sufficient.
                    stability_duration = current_time - monitoring_files[file_path]['stable_since']
                    is_stream_offline = shutdown_event and shutdown_event.is_set()
                    required_stability = 5 if is_stream_offline else 300  # 5 seconds if offline, 5 minutes (300s) otherwise

                    if stability_duration < required_stability:
                        if not is_stream_offline:
                            print(f"⏳ File {mp4_file.name} size stabilized but not yet stable for 5 minutes")
                        continue  # Wait until the file is stable for the required duration

                    # If the stream is offline, this is the final clip. We must repair it in-place
                    # to fix any corruption from the stream ending abruptly.
                    if is_stream_offline:
                        print(f"🛠️ Stream is offline. Attempting to finalize and repair final clip: {mp4_file.name}")
                        temp_repaired_path = file_path + ".repaired.mp4"
                        try:
                            # Use ffmpeg to re-mux the file, which can fix a corrupt header/container
                            repair_cmd = [
                                "ffmpeg", "-loglevel", "warning", "-y", "-i", file_path,
                                "-c", "copy", "-movflags", "faststart",
                                temp_repaired_path
                            ]
                            # Use a timeout to prevent getting stuck
                            subprocess.run(repair_cmd, check=True, capture_output=True, text=True, timeout=60)
                            
                            # If successful, replace the original with the repaired file
                            shutil.move(temp_repaired_path, file_path)
                            print(f"✅ Successfully repaired clip: {mp4_file.name}")

                        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                            stderr = e.stderr if hasattr(e, 'stderr') else '(no stderr)'
                            print(f"❌ Failed to repair clip {mp4_file.name}. It may be too corrupt. Deleting it. Error: {stderr}")
                            try:
                                os.remove(file_path)
                                if os.path.exists(temp_repaired_path):
                                    os.remove(temp_repaired_path)
                            except OSError as remove_err:
                                print(f"⚠️  Error deleting corrupt file: {remove_err}")
                            continue # Skip to the next file in the main loop
                        except Exception as e:
                            print(f"❌ An unexpected error occurred during repair of {mp4_file.name}: {e}. Deleting file.")
                            try:
                                os.remove(file_path)
                                if os.path.exists(temp_repaired_path):
                                    os.remove(temp_repaired_path)
                            except OSError as remove_err:
                                print(f"⚠️  Error deleting corrupt file: {remove_err}")
                            continue # Skip to the next file
                    
                    # Final check - verify file still exists and size hasn't changed
                    if not os.path.exists(file_path):
                        if file_path in monitoring_files:
                            del monitoring_files[file_path]
                        continue
                    
                    final_size = os.path.getsize(file_path)
                    if final_size != current_size:
                        # Size changed during final wait, reset stability
                        monitoring_files[file_path]['last_size'] = final_size
                        monitoring_files[file_path]['stable_since'] = None
                        print(f"⏳ File {mp4_file.name} size changed during final wait, resetting stability")
                        continue
                    
                    # File is now truly complete, remove from monitoring
                    del monitoring_files[file_path]
                    
                    # File is complete, add to processed set and analyze
                    processed_files.add(file_path)
                    print(f"🎬 New clip detected: {mp4_file.name}")
                    
                    #save the chat
                    print(f"🔄 Saving chat for {mp4_file.name}")
                    try:
                        # Get actual clip duration to fix chat desync at the end of clips
                        actual_duration = get_mp4_duration(file_path) or segment_length
                        dump_chat(mp4_file.name, actual_duration)
                    except Exception as e:
                        dump_chat(mp4_file.name, segment_length)
                        print(f"⚠️  Error saving chat for {mp4_file.name}: {e}")

                    analysis_done=False
                    chat_done=False

                    def check_segments(local_file_path=file_path, local_mp4_name=mp4_file.name):
                        nonlocal processed_files  # allow pruning of the tracking set
                        # Run segment creation after successful analysis
                        print(f"🔄 Running segment creation after analyzing {local_mp4_name}...")

                        # Delete the original recording now that a processed version exists
                        try:
                            if os.path.exists(local_file_path):
                                os.remove(local_file_path)
                                print(f"🗑️  Deleted original recording: {local_mp4_name}")
                        except Exception as e:
                            print(f"⚠️  Could not delete original recording {local_mp4_name}: {e}")

                        # Determine whether to ignore latest clips (normal mode) or allow them (stream ended)
                        ignore_count = 0 if (shutdown_event and shutdown_event.is_set()) else 2

                        run_segment_creation(
                                responses_folder=responses_folder, 
                                clips_dir=processed_recordings_folder, 
                                segments_dir=segments_dir, 
                                streamer_name=streamer_name, 
                                examples=examples,
                                auto_upload=auto_upload,
                                config_path=config_path,
                                latest_clip_ignore_count=ignore_count,
                            )

                        # Remove the clip from the in-memory bookkeeping so the
                        # set does not grow without bound over long sessions.
                        processed_files.discard(local_file_path)

                    # Analyze the video in a separate thread to avoid blocking
                    def analyze_clip(local_file_path=file_path, local_mp4_name=mp4_file.name):
                        nonlocal analysis_done
                        print(f"🧵 analysis_thread STARTED for {local_mp4_name}")

                        cmd = [
                            sys.executable,
                            os.path.abspath("analyze.py"),
                            "--clip", local_file_path,
                            "--streamer", streamer_name,
                            "--responses", responses_folder,
                            "--config", config_path,
                        ]

                        try:
                            proc = subprocess.Popen(cmd)
                            # Poll for completion while checking shutdown signal
                            while proc.poll() is None:
                                if shutdown_event and shutdown_event.is_set():
                                    # print(f"🛑 Terminating analysis subprocess for {local_mp4_name}")
                                    # proc.terminate()
                                    # proc.wait(timeout=5)
                                    return
                                time.sleep(1)
                            
                            if proc.returncode != 0:
                                raise subprocess.CalledProcessError(proc.returncode, cmd)
                            
                            analysis_done = True
                            print(f"✅ analysis_thread FINISHED for {local_mp4_name}")
                            if chat_done:
                                check_segments()
                        except subprocess.CalledProcessError as e:
                            print(f"❌ analysis subprocess failed for {local_mp4_name}: {e}")
                            # Remove from processed set so it can be retried later
                            processed_files.discard(local_file_path)
                            return

                    # Add the chat to the video in a separate thread too
                    def add_the_chat(local_file_path=file_path, local_mp4_name=mp4_file.name):
                        nonlocal chat_done
                        print(f"🧵 chat_thread STARTED for {local_mp4_name}")

                        # Off-load heavy overlay work to a separate Python process so
                        # the large NumPy/Pillow allocations are reclaimed by the OS
                        # when the process exits (avoids RSS stair-casing).

                        output_with_chat = os.path.join(processed_recordings_folder, local_mp4_name)

                        try:
                            cmd = [
                                sys.executable,
                                os.path.abspath("chat_overlay.py"),
                                local_file_path,
                                "chat_replays",
                                output_with_chat,
                                twitch_id,
                            ]
                            
                            proc = subprocess.Popen(cmd)
                            # Poll for completion while checking shutdown signal
                            while proc.poll() is None:
                                if shutdown_event and shutdown_event.is_set():
                                    # print(f"🛑 Terminating chat overlay subprocess for {local_mp4_name}")
                                    # proc.terminate()
                                    # proc.wait(timeout=5)
                                    return
                                time.sleep(1)
                            
                            if proc.returncode != 0:
                                raise subprocess.CalledProcessError(proc.returncode, cmd)
                            
                        except subprocess.CalledProcessError as e:
                            print(f"❌ chat overlay subprocess failed for {local_mp4_name}: {e}")
                            # Remove from processed set so it can be retried later
                            processed_files.discard(local_file_path)
                            return

                        chat_done = True
                        print(f"✅ chat_thread FINISHED for {local_mp4_name}")
                        if analysis_done:
                            check_segments()

                    print(f"🧵 Creating analysis_thread for {mp4_file.name}")
                    analysis_thread = threading.Thread(target=analyze_clip, name=f"analysis_thread_{mp4_file.stem}")
                    analysis_thread.daemon = True
                    analysis_thread.start()

                    print(f"🧵 Creating chat_thread for {mp4_file.name}")
                    chat_thread = threading.Thread(target=add_the_chat, name=f"chat_thread_{mp4_file.stem}")
                    chat_thread.daemon = True
                    chat_thread.start()
                    
                except OSError as e:
                    print(f"Error checking file {mp4_file.name}: {e}")
                    continue
            
            # Sleep before next check
            time.sleep(30)
            
            # Periodically collect garbage to prevent memory bloat in the long-running watcher loop
            gc.collect()
            
        except KeyboardInterrupt:
            print("🛑 Clip watcher stopped by user")
            break
        except Exception as e:
            print(f"❌ Error in clip watcher: {e}")
            time.sleep(10)  # Wait longer on error


def run_recorder_with_analysis(channel: str, quality: str, segment_sec: int, output_dir: str, 
                              streamer_name: str, examples: list[str], responses_folder: str = "responses", 
                              segments_dir: str = "segments", retry_sec: int = 30, auto_upload: bool = True, 
                              config_path: str = "configs/xqc.json",segment_length: int = 10,processed_recordings_folder: str = "processed_recordings",twitch_id: str = "494543675"):
    """
    Run the recorder with automatic clip analysis and segment creation.
    
    Args:
        channel: Twitch channel name
        quality: Stream quality
        segment_sec: Segment length in seconds
        output_dir: Output directory for clips
        streamer_name: Name of the streamer for analysis
        examples: List of example clip descriptions
        responses_folder: Folder to save analysis results
        segments_dir: Directory to save created segments
        retry_sec: Retry interval in seconds
        auto_upload: Whether to automatically upload segments to YouTube
        config_path: Path to configuration file for post-processing
    """
    # Create recorder manager (it will handle timestamp watcher internally now)
    recorder_manager = RecorderManager(
        channel,
        quality,
        segment_sec,
        output_dir,
        responses_folder,
        segments_dir,
        "recordings_times",
        retry_sec,
        processed_recordings_folder,
    )
    
    # Start the clip watcher in a separate thread
    watcher_thread = threading.Thread(
        target=watch_and_analyze_clips,
        args=(output_dir, streamer_name, examples, responses_folder, segments_dir),
        kwargs={
            'auto_upload': auto_upload,
            'config_path': config_path,
            'segment_length': segment_length,
            'processed_recordings_folder': processed_recordings_folder,
            'twitch_id': twitch_id,
            'shutdown_event': recorder_manager.shutdown_event,
        },
        daemon=True
    )
    watcher_thread.start()
    
    # Start the recorder manager
    recorder_manager.start()
    
    # Chat watching will now start/stop automatically based on stream status

    # Health monitoring loop
    print("🔍 Starting health monitor...")
    last_status_time = time.time()
    stream_offline_since = None
    
    def _terminate(signum, frame):
        print("🛑 Shutdown signal received")
        stop_watching_chat()
        recorder_manager.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, _terminate)
    signal.signal(signal.SIGTERM, _terminate)
    
    try:
        while True:
            time.sleep(30)  # Check every 30 seconds
            
            # Get current status
            status = recorder_manager.get_status()
            current_time = time.time()
            
            if not status['is_recording']:
                if stream_offline_since is None:
                    print("🕒 Stream appears to be offline. Starting 20-minute shutdown timer.")
                    stream_offline_since = current_time
                else:
                    elapsed = current_time - stream_offline_since
                    if elapsed > 20 * 60:
                        print("🛑 Stream has been offline for over 20 minutes. Shutting down.")
                        recorder_manager.stop()
                        sys.exit(0)
            else:
                if stream_offline_since is not None:
                    print("✅ Stream is back online. Resetting shutdown timer.")
                    stream_offline_since = None

            # Print status update every 5 minutes
            if current_time - last_status_time > 300:
                print(f"📊 Health check - Recording: {status['is_recording']}, "
                      f"Thread: {status['thread_alive']}, "
                      f"Streamlink: {status['streamlink_alive']}, "
                      f"FFmpeg: {status['ffmpeg_alive']}")
                last_status_time = current_time
            
            # If recorder thread died unexpectedly, restart it
            if not status['thread_alive']:
                print("❌ Recorder thread died unexpectedly! Restarting...")
                recorder_manager.restart()
                time.sleep(5)  # Give it a moment to start
                continue
            
            # If processes died while thread is alive, it should auto-restart
            # But we can add additional logic here if needed
            
    except KeyboardInterrupt:
        print("🛑 Keyboard interrupt received")
        recorder_manager.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Record Twitch streams to segmented video files")
    parser.add_argument("config_file", help="Path to JSON configuration file")
    args = parser.parse_args()
    
    # Read configuration from JSON file
    try:
        with open(args.config_file, 'r') as f:
            config = json.load(f)
    except FileNotFoundError:
        print(f"Error: Configuration file '{args.config_file}' not found.")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in configuration file: {e}")
        sys.exit(1)
    
    # Extract configuration values
    channel = config.get("channel_name")
    quality = config.get("quality", "best")
    segment_seconds = config.get("segment_seconds", 300)
    output_dir = config.get("output_dir", "recordings")
    
    # Extract analysis-related values
    streamer_name = config.get("channel_name", channel)  # Use channel name as streamer name
    examples = config.get("example_segment_titles", [])
    responses_folder = config.get("responses_folder", "responses")
    
    # Extract segment creation values
    segments_dir = config.get("segments_dir", "segments")
    
    # Extract YouTube upload settings
    auto_upload = config.get("auto_upload_to_youtube", True)  # Default to True for backward compatibility
    config_path = args.config_file  # Use the same config file for post-processing
    
    processed_recordings_folder="processed_recordings"
    twitch_id=config.get("twitch_id", "494543675")

    # 🧹 Clear relevant working folders at script startup
    startup_folders = [output_dir, processed_recordings_folder, responses_folder, segments_dir, "chat_replays", "recordings_times"]
    print(f"🧹 Clearing startup folders: {', '.join(startup_folders)}")
    clear_folders(*startup_folders)

    if not channel:
        print("Error: 'channel_name' is required in configuration file.")
        sys.exit(1)
    
    print(f"Starting recorder with configuration from '{args.config_file}':")
    print(f"  Channel: {channel}")
    print(f"  Quality: {quality}")
    print(f"  Segment length: {segment_seconds} seconds")
    print(f"  Output directory: {output_dir}")
    print(f"  Auto-analysis: {'enabled' if examples else 'disabled'}")
    if examples:
        print(f"  Example clips: {len(examples)} provided")
        print(f"  Segments directory: {segments_dir}")
        print(f"  YouTube upload: {'enabled' if auto_upload else 'disabled'}")
    
    # Run with analysis if examples are provided
    if examples:
        run_recorder_with_analysis(channel, quality, segment_seconds, output_dir, 
                                 streamer_name, examples, responses_folder, segments_dir,
                                 auto_upload=auto_upload, config_path=config_path,segment_length=segment_seconds,processed_recordings_folder=processed_recordings_folder,twitch_id=twitch_id)
    else:
        run_recorder(channel, quality, segment_seconds, output_dir, responses_folder, segments_dir) 