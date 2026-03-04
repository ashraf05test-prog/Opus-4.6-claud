import os
import json
import time
import threading
import uuid
import queue
from datetime import datetime
from flask import (
    Flask, render_template, request, jsonify,
    send_file, redirect, url_for, Response, session
)
from werkzeug.utils import secure_filename
from config import Config
from services.downloader import VideoDownloader
from services.transcriber import Transcriber
from services.ai_analyzer import AIAnalyzer
from services.video_processor import VideoProcessor
from services.youtube_uploader import YouTubeUploader
from services.gdrive_service import GDriveService
from services.scheduler_service import SchedulerService

app = Flask(__name__)
app.config.from_object(Config)

# ══════════ SERVICES INIT ══════════
dl = VideoDownloader()
transcriber = Transcriber()
ai = AIAnalyzer()
vp = VideoProcessor()
yt = YouTubeUploader()
gdrive = GDriveService()
sched = SchedulerService()
sched.start()

# ══════════ STATE ══════════
tasks = {}           # task_id -> {status, progress, ...}
clips = {}           # clip_id -> {path, title, ...}
log_queues = {}      # task_id -> queue.Queue (for real-time SSE logs)
global_logs = []     # Global activity log


def add_log(task_id: str, message: str, level: str = "info"):
    """Add real-time log entry — pushes to SSE queue + stores globally."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    entry = {
        "time": timestamp,
        "message": message,
        "level": level,  # info, success, error, warning
        "task_id": task_id,
    }

    # Push to SSE queue for real-time
    if task_id in log_queues:
        log_queues[task_id].put(entry)

    # Store globally
    global_logs.append({**entry, "full_time": datetime.now().isoformat()})
    if len(global_logs) > 500:
        global_logs.pop(0)

    print(f"[{level.upper()}] [{timestamp}] {message}")


# ══════════════════════════════════════════
#                  PAGES
# ══════════════════════════════════════════

@app.route("/")
def index():
    """মূল পেইজ — Auto Crop"""
    cookies_exist = os.path.exists(os.path.join(Config.DATA_DIR, "cookies.txt"))
    return render_template("index.html", cookies_uploaded=cookies_exist)


@app.route("/manual")
def manual_page():
    """Manual Crop পেইজ"""
    return render_template("manual_crop.html")


@app.route("/preview/<clip_id>")
def preview_page(clip_id):
    """ক্লিপ প্রিভিউ পেইজ"""
    clip = clips.get(clip_id)
    return render_template("preview.html", clip=clip, clip_id=clip_id)


@app.route("/scheduler")
def scheduler_page():
    """শিডিউলার পেইজ"""
    jobs = sched.get_jobs()
    logs = sched.get_logs()
    return render_template("scheduler.html", jobs=jobs, logs=logs)


@app.route("/settings")
def settings_page():
    """সেটিংস পেইজ"""
    cookies_exist = os.path.exists(os.path.join(Config.DATA_DIR, "cookies.txt"))
    return render_template("settings.html", cookies_uploaded=cookies_exist)


@app.route("/logs")
def logs_page():
    """গ্লোবাল লগ পেইজ"""
    return render_template("logs.html", logs=global_logs)


# ══════════════════════════════════════════
#            COOKIES UPLOAD (Bot Detection Fix)
# ══════════════════════════════════════════

@app.route("/api/cookies/upload", methods=["POST"])
def upload_cookies():
    """
    YouTube কুকিজ ফাইল আপলোড।
    ব্রাউজার থেকে cookies.txt এক্সপোর্ট করে আপলোড দিলে
    বট ডিটেকশন আর হবে না।
    """
    if "cookies_file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["cookies_file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    # Validate it's a text file
    if not file.filename.endswith(".txt"):
        return jsonify({"error": "Only .txt files allowed (Netscape cookie format)"}), 400

    save_path = os.path.join(Config.DATA_DIR, "cookies.txt")
    file.save(save_path)

    # Validate cookie format
    try:
        with open(save_path, "r") as f:
            content = f.read()
            if ".youtube.com" not in content and "youtube" not in content.lower():
                os.remove(save_path)
                return jsonify({"error": "This doesn't look like a YouTube cookies file"}), 400
    except Exception:
        pass

    # Tell downloader to use cookies
    dl.set_cookies(save_path)

    add_log("system", "✅ YouTube cookies uploaded successfully", "success")
    return jsonify({
        "success": True,
        "message": "Cookies uploaded! Bot detection should be bypassed now."
    })


@app.route("/api/cookies/delete", methods=["POST"])
def delete_cookies():
    """কুকিজ ফাইল ডিলিট করা।"""
    save_path = os.path.join(Config.DATA_DIR, "cookies.txt")
    if os.path.exists(save_path):
        os.remove(save_path)
    dl.set_cookies(None)
    add_log("system", "🗑️ Cookies deleted", "warning")
    return jsonify({"success": True})


@app.route("/api/cookies/status")
def cookies_status():
    """কুকিজ আছে কিনা চেক।"""
    path = os.path.join(Config.DATA_DIR, "cookies.txt")
    exists = os.path.exists(path)
    size = os.path.getsize(path) if exists else 0
    return jsonify({"exists": exists, "size": size})


# ══════════════════════════════════════════
#          REAL-TIME LOG STREAM (SSE)
# ══════════════════════════════════════════

@app.route("/api/logs/stream/<task_id>")
def stream_logs(task_id):
    """
    Server-Sent Events (SSE) — রিয়েল টাইম লগ স্ট্রিম।
    ব্রাউজার EventSource দিয়ে কানেক্ট করবে, লাইভ লগ দেখবে।
    """
    if task_id not in log_queues:
        log_queues[task_id] = queue.Queue()

    def event_stream():
        q = log_queues[task_id]
        while True:
            try:
                # Wait for new log entry (timeout 30s to keep alive)
                entry = q.get(timeout=30)
                data = json.dumps(entry)
                yield f"data: {data}\n\n"
            except queue.Empty:
                # Send heartbeat to keep connection alive
                yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
            except GeneratorExit:
                break

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
    )


@app.route("/api/logs/global")
def get_global_logs():
    """সব লগ JSON-এ রিটার্ন।"""
    limit = request.args.get("limit", 100, type=int)
    return jsonify({"logs": global_logs[-limit:]})


# ══════════════════════════════════════════
#              AUTO ANALYZE
# ══════════════════════════════════════════

@app.route("/api/analyze", methods=["POST"])
def analyze():
    """
    মূল এন্ডপয়েন্ট: URL দিলে ডাউনলোড → ট্রান্সক্রাইব → AI Analysis → Crop।
    সব লগ রিয়েল টাইমে দেখা যায়।
    """
    data = request.json
    url = data.get("url", "").strip()
    num_clips = min(int(data.get("num_clips", 5)), 15)

    if not url:
        return jsonify({"error": "URL দিন!"}), 400

    task_id = str(uuid.uuid4())[:8]
    tasks[task_id] = {"status": "starting", "progress": 0}
    log_queues[task_id] = queue.Queue()

    def run():
        try:
            # ──── STEP 1: Download ────
            tasks[task_id] = {"status": "downloading", "progress": 10}
            add_log(task_id, f"📥 ভিডিও ডাউনলোড শুরু: {url[:80]}...")
            add_log(task_id, f"🎬 Resolution: 480p (analysis mode)")

            cookies_path = os.path.join(Config.DATA_DIR, "cookies.txt")
            has_cookies = os.path.exists(cookies_path)
            if has_cookies:
                add_log(task_id, "🍪 Cookies ব্যবহার হচ্ছে — বট ডিটেকশন বাইপাস", "success")
            else:
                add_log(task_id, "⚠️ Cookies নেই — বট ডিটেকশন হতে পারে", "warning")

            t1 = time.time()
            video_info = dl.download_full(url, max_height=480)
            download_time = time.time() - t1
            video_path = video_info["video_path"]

            file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
            add_log(task_id, f"✅ ডাউনলোড সম্পন্ন! ({file_size_mb:.1f} MB, {download_time:.1f}s)", "success")
            add_log(task_id, f"📋 Title: {video_info['title'][:60]}")
            add_log(task_id, f"⏱️ Duration: {video_info['duration']}s ({video_info['duration']//60}m {video_info['duration']%60}s)")

            heatmap = video_info.get("heatmap")
            if heatmap:
                add_log(task_id, f"🔥 Heatmap পাওয়া গেছে! ({len(heatmap)} data points)", "success")
            else:
                add_log(task_id, "📊 Heatmap পাওয়া যায়নি — শুধু AI transcript analysis হবে", "warning")

            # ──── STEP 2: Extract Audio ────
            tasks[task_id] = {"status": "extracting_audio", "progress": 20}
            add_log(task_id, "🎵 অডিও এক্সট্র্যাক্ট হচ্ছে (64kbps MP3)...")

            t2 = time.time()
            audio_path = dl.extract_audio(video_path)
            audio_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
            add_log(task_id, f"✅ অডিও ready ({audio_size_mb:.1f} MB, {time.time()-t2:.1f}s)", "success")

            # ──── STEP 3: Transcribe ────
            tasks[task_id] = {"status": "transcribing", "progress": 30}
            add_log(task_id, "🎙️ Groq Whisper API-তে transcription শুরু...")
            add_log(task_id, f"📤 Sending {audio_size_mb:.1f} MB audio to Groq...")

            t3 = time.time()
            transcript = transcriber.transcribe(audio_path)
            transcribe_time = time.time() - t3

            num_segments = len(transcript.get("segments", []))
            total_words = len(transcript.get("text", "").split())
            add_log(task_id, f"✅ Transcription সম্পন্ন! ({transcribe_time:.1f}s)", "success")
            add_log(task_id, f"📝 {num_segments} segments, ~{total_words} words, Language: {transcript.get('language', 'N/A')}")

            # Cleanup audio — disk space save
            dl.cleanup(audio_path)
            add_log(task_id, "🗑️ Temp audio file deleted (disk space saved)")

            # ──── STEP 4: AI Analysis ────
            tasks[task_id] = {"status": "analyzing", "progress": 50}
            add_log(task_id, f"🧠 AI Analysis শুরু — {num_clips}টা viral segment খুঁজছে...")
            add_log(task_id, "🤖 Grok AI + 💎 Gemini AI — দুইটা parallel analysis...")

            t4 = time.time()
            segments = ai.find_viral_segments(
                transcript["segments"],
                heatmap,
                num_clips,
                video_info["title"]
            )
            analyze_time = time.time() - t4

            if not segments:
                add_log(task_id, "❌ কোনো viral segment পাওয়া যায়নি!", "error")
                tasks[task_id] = {"status": "error", "progress": 0, "error": "No viral segments found. Video might be too short or transcript unclear."}
                return

            add_log(task_id, f"✅ {len(segments)}টা viral segment পাওয়া গেছে! ({analyze_time:.1f}s)", "success")
            for i, seg in enumerate(segments):
                add_log(task_id, f"  #{i+1} [{seg['start_time']:.0f}s-{seg['end_time']:.0f}s] Score: {seg.get('hook_score',0)}/10 | {seg.get('reason','')[:50]}")

            # Delete 480p full video — no longer needed
            dl.cleanup(video_path)
            add_log(task_id, "🗑️ 480p analysis video deleted")

            # ──── STEP 5: Crop Segments ────
            tasks[task_id] = {"status": "cropping", "progress": 65}
            add_log(task_id, f"✂️ {len(segments)}টা segment crop শুরু (720p, 9:16)...")

            result_clips = []
            for i, seg in enumerate(segments):
                clip_id = f"{task_id}_{i}"
                clip_num = i + 1

                add_log(task_id, f"  📥 Clip #{clip_num}: Downloading segment [{seg['start_time']:.0f}s-{seg['end_time']:.0f}s]...")

                # Download just this segment at higher quality
                t_seg = time.time()
                try:
                    seg_name = f"{clip_id}_raw.mp4"
                    seg_path = dl.download_segment(url, seg["start_time"], seg["end_time"], seg_name)
                    add_log(task_id, f"  ✅ Clip #{clip_num}: Downloaded ({time.time()-t_seg:.1f}s)")
                except Exception as e:
                    add_log(task_id, f"  ⚠️ Clip #{clip_num}: Segment download failed, using full video fallback", "warning")
                    # Fallback: re-download full video and cut
                    full_info = dl.download_full(url, max_height=720)
                    seg_path = full_info["video_path"]

                # Crop to 9:16
                add_log(task_id, f"  ✂️ Clip #{clip_num}: Cropping to 1080x1920...")
                t_crop = time.time()
                short_name = f"{clip_id}.mp4"
                try:
                    short_path = vp.crop_to_shorts(
                        seg_path, 0, seg["end_time"] - seg["start_time"], short_name
                    )
                except Exception:
                    # If segment was full video, cut from correct time
                    short_path = vp.crop_to_shorts(
                        seg_path, seg["start_time"], seg["end_time"], short_name
                    )

                add_log(task_id, f"  ✅ Clip #{clip_num}: Cropped ({time.time()-t_crop:.1f}s)")

                # Cleanup raw segment
                if seg_path != short_path:
                    dl.cleanup(seg_path)

                # Thumbnail
                thumb = vp.generate_thumbnail(short_path)

                # AI Metadata
                add_log(task_id, f"  🏷️ Clip #{clip_num}: Generating title, hashtags, description...")
                seg_text = " ".join(
                    s["text"] for s in transcript["segments"]
                    if s["start"] >= seg["start_time"] and s["end"] <= seg["end_time"]
                )
                meta = ai.generate_metadata(seg_text, seg.get("suggested_title", ""))

                clip_data = {
                    "id": clip_id,
                    "path": short_path,
                    "thumbnail": thumb,
                    "start_time": seg["start_time"],
                    "end_time": seg["end_time"],
                    "duration": seg["end_time"] - seg["start_time"],
                    "hook_score": seg.get("hook_score", 0),
                    "reason": seg.get("reason", ""),
                    "source": seg.get("source", ""),
                    "title": meta.get("title", ""),
                    "description": meta.get("description", ""),
                    "hashtags": meta.get("hashtags", []),
                    "caption_text": meta.get("caption_text", ""),
                }
                clips[clip_id] = clip_data
                result_clips.append(clip_data)

                if short_path and os.path.exists(short_path):
                    size_mb = os.path.getsize(short_path) / (1024 * 1024)
                    add_log(task_id, f"  ✅ Clip #{clip_num}: Ready! ({size_mb:.1f} MB) — \"{meta.get('title','')[:40]}\"", "success")

                pct = 65 + int(clip_num / len(segments) * 30)
                tasks[task_id] = {
                    "status": "cropping", "progress": pct,
                    "current": clip_num, "total": len(segments)
                }

            # ──── DONE ────
            total_time = time.time() - t1
            add_log(task_id, f"🎉 সব সম্পন্ন! {len(result_clips)}টা Shorts তৈরি হয়েছে! (Total: {total_time:.0f}s)", "success")
            add_log(task_id, "─" * 40)

            tasks[task_id] = {
                "status": "done", "progress": 100,
                "clips": result_clips
            }

            # Cleanup old files
            vp.cleanup_old_files()

        except Exception as e:
            error_msg = str(e)
            add_log(task_id, f"❌ ERROR: {error_msg}", "error")

            # Specific error hints
            if "Sign in to confirm" in error_msg or "bot" in error_msg.lower():
                add_log(task_id, "💡 TIP: YouTube বট ডিটেকশন হচ্ছে। Settings থেকে cookies.txt আপলোড করুন!", "warning")
            elif "HTTP Error 429" in error_msg:
                add_log(task_id, "💡 TIP: Too many requests। কিছুক্ষণ পর আবার চেষ্টা করুন।", "warning")
            elif "Video unavailable" in error_msg:
                add_log(task_id, "💡 TIP: ভিডিওটি private/deleted হতে পারে।", "warning")

            tasks[task_id] = {"status": "error", "progress": 0, "error": error_msg}

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return jsonify({"task_id": task_id})


@app.route("/api/status/<task_id>")
def status(task_id):
    """Task status চেক।"""
    return jsonify(tasks.get(task_id, {"status": "not_found"}))


# ══════════════════════════════════════════
#              MANUAL CROP
# ══════════════════════════════════════════

@app.route("/api/manual_crop", methods=["POST"])
def manual_crop():
    """নির্দিষ্ট সময় থেকে manually crop।"""
    data = request.json
    url = data.get("url", "").strip()
    start = float(data.get("start_time", 0))
    end = float(data.get("end_time", 60))

    if not url or end <= start:
        return jsonify({"error": "Invalid input"}), 400

    if end - start > 180:
        return jsonify({"error": "Maximum 3 minutes crop allowed"}), 400

    task_id = str(uuid.uuid4())[:8]
    tasks[task_id] = {"status": "starting", "progress": 0}
    log_queues[task_id] = queue.Queue()

    def run():
        try:
            clip_id = f"m_{task_id}"

            # Download segment
            tasks[task_id] = {"status": "downloading_segment", "progress": 20}
            add_log(task_id, f"📥 Segment download: {start:.0f}s → {end:.0f}s")

            raw_name = f"{clip_id}_raw.mp4"
            raw_path = dl.download_segment(url, start, end, raw_name)
            add_log(task_id, "✅ Segment downloaded", "success")

            # Crop to 9:16
            tasks[task_id] = {"status": "cropping", "progress": 50}
            add_log(task_id, "✂️ Cropping to 9:16 (1080x1920)...")

            short_path = vp.crop_to_shorts(raw_path, 0, end - start, f"{clip_id}.mp4")
            dl.cleanup(raw_path)
            add_log(task_id, "✅ Crop done!", "success")

            thumb = vp.generate_thumbnail(short_path)

            # Metadata
            tasks[task_id] = {"status": "generating_metadata", "progress": 75}
            add_log(task_id, "🎙️ Transcribing for metadata...")

            audio = dl.extract_audio(short_path)
            transcript = transcriber.transcribe(audio)
            dl.cleanup(audio)

            meta = ai.generate_metadata(transcript["text"])
            add_log(task_id, f"✅ Metadata generated: \"{meta.get('title','')[:50]}\"", "success")

            clip_data = {
                "id": clip_id,
                "path": short_path,
                "thumbnail": thumb,
                "start_time": start,
                "end_time": end,
                "duration": end - start,
                "hook_score": 0,
                "reason": "Manual crop",
                "source": "manual",
                "title": meta.get("title", ""),
                "description": meta.get("description", ""),
                "hashtags": meta.get("hashtags", []),
                "caption_text": meta.get("caption_text", ""),
            }
            clips[clip_id] = clip_data
            tasks[task_id] = {"status": "done", "progress": 100, "clips": [clip_data]}
            add_log(task_id, "🎉 Manual crop সম্পন্ন!", "success")

        except Exception as e:
            add_log(task_id, f"❌ Error: {str(e)}", "error")
            tasks[task_id] = {"status": "error", "error": str(e)}

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"task_id": task_id})


# ══════════════════════════════════════════
#              TEXT OVERLAY
# ══════════════════════════════════════════

@app.route("/api/add_text", methods=["POST"])
def add_text():
    """ভিডিওতে text overlay যোগ করা।"""
    data = request.json
    clip_id = data.get("clip_id")
    clip = clips.get(clip_id)
    if not clip:
        return jsonify({"error": "Clip not found"}), 404

    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "Text is empty"}), 400

    try:
        add_log("system", f"🔤 Adding text overlay to {clip_id}: \"{text[:30]}...\"")
        new_path = vp.add_text_overlay(
            clip["path"],
            text,
            data.get("position", "bottom"),
            int(data.get("font_size", 42)),
            float(data.get("bg_opacity", 0.7)),
            data.get("text_color", "white"),
            data.get("bg_color", "black"),
        )
        clip["path"] = new_path
        clips[clip_id] = clip
        add_log("system", f"✅ Text added to {clip_id}", "success")
        return jsonify({"success": True})
    except Exception as e:
        add_log("system", f"❌ Text overlay error: {str(e)}", "error")
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════
#           VIDEO/THUMBNAIL SERVE
# ══════════════════════════════════════════

@app.route("/api/video/<clip_id>")
def serve_video(clip_id):
    """ভিডিও ফাইল serve করা।"""
    clip = clips.get(clip_id)
    if clip and os.path.exists(clip["path"]):
        return send_file(
            clip["path"],
            mimetype="video/mp4",
            as_attachment=False,
            download_name=f"{clip_id}.mp4"
        )
    return jsonify({"error": "Video not found"}), 404


@app.route("/api/thumb/<clip_id>")
def serve_thumb(clip_id):
    """থাম্বনেইল serve করা।"""
    clip = clips.get(clip_id)
    if clip and clip.get("thumbnail") and os.path.exists(clip["thumbnail"]):
        return send_file(clip["thumbnail"], mimetype="image/jpeg")
    return jsonify({"error": "Thumbnail not found"}), 404


@app.route("/api/download/<clip_id>")
def download_clip(clip_id):
    """ক্লিপ ডাউনলোড।"""
    clip = clips.get(clip_id)
    if clip and os.path.exists(clip["path"]):
        return send_file(
            clip["path"],
            mimetype="video/mp4",
            as_attachment=True,
            download_name=f"short_{clip_id}.mp4"
        )
    return jsonify({"error": "Clip not found"}), 404


# ══════════════════════════════════════════
#           YOUTUBE CONNECTION
# ══════════════════════════════════════════

@app.route("/api/youtube/auth_url")
def yt_auth_url():
    """YouTube OAuth URL জেনারেট।"""
    try:
        auth_url = yt.get_auth_url()
        add_log("system", "🔗 YouTube OAuth URL generated")
        return jsonify({"url": auth_url})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/youtube/callback")
def yt_callback():
    """YouTube OAuth callback — code exchange।"""
    code = request.args.get("code")
    if not code:
        return "No authorization code received", 400

    try:
        yt.handle_callback(code)
        add_log("system", "✅ YouTube channel connected!", "success")
        return redirect("/settings?yt=connected")
    except Exception as e:
        add_log("system", f"❌ YouTube auth error: {str(e)}", "error")
        return redirect(f"/settings?yt=error&msg={str(e)}")


@app.route("/api/youtube/status")
def yt_status():
    """YouTube connection status।"""
    connected = yt.is_connected()
    channel = None
    if connected:
        try:
            channel = yt.get_channel_info()
        except Exception:
            # Token might be expired, try refresh
            try:
                yt.refresh_token()
                channel = yt.get_channel_info()
            except Exception:
                connected = False
    return jsonify({"connected": connected, "channel": channel})


@app.route("/api/youtube/disconnect", methods=["POST"])
def yt_disconnect():
    """YouTube channel disconnect / log out।"""
    yt.disconnect()
    add_log("system", "🔌 YouTube disconnected", "warning")
    return jsonify({"success": True})


# ══════════════════════════════════════════
#            UPLOAD TO YOUTUBE
# ══════════════════════════════════════════

@app.route("/api/upload", methods=["POST"])
def upload():
    """একটি ক্লিপ YouTube-এ আপলোড।"""
    data = request.json
    clip_id = data.get("clip_id")
    clip = clips.get(clip_id)

    if not clip:
        return jsonify({"error": "Clip not found"}), 404

    if not yt.is_connected():
        return jsonify({"error": "YouTube not connected! Settings থেকে connect করুন।"}), 400

    try:
        title = clip.get("title", "Amazing Short! 🔥")
        add_log("system", f"📤 Uploading to YouTube: \"{title[:50]}\"...")

        desc = clip.get("description", "") + "\n\n" + " ".join(clip.get("hashtags", []))
        result = yt.upload_short(
            clip["path"],
            title,
            desc,
            clip.get("hashtags", []),
            clip.get("thumbnail")
        )

        add_log("system", f"✅ Upload successful! {result.get('url', '')}", "success")
        sched.log(title, "success", result)
        return jsonify({"success": True, "result": result})

    except Exception as e:
        error_msg = str(e)
        add_log("system", f"❌ Upload failed: {error_msg}", "error")
        sched.log(clip.get("title", "Unknown"), "error", {"error": error_msg})

        if "401" in error_msg or "unauthorized" in error_msg.lower():
            return jsonify({"error": "YouTube token expired। Settings থেকে reconnect করুন।"}), 401

        return jsonify({"error": error_msg}), 500


@app.route("/api/upload/all", methods=["POST"])
def upload_all():
    """সব generated clips একসাথে আপলোড।"""
    data = request.json
    clip_ids = data.get("clip_ids", [])

    if not clip_ids:
        return jsonify({"error": "No clips specified"}), 400

    results = []
    for cid in clip_ids:
        clip = clips.get(cid)
        if not clip:
            results.append({"clip_id": cid, "status": "not_found"})
            continue

        try:
            desc = clip.get("description", "") + "\n\n" + " ".join(clip.get("hashtags", []))
            result = yt.upload_short(
                clip["path"],
                clip.get("title", "Short #Shorts"),
                desc,
                clip.get("hashtags", []),
                clip.get("thumbnail")
            )
            results.append({"clip_id": cid, "status": "success", "result": result})
            add_log("system", f"✅ Uploaded: {clip.get('title','')[:40]}", "success")
            sched.log(clip.get("title", ""), "success", result)
            # Small delay between uploads
            time.sleep(2)
        except Exception as e:
            results.append({"clip_id": cid, "status": "error", "error": str(e)})
            add_log("system", f"❌ Upload failed for {cid}: {str(e)}", "error")

    return jsonify({"results": results})


# ══════════════════════════════════════════
#         GOOGLE DRIVE + SCHEDULER
# ══════════════════════════════════════════

@app.route("/api/schedule/setup", methods=["POST"])
def setup_schedule():
    """Google Drive auto-upload শিডিউল সেটআপ।"""
    data = request.json
    folder_url = data.get("folder_url", "").strip()
    times = data.get("times", [{"hour": 12, "minute": 0}, {"hour": 20, "minute": 0}])

    if not folder_url:
        return jsonify({"error": "Folder URL required"}), 400

    # Validate folder URL
    try:
        zip_files = gdrive.list_zip_files(folder_url)
        add_log("system", f"📁 Drive folder found: {len(zip_files)} zip files", "success")
    except Exception as e:
        add_log("system", f"❌ Drive folder error: {str(e)}", "error")
        return jsonify({"error": f"Cannot access folder: {str(e)}"}), 400

    # Save config
    cfg_path = os.path.join(Config.DATA_DIR, "schedule_config.json")
    with open(cfg_path, "w") as f:
        json.dump({"folder_url": folder_url, "times": times}, f)

    # Setup jobs
    for i, t in enumerate(times):
        job_id = f"auto_upload_{i}"
        sched.add_job(job_id, _auto_upload, t["hour"], t.get("minute", 0))
        add_log("system", f"⏰ Schedule set: {t['hour']:02d}:{t.get('minute',0):02d} (Job: {job_id})", "success")

    return jsonify({"success": True, "jobs": len(times), "zip_files": len(zip_files)})


def _auto_upload():
    """Scheduled auto upload — Google Drive zip থেকে random video নিয়ে upload।"""
    add_log("scheduler", "🤖 Auto-upload triggered!")

    cfg_path = os.path.join(Config.DATA_DIR, "schedule_config.json")
    if not os.path.exists(cfg_path):
        add_log("scheduler", "❌ No schedule config found", "error")
        return

    with open(cfg_path) as f:
        cfg = json.load(f)

    try:
        # Get random video from zip
        add_log("scheduler", "📦 Getting random video from Google Drive zip...")
        video_path, video_name = gdrive.get_random_video(cfg["folder_url"])
        add_log("scheduler", f"🎬 Selected: {video_name}", "success")

        # Transcribe for metadata
        add_log("scheduler", "🎙️ Transcribing for auto-metadata...")
        audio = dl.extract_audio(video_path)
        transcript = transcriber.transcribe(audio)
        dl.cleanup(audio)

        meta = ai.generate_metadata(transcript["text"])
        thumb = vp.generate_thumbnail(video_path)
        add_log("scheduler", f"🏷️ Title: {meta.get('title','')[:50]}")

        # Upload
        add_log("scheduler", "📤 Uploading to YouTube...")
        desc = meta.get("description", "") + "\n\n" + " ".join(meta.get("hashtags", []))
        result = yt.upload_short(
            video_path,
            meta.get("title", "🔥 #Shorts"),
            desc,
            meta.get("hashtags", []),
            thumb
        )

        add_log("scheduler", f"✅ Auto-upload complete! {result.get('url', '')}", "success")
        sched.log(video_name, "success", result)

        # Cleanup
        dl.cleanup(video_path)
        if thumb:
            dl.cleanup(thumb)

    except Exception as e:
        add_log("scheduler", f"❌ Auto-upload failed: {str(e)}", "error")
        sched.log("auto_upload", "error", {"error": str(e)})


@app.route("/api/schedule/jobs")
def list_jobs():
    return jsonify({"jobs": sched.get_jobs()})


@app.route("/api/schedule/logs")
def list_logs():
    return jsonify({"logs": sched.get_logs()})


@app.route("/api/schedule/remove/<job_id>", methods=["DELETE"])
def remove_job(job_id):
    sched.remove_job(job_id)
    add_log("system", f"🗑️ Schedule removed: {job_id}", "warning")
    return jsonify({"success": True})


@app.route("/api/schedule/trigger", methods=["POST"])
def trigger_manual_upload():
    """ম্যানুয়ালি এখনই auto-upload trigger করা (টেস্ট করার জন্য)।"""
    threading.Thread(target=_auto_upload, daemon=True).start()
    return jsonify({"success": True, "message": "Auto-upload triggered!"})


# ══════════════════════════════════════════
#              CLIP MANAGEMENT
# ══════════════════════════════════════════

@app.route("/api/clips")
def list_clips():
    """সব generated clips এর লিস্ট।"""
    clip_list = []
    for cid, c in clips.items():
        clip_list.append({
            "id": cid,
            "title": c.get("title", ""),
            "duration": c.get("duration", 0),
            "hook_score": c.get("hook_score", 0),
            "source": c.get("source", ""),
            "exists": os.path.exists(c.get("path", "")),
        })
    return jsonify({"clips": clip_list})


@app.route("/api/clips/<clip_id>", methods=["DELETE"])
def delete_clip(clip_id):
    """একটি ক্লিপ ডিলিট করা।"""
    clip = clips.pop(clip_id, None)
    if clip:
        dl.cleanup(clip.get("path", ""))
        dl.cleanup(clip.get("thumbnail", ""))
        return jsonify({"success": True})
    return jsonify({"error": "Clip not found"}), 404


@app.route("/api/clips/<clip_id>/metadata", methods=["PUT"])
def update_metadata(clip_id):
    """ক্লিপের title, description, hashtags আপডেট করা।"""
    clip = clips.get(clip_id)
    if not clip:
        return jsonify({"error": "Clip not found"}), 404

    data = request.json
    if "title" in data:
        clip["title"] = data["title"]
    if "description" in data:
        clip["description"] = data["description"]
    if "hashtags" in data:
        clip["hashtags"] = data["hashtags"]
    if "caption_text" in data:
        clip["caption_text"] = data["caption_text"]

    clips[clip_id] = clip
    return jsonify({"success": True, "clip": clip})


# ══════════════════════════════════════════
#              SYSTEM / HEALTH
# ══════════════════════════════════════════

@app.route("/api/health")
def health():
    """Health check endpoint।"""
    temp_files = len(os.listdir(Config.TEMP_DIR)) if os.path.exists(Config.TEMP_DIR) else 0
    output_files = len(os.listdir(Config.OUTPUT_DIR)) if os.path.exists(Config.OUTPUT_DIR) else 0

    return jsonify({
        "status": "healthy",
        "time": datetime.now().isoformat(),
        "temp_files": temp_files,
        "output_files": output_files,
        "active_tasks": len([t for t in tasks.values() if t.get("status") not in ("done", "error")]),
        "total_clips": len(clips),
        "cookies_loaded": os.path.exists(os.path.join(Config.DATA_DIR, "cookies.txt")),
        "youtube_connected": yt.is_connected(),
        "scheduled_jobs": len(sched.get_jobs()),
    })


@app.route("/api/cleanup", methods=["POST"])
def cleanup_files():
    """Temp ও output ফোল্ডার ক্লিন করা।"""
    vp.cleanup_old_files()
    add_log("system", "🧹 Temp files cleaned up", "success")
    return jsonify({"success": True})


# ══════════════════════════════════════════
#                  RUN
# ══════════════════════════════════════════

if __name__ == "__main__":
    # Load cookies if exists
    cookies_path = os.path.join(Config.DATA_DIR, "cookies.txt")
    if os.path.exists(cookies_path):
        dl.set_cookies(cookies_path)
        print("🍪 Cookies loaded from data/cookies.txt")

    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"

    print(f"""
    ╔══════════════════════════════════════╗
    ║   YouTube Shorts AI Tool 🎬         ║
    ║   Running on port {port}              ║
    ║   Debug: {debug}                      ║
    ╚══════════════════════════════════════╝
    """)

    app.run(host="0.0.0.0", port=port, debug=debug)