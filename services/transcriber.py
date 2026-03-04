import os
import requests
import math
from config import Config


class Transcriber:
    """
    Groq Whisper API ব্যবহার করে transcription.
    LOCAL Whisper মডেল লোড করার দরকার নেই — RAM বাঁচায়।
    Groq-এর Whisper API ফ্রি টিয়ারে প্রতি মাসে অনেক মিনিট দেয়।
    Max file size: 25MB — তাই বড় ফাইল চাংকে ভাগ করে পাঠাই।
    """

    def __init__(self):
        self.api_key = Config.GROQ_API_KEY
        self.api_url = "https://api.groq.com/openai/v1/audio/transcriptions"
        self.max_file_size = 24 * 1024 * 1024  # 24MB safe limit

    def transcribe(self, audio_path: str) -> dict:
        """Transcribe audio using Groq Whisper API."""
        file_size = os.path.getsize(audio_path)

        if file_size <= self.max_file_size:
            return self._transcribe_single(audio_path)
        else:
            return self._transcribe_chunks(audio_path)

    def _transcribe_single(self, audio_path: str) -> dict:
        """Transcribe a single audio file."""
        with open(audio_path, "rb") as f:
            response = requests.post(
                self.api_url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"file": (os.path.basename(audio_path), f, "audio/mpeg")},
                data={
                    "model": "whisper-large-v3-turbo",
                    "response_format": "verbose_json",
                    "timestamp_granularities[]": "segment",
                    "language": "en",
                },
                timeout=300,
            )

        response.raise_for_status()
        data = response.json()

        segments = []
        for seg in data.get("segments", []):
            segments.append({
                "start": seg["start"],
                "end": seg["end"],
                "text": seg["text"].strip(),
            })

        return {
            "text": data.get("text", ""),
            "segments": segments,
            "language": data.get("language", "en"),
        }

    def _transcribe_chunks(self, audio_path: str) -> dict:
        """
        বড় অডিও ফাইল ১০ মিনিটের চাংকে ভাগ করে transcribe।
        """
        import subprocess

        # Get duration
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", audio_path],
            capture_output=True, text=True, timeout=30
        )
        total_duration = float(result.stdout.strip())

        chunk_duration = 600  # 10 minutes per chunk
        num_chunks = math.ceil(total_duration / chunk_duration)

        all_segments = []
        full_text = []
        temp_dir = Config.TEMP_DIR

        for i in range(num_chunks):
            chunk_start = i * chunk_duration
            chunk_path = os.path.join(temp_dir, f"chunk_{i}.mp3")

            # Extract chunk
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(chunk_start),
                "-i", audio_path,
                "-t", str(chunk_duration),
                "-acodec", "libmp3lame",
                "-ab", "64k",
                "-ar", "16000",
                chunk_path
            ]
            subprocess.run(cmd, capture_output=True, timeout=120)

            if not os.path.exists(chunk_path):
                continue

            try:
                chunk_result = self._transcribe_single(chunk_path)

                # Adjust timestamps with offset
                for seg in chunk_result["segments"]:
                    seg["start"] += chunk_start
                    seg["end"] += chunk_start
                    all_segments.append(seg)

                full_text.append(chunk_result["text"])
            except Exception as e:
                print(f"Chunk {i} transcription error: {e}")
            finally:
                if os.path.exists(chunk_path):
                    os.remove(chunk_path)

        return {
            "text": " ".join(full_text),
            "segments": all_segments,
            "language": "en",
        }