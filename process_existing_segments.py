#!/usr/bin/env python3
"""
Process existing video segments with thumbnail creation and YouTube upload.
This script is useful for segments that were created before the automatic integration.
"""

import argparse
import os
import sys
from pathlib import Path
from segment_post_processor import SegmentPostProcessor


def main():
    parser = argparse.ArgumentParser(
        description='Process existing video segments with thumbnail creation and YouTube upload'
    )
    parser.add_argument(
        'segments_dir',
        nargs='?',
        default='xqc_segments',
        help='Directory containing video segments (default: xqc_segments)'
    )
    parser.add_argument(
        '-c', '--config',
        default='configs/xqc.json',
        help='Path to configuration file (default: configs/xqc.json)'
    )
    parser.add_argument(
        '--no-thumbnail',
        action='store_true',
        help='Skip thumbnail creation'
    )
    parser.add_argument(
        '--no-upload',
        action='store_true',
        help='Skip YouTube upload'
    )
    parser.add_argument(
        '--single',
        help='Process only a single video file'
    )
    
    args = parser.parse_args()
    
    # Initialize processor
    processor = SegmentPostProcessor(args.config)
    
    if args.single:
        # Process a single file
        if not os.path.exists(args.single):
            print(f"❌ File not found: {args.single}")
            sys.exit(1)
        
        title = Path(args.single).stem.replace('_', ' ')
        print(f"Processing single file: {args.single}")
        
        result = processor.process_segment(
            video_path=args.single,
            title=title,
            create_thumbnail=not args.no_thumbnail,
            upload_to_youtube=not args.no_upload
        )
        
        print(f"\n{'✅' if result['success'] else '❌'} Processing complete")
        if result['thumbnail_path']:
            print(f"   Thumbnail: {result['thumbnail_path']}")
        if result['youtube_video_id']:
            print(f"   YouTube: https://www.youtube.com/watch?v={result['youtube_video_id']}")
    
    else:
        # Process all segments in directory
        if not os.path.exists(args.segments_dir):
            print(f"❌ Directory not found: {args.segments_dir}")
            sys.exit(1)
        
        print(f"Processing segments in: {args.segments_dir}")
        print(f"Config: {args.config}")
        print(f"Create thumbnails: {not args.no_thumbnail}")
        print(f"Upload to YouTube: {not args.no_upload}")
        print("-" * 50)
        
        results = processor.batch_process_segments(
            segments_dir=args.segments_dir,
            create_thumbnails=not args.no_thumbnail,
            upload_to_youtube=not args.no_upload
        )
        
        # Print summary
        if results:
            print("\n" + "=" * 50)
            print("SUMMARY")
            print("=" * 50)
            
            successful = sum(1 for r in results if r['success'])
            print(f"Total segments: {len(results)}")
            print(f"Successful: {successful}")
            print(f"Failed: {len(results) - successful}")
            
            # List any failures
            failures = [r for r in results if not r['success']]
            if failures:
                print("\nFailed segments:")
                for r in failures:
                    print(f"  - {r['title']}")


if __name__ == "__main__":
    main() 