import unittest

from web_app.options import DownloadSettings
from web_app.size_estimate import estimate_size

URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"


class SizeEstimateTests(unittest.TestCase):
    def setUp(self):
        # yt-dlp returns streams ordered from worst to best; the calculator preserves it.
        self.info = dict(duration=100, formats=[
            dict(format_id="ru", ext="m4a", vcodec="none", acodec="mp4a.40.2", language="ru", abr=96, filesize=1_200_000, asr=48000, audio_channels=2),
            dict(format_id="en", ext="m4a", vcodec="none", acodec="mp4a.40.2", language="en", abr=128, filesize=1_600_000, asr=48000, audio_channels=2),
            dict(format_id="720", ext="mp4", vcodec="avc1", acodec="none", height=720, fps=30, filesize=10_000_000),
            dict(format_id="1080", ext="mp4", vcodec="avc1", acodec="none", height=1080, fps=60, filesize=20_000_000),
        ])

    def estimate(self, **settings):
        return estimate_size(self.info, DownloadSettings(url=URL, **settings))

    def test_video_size_includes_audio_and_follows_quality(self):
        result = self.estimate()
        self.assertEqual(result["size_bytes"],21_600_000)
        self.assertTrue(result["approximate"])
        self.assertEqual(result["format_ids"],["1080","en"])
        self.assertEqual(self.estimate(resolution=720)["size_bytes"],11_600_000)
        self.assertEqual(self.estimate(fps=30)["size_bytes"],11_600_000)
        self.assertEqual(self.estimate(audio_language="ru")["size_bytes"],21_200_000)

    def test_silent_exact_size_and_manual_selection(self):
        result = self.estimate(mode="silent",metadata=False,chapters=False)
        self.assertEqual(result["size_bytes"],20_000_000)
        self.assertFalse(result["approximate"])
        self.assertEqual(self.estimate(format_selector="720+ru")["size_bytes"],11_200_000)

    def test_audio_conversion_and_lossless_output(self):
        self.assertEqual(self.estimate(mode="audio",audio_format="mp3",audio_quality="192")["size_bytes"],2_400_000)
        self.assertEqual(self.estimate(mode="audio",audio_format="mp3",audio_quality="320")["size_bytes"],4_000_000)
        self.assertEqual(self.estimate(mode="audio",audio_format="m4a")["size_bytes"],1_600_000)
        self.assertEqual(self.estimate(mode="audio",audio_format="wav")["size_bytes"],19_200_000)
        self.assertIsNone(self.estimate(mode="audio",audio_format="flac")["size_bytes"])
        self.assertIsNone(self.estimate(mode="audio",audio_quality="0")["size_bytes"])

    def test_clip_scales_streams_and_converted_audio(self):
        self.assertEqual(self.estimate(start="10",end="30")["size_bytes"],4_320_000)
        self.assertEqual(self.estimate(mode="audio",start="10",end="30")["size_bytes"],480_000)
        self.assertEqual(self.estimate(start="90",end="200")["size_bytes"],2_160_000)
        self.assertIsNone(self.estimate(start="200")["size_bytes"])

    def test_approximate_sizes_bitrates_and_unknown_streams(self):
        self.info["formats"][-1].pop("filesize")
        self.info["formats"][-1]["filesize_approx"] = 30_000_000
        self.assertEqual(self.estimate()["size_bytes"],31_600_000)
        self.info["formats"][-1].pop("filesize_approx")
        self.info["formats"][-1]["tbr"] = 1600
        self.assertEqual(self.estimate()["size_bytes"],21_600_000)
        self.info["formats"][-1].pop("tbr")
        self.assertIsNone(self.estimate()["size_bytes"])
        self.assertIsNone(self.estimate(format_selector="nonexistent")["size_bytes"])
        self.assertIsNone(self.estimate(format_selector="bv+(")["size_bytes"])

    def test_combined_stream_only_counts_audio_or_video_when_extracting(self):
        self.info["formats"] = [dict(format_id="combined",ext="mp4",vcodec="avc1",acodec="mp4a.40.2",
                                     height=720,tbr=1000,abr=128,filesize=12_500_000)]
        self.assertEqual(self.estimate(mode="audio",audio_format="m4a")["size_bytes"],1_600_000)
        self.assertEqual(self.estimate(mode="silent",format_selector="combined")["size_bytes"],10_900_000)


if __name__ == "__main__":
    unittest.main()
