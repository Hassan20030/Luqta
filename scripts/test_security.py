"""Adversarial policy checks, real TLS/redirects/fragments and secret packaging.

The TLS fixture maps its test hostname to loopback and classifies only that
fixture IP as public. Production address enforcement is tested separately in a
fresh worker process, without those substitutions or local-media overrides.
"""
import contextlib
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "app/src/main/assets/engine"
sys.path.insert(0, str(ENGINE))
import images
import network
from core import base_options


class TLSFixture(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.server.hits.append(self.path)
        if self.path.startswith("/redirect-"):
            target = "http://source.luqta.test/captured" if self.path == "/redirect-http" else "https://10.0.0.5/captured"
            self.send_response(302);self.send_header("Location", target);self.end_headers();return
        if self.path == "/page":
            data = b'<html><title>Unsafe media fixture</title><video src="http://source.luqta.test/captured.mp4"></video></html>'
            mime = "text/html"
        elif self.path == "/hls.m3u8":
            data = b"#EXTM3U\n#EXT-X-TARGETDURATION:3\n#EXTINF:3,\nhttp://source.luqta.test/captured.ts\n#EXT-X-ENDLIST\n"
            mime = "application/vnd.apple.mpegurl"
        elif self.path == "/hls-redirect.m3u8":
            data = b"#EXTM3U\n#EXT-X-TARGETDURATION:3\n#EXTINF:3,\nhttps://source.luqta.test/redirect-private\n#EXT-X-ENDLIST\n"
            mime = "application/vnd.apple.mpegurl"
        else:
            data = (ENGINE / "sample.mp4").read_bytes();mime = "video/mp4"
        self.send_response(200);self.send_header("Content-Type", mime);self.send_header("Content-Length", str(len(data)));self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass


class SecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.cert = cls.root / "fixture-cert.pem"
        key = cls.root / "fixture-key.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-keyout", str(key), "-out", str(cls.cert), "-subj", "/CN=source.luqta.test", "-addext", "subjectAltName=DNS:source.luqta.test"], check=True, capture_output=True)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), TLSFixture)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cls.cert), str(key))
        cls.server.socket = context.wrap_socket(cls.server.socket, server_side=True)
        cls.server.hits = []
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True);cls.thread.start()
        cls.original_dns = staticmethod(socket.getaddrinfo)
        cls.original_public = staticmethod(network.public_address)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.temp.cleanup()

    def setUp(self):
        self.server.hits.clear()

    @contextlib.contextmanager
    def fixture_network(self, trust=True):
        def dns(host, port, *args, **kwargs):
            if host in ("source.luqta.test", "wrong.luqta.test"):
                return self.original_dns("127.0.0.1", self.server.server_port, *args, **kwargs)
            return self.original_dns(host, port, *args, **kwargs)
        with patch.dict(os.environ, {"SSL_CERT_FILE": str(self.cert) if trust else "", "LUQTA_ALLOW_LOCAL_MEDIA": "0"}), patch.object(socket, "getaddrinfo", side_effect=dns), patch.object(network, "public_address", side_effect=lambda value: value == "127.0.0.1" or self.original_public(value)):
            yield

    def ydl(self, **options):
        params = base_options()
        params.update(logger=Quiet(), retries=0, fragment_retries=0, extractor_retries=0, outtmpl=str(self.root / "output.%(ext)s"))
        params.update(options)
        return network.SecureYoutubeDL(params)

    def test_initial_urls_reject_http_credentials_private_ports_and_schemes(self):
        with patch.dict(os.environ, {"LUQTA_ALLOW_LOCAL_MEDIA": "0"}):
            for value in ("http://example.com/a", "https://127.0.0.1/a", "https://10.0.0.1/a", "https://[::1]/a", "https://[::ffff:127.0.0.1]/a", "https://169.254.169.254/a", "https://user:pass@example.com/a", "https://@example.com/a", "https://example.com:80/a", "file:///etc/passwd", "ftp://example.com/a", "https://example.com/\nsecret", "https://example.com\\@localhost/a"):
                with self.subTest(url=value), self.assertRaises(network.NetworkPolicyError):
                    network.validate_url(value)

    def test_ipv6_transition_and_multicast_are_not_public_peers(self):
        for value in ("::ffff:10.0.0.1", "2002:7f00:1::", "64:ff9b::7f00:1", "ff02::1", "224.0.0.1", "fe80::1%lo"):
            self.assertFalse(network.public_address(value), value)
        self.assertTrue(network.public_address("2606:4700:4700::1111"))

    def test_ytdlp_only_uses_guarded_transport(self):
        with self.ydl() as ydl:
            handlers = list(ydl._request_director.handlers.values())
            self.assertEqual(len(handlers), 1)
            self.assertIsInstance(handlers[0], network.SecureUrllibRH)

    def test_real_https_with_trusted_certificate_succeeds_for_both_transports(self):
        with self.fixture_network():
            with images.open_url("https://source.luqta.test/sample.mp4") as response:
                self.assertEqual(response.read(), (ENGINE / "sample.mp4").read_bytes())
            with self.ydl() as ydl, ydl.urlopen("https://source.luqta.test/sample.mp4") as response:
                self.assertEqual(response.read(), (ENGINE / "sample.mp4").read_bytes())

    def test_untrusted_tls_certificate_is_rejected_for_both_transports(self):
        with self.fixture_network(trust=False):
            with self.assertRaises(Exception):
                images.open_url("https://source.luqta.test/sample.mp4")
            with self.ydl() as ydl, self.assertRaises(Exception):
                ydl.urlopen("https://source.luqta.test/sample.mp4")
        self.assertEqual(self.server.hits, [])

    def test_tls_hostname_mismatch_is_rejected_for_both_transports(self):
        with self.fixture_network():
            with self.assertRaises(Exception):
                images.open_url("https://wrong.luqta.test/sample.mp4")
            with self.ydl() as ydl, self.assertRaises(Exception):
                ydl.urlopen("https://wrong.luqta.test/sample.mp4")
        self.assertEqual(self.server.hits, [])

    def test_https_redirect_cannot_downgrade_or_enter_private_network(self):
        with self.fixture_network():
            for path in ("/redirect-http", "/redirect-private"):
                with self.subTest(path=path), self.assertRaises(Exception):
                    images.open_url("https://source.luqta.test" + path)
                with self.ydl() as ydl, self.assertRaises(Exception):
                    ydl.urlopen("https://source.luqta.test" + path)
        self.assertEqual(self.server.hits, ["/redirect-http", "/redirect-http", "/redirect-private", "/redirect-private"])

    def test_extracted_media_url_cannot_use_http(self):
        with self.fixture_network(), self.ydl() as ydl, self.assertRaisesRegex(Exception, "HTTPS"):
            ydl.extract_info("https://source.luqta.test/page", download=True)
        self.assertNotIn("/captured.mp4", self.server.hits)
        self.assertFalse((self.root / "output.mp4").exists())

    def test_hls_segments_and_their_redirects_follow_same_policy(self):
        with self.fixture_network():
            for path in ("/hls.m3u8", "/hls-redirect.m3u8"):
                with self.subTest(path=path), self.ydl() as ydl, self.assertRaisesRegex(Exception, "HTTPS|غير عام|الشبكة المحلية"):
                    ydl.extract_info("https://source.luqta.test" + path, download=True)
        self.assertNotIn("/captured.ts", self.server.hits)
        self.assertNotIn("/captured", self.server.hits)
        self.assertIn("/redirect-private", self.server.hits)

    def test_dns_rebinding_is_blocked_at_actual_connect_in_fresh_process(self):
        # URL validation sees a public DNS answer, but the actual peer is private.
        code = """
from unittest.mock import patch
import network, socket
network.install_guards()
with patch.object(socket, 'getaddrinfo', return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))]):
    network.validate_url('https://rebinding.test/a')
for address in [('127.0.0.1',443),('10.0.0.1',443),('93.184.216.34',80),('localhost',443)]:
    with socket.socket() as connection:
        try: connection.connect(address)
        except network.NetworkPolicyError: continue
        raise AssertionError('Connection escaped endpoint policy')
print('actual endpoint checks passed')
"""
        env = dict(os.environ, PYTHONPATH=str(ENGINE), LUQTA_ALLOW_LOCAL_MEDIA="0")
        result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("passed", result.stdout)

    def test_only_bundled_sample_can_be_read_as_file_url(self):
        with self.ydl(enable_file_urls=True) as ydl:
            with ydl.urlopen(network.SAMPLE_FILE) as response:
                self.assertEqual(response.read(), (ENGINE / "sample.mp4").read_bytes())
            with self.assertRaises(Exception):
                ydl.urlopen("file:///etc/passwd")

    def test_ffprobe_rejects_local_playlist_with_network_reference(self):
        malicious = self.root / "pretend-video.mp4"
        malicious.write_text("#EXTM3U\n#EXT-X-TARGETDURATION:3\n#EXTINF:3,\nhttps://source.luqta.test/captured\n#EXT-X-ENDLIST\n")
        result = subprocess.run(["ffprobe", *network.LOCAL_INPUT_ARGS, "-v", "error", "-show_streams", str(malicious)], capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.server.hits, [])

    def test_ytdlp_ffmpeg_protects_both_merge_inputs(self):
        malicious = self.root / "malicious-audio.m3u8"
        malicious.write_text("#EXTM3U\n#EXT-X-TARGETDURATION:3\n#EXTINF:3,\nhttps://source.luqta.test/captured\n#EXT-X-ENDLIST\n")
        code = """
import network, yt_dlp, sys, subprocess
network.install_guards()
out, error, status = yt_dlp.utils.Popen.run(['ffmpeg', '-v', 'error', '-y', '-i', sys.argv[1], '-i', sys.argv[2], '-c', 'copy', sys.argv[3]], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
assert status != 0, error
assert 'not on whitelist' in error, error
print('Both inputs protected')
"""
        result = subprocess.run([sys.executable, "-c", code, str(ENGINE / "sample.mp4"), str(malicious), str(self.root / "merge.mp4")], env=dict(os.environ, PYTHONPATH=str(ENGINE), LUQTA_ALLOW_LOCAL_MEDIA="0"), capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.server.hits, [])

    def test_build_requires_private_external_key_and_source_has_no_keystore(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/build.py"), "--sdk", "/missing", "--ecj", "/missing"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("external --keystore", result.stderr)
        # Package in an isolated project, so a planted private key inside an
        # allowlisted directory makes packaging fail instead of sharing it.
        import importlib.util
        spec = importlib.util.spec_from_file_location("source_packaging", ROOT / "scripts/package_source.py")
        module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        fake = self.root / "project"
        for directory in module.DIRECTORIES:
            (fake / directory).mkdir(parents=True)
        (fake / "app/secret.jks").write_bytes(b"private key fixture")
        with patch.object(module, "ROOT", fake), self.assertRaises(ValueError):
            module.package()


class Quiet:
    def debug(self, message): pass
    info = warning = error = debug


if __name__ == "__main__":
    unittest.main(verbosity=2)
