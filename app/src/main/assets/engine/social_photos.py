"""Read ordered post images, never Open Graph thumbnails, using yt-dlp sessions."""
import json
import re
from html.parser import HTMLParser
from http.cookiejar import Cookie
from urllib.parse import urlsplit

from yt_dlp.extractor.instagram import InstagramIE
from yt_dlp.extractor.tiktok import TikTokIE

from core import base_options, validate_url
from network import SecureYoutubeDL

MAX_IMAGES = 50


def platform(url):
    host = (urlsplit(url).hostname or "").lower()
    for domain, name in (("instagram.com", "instagram"), ("tiktok.com", "tiktok")):
        if host == domain or host.endswith("." + domain):
            return name


def ratio_matches(width, height, original_width, original_height):
    if not all((width, height, original_width, original_height)):
        return True
    return abs((width / height) / (original_width / original_height) - 1) <= 0.025


def photo(candidates, width=0, height=0, identity=""):
    width, height = int(width or 0), int(height or 0)
    variants, seen = [], set()
    for candidate in candidates:
        url = candidate.get("url") or candidate.get("src")
        if not isinstance(url, str) or not url.startswith(("https://", "http://")) or url in seen:
            continue
        seen.add(url)
        w = int(candidate.get("width") or candidate.get("config_width") or width or 0)
        h = int(candidate.get("height") or candidate.get("config_height") or height or 0)
        if w > 0 and h > 0:
            variants.append({"image_url": url, "width": w, "height": h})
    variants.sort(key=lambda v: v["width"] * v["height"], reverse=True)
    if not variants:
        raise ValueError("لم يعرض المصدر رابط الصورة الكاملة.")
    width, height = width or variants[0]["width"], height or variants[0]["height"]
    variants = [v for v in variants if ratio_matches(v["width"], v["height"], width, height)]
    if not variants:
        raise ValueError("المصدر أعاد معاينة مقصوصة فقط. اربط حساب المصدر أو حدّث المحرك ثم افحص الرابط مجددًا.")
    return {"id": str(identity), "original_width": width, "original_height": height,
            "variants": variants, **variants[0], "is_4k": max(variants[0]["width"], variants[0]["height"]) >= 3840, "ext": "image", "size": 0}


def instagram_post(post):
    nodes = post.get("carousel_media")
    if nodes is None:
        edges = (post.get("edge_sidecar_to_children") or {}).get("edges")
        nodes = [e["node"] for e in edges] if edges is not None else [post]
    if not nodes or len(nodes) > MAX_IMAGES:
        raise ValueError("عدد عناصر الألبوم غير مدعوم؛ الحد 50 صورة.")
    images, skipped = [], 0
    for position, node in enumerate(nodes, 1):
        if node.get("is_video") or node.get("media_type") == 2 or node.get("video_versions"):
            skipped += 1
            continue
        candidates = (node.get("image_versions2") or {}).get("candidates") or node.get("display_resources") or []
        dims = node.get("dimensions") or {}
        width, height = node.get("original_width") or dims.get("width") or 0, node.get("original_height") or dims.get("height") or 0
        if not candidates and node.get("display_url"):
            candidates = [{"url": node["display_url"], "width": width, "height": height}]
        item = photo(candidates, width, height, node.get("pk") or node.get("id") or position)
        item["position"] = position
        images.append(item)
    if not images:
        raise ValueError("المنشور لا يحتوي صورًا. اختر وضع الفيديو إذا كان المقطع فيديو.")
    caption = post.get("caption") or {}
    edges = (post.get("edge_media_to_caption") or {}).get("edges") or []
    title = caption.get("text") if isinstance(caption, dict) else str(caption)
    title = title or (edges[0].get("node", {}).get("text") if edges else None) or "صور إنستقرام"
    return {"images": images, "title": title[:400], "skipped_videos": skipped, "post_count": len(nodes)}


def tiktok_post(post):
    album = post.get("imagePost") or post.get("image_post_info") or {}
    nodes = album.get("images") or []
    if not nodes or len(nodes) > MAX_IMAGES:
        raise ValueError("لم يعرض تيك توك صور المنشور؛ اختر رابط منشور صور واحد (حتى 50 صورة).")
    images = []
    for position, node in enumerate(nodes, 1):
        full = node.get("imageURL") or node.get("image_url") or {}
        width, height = node.get("imageWidth") or node.get("width") or full.get("width") or 0, node.get("imageHeight") or node.get("height") or full.get("height") or 0
        urls = full.get("urlList") or full.get("url_list") or []
        if not urls:
            raise ValueError(f"المصدر لم يعرض الصورة رقم {position}؛ أعد فحص الرابط.")
        # URL mirrors are full images; covers, thumbnails and profile images are ignored.
        if width and height:
            item = photo([{"url": u, "width": width, "height": height} for u in urls], width, height, position)
        else:
            item = {"id": str(position), "image_url": urls[0], "variants": [{"image_url": u} for u in urls], "width": 0, "height": 0, "original_width": 0, "original_height": 0, "is_4k": False, "ext": "image", "size": 0}
        item["position"] = position
        images.append(item)
    return {"images": images, "title": str(post.get("desc") or "صور تيك توك")[:400], "post_count": len(images), "skipped_videos": 0}


class JsonScripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.capture, self.parts, self.payloads = False, [], []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "script" and (values.get("type") == "application/json" or values.get("id") in ("SIGI_STATE", "__UNIVERSAL_DATA_FOR_REHYDRATION__")):
            self.capture, self.parts = True, []

    def handle_data(self, data):
        if self.capture:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.capture:
            try:
                self.payloads.append(json.loads("".join(self.parts)))
            except ValueError:
                pass
            self.capture = False


def embedded_post(text, source, identity):
    parser = JsonScripts()
    parser.feed(text)
    def walk(node):
        if isinstance(node, dict):
            # Match the requested post so recommendations and avatars cannot enter the album.
            if source == "instagram" and (node.get("shortcode") == identity or node.get("code") == identity) and (node.get("image_versions2") or node.get("display_resources") or node.get("carousel_media") or node.get("edge_sidecar_to_children")):
                return node
            if source == "tiktok" and str(node.get("id") or node.get("aweme_id")) == identity and (node.get("imagePost") or node.get("image_post_info")):
                return node
            for value in node.values():
                found = walk(value)
                if found:
                    return found
        elif isinstance(node, list):
            for value in node:
                found = walk(value)
                if found:
                    return found
    for payload in parser.payloads:
        found = walk(payload)
        if found:
            return found


class ProductFound(Exception):
    def __init__(self, product):
        self.product = product


class InstagramPhotos(InstagramIE):
    # Reuse upstream authentication and extraction, then handle image candidates
    # before yt-dlp's video format selection rejects a photo-only post.
    def _extract_product(self, product_info, video_id=None, get_comments=True):
        raise ProductFound(product_info)


def session(source, cookies, logger):
    opts = base_options()
    opts.update(logger=logger)
    ydl = SecureYoutubeDL(opts)
    domain = ".instagram.com" if source == "instagram" else ".tiktok.com"
    for pair in (cookies or "").split(";"):
        name, sep, value = pair.strip().partition("=")
        if sep and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", name):
            ydl.cookiejar.set_cookie(Cookie(0, name, value, None, False, domain, True, True, "/", True, True, None, True, None, None, {}))
    return ydl


def inspect(url, cookies=None, logger=None, page=None):
    source = platform(url)
    path = urlsplit(url).path
    match = re.search(r"/(?:p|reel|tv)/([A-Za-z0-9_-]+)", path) if source == "instagram" else re.search(r"/(?:photo|video)/(\d+)", path)
    if not match:
        raise ValueError("استخدم رابط منشور صور واحد؛ روابط الحسابات والقصص غير مفعلة.")
    identity = match[1]
    product = embedded_post(page, source, identity) if page else None
    if not product:
        try:
            with session(source, cookies, logger) as ydl:
                if source == "instagram":
                    extractor = InstagramPhotos(ydl)
                    try:
                        extractor.extract(url)
                    except ProductFound as found:
                        product = found.product
                else:
                    extractor = TikTokIE(ydl)
                    extractor.initialize()
                    canonical = url.replace("/photo/", "/video/", 1)
                    product, status = extractor._extract_web_data_and_status(canonical, identity)
                    if status:
                        raise ValueError("المنشور غير متاح من تيك توك.")
        except Exception as exc:
            raise ValueError("لم يعرض المصدر صور المنشور الكاملة. اربط حساب المصدر من الإعدادات عند الحاجة، ثم افحص الرابط مجددًا. لن تُنزّل معاينة مربعة بدل الصورة.") from exc
    if not product:
        raise ValueError("لم يعرض المصدر صور المنشور الكاملة؛ لن تُنزّل صورة المعاينة بدلها.")
    details = instagram_post(product) if source == "instagram" else tiktok_post(product)
    return {"source": source, "social": True, **details}
