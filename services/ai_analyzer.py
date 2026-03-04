import json
import requests
import google.generativeai as genai
from config import Config


class AIAnalyzer:
    def __init__(self):
        self.grok_api_key = Config.GROK_API_KEY
        self.gemini_api_key = Config.GEMINI_API_KEY
        if self.gemini_api_key:
            genai.configure(api_key=self.gemini_api_key)

    def find_viral_segments(
        self, transcript_segments: list, heatmap: list = None,
        num_clips: int = 5, video_title: str = ""
    ) -> list:
        """
        Grok + Gemini দুইটা দিয়ে analyze করে, merge করে,
        heatmap fallback-ও আছে।
        """
        transcript_text = self._format_transcript(transcript_segments)

        # Run both APIs
        grok_results = self._analyze_grok(transcript_text, heatmap, num_clips, video_title)
        gemini_results = self._analyze_gemini(transcript_text, heatmap, num_clips, video_title)

        merged = self._merge_results(grok_results, gemini_results)

        # Heatmap fallback
        if len(merged) < num_clips and heatmap:
            heatmap_segments = self._heatmap_fallback(heatmap, transcript_segments)
            for seg in heatmap_segments:
                if not self._is_overlapping(seg, merged):
                    merged.append(seg)

        merged.sort(key=lambda x: x.get("hook_score", 0), reverse=True)
        return merged[:num_clips]

    def _format_transcript(self, segments: list) -> str:
        """Transcript segments কে readable text-এ convert।"""
        lines = []
        for seg in segments:
            lines.append(f"[{seg['start']:.1f}s-{seg['end']:.1f}s] {seg['text']}")
        return "\n".join(lines)

    def _build_prompt(self, transcript: str, heatmap: list, num_clips: int, title: str) -> str:
        """AI-এর জন্য prompt তৈরি।"""
        heatmap_info = ""
        if heatmap:
            heatmap_info = f"\n\nYouTube Heatmap (Most Replayed):\n{json.dumps(heatmap[:30])}"

        return f"""You are a YouTube Shorts viral content expert.
Video title: "{title}"

Analyze this transcript and find {num_clips} MOST VIRAL segments for YouTube Shorts (30-59 seconds each).

FIND segments with:
- Strong emotional hooks or shocking openings
- Controversial/thought-provoking statements  
- Funny or highly entertaining moments
- Key insights, wisdom, or "aha" moments
- Dramatic reveals or storytelling peaks

Transcript:
{transcript[:8000]}
{heatmap_info}

Return ONLY a valid JSON array. Each object must have:
- "start_time": float (seconds)
- "end_time": float (seconds, max 59s duration)
- "hook_score": integer 1-10
- "reason": string (why it's viral, 1 sentence)
- "suggested_title": string (catchy title with emoji)

IMPORTANT: Each clip must be 30-59 seconds. Return exactly {num_clips} segments.
Return ONLY the JSON array, no markdown, no explanation."""

    def _analyze_grok(self, transcript: str, heatmap: list, num_clips: int, title: str) -> list:
        """Grok AI (xAI) দিয়ে viral segment খোঁজা।"""
        if not self.grok_api_key:
            return []

        prompt = self._build_prompt(transcript, heatmap, num_clips, title)

        try:
            response = requests.post(
                "https://api.x.ai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.grok_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "grok-2-latest",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.6,
                },
                timeout=120,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return self._parse_json_response(content)
        except Exception as e:
            print(f"Grok error: {e}")
            return []

    def _analyze_gemini(self, transcript: str, heatmap: list, num_clips: int, title: str) -> list:
        """Gemini AI দিয়ে viral segment খোঁজা।"""
        if not self.gemini_api_key:
            return []

        prompt = self._build_prompt(transcript, heatmap, num_clips, title)

        try:
            model = genai.GenerativeModel("gemini-1.5-flash")
            response = model.generate_content(prompt)
            return self._parse_json_response(response.text)
        except Exception as e:
            print(f"Gemini error: {e}")
            return []

    def _parse_json_response(self, text: str) -> list:
        """AI response থেকে JSON parse করা।"""
        text = text.strip()

        # Markdown code block remove
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        # JSON array খুঁজে বের করা
        start = text.find("[")
        end = text.rfind("]") + 1
        if start != -1 and end > start:
            text = text[start:end]

        try:
            result = json.loads(text)
            if isinstance(result, list):
                # Duration validate ও fix
                for seg in result:
                    duration = seg.get("end_time", 0) - seg.get("start_time", 0)
                    if duration > 59:
                        seg["end_time"] = seg["start_time"] + 59
                    if duration < 15:
                        seg["end_time"] = seg["start_time"] + 45
                return result
        except json.JSONDecodeError:
            pass
        return []

    def _merge_results(self, grok: list, gemini: list) -> list:
        """দুই AI-এর রেজাল্ট merge + overlapping segment boost।"""
        all_segs = []

        for seg in grok:
            seg["source"] = "grok"
            all_segs.append(seg)

        for seg in gemini:
            overlapping = False
            for existing in all_segs:
                if abs(existing["start_time"] - seg["start_time"]) < 15:
                    # দুই AI-ই একই জায়গা suggest করেছে — score boost
                    existing["hook_score"] = min(10, existing.get("hook_score", 5) + 1)
                    existing["source"] = "both"
                    overlapping = True
                    break
            if not overlapping:
                seg["source"] = "gemini"
                all_segs.append(seg)

        return all_segs

    def _heatmap_fallback(self, heatmap: list, segments: list) -> list:
        """AI fail করলে heatmap peaks থেকে segment নেওয়া।"""
        results = []
        if not heatmap:
            return results

        sorted_hm = sorted(
            heatmap,
            key=lambda x: x.get("value", x.get("end_time", 0)),
            reverse=True
        )

        for i, point in enumerate(sorted_hm[:5]):
            start = point.get("start_time", 0)
            end = min(start + 50, point.get("end_time", start + 50))
            if end - start > 59:
                end = start + 59

            results.append({
                "start_time": start,
                "end_time": end,
                "hook_score": max(1, 8 - i),
                "reason": "Most Replayed section (Heatmap peak)",
                "suggested_title": f"🔥 Most Watched Moment #{i + 1}",
                "source": "heatmap",
            })
        return results

    def _is_overlapping(self, seg: dict, existing: list) -> bool:
        """দুইটা segment overlap করছে কিনা চেক।"""
        for ex in existing:
            if abs(ex["start_time"] - seg["start_time"]) < 20:
                return True
        return False

    def generate_metadata(self, transcript_text: str, title: str = "") -> dict:
        """Shorts-এর জন্য title, description, hashtags generate।"""
        prompt = f"""Generate YouTube Shorts metadata.
Context: {title}
Content: {transcript_text[:1500]}

Return JSON:
{{"title": "catchy title with emoji (max 80 chars)", "description": "2-3 line engaging description", "hashtags": ["#shorts", "#viral", ...10 more relevant tags], "caption_text": "short hook text for video overlay (max 8 words)"}}

Return ONLY valid JSON."""

        try:
            if self.gemini_api_key:
                model = genai.GenerativeModel("gemini-1.5-flash")
                response = model.generate_content(prompt)
                return self._parse_metadata(response.text)
        except Exception as e:
            print(f"Metadata generation error: {e}")

        # Fallback metadata
        return {
            "title": f"🔥 {title[:70]} #Shorts" if title else "🔥 Must Watch! #Shorts",
            "description": "Watch this incredible moment! Like & Subscribe for more.",
            "hashtags": ["#shorts", "#viral", "#trending", "#youtube", "#fyp"],
            "caption_text": "Watch This! 🔥",
        }

    def _parse_metadata(self, text: str) -> dict:
        """Metadata JSON parse করা।"""
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        try:
            start = text.find("{")
            end = text.rfind("}") + 1
            return json.loads(text[start:end])
        except Exception:
            return {
                "title": "🔥 Amazing Short! #Shorts",
                "description": "Watch this!",
                "hashtags": ["#shorts", "#viral"],
                "caption_text": "Watch This!",
            }