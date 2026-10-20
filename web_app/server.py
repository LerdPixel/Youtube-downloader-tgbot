import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
from collections import OrderedDict
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.background import BackgroundTask
from yt_dlp.version import __version__
from pydantic import BaseModel, Field

from .jobs import JobManager
from .options import ROOT, Source, DownloadSettings, runtime_options, readable_error, is_playlist_url, canonical_playlist_url, MAX_PLAYLIST_ITEMS
from .size_estimate import estimate_size
from .playlists import normalize_playlist

STATIC = Path(__file__).parent / "static"


class EstimateRequest(BaseModel):
    analysis_id: str = Field(max_length=36)
    settings: DownloadSettings


class PlaylistDownloadRequest(EstimateRequest):
    entry_indices: list[int] | None = Field(default=None, max_length=MAX_PLAYLIST_ITEMS)


def inspect_video(source: Source) -> dict:
    command = [sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-playlist",
               "--skip-download", "--dump-single-json", "--no-warnings", "--no-colors",
               "--socket-timeout", "20", "--retries", "2"]
    if is_playlist_url(source.url):
        command.remove("--no-playlist")
        command += ["--yes-playlist", "--flat-playlist", "--playlist-end", str(MAX_PLAYLIST_ITEMS + 1)]
    for runtime in runtime_options():
        command += ["--js-runtimes", runtime]
    if source.cookies:
        command += ["--cookies", source.cookies]
    if source.cookies_browser:
        command += ["--cookies-from-browser", source.cookies_browser]
    if source.proxy:
        command += ["--proxy", source.proxy]
    try:
        target_url = canonical_playlist_url(source.url) if is_playlist_url(source.url) else source.url
        result = subprocess.run(command + ["--", target_url], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=110)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("YouTube не ответил вовремя. Повторите анализ или укажите прокси.") from exc
    if result.returncode:
        error = result.stderr.strip()[-3000:] or "Не удалось получить информацию о видео."
        if source.proxy:
            error = error.replace(source.proxy, "[proxy]")
        raise ValueError(readable_error(error))
    info = json.loads(result.stdout)
    if info.get("_type") in {"playlist", "multi_video"}:
        return normalize_playlist(info)
    if info.get("is_live") or info.get("live_status") == "is_upcoming":
        raise ValueError("Выберите завершённое видео: текущие и запланированные эфиры не поддерживаются.")
    formats = []
    for item in info.get("formats", []):
        if item.get("has_drm") or item.get("vcodec") == "none" and item.get("acodec") == "none":
            continue
        formats.append({key: item.get(key) for key in (
            "format_id", "ext", "width", "height", "fps", "vcodec", "acodec", "abr", "tbr",
            "filesize", "filesize_approx", "language", "format_note", "audio_channels", "dynamic_range", "asr", "vbr", "protocol",
        )})
    subtitles: dict[str, dict] = {}
    for kind, key in (("manual", "subtitles"), ("auto", "automatic_captions")):
        for language, tracks in info.get(key, {}).items():
            if language == "live_chat":
                continue
            record = subtitles.setdefault(language, dict(code=language, name=language, manual=False, auto=False))
            record[kind] = True
            if tracks:
                record["name"] = tracks[0].get("name") or language
    thumbnail = info.get("thumbnail") or ""
    if urlparse(thumbnail).scheme not in {"https", "http"}:
        thumbnail = ""
    return dict(kind="video", id=info.get("id"), title=info.get("title"), channel=info.get("channel") or info.get("uploader"),
                duration=info.get("duration"), thumbnail=thumbnail, webpage_url=info.get("webpage_url"),
                formats=formats, resolutions=sorted({f["height"] for f in formats if f["height"]}, reverse=True),
                audio_languages=sorted({f["language"] for f in formats if f["language"] and f["acodec"] != "none"}),
                subtitles=sorted(subtitles.values(), key=lambda item: item["code"]),
                chapters=[{key: chapter.get(key) for key in ("title", "start_time", "end_time")}
                          for chapter in info.get("chapters") or []])


def create_app(download_dir: Path | None = None, save_dir: Path | None = None) -> FastAPI:
    root = (download_dir or ROOT / "downloads-web").resolve()
    destination = (save_dir or Path.home() / "Downloads").expanduser().resolve()

    @asynccontextmanager
    async def lifespan(app):
        app.state.jobs = JobManager(root, destination)
        app.state.analysis_slots = asyncio.Semaphore(2)
        app.state.analyses = OrderedDict()
        yield
        await asyncio.to_thread(app.state.jobs.close)

    app = FastAPI(title="YouTube Local", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Откройте приложение через его локальный адрес."}, status_code=403)
        if request.method == "POST":
            if not request.headers.get("content-type", "").startswith("application/json"):
                return JSONResponse({"detail": "Ожидается JSON."}, status_code=415)
            try:
                limit = 131072 if request.url.path == "/api/playlists/download" else 16384
                if int(request.headers.get("content-length", "0")) > limit:
                    return JSONResponse({"detail": "Слишком большой запрос."}, status_code=413)
            except ValueError:
                return JSONResponse({"detail": "Некорректный запрос."}, status_code=400)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' https: data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        messages = [f"{'.'.join(str(part) for part in item['loc'][1:])}: {item['msg'].removeprefix('Value error, ')}"
                    for item in exc.errors()]
        return JSONResponse({"detail": "\n".join(messages)}, status_code=422)

    @app.get("/api/health")
    async def health():
        return dict(version=__version__, ffmpeg=bool(shutil.which("ffmpeg")), ffprobe=bool(shutil.which("ffprobe")),
                    runtimes=list(runtime_options()), download_dir=str(destination), cache_dir=str(root),
                    features=dict(playlists=True, size_estimates=True),
                    cookies_available=(ROOT / "yt_cookies.txt").is_file())

    @app.post("/api/analyze")
    async def analyze(source: Source, request: Request):
        async with request.app.state.analysis_slots:
            try:
                info = await asyncio.to_thread(inspect_video, source)
                analysis_id = str(uuid.uuid4())
                cache = request.app.state.analyses
                cache[analysis_id] = (time.monotonic(), source.url, info)
                while len(cache) > 20:
                    cache.popitem(last=False)
                return dict(info, analysis_id=analysis_id)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc

    @app.post("/api/estimate")
    async def estimate(data: EstimateRequest, request: Request):
        cached = request.app.state.analyses.get(data.analysis_id)
        if not cached or time.monotonic() - cached[0] > 3600 or cached[1] != data.settings.url:
            raise HTTPException(400, "Повторите анализ ссылки, чтобы рассчитать размер.")
        if cached[2].get("kind") == "playlist":
            return dict(size_bytes=None, approximate=True, format_ids=[],
                        note="У плейлиста пока известен только список видео. Размер зависит от потоков каждого ролика; итоговый объём появится по мере скачивания.")
        return await asyncio.to_thread(estimate_size, cached[2], data.settings)

    @app.post("/api/playlists/download", status_code=201)
    async def download_playlist(data: PlaylistDownloadRequest, request: Request):
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise HTTPException(400, "Установите ffmpeg и ffprobe, затем перезапустите приложение.")
        cached = request.app.state.analyses.get(data.analysis_id)
        if not cached or time.monotonic() - cached[0] > 3600 or cached[1] != data.settings.url or cached[2].get("kind") != "playlist":
            raise HTTPException(400, "Сначала разберите ссылку на плейлист.")
        playlist = cached[2]
        wanted = set(data.entry_indices) if data.entry_indices is not None else None
        if wanted is not None and not wanted.issubset({item["index"] for item in playlist["entries"]}):
            raise HTTPException(400, "В списке выбора есть неизвестные видео.")
        entries = [item for item in playlist["entries"] if item["available"] and (wanted is None or item["index"] in wanted)]
        if not entries:
            raise HTTPException(400, "Выберите хотя бы одно доступное видео.")
        batch_id = str(uuid.uuid4())
        items = []
        for entry in entries:
            filename = f"{entry['index']:03d} - {data.settings.filename or entry['title']}"[:120]
            settings = data.settings.model_copy(update=dict(url=entry["url"], filename=filename))
            items.append((settings, dict(title=entry["title"], batch_id=batch_id, playlist_title=playlist["title"],
                                         playlist_index=entry["index"], playlist_count=len(entries))))
        try:
            result = await asyncio.to_thread(request.app.state.jobs.create_many, items)
            return dict(batch_id=batch_id, count=len(result), skipped=playlist["entry_count"] - len(entries))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/batches/{batch_id}/cancel")
    async def cancel_batch(batch_id: str, request: Request):
        return await asyncio.to_thread(request.app.state.jobs.cancel_batch, batch_id)

    @app.get("/api/jobs")
    async def jobs(request: Request):
        return request.app.state.jobs.list()

    @app.post("/api/jobs", status_code=201)
    async def download(settings: DownloadSettings, request: Request):
        if is_playlist_url(settings.url):
            raise HTTPException(400, "Сначала разберите плейлист, затем нажмите «Скачать весь плейлист».")
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise HTTPException(400, "Установите ffmpeg и ffprobe, затем перезапустите приложение.")
        try:
            return request.app.state.jobs.create(settings)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel(job_id: str, request: Request):
        try:
            return request.app.state.jobs.cancel(job_id)
        except KeyError as exc:
            raise HTTPException(404, "Загрузка не найдена.") from exc

    @app.get("/api/jobs/{job_id}/files/{name}")
    async def file(job_id: str, name: str, request: Request):
        try:
            path = request.app.state.jobs.file(job_id, name)
            return FileResponse(path, filename=path.name, media_type="application/octet-stream")
        except (KeyError, FileNotFoundError) as exc:
            raise HTTPException(404, "Файл не найден.") from exc

    @app.get("/api/jobs/{job_id}/bundle")
    async def bundle(job_id: str, request: Request):
        try:
            path = await asyncio.to_thread(request.app.state.jobs.bundle, job_id)
            return FileResponse(path, filename=f"youtube-{job_id[:8]}.zip", media_type="application/zip",
                                background=BackgroundTask(path.unlink, missing_ok=True))
        except (KeyError, FileNotFoundError) as exc:
            raise HTTPException(404, "Файлы ещё не готовы.") from exc

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
