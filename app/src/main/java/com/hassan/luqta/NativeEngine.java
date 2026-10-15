package com.hassan.luqta;

import android.content.Context;
import android.webkit.CookieManager;
import android.system.Os;
import android.system.OsConstants;
import com.yausername.youtubedl_android.YoutubeDL;
import com.yausername.ffmpeg.FFmpeg;
import org.json.JSONObject;
import java.io.*;
import java.util.*;
import java.util.concurrent.*;

final class NativeEngine {
    static final String SAMPLE="https://appassets.androidplatform.net/sample.mp4";
    private static NativeEngine instance;
    final Context context;
    final File scripts, nativeDir, dataDir;
    private final CountDownLatch initialized=new CountDownLatch(1);
    private final ConcurrentHashMap<String,Runner> running=new ConcurrentHashMap<>();
    private final Set<String> cancelledIds=ConcurrentHashMap.newKeySet();
    private final ScheduledExecutorService timers=Executors.newSingleThreadScheduledExecutor();
    private JSONObject health;
    private String initError;
    private boolean updating;
    private final LinkedHashMap<String,JSONObject> inspections=new LinkedHashMap<>();
    final JobManager jobs;

    static synchronized NativeEngine get(Context c) {
        if (instance==null) instance=new NativeEngine(c.getApplicationContext());
        return instance;
    }
    private NativeEngine(Context c) {
        context=c; scripts=new File(c.getFilesDir(),"engine"); nativeDir=new File(c.getApplicationInfo().nativeLibraryDir);
        dataDir=new File(c.getNoBackupFilesDir(),"youtubedl-android"); jobs=new JobManager(c,this);
        new Thread(() -> {
            try {
                scripts.mkdirs();
                for (String name:new String[]{"core.py","network.py","worker.py","images.py","social_photos.py","sample.mp4"}) {
                    try(InputStream in=c.getAssets().open("engine/"+name); OutputStream out=new FileOutputStream(new File(scripts,name))) { copy(in,out); }
                }
                YoutubeDL.getInstance().init(c); FFmpeg.getInstance().init(c);
                installBundledEngine();
                health=runRaw(Json.object("mode","health"),"init",null,60);
                if (!health.optBoolean("ffmpeg")) throw new IOException("لم يكتمل تجهيز FFmpeg.");
                if (!health.optBoolean("network_policy")) throw new IOException("لم يكتمل تجهيز حماية اتصالات المحرك.");
                if (!health.optBoolean("quickjs") || health.isNull("ejs")) throw new IOException("لم يكتمل تجهيز محرك قراءة روابط الفيديو.");
            } catch(Exception e) { initError=Json.error(e); }
            finally { initialized.countDown(); }
        },"LuqtaInit").start();
    }
    void awaitReady() throws Exception {
        if (!initialized.await(90,TimeUnit.SECONDS)) throw new IOException("استغرق تجهيز المحرك وقتًا طويلًا. افتح التطبيق مجددًا.");
        if (initError!=null) throw new IOException(initError);
        synchronized(this) { if(updating) throw new IOException("جاري تحديث المحرك. انتظر قليلًا."); }
    }
    JSONObject health() throws Exception { awaitReady(); return health; }
    JSONObject inspect(JSONObject request) throws Exception {
        String url=request.optString("url");
        awaitReady(); validate(url);
        JSONObject data=Json.object("mode","info","url",url,"kind",request.optString("kind","video"));
        Json.put(data,"cookies",sourceCookies(url));
        JSONObject result=runRaw(data,"info-"+UUID.randomUUID(),null,120);
        if("image".equals(result.optString("kind"))){
            String id=UUID.randomUUID().toString();Json.put(result,"inspection_id",id);
            synchronized(inspections){
                inspections.put(id,Json.object("url",url,"created",System.currentTimeMillis(),"info",result));
                while(inspections.size()>12) inspections.remove(inspections.keySet().iterator().next());
            }
        }
        return result;
    }
    org.json.JSONArray imageRequests(JSONObject body) throws Exception {
        JSONObject stored;synchronized(inspections){stored=inspections.get(body.optString("inspection_id"));}
        if(stored==null || System.currentTimeMillis()-stored.optLong("created")>15*60*1000L || !stored.optString("url").equals(body.optString("url"))) throw new IOException("أعد فحص رابط الصور قبل التحميل.");
        JSONObject info=stored.getJSONObject("info");org.json.JSONArray images=info.getJSONArray("images"),selected=body.optJSONArray("selected_images"),result=new org.json.JSONArray();
        if(selected==null || selected.length()==0 || selected.length()>50) throw new IOException("اختر الصور المطلوبة أولًا.");
        Set<Integer> seen=new HashSet<>();String album=UUID.randomUUID().toString().replace("-","");
        for(int n=0;n<selected.length();n++){
            int index=selected.getInt(n);if(index<0 || index>=images.length() || !seen.add(index)) throw new IOException("اختيار الصور غير صحيح.");
            JSONObject photo=images.getJSONObject(index),data=new JSONObject(body.toString());data.remove("inspection_id");data.remove("selected_images");
            Json.put(data,"image_url",photo.optString("image_url"));Json.put(data,"image_variants",photo.optJSONArray("variants"));
            Json.put(data,"original_width",photo.optInt("original_width",photo.optInt("width")));Json.put(data,"original_height",photo.optInt("original_height",photo.optInt("height")));
            Json.put(data,"album_id",album);Json.put(data,"album_position",photo.optInt("position",index+1));Json.put(data,"album_count",info.optInt("post_count",images.length()));
            String title=info.optString("title","صور");Json.put(data,"title",selected.length()>1?String.format(Locale.ROOT,"%02d — %s",photo.optInt("position",index+1),title):title);result.put(data);
        }
        return result;
    }
    private String sourceCookies(String url){
        try{
            String host=new java.net.URI(url).getHost().toLowerCase(Locale.ROOT);
            String domain=host.equals("instagram.com") || host.endsWith(".instagram.com")?"instagram.com":host.equals("tiktok.com") || host.endsWith(".tiktok.com")?"tiktok.com":null;
            return domain==null?"":Objects.toString(CookieManager.getInstance().getCookie("https://www."+domain+"/"),"");
        }catch(Exception ignored){return "";}
    }
    JSONObject download(JSONObject request,String id,Listener listener) throws Exception {
        awaitReady(); validate(request.optString("url"));
        Json.put(request,"cookies",sourceCookies(request.optString("url")));
        return runRaw(request,id,listener,0);
    }
    JSONObject tiktok(File input,File output,String id,Listener listener) throws Exception {
        awaitReady();
        return runRaw(Json.object("mode","tiktok","input",input.getAbsolutePath(),"output",output.getAbsolutePath()),id,listener,0);
    }
    JSONObject update() throws Exception {
        awaitReady();
        synchronized(this) { return updateReadyEngine(); }
    }
    private JSONObject updateReadyEngine() throws Exception {
        if (!running.isEmpty() || jobs.activeCount()>0) throw new IOException("انتظر اكتمال التحميلات قبل تحديث المحرك.");
        updating=true;
        File installed=engineFile(),backup=new File(context.getCacheDir(),"yt-dlp-backup");
        JSONObject previousHealth=health;
        boolean backedUp=false;
        try {
            try(InputStream in=new FileInputStream(installed);OutputStream out=new FileOutputStream(backup)) { copy(in,out); }
            backedUp=true;
            YoutubeDL.getInstance().updateYoutubeDL(context,YoutubeDL.UpdateChannel._STABLE);
            if(EngineArchive.version(installed).isEmpty() || !EngineArchive.hasEjs(installed)) throw new IOException("التحديث الذي وصل غير مكتمل.");
            health=runRaw(Json.object("mode","health"),"update-health",null,60);
            if(health.isNull("ejs")) throw new IOException("التحديث الذي وصل لا يحتوي أدوات قراءة روابط الفيديو.");
            if(!health.optBoolean("network_policy")) throw new IOException("التحديث غير متوافق مع حماية اتصالات المحرك.");
            return health;
        } catch(Exception e) {
            if(backedUp) { installed.getParentFile().mkdirs();EngineArchive.install(backup,installed); }
            health=previousHealth;
            throw new IOException("تعذّر تحديث المحرك؛ النسخة السابقة باقية. تحقق من الاتصال ثم جرّب مجددًا.",e);
        } finally { backup.delete();updating=false; }
    }
    private File engineFile(){return new File(dataDir,"yt-dlp/yt-dlp");}
    private void installBundledEngine() throws IOException {
        File installed=engineFile(),temporary=new File(installed.getParentFile(),"bundled.tmp");
        try {
            try(InputStream in=context.getResources().openRawResource(R.raw.ytdlp);OutputStream out=new FileOutputStream(temporary)) { copy(in,out); }
            if(EngineArchive.shouldInstall(temporary,installed)) EngineArchive.install(temporary,installed);
        } finally { temporary.delete(); }
    }
    static void validate(String value) throws IOException {
        if (SAMPLE.equals(value)) return;
        try {
            java.net.URI u=new java.net.URI(value);
            if(!WebPolicy.https(value)) throw new Exception();
            String host=u.getHost().toLowerCase(Locale.ROOT);
            if(!host.contains(".") || host.equals("localhost") || host.endsWith(".local")) throw new Exception();
        } catch(Exception e) { throw new IOException("يقبل التطبيق رابط HTTPS فقط على موقع عام."); }
    }
    interface Listener { void event(JSONObject e); }
    JSONObject runRaw(JSONObject data,String id,Listener listener,int timeout) throws Exception {
        Runner runner=new Runner();
        synchronized(this) { if(updating && !"update-health".equals(id)) throw new IOException("جاري تحديث المحرك. انتظر قليلًا.");running.put(id,runner); }
        File packages=new File(dataDir,"packages");
        ProcessBuilder pb=new ProcessBuilder(new File(nativeDir,"libpython.so").getAbsolutePath(),new File(scripts,"worker.py").getAbsolutePath());
        Map<String,String> env=pb.environment();
        env.remove("LUQTA_ALLOW_LOCAL_MEDIA");
        for(String key:new String[]{"HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","http_proxy","https_proxy","all_proxy"}) env.remove(key);
        env.put("LD_LIBRARY_PATH",new File(packages,"python/usr/lib").getAbsolutePath()+":"+new File(packages,"ffmpeg/usr/lib").getAbsolutePath());
        env.put("LUQTA_USER_AGENT",android.webkit.WebSettings.getDefaultUserAgent(context).replace("; wv", "").replace("Version/4.0 ",""));
        env.put("SSL_CERT_FILE",new File(packages,"python/usr/etc/tls/cert.pem").getAbsolutePath());
        env.put("PYTHONHOME",new File(packages,"python/usr").getAbsolutePath());
        env.put("PYTHONPATH",new File(dataDir,"yt-dlp/yt-dlp").getAbsolutePath());
        env.put("PATH",System.getenv("PATH")+":"+nativeDir.getAbsolutePath());
        env.put("HOME",scripts.getAbsolutePath()); env.put("TMPDIR",context.getCacheDir().getAbsolutePath()); env.put("PYTHONUNBUFFERED","1");
        env.put("LUQTA_ENGINE_CACHE",new File(context.getCacheDir(),"yt-dlp").getAbsolutePath());
        env.put("LUQTA_NATIVE_FFMPEG",new File(nativeDir,"libffmpeg.so").getAbsolutePath());
        env.put("LUQTA_NATIVE_FFPROBE",new File(nativeDir,"libffprobe.so").getAbsolutePath());
        env.put("LUQTA_NATIVE_QJS",new File(nativeDir,"libqjs.so").getAbsolutePath());
        env.put("LUQTA_APP_DATA_DIR",context.getDataDir().getAbsolutePath());
        pb.directory(scripts);
        ScheduledFuture<?> deadline=null;
        StringBuilder stderr=new StringBuilder();
        try {
            if(cancelledIds.contains(id)) throw new IOException("أُلغي التحميل.");
            runner.process=pb.start();
            if(runner.cancelled) runner.stop();
            Thread err=new Thread(() -> {
                try(BufferedReader r=new BufferedReader(new InputStreamReader(runner.process.getErrorStream(),"UTF-8"))) {
                    String line; while((line=r.readLine())!=null) synchronized(stderr) { if(stderr.length()<4000) stderr.append(line).append('\n'); }
                } catch(IOException ignored){}
            },"LuqtaErrorReader"); err.start();
            try(OutputStream out=runner.process.getOutputStream()) { out.write((data.toString()+"\n").getBytes("UTF-8")); }
            // Downloads have no fixed 30-minute cutoff. Detect stalled workers,
            // allowing FFmpeg longer than a network stage to finish processing.
            if(timeout>0) deadline=timers.schedule(() -> {runner.timedOut=true;runner.stop();},timeout,TimeUnit.SECONDS);
            else deadline=timers.scheduleAtFixedRate(() -> {
                long quiet=System.nanoTime()-runner.lastEvent;
                if(quiet>TimeUnit.SECONDS.toNanos(runner.processing?1800:300)){runner.timedOut=true;runner.stop();}
            },30,30,TimeUnit.SECONDS);
            JSONObject result=null;
            try(BufferedReader reader=new BufferedReader(new InputStreamReader(runner.process.getInputStream(),"UTF-8"))) {
                String line;
                while((line=reader.readLine())!=null) {
                    if(!line.startsWith("{")) continue;
                    JSONObject event;
                    try { event=new JSONObject(line); } catch(Exception ignored) { continue; }
                    runner.lastEvent=System.nanoTime();runner.processing="processing".equals(event.optString("status"));
                    if(event.has("pid")) { runner.pid=event.optInt("pid"); if(runner.cancelled) runner.stop(); continue; }
                    if(event.has("info")) result=event.getJSONObject("info");
                    if("health".equals(event.optString("status"))) result=event;
                    if("done".equals(event.optString("status"))) result=event;
                    if(listener!=null) listener.event(event);
                    if("error".equals(event.optString("status"))) throw new IOException(event.optString("error","تعذّر التحميل."));
                }
            }
            int exit=runner.process.waitFor(); err.join(2000);
            if(runner.cancelled) throw new IOException("أُلغي التحميل.");
            if(runner.timedOut) throw new IOException("انتهت مهلة العملية. جرّب مجددًا.");
            if(exit!=0 || result==null) { synchronized(stderr) { throw new IOException(stderr.length()>0?stderr.toString():"توقف المحرك قبل اكتمال العملية."); } }
            return result;
        } finally {
            if(deadline!=null) deadline.cancel(false);
            if(runner.process!=null && runner.process.isAlive()) runner.stop();
            running.remove(id);
            cancelledIds.remove(id);
        }
    }
    void cancel(String id) { cancelledIds.add(id); Runner runner=running.get(id); if(runner!=null) { runner.cancelled=true; runner.stop(); } }
    void resetCancellation(String id){cancelledIds.remove(id);}
    static void copy(InputStream input,OutputStream output) throws IOException { byte[] b=new byte[65536]; int n; while((n=input.read(b))!=-1) output.write(b,0,n); }
    private static final class Runner {
        volatile Process process; volatile int pid; volatile boolean cancelled,timedOut,processing;
        volatile long lastEvent=System.nanoTime();
        void stop(){
            if(pid>0) try { Os.kill(-pid,OsConstants.SIGTERM); } catch(Exception ignored){}
            if(process!=null) process.destroy();
        }
    }
}
