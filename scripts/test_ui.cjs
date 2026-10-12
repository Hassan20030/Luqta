// DOM/IPC regression checks. These do not run Android or verify rendered layout.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM, VirtualConsole } = require('jsdom');
const assets = path.resolve(__dirname, '../app/src/main/assets');
const errors = [], calls = [];
const dom = new JSDOM(fs.readFileSync(path.join(assets, 'index.html'), 'utf8'), {
  url: 'https://appassets.androidplatform.net/index.html', runScripts: 'outside-only',
  virtualConsole: new VirtualConsole().on('jsdomError', e => errors.push(e.message))
});
const w = dom.window, doc = w.document;
w.HTMLElement.prototype.scrollIntoView = () => {};
w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
w.HTMLDialogElement.prototype.close = function (value) { this.returnValue=value||'';this.open = false;this.dispatchEvent(new w.Event('close')); };
const id = 'b'.repeat(32), doneId = 'c'.repeat(32);
const base = { title: 'مقطع اختبار', format: 'mp4', quality: '720', created_at: Date.now(), size: 0, percent: null, speed: 0 };
let jobs = [
  { ...base, id: 'a'.repeat(32), status: 'retrying', message: 'المصدر رفض الرابط؛ جاري جلب رابط جديد وإعادة المحاولة' },
  { ...base, id, status: 'error', retryable: true, error: 'الموقع رفض تنزيل الفيديو (403). حدّث المحرك، وتأكد أن المقطع متاح لك في تطبيق المصدر.', error_detail: 'ERROR: unable to download video data: HTTP Error 403: Forbidden' },
  { ...base, id: doneId, status: 'done', percent: 100, size: 1543410, width: 2160, height: 3840, fps: 120, resolution: 2160 }
];
let health = { mode: 'engine', ytdlp: '2026.08.19', ffmpeg: true, quickjs: true, ejs: '0.8.0' };
w.NativeLuqta = {
  readClipboard: () => 'https://media.example/test.mp4',
  postMessage(text) {
    const request = JSON.parse(text); calls.push(request);
    w.setTimeout(() => {
      let data;
      try {
        if (request.path === '/api/engine/update') { health = { ...health, ytdlp: '2099.01.01' }; data = health; }
        else if (request.path === '/api/health') data = health;
        else if (request.path === '/api/settings') data = request.method === 'GET' ? {fragments:4,restore:true,auto_save:false} : request.body;
        else if (request.path === '/api/storage' || request.path === '/api/storage/clean') data = request.path.endsWith('/clean') ? {removed:512,storage:{free:1e9,temporary:0,retained:512,unsaved:1024}} : {free:1e9,temporary:512,retained:512,unsaved:1024};
        else if (request.path === '/api/jobs' && request.method === 'GET') data = { jobs };
        else if (request.path === '/api/source/login') data = {ok:true};
        else if (request.path === '/api/history/clear') {jobs=jobs.filter(j=>['queued','downloading','paused'].includes(j.status));data={ok:true};}
        else if (request.path === '/api/info' && request.body.kind === 'image') data = {inspection_id:'inspection-fixture',kind:'image',title:'صورة 4K',source:'Public',width:3840,height:2160,is_4k:true,ext:'jpg',image_url:'https://media.example/large.jpg',images:[{image_url:'https://media.example/large.jpg',width:3840,height:2160,ext:'jpg',is_4k:true},{image_url:'https://media.example/small.png',width:800,height:600,ext:'png',is_4k:false}]};
        else if (request.path === '/api/info') data = { title: '<img src=x onerror=alert(1)>', source: 'Generic', duration: 3, width: 2160, height: 3840, fps: 60, resolution: 2160, resolutions: [2160, 1080, 720], variants: [{ width: 2160, height: 3840, resolution: 2160, fps: 60, ext: 'mp4' }, { width: 1080, height: 1920, resolution: 1080, fps: 120, ext: 'mp4' }, { width: 720, height: 1280, resolution: 720, fps: 30, ext: 'mp4' }], size: 1543410 };
        else if (request.path === '/api/jobs') {const indices=request.body.kind==='image'?request.body.selected_images:[0];for(const index of indices)jobs.unshift({...base,id:(request.body.kind==='image'?String(index+1):'d').repeat(32),status:'queued',...request.body});data={job_id:'d'.repeat(32),count:indices.length};}
        else {
          const match = request.path.match(/^\/api\/jobs\/([a-f0-9]{32})\/(retry|pause|resume|cancel|export|open|share|remove)$/);
          assert.ok(match, 'Unexpected IPC route: ' + request.path);
          const job = jobs.find(j => j.id === match[1]); assert.ok(job);
          if (match[2] === 'retry') { job.status = 'queued'; job.retryable = false; delete job.error; delete job.error_detail; }
          if (match[2] === 'pause') { job.status = 'paused'; job.resumable = true; }
          if (match[2] === 'resume') { job.status = 'queued'; job.resumable = false; }
          if (match[2] === 'cancel') { job.status = 'cancelled'; job.retryable = true; }
          if(match[2] === 'remove') jobs=jobs.filter(j=>j.id!==match[1]);
          data = { ok: true, saved_to: 'Movies/Luqta' };
        }
        w.__luqtaReply(request.id, { ok: true, data: JSON.parse(JSON.stringify(data)) });
      } catch (e) { errors.push(e.message); w.__luqtaReply(request.id, { ok: false, error: e.message }); }
    }, request.path === '/api/info' ? 30 : 0);
  }
};
const $ = selector => doc.querySelector(selector);
const click = selector => { const e = $(selector); assert.ok(e, selector); e.click(); };
async function until(condition) {
  const deadline = Date.now() + 2500;
  while (!condition()) { if (Date.now() > deadline) throw new Error('UI assertion timed out: ' + condition); await new Promise(resolve => setTimeout(resolve, 5)); }
}

(async () => {
  try {
    w.eval(fs.readFileSync(path.join(assets, 'app.js'), 'utf8'));
    await until(() => $('.job.error'));
    assert.ok($('.job.retrying .progress-track.indeterminate'));
    assert.ok($('.job.error').textContent.includes('403'));
    click('.job.error summary'); assert.ok($('.job.error details').open);
    assert.equal(doc.querySelectorAll('.job.done .job-actions button').length, 4);
    assert.ok($('.job.done .job-meta').textContent.includes('2160×3840'));
    assert.ok($('.job.done .job-meta').textContent.includes('120 FPS'));
    click('.job.error button'); await until(() => !$('.job.error'));
    assert.equal(doc.querySelectorAll('.job.queued').length, 1);
    click('.job.queued button'); await until(() => $('.job.paused'));
    assert.ok($('.job.paused').textContent.includes('استكمال'));
    click('.job.paused button'); await until(() => $('.job.queued'));
    doc.querySelectorAll('.job.queued button')[1].click(); await until(() => $('.job.cancelled'));
    click('.job.done button'); await until(() => $('#toast').textContent.includes('Movies/Luqta'));
    doc.querySelectorAll('.job.done button')[1].click();
    doc.querySelectorAll('.job.done button')[2].click();
    click('#paste-btn'); assert.equal($('#url-input').value, 'https://media.example/test.mp4');
    const beforeInvalid = calls.filter(c => c.path === '/api/info').length;
    for (const url of ['http://media.example/test.mp4', 'https://media.example:80/test.mp4', 'https://user:pass@media.example/test.mp4']) {
      w.__luqtaImportLink(url); click('#action-btn');
      await until(() => !$('#action-btn').disabled);
      assert.equal(calls.filter(c => c.path === '/api/info').length, beforeInvalid, 'Unsafe URL must not reach native inspection');
    }
    click('#paste-btn');
    click('#action-btn');
    w.__luqtaImportLink('https://media.example/changed.mp4');
    await until(() => !$('#action-btn').disabled);
    assert.ok($('#video-preview').hidden, 'A stale inspection must not overwrite a newly shared link');
    assert.equal($('#url-input').value, 'https://media.example/changed.mp4');
    click('#paste-btn'); click('#action-btn'); await until(() => !$('#video-preview').hidden);
    assert.equal($('#preview-title').textContent, '<img src=x onerror=alert(1)>');
    assert.equal($('#preview-title img'), null, 'Source titles must be text, not executable HTML');
    assert.ok($('#preview-meta').textContent.includes('2160×3840'));
    assert.ok(! $('[data-quality="2160"]').disabled, 'A portrait 2160 source must enable 4K');
    click('[data-quality="2160"]');
    assert.ok($('#fps-select option[value="120"]').disabled, '4K60 and 1080p120 must not imply 4K120');
    assert.ok(!$('#fps-select option[value="60"]').disabled);
    click('[data-quality="1080"]');
    assert.ok(!$('#fps-select option[value="120"]').disabled);
    $('#fps-select').value = '120'; $('#fps-select').dispatchEvent(new w.Event('change'));
    click('#action-btn');
    await until(() => doc.querySelectorAll('.job.queued').length === 1);
    assert.equal(calls.find(c => c.path === '/api/jobs' && c.method === 'POST').body.quality, '1080');
    assert.equal(calls.find(c => c.path === '/api/jobs' && c.method === 'POST').body.fps, '120');
    click('.mobile-settings'); assert.ok($('#settings-dialog').open);
    click('#update-engine'); await until(() => $('#native-update-result').textContent.includes('2099.01.01'));
    assert.ok($('#engine-detail').textContent.includes('2099.01.01'), 'Update health must refresh the settings display');
    assert.equal($('#engine-url, #engine-token'), null);
    click('[data-close]'); assert.ok(!$('#settings-dialog').open);
    click('#theme-toggle'); assert.ok(doc.body.classList.contains('light'));
    click('[data-type="image"]'); assert.ok($('#video-preview').hidden); assert.ok($('#fps-section').hidden);
    click('#action-btn'); await until(() => !$('#video-preview').hidden);
    assert.equal(calls.filter(c=>c.path==='/api/info').at(-1).body.kind,'image');
    assert.ok($('#preview-meta').textContent.includes('3840×2160'));
    assert.ok(!$('#image-section').hidden); assert.equal($('#image-select').options.length,3);
    assert.equal($('#image-select').value,'all');assert.ok($('#action-btn').textContent.includes('كل الصور (2)'));
    assert.ok($('#image-quality option[value="720"]'));assert.equal($('#image-quality option[value="3840"]'),null,'No 4K option when an album image is smaller');
    $('#image-quality').value='720';$('#image-quality').dispatchEvent(new w.Event('change'));
    click('#action-btn');await until(()=>calls.filter(c=>c.path==='/api/jobs' && c.method==='POST').length===2);
    const album=calls.filter(c=>c.path==='/api/jobs' && c.method==='POST').at(-1).body;assert.deepEqual(album.selected_images,[0,1]);assert.equal(album.inspection_id,'inspection-fixture');assert.equal(album.quality,'720');assert.equal(album.image_url,undefined);
    await until(()=>!$('#action-btn').disabled);
    $('#image-select').value='0';$('#image-select').dispatchEvent(new w.Event('change'));assert.ok($('#image-quality option[value="3840"]'));
    $('#image-quality').value='best';$('#image-quality').dispatchEvent(new w.Event('change'));click('#action-btn');await until(()=>calls.filter(c=>c.path==='/api/jobs' && c.method==='POST').length===3);
    const imageJob=calls.filter(c=>c.path==='/api/jobs' && c.method==='POST').at(-1).body;assert.deepEqual(imageJob.selected_images,[0]);assert.equal(imageJob.kind,'image');assert.equal(imageJob.quality,'best');assert.equal(imageJob.fps,'best');
    assert.equal(doc.querySelectorAll('.album-photo').length,2);
    const doneButtons=doc.querySelectorAll('.job.done button');doneButtons[3].click();await until(()=>$('#confirm-dialog').open);
    assert.ok($('#confirm-message').textContent.includes('حذف'));click('#confirm-reject');assert.ok($('.job.done'),'Cancel keeps the history row');
    doneButtons[3].click();await until(()=>$('#confirm-dialog').open);click('#confirm-accept');await until(()=>!$('.job.done'));
    click('[data-login="instagram"]');await until(()=>calls.some(c=>c.path==='/api/source/login'));
    $('#fragments-select').value='8';$('#fragments-select').dispatchEvent(new w.Event('change'));await until(()=>$('#settings-result').textContent.includes('حُفظت'));
    assert.equal(calls.filter(c=>c.path==='/api/settings' && c.method==='POST').at(-1).body.fragments,8);
    click('#clean-storage');await until(()=>$('#toast').textContent.includes('512'));
    assert.deepEqual(errors, []);
    for (const action of ['retry', 'pause', 'resume', 'cancel', 'export', 'open', 'share', 'remove']) assert.ok(calls.some(c => c.path.endsWith('/' + action)));
    console.log(JSON.stringify({ result: 'passed', ipcCalls: calls.length, errors, check: 'DOM and mocked Android IPC' }));
  } finally { dom.window.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
