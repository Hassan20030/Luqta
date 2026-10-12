"""Original image downloads, public page candidates, and validated HTTP resume."""
import hashlib
import json
import os
import re
import struct
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import unquote, urljoin, urlsplit
from urllib.request import Request
from network import public_opener, LOCAL_INPUT_ARGS

from core import tools, validate_url
import social_photos

HEADER_LIMIT = 1024 * 1024
MAX_SIZE = 100 * 1024 * 1024


def open_url(url, referrer=None, headers=None):
    values = {"User-Agent": os.environ.get("LUQTA_USER_AGENT", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"), "Accept-Encoding": "identity"}
    if referrer:
        # Query strings may contain private page tokens; only send the origin.
        p = urlsplit(validate_url(referrer))
        values["Referer"] = f"{p.scheme}://{p.netloc}/"
    values.update(headers or {})
    return public_opener().open(Request(validate_url(url), headers=values), timeout=20)


def dimensions(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24 and data[12:16] == b"IHDR":
        return (*struct.unpack(">II", data[16:24]), "png")
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP" and len(data) >= 30:
        kind = data[12:16]
        if kind == b"VP8X":
            return (1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little"), "webp")
        if kind == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
            w, h = struct.unpack("<HH", data[26:30])
            return (w & 0x3fff, h & 0x3fff, "webp")
        if kind == b"VP8L" and data[20] == 0x2f:
            bits = int.from_bytes(data[21:25], "little")
            return (1 + (bits & 0x3fff), 1 + ((bits >> 14) & 0x3fff), "webp")
    if data.startswith(b"\xff\xd8"):
        p = 2
        while p + 4 <= len(data):
            if data[p] != 0xff:
                break
            while p < len(data) and data[p] == 0xff:
                p += 1
            if p >= len(data):
                break
            marker = data[p]
            p += 1
            if marker in (0xd9, 0xda):
                break
            if marker == 0x01 or 0xd0 <= marker <= 0xd7:
                continue
            if p + 2 > len(data):
                break
            length = int.from_bytes(data[p:p + 2], "big")
            if length < 2 or p + length > len(data):
                break
            if marker in (0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf) and length >= 7:
                h, w = struct.unpack(">HH", data[p + 3:p + 7])
                return (w, h, "jpg")
            p += length
    raise ValueError("لم أجد صورة JPEG أو PNG أو WebP صالحة في الرابط.")


def image_details(data, url, size=0):
    w, h, ext = dimensions(data)
    if w <= 0 or h <= 0 or w * h > 200_000_000:
        raise ValueError("أبعاد الصورة غير صالحة أو أكبر من الحد المدعوم.")
    name = unquote(Path(urlsplit(url).path).name) or "صورة"
    return {"image_url": url, "width": w, "height": h, "ext": ext, "size": size,
            "is_4k": max(w, h) >= 3840, "title": name[:150]}


def read_prefix(url, referrer=None):
    with open_url(url, referrer, {"Range": f"bytes=0-{HEADER_LIMIT - 1}"}) as response:
        data = response.read(4096)
        while len(data) < HEADER_LIMIT:
            try:
                dimensions(data)
                break
            except ValueError:
                chunk = response.read(min(65536, HEADER_LIMIT - len(data)))
                if not chunk:
                    break
                data += chunk
        total = re.search(r"/(\d+)$", response.headers.get("Content-Range", ""))
        size = int(total[1]) if total else int(response.headers.get("Content-Length", "0") or 0)
        return data, response.geturl(), size, response.headers.get_content_charset() or "utf-8"


class PageImages(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.urls, self.title, self.in_title = base, [], "", False

    def add(self, value):
        if value and len(self.urls) < 80:
            url = urljoin(self.base, value)
            if urlsplit(url).scheme in ("http", "https") and url not in self.urls:
                self.urls.append(url)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self.in_title = True
        if tag == "meta" and (a.get("property") or a.get("name") or "").lower() in ("og:image", "og:image:url", "og:image:secure_url", "twitter:image", "twitter:image:src"):
            self.add(a.get("content"))
        if tag == "link" and a.get("rel") == "image_src":
            self.add(a.get("href"))
        if tag in ("img", "source"):
            # Prefer the largest declared srcset; actual dimensions are verified.
            candidates = []
            for item in (a.get("srcset") or a.get("data-srcset") or "").split(","):
                parts = item.strip().split()
                if parts:
                    weight = float(parts[1][:-1]) if len(parts) > 1 and re.fullmatch(r"\d+(?:\.\d+)?[wx]", parts[1]) else 0
                    candidates.append((weight, parts[0]))
            for _, url in sorted(candidates, reverse=True)[:2]:
                self.add(url)
            for key in ("data-original", "data-full", "data-src", "src"):
                self.add(a.get(key))
        if tag == "a" and re.search(r"\.(?:jpe?g|png|webp)$", urlsplit(a.get("href", "")).path, re.I):
            self.add(a.get("href"))

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title = (self.title + data)[:400]


def inspect(url, cookies=None, logger=None):
    source = social_photos.platform(url)
    if source:
        page = None
        # Short share links are resolved once, with every redirect validated.
        if not re.search(r"/(?:p|reel|tv|photo|video)/", urlsplit(url).path):
            data, url, _, charset = read_prefix(url)
            if social_photos.platform(url) != source:
                raise ValueError("الرابط المختصر لا يشير إلى منشور في المصدر نفسه.")
            page = data.decode(charset, errors="replace")
        elif not cookies:
            try:
                data, resolved, _, charset = read_prefix(url)
                if social_photos.platform(resolved) == source:
                    page = data.decode(charset, errors="replace")
            except (OSError, ValueError):
                pass
        result = social_photos.inspect(url, cookies, logger, page)
        for i, item in enumerate(result["images"]):
            if not item.get("width") or not item.get("height"):
                data, resolved, length, _ = read_prefix(item["image_url"], url)
                item.update(image_details(data, resolved, length))
                item["variants"] = [{k: item[k] for k in ("image_url", "width", "height")}]
        first = result["images"][0]
        return {"kind": "image", "thumbnail": first["image_url"], **result, **{k: first[k] for k in ("width", "height", "size", "ext", "is_4k", "image_url")}}
    data, final, size, charset = read_prefix(url)
    try:
        images = [image_details(data, final, size)]
        title = images[0]["title"]
    except ValueError:
        if not re.search(br"<(?:!doctype|html|head|meta|img)\b", data[:10000], re.I):
            raise ValueError("الرابط لا يحتوي صورة مدعومة. استخدم رابط JPEG أو PNG أو WebP مباشرًا.") from None
        page = PageImages(final)
        page.feed(data.decode(charset, errors="replace"))
        images, title = [], page.title.strip() or "صور الصفحة"
        def inspect_candidate(candidate):
            try:
                prefix, resolved, length, _ = read_prefix(candidate, final)
                item = image_details(prefix, resolved, length)
                if min(item["width"], item["height"]) >= 256:
                    return item
            except (OSError, ValueError):
                return None
        with ThreadPoolExecutor(max_workers=4) as pool:
            for item in pool.map(inspect_candidate, page.urls[:12]):
                if item and item["image_url"] not in {v["image_url"] for v in images}:
                    images.append(item)
        if not images:
            raise ValueError("لم تظهر صور قابلة للتنزيل في الصفحة. انسخ رابط الصورة المباشر؛ بعض المنصات تحتاج تسجيل دخول.")
        images.sort(key=lambda i: i["width"] * i["height"], reverse=True)
    for position, item in enumerate(images, 1):
        item.update(id=str(position), position=position, original_width=item["width"], original_height=item["height"], variants=[{k: item[k] for k in ("image_url", "width", "height")}])
    return {"kind": "image", "title": title, "source": urlsplit(final).hostname, "social": False, "post_count": len(images),
            "thumbnail": images[0]["image_url"], "images": images, **{k: images[0][k] for k in ("width", "height", "size", "ext", "is_4k", "image_url")}}


def require_quality(info, quality):
    if quality not in ("best", "lowest", "4k", "240", "360", "480", "720", "1080", "1440", "2160", "3840"):
        raise ValueError("جودة الصورة غير صحيحة.")
    if quality == "4k" and not info["is_4k"]:
        raise ValueError(f"الصورة المتاحة {info['width']}×{info['height']}؛ لا تبلغ 4K. اختر «الأصلية» لحفظها دون تكبير.")


def download(request, emit, probe):
    quality = request.get("quality", "best")
    candidates = request.get("image_variants") or [{"image_url": request.get("image_url") or inspect(request["url"], request.get("cookies"))["image_url"]}]
    candidates.sort(key=lambda i: (i.get("width", 0) * i.get("height", 0)), reverse=quality != "lowest")
    if quality.isdigit():
        large = [i for i in candidates if max(i.get("width", 0), i.get("height", 0)) >= int(quality)]
        if large:
            candidates = sorted(large, key=lambda i: i.get("width", 0) * i.get("height", 0))
    original_width, original_height = request.get("original_width", 0), request.get("original_height", 0)
    source = None
    for candidate in candidates[:6]:
        try:
            url = candidate["image_url"]
            data, url, length, _ = read_prefix(url, request["url"])
            details = image_details(data, url, length)
            if not social_photos.ratio_matches(details["width"], details["height"], original_width, original_height):
                continue
            require_quality(details, quality)
            source = details
            break
        except (OSError, ValueError):
            if len(candidates) == 1:
                raise
    if not source:
        raise ValueError("لم يعرض المصدر الصورة الكاملة بالجودة المطلوبة؛ أعد فحص الرابط.")
    directory = Path(request["directory"])
    directory.mkdir(parents=True, exist_ok=True)
    part, state_file = directory / "image.part", directory / "image.part.json"
    try:
        previous = json.loads(state_file.read_text())
    except (OSError, ValueError):
        previous = {}
    # Only a strong ETag is safe to combine stored bytes with the current file.
    validator = previous.get("etag")
    offset = part.stat().st_size if part.exists() and previous.get("url") == url and validator else 0
    headers = {"Range": f"bytes={offset}-", "If-Range": validator} if offset else {}
    try:
        response = open_url(url, request["url"], headers)
    except HTTPError as exc:
        if exc.code != 416 or not offset:
            raise
        # The source may have changed or the previous transfer already finished.
        response = open_url(url, request["url"])
        offset = 0
    with response:
        total = int(response.headers.get("Content-Length", "0") or 0)
        if response.status == 206:
            match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
            if not match or int(match[1]) != offset or int(match[2]) < offset or int(match[3]) <= int(match[2]):
                raise ValueError("الموقع أعاد نطاق تنزيل غير صالح. أعد المحاولة.")
            total = int(match[3])
            if offset and previous.get("etag") and response.headers.get("ETag") != previous["etag"]:
                raise ValueError("تغيّر ملف الصورة أثناء الاستكمال. ألغِ المهمة وابدأ تحميلًا جديدًا.")
        else:
            offset = 0
        if total > MAX_SIZE:
            raise ValueError("حجم الصورة أكبر من الحد المدعوم: 100 MB.")
        etag = response.headers.get("ETag", "")
        state = {"url": url, "etag": etag if not etag.startswith("W/") else ""}
        started, last, done = time.monotonic(), 0, offset
        with part.open("ab" if offset else "wb") as out:
            state_file.write_text(json.dumps(state))
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if done > MAX_SIZE:
                    raise ValueError("حجم الصورة أكبر من الحد المدعوم: 100 MB.")
                now = time.monotonic()
                if now - last >= 0.3:
                    out.flush()
                    speed = (done - offset) / max(0.001, now - started)
                    emit({"status": "downloading", "percent": min(99, round(done / total * 100, 1)) if total else None,
                          "downloaded": done, "speed": speed, "eta": (total - done) / speed if total and speed else 0})
                    last = now
        if total and done != total:
            raise OSError("توقف نقل الصورة قبل اكتماله؛ يمكنك الاستكمال بإعادة المحاولة.")
    with part.open("rb") as source:
        info = image_details(source.read(HEADER_LIMIT), url, done)
    require_quality(info, quality)
    actual = probe(part)
    if not actual.get("width") or not actual.get("height"):
        raise ValueError("لم يتمكن المحرك من قراءة الصورة المنزّلة.")
    require_quality({"is_4k": max(actual["width"], actual["height"]) >= 3840, **actual}, quality)
    # Keep source bytes and metadata; never resize or re-encode the image.
    safe_title = re.sub(r'[\x00-\x1f/\\:*?"<>|]', "_", request.get("title") or info["title"])[:80].strip(". ") or "صورة"
    safe_title = safe_title.encode("utf-8")[:150].decode("utf-8", errors="ignore")
    file = directory / f"{safe_title} [{hashlib.sha256(url.encode()).hexdigest()[:10]}].{info['ext']}"
    if quality.isdigit() and max(actual["width"], actual["height"]) > int(quality):
        factor = int(quality) / max(actual["width"], actual["height"])
        w, h = max(1, round(actual["width"] * factor)), max(1, round(actual["height"] * factor))
        emit({"status": "processing", "percent": None, "speed": 0, "eta": 0, "message": "جاري تصغير الصورة كاملة دون قص"})
        temporary = directory / ("scaled." + info["ext"])
        ffmpeg, _ = tools()
        result = subprocess.run([ffmpeg, *LOCAL_INPUT_ARGS, "-v", "error", "-y", "-i", str(part), "-vf", f"scale={w}:{h}:flags=lanczos,setsar=1", "-frames:v", "1", "-threads", "1", str(temporary)], capture_output=True, timeout=120)
        if result.returncode or not temporary.is_file():
            raise ValueError("تعذّر تجهيز جودة الصورة المختارة. جرّب الجودة الأصلية.")
        actual = probe(temporary)
        if (actual["width"], actual["height"]) != (w, h):
            raise ValueError("أبعاد الصورة الناتجة لا تطابق الجودة المختارة.")
        temporary.replace(file)
        part.unlink()
    else:
        part.replace(file)
    state_file.unlink(missing_ok=True)
    emit({"status": "done", "kind": "image", "percent": 100, "filename": file.name, "size": file.stat().st_size,
          "title": request.get("title") or info["title"], "source": urlsplit(url).hostname,
          "format": info["ext"], "width": actual["width"], "height": actual["height"], "is_4k": max(actual["width"], actual["height"]) >= 3840,
          "original_width": info["width"], "original_height": info["height"], "fps": None, "speed": 0, "eta": 0})
