import yt_dlp
#import whisper
import logging
import requests
from io import BytesIO
import os
import tempfile
from yt_dlp.utils import DownloadError


def _cookie_opts() -> dict:
    cookiefile = "yt_cookies.txt"
    if os.path.isfile(cookiefile):
        return {"cookiefile": cookiefile}
    return {}


def shorter_than_ten_minutes(info, *, incomplete):
    """Download only videos shorter than acceptable_dur seconds"""
    duration = info.get('duration')
    acceptable_dur = 1000 * 60
    if duration and duration > acceptable_dur:
        raise Exception(f'The video is longer than {acceptable_dur} seconds')
        return 'The video is too long'

def _stream_url_to_buffer(direct_url: str, *, max_size: int, user_agent: str = "Mozilla/5.0") -> BytesIO:
    buffer = BytesIO()
    headers = {"User-Agent": user_agent}
    response = requests.get(direct_url, headers=headers, stream=True)

    total = 0
    for chunk in response.iter_content(chunk_size=1024 * 1024):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_size:
            raise Exception("File is too large to send via Telegram bot (max 50MB).")
        buffer.write(chunk)

    if total == 0:
        raise Exception("Download failed: file is empty.")

    return buffer

def _ytdlp_download_to_buffer(url: str, *, ydl_opts: dict, max_size: int) -> tuple[BytesIO, str, str]:
    """
    Download with yt-dlp to a temp file (handles DASH + merge), then read into memory.
    Returns (buffer, title, ext).
    """
    with tempfile.TemporaryDirectory(prefix="ytdlp_") as tmpdir:
        # Force output into tmpdir, stable name
        opts = dict(ydl_opts)
        opts.update(
            {
                "paths": {"home": tmpdir},
                "outtmpl": os.path.join(tmpdir, "download.%(id)s.%(ext)s"),
                "quiet": True,
                "no_warnings": True,
            }
        )
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "download")

            # Best-effort resolve resulting file path
            filepath = info.get("_filename") or ydl.prepare_filename(info)
            if not filepath or not os.path.exists(filepath):
                # fallback: pick the newest non-part file in tmpdir
                candidates = [
                    os.path.join(tmpdir, f)
                    for f in os.listdir(tmpdir)
                    if os.path.isfile(os.path.join(tmpdir, f)) and not f.endswith(".part")
                ]
                if not candidates:
                    raise Exception("yt-dlp download failed: output file not found.")
                filepath = max(candidates, key=os.path.getmtime)

            ext = os.path.splitext(filepath)[1].lstrip(".") or info.get("ext", "bin")
            size = os.path.getsize(filepath)
            if size > max_size:
                raise Exception("File is too large to send via Telegram bot (max 50MB).")

            with open(filepath, "rb") as f:
                buffer = BytesIO(f.read())

        return buffer, title, ext


def download_audio(yt_url, output_filename="audio.m4a"):
    # Options for yt-dlp to download the best audio only
    ydl_opts = {
        **_cookie_opts(),
        # NOTE: don't filter by filesize here — YouTube often doesn't provide it, causing "Requested format is not available".
        # Size is enforced during streaming below.
        # Try to get a direct audio-only URL first (single stream).
        'format': 'ba[ext=m4a]/ba/bestaudio/best',
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
    }
    max_size = 49 * 1024 * 1024  # Telegram document limit is 50MB
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(yt_url, download=False)
            direct_url = info["url"]
            title = info.get("title", "audio")
            ext = info.get("ext", "audio")

        buffer = _stream_url_to_buffer(direct_url, max_size=max_size)
        return buffer, title, ext
    except DownloadError:
        # Fallback: download via yt-dlp (more reliable for edge cases)
        buffer, title, ext = _ytdlp_download_to_buffer(yt_url, ydl_opts=ydl_opts, max_size=max_size)
        return buffer, title, ext



def download_video(video_url):
    ydl_opts = {
        'match_filter' : shorter_than_ten_minutes,
        # 1) First try a single progressive stream (audio+video), so `info["url"]` is directly downloadable.
        # 2) If not available, fallback below will use yt-dlp download (DASH + merge).
        'format': 'b[ext=mp4]/b/best',
        'noplaylist': True,
        'quiet': True,
        'retries': 3,
        'no_warnings': True,
        'logger': logging.getLogger("yt_dlp"),
        **_cookie_opts(),
        'force_overwrites': True,
    }

    max_size = 49 * 1024 * 1024  # Telegram's send_video limit is 50MB
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            direct_url = info["url"]
            title = info.get("title", "video")
            ext = info.get("ext", "mp4")

        buffer = _stream_url_to_buffer(direct_url, max_size=max_size)
        return buffer, title, ext
    except DownloadError:
        # Fallback path: allow DASH (separate audio/video) and let yt-dlp merge.
        # This requires ffmpeg to be installed in the environment.
        merge_opts = dict(ydl_opts)
        merge_opts.update(
            {
                "format": "bv*+ba/best",
                "merge_output_format": "mp4",
            }
        )
        buffer, title, ext = _ytdlp_download_to_buffer(video_url, ydl_opts=merge_opts, max_size=max_size)
        return buffer, title, ext
"""
def transcribe_audio(input_filename, output_filename):
    model = whisper.load_model("turbo")
    result = model.transcribe(input_filename)
    with open(output_filename, "w") as text_file:
        text_file.write(result["text"])
"""

