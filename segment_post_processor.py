#!/usr/bin/env python3
"""
Segment Post-Processor: Handles thumbnail creation and YouTube uploading for video segments.
"""

import os
import glob
import json
from pathlib import Path
from typing import Dict, Optional, Tuple
import time

# Import thumbnail and upload functions directly
from create_thumbnail import create_thumbnail, load_config as load_thumbnail_config
from upload_video import get_authenticated_service, initialize_upload, poll_and_publish, set_thumbnail


class UploadOptions:
    """Simple options class for YouTube upload parameters."""
    def __init__(self, file_path: str, title: str, description: str, category: str = "20", keywords: str = ""):
        self.file = file_path
        self.title = title
        self.description = description
        self.category = category
        self.keywords = keywords


class SegmentPostProcessor:
    """Handles post-processing of video segments including thumbnail creation and YouTube upload."""
    
    def __init__(self, config_path: str = "configs/xqc.json"):
        """
        Initialize the post-processor with configuration.
        
        Args:
            config_path: Path to the configuration file
        """
        self.config_path = config_path
        self.config = self._load_config()
        
    def _load_config(self) -> Dict:
        """Load configuration from JSON file."""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"❌ Error loading config file: {e}")
            return {}
    
    def create_thumbnail(self, video_path: str, title: str) -> Optional[str]:
        """
        Create a thumbnail for the video segment.
        
        Args:
            video_path: Path to the video file
            title: Title of the video (used as overlay text)
            
        Returns:
            Path to the created thumbnail or None if failed
        """
        # Create thumbnail filename based on video filename
        video_dir = os.path.dirname(video_path)
        video_name = Path(video_path).stem
        thumbnail_path = os.path.join(video_dir, f"{video_name}_thumbnail.jpg")
        
        try:
            print(f"🖼️  Creating thumbnail for: {title}")
            
            # Get overlay image path from config
            overlay_image_path = None
            if 'thumbnail_image' in self.config:
                thumbnail_image = self.config['thumbnail_image']
                if not os.path.isabs(thumbnail_image):
                    config_dir = os.path.dirname(self.config_path)
                    overlay_image_path = os.path.join(config_dir, thumbnail_image)
                else:
                    overlay_image_path = thumbnail_image
                
                if not os.path.exists(overlay_image_path):
                    print(f"⚠️ Overlay image not found: {overlay_image_path}")
                    overlay_image_path = None
            
            # Create thumbnail directly
            success = create_thumbnail(
                video_path=video_path,
                text=title.upper(),  # Convert to uppercase
                output_path=thumbnail_path,
                text_color=(255, 255, 255),  # White text
                thumbnail_size=(1920, 1080),
                overlay_image_path=overlay_image_path,
                no_text=True,
                pic_big_side=True
            )
            
            if success:
                print(f"✅ Thumbnail created: {thumbnail_path}")
                return thumbnail_path
            else:
                print(f"❌ Failed to create thumbnail")
                return None
                
        except Exception as e:
            print(f"❌ Failed to create thumbnail: {e}")
            return None
    
    def upload_to_youtube(self, video_path: str, title: str, description: str = None, thumbnail_path: str = None) -> Tuple[bool, Optional[str]]:
        """
        Upload a video to YouTube.
        
        Args:
            video_path: Path to the video file
            title: Title for the YouTube video
            description: Description for the YouTube video
            thumbnail_path: Path to the thumbnail image file (optional)
            
        Returns:
            Tuple of (success: bool, video_id: Optional[str])
        """
        # Build description if not provided
        if description is None:
            # Use video_description from config if available, otherwise fallback to default
            if 'video_description' in self.config:
                description = self.config['video_description'].replace('\\n', '\n')
            else:
                channel_name = self.config.get('channel_name', 'xQc')
                description = f"Watch {channel_name} - {title}\n\nAutomatically clipped and uploaded."
        
        try:
            print(f"📤 Uploading to YouTube: {title}")
            
            # Create upload options
            options = UploadOptions(
                file_path=video_path,
                title=title,
                description=description,
                category="20",  # Gaming category
                keywords="xqc,twitch,clips,highlights,gaming"
            )
            
            # Get authenticated service
            youtube = get_authenticated_service()
            
            # Upload the video
            video_id = initialize_upload(youtube, options)
            
            if video_id:
                print(f"✅ Video uploaded, processing... ID: {video_id}")
                
                # Set thumbnail if provided
                if thumbnail_path and os.path.exists(thumbnail_path):
                    set_thumbnail(youtube, video_id, thumbnail_path)
                
                # Wait for processing and publish if no claims
                poll_and_publish(youtube, video_id)
                
                print(f"✅ Upload complete: https://www.youtube.com/watch?v={video_id}")
                return True, video_id
            else:
                print(f"❌ Upload failed - no video ID returned")
                return False, None
                
        except Exception as e:
            print(f"❌ Failed to upload video: {e}")
            return False, None
    
    def process_segment(self, video_path: str, title: str, create_thumbnail: bool = True, 
                       upload_to_youtube: bool = True, description: str = None) -> Dict[str, any]:
        """
        Process a video segment with thumbnail creation and YouTube upload.
        
        Args:
            video_path: Path to the video segment
            title: Title of the segment
            create_thumbnail: Whether to create a thumbnail
            upload_to_youtube: Whether to upload to YouTube
            description: Description for the YouTube video (optional)
            
        Returns:
            Dictionary with processing results
        """
        results = {
            'video_path': video_path,
            'title': title,
            'thumbnail_path': None,
            'youtube_video_id': None,
            'success': False
        }
        
        # Create thumbnail
        thumbnail_path = None
        if create_thumbnail:
            thumbnail_path = self.create_thumbnail(video_path, title)
            results['thumbnail_path'] = thumbnail_path
            
        # Upload to YouTube
        if upload_to_youtube and os.path.exists(video_path):
            success, video_id = self.upload_to_youtube(video_path, title, description=description, thumbnail_path=thumbnail_path)
            results['youtube_video_id'] = video_id
            results['success'] = success
        
        return results
    
    def batch_process_segments(self, segments_dir: str, create_thumbnails: bool = True,
                             upload_to_youtube: bool = True, description: str = None) -> list:
        """
        Process all video segments in a directory.
        
        Args:
            segments_dir: Directory containing video segments
            create_thumbnails: Whether to create thumbnails
            upload_to_youtube: Whether to upload to YouTube
            description: Description for the YouTube videos (optional)
            
        Returns:
            List of processing results for each segment
        """
        results = []
        
        # Find all MP4 files in the segments directory
        video_files = glob.glob(os.path.join(segments_dir, "*.mp4"))
        video_files.sort()
        
        if not video_files:
            print(f"⚠️ No video files found in: {segments_dir}")
            return results
        
        print(f"📹 Found {len(video_files)} segments to process")
        
        for i, video_path in enumerate(video_files, 1):
            # Extract title from filename (remove .mp4 and replace underscores)
            video_name = Path(video_path).stem
            title = video_name.replace('_', ' ')
            
            print(f"\n[{i}/{len(video_files)}] Processing: {title}")
            
            # Process the segment
            result = self.process_segment(
                video_path=video_path,
                title=title,
                create_thumbnail=create_thumbnails,
                upload_to_youtube=upload_to_youtube,
                description=description
            )
            
            results.append(result)
            
            # Small delay between uploads to avoid rate limiting
            if upload_to_youtube and i < len(video_files):
                time.sleep(2)
        
        # Summary
        successful_uploads = sum(1 for r in results if r['success'])
        print(f"\n📊 Processing complete: {successful_uploads}/{len(video_files)} successfully uploaded")
        
        return results


# Helper functions for integration with existing code
def process_single_segment(video_path: str, title: str, config_path: str = "configs/xqc.json", description: str = None) -> Dict[str, any]:
    """
    Convenience function to process a single segment.
    
    Args:
        video_path: Path to the video segment
        title: Title of the segment
        config_path: Path to configuration file
        description: Description for the YouTube video (optional)
        
    Returns:
        Processing results dictionary
    """
    processor = SegmentPostProcessor(config_path)
    return processor.process_segment(video_path, title, description=description)


def create_segment_thumbnail(video_path: str, title: str, config_path: str = "configs/xqc.json") -> Optional[str]:
    """
    Convenience function to create a thumbnail for a segment.
    
    Args:
        video_path: Path to the video segment
        title: Title of the segment
        config_path: Path to configuration file
        
    Returns:
        Path to created thumbnail or None if failed
    """
    processor = SegmentPostProcessor(config_path)
    return processor.create_thumbnail(video_path, title)


def upload_segment_to_youtube(video_path: str, title: str, config_path: str = "configs/xqc.json", thumbnail_path: str = None, description: str = None) -> Tuple[bool, Optional[str]]:
    """
    Convenience function to upload a segment to YouTube.
    
    Args:
        video_path: Path to the video segment
        title: Title of the segment
        config_path: Path to configuration file
        thumbnail_path: Path to thumbnail image (optional)
        description: Description for the YouTube video (optional)
        
    Returns:
        Tuple of (success: bool, video_id: Optional[str])
    """
    processor = SegmentPostProcessor(config_path)
    return processor.upload_to_youtube(video_path, title, description=description, thumbnail_path=thumbnail_path)


if __name__ == "__main__":
    # Example usage
    import sys
    
    if len(sys.argv) > 1:
        # Process a specific video file
        video_file = sys.argv[1]
        title = sys.argv[2] if len(sys.argv) > 2 else Path(video_file).stem.replace('_', ' ')
        
        result = process_single_segment(video_file, title)
        print(f"\nResult: {json.dumps(result, indent=2)}")
    else:
        # Process all segments in the default directory
        processor = SegmentPostProcessor()
        results = processor.batch_process_segments("xqc_segments") 