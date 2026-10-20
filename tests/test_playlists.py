import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from pydantic import ValidationError

from fastapi.testclient import TestClient

from web_app.jobs import ACTIVE, JobManager
from web_app.options import Source, DownloadSettings, is_playlist_url
from web_app.playlists import normalize_playlist
from web_app.server import create_app, inspect_video

URL = "https://www.youtube.com/playlist?list=PL_test"
RAW = dict(_type="playlist",id="PL_test",title="Тестовый плейлист",entries=[
    dict(id="first",title="Первое видео",duration=10),
    dict(id="private",title="[Private video]"),
    dict(id="second",title="Второе видео",duration=20),
    None,
    dict(id="live",title="Сейчас в эфире",live_status="is_live"),
])


class PlaylistAnalysisTests(unittest.TestCase):
    def test_valid_playlist_urls_and_normalization(self):
        for url in (URL,"https://music.youtube.com/playlist?list=PL_test","https://youtube.com/watch?v=video&list=PL_test"):
            self.assertTrue(is_playlist_url(Source(url=url).url))
        info = normalize_playlist(RAW)
        self.assertEqual(info["entry_count"],5)
        self.assertEqual(info["available_count"],2)
        self.assertEqual(info["duration"],30)
        self.assertEqual(info["entries"][2]["url"],"https://www.youtube.com/watch?v=second")
        self.assertFalse(info["entries"][1]["available"])
        self.assertFalse(info["entries"][3]["available"])
        self.assertFalse(info["entries"][4]["available"])

    def test_playlist_analysis_is_flat(self):
        with patch("web_app.server.subprocess.run",return_value=subprocess.CompletedProcess([],0,json.dumps(RAW),"")) as run:
            result = inspect_video(Source(url=URL))
        command = run.call_args.args[0]
        self.assertIn("--flat-playlist",command)
        self.assertIn("--yes-playlist",command)
        self.assertNotIn("--no-playlist",command)
        self.assertEqual(result["kind"],"playlist")

    def test_music_links_use_standard_playlist_extractor(self):
        with patch("web_app.server.subprocess.run",return_value=subprocess.CompletedProcess([],0,json.dumps(RAW),"")) as run:
            result = inspect_video(Source(url="https://music.youtube.com/playlist?list=PL_test&si=share-code"))
        self.assertEqual(run.call_args.args[0][-1],URL)
        self.assertEqual(result["available_count"],2)

    def test_missing_playlist_id_explains_incomplete_link(self):
        for url in ("https://music.youtube.com/playlist?list=", "https://youtube.com/playlist"):
            with self.subTest(url=url),self.assertRaisesRegex(ValidationError,"идентификатор после list="):
                Source(url=url)

    def test_empty_and_too_large_playlists(self):
        with self.assertRaises(ValueError):
            normalize_playlist(dict(entries=[]))
        with self.assertRaises(ValueError):
            normalize_playlist(dict(RAW,playlist_count=10001))


class PlaylistAPITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.client = TestClient(create_app(root/"cache",root/"Downloads"),base_url="http://127.0.0.1")
        self.client.__enter__()
        with patch("web_app.server.inspect_video",return_value=normalize_playlist(RAW)):
            self.analysis = self.client.post("/api/analyze",json=dict(url=URL)).json()

    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.directory.cleanup()

    def payload(self,**kwargs):
        return dict(analysis_id=self.analysis["analysis_id"],settings=dict(url=URL,mode="audio",audio_format="mp3"),**kwargs)

    def test_download_all_keeps_order_and_common_settings(self):
        with patch.object(self.client.app.state.jobs,"create_many",return_value=[{},{}]) as create:
            response = self.client.post("/api/playlists/download",json=self.payload())
        self.assertEqual(response.status_code,201,response.text)
        self.assertEqual(response.json()["count"],2)
        items = create.call_args.args[0]
        self.assertEqual([settings.url for settings,_ in items],["https://www.youtube.com/watch?v=first","https://www.youtube.com/watch?v=second"])
        self.assertTrue(all(settings.mode == "audio" and settings.audio_format == "mp3" for settings,_ in items))
        self.assertEqual(items[0][0].filename,"001 - Первое видео")
        self.assertEqual(items[1][0].filename,"003 - Второе видео")
        self.assertEqual(items[0][1]["batch_id"],items[1][1]["batch_id"])

    def test_select_subset_empty_and_unknown_entries(self):
        with patch.object(self.client.app.state.jobs,"create_many",return_value=[{}]) as create:
            response = self.client.post("/api/playlists/download",json=self.payload(entry_indices=[3]))
        self.assertEqual(response.status_code,201)
        self.assertEqual(create.call_args.args[0][0][1]["playlist_index"],3)
        for indices in ([],[2],[999]):
            response = self.client.post("/api/playlists/download",json=self.payload(entry_indices=indices))
            self.assertEqual(response.status_code,400,response.text)

    def test_analysis_required_and_direct_jobs_do_not_expand_playlists(self):
        data = self.payload(); data["analysis_id"] = "missing"
        self.assertEqual(self.client.post("/api/playlists/download",json=data).status_code,400)
        self.assertEqual(self.client.post("/api/jobs",json=dict(url=URL)).status_code,400)
        response = self.client.post("/api/estimate",json=self.payload())
        self.assertIsNone(response.json()["size_bytes"])
        self.assertEqual(self.client.post("/api/batches/missing/cancel",json={}).json()["cancelled"],0)


class PlaylistQueueTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.manager = JobManager(root/"cache",root/"Downloads")

    def tearDown(self):
        self.manager.close()
        self.directory.cleanup()

    def items(self,count):
        return [(DownloadSettings(url=f"https://www.youtube.com/watch?v=video{index}"),
                 dict(batch_id="batch",playlist_title="Playlist",playlist_index=index,title=f"Video {index}")) for index in range(count)]

    def test_can_enqueue_more_than_thirty_and_cancel_as_a_group(self):
        with patch.object(self.manager,"_run"):
            result = self.manager.create_many(self.items(35))
        self.assertEqual(len(result),35)
        self.assertEqual(self.manager.cancel_batch("batch")["cancelled"],35)
        self.assertTrue(all(job["status"] == "cancelled" for job in self.manager.list()))

    def test_failed_video_does_not_stop_later_video_and_outputs_are_saved(self):
        def command(settings,directory):
            if settings.url.endswith("video0"):
                return [sys.executable,"-c","print('ERROR: unavailable'); raise SystemExit(1)"]
            return [sys.executable,"-c",f"from pathlib import Path; (Path({str(directory)!r})/'video.mp4').write_bytes(b'video')"]
        with patch("web_app.jobs.build_command",side_effect=command):
            jobs = self.manager.create_many(self.items(2))
            deadline = time.monotonic()+10
            while any(job["status"] in ACTIVE for job in self.manager.list()) and time.monotonic()<deadline:
                time.sleep(.02)
        self.assertEqual(self.manager.get(jobs[0]["id"])["status"],"failed")
        self.assertEqual(self.manager.get(jobs[1]["id"])["status"],"complete")
        self.assertTrue((self.manager.save_dir/"video.mp4").exists())
        self.assertFalse((self.manager.root/jobs[1]["id"]/"files").exists())

    def test_queue_preparation_is_atomic_on_disk_failure(self):
        original = self.manager._save
        count = 0
        def fail_second(job):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("disk full")
            original(job)
        with patch.object(self.manager,"_save",side_effect=fail_second),patch.object(self.manager,"_run") as run:
            with self.assertRaises(OSError):
                self.manager.create_many(self.items(3))
            run.assert_not_called()
        self.assertEqual(self.manager.list(),[])
        self.assertEqual(list(self.manager.root.iterdir()),[])


if __name__ == "__main__":
    unittest.main()
