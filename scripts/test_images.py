"""Real image byte preservation, HTTP range resume and quality regression checks."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / 'app/src/main/assets/engine'
sys.path.insert(0, str(ENGINE))
import images

class Source(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/page':
            data = b'<html><head><title>Public photo</title><meta property="og:image" content="/small.jpg"></head><body><img srcset="/small.jpg 800w, /large.png 4096w"><img src="/large.webp"></body></html>'
            self.send_response(200);self.send_header('Content-Length', str(len(data)));self.end_headers();self.wfile.write(data);return
        if path not in self.server.data: self.send_error(404);return
        data = self.server.data[path];etag = '"' + hashlib.sha256(data).hexdigest() + '"'
        start, end, ranged = 0, len(data)-1, False
        if self.headers.get('Range') and (not self.headers.get('If-Range') or self.headers['If-Range'] == etag):
            a, b = self.headers['Range'].split('=')[1].split('-');start=int(a);end=min(int(b) if b else end, end);ranged=True
        self.server.requests.append((path, start, self.headers.get('If-Range'), ranged))
        if start >= len(data): self.send_error(416);return
        self.send_response(206 if ranged else 200)
        self.send_header('Content-Length',str(end-start+1));self.send_header('ETag',etag)
        if ranged: self.send_header('Content-Range', f'bytes {start if path != "/bad.jpg" or start == 0 else 0}-{end}/{len(data)}')
        self.end_headers()
        try:
            for i in range(start,end+1,32768):
                self.wfile.write(data[i:min(i+32768,end+1)]);self.wfile.flush()
                if not self.headers.get('Range') or start > 0: time.sleep(.004)
        except (BrokenPipeError,ConnectionResetError): pass

class ImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='luqta-images-');cls.root=Path(cls.tmp.name)
        for name, dims in [('large.jpg','3840x2160'),('large.png','4096x2304'),('large.webp','2160x3840'),('small.jpg','800x600')]:
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s='+dims,'-frames:v','1','-threads','1',str(cls.root/name)],check=True)
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Source)
        cls.server.data={'/'+p.name:p.read_bytes() for p in cls.root.iterdir() if p.is_file()}
        cls.server.data['/resume.jpg']=cls.server.data['/large.jpg'] + b'A'*(3*1024*1024)
        cls.server.data['/bad.jpg']=cls.server.data['/resume.jpg']
        cls.server.requests=[]
        threading.Thread(target=cls.server.serve_forever,daemon=True).start()
        cls.base=f'http://127.0.0.1:{cls.server.server_port}'
        cls.env=dict(os.environ,LUQTA_ALLOW_LOCAL_MEDIA='1',LUQTA_NATIVE_FFMPEG='/usr/bin/ffmpeg',LUQTA_NATIVE_FFPROBE='/usr/bin/ffprobe')
    @classmethod
    def tearDownClass(cls): cls.server.shutdown();cls.server.server_close();cls.tmp.cleanup()
    def request(self,path,quality='best',directory=None,mode=None):
        data={'kind':'image','url':self.base+path,'image_url':self.base+path,'quality':quality,'directory':str(directory or Path(tempfile.mkdtemp(dir=self.root)))}
        if mode: data['mode']=mode
        return data
    def run_worker(self,request):
        p=subprocess.run([sys.executable,str(ENGINE/'worker.py')],input=json.dumps(request),text=True,capture_output=True,timeout=45,env=self.env)
        return p.returncode,[json.loads(l) for l in p.stdout.splitlines() if l.startswith('{')]
    def pause(self,request):
        p=subprocess.Popen([sys.executable,str(ENGINE/'worker.py')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=self.env)
        p.stdin.write(json.dumps(request)+'\n');p.stdin.close()
        while True:
            line=p.stdout.readline();self.assertTrue(line,'Worker stopped before a progress event')
            if json.loads(line).get('status')=='downloading': break
        p.terminate();p.wait(timeout=5);p.stdout.close();p.stderr.close()
        part=Path(request['directory'])/'image.part';self.assertGreater(part.stat().st_size,0);self.assertLess(part.stat().st_size,len(self.server.data[urlsplit(request['url']).path]))
    def test_original_images_and_actual_dimensions(self):
        for name,dims in [('large.jpg',(3840,2160)),('large.png',(4096,2304)),('large.webp',(2160,3840))]:
            with self.subTest(name=name):
                req=self.request('/'+name,'4k');code,events=self.run_worker(req);self.assertEqual(code,0,events)
                done=events[-1];self.assertEqual((done['width'],done['height']),dims);self.assertIsNone(done['fps']);self.assertTrue(done['is_4k'])
                self.assertEqual((Path(req['directory'])/done['filename']).read_bytes(),self.server.data['/'+name]);self.assertFalse((Path(req['directory'])/'image.part').exists())
    def test_page_candidates_largest_first_and_selection(self):
        code,events=self.run_worker(self.request('/page',mode='info'));self.assertEqual(code,0,events)
        info=events[-1]['info'];self.assertEqual(info['title'],'Public photo');self.assertEqual(len(info['images']),3);self.assertTrue(info['image_url'].endswith('/large.png'))
        req=self.request('/page','4k');req['image_url']=info['images'][1]['image_url'];code,events=self.run_worker(req);self.assertEqual(code,0,events)
        self.assertEqual((Path(req['directory'])/events[-1]['filename']).read_bytes(),self.server.data['/large.webp'])
    def test_4k_rejects_small_image_without_upscaling(self):
        req=self.request('/small.jpg','4k');code,events=self.run_worker(req);self.assertNotEqual(code,0);self.assertIn('800×600',events[-1]['error']);self.assertFalse(list(Path(req['directory']).glob('*.jpg')))
        req['quality']='best';code,events=self.run_worker(req);self.assertEqual(code,0);self.assertEqual(events[-1]['width'],800);self.assertFalse(events[-1]['is_4k'])
    def test_pause_resume_reuses_bytes(self):
        req=self.request('/resume.jpg','4k');self.pause(req);self.server.requests.clear();code,events=self.run_worker(req);self.assertEqual(code,0,events)
        self.assertTrue(any(p=='/resume.jpg' and start>0 and validator and ranged for p,start,validator,ranged in self.server.requests))
        self.assertEqual((Path(req['directory'])/events[-1]['filename']).read_bytes(),self.server.data['/resume.jpg'])
    def test_changed_etag_restarts_instead_of_mixing_files(self):
        req=self.request('/resume.jpg','4k');self.pause(req)
        previous=self.server.data['/resume.jpg'];self.server.data['/resume.jpg']=self.server.data['/large.jpg']+b'B'*(2*1024*1024)
        try:
            self.server.requests.clear();code,events=self.run_worker(req);self.assertEqual(code,0,events)
            self.assertTrue(any(validator and not ranged for _,_,validator,ranged in self.server.requests))
            self.assertEqual((Path(req['directory'])/events[-1]['filename']).read_bytes(),self.server.data['/resume.jpg'])
        finally: self.server.data['/resume.jpg']=previous
    def test_bad_content_range_is_rejected(self):
        req=self.request('/bad.jpg');self.pause(req);code,events=self.run_worker(req);self.assertNotEqual(code,0);self.assertIn('نطاق',events[-1]['error']);self.assertFalse(list(Path(req['directory']).glob('*.jpg')))
    def test_non_image_and_unsupported_data(self):
        for data in (b'<html>403</html>',b'GIF89a'+b'0'*40,b'\xff\xd8\xff\xc0\x00\x00'):
            with self.assertRaises(ValueError): images.dimensions(data)

if __name__=='__main__': unittest.main(verbosity=2)
