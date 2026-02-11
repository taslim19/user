import re
import os
import yt_dlp
import asyncio
import aiohttp
import logging
import random
from typing import Union, List, Dict, Optional
from decouple import config

# Compatibility for youtubesearchpython / py_yt
try:
    from py_yt import VideosSearch, Playlist
except ImportError:
    try:
        from youtubesearchpython import VideosSearch, Playlist
    except ImportError:
        VideosSearch = Playlist = None

from pyUltroid import LOGS, udB

logger = LOGS

# Piped API Instance (Custom only)
PIPED_API_URL = config("PIPED_API_URL", default="api.dragon.indevs.in") or udB.get_key("PIPED_API_URL")
if not PIPED_API_URL.startswith("http"):
    PIPED_API_URL = f"https://{PIPED_API_URL}"
PIPED_API_URL = PIPED_API_URL.rstrip("/")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

def time_to_seconds(time):
    if not time or not isinstance(time, str) or ":" not in time:
        return 0
    string_format = [60, 3600, 86400]
    t = time.split(":")
    t.reverse()
    n = 0
    for i in range(len(t)):
        n += int(t[i]) * (string_format[i - 1] if i > 0 else 1)
    return n

# Cobalt Instances (The most robust downloader/streamer right now)
COBALT_INSTANCES = [
    "https://api.cobalt.tools",
    "https://cobalt.api.unv.ovh",
    "https://cobalt.hot-as-hell.club"
]

class YouTubeAPI:
    """
    YouTube API wrapper that handles metadata via Piped API and downloads via local yt-dlp.
    Falls back to Piped or Cobalt Stream if local download is blocked.
    """
    
    async def get_cobalt_stream(self, video_id: str) -> Optional[str]:
        """Fetch stream URL using Cobalt API."""
        url = self.base + video_id
        random.shuffle(COBALT_INSTANCES)
        for base in COBALT_INSTANCES:
            try:
                headers = {**HEADERS, "Accept": "application/json", "Content-Type": "application/json"}
                json_data = {"url": url, "videoQuality": "720", "audioFormat": "mp3", "isAudioOnly": True}
                async with aiohttp.ClientSession(headers=headers) as session:
                    async with session.post(base, json=json_data, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            return data.get("url")
            except Exception as e:
                logger.debug(f"Cobalt {base} failed: {e}")
                continue
        return None

    async def _fetch(self, path: str, params: dict = None) -> Optional[dict]:
        """Fetch data from the custom Piped instance."""
        url = f"{PIPED_API_URL}/{path.lstrip('/')}"
        try:
            async with aiohttp.ClientSession(headers=HEADERS) as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        return await response.json()
                    logger.error(f"Piped Instance error: Status {response.status} for {url}")
        except Exception as e:
            logger.error(f"Failed to connect to Piped Instance: {e}")
        return None

    def __init__(self):
        self.base = "https://www.youtube.com/watch?v="
        self.regex = r"(?:youtube\.com|youtu\.be)"
        self.listbase = "https://youtube.com/playlist?list="
        self.download_folder = "vcbot/downloads"
        os.makedirs(self.download_folder, exist_ok=True)

    async def exists(self, link: str, videoid: Union[bool, str] = None) -> bool:
        """Check if URL is a valid YouTube URL."""
        if videoid:
            link = self.base + link
        return bool(re.search(self.regex, link))

    def _extract_video_id(self, link: str) -> Optional[str]:
        """Extract video ID from YouTube URL."""
        if "v=" in link:
            return link.split("v=")[-1].split("&")[0]
        elif "youtu.be/" in link:
            return link.split("youtu.be/")[-1].split("?")[0].split("&")[0]
        elif "youtube.com/shorts/" in link:
             return link.split("shorts/")[-1].split("?")[0]
        return None

    async def get_backend_stream(self, video_id: str) -> Optional[str]:
        """Fetch direct audio stream URL from Piped API with retries."""
        logger.info(f"Fetching Piped stream for: {video_id}")
        data = await self._fetch(f"streams/{video_id}")
        if data:
            audio_streams = data.get("audioStreams", [])
            if audio_streams:
                best_audio = sorted(audio_streams, key=lambda x: x.get("bitrate", 0), reverse=True)[0]
                return best_audio.get("url")
        return None

    async def search(self, query: str, limit: int = 10) -> List[Dict]:
        """Search YouTube using Piped API with retries."""
        logger.info(f"Searching Piped for: {query}")
        data = await self._fetch("search", params={"q": query, "filter": "music_videos"})
        if not data:
            # Try plain search if music_videos filter fails
            data = await self._fetch("search", params={"q": query})
            
        if data:
            results = []
            for item in data:
                if item.get("type") == "video":
                    results.append({
                        "title": item.get("title"),
                        "link": "https://www.youtube.com" + item.get("url"),
                        "id": item.get("url").split("v=")[-1],
                        "duration": item.get("duration"),
                        "thumbnails": [{"url": item.get("thumbnail")}]
                    })
                if len(results) >= limit:
                    break
            return results
            
        if VideosSearch:
            try:
                search = VideosSearch(query, limit=limit).result()
                return search["result"]
            except Exception as e:
                logger.error(f"Local search fallback failed: {e}")
        return []

    async def _download_video(self, video_id: str) -> Optional[str]:
        """Download video using yt-dlp."""
        file_path = os.path.join(self.download_folder, f"{video_id}.mp4")
        if os.path.exists(file_path) and os.path.getsize(file_path) > 200 * 1024:
            return file_path
        
        youtube_url = self.base + video_id
        ydl_opts = {
            "format": "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
            "outtmpl": os.path.join(self.download_folder, f"{video_id}.%(ext)s"),
            "merge_output_format": "mp4",
            "quiet": True,
            "no_warnings": True,
            "geo_bypass": True,
            "nocheckcertificate": True,
            "no_playlist": True,
            "ignoreerrors": True,
            "source_address": "0.0.0.0",
            "youtube_include_dash_manifest": True,
            "youtube_include_hls_manifest": True,
            "extractor_args": {
                "youtube": {
                    "player_client": ["android_web", "web_embedded", "ios"],
                }
            },
        }
        cookie_path = os.path.abspath("cookies.txt")
        if os.path.exists(cookie_path):
            ydl_opts["cookiefile"] = cookie_path
        
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._download_with_ytdlp, youtube_url, ydl_opts)
            if os.path.exists(file_path):
                return file_path
            return None
        except Exception as e:
            logger.error(f"Error downloading video for {video_id}: {e}")
            return None

    async def _download_audio(self, video_id: str) -> Optional[str]:
        """Download audio using yt-dlp."""
        for ext in ["m4a", "opus", "webm", "mp3"]:
            file_path = os.path.join(self.download_folder, f"{video_id}.{ext}")
            if os.path.exists(file_path) and os.path.getsize(file_path) > 200 * 1024:
                return file_path
        
        youtube_url = self.base + video_id
        ydl_opts = {
            "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
            "outtmpl": os.path.join(self.download_folder, f"{video_id}.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "geo_bypass": True,
            "nocheckcertificate": True,
            "no_playlist": True,
            "ignoreerrors": True,
            "source_address": "0.0.0.0",
            "youtube_include_dash_manifest": True,
            "youtube_include_hls_manifest": True,
            "extractor_args": {
                "youtube": {
                    "player_client": ["android_web", "web_embedded", "ios"],
                }
            },
        }
        cookie_path = os.path.abspath("cookies.txt")
        if os.path.exists(cookie_path):
            ydl_opts["cookiefile"] = cookie_path
        
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._download_with_ytdlp, youtube_url, ydl_opts)
            for ext in ["m4a", "opus", "webm", "mp3"]:
                test_path = os.path.join(self.download_folder, f"{video_id}.{ext}")
                if os.path.exists(test_path) and os.path.getsize(test_path) > 200 * 1024:
                    return test_path
            
            # Fallback
            ydl_opts["format"] = "best"
            if "extractor_args" in ydl_opts:
                del ydl_opts["extractor_args"]
            await loop.run_in_executor(None, self._download_with_ytdlp, youtube_url, ydl_opts)
            for ext in ["m4a", "opus", "webm", "mp3", "mp4", "webm"]:
                test_path = os.path.join(self.download_folder, f"{video_id}.{ext}")
                if os.path.exists(test_path) and os.path.getsize(test_path) > 200 * 1024:
                    return test_path
            return None
        except Exception as e:
            logger.error(f"Error downloading audio for {video_id}: {e}")
            return None

    def _download_with_ytdlp(self, url: str, opts: dict) -> None:
        """Synchronous download using yt-dlp."""
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

    async def get_stream(self, video_id: str) -> Optional[str]:
        """Get playable stream URL or local path with multiple fallbacks."""
        # 1. Check local cache first (fastest)
        for ext in ["m4a", "opus", "webm", "mp3"]:
            file_path = os.path.join(self.download_folder, f"{video_id}.{ext}")
            if os.path.exists(file_path) and os.path.getsize(file_path) > 100 * 1024:
                return file_path
        
        # 2. Try Piped API (Very fast, bypasses most blocks)
        logger.info(f"Prioritizing Piped stream for {video_id}...")
        url = await self.get_backend_stream(video_id)
        if url:
            return url
            
        # 3. Try Cobalt API (Extremely robust fallback)
        logger.info(f"Piped failed, trying Cobalt stream for {video_id}...")
        url = await self.get_cobalt_stream(video_id)
        if url:
            return url

        # 4. Final fallback to local download (Uses cookies if available)
        logger.warning(f"External APIs failed for {video_id}, attempting local download...")
        return await self._download_audio(video_id)

    async def track_details(self, video_id: str) -> Optional[Dict]:
        """Get track details."""
        try:
            link = self.base + video_id
            search = VideosSearch(link, limit=1)
            result_data = (await search.next())["result"]
            for result in result_data:
                return {
                    "title": result["title"],
                    "link": result["link"],
                    "vidid": result["id"],
                    "duration": result.get("duration"),
                    "duration_min": result.get("duration"),
                    "thumb": result["thumbnails"][0]["url"].split("?")[0],
                }
        except Exception as e:
            logger.error(f"Error getting track details: {e}")
        return None

    async def resolve_play_request(self, query: str) -> Optional[Dict]:
        """Resolve play request from query string."""
        if await self.exists(query):
            video_id = self._extract_video_id(query)
            if video_id:
                details = await self.track_details(video_id)
                if details:
                    return {"video_id": video_id, **details}
            return None

        results = await self.search(query, limit=1)
        if results:
            video_id = results[0].get("id") or results[0].get("video_id") or results[0].get("vidid")
            if not video_id and "link" in results[0]:
                video_id = self._extract_video_id(results[0]["link"])
            
            if video_id:
                details = await self.track_details(video_id)
                if details:
                    return {"video_id": video_id, **details}
        return None

    async def details(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        video_id = self._extract_video_id(link) if not videoid else link
        det = await self.track_details(video_id)
        if det:
            dur_sec = time_to_seconds(det["duration"])
            return det["title"], det["duration"], dur_sec, det["thumb"], det["vidid"]
        return None

    async def title(self, link: str, videoid: Union[bool, str] = None):
        if videoid: link = self.base + link
        det = await self.track_details(self._extract_video_id(link) or link)
        return det["title"] if det else None

    async def duration(self, link: str, videoid: Union[bool, str] = None):
        if videoid: link = self.base + link
        det = await self.track_details(self._extract_video_id(link) or link)
        return det["duration"] if det else None

    async def thumbnail(self, link: str, videoid: Union[bool, str] = None):
        if videoid: link = self.base + link
        det = await self.track_details(self._extract_video_id(link) or link)
        return det["thumb"] if det else None

    async def video(self, link: str, videoid: Union[bool, str] = None):
        video_id = link if videoid else self._extract_video_id(link)
        if not video_id: return 0, "No ID"
        path = await self._download_video(video_id)
        return (1, path) if path else (0, "Failed")

    async def playlist(self, link, limit, videoid: Union[bool, str] = None):
        if videoid: link = self.listbase + link
        try:
            plist = await Playlist.get(link)
            videos = plist.get("videos") or []
            return [v.get("id") for v in videos[:limit] if v.get("id")]
        except:
            return []

    async def track(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            video_id = link
            link = self.base + link
        else:
            video_id = self._extract_video_id(link)
        det = await self.track_details(video_id)
        return det, video_id

    async def slider(self, query: str, query_type: int):
        results = await self.search(query, limit=10)
        if results and len(results) > query_type:
            res = results[query_type]
            video_id = res.get("id") or res.get("video_id") or res.get("vidid")
            if not video_id and "link" in res:
                video_id = self._extract_video_id(res["link"])
            
            title = res.get("title", "Unknown")
            duration = res.get("duration") or res.get("duration_string")
            thumbnail = res.get("thumbnail") or res.get("thumb")
            if isinstance(thumbnail, list): thumbnail = thumbnail[0].get("url")
            return title, duration, thumbnail, video_id
        return None, None, None, None

    async def download(self, link: str, mystic=None, video: bool = False, videoid: bool = False, **kwargs):
        video_id = link if videoid else self._extract_video_id(link)
        if not video_id: return None, None
        path = await self._download_video(video_id) if video else await self.get_stream(video_id)
        return (path, True) if path else (None, None)

    async def formats(self, link: str, videoid: Union[bool, str] = None):
        return [], link

YouTube = YouTubeAPI()
