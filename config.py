import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "change-this-secret-key-123")
    
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    TEMP_DIR = os.path.join(BASE_DIR, "temp")
    OUTPUT_DIR = os.path.join(BASE_DIR, "output")
    DATA_DIR = os.path.join(BASE_DIR, "data")
    
    # API Keys
    GROK_API_KEY = os.getenv("GROK_API_KEY", "")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")  # Groq Whisper API
    GOOGLE_DRIVE_API_KEY = os.getenv("GOOGLE_DRIVE_API_KEY", "")
    
    # YouTube OAuth
    YOUTUBE_CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID", "")
    YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET", "")
    YOUTUBE_REDIRECT_URI = os.getenv("YOUTUBE_REDIRECT_URI", "")
    
    YOUTUBE_SCOPES = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube"
    ]
    
    # Shorts settings
    MAX_SHORT_DURATION = 59
    SHORTS_WIDTH = 1080
    SHORTS_HEIGHT = 1920
    
    # Cleanup: delete temp files older than this (hours)
    TEMP_CLEANUP_HOURS = 2
    
    for d in [TEMP_DIR, OUTPUT_DIR, DATA_DIR]:
        os.makedirs(d, exist_ok=True)