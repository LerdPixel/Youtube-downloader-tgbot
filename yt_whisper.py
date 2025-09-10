import yt_dlp
#import whisper
import logging
import requests
from io import BytesIO

def shorter_than_ten_minutes(info, *, incomplete):
    """Download only videos shorter than 10 minutes"""
    duration = info.get('duration')
    acceptable_dur = 600
    if duration and duration > acceptable_dur:
        raise Exception(f'The video is longer than {acceptable_dur} seconds')
        return 'The video is too long'


def download_audio(yt_url, output_filename="audio.m4a"):
    # Options for yt-dlp to download the best audio only
    ydl_opts = {
        "cookiefile": "yt_cookies.txt",         # You should get your youtube cookie file using "Get cookies.txt LOCALLY" addon
        'format': 'bestaudio[filesize<49M]/bestaudio/worst',
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(yt_url, download=False)
        direct_url = info['url']
        title = info.get("title", "audio")
        ext = info.get("ext", "opus")  # like webm, m4a, opus, etc.
    # Stream into memory
    buffer = BytesIO()
    headers = {'User-Agent': 'Mozilla/5.0'}
    response = requests.get(direct_url, headers=headers, stream=True)

    max_size = 49 * 1024 * 1024  # Telegram document limit is 50MB
    total = 0
    for chunk in response.iter_content(chunk_size=1024 * 1024):
        if chunk:
            total += len(chunk)
            if total > max_size:
                raise Exception("Audio is too large to send via Telegram bot (max 50MB).")
                return
            buffer.write(chunk)

    if total == 0:
        raise Exception("Download failed: audio is empty.")
        return
    return buffer, title, ext



def download_video(video_url):
    buffer = BytesIO()

    ydl_opts = {
        'match_filter' : shorter_than_ten_minutes,
        'format': 'best[ext=mp4][filesize<49M]/best[filesize<49M]/worst',
        'noplaylist': True,
        'quiet': True,
        'merge_output_format': 'mp4',
        'outtmpl': '-',  # output to stdout (not really used here)
        'retries': 3,
        'no_warnings': True,
        'logger': logging.getLogger("yt_dlp"),
        "cookiefile": "yt_cookies.txt",         # You should get your youtube cookie file using "Get cookies.txt LOCALLY" addon
        'outtmpl': {
            'default': '-',  # dummy, not saving to file
        },
        'force_overwrites': True,
        'buffer': buffer,  # not a real yt_dlp option, just a placeholder
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
        direct_url = info['url']
        title = info.get("title", "video")

    buffer = BytesIO()
    headers = {'User-Agent': 'Mozilla/5.0'}
    response = requests.get(direct_url, headers=headers, stream=True)

    max_size = 49 * 1024 * 1024  # Telegram's send_video limit is 50MB
    total = 0
    for chunk in response.iter_content(chunk_size=1024 * 1024):
        if chunk:
            total += len(chunk)
            if total > max_size:
                raise Exception("Video too large to send via Telegram bot (max 50MB).")
                return
            buffer.write(chunk)

    if total == 0:
        raise Exception("download failed file is empty")
        return
    return buffer, title
"""
def transcribe_audio(input_filename, output_filename):
    model = whisper.load_model("turbo")
    result = model.transcribe(input_filename)
    with open(output_filename, "w") as text_file:
        text_file.write(result["text"])
"""

