import yt_dlp
#import whisper
import logging
import requests
from io import BytesIO
import os
import tempfile
from collections.abc import Callable
from yt_dlp.utils import DownloadError

ProgressCallback = Callable[[int, int | None, str], None]


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

def _make_progress_hook(progress_callback: ProgressCallback | None):
    def hook(status: dict) -> None:
        if not progress_callback:
            return
        if status["status"] == "downloading":
            progress_callback(
                status.get("downloaded_bytes") or 0,
                status.get("total_bytes") or status.get("total_bytes_estimate"),
                "Downloading",
            )
        elif status["status"] == "finished":
            progress_callback(
                status.get("downloaded_bytes") or 0,
                status.get("total_bytes") or status.get("downloaded_bytes"),
                "Processing",
            )

    return hook


def _stream_url_to_buffer(
    direct_url: str,
    *,
    max_size: int,
    progress_callback: ProgressCallback | None = None,
    user_agent: str = "Mozilla/5.0",
) -> BytesIO:
    buffer = BytesIO()
    headers = {"User-Agent": user_agent}
    response = requests.get(direct_url, headers=headers, stream=True)
    response.raise_for_status()

    content_length = response.headers.get("content-length")
    total = int(content_length) if content_length else None
    downloaded = 0

    for chunk in response.iter_content(chunk_size=1024 * 1024):
        if not chunk:
            continue
        downloaded += len(chunk)
        if downloaded > max_size:
            raise Exception("File is too large to send via Telegram bot (max 50MB).")
        buffer.write(chunk)
        if progress_callback:
            progress_callback(downloaded, total, "Downloading")

    if downloaded == 0:
        raise Exception("Download failed: file is empty.")

    return buffer

def _ytdlp_download_to_buffer(
    url: str,
    *,
    ydl_opts: dict,
    max_size: int,
    progress_callback: ProgressCallback | None = None,
) -> tuple[BytesIO, str, str]:
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
                "progress_hooks": [_make_progress_hook(progress_callback)],
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


def download_audio(yt_url, output_filename="audio.m4a", *, progress_callback: ProgressCallback | None = None):
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
    if progress_callback:
        progress_callback(0, None, "Fetching info")
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(yt_url, download=False)
            direct_url = info["url"]
            title = info.get("title", "audio")
            ext = info.get("ext", "audio")

        buffer = _stream_url_to_buffer(
            direct_url,
            max_size=max_size,
            progress_callback=progress_callback,
        )
        return buffer, title, ext
    except DownloadError:
        # Fallback: download via yt-dlp (more reliable for edge cases)
        buffer, title, ext = _ytdlp_download_to_buffer(
            yt_url,
            ydl_opts=ydl_opts,
            max_size=max_size,
            progress_callback=progress_callback,
        )
        return buffer, title, ext



def download_video(video_url, *, progress_callback: ProgressCallback | None = None):
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
    if progress_callback:
        progress_callback(0, None, "Fetching info")
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            direct_url = info["url"]
            title = info.get("title", "video")
            ext = info.get("ext", "mp4")

        buffer = _stream_url_to_buffer(
            direct_url,
            max_size=max_size,
            progress_callback=progress_callback,
        )
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
        buffer, title, ext = _ytdlp_download_to_buffer(
            video_url,
            ydl_opts=merge_opts,
            max_size=max_size,
            progress_callback=progress_callback,
        )
        return buffer, title, ext
"""
def transcribe_audio(input_filename, output_filename):
    model = whisper.load_model("turbo")
    result = model.transcribe(input_filename)
    with open(output_filename, "w") as text_file:
        text_file.write(result["text"])
"""

