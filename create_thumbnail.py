#!/usr/bin/env python3
"""
Create beautiful thumbnails from MP4 videos with random screenshots and overlay text.
Uses ffmpeg for video frame extraction and PIL for text rendering.
"""

import os
import sys
import subprocess
import tempfile
import random
import argparse
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps
import textwrap


def get_video_duration(video_path):
    """Get the duration of a video file in seconds using ffprobe."""
    try:
        cmd = [
            'ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
            '-of', 'csv=p=0', video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except subprocess.CalledProcessError as e:
        print(f"❌ Error getting video duration: {e}")
        return None


def extract_random_frame(video_path, output_path, exclude_start=10, exclude_end=10):
    """
    Extract a random frame from the video, avoiding the first and last few seconds.
    
    Args:
        video_path: Path to the input video
        output_path: Path for the extracted frame
        exclude_start: Seconds to exclude from the beginning
        exclude_end: Seconds to exclude from the end
    """
    duration = get_video_duration(video_path)
    if not duration:
        return False
    
    # Calculate safe time range
    safe_start = exclude_start
    safe_end = max(duration - exclude_end, safe_start + 1)
    
    if safe_end <= safe_start:
        print(f"⚠️ Video too short, using middle frame")
        timestamp = duration / 2
    else:
        timestamp = random.uniform(safe_start, safe_end)
    
    try:
        cmd = [
            'ffmpeg', '-ss', str(timestamp), '-i', video_path,
            '-vframes', '1', '-q:v', '2', '-y', output_path
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        print(f"🎬 Extracted frame at {timestamp:.1f}s")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Error extracting frame: {e}")
        return False


def get_optimal_font_size(draw, text, max_width, max_height, font_path, max_font_size=180):
    """Find the optimal font size that fits the text within the given dimensions, prioritizing larger fonts with max 3 lines."""
    # Start with maximum font size and work down
    font_size = max_font_size
    
    # Try different wrapping widths, starting with shorter lines to allow larger fonts
    wrap_widths = [8, 12, 15, 18, 20, 25, 30, 35, 40]  # More aggressive wrapping
    
    while font_size > 24:  # Minimum font size
        try:
            font = ImageFont.truetype(font_path, font_size)
        except OSError:
            # Fallback to default font
            font = ImageFont.load_default()
            break
            
        # Try different wrap widths for this font size
        for wrap_width in wrap_widths:
            lines = textwrap.wrap(text, width=wrap_width)
            
            # Skip if more than 3 lines
            if len(lines) > 3:
                continue
            
            # Calculate total text dimensions
            line_heights = []
            max_line_width = 0
            
            for line in lines:
                bbox = draw.textbbox((0, 0), line, font=font)
                line_width = bbox[2] - bbox[0]
                line_height = bbox[3] - bbox[1]
                
                max_line_width = max(max_line_width, line_width)
                line_heights.append(line_height)
            
            total_height = sum(line_heights) + (len(lines) - 1) * int(font_size * 0.3)  # Line spacing
            
            # If it fits, return this configuration
            if max_line_width <= max_width and total_height <= max_height:
                return font, lines
        
        font_size -= 8  # Reduce font size more aggressively
    
    # Fallback - use smaller font with more wrapping, still max 3 lines
    try:
        font = ImageFont.truetype(font_path, 32)
    except OSError:
        font = ImageFont.load_default()
    
    # Force into 3 lines or less
    for wrap_width in [8, 10, 12, 15, 18, 20]:
        lines = textwrap.wrap(text, width=wrap_width)
        if len(lines) <= 3:
            return font, lines
    
    # Ultimate fallback - truncate to 3 lines
    lines = textwrap.wrap(text, width=8)[:3]
    return font, lines


def draw_text_with_shadow_and_outline(draw, position, text, font, fill_color, shadow_color=(0, 0, 0, 180), shadow_blur=25, outline_color=(0, 0, 0), outline_width=4):
    """Draw text with both a large blurred shadow effect and a sharp black outline."""
    from PIL import ImageFilter
    
    x, y = position
    
    # Get the main image from the draw object
    main_img = draw._image
    
    # Create a temporary image for the shadow with extra padding for blur
    padding = shadow_blur * 2
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    
    # Create shadow layer with sufficient size
    shadow_width = text_width + padding * 2
    shadow_height = text_height + padding * 2
    shadow_img = Image.new('RGBA', (shadow_width, shadow_height), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_img)
    
    # Draw shadow text in the center of the shadow image
    shadow_draw.text((padding, padding), text, font=font, fill=shadow_color)
    
    # Apply blur to shadow
    shadow_img = shadow_img.filter(ImageFilter.GaussianBlur(radius=shadow_blur))
    
    # Calculate position to paste shadow (offset by padding)
    shadow_x = max(0, x - padding)
    shadow_y = max(0, y - padding)
    
    # Ensure shadow doesn't go outside image bounds
    crop_x = max(0, padding - x) if x < padding else 0
    crop_y = max(0, padding - y) if y < padding else 0
    
    # Crop shadow if necessary
    if crop_x > 0 or crop_y > 0:
        shadow_img = shadow_img.crop((crop_x, crop_y, shadow_width, shadow_height))
    
    # Paste shadow onto main image
    try:
        main_img.alpha_composite(shadow_img, (shadow_x, shadow_y))
    except Exception:
        # Fallback: convert shadow to RGB and paste with alpha
        shadow_rgb = Image.new('RGB', shadow_img.size, (0, 0, 0))
        shadow_alpha = shadow_img.split()[-1]  # Get alpha channel
        main_img.paste(shadow_rgb, (shadow_x, shadow_y), shadow_alpha)
    
    # Recreate draw object with updated image
    draw = ImageDraw.Draw(main_img)
    
    # Draw outline by drawing text in multiple positions
    for dx in range(-outline_width, outline_width + 1):
        for dy in range(-outline_width, outline_width + 1):
            if dx != 0 or dy != 0:
                draw.text((x + dx, y + dy), text, font=font, fill=outline_color)
    
    # Draw main text
    draw.text((x, y), text, font=font, fill=fill_color)
    
    return draw


def create_thumbnail(video_path, text, output_path, text_color=(255, 255, 255), 
                    outline_color=(0, 0, 0), thumbnail_size=(1920, 1080), overlay_image_path=None, no_text=False,pic_big_side=False):
    """
    Create a thumbnail from a video with overlay text and optional bottom image.
    
    Args:
        video_path: Path to the input video
        text: Text to overlay
        output_path: Path for the output thumbnail
        text_color: RGB tuple for text color
        outline_color: RGB tuple for outline color
        thumbnail_size: Tuple of (width, height) for output size
        overlay_image_path: Path to image to overlay at bottom center
        no_text: If True, skip text overlay
        pic_big_side: If True, place overlay image on side with angle instead of bottom
    """
    
    # Create temporary file for frame extraction
    with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as temp_frame:
        temp_frame_path = temp_frame.name
    
    try:
        # Extract random frame
        if not extract_random_frame(video_path, temp_frame_path):
            return False
        
        # Open and resize the frame
        with Image.open(temp_frame_path) as img:
            # Resize to thumbnail dimensions while maintaining aspect ratio
            img = ImageOps.fit(img, thumbnail_size, Image.Resampling.LANCZOS)
            
            # Convert to RGBA for proper overlay support
            img = img.convert('RGBA')
            
            # Create opaque black overlay
            overlay = Image.new('RGBA', thumbnail_size, (0, 0, 0, 128 if not pic_big_side else 50))  # Semi-transparent black
            img = Image.alpha_composite(img, overlay)
            
            # Load and overlay image if provided
            overlay_height = 0
            if overlay_image_path and os.path.exists(overlay_image_path):
                try:
                    with Image.open(overlay_image_path) as overlay_img:
                        # Convert to RGBA if needed
                        if overlay_img.mode != 'RGBA':
                            overlay_img = overlay_img.convert('RGBA')
                        
                        if pic_big_side:
                            print("📐 Positioning overlay image on side with angle...")
                            
                            # Make the image bigger for side placement - about 60% of thumbnail height with randomness
                            base_height_ratio = 0.9
                            height_variation = random.uniform(-0.15, 0.15)  # ±15% variation
                            target_height = int(thumbnail_size[1] * (base_height_ratio + height_variation))
                            print(f"📏 Using height ratio: {base_height_ratio + height_variation:.2f} (target height: {target_height}px)")
                            aspect_ratio = overlay_img.width / overlay_img.height
                            target_width = int(target_height * aspect_ratio)
                            
                            # Resize the overlay image
                            overlay_img = overlay_img.resize((target_width, target_height), Image.Resampling.LANCZOS)
                            
                            # Apply a slight rotation (5-10 degrees)
                            angle = 30 + random.randint(-5, 5)  # Negative for clockwise rotation with random variation
                            rotated_img = overlay_img.rotate(angle, expand=True, fillcolor=(0, 0, 0, 0))
                            
                            # Create shadow for the rotated image
                            print("🌑 Adding shadow to overlay image...")
                            shadow_offset = 8  # Shadow offset in pixels
                            
                            # Create shadow image (black version of the rotated image)
                            shadow_img = Image.new('RGBA', rotated_img.size, (0, 0, 0, 0))
                            shadow_draw = ImageDraw.Draw(shadow_img)
                            
                            # Create a black silhouette of the image for shadow
                            shadow_mask = rotated_img.split()[-1]  # Get alpha channel
                            shadow_color = Image.new('RGBA', rotated_img.size, (0, 0, 0, 180))  # Semi-transparent black
                            shadow_img = Image.composite(shadow_color, shadow_img, shadow_mask)
                            
                            # Position on the right side, peeking in from the edge
                            # Place it so part of it extends beyond the right edge
                            peek_amount = int(target_width * 0.05)  # 30% of image width peeks out
                            overlay_x = thumbnail_size[0] - target_width - peek_amount
                            overlay_y = thumbnail_size[1] - rotated_img.height + int(target_height*0.25)# Bottom aligned
                            
                            # Create a temporary canvas to handle the rotation and positioning
                            temp_canvas = Image.new('RGBA', thumbnail_size, (0, 0, 0, 0))
                            
                            # First paste the shadow (offset)
                            shadow_x = overlay_x + shadow_offset
                            shadow_y = overlay_y + shadow_offset
                            if shadow_x + shadow_img.width > 0:  # Only paste if visible
                                temp_canvas.paste(shadow_img, (shadow_x, shadow_y), shadow_img)
                            
                            # Then paste the actual rotated image on top
                            if overlay_x + rotated_img.width > 0:  # Only paste if visible
                                temp_canvas.paste(rotated_img, (overlay_x, overlay_y), rotated_img)
                            
                            # Composite the temp canvas onto the main image
                            img = Image.alpha_composite(img, temp_canvas)
                            
                            # No overlay height adjustment needed for side placement
                            overlay_height = 0
                        else:
                            # Original bottom placement logic
                            # Scale overlay image to be 1/3 of thumbnail height while maintaining aspect ratio
                            target_height = int(thumbnail_size[1] / 3)
                            aspect_ratio = overlay_img.width / overlay_img.height
                            target_width = int(target_height * aspect_ratio)
                            
                            force_scale_factor = 1.5

                            overlay_img = overlay_img.resize((int(target_width*force_scale_factor), int(target_height*force_scale_factor)), Image.Resampling.LANCZOS)
                            
                            # Position at bottom center, flush with bottom
                            overlay_x = (thumbnail_size[0] - int(target_width*force_scale_factor)) // 2
                            overlay_y = thumbnail_size[1] - int(target_height*force_scale_factor)  # Flush with bottom
                            
                            # Paste overlay image
                            img.paste(overlay_img, (overlay_x, overlay_y), overlay_img)
                            overlay_height = target_height  # Just the height, no margins
                        
                except Exception as e:
                    print(f"⚠️ Could not load overlay image: {e}")
            
            # Skip text overlay if no_text is True
            if not no_text:
                print("📝 Adding text overlay...")
                
                # Create drawing context
                draw = ImageDraw.Draw(img)
                
                # Calculate text area (full image minus overlay area for centering)
                available_height = thumbnail_size[1] - overlay_height
                text_area_height = available_height
                text_area_width = int(thumbnail_size[0] * 0.9)  # 90% width with margins
                
                # Try to load a bold font (common system fonts)
                font_paths = [
                    # Windows bold fonts
                    "C:/Windows/Fonts/arialbd.ttf",  # Arial Bold
                    "C:/Windows/Fonts/calibrib.ttf",  # Calibri Bold
                    "C:/Windows/Fonts/arial.ttf",    # Arial fallback
                    "C:/Windows/Fonts/calibri.ttf",  # Calibri fallback
                    # macOS bold fonts  
                    "/System/Library/Fonts/Arial Bold.ttf",
                    "/System/Library/Fonts/Helvetica Bold.ttc",
                    "/System/Library/Fonts/Arial.ttf",
                    "/System/Library/Fonts/Helvetica.ttc",
                    # Linux bold fonts
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                    "/usr/share/fonts/truetype/liberation/LiberationSans.ttf"
                ]
                
                font_path = None
                for path in font_paths:
                    if os.path.exists(path):
                        font_path = path
                        break
                
                if not font_path:
                    print("⚠️ No system font found, using default")
                    font = ImageFont.load_default()
                    lines = textwrap.wrap(text, width=25)
                else:
                    # Get optimal font size and wrapped text
                    font, lines = get_optimal_font_size(
                        draw, text, text_area_width, text_area_height, font_path
                    )
                
                # Calculate total text height for centering
                line_heights = []
                max_line_width = 0
                
                for line in lines:
                    bbox = draw.textbbox((0, 0), line, font=font)
                    line_width = bbox[2] - bbox[0]
                    line_height = bbox[3] - bbox[1]
                    
                    max_line_width = max(max_line_width, line_width)
                    line_heights.append(line_height)
                
                total_text_height = sum(line_heights) + (len(lines) - 1) * int(font.size * 0.2)
                
                # Calculate starting position (centered both X and Y in available space)
                start_x = (thumbnail_size[0] - max_line_width) // 2
                start_y = (available_height - total_text_height) // 2  # Center vertically in available space
                
                # Draw each line
                current_y = start_y
                for i, line in enumerate(lines):
                    # Center each line horizontally
                    bbox = draw.textbbox((0, 0), line, font=font)
                    line_width = bbox[2] - bbox[0]
                    line_x = (thumbnail_size[0] - line_width) // 2
                    
                    # Draw text with shadow and outline
                    draw = draw_text_with_shadow_and_outline(
                        draw, (line_x, current_y), line, font, 
                        text_color, shadow_color=(0, 0, 0, 180), shadow_blur=25,
                        outline_color=(0, 0, 0), outline_width=4
                    )
                    
                    current_y += line_heights[i] + int(font.size * 0.2)
            else:
                print("🚫 Skipping text overlay (no_text=True)")
            
            # Convert back to RGB for JPEG saving
            if img.mode == 'RGBA':
                # Create white background for final image
                final_img = Image.new('RGB', img.size, (255, 255, 255))
                final_img.paste(img, mask=img.split()[-1])  # Use alpha as mask
                img = final_img
            
            # Save the thumbnail
            img.save(output_path, 'JPEG', quality=95)
            print(f"✅ Thumbnail saved: {output_path}")
            return True
            
    finally:
        # Clean up temporary file
        if os.path.exists(temp_frame_path):
            os.unlink(temp_frame_path)
    
    return False
def get_video_title_from_filename(video_path):
    """Extract a clean title from the video filename."""
    filename = Path(video_path).stem
    # Replace underscores with spaces and capitalize
    title = filename.replace('_', ' ').replace('-', ' ')
    # Remove common video format indicators
    title = title.replace('.mp4', '').replace('.mov', '').replace('.avi', '')
    return title


def load_config(config_path):
    """Load configuration from JSON file."""
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Error loading config file: {e}")
        return None


def main():

    # Use the specified video file
    # Default settings - no CLI args needed
    video_path = r"finishedvideos\xQc Reacts to Asmongold on Twitch Drama.mp4"
    config_path = r"configs\xqc.json"  # Can be set to a config file path if needed
    text = None  # Will use filename if None
    output_path = None  # Will auto-generate if None
    color = 'white'
    size = '1920x1080'
    
    print(f"🚀 Starting thumbnail creation...")
    print(f"📁 Looking for video: {video_path}")
    
    # Validate video file
    if not os.path.exists(video_path):
        print(f"❌ Video file not found: {video_path}")
        return 1
    
    # Load config if provided
    config = None
    overlay_image_path = None
    if config_path:
        config = load_config(config_path)
        if config and 'thumbnail_image' in config:
            # Check if path is relative and make it relative to config directory
            thumbnail_image = config['thumbnail_image']
            if not os.path.isabs(thumbnail_image):
                config_dir = os.path.dirname(config_path)
                overlay_image_path = os.path.join(config_dir, thumbnail_image)
            else:
                overlay_image_path = thumbnail_image
            
            if not os.path.exists(overlay_image_path):
                print(f"⚠️ Overlay image not found: {overlay_image_path}")
                overlay_image_path = None
    
    # Determine text and convert to uppercase
    text = text if text else get_video_title_from_filename(video_path)
    text = text.upper()  # Convert to all caps
    
    # Determine output path
    if output_path:
        output_path = output_path
    else:
        video_dir = os.path.dirname(video_path)
        video_name = Path(video_path).stem
        output_path = os.path.join(video_dir, f"{video_name}_thumbnail.jpg")
    
    # Parse color
    color_map = {
        'white': (255, 255, 255),
        'red': (255, 100, 100),
        'blue': (100, 150, 255),
        'yellow': (255, 255, 100),
        'green': (100, 255, 100),
        'purple': (200, 100, 255),
        'orange': (255, 165, 100)
    }
    text_color = color_map.get(color.lower(), (255, 255, 255))
    
    # Parse size
    try:
        width, height = map(int, size.split('x'))
        thumbnail_size = (width, height)
    except ValueError:
        print(f"❌ Invalid size format: {size}. Use WIDTHxHEIGHT (e.g., 1920x1080)")
        return 1
    
    print(f"🎥 Processing: {video_path}")
    print(f"📝 Text: {text}")
    print(f"🎨 Color: {color}")
    print(f"📐 Size: {thumbnail_size[0]}x{thumbnail_size[1]}")
    if overlay_image_path:
        print(f"🖼️ Overlay: {overlay_image_path}")
    
    # Create thumbnail
    success = create_thumbnail(
        video_path, text, output_path, 
        text_color=text_color, 
        thumbnail_size=thumbnail_size,
        overlay_image_path=overlay_image_path,
        no_text=True,
        pic_big_side=True
    )
    
    if success:
        print(f"🎉 Success! Thumbnail created: {output_path}")
        return 0
    else:
        print("❌ Failed to create thumbnail")
        return 1


if __name__ == "__main__":
    sys.exit(main())