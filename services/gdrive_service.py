import os
import re
import zipfile
import random
import json
import requests
from config import Config


class GDriveService:
    def __init__(self):
        self.api_key = Config.GOOGLE_DRIVE_API_KEY
        self.temp_dir = Config.TEMP_DIR
        self.used_file = os.path.join(Config.DATA_DIR, "used_videos.json")

    def extract_folder_id(self, url: str) -> str:
        patterns = [
            r"folders/([a-zA-Z0-9_-]+)",
            r"id=([a-zA-Z0-9_-]+)",
        ]
        for p in patterns:
            match = re.search(p, url)
            if match:
                return match.group(1)
        return url

    def list_zip_files(self, folder_url: str) -> list:
        folder_id = self.extract_folder_id(folder_url)
        url = "https://www.googleapis.com/drive/v3/files"
        params = {
            "q": f"'{folder_id}' in parents and mimeType='application/zip'",
            "key": self.api_key,
            "fields": "files(id,name,size)",
            "pageSize": 100,
        }
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json().get("files", [])

    def get_random_video(self, folder_url: str) -> tuple:
        """
        জিপ থেকে একটা র‍্যান্ডম ভিডিও বের করা।
        আগে ব্যবহৃত ভিডিও আবার নেবে না।
        """
        used = self._load_used()
        zip_files = self.list_zip_files(folder_url)

        if not zip_files:
            raise ValueError("No zip files found in Drive folder")

        random.shuffle(zip_files)

        for zf in zip_files:
            zip_path = self._download_file(zf["id"], zf["name"])

            try:
                with zipfile.ZipFile(zip_path, "r") as z:
                    video_exts = {".mp4", ".mov", ".webm", ".mkv"}
                    videos = [
                        f for f in z.namelist()
                        if os.path.splitext(f)[1].lower() in video_exts
                        and f not in used
                    ]

                    if not videos:
                        continue

                    chosen = random.choice(videos)
                    z.extract(chosen, self.temp_dir)
                    video_path = os.path.join(self.temp_dir, chosen)

                    # Mark as used
                    used.append(chosen)
                    self._save_used(used)

                    return video_path, chosen
            finally:
                if os.path.exists(zip_path):
                    os.remove(zip_path)

        # All videos used — reset and try again
        self._save_used([])
        raise ValueError("All videos have been uploaded. List reset.")

    def _download_file(self, file_id: str, filename: str) -> str:
        url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media&key={self.api_key}"
        filepath = os.path.join(self.temp_dir, filename)
        response = requests.get(url, stream=True, timeout=300)
        response.raise_for_status()
        with open(filepath, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        return filepath

    def _load_used(self) -> list:
        if os.path.exists(self.used_file):
            with open(self.used_file) as f:
                return json.load(f)
        return []

    def _save_used(self, used: list):
        with open(self.used_file, "w") as f:
            json.dump(used, f)