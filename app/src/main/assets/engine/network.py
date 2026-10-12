"""Network policy for this worker, including redirects and actual socket peers.

Only the host test harness may enable local HTTP fixtures. Android removes that
environment variable before launching the worker. This is policy enforcement,
not a sandbox for arbitrary Python code or a replacement for dependency updates.
"""
import ipaddress
import os
import socket
import ssl
import sys
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import BaseHandler, HTTPSHandler, ProxyHandler, build_opener

import yt_dlp
from yt_dlp.downloader import get_suitable_downloader
from yt_dlp.downloader.dash import DashSegmentsFD
from yt_dlp.downloader.external import FFmpegFD
from yt_dlp.downloader.hls import HlsFD
from yt_dlp.downloader.http import HttpFD
from yt_dlp.networking._urllib import UrllibRH
from yt_dlp.networking.exceptions import RequestError

SAMPLE_FILE = (Path(__file__).parent / "sample.mp4").resolve().as_uri()
LOCAL_INPUT_ARGS = ["-protocol_whitelist", "file,pipe", "-format_whitelist",
                    "mov,mp4,m4a,3gp,3g2,mj2,matroska,webm,mpegts,mp3,aac,ogg,flac,wav,avi,mpeg,mpegvideo,flv,asf,image2,jpeg_pipe,png_pipe,webp_pipe"]
_installed = False


class NetworkPolicyError(ValueError):
    pass


def local_fixtures():
    return os.environ.get("LUQTA_ALLOW_LOCAL_MEDIA") == "1"


def public_address(value):
    if "%" in value:
        return False
    try:
        address = ipaddress.ip_address(value)
        if isinstance(address, ipaddress.IPv6Address):
            if address.ipv4_mapped:
                address = address.ipv4_mapped
            elif address.sixtofour or address.teredo or address in ipaddress.ip_network("64:ff9b::/96"):
                return False
        return address.is_global and not address.is_multicast and not address.is_unspecified
    except ValueError:
        return False


def validate_url(value, *, allow_sample=False):
    if allow_sample and value == SAMPLE_FILE:
        return value
    if not isinstance(value, str) or not 1 <= len(value) <= 4096 or any(ord(c) < 32 or ord(c) == 127 for c in value) or "\\" in value:
        raise NetworkPolicyError("أدخل رابط HTTPS صحيحًا.")
    value = value.strip()
    try:
        u = urlsplit(value)
        port = u.port
        if u.scheme not in (("http", "https") if local_fixtures() else ("https",)) or not u.hostname or u.username is not None or u.password is not None:
            raise ValueError()
        if not local_fixtures() and port not in (None, 443):
            raise ValueError()
    except ValueError:
        raise NetworkPolicyError("يقبل التطبيق روابط HTTPS فقط، دون بيانات دخول وعلى المنفذ المعتاد.") from None
    if not local_fixtures():
        try:
            addresses = socket.getaddrinfo(u.hostname, port or 443, type=socket.SOCK_STREAM)
        except OSError:
            raise NetworkPolicyError("تعذّر الوصول إلى اسم الموقع. تحقق من الرابط والاتصال.") from None
        if not addresses or any(not public_address(a[4][0]) for a in addresses):
            raise NetworkPolicyError("حُظر اتصال بموقع غير عام أو بعنوان داخل الشبكة المحلية.")
    return value


class RequestPolicy(BaseHandler):
    # Runs for each request issued by urllib, including redirect hops, before
    # cookies or a connection. Other schemes never enter our request handler.
    handler_order = 100

    def https_request(self, request):
        validate_url(request.full_url)
        return request

    http_request = https_request


def public_opener(*handlers):
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return build_opener(ProxyHandler({}), RequestPolicy(), HTTPSHandler(context=context), *handlers)


class SecureUrllibRH(UrllibRH):
    def _create_instance(self, proxies, cookiejar, legacy_ssl_support=None):
        opener = super()._create_instance({}, cookiejar, legacy_ssl_support)
        opener.add_handler(RequestPolicy())
        return opener

    def _send(self, request):
        try:
            validate_url(request.url, allow_sample=self.enable_file_urls)
        except NetworkPolicyError as error:
            raise RequestError(str(error)) from error
        return super()._send(request)

    def _make_sslcontext(self, *args, **kwargs):
        context = super()._make_sslcontext(*args, **kwargs)
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        return context


class SecureYoutubeDL(yt_dlp.YoutubeDL):
    def build_request_director(self, handlers, preferences=None):
        # curl/requests, proxy and impersonation handlers would bypass urllib's
        # redirect policy. Use one controlled transport and system CA validation.
        return super().build_request_director([SecureUrllibRH])

    def dl(self, name, info, subtitle=False, test=False):
        for media in info.get("requested_formats") or [info]:
            validate_url(media.get("url"), allow_sample=self.params.get("enable_file_urls", False))
        downloader = get_suitable_downloader(info, self.params, to_stdout=(name == "-"))
        if downloader not in (HttpFD, HlsFD, DashSegmentsFD):
            raise NetworkPolicyError("طريقة نقل هذا المصدر لا تدعم فحص الاتصال الآمن. اختر صيغة أخرى.")
        return super().dl(name, info, subtitle, test)


def install_guards():
    global _installed
    if _installed:
        return
    _installed = True

    def audit(event, args):
        if event == "socket.connect":
            connection, address = args
            if connection.family in (socket.AF_INET, socket.AF_INET6) and not local_fixtures():
                # create_connection passes the numeric address returned by DNS.
                # Check that actual endpoint, not a second resolution of its name.
                if address[1] != 443 or not public_address(address[0]):
                    raise NetworkPolicyError("حُظر اتصال غير آمن أو بعنوان داخل الشبكة المحلية.")

    sys.addaudithook(audit)

    # All yt-dlp FFmpeg/FFprobe invocations share this Popen class, including
    # codec probing which does not accept postprocessor_args. Keep processing
    # local and reject playlist demuxers which can reference other files.
    original_init = yt_dlp.utils.Popen.__init__

    def local_process(self, args, *remaining, **kwargs):
        if isinstance(args, (list, tuple)) and args:
            executable = Path(os.fsdecode(args[0])).name
            if executable in ("ffmpeg", "ffprobe", "libffmpeg.so", "libffprobe.so") and "-version" not in args:
                if "-i" in args:
                    # FFmpeg input options reset after each input, so merging
                    # video and audio requires protection on both files.
                    protected = [args[0]]
                    for argument in args[1:]:
                        if argument == "-i":
                            protected.extend(LOCAL_INPUT_ARGS)
                        protected.append(argument)
                    args = protected
                else:
                    args = [args[0], *LOCAL_INPUT_ARGS, *args[1:]]
        return original_init(self, args, *remaining, **kwargs)

    yt_dlp.utils.Popen.__init__ = local_process

    def reject_external(*args, **kwargs):
        raise NetworkPolicyError("هذا المسار يحتاج اتصالًا خارج محرك النقل الآمن. اختر صيغة أخرى أو حدّث المحرك.")

    # Native HLS can delegate unsupported manifests to FFmpeg at runtime.
    FFmpegFD.real_download = reject_external
