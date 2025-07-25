#!/usr/bin/env python3
"""
Simple control script for the RecorderManager.
Allows manual start/stop/restart of the recording process.
"""

import json
import sys
import time
import threading
from index import RecorderManager

def print_status(manager):
    """Print current status of the recorder."""
    status = manager.get_status()
    print(f"\n📊 Current Status:")
    print(f"   Recording: {'🔴 YES' if status['is_recording'] else '⚪ NO'}")
    print(f"   Thread alive: {'✅ YES' if status['thread_alive'] else '❌ NO'}")
    print(f"   Streamlink alive: {'✅ YES' if status['streamlink_alive'] else '❌ NO'}")
    print(f"   FFmpeg alive: {'✅ YES' if status['ffmpeg_alive'] else '❌ NO'}")

def monitor_loop(manager):
    """Background monitoring loop."""
    while True:
        time.sleep(60)  # Check every minute
        status = manager.get_status()
        if not status['thread_alive']:
            print("\n⚠️  WARNING: Recorder thread died!")
            break

def main():
    if len(sys.argv) != 2:
        print("Usage: python recorder_control.py <config_file>")
        sys.exit(1)
    
    config_file = sys.argv[1]
    
    # Read configuration
    try:
        with open(config_file, 'r') as f:
            config = json.load(f)
    except Exception as e:
        print(f"❌ Error reading config: {e}")
        sys.exit(1)
    
    # Create recorder manager
    manager = RecorderManager(
        channel=config.get("channel_name"),
        quality=config.get("quality", "best"),
        segment_sec=config.get("segment_seconds", 300),
        output_dir=config.get("output_dir", "recordings"),
        responses_folder=config.get("responses_folder", "responses"),
        segments_dir=config.get("segments_dir", "segments")
    )
    
    # Start monitoring thread
    monitor_thread = threading.Thread(target=monitor_loop, args=(manager,), daemon=True)
    monitor_thread.start()
    
    print(f"🎥 Recorder Control for channel: {config.get('channel_name')}")
    print("\nCommands:")
    print("  start   - Start recording")
    print("  stop    - Stop recording") 
    print("  restart - Restart recording")
    print("  status  - Show current status")
    print("  quit    - Exit program")
    
    try:
        while True:
            cmd = input("\n> ").strip().lower()
            
            if cmd == "start":
                print("▶️  Starting recorder...")
                manager.start()
                
            elif cmd == "stop":
                print("🛑 Stopping recorder...")
                manager.stop()
                
            elif cmd == "restart":
                print("🔄 Restarting recorder...")
                manager.restart()
                
            elif cmd == "status":
                print_status(manager)
                
            elif cmd in ["quit", "exit", "q"]:
                print("👋 Goodbye!")
                manager.stop()
                break
                
            else:
                print("❓ Unknown command. Type 'quit' to exit.")
                
    except KeyboardInterrupt:
        print("\n🛑 Stopping...")
        manager.stop()

if __name__ == "__main__":
    main() 