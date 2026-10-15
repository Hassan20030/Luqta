"""Host regression checks using real yt-dlp/FFmpeg and a controlled HTTP source."""
import contextlib
import io
import json
import os
import subprocess
import sys
import struct
import time
import tempfile
import threading
import unittest
from fractions import Fraction
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "app/src/main/assets/engine"
sys.path.insert(0, str(ENGINE))
import worker
from core import download_options, resolution, verify_spec


class Fixture(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/resume.mp4":
            data = (Path(self.directory) / "resume.mp4").read_bytes()
            value = self.headers.get("Range", "bytes=0-").split("=")[1].split("-")
            start, end = int(value[0]), min(int(value[1]) if value[1] else len(data)-1,len(data)-1)
            self.server.ranges.append(start)
            self.send_response(206 if self.headers.get("Range") else 200)
            self.send_header("Content-Type","video/mp4");self.send_header("Content-Length",str(end-start+1))
            if self.headers.get("Range"): self.send_header("Content-Range",f"bytes {start}-{end}/{len(data)}")
            self.end_headers()
            try:
                for i in range(start,end+1,32768):
                    self.wfile.write(data[i:min(i+32768,end+1)]);self.wfile.flush();time.sleep(.004)
            except (BrokenPipeError,ConnectionResetError): pass
            return
        if path == "/blocked.ts":
            self.server.refresh = True
            self.send_error(403)
            return
        if path in ("/refresh.m3u8", "/deny.m3u8"):
            self.server.manifests += 1
            text = (Path(self.directory) / "stream.m3u8").read_text()
            if path == "/deny.m3u8" or not self.server.refresh:
                text = text.replace("segment0.ts", "blocked.ts")
            data = text.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        super().do_GET()

    def do_HEAD(self):
        if self.path in ("/refresh.m3u8", "/deny.m3u8"):
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.end_headers()
        else:
            super().do_HEAD()


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="luqta-tests-")
        cls.root = Path(cls.temp.name)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(ENGINE / "sample.mp4"), "-c", "copy",
                        "-hls_time", "1", "-hls_playlist_type", "vod", "-hls_segment_filename", str(cls.root / "segment%d.ts"),
                        str(cls.root / "stream.m3u8")], check=True)
        dash = cls.root / "dash120"
        dash.mkdir()
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=2160x3840:r=120",
                        "-frames:v", "6", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "40", "-threads", "1",
                        str(dash / "portrait.mp4")], check=True)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(dash / "portrait.mp4"), "-c", "copy",
                        "-f", "dash", str(dash / "stream.mpd")], check=True)
        # Declare the same measured rate on the representation as well as the
        # adaptation set, so yt-dlp's generic DASH parser exposes it to selection.
        manifest = dash / "stream.mpd"
        manifest.write_text(manifest.read_text().replace('<Representation id="0"', '<Representation id="0" frameRate="120"'))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Fixture, directory=str(cls.root)))
        cls.server.refresh = False
        cls.server.manifests = 0
        cls.server.ranges = []
        (cls.root / "resume.mp4").write_bytes((ENGINE / "sample.mp4").read_bytes() + struct.pack(">I4s",3*1024*1024,b"free") + bytes(3*1024*1024-8))
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp.cleanup()

    def run_worker(self, request):
        env = dict(os.environ, LUQTA_ALLOW_LOCAL_MEDIA="1", LUQTA_NATIVE_FFMPEG="/usr/bin/ffmpeg", LUQTA_NATIVE_FFPROBE="/usr/bin/ffprobe", LUQTA_APP_DATA_DIR=str(self.root))
        env.pop("LUQTA_NATIVE_QJS", None)
        result = subprocess.run([sys.executable, str(ENGINE / "worker.py")], input=json.dumps(request),
                                text=True, capture_output=True, env=env, timeout=60)
        events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        self.assertTrue(events, result.stderr)
        return result.returncode, events

    def download(self, url, fmt, quality="best", fps="best"):
        directory = Path(tempfile.mkdtemp(dir=self.root))
        code, events = self.run_worker({"url": url, "format": fmt, "quality": quality, "fps": fps, "directory": str(directory)})
        return code, events, directory

    def probe(self, file):
        return json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(file)]))

    def test_packaged_ejs_health(self):
        code, events = self.run_worker({"mode": "health"})
        self.assertEqual(code, 0)
        self.assertTrue(events[-1]["ffmpeg"])
        self.assertTrue(events[-1]["ejs"])

    def test_real_download_and_audio(self):
        code, events = self.run_worker({"mode": "info", "url": worker.SAMPLE})
        self.assertEqual(code, 0)
        self.assertEqual(events[-1]["info"]["duration"], 3)
        self.assertEqual(events[-1]["info"]["resolutions"], [720])
        for fmt in ("mp4", "mp3", "m4a"):
            with self.subTest(format=fmt):
                code, events, directory = self.download(worker.SAMPLE, fmt)
                self.assertEqual(code, 0, events)
                done = events[-1]
                self.assertEqual(done["status"], "done")
                data = self.probe(directory / done["filename"])
                kinds = {s["codec_type"] for s in data["streams"]}
                self.assertEqual(kinds, {"video", "audio"} if fmt == "mp4" else {"audio"})
                self.assertAlmostEqual(float(data["format"]["duration"]), 3, delta=0.3)
                self.assertFalse(any(e.get("percent") == 100 for e in events[:-1]))
                if fmt == "mp4":
                    self.assertEqual((done["width"], done["height"], done["fps"]), (1280, 720, 24))

    def test_real_portrait_2160_at_120_fps(self):
        url = f"http://127.0.0.1:{self.server.server_port}/dash120/stream.mpd"
        code, events, directory = self.download(url, "mp4", "2160", "120")
        self.assertEqual(code, 0, events)
        done = events[-1]
        self.assertEqual((done["width"], done["height"], done["resolution"], done["fps"]), (2160, 3840, 2160, 120))
        self.assertTrue((directory / done["filename"]).is_file())

    def test_tiktok_copy_transcodes_codec_audio_pixels_and_fps(self):
        source, output = self.root / "tiktok-source.webm", self.root / "tiktok-copy.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x640:r=120:d=1",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:v", "libvpx-vp9", "-deadline", "realtime",
                        "-cpu-used", "8", "-c:a", "libopus", "-shortest", str(source)], check=True)
        code, events = self.run_worker({"mode": "tiktok", "input": str(source), "output": str(output)})
        self.assertEqual(code, 0, events)
        self.assertEqual(events[-1]["status"], "done")
        streams = self.probe(output)["streams"]
        video = next(s for s in streams if s["codec_type"] == "video")
        audio = next(s for s in streams if s["codec_type"] == "audio")
        self.assertEqual((video["codec_name"], video["pix_fmt"]), ("h264", "yuv420p"))
        self.assertLessEqual(float(Fraction(video["avg_frame_rate"])), 60.5)
        self.assertEqual(audio["codec_name"], "aac")
        self.assertTrue(any(e.get("status") == "processing" and e.get("percent") is not None for e in events))

    def test_tiktok_copy_accepts_odd_dimensions_without_audio(self):
        source, output = self.root / "odd.mkv", self.root / "odd-tiktok.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=321x641:rate=24:duration=0.25",
                        "-c:v", "ffv1", "-pix_fmt", "bgr0", "-threads", "1", str(source)], check=True)
        original = source.read_bytes()
        code, events = self.run_worker({"mode": "tiktok", "input": str(source), "output": str(output)})
        self.assertEqual(code, 0, events)
        video = next(s for s in self.probe(output)["streams"] if s["codec_type"] == "video")
        self.assertEqual((video["width"], video["height"]), (320, 640))
        self.assertEqual(video["sample_aspect_ratio"], "1:1")
        self.assertEqual(float(Fraction(video["avg_frame_rate"])), 24)
        self.assertIsNone(events[-1]["audio_codec"])
        self.assertEqual(source.read_bytes(), original)

    def test_tiktok_copy_limits_square_and_portrait_on_both_axes(self):
        square = self.root / "square.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=1400x1400:r=30:d=0.2",
                        "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1", str(square)], check=True)
        for name, source, expected in [("square", square, (1080, 1080)), ("portrait", self.root / "dash120/portrait.mp4", (1080, 1920))]:
            with self.subTest(name=name):
                output = self.root / (name + "-fitted.mp4")
                code, events = self.run_worker({"mode": "tiktok", "input": str(source), "output": str(output)})
                self.assertEqual(code, 0, events)
                self.assertEqual((events[-1]["width"], events[-1]["height"]), expected)
                self.assertLessEqual(events[-1]["fps"], 60)

    def test_tiktok_copy_bakes_rotation_once(self):
        source, rotated, output = [self.root / name for name in ("landscape.mp4", "rotated.mp4", "rotation-tiktok.mp4")]
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=0.2",
                        "-c:v", "libx264", "-threads", "1", str(source)], check=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-display_rotation", "90", "-i", str(source), "-c", "copy", str(rotated)], check=True)
        code, events = self.run_worker({"mode": "tiktok", "input": str(rotated), "output": str(output)})
        self.assertEqual(code, 0, events)
        video = next(s for s in self.probe(output)["streams"] if s["codec_type"] == "video")
        self.assertEqual((video["width"], video["height"]), (240, 320))
        self.assertFalse(any(s.get("rotation", 0) for s in video.get("side_data_list", [])))

    def test_tiktok_copy_rejects_paths_outside_app_data(self):
        output = self.root / "blocked-copy.mp4"
        code, events = self.run_worker({"mode": "tiktok", "input": str(ENGINE / "sample.mp4"), "output": str(output)})
        self.assertNotEqual(code, 0)
        self.assertEqual(events[-1]["status"], "error")
        self.assertFalse(output.exists())

    def test_unavailable_120_fps_does_not_save_a_lower_rate(self):
        code, events, directory = self.download(worker.SAMPLE, "mp4", "720", "120")
        self.assertNotEqual(code, 0)
        self.assertEqual(events[-1]["status"], "error")
        self.assertFalse(list(directory.glob("*.mp4")))

    def select(self, formats, quality="2160", fps="best", recovery=False):
        job = {"url": "https://www.youtube.com/watch?v=fixture", "format": "mp4", "quality": quality, "fps": fps}
        opts = download_options(job, self.root / "selection", recovery)
        opts["logger"] = worker.DiagnosticLog()
        with worker.yt_dlp.YoutubeDL(opts) as ydl:
            ydl.add_post_processor(worker.MediaMetadataPP(ydl), when="pre_process")
            return ydl.process_ie_result({"id": "fixture", "title": "fixture", "extractor": "Fixture", "formats": formats}, download=False)

    def formats(self, portrait=True):
        result = []
        for short, rate, codec in [(1080, 120, "h264"), (2160, 60, "h264"), (2160, 120, "vp9")]:
            width, height = short, round(short * 16 / 9)
            if not portrait:
                width, height = height, width
            result.append({"format_id": f"{short}-{rate}", "width": width, "height": height, "fps": rate,
                           "url": "https://media.example/video.mp4", "vcodec": codec, "acodec": "none", "ext": "mp4"})
        result.append({"format_id": "audio", "url": "https://media.example/audio.m4a", "vcodec": "none", "acodec": "aac", "ext": "m4a"})
        return result

    def test_portrait_and_landscape_select_2160_highest_fps(self):
        for portrait in (True, False):
            with self.subTest(portrait=portrait):
                info = self.select(self.formats(portrait))
                self.assertEqual(resolution(info), 2160)
                self.assertEqual(info["fps"], 120)
                self.assertEqual(worker.metadata(info)["resolutions"], [2160, 1080])

    def test_explicit_request_and_recovery_cannot_downgrade(self):
        with self.assertRaisesRegex(Exception, "Requested format is not available"):
            self.select([f for f in self.formats() if f.get("width") != 2160])
        formats = self.formats()
        for f in formats:
            if f.get("width") == 1080 or f["format_id"] == "audio":
                f["protocol"] = "m3u8_native"
            else:
                f["protocol"] = "https"
        with self.assertRaisesRegex(Exception, "Requested format is not available"):
            self.select(formats, fps="120", recovery=True)

    def test_final_specs_reject_false_source_labels(self):
        with self.assertRaises(ValueError):
            verify_spec({"width": 1080, "height": 1920, "fps": 120}, {"format": "mp4", "quality": "2160", "fps": "120"})
        with self.assertRaises(ValueError):
            verify_spec({"width": 2160, "height": 3840, "fps": 60}, {"format": "mp4", "quality": "2160", "fps": "120"})
        verify_spec({"width": 2160, "height": 3840, "fps": 119.88}, {"format": "mp4", "quality": "2160", "fps": "120"})

    def test_refresh_recovers_from_403(self):
        self.server.refresh = False
        url = f"http://127.0.0.1:{self.server.server_port}/refresh.m3u8"
        code, events, directory = self.download(url, "mp4")
        self.assertEqual(code, 0, events)
        self.assertEqual(sum(e.get("status") == "retrying" for e in events), 1)
        self.assertEqual(events[-1]["status"], "done")
        self.assertAlmostEqual(float(self.probe(directory / events[-1]["filename"])["format"]["duration"]), 3, delta=0.3)

    def test_permanent_403_stops_after_one_recovery(self):
        url = f"http://127.0.0.1:{self.server.server_port}/deny.m3u8"
        code, events, _ = self.download(url, "mp4")
        self.assertEqual(code, 1)
        self.assertEqual(sum(e.get("status") == "retrying" for e in events), 1)
        final = events[-1]
        self.assertEqual(final["status"], "error")
        self.assertIn("الموقع رفض", final["error"])
        self.assertIn("403", final["error_detail"])
        self.assertIsNone(final["percent"])
        self.assertEqual(final["speed"], 0)

    def test_other_errors_do_not_retry(self):
        code, events, _ = self.download(f"http://127.0.0.1:{self.server.server_port}/absent.mp4", "mp4")
        self.assertNotEqual(code, 0)
        self.assertFalse(any(e.get("status") == "retrying" for e in events))

    def test_two_stream_progress_does_not_jump_back(self):
        progress = worker.Progress("test")
        progress.prepare({"requested_formats": [{"format_id": "v"}, {"format_id": "a"}]})
        with contextlib.redirect_stdout(io.StringIO()) as output:
            for fmt, downloaded, total, finished in [("v", 50, 100, False), ("v", 100, 100, True), ("a", 0, 20, False), ("a", 10, 20, False), ("a", 20, 20, True)]:
                progress.last = 0
                progress.download({"info_dict": {"format_id": fmt}, "downloaded_bytes": downloaded,
                                   "total_bytes": total, "status": "finished" if finished else "downloading"})
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([e["percent"] for e in events], [25, 50, 50, 75, 99])
        self.assertEqual(events[-1]["downloaded"], 120)

    def test_redacts_sensitive_error_details(self):
        text = worker.safe_error("HTTP Error 403: Forbidden https://media.test/video?token=secret cookie=private")
        self.assertNotIn("secret", text)
        self.assertNotIn("private", text)
        self.assertNotIn("https://", text)

    def test_configurable_fragment_concurrency(self):
        job={"url":"https://media.example/video", "format":"mp4", "quality":"best"}
        for n in (1,2,4,8):
            self.assertEqual(download_options({**job,"fragments":n},self.root)["concurrent_fragment_downloads"],n)
        for n in (0,16,True,"8"):
            with self.assertRaises(ValueError): download_options({**job,"fragments":n},self.root)

    def test_video_pause_then_resume_uses_range(self):
        directory=Path(tempfile.mkdtemp(dir=self.root))
        request={"url":f"http://127.0.0.1:{self.server.server_port}/resume.mp4","format":"mp4","quality":"best","directory":str(directory)}
        env=dict(os.environ,LUQTA_ALLOW_LOCAL_MEDIA="1",LUQTA_NATIVE_FFMPEG="/usr/bin/ffmpeg",LUQTA_NATIVE_FFPROBE="/usr/bin/ffprobe")
        p=subprocess.Popen([sys.executable,str(ENGINE/"worker.py")],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
        p.stdin.write(json.dumps(request)+"\n");p.stdin.close()
        while True:
            line=p.stdout.readline();self.assertTrue(line,"No download progress")
            event=json.loads(line)
            if event.get("status")=="downloading" and event.get("downloaded",0)>65536: break
        p.terminate();p.wait(timeout=5);p.stdout.close();p.stderr.close()
        self.assertTrue(list(directory.rglob("*.part")),"Pause keeps video parts")
        self.server.ranges.clear();code,events=self.run_worker(request);self.assertEqual(code,0,events)
        self.assertTrue(any(n>0 for n in self.server.ranges),"Resume requests remaining bytes")
        self.assertEqual((directory/events[-1]["filename"]).read_bytes(),(self.root/"resume.mp4").read_bytes())


if __name__ == "__main__":
    unittest.main(verbosity=2)
