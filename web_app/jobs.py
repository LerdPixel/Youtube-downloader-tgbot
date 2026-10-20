"""Disk-backed queue. Each download runs in its own cancellable process group."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import threading
import uuid
import zipfile

from .options import DownloadSettings, build_command, readable_error, MAX_PLAYLIST_ITEMS, is_playlist_url

ACTIVE = {"queued", "downloading", "processing"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except ProcessLookupError:
        pass


class JobManager:
    def __init__(self, root: Path, save_dir: Path | None = None):
        self.root = root.resolve()
        self.save_dir = (save_dir or Path.home() / "Downloads").expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="download")
        self.processes: dict[str, subprocess.Popen] = {}
        self.jobs: dict[str, dict] = {}
        self.closed = False
        for state in sorted(self.root.glob("*/job.json")):
            try:
                job = json.loads(state.read_text(encoding="utf-8"))
                if str(uuid.UUID(job["id"])) != state.parent.name:
                    continue
                if job["status"] in ACTIVE:
                    job.update(status="interrupted", phase="Загрузка прервана перезапуском", speed=None, eta=None)
                self.jobs[job["id"]] = job
            except (OSError, ValueError, KeyError, TypeError):
                continue
        # Previous versions kept completed media in the cache. Move it on next startup.
        for job in self.jobs.values():
            if job["status"] == "complete" and job.get("files"):
                self.executor.submit(self._migrate, job["id"])

    def _migrate(self, job_id: str) -> None:
        try:
            job = self.get(job_id)
            if job.get("saved_dir"):
                # A previous shutdown may have happened between saving history and cleanup.
                if all(self.file(job_id, item["name"]).stat().st_size == item["size"] for item in job["files"]):
                    self._clean_cache(job_id)
            else:
                self._store_files(job_id, job["files"])
        except (OSError, ValueError) as exc:
            with self.lock:
                job = self.jobs[job_id]
                job["storage_error"] = f"Не удалось перенести файлы в Загрузки: {exc}"
                self._save(job)

    def _copy_unique(self, source: Path) -> Path:
        """Reserve a unique name; keep the cache intact until the whole result is saved."""
        for number in range(10000):
            name = source.name if number == 0 else f"{source.stem} ({number}){source.suffix}"
            target = self.save_dir / name
            try:
                # Same-volume hard links avoid copying gigabytes or requiring twice the space.
                os.link(source, target)
            except FileExistsError:
                continue
            except OSError:
                # Different volumes / filesystems: copy without ever overwriting an existing file.
                try:
                    destination = target.open("xb")
                except FileExistsError:
                    continue
                try:
                    with destination, source.open("rb") as original:
                        shutil.copyfileobj(original, destination, length=1024 * 1024)
                        destination.flush()
                        os.fsync(destination.fileno())
                except BaseException:
                    target.unlink(missing_ok=True)
                    raise
            return target
        raise OSError("Слишком много файлов с одинаковым именем в папке Загрузки.")

    def _store_files(self, job_id: str, files: list[dict]) -> None:
        directory = (self.root / job_id / "files").resolve()
        if self.save_dir == directory or directory in self.save_dir.parents:
            raise ValueError("Папка сохранения не должна находиться внутри временных файлов загрузки.")
        self.save_dir.mkdir(parents=True, exist_ok=True)
        created: list[Path] = []
        saved = []
        try:
            for item in files:
                source = (directory / item["name"]).resolve()
                if source.parent != directory or not source.is_file():
                    raise FileNotFoundError(item["name"])
                target = self._copy_unique(source)
                created.append(target)
                if target.stat().st_size != item["size"]:
                    raise OSError(f"Размер сохранённого файла не совпал: {target.name}")
                saved.append(dict(name=target.name, size=item["size"]))
            with self.lock:
                job = self.jobs[job_id]
                before = dict(job)
                job.update(files=saved, saved_dir=str(self.save_dir), status="complete",
                           phase="Сохранено в Загрузки", percent=100, finished_at=now(), speed=None, eta=None)
                job.pop("storage_error", None)
                try:
                    # Persist the new paths before removing the only remaining cache entries.
                    self._save(job)
                except BaseException:
                    job.clear()
                    job.update(before)
                    raise
        except BaseException:
            for path in created:
                path.unlink(missing_ok=True)
            raise
        self._clean_cache(job_id)

    def _clean_cache(self, job_id: str) -> None:
        with self.lock:
            job = self.jobs[job_id]
            try:
                directory = self.root / job_id / "files"
                if directory.exists():
                    shutil.rmtree(directory)
                (self.root / job_id / "download.zip").unlink(missing_ok=True)
                (self.root / job_id / "download.tmp").unlink(missing_ok=True)
                job.pop("storage_error", None)
            except OSError as exc:
                job["storage_error"] = f"Файлы сохранены, но временная папка не очищена: {exc}"
            self._save(job)

    def _save(self, job: dict) -> None:
        state = self.root / job["id"] / "job.json"
        temporary = state.with_suffix(".tmp")
        temporary.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(state)

    def list(self) -> list[dict]:
        with self.lock:
            return json.loads(json.dumps(sorted(self.jobs.values(), key=lambda job: job["created_at"], reverse=True)))

    def get(self, job_id: str) -> dict:
        with self.lock:
            return json.loads(json.dumps(self.jobs[job_id]))

    def create(self, settings: DownloadSettings) -> dict:
        return self.create_many([(settings, {})])[0]

    def create_many(self, items: list[tuple[DownloadSettings, dict]]) -> list[dict]:
        with self.lock:
            if self.closed:
                raise ValueError("Приложение завершает работу.")
            if not items:
                raise ValueError("Нет видео для скачивания.")
            if len(items) + sum(job["status"] in ACTIVE for job in self.jobs.values()) > MAX_PLAYLIST_ITEMS:
                raise ValueError(f"В очереди может быть до {MAX_PLAYLIST_ITEMS} видео. Дождитесь завершения текущих загрузок.")
            if any(is_playlist_url(settings.url) for settings, _ in items):
                raise ValueError("В очередь можно добавлять только отдельные видео. Сначала разберите плейлист.")
            prepared = []
            try:
                for settings, metadata in items:
                    job_id = str(uuid.uuid4())
                    job = dict(id=job_id, url=settings.url, title=settings.filename or settings.url,
                               mode=settings.mode, preset=settings.audio_format.upper() if settings.mode == "audio" else
                               f"{settings.container.upper()} · {str(settings.resolution) + 'p' if settings.resolution else 'максимум'}",
                               status="queued", phase="В очереди", percent=None, downloaded=0,
                               total=None, speed=None, eta=None, files=[], error=None, warnings=[], created_at=now())
                    job.update({key: value for key, value in metadata.items() if key in {
                        "title", "batch_id", "playlist_title", "playlist_index", "playlist_count"}})
                    prepared.append((settings, job))
                    (self.root / job_id / "files").mkdir(parents=True)
                    self._save(job)
            except Exception:
                # Nothing has started yet: don't leave a partially enqueued playlist.
                for _, job in prepared:
                    shutil.rmtree(self.root / job["id"], ignore_errors=True)
                raise
            for settings, job in prepared:
                self.jobs[job["id"]] = job
                self.executor.submit(self._run, job["id"], settings)
            return [dict(job) for _, job in prepared]

    def cancel_batch(self, batch_id: str) -> dict:
        with self.lock:
            targets = [job["id"] for job in self.jobs.values() if job.get("batch_id") == batch_id
                       and job["status"] in ACTIVE and job["phase"] != "Сохраняем в Загрузки"]
            for job_id in targets:
                self.cancel(job_id)
            return dict(cancelled=len(targets))

    def cancel(self, job_id: str) -> dict:
        with self.lock:
            job = self.jobs[job_id]
            if job["status"] in ACTIVE and job["phase"] != "Сохраняем в Загрузки":
                job.update(status="cancelled", phase="Отменено", speed=None, eta=None)
                self._save(job)
                process = self.processes.get(job_id)
                if process:
                    stop_process(process)
                    # ffmpeg or a network read can ignore SIGTERM; reap without blocking the API.
                    threading.Thread(target=self._reap, args=(process,), daemon=True).start()
            return json.loads(json.dumps(job))

    @staticmethod
    def _reap(process):
        try:
            process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass

    def _run(self, job_id: str, settings: DownloadSettings):
        directory = self.root / job_id / "files"
        errors: list[str] = []
        process = None
        try:
            with self.lock:
                job = self.jobs[job_id]
                if job["status"] == "cancelled":
                    return
                process = subprocess.Popen(
                    build_command(settings, directory), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                    start_new_session=os.name == "posix",
                )
                self.processes[job_id] = process
                job.update(status="downloading", phase="Получаем информацию")
                self._save(job)
            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    if line.startswith(("ERROR:", "WARNING:")):
                        # Avoid persisting proxy credentials in the history.
                        if settings.proxy:
                            line = line.replace(settings.proxy, "[proxy]")
                        errors.append(line[:2000])
                        errors = errors[-12:]
                    continue
                if not isinstance(event, dict):
                    continue
                with self.lock:
                    if job["status"] == "cancelled":
                        continue
                    kind = event.get("type")
                    if kind == "title":
                        job["title"] = event.get("title") or job["title"]
                    elif kind == "progress":
                        total = numeric(event.get("total")) or numeric(event.get("estimate"))
                        downloaded = numeric(event.get("downloaded")) or 0
                        job.update(status="downloading", phase=f"Скачиваем поток {event.get('format_id') or ''}",
                                   downloaded=downloaded, total=total,
                                   percent=min(100, round(downloaded * 100 / total, 1)) if total else None,
                                   speed=numeric(event.get("speed")), eta=numeric(event.get("eta")))
                    elif kind in {"processing", "file"}:
                        job.update(status="processing", phase="Обрабатываем и собираем файлы", speed=None, eta=None)
            process.stdout.close()
            return_code = process.wait()
            if return_code == 0 and settings.mode == "silent" and settings.format_selector:
                # Advanced selectors can include a combined stream. Honor the mode even then.
                for media in directory.iterdir():
                    if media.suffix.lower() not in {".mp4", ".mkv", ".webm", ".mov", ".avi"}:
                        continue
                    temporary = media.with_name(f".silent-{media.stem}{media.suffix}")
                    with self.lock:
                        if job["status"] == "cancelled":
                            return
                        job.update(status="processing", phase="Удаляем звуковую дорожку")
                        process = subprocess.Popen(
                            ["ffmpeg", "-nostdin", "-y", "-i", str(media), "-map", "0:v", "-map", "0:s?",
                             "-map_metadata", "0", "-map_chapters", "0", "-c", "copy", "-an", str(temporary)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                            start_new_session=os.name == "posix",
                        )
                        self.processes[job_id] = process
                    _, diagnostic = process.communicate()
                    with self.lock:
                        if job["status"] == "cancelled":
                            return
                    if process.returncode:
                        raise RuntimeError(f"Не удалось удалить звук: {diagnostic[-1500:]}")
                    temporary.replace(media)
            with self.lock:
                if job["status"] == "cancelled":
                    return
                if return_code:
                    raise RuntimeError("\n".join(line for line in errors if line.startswith("ERROR:"))
                                       or "\n".join(errors[-3:]) or f"yt-dlp завершился с кодом {return_code}.")
                files = [dict(name=path.name, size=path.stat().st_size)
                         for path in sorted(directory.iterdir()) if path.is_file()
                         and not path.name.startswith(".")
                         and not path.name.endswith((".part", ".ytdl", ".temp")) and path.stat().st_size > 0]
                if not files:
                    raise RuntimeError("Загрузчик завершил работу, но не создал файлов.")
                job.update(status="processing", phase="Сохраняем в Загрузки", files=files,
                           warnings=[line.removeprefix("WARNING: ") for line in errors if line.startswith("WARNING:")],
                           finished_at=now(), speed=None, eta=None)
                self._save(job)
            self._store_files(job_id, files)
        except Exception as exc:
            with self.lock:
                job = self.jobs[job_id]
                if job["status"] != "cancelled":
                    job.update(status="failed", phase="Не удалось скачать", error=readable_error(str(exc))[:6000], speed=None, eta=None)
                    self._save(job)
        finally:
            if process is not None:
                if process.poll() is None:
                    stop_process(process)
                    self._reap(process)
                for pipe in (process.stdout, process.stderr):
                    if pipe and not pipe.closed:
                        pipe.close()
            with self.lock:
                self.processes.pop(job_id, None)

    def file(self, job_id: str, name: str) -> Path:
        with self.lock:
            job = self.jobs[job_id]
            if job["status"] != "complete" or name not in {file["name"] for file in job["files"]}:
                raise FileNotFoundError(name)
        directory = Path(job["saved_dir"]) if job.get("saved_dir") else self.root / job_id / "files"
        if job.get("saved_dir") and directory.resolve() != self.save_dir:
            raise FileNotFoundError(name)
        path = (directory / name).resolve()
        if path.parent != directory.resolve() or not path.is_file():
            raise FileNotFoundError(name)
        return path

    def bundle(self, job_id: str) -> Path:
        # Serialize archive creation, including simultaneous clicks in several tabs.
        with self.lock:
            job = self.jobs[job_id]
            if job["status"] != "complete":
                raise FileNotFoundError(job_id)
            bundle = self.root / job_id / f"download-{uuid.uuid4().hex}.zip"
            temporary = bundle.with_suffix(".tmp")
            try:
                with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                    for file in job["files"]:
                        archive.write(self.file(job_id, file["name"]), arcname=file["name"])
                temporary.replace(bundle)
            finally:
                temporary.unlink(missing_ok=True)
            return bundle

    def close(self):
        with self.lock:
            self.closed = True
            for job_id in self.jobs:
                if self.jobs[job_id]["status"] in ACTIVE:
                    # Saving the final result is atomic from the user's perspective.
                    if self.jobs[job_id]["phase"] != "Сохраняем в Загрузки":
                        self.cancel(job_id)
        self.executor.shutdown(wait=True, cancel_futures=True)


def numeric(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
