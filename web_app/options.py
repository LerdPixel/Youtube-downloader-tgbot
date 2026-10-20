"""Validated UI settings and a shell-free yt-dlp command builder."""

from pathlib import Path
import re
import shutil
import sys
from typing import Literal
from urllib.parse import parse_qs, urlencode, urlparse

from pydantic import BaseModel, Field, field_validator, model_validator

ROOT = Path(__file__).resolve().parent.parent
MAX_PLAYLIST_ITEMS = 10000


def is_playlist_url(url: str) -> bool:
    return bool(parse_qs(urlparse(url).query).get("list", [""])[0])


def canonical_playlist_url(url: str) -> str:
    """YouTube and YouTube Music share playlist IDs; use one extractor entry point."""
    playlist_id = parse_qs(urlparse(url).query).get("list", [""])[0]
    return "https://www.youtube.com/playlist?" + urlencode({"list": playlist_id})


class Source(BaseModel):
    url: str = Field(max_length=2048)
    cookies: str = Field(default="", max_length=4096)
    cookies_browser: Literal["", "chrome", "chromium", "firefox", "safari", "edge", "brave", "opera", "vivaldi"] = ""
    proxy: str = Field(default="", max_length=2048)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        value = value.strip()
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or not (
            host in {"youtu.be", "youtube.com", "youtube-nocookie.com"}
            or host.endswith(".youtube.com")
            or host.endswith(".youtube-nocookie.com")
        ) or parsed.username or parsed.password:
            raise ValueError("Вставьте полную ссылку на видео или плейлист YouTube.")
        path = parsed.path.rstrip("/")
        is_video = (bool(re.fullmatch(r"/[\w-]+", path)) if host == "youtu.be" else
                    path == "/watch" and bool(re.fullmatch(r"[\w-]+", parse_qs(parsed.query).get("v", [""])[0]))
                    or bool(re.fullmatch(r"/(?:shorts|live|embed|v|clip)/[\w-]+", path)))
        playlist_id = parse_qs(parsed.query).get("list", [""])[0]
        if path == "/playlist" and not playlist_id:
            raise ValueError("В ссылке отсутствует идентификатор после list=. Скопируйте полный адрес плейлиста.")
        is_playlist = path in {"/playlist", "/watch"} and bool(re.fullmatch(r"[\w-]+", playlist_id))
        if playlist_id and not re.fullmatch(r"[\w-]+", playlist_id):
            raise ValueError("Некорректный идентификатор плейлиста.")
        if not is_video and not is_playlist:
            raise ValueError("Нужна ссылка на видео или плейлист, а не на канал.")
        return value

    @field_validator("proxy")
    @classmethod
    def validate_proxy(cls, value: str) -> str:
        value = value.strip()
        if value and (urlparse(value).scheme not in {"http", "https", "socks4", "socks5", "socks5h"}
                      or not urlparse(value).hostname):
            raise ValueError("Прокси должен начинаться с http://, https:// или socks5://.")
        return value

    @field_validator("cookies")
    @classmethod
    def validate_cookies(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return ""
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = ROOT / path
        if not path.is_file():
            raise ValueError("Файл cookies не найден. Укажите путь к файлу Netscape cookies.txt.")
        return str(path.resolve())

    @model_validator(mode="after")
    def single_cookie_source(self):
        if self.cookies and self.cookies_browser:
            raise ValueError("Выберите один источник cookies: файл или браузер.")
        return self


class DownloadSettings(Source):
    mode: Literal["video", "audio", "silent"] = "video"
    container: Literal["auto", "mp4", "mkv", "webm"] = "mp4"
    resolution: int = Field(default=1080, ge=0, le=16384)
    fps: int = Field(default=0, ge=0, le=240)
    video_codec: Literal["auto", "h264", "vp9", "av1"] = "auto"
    audio_format: Literal["best", "mp3", "m4a", "aac", "opus", "vorbis", "flac", "wav", "alac"] = "mp3"
    audio_quality: Literal["0", "64", "96", "128", "192", "256", "320"] = "192"
    audio_language: str = Field(default="", max_length=50)
    thumbnail: Literal["none", "file", "embed", "both"] = "none"
    metadata: bool = True
    chapters: bool = True
    description: bool = False
    info_json: bool = False
    subtitles: list[str] = Field(default_factory=list, max_length=20)
    auto_subtitles: bool = False
    subtitle_format: Literal["srt", "vtt"] = "srt"
    embed_subtitles: bool = False
    start: str = Field(default="", max_length=20)
    end: str = Field(default="", max_length=20)
    precise_cuts: bool = False
    rate_limit: int = Field(default=0, ge=0, le=102400)
    fragments: int = Field(default=4, ge=1, le=16)
    format_selector: str = Field(default="", max_length=500)
    filename: str = Field(default="", max_length=120)

    @field_validator("subtitles")
    @classmethod
    def validate_languages(cls, values: list[str]) -> list[str]:
        if any(not re.fullmatch(r"[\w.-]+", value) for value in values):
            raise ValueError("Некорректный код языка субтитров.")
        return list(dict.fromkeys(values))

    @field_validator("audio_language")
    @classmethod
    def validate_audio_language(cls, value: str) -> str:
        if value and not re.fullmatch(r"[\w.-]+", value):
            raise ValueError("Некорректный код языка аудио.")
        return value

    @field_validator("start", "end")
    @classmethod
    def validate_time(cls, value: str) -> str:
        value = value.strip()
        if value:
            seconds(value)
        return value

    @field_validator("format_selector")
    @classmethod
    def validate_selector(cls, value: str) -> str:
        value = value.strip()
        if value and ("\n" in value or "\r" in value or "\x00" in value or value.startswith("-")):
            raise ValueError("Некорректное выражение выбора потоков.")
        return value

    @model_validator(mode="after")
    def compatible_options(self):
        if self.end and seconds(self.end) <= seconds(self.start or "0"):
            raise ValueError("Конец фрагмента должен быть позже начала.")
        if self.mode != "audio" and self.container == "webm" and self.video_codec == "h264":
            raise ValueError("H.264 несовместим с WebM. Выберите MP4 или MKV.")
        if self.thumbnail in {"embed", "both"}:
            if self.mode == "audio" and self.audio_format not in {"mp3", "m4a", "flac", "opus", "vorbis", "alac"}:
                raise ValueError("Для встроенной обложки выберите MP3, M4A, FLAC, Opus, Vorbis или ALAC.")
            if self.mode != "audio" and self.container not in {"mp4", "mkv"}:
                raise ValueError("Для встроенной обложки выберите контейнер MP4 или MKV.")
        if self.embed_subtitles and (self.mode == "audio" or not self.subtitles):
            raise ValueError("Для встраивания субтитров выберите видео и хотя бы один язык.")
        if self.embed_subtitles and self.container == "webm" and self.subtitle_format != "vtt":
            raise ValueError("Для субтитров в WebM выберите WebVTT.")
        return self


def seconds(value: str) -> float:
    if not re.fullmatch(r"\d+(?::[0-5]?\d){0,2}(?:\.\d+)?", value):
        raise ValueError("Время: секунды, ММ:СС или ЧЧ:ММ:СС (например 01:30).")
    total = 0.0
    for part in value.split(":"):
        total = total * 60 + float(part)
    return total


def runtime_options() -> dict:
    return {name: {} for name in ("deno", "node", "bun") if shutil.which(name)}


def readable_error(message: str) -> str:
    if "Sign in to confirm" in message:
        return ("YouTube просит подтвердить вход. Откройте «Cookies и прокси», выберите браузер, "
                "в котором выполнен вход в YouTube, либо укажите файл cookies.txt. "
                "Затем повторите анализ. Если проверка остаётся, попробуйте прокси.")
    if "Requested format is not available" in message:
        return ("Выбранное сочетание потоков недоступно. Разберите ссылку и проверьте таблицу потоков. "
                "Попробуйте MKV, лучший доступный кодек или другое разрешение и язык аудио.")
    if "This video is unavailable" in message:
        return "Это видео недоступно на YouTube. Проверьте ссылку и доступ к видео в браузере."
    return message


def select_format(settings: DownloadSettings) -> str:
    if settings.format_selector:
        return settings.format_selector
    language = f"[language={settings.audio_language}]" if settings.audio_language else ""
    audio_filter = "[ext=m4a]" if settings.container == "mp4" else "[ext=webm]" if settings.container == "webm" else ""
    audio = f"ba{language}{audio_filter}"
    if settings.mode == "audio":
        return f"ba{language}/b{language}"
    filters = ""
    if settings.resolution:
        filters += f"[height<=?{settings.resolution}]"
    if settings.fps:
        filters += f"[fps<=?{settings.fps}]"
    codec = {"h264": "avc", "vp9": "vp", "av1": "av01"}.get(settings.video_codec)
    if codec:
        filters += f"[vcodec^={codec}]"
    if settings.container == "mp4":
        filters += "[ext=mp4]"
    elif settings.container == "webm":
        filters += "[ext=webm]"
    if settings.mode == "silent":
        # Strict video-only selection: no accidental soundtrack on progressive streams.
        return f"bv{filters}"
    return f"bv{filters}+{audio}/b{filters}{language}"


def build_command(settings: DownloadSettings, output: Path) -> list[str]:
    command = [
        sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-playlist",
        "--no-colors", "--newline", "--progress", "--no-simulate",
        "--match-filters", "!is_live & live_status!=?is_upcoming", "--break-on-reject",
        "--windows-filenames", "--trim-filenames", "180", "--retries", "5",
        "--fragment-retries", "5", "--socket-timeout", "30", "--no-mtime",
        "--paths", str(output), "--format", select_format(settings),
        "--concurrent-fragments", str(settings.fragments),
        "--progress-delta", "0.5",
        "--progress-template", 'download:{"type":"progress","downloaded":%(progress.downloaded_bytes)j,"total":%(progress.total_bytes)j,"estimate":%(progress.total_bytes_estimate)j,"speed":%(progress.speed)j,"eta":%(progress.eta)j,"status":%(progress.status)j,"format_id":%(info.format_id)j}',
        "--progress-template", 'postprocess:{"type":"processing","name":%(progress.postprocessor)j}',
        "--print", 'before_dl:{"type":"title","title":%(title)j}',
        "--print", 'after_move:{"type":"file","path":%(filepath)j,"title":%(title)j}',
    ]
    # User text is always a literal filename, never a yt-dlp template or shell command.
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", settings.filename).strip(" .")
    name = name.replace("%", "%%")
    template = f"{name}.%(ext)s" if name else "%(title).140B [%(id)s].%(ext)s"
    command += ["--output", template]
    for runtime in runtime_options():
        command += ["--js-runtimes", runtime]
    if settings.cookies:
        command += ["--cookies", settings.cookies]
    if settings.cookies_browser:
        command += ["--cookies-from-browser", settings.cookies_browser]
    if settings.proxy:
        command += ["--proxy", settings.proxy]
    if settings.mode == "audio":
        command += ["--extract-audio", "--audio-format", settings.audio_format,
                    "--audio-quality", settings.audio_quality if settings.audio_quality == "0" else f"{settings.audio_quality}K"]
    elif settings.container != "auto":
        command += ["--merge-output-format", settings.container, "--remux-video", settings.container]
    if settings.thumbnail != "none":
        command += ["--convert-thumbnails", "jpg"]
        if settings.thumbnail in {"file", "both"}:
            command += ["--write-thumbnail"]
        if settings.thumbnail in {"embed", "both"}:
            command += ["--embed-thumbnail"]
    if settings.metadata:
        command += ["--embed-metadata", "--no-embed-info-json"]
    command += ["--embed-chapters" if settings.chapters else "--no-embed-chapters"]
    if settings.description:
        command += ["--write-description"]
    if settings.info_json:
        command += ["--write-info-json"]
    if settings.subtitles:
        command += ["--write-subs", "--sub-langs", ",".join(f"^{re.escape(lang)}$" for lang in settings.subtitles),
                    "--sub-format", "vtt/best", "--convert-subs", settings.subtitle_format]
        if settings.auto_subtitles:
            command += ["--write-auto-subs"]
        if settings.embed_subtitles:
            command += ["--embed-subs"]
    if settings.start or settings.end:
        command += ["--download-sections", f"*{seconds(settings.start or '0')}-{seconds(settings.end) if settings.end else 'inf'}"]
        if settings.precise_cuts:
            command += ["--force-keyframes-at-cuts"]
    if settings.rate_limit:
        command += ["--limit-rate", f"{settings.rate_limit}K"]
    return command + ["--", settings.url]
