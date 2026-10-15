(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const all = selector => Array.from(document.querySelectorAll(selector));
  const state = { health: null, info: null, quality: 'best', imageQuality: 'best', imageIndex: 'all', fps: 'best', type: 'video', filter: 'all', busy: false, jobs: [], refreshing: false };
  const statuses = { queued: 'في الانتظار', extracting: 'جاري قراءة المصدر', retrying: 'جاري جلب رابط جديد وإعادة المحاولة', downloading: 'جاري التحميل', processing: 'جاري تجهيز الملف داخل الهاتف', done: 'اكتمل تجهيز الملف', error: 'تعذّر التحميل', cancelled: 'أُلغي التحميل', paused: 'موقوف مؤقتًا — الأجزاء محفوظة للاستكمال' };
  const active = job => ['queued', 'extracting', 'retrying', 'downloading', 'processing'].includes(job.status);
  const pending = new Map(), expandedErrors = new Set();
  let next = 0, toastTimer, pollTimer, inspectionVersion = 0;

  function api(path, method = 'GET', body = {}) {
    return new Promise((resolve, reject) => {
      const id = 'n-' + (++next);
      const timeout = path.endsWith('/tiktok') ? 1800000 : path === '/api/engine/update' ? 180000 : path === '/api/info' ? 150000 : path === '/api/health' ? 100000 : 30000;
      const timer = setTimeout(() => { pending.delete(id); reject(new Error('استغرقت العملية وقتًا طويلًا. تحقق من الاتصال ثم جرّب مجددًا.')); }, timeout);
      pending.set(id, { resolve, reject, timer });
      try { NativeLuqta.postMessage(JSON.stringify({ id, path, method, body })); }
      catch { clearTimeout(timer); pending.delete(id); reject(new Error('تعذّر الاتصال بمحرك الهاتف. أعد فتح التطبيق.')); }
    });
  }
  window.__luqtaReply = (id, result) => {
    const request = pending.get(id);
    if (!request) return;
    pending.delete(id); clearTimeout(request.timer);
    result.ok ? request.resolve(result.data) : request.reject(new Error(result.error || 'تعذّر تنفيذ العملية.'));
  };
  window.__luqtaImportLink = url => { $('url-input').value = url; invalidate(); $('new-section').scrollIntoView(); $('url-input').focus(); };
  const icon = name => { const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'), use = document.createElementNS('http://www.w3.org/2000/svg', 'use'); use.setAttribute('href', '#i-' + name); svg.append(use); return svg; };
  const elem = (tag, cls, content) => { const e = document.createElement(tag); if (cls) e.className = cls; if (content !== undefined) e.textContent = content; return e; };
  function brandIcon(platform) { const svg = icon(platform); svg.classList.add('brand-icon'); svg.setAttribute('aria-hidden', 'true'); svg.setAttribute('focusable', 'false'); return svg; }
  function sourceLabel(source) {
    const text = String(source || 'رابط مباشر'), value = text.trim().toLowerCase();
    const platform = /^(youtube|youtu\.be)(?:$|[: _-])/.test(value) || value === 'يوتيوب' ? 'youtube' : /^tiktok(?:$|[: _-])/.test(value) || value === 'تيك توك' ? 'tiktok' : /^instagram(?:$|[: _-])/.test(value) || value === 'إنستقرام' || value === 'انستقرام' ? 'instagram' : null;
    const label = elem('span', 'source-label');
    if (platform) label.append(brandIcon(platform));
    label.append(elem('span', '', text)); return label;
  }
  function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 4200); }
  function error(message = '') { $('error-box').textContent = message; $('error-box').hidden = !message; }
  function size(value) { const n = Number(value); if (!(n > 0) || !Number.isFinite(n)) return ''; const units = ['B', 'KB', 'MB', 'GB'], i = Math.max(0, Math.min(3, Math.floor(Math.log(n) / Math.log(1024)))); return (n / 1024 ** i).toFixed(i > 1 ? 1 : 0) + ' ' + units[i]; }
  function duration(n) { return n > 0 ? Math.floor(n / 60) + ':' + String(Math.floor(n % 60)).padStart(2, '0') : ''; }
  function dimensions(width, height) { return width && height ? '\u2066' + width + '×' + height + '\u2069' : ''; }
  function normalizeUrl(value) { const u = new URL(value.trim()); if (u.protocol !== 'https:' || u.port || u.username || u.password || /[\x00-\x1f\x7f\\]/.test(value)) throw new Error('يقبل التطبيق روابط HTTPS فقط، دون بيانات دخول وعلى المنفذ المعتاد.'); return u.href; }
  function selectedImages() { return state.info?.images ? state.imageIndex === 'all' ? state.info.images : [state.info.images[Number(state.imageIndex)]] : []; }
  function confirmRemove(message) { return new Promise(resolve => { const dialog = $('confirm-dialog'); $('confirm-message').textContent = message; dialog.returnValue = ''; dialog.onclose = () => resolve(dialog.returnValue === 'yes'); dialog.showModal(); }); }
  $('confirm-accept').onclick = () => $('confirm-dialog').close('yes');
  $('confirm-reject').onclick = () => $('confirm-dialog').close('no');
  function actionText() {
    const ready = !!state.info;
    $('action-btn').disabled = state.busy || !state.health;
    $('action-btn').querySelector('span').textContent = state.busy ? (ready ? 'جاري بدء التحميل…' : 'جاري فحص الرابط…') : ready ? (state.type === 'image' ? selectedImages().length > 1 ? 'تحميل كل الصور (' + selectedImages().length + ')' : 'تحميل الصورة' : state.type === 'audio' ? 'تحميل الصوت' : 'تحميل الفيديو') : 'فحص الرابط';
    $('action-btn').querySelector('use').setAttribute('href', state.busy ? '#i-clock' : ready ? '#i-download' : '#i-link');
    document.querySelector('.step-number').firstChild.textContent = ready ? '02 ' : '01 ';
  }
  function options() {
    all('[data-type]').forEach(b => b.classList.toggle('active', b.dataset.type === state.type));
    const select = $('format-select'), prior = select.value;
    const values = state.type === 'image' ? [['image', 'الصيغة الأصلية — JPEG / PNG / WebP']] : state.type === 'audio' ? [['mp3', 'MP3 — جودة 192 kbps'], ['m4a', 'M4A — أسرع عند توفر الصوت الأصلي']] : [['mp4', 'MP4 — متوافق مع أغلب الأجهزة'], ['webm', 'WebM — ترميز VP9 أو AV1']];
    select.replaceChildren(...values.map(([value, label]) => { const option = elem('option', '', label); option.value = value; return option; }));
    if (values.some(([v]) => v === prior)) select.value = prior;
    $('quality-options').hidden = state.type !== 'video';
    document.querySelector('.quality-heading').hidden = state.type !== 'video';
    $('fps-section').hidden = state.type !== 'video';
    $('image-section').hidden = state.type !== 'image';
    $('form-note').lastChild.textContent = state.type === 'image' ? ' الجودة الأصلية تحفظ الملف كاملًا كما وفره المصدر. الجودات الأقل تصغّر الصورة دون قص أو تكبير؛ 4K يحتاج مصدرًا بهذه الدقة.' : ' الدقة وFPS يُحفظان من المصدر. الاختيارات غير المتاحة تُعطّل؛ لا توجد زيادة اصطناعية للجودة أو الإطارات.';
    if (state.type === 'image') {
      const selected = selectedImages(), maximum = selected.length ? Math.min(...selected.map(p => Math.max(p.width || 0, p.height || 0))) : 0;
      const values = [240,360,480,720,1080,1440,2160,3840].filter(n => n <= maximum).map(n => [String(n), n + ' px' + (n === 3840 ? ' — 4K' : '')]);
      values.push(['best', 'أعلى جودة أصلية' + (maximum ? ' — حتى ' + maximum + ' px' : '')]);
      const quality = $('image-quality'); quality.replaceChildren(...values.map(([v,t]) => { const o=elem('option','',t);o.value=v;return o; }));
      if (!values.some(([v]) => v === state.imageQuality)) state.imageQuality = 'best'; quality.value = state.imageQuality;
      $('image-note').textContent = selected.length > 1 ? 'صور المنشور: ' + selected.length + '. تُحمّل بترتيبها؛ الجودة المختارة تحدّد حد الضلع الطويل وتحفظ الصورة كاملة. لكل صورة تقدم مستقل في السجل.' : 'تُحفظ الصورة كاملة بنسبة أبعادها. الجودة الأصلية تحتفظ بملف المصدر، والجودات الأقل تصغّر الصورة دون قص.';
      actionText(); return;
    }
    const variants = (state.info?.variants || []).filter(v => select.value !== 'webm' || v.ext === 'webm');
    const resolutions = [...new Set(variants.map(v => v.resolution).filter(Boolean))];
    if (state.info && state.type === 'video' && state.quality !== 'best' && !resolutions.includes(Number(state.quality))) {
      toast('الدقة ' + state.quality + 'p غير متاحة بهذه الصيغة؛ اختيرت أفضل دقة متاحة.'); state.quality = 'best';
    }
    all('[data-quality]').forEach(b => { b.disabled = b.dataset.quality !== 'best' && (!state.info || !resolutions.includes(Number(b.dataset.quality))); b.classList.toggle('active', b.dataset.quality === state.quality); });
    const rates = variants.filter(v => state.quality === 'best' || v.resolution === Number(state.quality)).map(v => Number(v.fps));
    const fps = $('fps-select');
    Array.from(fps.options).forEach(option => option.disabled = option.value !== 'best' && !rates.some(rate => rate > 0 && Math.abs(rate - Number(option.value)) <= 0.5));
    if (fps.querySelector('option[value="' + state.fps + '"]').disabled) { toast(state.fps + ' FPS غير متاح للدقة والصيغة المختارتين؛ اختير الأعلى المتاح.'); state.fps = 'best'; }
    fps.value = state.fps;
    $('fps-note').textContent = 'يُحفظ معدل المصدر الأصلي. 120 FPS متاح فقط عندما يوفّره المصدر لهذه الدقة.';
    $('quality-note').textContent = state.info && !resolutions.length ? 'المصدر لم يعلن الدقة؛ «الأفضل» يحفظ المتاح' : state.quality === 'best' ? 'أعلى دقة متاحة؛ قد تعني ملفًا أكبر' : 'الدقة المحددة مطلوبة؛ لا تُخفض تلقائيًا';
    if (state.quality === 'best' && state.fps !== 'best') {
      const available = variants.filter(v => v.resolution && Math.abs(Number(v.fps) - Number(state.fps)) <= 0.5).map(v => v.resolution);
      if (available.length) $('quality-note').textContent = 'أعلى دقة عند ' + state.fps + ' FPS: ' + Math.max(...available) + 'p';
    }
    actionText();
  }
  function showInfo(info) {
    state.info = info; $('video-preview').hidden = false;
    $('preview-title').textContent = info.title || 'ملف فيديو'; $('preview-source').replaceChildren(sourceLabel(info.source));
    const image = info.kind === 'image';
    $('preview-meta').textContent = [duration(info.duration), dimensions(info.width, info.height), info.fps ? info.fps + ' FPS' : '', size(info.size), image ? (info.is_4k ? '4K أو أعلى — أصلية' : 'أقل من 4K — أصلية') : info.resolutions?.length ? 'الدقات: ' + info.resolutions.join(' / ') + 'p' : 'الدقة غير معلنة'].filter(Boolean).join(' • ');
    if (image) {
      const select = $('image-select'), children = info.images.map((item, i) => { const option = elem('option', '', 'الصورة ' + (item.position || i + 1) + ' • ' + dimensions(item.width, item.height) + (item.is_4k ? ' • 4K+' : '')); option.value = String(i); return option; });
      if(info.images.length > 1){const option=elem('option','','كل صور المنشور ('+info.images.length+')');option.value='all';children.unshift(option);} else state.imageIndex='0';
      select.replaceChildren(...children); select.value = String(state.imageIndex);
      const gallery=$('album-preview');gallery.replaceChildren();
      info.images.forEach((item,i) => {const button=elem('button','album-photo'+(state.imageIndex==='all'||Number(state.imageIndex)===i?' selected':''));button.type='button';const img=elem('img');try{img.src=normalizeUrl(item.image_url);}catch{}img.alt='الصورة '+(i+1);img.referrerPolicy='no-referrer';button.append(img,elem('span','','الصورة '+(item.position||i+1)));button.onclick=()=>{state.imageIndex=String(i);showInfo({...state.info,...item,thumbnail:item.image_url});};gallery.append(button);});
    }
    const thumb = $('preview-thumb'); thumb.replaceChildren(icon(image ? 'image' : 'play'));
    if (info.thumbnail) { try { const img = elem('img'); img.src = normalizeUrl(info.thumbnail); img.alt = ''; img.referrerPolicy = 'no-referrer'; img.onerror = () => thumb.replaceChildren(icon('play')); thumb.replaceChildren(img); } catch {} }
    options();
  }
  function invalidate() { inspectionVersion++; state.info = null; state.imageIndex = 'all'; state.fps = 'best'; $('image-select').replaceChildren(); $('album-preview').replaceChildren(); $('video-preview').hidden = true; error(); options(); }
  async function inspect() {
    let url;
    try { url = normalizeUrl($('url-input').value); } catch { error('ألصق رابطًا صحيحًا أولًا.'); return; }
    const version = ++inspectionVersion; state.busy = true; actionText(); error();
    try { const info = await api('/api/info', 'POST', { url, kind: state.type === 'image' ? 'image' : 'video' }); if (version === inspectionVersion) showInfo({ ...info, url }); }
    catch (e) { if (version === inspectionVersion) error(e.message); }
    finally { state.busy = false; actionText(); }
  }
  async function startDownload() {
    if (!state.info || state.busy) return;
    state.busy = true; actionText(); error();
    try {
      const result = await api('/api/jobs', 'POST', { url: state.info.url, title: state.info.title, kind: state.type === 'image' ? 'image' : 'video', inspection_id: state.type === 'image' ? state.info.inspection_id : undefined, selected_images: state.type === 'image' ? state.imageIndex === 'all' ? state.info.images.map((p,i)=>i) : [Number(state.imageIndex)] : undefined, format: $('format-select').value, quality: state.type === 'image' ? state.imageQuality : state.quality, fps: state.type === 'video' ? state.fps : 'best' });
      await refreshJobs(); toast(result.count > 1 ? 'أُضيفت ' + result.count + ' صور إلى قائمة التحميل بترتيبها.' : 'أُضيف الملف إلى قائمة التحميل.'); $('downloads-section').scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (e) { error(e.message); }
    finally { state.busy = false; actionText(); }
  }
  function jobAction(job, action, label) {
    const button = elem('button', 'save-btn', label);
    button.onclick = async () => {
      if(action === 'remove' && !await confirmRemove('حذف «'+job.title+'» من السجل؟')) return;
      button.disabled = true;
      try {
        const result = await api('/api/jobs/' + job.id + '/' + action, 'POST');
        if (action === 'export') toast('تم الحفظ في ' + result.saved_to);
        if (action === 'tiktok') toast(result.message || 'تم إنشاء نسخة متوافقة مع تيك توك.');
        if (action === 'retry') toast('جاري إعادة المحاولة برابط جديد.');
        if (action === 'pause') toast('أُوقف التحميل مؤقتًا؛ الأجزاء محفوظة.');
        if (action === 'resume') toast('جاري استكمال التحميل.');
        if (['retry', 'cancel', 'pause', 'resume', 'export', 'remove'].includes(action)) await refreshJobs();
      } catch (e) { toast(e.message); }
      finally { button.disabled = false; }
    };
    return button;
  }
  function renderJobs() {
    const jobs = [...state.jobs].sort((a, b) => b.created_at - a.created_at);
    $('side-count').textContent = String(jobs.length); $('history-count').textContent = String(jobs.length);
    const visible = jobs.filter(j => state.filter === 'all' || (state.filter === 'active' ? active(j) : j.status === state.filter));
    const list = $('jobs-list'); list.replaceChildren(); $('empty-state').hidden = !!visible.length;
    $('empty-state').querySelector('h3').textContent = jobs.length ? 'ما فيه تحميلات في هذا القسم' : 'أول لقطة تنتظرك';
    $('empty-state').querySelector('p').textContent = jobs.length ? 'اختر قسمًا ثانيًا لعرض بقية السجل.' : 'ابدأ برابط فيديو، وبتلقى تحميلاتك هنا.';
    visible.forEach(job => {
      const card = elem('article', 'job ' + job.status), ji = elem('span', 'job-icon'); ji.append(icon(job.kind === 'image' ? 'image' : ['mp3', 'm4a'].includes(job.format) ? 'music' : 'play'));
      const content = elem('div', 'job-info'); content.append(elem('h3', '', job.title || 'مقطع فيديو'));
      const date = new Date(job.created_at).toLocaleDateString('ar-SA', { month: 'short', day: 'numeric' });
      const video = ['mp4', 'webm'].includes(job.format);
      const pixels = (video || job.kind === 'image') && job.width && job.height ? dimensions(job.width, job.height) : video ? (job.quality === 'best' ? 'طلب أعلى دقة' : 'طلب ' + job.quality + 'p') : job.kind === 'image' ? (job.quality === '4k' ? 'طلب صورة 4K' : 'الصورة الأصلية') : '';
      const rate = video && job.fps ? job.fps + ' FPS' : video && job.requested_fps && job.requested_fps !== 'best' ? 'طلب ' + job.requested_fps + ' FPS' : '';
      const meta = elem('div', 'job-meta');
      if (job.source) meta.append(sourceLabel(job.source));
      meta.append(elem('span', '', [(job.format || '').toUpperCase(), pixels, rate, job.album_count > 1 ? 'صورة ' + job.album_position + '/' + job.album_count : '', size(job.size), date].filter(Boolean).join(' • '))); content.append(meta);
      const status = elem('div', 'job-status'); status.append(elem('span', '', job.error || job.save_error || (job.resume_requested ? 'جاري الاستكمال بعد توقف المحرك…' : job.message || (job.export_uri ? 'محفوظ على الجهاز' : statuses[job.status]))));
      if (active(job)) status.append(elem('span', '', [job.percent != null ? Math.round(job.percent) + '%' : '', job.speed ? size(job.speed) + '/s' : '', job.status === 'downloading' && job.eta ? 'وقت المسار: ' + duration(job.eta) : ''].filter(Boolean).join(' • ')));
      content.append(status);
      if (job.status === 'error' && job.error_detail) {
        const details = elem('details', 'job-error-details'); details.open = expandedErrors.has(job.id);
        details.append(elem('summary', '', 'تفاصيل الخطأ'), elem('p', '', [job.error_detail, ...(job.warnings || [])].join('\n')));
        details.ontoggle = () => details.open ? expandedErrors.add(job.id) : expandedErrors.delete(job.id); content.append(details);
      }
      if (active(job)) {
        const track = elem('div', 'progress-track' + (job.percent == null ? ' indeterminate' : '')); track.setAttribute('role', 'progressbar'); track.setAttribute('aria-label', statuses[job.status]);
        if (job.percent != null) track.setAttribute('aria-valuenow', String(job.percent));
        const fill = elem('span'); if (job.percent != null) fill.style.width = Math.min(100, Math.max(0, job.percent)) + '%'; track.append(fill); content.append(track);
      }
      const actions = elem('div', 'job-actions');
      if (job.status === 'done') {
        for (const [action, label] of [['export', 'حفظ على الجهاز'], ['open', 'فتح'], ['share', 'مشاركة']]) actions.append(jobAction(job, action, label));
        if (video) actions.append(jobAction(job, 'tiktok', 'نسخة تيك توك'));
      }
      if (job.retryable) actions.append(jobAction(job, 'retry', 'إعادة المحاولة'));
      if (job.resumable && !job.resume_requested) actions.append(jobAction(job, 'resume', 'استكمال'));
      if (active(job)) actions.append(jobAction(job, 'pause', 'إيقاف مؤقت'));
      if (active(job) || job.status === 'paused') actions.append(jobAction(job, 'cancel', 'إلغاء'));
      actions.append(jobAction(job,'remove','حذف من السجل'));
      card.append(ji, content, actions); list.append(card);
    });
  }
  async function refreshJobs() {
    if (!state.health || state.refreshing) return;
    state.refreshing = true;
    try { const data = await api('/api/jobs'); state.jobs = data.jobs || []; renderJobs(); $('history-caption').textContent = 'ملفاتك، مرتبة وفي مكان واحد.'; }
    catch (e) { $('history-caption').textContent = e.message; }
    finally { state.refreshing = false; clearTimeout(pollTimer); pollTimer = setTimeout(refreshJobs, state.jobs.some(active) ? 1400 : 10000); }
  }
  function healthDisplay() {
    const h = state.health; $('engine-badge').classList.toggle('connected', !!h);
    $('engine-badge').lastElementChild.textContent = h ? 'المحرك جاهز' : 'المحرك غير جاهز';
    const detail = $('engine-detail'); detail.replaceChildren();
    for (const [label, value, off] of [['yt-dlp', h?.ytdlp || 'جاري التجهيز', !h?.ytdlp], ['FFmpeg', h?.ffmpeg ? 'جاهز' : 'غير جاهز', !h?.ffmpeg], ['QuickJS', h?.quickjs ? 'جاهز' : 'غير جاهز', !h?.quickjs], ['أدوات قراءة الروابط', h?.ejs || 'غير جاهزة', !h?.ejs]]) {
      const line = elem('div', 'engine-line'); line.append(elem('span', '', label), elem('span', off ? 'off' : '', value)); detail.append(line);
    }
    actionText();
  }
  async function checkHealth() {
    try { state.health = await api('/api/health'); $('connection-result').textContent = 'محرك الهاتف جاهز للتحميل.'; }
    catch (e) { $('connection-result').textContent = e.message; error(e.message); }
    healthDisplay(); if (state.health) { refreshJobs(); loadSettings(); }
  }
  function storageDisplay(data) { $('storage-detail').textContent = ['متاح: ' + (size(data.free) || '0 B'), 'مؤقت: ' + (size(data.temporary) || '0 B'), 'أجزاء محفوظة: ' + (size(data.retained) || '0 B'), 'مكتمل داخل التطبيق: ' + (size(data.unsaved) || '0 B')].join(' • '); }
  async function loadSettings() {
    try { const settings = await api('/api/settings'); $('fragments-select').value = String(settings.fragments); $('restore-downloads').checked = settings.restore; $('auto-save').checked = settings.auto_save; storageDisplay(await api('/api/storage')); }
    catch (e) { $('settings-result').textContent = e.message; }
  }
  async function saveSettings() {
    const controls = [$('fragments-select'), $('restore-downloads'), $('auto-save')]; controls.forEach(e => e.disabled = true);
    try { await api('/api/settings', 'POST', { fragments: Number($('fragments-select').value), restore: $('restore-downloads').checked, auto_save: $('auto-save').checked }); $('settings-result').textContent = 'حُفظت الإعدادات. تطبق عند بدء المهمة أو استكمالها.'; }
    catch (e) { $('settings-result').textContent = e.message; }
    finally { controls.forEach(e => e.disabled = false); }
  }
  [$('fragments-select'), $('restore-downloads'), $('auto-save')].forEach(e => e.onchange = saveSettings);
  $('clean-storage').onclick = async () => { const button = $('clean-storage'); button.disabled = true; try { const result = await api('/api/storage/clean', 'POST'); storageDisplay(result.storage); toast('تم تنظيف ' + (size(result.removed) || '0 B')); } catch(e) { toast(e.message); } finally { button.disabled = false; } };
  $('link-form').onsubmit = e => { e.preventDefault(); state.info ? startDownload() : inspect(); };
  $('url-input').oninput = invalidate;
  $('paste-btn').onclick = () => { const text = NativeLuqta.readClipboard(); if (text) { $('url-input').value = text.trim(); invalidate(); } else toast('الحافظة فارغة. ألصق الرابط في الحقل.'); $('url-input').focus(); };
  $('sample-btn').onclick = () => { if (state.busy) return; state.type = 'video'; $('url-input').value = 'https://appassets.androidplatform.net/sample.mp4'; invalidate(); inspect(); };
  all('[data-type]').forEach(b => b.onclick = () => { if (!state.busy) { const changed = (state.type === 'image') !== (b.dataset.type === 'image'); state.type = b.dataset.type; changed ? invalidate() : options(); } });
  $('image-quality').onchange = () => { state.imageQuality = $('image-quality').value; options(); };
  $('image-select').onchange = () => { state.imageIndex = $('image-select').value; const item = state.info?.images[state.imageIndex === 'all' ? 0 : Number(state.imageIndex)]; if (item) showInfo({ ...state.info, ...item, thumbnail: item.image_url }); };
  all('[data-login]').forEach(button => button.onclick=async()=>{try{await api('/api/source/login','POST',{source:button.dataset.login});}catch(e){toast(e.message);}});
  all('[data-quality]').forEach(b => b.onclick = () => { state.quality = b.dataset.quality; options(); });
  $('format-select').onchange = options;
  $('fps-select').onchange = () => { state.fps = $('fps-select').value; options(); };
  all('[data-quick]').forEach(b => b.onclick = () => { if (state.busy) return; const wasImage = state.type === 'image'; state.type = b.dataset.quick === 'audio' ? 'audio' : 'video'; state.quality = b.dataset.quick === '4k' ? '2160' : 'best'; wasImage ? invalidate() : options(); $('link-form').scrollIntoView({ behavior: 'smooth' }); $('url-input').focus(); });
  all('[data-filter]').forEach(b => b.onclick = () => { state.filter = b.dataset.filter; all('[data-filter]').forEach(x => x.classList.toggle('active', x === b)); renderJobs(); });
  all('[data-settings], [data-help]').forEach(b => b.onclick = () => { $('settings-dialog').showModal(); state.health ? loadSettings() : checkHealth(); });
  all('[data-close]').forEach(b => b.onclick = () => $('settings-dialog').close());
  $('settings-dialog').addEventListener('click', e => { if (e.target === $('settings-dialog')) { const r = e.target.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right) $('settings-dialog').close(); } });
  $('update-engine').onclick = async () => {
    const button = $('update-engine'), result = $('native-update-result'); button.disabled = true; button.textContent = 'جاري تحديث المحرك…'; result.textContent = '';
    try { state.health = await api('/api/engine/update', 'POST'); healthDisplay(); result.textContent = 'المحرك محدّث: ' + state.health.ytdlp; await refreshJobs(); }
    catch (e) { result.textContent = e.message; }
    finally { button.disabled = false; button.textContent = 'تحديث محرك yt-dlp'; }
  };
  all('[data-nav]').forEach(b => b.onclick = () => { const section = b.dataset.nav; all('[data-nav]').forEach(x => { x.classList.toggle('active', x.dataset.nav === section); x.classList.toggle('selected', x.dataset.nav === section && x.classList.contains('nav-item')); }); $('page-label').textContent = section === 'new' ? 'تنزيل جديد' : 'التحميلات'; (section === 'new' ? $('new-section') : $('downloads-section')).scrollIntoView({ behavior: 'smooth' }); });
  $('clear-history').onclick = async () => { if(!await confirmRemove('مسح السجل المنتهي، بما فيه الملفات المكتملة غير المحفوظة داخل التطبيق؟ المهام الجارية والموقوفة تبقى.')) return;try { await api('/api/history/clear', 'POST'); await refreshJobs(); toast('مُسح السجل المنتهي. الملفات المحفوظة في المعرض باقية.'); } catch (e) { toast(e.message); } };
  try { if (localStorage.getItem('luqta-theme') === '"light"') document.body.classList.add('light'); } catch {}
  $('theme-toggle').onclick = () => { document.body.classList.toggle('light'); try { localStorage.setItem('luqta-theme', JSON.stringify(document.body.classList.contains('light') ? 'light' : 'dark')); } catch {} };
  renderJobs(); options(); checkHealth();
})();
