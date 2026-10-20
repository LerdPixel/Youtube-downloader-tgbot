from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

from fastapi.testclient import TestClient
from pydantic import ValidationError
import yt_dlp

from web_app.jobs import ACTIVE, JobManager
from web_app.options import DownloadSettings, Source, build_command, seconds, select_format
from web_app.server import create_app, inspect_video

URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"


class SettingsTests(unittest.TestCase):
    def test_url_rejects_other_hosts_and_channels(self):
        for url in ("file:///etc/passwd", "https://youtube.com.evil.test/watch?v=x", "http://127.0.0.1/x",
                    "https://user:pass@youtube.com/watch?v=x", "https://www.youtube.com/playlist",
                    "https://www.youtube.com/@channel", "https://www.youtube.com/watch?list=foo%20bar"):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                Source(url=url)
        self.assertEqual(Source(url=" https://youtu.be/test ").url, "https://youtu.be/test")

    def test_time_validation(self):
        self.assertEqual(seconds("1:02:03.5"), 3723.5)
        for value in ("1:70", "-2", "abc", "1:2:3:4"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                seconds(value)
        with self.assertRaises(ValidationError):
            DownloadSettings(url=URL, start="20", end="10")

    def test_browser_cookies(self):
        settings = DownloadSettings(url=URL, cookies_browser="firefox")
        command = build_command(settings,Path("/tmp/results"))
        self.assertEqual(command[command.index("--cookies-from-browser")+1],"firefox")
        with self.assertRaises(ValidationError):
            Source(url=URL,cookies_browser="invalid")

    def test_format_selection_keeps_filters_in_fallback(self):
        settings = DownloadSettings(url=URL, resolution=720, fps=30, video_codec="h264")
        self.assertEqual(select_format(settings), "bv[height<=?720][fps<=?30][vcodec^=avc][ext=mp4]+ba[ext=m4a]/b[height<=?720][fps<=?30][vcodec^=avc][ext=mp4]")
        self.assertEqual(select_format(DownloadSettings(url=URL, mode="silent", container="auto", resolution=0)), "bv")
        self.assertEqual(select_format(DownloadSettings(url=URL, mode="audio", audio_language="en")), "ba[language=en]/b[language=en]")

    def test_incompatible_options(self):
        for options in (dict(container="webm", video_codec="h264"), dict(mode="audio", audio_format="wav", thumbnail="embed"),
                        dict(embed_subtitles=True), dict(container="webm", subtitles=["en"], embed_subtitles=True)):
            with self.subTest(options=options), self.assertRaises(ValidationError):
                DownloadSettings(url=URL, **options)

    def test_command_parses_in_real_ytdlp(self):
        settings = DownloadSettings(url=URL, mode="audio", audio_format="mp3", audio_quality="320", thumbnail="both",
                                    subtitles=["en", "ru"], auto_subtitles=True, start="1:20", end="2:00", filename="100% music")
        command = build_command(settings, Path("/tmp/results"))
        parsed = yt_dlp.parse_options(command[3:])
        self.assertEqual(parsed.ydl_opts["format"], "ba/b")
        self.assertEqual(parsed.ydl_opts["outtmpl"]["default"], "100%% music.%(ext)s")
        self.assertTrue(parsed.ydl_opts["writethumbnail"])
        self.assertTrue(parsed.ydl_opts["writeautomaticsub"])
        self.assertEqual(list(parsed.ydl_opts["download_ranges"]({}, None))[0], {"start_time":80, "end_time":120})
        self.assertIn("FFmpegExtractAudio", [pp["key"] for pp in parsed.ydl_opts["postprocessors"]])

    def test_no_command_injection_or_filename_templates(self):
        settings = DownloadSettings(url=URL, filename="$(touch owned) %(title)s / file", format_selector="137+140")
        command = build_command(settings, Path("/tmp/results"))
        self.assertEqual(command[-2:], ["--", URL])
        self.assertIn("$(touch owned) %%(title)s _ file.%(ext)s", command)
        self.assertEqual(command[command.index("--format")+1], "137+140")


class APITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.client = TestClient(create_app(Path(self.directory.name)/"cache", Path(self.directory.name)/"Downloads"), base_url="http://127.0.0.1")
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.directory.cleanup()

    def test_static_and_health(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertIn("YouTube Local", self.client.get("/").text)
        self.assertIn("version", self.client.get("/api/health").json())
        self.assertEqual(self.client.get("/api/health").json()["download_dir"],str((Path(self.directory.name)/"Downloads").resolve()))
        self.assertEqual(self.client.get("/api/jobs").json(), [])
        self.assertEqual(self.client.get("/.env").status_code, 404)
        self.assertEqual(self.client.get("/bot.py").status_code, 404)

    def test_origin_and_host_checks(self):
        response = self.client.post("/api/analyze", json={"url":URL}, headers={"Origin":"https://evil.test"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get("/api/health", headers={"Host":"evil.test"}).status_code, 400)
        self.assertEqual(self.client.post("/api/analyze", content="x", headers={"Content-Type":"text/plain"}).status_code, 415)

    def test_validation_and_missing_jobs(self):
        self.assertEqual(self.client.post("/api/analyze", json={"url":"http://localhost/foo"}).status_code, 422)
        self.assertEqual(self.client.post("/api/jobs", json={"url":URL, "start":"20", "end":"10"}).status_code, 422)
        self.assertEqual(self.client.post("/api/jobs/missing/cancel", json={}).status_code, 404)
        self.assertEqual(self.client.get("/api/jobs/missing/bundle").status_code, 404)

    def test_inspection_removes_direct_urls_and_storyboards(self):
        info = dict(id="x", title="Title", duration=3, formats=[
            dict(format_id="1", height=1080, ext="mp4", vcodec="avc1", acodec="none", url="https://secret.example"),
            dict(format_id="2", ext="m4a", vcodec="none", acodec="mp4a", language="en"),
            dict(format_id="3", vcodec="none", acodec="none"),
            dict(format_id="4", vcodec="avc1", has_drm=True),
        ], subtitles={"en":[{"name":"English"}], "live_chat":[]}, automatic_captions={"ru":[{"name":"Russian"}]})
        with patch("web_app.server.subprocess.run", return_value=subprocess.CompletedProcess([],0,json.dumps(info),"")):
            data = self.client.post("/api/analyze",json={"url":URL}).json()
        self.assertEqual(data["resolutions"],[1080])
        self.assertEqual(len(data["formats"]),2)
        self.assertNotIn("secret.example",json.dumps(data))
        self.assertEqual(data["audio_languages"],["en"])
        self.assertEqual([sub["code"] for sub in data["subtitles"]],["en","ru"])

    def test_estimate_uses_cached_analysis_without_network_requests(self):
        info = dict(duration=100,formats=[
            dict(format_id="a",ext="m4a",vcodec="none",acodec="mp4a.40.2",filesize=1_000_000),
            dict(format_id="v",ext="mp4",vcodec="avc1",acodec="none",height=1080,filesize=10_000_000)])
        with patch("web_app.server.inspect_video",return_value=info) as inspect:
            analyzed = self.client.post("/api/analyze",json={"url":URL}).json()
            data = dict(analysis_id=analyzed["analysis_id"],settings=dict(url=URL))
            response = self.client.post("/api/estimate",json=data)
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()["size_bytes"],11_000_000)
            inspect.assert_called_once()
        data["settings"]["url"] = "https://youtu.be/other"
        self.assertEqual(self.client.post("/api/estimate",json=data).status_code,400)
        data["analysis_id"] = "expired"
        self.assertEqual(self.client.post("/api/estimate",json=data).status_code,400)


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = (Path(self.directory.name)/"cache").resolve()
        self.downloads = (Path(self.directory.name)/"Downloads").resolve()
        self.manager = JobManager(self.root,self.downloads)

    def tearDown(self):
        self.manager.close()
        self.directory.cleanup()

    def wait(self, job_id, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.manager.get(job_id)
            if job["status"] not in ACTIVE:
                return job
            time.sleep(0.03)
        self.fail(f"Job did not finish: {self.manager.get(job_id)}")

    def test_real_process_events_files_archive_and_history(self):
        def fake_command(settings, output):
            code = "from pathlib import Path; import json; " + f"p=Path({str(output)!r})/'пример.mp4'; p.write_bytes(b'video'); " + 'print(json.dumps({"type":"title","title":"Example"})); print(json.dumps({"type":"progress","downloaded":5,"total":5})); print(json.dumps({"type":"file","path":str(p)}))'
            return [sys.executable,"-c",code]
        with patch("web_app.jobs.build_command",side_effect=fake_command):
            job = self.manager.create(DownloadSettings(url=URL))
            result = self.wait(job["id"])
        self.assertEqual(result["status"],"complete")
        self.assertEqual(result["title"],"Example")
        self.assertEqual(self.manager.file(job["id"],"пример.mp4").read_bytes(),b"video")
        archive_path = self.manager.bundle(job["id"])
        with zipfile.ZipFile(archive_path) as archive:
            self.assertEqual(archive.namelist(),["пример.mp4"])
        archive_path.unlink()  # direct callers own the temporary archive, like the HTTP response
        with self.assertRaises(FileNotFoundError):
            self.manager.file(job["id"],"../job.json")
        self.assertEqual(Path(result["saved_dir"]),self.downloads)
        self.assertTrue((self.downloads/"пример.mp4").is_file())
        self.assertFalse((self.root/job["id"] / "files").exists())
        restored = JobManager(self.root,self.downloads)
        try:
            self.assertEqual(restored.get(job["id"])["status"],"complete")
        finally:
            restored.close()
        # Exercise actual FileResponse and ZIP through HTTP as well.
        with TestClient(create_app(self.root,self.downloads),base_url="http://127.0.0.1") as client:
            response = client.get(f"/api/jobs/{job['id']}/files/пример.mp4")
            self.assertEqual(response.content,b"video")
            self.assertIn("attachment",response.headers["content-disposition"])
            archive = client.get(f"/api/jobs/{job['id']}/bundle")
            self.assertEqual(zipfile.ZipFile(io.BytesIO(archive.content)).namelist(),["пример.mp4"])
            self.assertFalse(list((self.root/job['id']).glob('download-*.zip')))
            self.assertTrue((self.downloads/"пример.mp4").is_file())

    def test_cancel_running_and_queued(self):
        with patch("web_app.jobs.build_command", return_value=[sys.executable,"-c","import time; time.sleep(60)"]):
            first = self.manager.create(DownloadSettings(url=URL))
            deadline = time.monotonic()+4
            while first["id"] not in self.manager.processes and time.monotonic()<deadline:
                time.sleep(.02)
            second = self.manager.create(DownloadSettings(url=URL))
            self.manager.cancel(second["id"])
            self.manager.cancel(first["id"])
            self.assertEqual(self.wait(first["id"])["status"],"cancelled")
            self.assertEqual(self.wait(second["id"])["status"],"cancelled")

    def test_failed_process_reports_error(self):
        with patch("web_app.jobs.build_command",return_value=[sys.executable,"-c","print('ERROR: format not available'); raise SystemExit(1)"]):
            job = self.manager.create(DownloadSettings(url=URL))
            result = self.wait(job["id"])
        self.assertEqual(result["status"],"failed")
        self.assertIn("format not available",result["error"])

    def test_restart_marks_unfinished_job(self):
        job_id = "20c87e56-f43c-418f-9d02-940445ab9a40"
        directory = self.root/job_id
        directory.mkdir()
        (directory/"job.json").write_text(json.dumps(dict(id=job_id,status="downloading",created_at="2026-01-01")))
        restored = JobManager(self.root,self.downloads)
        try:
            self.assertEqual(restored.get(job_id)["status"],"interrupted")
        finally:
            restored.close()

    def legacy_job(self, files):
        job_id = "20c87e56-f43c-418f-9d02-940445ab9a40"
        directory = self.root/job_id/"files"
        directory.mkdir(parents=True)
        for name, content in files.items():
            (directory/name).write_bytes(content)
        job = dict(id=job_id,status="complete",phase="Готово",created_at="2026-01-01",
                   files=[dict(name=name,size=len(content)) for name,content in files.items()])
        (directory.parent/"job.json").write_text(json.dumps(job))
        return job_id,directory

    def test_old_downloads_migrate_and_existing_names_are_not_overwritten(self):
        job_id,directory = self.legacy_job({"video.mp4":b"new video","cover.jpg":b"cover"})
        self.downloads.mkdir()
        (self.downloads/"video.mp4").write_bytes(b"existing video")
        (directory.parent/"download.zip").write_bytes(b"old archive")
        restored = JobManager(self.root,self.downloads)
        restored.close()  # wait for migration, including cleanup
        self.assertEqual((self.downloads/"video.mp4").read_bytes(),b"existing video")
        self.assertEqual((self.downloads/"video (1).mp4").read_bytes(),b"new video")
        self.assertEqual((self.downloads/"cover.jpg").read_bytes(),b"cover")
        self.assertEqual(set(path.name for path in directory.parent.iterdir()),{"job.json"})
        self.assertEqual(restored.get(job_id)["saved_dir"],str(self.downloads))

    def test_failed_save_keeps_cache_and_rolls_back_new_copies(self):
        job_id,directory = self.legacy_job({"video.mp4":b"video","cover.jpg":b"cover"})
        original = JobManager._copy_unique
        def copy_first(manager,path):
            if path.suffix == ".jpg":
                raise OSError("disk full")
            return original(manager,path)
        with patch.object(JobManager,"_copy_unique",copy_first):
            restored = JobManager(self.root,self.downloads)
            restored.close()
        self.assertEqual((directory/"video.mp4").read_bytes(),b"video")
        self.assertEqual((directory/"cover.jpg").read_bytes(),b"cover")
        self.assertFalse(list(self.downloads.iterdir()))
        self.assertIn("disk full",restored.get(job_id)["storage_error"])

    def test_cross_volume_copy_and_restart_keep_saved_files(self):
        job_id,directory = self.legacy_job({"video.mp4":b"video"})
        with patch("web_app.jobs.os.link",side_effect=OSError("cross-device link")):
            restored = JobManager(self.root,self.downloads)
            restored.close()
        self.assertFalse(directory.exists())
        self.assertEqual((self.downloads/"video.mp4").read_bytes(),b"video")
        again = JobManager(self.root,self.downloads)
        again.close()
        self.assertEqual([path.name for path in self.downloads.iterdir()],["video.mp4"])
        self.assertEqual(again.file(job_id,"video.mp4").read_bytes(),b"video")


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self,*args):
        pass


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),"ffmpeg and ffprobe required")
class MediaIntegrationTests(unittest.TestCase):
    """Use real yt-dlp and ffmpeg with locally generated media; no YouTube dependency."""
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.directory.name)
        subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y","-f","lavfi","-i","testsrc=size=320x180:rate=25",
                        "-f","lavfi","-i","sine=frequency=440:sample_rate=44100","-t","4","-c:v","libx264","-pix_fmt","yuv420p",
                        "-c:a","aac","-movflags","+faststart",str(cls.root/"source.mp4")],check=True)
        cls.server = ThreadingHTTPServer(("127.0.0.1",0),partial(QuietHandler,directory=str(cls.root)))
        cls.thread = threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/source.mp4"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()
        cls.directory.cleanup()

    def run_download(self,**options):
        directory = self.root/f"jobs-{time.monotonic_ns()}"
        manager = JobManager(directory,self.root/"Downloads")
        # Local fixture bypasses the YouTube-only input validator, not the download pipeline.
        settings = DownloadSettings(url=URL,**options).model_copy(update={"url":self.url})
        try:
            job = manager.create(settings)
            deadline = time.monotonic()+40
            while time.monotonic()<deadline:
                result = manager.get(job["id"])
                if result["status"] not in ACTIVE:
                    self.assertEqual(result["status"],"complete",result)
                    files = [manager.file(job["id"],file["name"]) for file in result["files"]]
                    return files
                time.sleep(.1)
            self.fail("Media download timed out")
        finally:
            manager.close()

    def probe(self,path):
        result = subprocess.run(["ffprobe","-v","error","-show_streams","-show_format","-of","json",str(path)],capture_output=True,text=True,check=True)
        return json.loads(result.stdout)

    def test_video_and_sidecar(self):
        files = self.run_download(format_selector="b",filename="test-video",info_json=True)
        video = next(path for path in files if path.suffix == ".mp4")
        types = {stream["codec_type"] for stream in self.probe(video)["streams"]}
        self.assertEqual(types,{"video","audio"})
        self.assertTrue(any(path.name.endswith(".info.json") for path in files))

    def test_audio_conversion(self):
        files = self.run_download(mode="audio",audio_format="mp3",audio_quality="192",filename="test-audio")
        self.assertEqual([path.suffix for path in files],[".mp3"])
        streams = self.probe(files[0])["streams"]
        self.assertEqual([stream["codec_type"] for stream in streams],["audio"])
        self.assertEqual(streams[0]["codec_name"],"mp3")

    def test_silent_mode_removes_audio_even_with_manual_combined_stream(self):
        files = self.run_download(mode="silent",format_selector="b",filename="test-silent")
        self.assertEqual([stream["codec_type"] for stream in self.probe(files[0])["streams"]],["video"])

    def test_precise_clip_duration(self):
        files = self.run_download(format_selector="b",start="1",end="3",precise_cuts=True,filename="test-clip")
        duration = float(self.probe(files[0])["format"]["duration"])
        self.assertGreater(duration,1.8)
        self.assertLess(duration,2.3)


if __name__ == "__main__":
    unittest.main()
