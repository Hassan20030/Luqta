"""Controlled platform schemas and actual full-image downloads; no live account."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_images
import images
import social_photos as social
import worker

class SocialPhotoTests(test_images.ImageTests):
    # Use the HTTP/photo fixtures without inheriting their test cases.
    def test_instagram_album_keeps_order_and_excludes_square_crops(self):
        posts=[]
        for n in range(10):
            posts.append({'pk':str(n),'media_type':1,'original_width':2160,'original_height':3840,
                'image_versions2':{'candidates':[{'url':self.base+'/small.jpg','width':600,'height':600},{'url':self.base+'/large.webp?index='+str(n),'width':2160,'height':3840}]}})
        post={'code':'TestAlbum','carousel_media':posts,'caption':{'text':'Ten photos'}}
        data=social.instagram_post(post);self.assertEqual(len(data['images']),10)
        self.assertEqual([p['position'] for p in data['images']],list(range(1,11)))
        for photo in data['images']:
            self.assertEqual(len(photo['variants']),1);self.assertIn('large.webp',photo['image_url'])
        html='<html><meta property="og:image" content="/small.jpg"><script type="application/json">'+json.dumps({'data':{'requested':post,'recommendation':{'code':'Other','carousel_media':posts[:1]}}})+'</script></html>'
        result=social.inspect('https://www.instagram.com/p/TestAlbum/',page=html)
        self.assertEqual(len(result['images']),10);self.assertEqual(result['title'],'Ten photos')
        for photo in result['images']:
            req=self.request('/large.webp');req.update(image_url=photo['image_url'],image_variants=photo['variants'],original_width=2160,original_height=3840,title=f"{photo['position']:02d} TestAlbum")
            code,events=self.run_worker(req);self.assertEqual(code,0,events);self.assertEqual((events[-1]['width'],events[-1]['height']),(2160,3840))
            self.assertEqual((Path(req['directory'])/events[-1]['filename']).read_bytes(),self.server.data['/large.webp'])
    def test_tiktok_album_uses_images_not_cover_or_thumbnail(self):
        nodes=[{'imageWidth':3840,'imageHeight':2160,'imageURL':{'urlList':[self.base+'/large.jpg?index='+str(n)]},'thumbnail':{'urlList':[self.base+'/small.jpg']}} for n in range(10)]
        post={'id':'123456789','desc':'Ten TikTok photos','imagePost':{'images':nodes},'video':{'cover':self.base+'/small.jpg'}}
        html='<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'+json.dumps({'__DEFAULT_SCOPE__':{'webapp.video-detail':{'itemInfo':{'itemStruct':post}}}})+'</script>'
        result=social.inspect('https://www.tiktok.com/@test/photo/123456789',page=html)
        self.assertEqual(len(result['images']),10);self.assertTrue(all('large.jpg' in p['image_url'] for p in result['images']))
    def test_production_inspection_reads_post_data_before_preview(self):
        post={'code':'TestAlbum','original_width':2160,'original_height':3840,'image_versions2':{'candidates':[{'url':self.base+'/large.webp','width':2160,'height':3840}]}}
        page=('<meta property="og:image" content="'+self.base+'/small.jpg"><script type="application/json">'+json.dumps({'post':post})+'</script>').encode()
        url='https://www.instagram.com/p/TestAlbum/'
        with patch.object(images,'read_prefix',return_value=(page,url,len(page),'utf-8')),patch.object(social.InstagramPhotos,'extract',side_effect=AssertionError('Post metadata should already be found')):
            info=images.inspect(url)
        self.assertEqual(info['image_url'],self.base+'/large.webp');self.assertEqual((info['width'],info['height']),(2160,3840));self.assertTrue(info['social'])
    def test_no_social_metadata_never_falls_back_to_open_graph_crop(self):
        with patch.object(social.InstagramPhotos,'extract',side_effect=ValueError('login required')):
            with self.assertRaisesRegex(ValueError,'لن تُنزّل معاينة'):
                social.inspect('https://www.instagram.com/p/Test/',page='<meta property="og:image" content="'+self.base+'/small.jpg">')
    def test_photo_capture_contract_matches_bundled_ytdlp(self):
        post={'image_versions2':{'candidates':[]}}
        extractor=social.InstagramPhotos()
        with self.assertRaises(social.ProductFound) as captured:extractor._extract_product(post)
        self.assertIs(captured.exception.product,post)
    def test_only_crop_candidates_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'مقصوصة'):
            social.photo([{'url':self.base+'/small.jpg','width':600,'height':600}],1080,1920)
    def test_lower_quality_retains_top_and_bottom_of_the_scene(self):
        import subprocess
        from PIL import Image
        source=self.root/'full-frame.png'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=1080x1920','-vf','drawbox=x=0:y=0:w=iw:h=300:color=red:t=fill,drawbox=x=0:y=1620:w=iw:h=300:color=lime:t=fill','-frames:v','1','-threads','1',str(source)],check=True)
        self.server.data['/full-frame.png']=source.read_bytes()
        req=self.request('/full-frame.png','720');req.update(original_width=1080,original_height=1920)
        code,events=self.run_worker(req);self.assertEqual(code,0,events)
        with Image.open(Path(req['directory'])/events[-1]['filename']) as picture:
            picture=picture.convert('RGB');w,h=picture.size;self.assertEqual(h,720)
            top,bottom=picture.getpixel((w//2,20)),picture.getpixel((w//2,h-20))
            self.assertGreater(top[0],200);self.assertLess(top[1],30);self.assertGreater(bottom[1],200);self.assertLess(bottom[0],30)
    def test_requested_quality_preserves_full_frame_and_never_upscales(self):
        for quality in ('240','480','720','1080','1440','2160'):
            req=self.request('/large.webp',quality);code,events=self.run_worker(req);self.assertEqual(code,0,events)
            done=events[-1];self.assertEqual(max(done['width'],done['height']),int(quality));self.assertAlmostEqual(done['width']/done['height'],2160/3840,delta=.004)
            self.assertTrue(any(e.get('status')=='processing' for e in events))
        req=self.request('/small.jpg','3840');code,events=self.run_worker(req);self.assertEqual(code,0);self.assertEqual((events[-1]['width'],events[-1]['height']),(800,600))
        self.assertEqual((Path(req['directory'])/events[-1]['filename']).read_bytes(),self.server.data['/small.jpg'])
    def test_actual_square_bytes_are_rejected_despite_portrait_metadata(self):
        req=self.request('/large.png');req['image_variants']=[{'image_url':self.base+'/small.jpg','width':2160,'height':3840}];req['original_width']=2160;req['original_height']=3840
        code,events=self.run_worker(req);self.assertNotEqual(code,0);self.assertIn('الصورة الكاملة',events[-1]['error'])
    def test_mixed_instagram_album_skips_video_and_preserves_positions(self):
        candidate={'url':self.base+'/large.jpg','width':3840,'height':2160}
        photo={'media_type':1,'original_width':3840,'original_height':2160,'image_versions2':{'candidates':[candidate]}}
        result=social.instagram_post({'carousel_media':[photo,{'media_type':2},photo]})
        self.assertEqual([p['position'] for p in result['images']],[1,3]);self.assertEqual(result['post_count'],3);self.assertEqual(result['skipped_videos'],1)
    def test_auth_cookies_are_scoped_to_source(self):
        with social.session('instagram','sessionid=private; csrftoken=token',worker.DiagnosticLog()) as ydl:
            cookies=list(ydl.cookiejar);self.assertEqual(len(cookies),2);self.assertTrue(all(c.domain=='.instagram.com' and c.secure for c in cookies))
            self.assertEqual(ydl.cookiejar.get_cookie_header(self.base+'/large.jpg'),None)

# Avoid running the inherited image checks twice in this suite.
for name in list(test_images.ImageTests.__dict__):
    if name.startswith('test_') and name not in SocialPhotoTests.__dict__:setattr(SocialPhotoTests,name,None)
if __name__=='__main__': unittest.main(verbosity=2)
