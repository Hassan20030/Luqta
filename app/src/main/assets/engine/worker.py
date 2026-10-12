"""One extraction/download path and a bounded recovery, with NDJSON events."""
import json
import os
import shutil
import subprocess
import sys
import time
from fractions import Fraction
from pathlib import Path

import yt_dlp
import images
from network import SecureYoutubeDL, SecureUrllibRH, install_guards, LOCAL_INPUT_ARGS
from core import base_options, download_options, error_message, forbidden, resolution, safe_error, tools, validate_url, verify_spec, youtube_url

SAMPLE = "https://appassets.androidplatform.net/sample.mp4"


def emit(data):
    print(json.dumps(data, ensure_ascii=False), flush=True)


class DiagnosticLog:
    def __init__(self):
        self.warnings = []

    def debug(self, message):
        pass

    info = debug
    error = debug

    def warning(self, message):
        text = safe_error(message)
        if text not in self.warnings:
            self.warnings = (self.warnings + [text])[-3:]


def validate_media(info, incomplete=False):
    if info.get("_type") in ("playlist", "multi_video"):
        raise ValueError("أضف رابط مقطع واحد. قوائم التشغيل غير مفعّلة في هذه النسخة.")
    if info.get("is_live"):
        raise ValueError("البث المباشر غير مفعّل. استخدم رابط مقطع مكتمل.")
    if info.get("has_drm"):
        raise ValueError("المقطع محمي ولا يمكن تحميله بهذه الأداة.")


def metadata(info):
    validate_media(info)
    variants = []
    for f in info.get("formats") or [info]:
        if f.get("vcodec") == "none" or f.get("has_drm"):
            continue
        item = {"resolution": resolution(f), "width": f.get("width"), "height": f.get("height"), "fps": f.get("fps"), "ext": f.get("ext")}
        if item not in variants:
            variants.append(item)
    resolutions = sorted({f["resolution"] for f in variants if f["resolution"]}, reverse=True)
    return {"title": str(info.get("title") or "مقطع فيديو")[:400], "source": info.get("extractor_key") or "yt-dlp",
            "duration": info.get("duration") or 0, "thumbnail": info.get("thumbnail"),
            "width": info.get("width"), "height": info.get("height"), "fps": info.get("fps"),
            "resolution": resolution(info), "resolutions": resolutions, "variants": variants,
            "size": info.get("filesize") or info.get("filesize_approx") or 0}


def probe_file(file):
    _, probe = tools()
    result = subprocess.run([probe, *LOCAL_INPUT_ARGS, "-v", "error", "-show_streams", "-of", "json", str(file)], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError("تعذّر التحقق من الملف الذي أعاده المصدر.")
    videos = [s for s in json.loads(result.stdout).get("streams", []) if s.get("codec_type") == "video"]
    if not videos:
        return {"width": None, "height": None, "fps": None, "resolution": None}
    video = videos[0]
    width, height = int(video["width"]), int(video["height"])
    rotation = next((s.get("rotation") for s in video.get("side_data_list", []) if "rotation" in s), (video.get("tags") or {}).get("rotate", 0))
    if abs(float(rotation)) % 180 == 90:
        width, height = height, width
    fps = None
    for key in ("avg_frame_rate", "r_frame_rate"):
        try:
            value = float(Fraction(video.get(key) or "0"))
            if value > 0:
                fps = round(value, 3)
                break
        except (ValueError, ZeroDivisionError):
            pass
    return {"width": width, "height": height, "fps": fps, "resolution": min(width, height), "duration": float(video.get("duration") or 0)}


class MediaMetadataPP(yt_dlp.postprocessor.PostProcessor):
    def __init__(self, ydl, sample=None):
        super().__init__(ydl)
        self.sample = sample

    def run(self, info):
        if self.sample:
            info.update(self.sample)
            info["title"] = "مقطع تجربة لُقطة"
        for f in info.get("formats") or [info]:
            if self.sample:
                f.update(self.sample)
            f["luqta_resolution"] = resolution(f)
        return [], info


def extract(ydl, url, download):
    # Exactly one format selection and pre-processing pass per extraction.
    sample = probe_file(Path(__file__).parent / "sample.mp4") if url == (Path(__file__).parent / "sample.mp4").resolve().as_uri() else None
    ydl.add_post_processor(MediaMetadataPP(ydl, sample), when="pre_process")
    raw = ydl.extract_info(url, download=False, process=False)
    validate_media(raw)
    return ydl.process_ie_result(raw, download=download)


class Progress:
    def __init__(self, title, job=None):
        self.title = title
        self.count = 1
        self.streams = {}
        self.last = 0
        self.percent = 0
        self.job = job

    def prepare(self, info, incomplete=False):
        validate_media(info)
        if not incomplete and not self.streams:
            self.count = max(1, len(info.get("requested_formats") or []))
            if self.job:
                verify_spec(info, self.job)
                emit({"status": "extracting", "percent": None, "width": info.get("width"), "height": info.get("height"), "fps": info.get("fps"), "resolution": resolution(info)})

    def download(self, data):
        info = data.get("info_dict") or {}
        key = str(info.get("format_id") or data.get("filename") or "stream")
        total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
        done = data.get("downloaded_bytes") or 0
        finished = data.get("status") == "finished"
        fraction = 1 if finished else min(1, done / total) if total else None
        self.streams[key] = (fraction, done)
        now = time.monotonic()
        if not finished and now - self.last < 0.3:
            return
        self.last = now
        known = all(f is not None for f, _ in self.streams.values())
        if known:
            value = sum(f for f, _ in self.streams.values()) / max(self.count, len(self.streams)) * 100
            self.percent = max(self.percent, min(99, round(value, 1)))
        emit({"status": "downloading", "percent": self.percent if known else None,
              "speed": data.get("speed") or 0, "eta": data.get("eta") or 0,
              "downloaded": sum(n for _, n in self.streams.values()),
              "title": str(info.get("title") or self.title)[:400]})

    def process(self, data):
        if data.get("status") == "started" and data.get("postprocessor") != "MediaMetadata":
            emit({"status": "processing", "percent": None, "speed": 0, "eta": 0,
                  "message": "جاري تجهيز الملف داخل الهاتف"})


def health():
    with SecureYoutubeDL(base_options()) as ydl:
        handlers = list(ydl._request_director.handlers.values())
        network_ready = len(handlers) == 1 and isinstance(handlers[0], SecureUrllibRH)
    ffmpeg, probe = tools()
    ready = all(path and subprocess.run([path, "-version"], capture_output=True, timeout=15).returncode == 0 for path in (ffmpeg, probe))
    qjs = os.environ.get("LUQTA_NATIVE_QJS")
    js = bool(qjs and subprocess.run([qjs, "-e", "console.log('ok')"], capture_output=True, timeout=10).returncode == 0)
    try:
        from yt_dlp.dependencies import yt_dlp_ejs
        ejs = yt_dlp_ejs.version
    except (ImportError, AttributeError):
        ejs = None
    emit({"status": "health", "mode": "engine", "ytdlp": yt_dlp.version.__version__, "ffmpeg": ready,
          "native": True, "quickjs": js, "ejs": ejs, "network_policy": network_ready, "ready": ready and network_ready})


def main():
    install_guards()
    request = json.loads(sys.stdin.readline())
    if request.get("mode") == "health":
        health()
        return
    if request.get("kind") == "image":
        validate_url(request["url"])
        if request.get("mode") == "info":
            emit({"info": images.inspect(request["url"], request.get("cookies"), DiagnosticLog())})
        else:
            images.download(request, emit, probe_file)
        return 0
    sample = request.get("url") == SAMPLE
    url = (Path(__file__).parent / "sample.mp4").resolve().as_uri() if sample else validate_url(request["url"])
    log = DiagnosticLog()
    if request.get("mode") == "info":
        opts = base_options()
        opts.update(logger=log, match_filter=validate_media, enable_file_urls=sample)
        with SecureYoutubeDL(opts) as ydl:
            emit({"info": metadata(extract(ydl, url, False))})
        return
    directory = Path(request["directory"])
    directory.mkdir(parents=True, exist_ok=True)
    for attempt in range(2):
        progress = Progress(request.get("title") or "مقطع فيديو", request)
        opts = download_options(request, directory, recovery=bool(attempt))
        opts.update(logger=log, match_filter=progress.prepare, enable_file_urls=sample,
                    progress_hooks=[progress.download], postprocessor_hooks=[progress.process])
        if not attempt:
            emit({"status": "extracting", "percent": None, "speed": 0, "eta": 0})
        try:
            with SecureYoutubeDL(opts) as ydl:
                details = metadata(extract(ydl, url, True))
            break
        except Exception as exc:
            if attempt or not forbidden(exc):
                emit({"status": "error", "percent": None, "speed": 0, "eta": 0,
                      "error": error_message(exc), "error_detail": safe_error(exc), "warnings": log.warnings})
                return 1
            emit({"status": "retrying", "percent": None, "speed": 0, "eta": 0, "downloaded": 0,
                  "message": "المصدر رفض الرابط؛ جاري جلب رابط جديد وإعادة المحاولة"})
            if youtube_url(request["url"]) and request["format"] != "webm":
                # Do not append HLS fragments to a partially downloaded HTTPS stream.
                shutil.rmtree(directory / ".tmp", ignore_errors=True)
    files = [p for p in directory.iterdir() if p.is_file() and p.suffix.lower() == "." + request["format"]]
    if len(files) != 1:
        raise RuntimeError("لم ينتج التحميل ملفًا واحدًا بالصيغة المطلوبة.")
    file = files[0]
    if not file.stat().st_size:
        raise RuntimeError("المصدر أعاد ملفًا فارغًا.")
    actual = probe_file(file)
    verify_spec(actual, request)
    emit({"status": "done", "percent": 100, "filename": file.name, "size": file.stat().st_size,
          "title": details["title"], "source": details["source"], "speed": 0, "eta": 0, **actual})
    return 0


if __name__ == "__main__":
    try:
        os.setsid()
    except OSError:
        pass
    emit({"pid": os.getpid()})
    try:
        sys.exit(main())
    except Exception as exc:
        emit({"status": "error", "percent": None, "speed": 0, "eta": 0,
              "error": error_message(exc), "error_detail": safe_error(exc)})
        sys.exit(1)
