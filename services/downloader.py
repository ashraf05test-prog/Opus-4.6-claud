import os
import subprocess
import yt_dlp
from config import Config


class VideoDownloader:
    """
    Railway-optimized downloader.
    - yt-dlp দিয়ে ডাউনলোড কিন্তু LOW resolution (360p/480p)
    - শুধু দরকারি সেগমেন্ট ডাউনলোড করে, পুরো ভিডিও না
    - ডিস্ক স্পেস বাঁচায়
    """

    def __init__(self):
        self.temp_dir = Config.TEMP_DIR

    def download_full(self, url: str, max_height: int = 720) -> dict:
        """
        Download video — Railway-friendly resolution.
        480p for analysis, 720p for final crop.
        """
        ydl_opts = {
            "format": f"bestvideo[height<={max_height}][ext=mp4]+bestaudio[ext=m4a]/best[height<={max_height}][ext=mp4]/best",
            "outtmpl": os.path.join(self.temp_dir, "%(id)s.%(ext)s"),
            "merge_output_format": "mp4",
            "noplaylist": True,
            "socket_timeout": 30,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            video_path = os.path.join(self.temp_dir, f"{info['id']}.mp4")

            # Extract heatmap data if available
            heatmap = info.get("heatmap") or []
            chapters = info.get("chapters") or []

            return {
                "video_path": video_path,
                "video_id": info["id"],
                "title": info.get("title", ""),
                "duration": info.get("duration", 0),
                "description": info.get("description", ""),
                "heatmap": heatmap,
                "chapters": chapters,
                "url": url,
            }

    def download_segment(self, url: str, start: float, end: float, output_name: str) -> str:
        """
        Download ONLY a specific segment — saves bandwidth & disk.
        Uses yt-dlp + ffmpeg.
        """
        output_path = os.path.join(self.temp_dir, output_name)

        ydl_opts = {
            "format": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]",
            "outtmpl": os.path.join(self.temp_dir, "segment_temp.%(ext)s"),
            "merge_output_format": "mp4",
            "noplaylist": True,
            "download_ranges": lambda info, ydl: [{"start_time": start, "end_time": end}],
            "force_keyframes_at_cuts": True,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            temp_file = os.path.join(self.temp_dir, "segment_temp.mp4")
            if os.path.exists(temp_file):
                os.rename(temp_file, output_path)
                return output_path
        except Exception:
            pass

        # Fallback: download full then cut with ffmpeg
        full = self.download_full(url, max_height=1080)
        self._ffmpeg_cut(full["video_path"], start, end, output_path)
        return output_path

    def _ffmpeg_cut(self, input_path: str, start: float, end: float, output_path: str):
        """Cut segment using ffmpeg — fast, no re-encoding."""
        duration = end - start
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-i", input_path,
            "-t", str(duration),
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            output_path
        ]
        subprocess.run(cmd, capture_output=True, timeout=120)

    def get_video_info(self, url: str) -> dict:
        """Get video info WITHOUT downloading."""
        ydl_opts = {"quiet": True, "no_download": True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                "video_id": info["id"],
                "title": info.get("title", ""),
                "duration": info.get("duration", 0),
                "description": info.get("description", ""),
                "heatmap": info.get("heatmap") or [],
                "chapters": info.get("chapters") or [],
            }

    def extract_audio(self, video_path: str) -> str:
        """Extract audio as mp3 for transcription API — much smaller file."""
        audio_path = video_path.rsplit(".", 1)[0] + ".mp3"
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vn",
            "-acodec", "libmp3lame",
            "-ab", "64k",  # Low bitrate = small file
            "-ar", "16000",  # 16kHz is enough for speech
            audio_path
        ]
        subprocess.run(cmd, capture_output=True, timeout=300)
        return audio_path

    def cleanup(self, *paths):
        """Remove temp files."""
        for p in paths:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass