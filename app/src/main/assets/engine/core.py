"""Shared validation and yt-dlp options. No shell commands accept user input."""
import os
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit
from network import validate_url

FORMATS = {"mp4", "webm", "mp3", "m4a"}
QUALITIES = {"best", "2160", "1080", "720", "480"}
FPS_CHOICES = {"best", "120", "60", "30"}

def resolution(info):
    dimensions = [int(info[k]) for k in ("width", "height") if info.get(k) and float(info[k]) > 0]
    return min(dimensions) if dimensions else None

def verify_spec(info, job):
    if job["format"] in ("mp3", "m4a"):
        return
    actual = resolution(info)
    if job["quality"] != "best" and actual != int(job["quality"]):
        text = f"{actual}p" if actual else "غير معروفة"
        raise ValueError(f"المصدر أعاد دقة {text}. طلبك {job['quality']}p؛ اختر الدقة المتاحة أو «الأفضل».")
    wanted = job.get("fps", "best")
    if wanted != "best" and (not info.get("fps") or abs(float(info["fps"]) - int(wanted)) > 0.5):
        text = f"{info['fps']} FPS" if info.get("fps") else "غير معروف"
        raise ValueError(f"المصدر أعاد معدل إطارات {text}. طلبك {wanted} FPS؛ اختر معدلًا متاحًا أو «الأعلى المتاح».")

def safe_error(error):
    text = re.sub(r"https?://\S+", "[رابط المصدر]", str(error))
    text = re.sub(r"(?i)(token|cookie|password|authorization)\s*[:=]\s*\S+", r"\1=[hidden]", text)
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    return text[-600:]

def forbidden(error):
    return bool(re.search(r"(?i)(?:HTTP (?:Error|error)[: ]+403\b|403: Forbidden)", str(error)))

def error_message(error):
    text = str(error)
    if forbidden(error):
        return "الموقع رفض تنزيل الملف (403). حدّث المحرك، وتأكد أن المحتوى متاح لك في تطبيق المصدر."
    if re.search(r"(?i)(429|too many requests)", text):
        return "الموقع أوقف الطلبات مؤقتًا لكثرتها (429). انتظر قليلًا قبل إعادة المحاولة."
    if re.search(r"(?i)(timed out|timeout|network is unreachable|name resolution)", text):
        return "تعذّر الاتصال بالمصدر. تحقق من الإنترنت ثم أعد المحاولة."
    if "Requested format is not available" in text:
        return "الدقة ومعدل الإطارات المطلوبان غير متاحين معًا بهذه الصيغة. اختر خيارات متاحة أو «الأفضل» و«الأعلى المتاح»."
    return safe_error(error)

def youtube_url(value):
    host = (urlsplit(value).hostname or "").lower()
    return host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com") or host == "youtube-nocookie.com" or host.endswith(".youtube-nocookie.com")

def tools():
    native_ffmpeg = os.environ.get("LUQTA_NATIVE_FFMPEG")
    native_probe = os.environ.get("LUQTA_NATIVE_FFPROBE")
    if native_ffmpeg and native_probe:
        return native_ffmpeg, native_probe
    return shutil.which("ffmpeg"), shutil.which("ffprobe")

def base_options():
    result = {"quiet": True, "noplaylist": True, "socket_timeout": 20,
              "retries": 3, "fragment_retries": 3, "extractor_retries": 2,
              "nocheckcertificate": False, "skip_unavailable_fragments": False,
              "proxy": "", "compat_opts": {"no-certifi"},
              "external_downloader": "native", "hls_prefer_native": True}
    ffmpeg, _ = tools()
    if ffmpeg:
        result["ffmpeg_location"] = ffmpeg if os.environ.get("LUQTA_NATIVE_FFMPEG") else str(Path(ffmpeg).parent)
    qjs = os.environ.get("LUQTA_NATIVE_QJS")
    if qjs:
        result["js_runtimes"] = {"quickjs": {"path": qjs}}
    node = shutil.which("node")
    if node and not qjs:
        result["js_runtimes"] = {"node": {"path": node}}
    if os.environ.get("LUQTA_ENGINE_CACHE"):
        result["cachedir"] = os.environ["LUQTA_ENGINE_CACHE"]
    return result

def download_options(job, directory, recovery=False):
    fmt, quality, fps = job["format"], job["quality"], job.get("fps", "best")
    if fmt not in FORMATS or quality not in QUALITIES or fps not in FPS_CHOICES:
        raise ValueError("خيارات التحميل غير صحيحة.")
    ffmpeg, ffprobe = tools()
    if not ffmpeg or not ffprobe:
        raise ValueError("ثبّت FFmpeg وFFprobe أولًا لتفعيل الدمج واستخراج الصوت.")
    options = base_options()
    fragments = job.get("fragments", 4)
    if isinstance(fragments, bool) or fragments not in (1, 2, 4, 8):
        raise ValueError("إعداد سرعة التحميل غير صحيح.")
    options.update({"paths": {"home": str(directory), "temp": str(directory / ".tmp")},
                    "outtmpl": "%(title).150B [%(id)s].%(ext)s",
                    "continuedl": True, "overwrites": False, "max_filesize": 2 * 1024**3,
                    "concurrent_fragment_downloads": fragments})
    # This is the short edge for landscape and portrait video, populated by the
    # shared metadata processor. An explicit request never falls back silently.
    limit = "" if quality == "best" else f"[luqta_resolution={int(quality)}]"
    if fps != "best":
        limit += f"[fps>={int(fps)-0.5}][fps<={int(fps)+0.5}]"
    # A fresh page extraction avoids reusing an expired media URL. YouTube's
    # Safari HLS is a bounded fallback, without requesting missing-token formats.
    youtube = youtube_url(job["url"])
    hls = "[protocol^=m3u8]" if recovery and youtube and fmt != "webm" else ""
    if youtube:
        options["http_chunk_size"] = 8 * 1024**2
    if hls:
        options["extractor_args"] = {"youtube": {"player_client": ["web_safari"]}}
    if fmt in ("mp3", "m4a"):
        options["format"] = f"ba{hls}/b{hls}" if hls else "bestaudio[ext=m4a]/bestaudio/best" if fmt == "m4a" else "bestaudio/best"
        options["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": fmt, "preferredquality": "192"}]
    elif fmt == "webm":
        options["format"] = f"bv[ext=webm]{limit}+ba[ext=webm]/b[ext=webm]{limit}"
        options["merge_output_format"] = "webm"
    else:
        options["format"] = f"bv*{hls}{limit}+ba{hls}/b{hls}{limit}"
        options["merge_output_format"] = "mp4"
        options["postprocessors"] = [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}]
    if fmt in ("mp4", "webm"):
        options["format_sort"] = ["res", "fps", "vcodec:h264", "acodec:aac"]
        options["format_sort_force"] = True
    return options
