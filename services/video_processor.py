import os
import subprocess
import json
from PIL import Image, ImageDraw, ImageFont
from config import Config


class VideoProcessor:
    """
    সবকিছু FFmpeg দিয়ে — moviepy লাগবে না।
    অনেক কম RAM ব্যবহার করে কারণ FFmpeg subprocess হিসেবে চলে।
    """

    def __init__(self):
        self.output_dir = Config.OUTPUT_DIR
        self.temp_dir = Config.TEMP_DIR

    def crop_to_shorts(self, input_path: str, start: float, end: float,
                       output_name: str = None) -> str:
        """
        ভিডিও সেগমেন্ট কাটা + 9:16 aspect ratio-তে crop + 1080x1920 resize।
        সব FFmpeg দিয়ে — single command-এ।
        """
        if not output_name:
            output_name = f"short_{start:.0f}_{end:.0f}.mp4"
        output_path = os.path.join(self.output_dir, output_name)

        duration = end - start

        # Get input video dimensions
        probe = self._probe(input_path)
        in_w = probe.get("width", 1920)
        in_h = probe.get("height", 1080)

        # Calculate center crop for 9:16
        target_ratio = 9 / 16
        current_ratio = in_w / in_h

        if current_ratio > target_ratio:
            # Wider than 9:16 — crop width
            crop_w = int(in_h * target_ratio)
            crop_h = in_h
            crop_filter = f"crop={crop_w}:{crop_h}:(in_w-{crop_w})/2:0"
        else:
            # Taller than 9:16 — crop height
            crop_w = in_w
            crop_h = int(in_w / target_ratio)
            crop_filter = f"crop={crop_w}:{crop_h}:0:(in_h-{crop_h})/2"

        # FFmpeg: cut + crop + resize in one pass
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-i", input_path,
            "-t", str(duration),
            "-vf", f"{crop_filter},scale=1080:1920:flags=lanczos",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            "-ar", "44100",
            "-movflags", "+faststart",
            "-r", "30",
            output_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            print(f"FFmpeg error: {result.stderr[-500:]}")
            raise RuntimeError(f"FFmpeg failed: {result.stderr[-200:]}")

        return output_path

    def add_text_overlay(self, input_path: str, text: str,
                         position: str = "bottom", font_size: int = 42,
                         bg_opacity: float = 0.7,
                         text_color: str = "white",
                         bg_color: str = "black") -> str:
        """Add text with background box using FFmpeg drawtext."""
        output_path = input_path.replace(".mp4", "_text.mp4")

        # Escape special chars for FFmpeg
        safe_text = text.replace("'", "'\\''").replace(":", "\\:")
        safe_text = safe_text.replace("%", "%%")

        # Position mapping
        if position == "top":
            y_expr = "h*0.08"
        elif position == "center":
            y_expr = "(h-text_h)/2"
        else:  # bottom
            y_expr = "h*0.82"

        drawtext = (
            f"drawtext=text='{safe_text}'"
            f":fontsize={font_size}"
            f":fontcolor={text_color}"
            f":fontfile=/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
            f":x=(w-text_w)/2"
            f":y={y_expr}"
            f":box=1"
            f":boxcolor={bg_color}@{bg_opacity}"
            f":boxborderw=15"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-vf", drawtext,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "copy",
            output_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            raise RuntimeError(f"Text overlay failed: {result.stderr[-200:]}")

        return output_path

    def generate_thumbnail(self, video_path: str, time_offset: float = 1.5) -> str:
        """Extract a frame as thumbnail."""
        thumb_path = video_path.replace(".mp4", "_thumb.jpg")

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(time_offset),
            "-i", video_path,
            "-vframes", "1",
            "-q:v", "2",
            thumb_path
        ]
        subprocess.run(cmd, capture_output=True, timeout=30)
        return thumb_path if os.path.exists(thumb_path) else ""

    def get_duration(self, video_path: str) -> float:
        probe = self._probe(video_path)
        return probe.get("duration", 0)

    def _probe(self, video_path: str) -> dict:
        """Get video metadata using ffprobe."""
        cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_streams", "-show_format",
            video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return {"width": 1920, "height": 1080, "duration": 0}

        data = json.loads(result.stdout)
        video_stream = None
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video":
                video_stream = stream
                break

        return {
            "width": int(video_stream.get("width", 1920)) if video_stream else 1920,
            "height": int(video_stream.get("height", 1080)) if video_stream else 1080,
            "duration": float(data.get("format", {}).get("duration", 0)),
        }

    def cleanup_old_files(self):
        """Railway-তে ডিস্ক স্পেস বাঁচাতে পুরানো ফাইল মুছে ফেলা।"""
        import time
        cutoff = time.time() - (Config.TEMP_CLEANUP_HOURS * 3600)

        for directory in [self.temp_dir, self.output_dir]:
            for f in os.listdir(directory):
                filepath = os.path.join(directory, f)
                if os.path.isfile(filepath) and os.path.getmtime(filepath) < cutoff:
                    try:
                        os.remove(filepath)
                    except OSError:
                        pass