import os
import json
import requests
from config import Config


class YouTubeUploader:
    """
    Railway-friendly YouTube uploader।
    InstalledAppFlow ব্যবহার না করে manual OAuth flow —
    কারণ Railway-তে ব্রাউজার ওপেন করা যায় না।
    """

    def __init__(self):
        self.client_id = Config.YOUTUBE_CLIENT_ID
        self.client_secret = Config.YOUTUBE_CLIENT_SECRET
        self.redirect_uri = Config.YOUTUBE_REDIRECT_URI
        self.token_file = os.path.join(Config.DATA_DIR, "youtube_token.json")
        self.token_data = self._load_token()

    def get_auth_url(self) -> str:
        """OAuth URL জেনারেট — user ব্রাউজারে যাবে, code পাবে।"""
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": " ".join(Config.YOUTUBE_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
        }
        base = "https://accounts.google.com/o/oauth2/v2/auth"
        query = "&".join(f"{k}={requests.utils.quote(str(v))}" for k, v in params.items())
        return f"{base}?{query}"

    def handle_callback(self, auth_code: str) -> dict:
        """OAuth callback handle — code to token exchange."""
        response = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": auth_code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": self.redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=30,
        )
        response.raise_for_status()
        self.token_data = response.json()
        self._save_token()
        return self.token_data

    def refresh_token(self):
        """Access token রিফ্রেশ করা।"""
        if not self.token_data or "refresh_token" not in self.token_data:
            raise ValueError("No refresh token available. Please reconnect.")

        response = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "refresh_token": self.token_data["refresh_token"],
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
        response.raise_for_status()
        new_data = response.json()
        self.token_data["access_token"] = new_data["access_token"]
        if "refresh_token" in new_data:
            self.token_data["refresh_token"] = new_data["refresh_token"]
        self._save_token()

    def is_connected(self) -> bool:
        return bool(self.token_data and self.token_data.get("access_token"))

    def disconnect(self):
        self.token_data = None
        if os.path.exists(self.token_file):
            os.remove(self.token_file)

    def get_channel_info(self) -> dict:
        """কানেক্টেড চ্যানেলের তথ্য।"""
        headers = self._auth_headers()
        response = requests.get(
            "https://www.googleapis.com/youtube/v3/channels",
            params={"part": "snippet,statistics", "mine": "true"},
            headers=headers,
            timeout=30,
        )

        if response.status_code == 401:
            self.refresh_token()
            headers = self._auth_headers()
            response = requests.get(
                "https://www.googleapis.com/youtube/v3/channels",
                params={"part": "snippet,statistics", "mine": "true"},
                headers=headers,
                timeout=30,
            )

        response.raise_for_status()
        items = response.json().get("items", [])
        if not items:
            return {}

        ch = items[0]
        return {
            "name": ch["snippet"]["title"],
            "thumbnail": ch["snippet"]["thumbnails"]["default"]["url"],
            "subscribers": ch["statistics"].get("subscriberCount", "0"),
            "id": ch["id"],
        }

    def upload_short(self, video_path: str, title: str, description: str,
                     tags: list = None, thumbnail_path: str = None,
                     privacy: str = "public") -> dict:
        """YouTube-এ ভিডিও আপলোড — resumable upload API ব্যবহার করে।"""

        if "#Shorts" not in title and "#shorts" not in title.lower():
            title = f"{title} #Shorts"

        headers = self._auth_headers()
        headers["Content-Type"] = "application/json"

        # Step 1: Start resumable upload
        metadata = {
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "tags": tags or ["shorts", "viral"],
                "categoryId": "22",
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False,
            },
        }

        init_response = requests.post(
            "https://www.googleapis.com/upload/youtube/v3/videos"
            "?uploadType=resumable&part=snippet,status",
            headers=headers,
            json=metadata,
            timeout=30,
        )

        if init_response.status_code == 401:
            self.refresh_token()
            headers = self._auth_headers()
            headers["Content-Type"] = "application/json"
            init_response = requests.post(
                "https://www.googleapis.com/upload/youtube/v3/videos"
                "?uploadType=resumable&part=snippet,status",
                headers=headers,
                json=metadata,
                timeout=30,
            )

        init_response.raise_for_status()
        upload_url = init_response.headers["Location"]

        # Step 2: Upload video file
        file_size = os.path.getsize(video_path)
        with open(video_path, "rb") as f:
            upload_response = requests.put(
                upload_url,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Length": str(file_size),
                },
                data=f,
                timeout=600,
            )

        upload_response.raise_for_status()
        video_data = upload_response.json()
        video_id = video_data["id"]

        # Step 3: Upload thumbnail (optional)
        if thumbnail_path and os.path.exists(thumbnail_path):
            try:
                with open(thumbnail_path, "rb") as f:
                    requests.post(
                        f"https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
                        f"?videoId={video_id}",
                        headers={
                            **self._auth_headers(),
                            "Content-Type": "image/jpeg",
                        },
                        data=f,
                        timeout=60,
                    )
            except Exception as e:
                print(f"Thumbnail upload error: {e}")

        return {
            "video_id": video_id,
            "url": f"https://youtube.com/shorts/{video_id}",
            "title": title,
        }

    def _auth_headers(self) -> dict:
        if not self.token_data:
            raise ValueError("Not connected to YouTube")
        return {"Authorization": f"Bearer {self.token_data['access_token']}"}

    def _save_token(self):
        with open(self.token_file, "w") as f:
            json.dump(self.token_data, f)

    def _load_token(self) -> dict:
        if os.path.exists(self.token_file):
            with open(self.token_file) as f:
                return json.load(f)
        return None