package com.hassan.luqta;

import android.content.*;
import android.net.Uri;
import android.os.Environment;
import android.provider.MediaStore;
import android.util.AtomicFile;
import org.json.*;
import java.io.*;
import java.util.*;
import java.util.concurrent.*;

final class JobManager {
    final Context context;
    final NativeEngine engine;
    final File root,history;
    private final SharedPreferences settings;
    private final LinkedHashMap<String,JSONObject> jobs=new LinkedHashMap<>();
    private final Set<String> scheduled=new HashSet<>();
    private final Set<String> exporting=new HashSet<>();
    private final Set<String> converting=new HashSet<>();
    private final ExecutorService downloads=Executors.newSingleThreadExecutor();
    private long lastPersist;
    private static final List<String> EVENT_FIELDS=Arrays.asList("status","percent","speed","eta","downloaded","title","source","filename","size","width","height","resolution","fps","original_width","original_height","kind","format","is_4k","message","error","error_detail","warnings");
    JobManager(Context c,NativeEngine e) {
        context=c; engine=e; settings=c.getSharedPreferences("download-settings",Context.MODE_PRIVATE);
        root=new File(c.getFilesDir(),"downloads"); root.mkdirs(); history=new File(root,"history.json");
        try {
            JSONArray saved=new JSONArray(new String(readAll(new AtomicFile(history).openRead()),"UTF-8"));
            for(int i=0;i<saved.length();i++) {
                JSONObject j=saved.getJSONObject(i); String id=j.optString("id");
                if(!id.matches("[a-f0-9]{32}")) continue;
                String status=DownloadState.restore(j.optString("status"),settings.getBoolean("restore",true),j.has("url"));
                Json.put(j,"status",status);j.remove("resume_requested");
                if("processing".equals(j.optString("tiktok_status"))){Json.put(j,"tiktok_status","error");Json.put(j,"tiktok_error","توقف تجهيز نسخة تيك توك. اضغط نسخة تيك توك للمحاولة مجددًا.");j.remove("tiktok_percent");}
                if("queued".equals(status) || "paused".equals(status)){Json.put(j,"speed",0);Json.put(j,"eta",0);j.remove("error");}
                jobs.put(id,j);
                if("deleted".equals(status)) cleanJob(id,j);
            }
        } catch(Exception ignored){}
        recoverOrphans();
    }
    private void recoverOrphans(){
        File[] folders=root.listFiles();if(folders==null) return;
        for(File folder:folders){
            String id=folder.getName();if(!id.matches("[a-f0-9]{32}") || jobs.containsKey(id) || !folder.isDirectory() || java.nio.file.Files.isSymbolicLink(folder.toPath())) continue;
            File[] media=folder.listFiles(file -> file.isFile() && !java.nio.file.Files.isSymbolicLink(file.toPath()) && DownloadFiles.media(file));
            if(media!=null && media.length==1 && media[0].length()>0){
                File file=media[0];String format=file.getName().substring(file.getName().lastIndexOf('.')+1).toLowerCase(Locale.ROOT);
                JSONObject job=Json.object("id",id,"title",file.getName(),"status","done","format",format,"kind",mime(format).startsWith("image/")?"image":"video","filename",file.getName(),"size",file.length(),"quality","best","percent",100,"created_at",file.lastModified(),"message","ملف سابق استُعيد إلى السجل؛ يمكنك حفظه على الجهاز");
                jobs.put(id,job);
            }
        }
    }
    synchronized JSONObject preferences(){return Json.object("fragments",settings.getInt("fragments",4),"restore",settings.getBoolean("restore",true),"auto_save",settings.getBoolean("auto_save",false));}
    synchronized JSONObject preferences(JSONObject data) throws IOException {
        int fragments=data.optInt("fragments",settings.getInt("fragments",4));
        if(!Arrays.asList(1,2,4,8).contains(fragments)) throw new IOException("اختر إعداد سرعة صحيحًا.");
        if(!settings.edit().putInt("fragments",fragments).putBoolean("restore",data.optBoolean("restore",settings.getBoolean("restore",true))).putBoolean("auto_save",data.optBoolean("auto_save",settings.getBoolean("auto_save",false))).commit()) throw new IOException("تعذّر حفظ الإعدادات.");
        return preferences();
    }
    synchronized int activeCount(){int n=converting.size();for(JSONObject j:jobs.values()) if(DownloadState.active(j.optString("status")) || j.optBoolean("resume_requested") || (scheduled.contains(j.optString("id")) && "done".equals(j.optString("status")))) n++;return n;}
    synchronized boolean hasPending(){for(JSONObject j:jobs.values()) if("queued".equals(j.optString("status"))) return true;return false;}
    synchronized JSONObject list() throws JSONException {
        List<JSONObject> data=new ArrayList<>(jobs.values());Collections.reverse(data);JSONArray result=new JSONArray();
        for(JSONObject j:data){if("deleted".equals(j.optString("status"))) continue;JSONObject copy=new JSONObject(j.toString());Json.put(copy,"retryable",j.has("url") && Arrays.asList("error","cancelled").contains(j.optString("status")));Json.put(copy,"resumable",j.has("url") && "paused".equals(j.optString("status")));copy.remove("url");copy.remove("image_url");copy.remove("image_variants");result.put(copy);}
        return Json.object("jobs",result);
    }
    synchronized String enqueue(JSONObject data) throws Exception {return enqueueInternal(data,true);}
    private String enqueueInternal(JSONObject data,boolean write) throws Exception {
        NativeEngine.validate(data.optString("url"));
        String kind=data.optString("kind","video"),format=data.optString("format","mp4"),quality=data.optString("quality","best"),fps=data.optString("fps","best");
        boolean image="image".equals(kind);
        if(image){format="image";fps="best";if(!Arrays.asList("best","lowest","4k","240","360","480","720","1080","1440","2160","3840").contains(quality)) throw new IOException("جودة الصورة غير صحيحة.");if(data.has("image_url")) NativeEngine.validate(data.optString("image_url"));}
        else if(!Arrays.asList("mp4","webm","mp3","m4a").contains(format) || !Arrays.asList("best","2160","1080","720","480").contains(quality) || !Arrays.asList("best","120","60","30").contains(fps)) throw new IOException("خيارات التحميل غير صحيحة.");
        checkCapacity();String id=UUID.randomUUID().toString().replace("-","");
        String title=data.optString("title",image?"صورة":"مقطع فيديو");if(title.length()>400) title=title.substring(0,400);
        JSONObject j=Json.object("id",id,"url",data.getString("url"),"kind",image?"image":"video","format",format,"quality",quality,"requested_fps",fps,"title",title,"status","queued","percent",JSONObject.NULL,"size",0,"speed",0,"created_at",System.currentTimeMillis());
        if(image && data.has("image_url")) Json.put(j,"image_url",data.optString("image_url"));
        if(image) for(String key:Arrays.asList("image_variants","original_width","original_height","album_id","album_position","album_count")) if(data.has(key)) Json.put(j,key,data.opt(key));
        jobs.put(id,j);if(write && !persist()){jobs.remove(id);throw new IOException("تعذّر حفظ المهمة في السجل.");}return id;
    }
    private void checkCapacity() throws IOException {
        int retained=0;for(JSONObject j:jobs.values()) if(DownloadState.active(j.optString("status")) || "paused".equals(j.optString("status"))) retained++;
        if(retained>=100) throw new IOException("قائمة الانتظار ممتلئة. أنهِ أو ألغِ بعض المهام.");
        if(root.getUsableSpace()<300L*1024*1024) throw new IOException("تحتاج مساحة متاحة لا تقل عن 300 MB.");
    }
    synchronized String retry(String id) throws Exception {
        JSONObject j=jobs.get(id);
        if(j==null || !j.has("url")) throw new IOException("أضف رابط هذا الملف مجددًا.");
        if(!DownloadState.canRestart(j.optString("status"))) throw new IOException("هذه المهمة لا تحتاج استكمالًا.");
        if(activeCount()>=100) throw new IOException("قائمة الانتظار ممتلئة.");
        if(root.getUsableSpace()<300L*1024*1024) throw new IOException("تحتاج مساحة متاحة لا تقل عن 300 MB.");
        if(scheduled.contains(id)){Json.put(j,"resume_requested",true);persist();return id;}
        prepareResume(j,id);persist();return id;
    }
    private void prepareResume(JSONObject j,String id){
        engine.resetCancellation(id);for(String key:Arrays.asList("error","error_detail","warnings","message","resume_requested","save_error")) j.remove(key);
        Json.put(j,"status","queued");Json.put(j,"speed",0);Json.put(j,"eta",0);
    }
    synchronized void startPending(){
        for(JSONObject job:new ArrayList<>(jobs.values())){String id=job.optString("id");if("queued".equals(job.optString("status")) && scheduled.add(id)) downloads.execute(() -> run(id));}
    }
    private void run(String id) {
        try {
            JSONObject request;
            synchronized(this){
                JSONObject j=jobs.get(id);if(j==null || !"queued".equals(j.optString("status"))) return;
                Json.put(j,"status","extracting");persist();
                request=Json.object("url",j.optString("url"),"title",j.optString("title"),"kind",j.optString("kind","video"),"format",j.optString("format"),"quality",j.optString("quality"),"fps",j.optString("requested_fps","best"),"fragments",settings.getInt("fragments",4),"directory",new File(root,id).getAbsolutePath());
                for(String key:Arrays.asList("image_url","image_variants","original_width","original_height")) if(j.has(key)) Json.put(request,key,j.opt(key));
            }
            engine.download(request,id,event -> {
                synchronized(JobManager.this){
                    JSONObject j=jobs.get(id);if(j==null || !DownloadState.acceptsEvent(j.optString("status"))) return;
                    for(String key:EVENT_FIELDS) if(event.has(key)) Json.put(j,key,event.opt(key));
                    if(!Arrays.asList("retrying","processing").contains(event.optString("status"))) j.remove("message");
                    if(System.currentTimeMillis()-lastPersist>1500 || "done".equals(event.optString("status")) || "error".equals(event.optString("status"))) persist();
                }
                DownloadService.changed(context);
            });
            boolean done;synchronized(this){done="done".equals(jobs.get(id).optString("status"));}
            if(done && settings.getBoolean("auto_save",false)) try{export(id);}catch(Exception e){synchronized(this){Json.put(jobs.get(id),"save_error","اكتمل التنزيل؛ تعذّر الحفظ التلقائي. اضغط حفظ على الجهاز.");}}
        } catch(Exception e){
            synchronized(this){JSONObject j=jobs.get(id);if(j!=null && DownloadState.active(j.optString("status"))){Json.put(j,"status","error");Json.put(j,"percent",JSONObject.NULL);Json.put(j,"speed",0);Json.put(j,"eta",0);Json.put(j,"error",Json.error(e));}}
        } finally {
            synchronized(this){
                scheduled.remove(id);engine.resetCancellation(id);JSONObject j=jobs.get(id);
                if(j!=null && j.optBoolean("resume_requested")) prepareResume(j,id);
                if(j!=null && Arrays.asList("done","cancelled","deleted").contains(j.optString("status"))) cleanJob(id,j);
                persist();startPending();
            }
            DownloadService.changed(context);
        }
    }
    void stop(String id,boolean pause){
        synchronized(this){
            JSONObject j=jobs.get(id);if(j==null || (!DownloadState.active(j.optString("status")) && !"paused".equals(j.optString("status")))) return;
            Json.put(j,"status",pause?"paused":"cancelled");Json.put(j,"speed",0);Json.put(j,"eta",0);j.remove("resume_requested");persist();
            engine.cancel(id);
            if(!scheduled.contains(id)){engine.resetCancellation(id);if(!pause) cleanJob(id,j);}
        }
        DownloadService.changed(context);
    }
    void cancel(String id){stop(id,false);}
    void pauseAll(){List<String> ids;synchronized(this){ids=new ArrayList<>(jobs.keySet());}for(String id:ids){stop(id,true);cancelTiktok(id);}}
    void cancelAll(){List<String> ids;synchronized(this){ids=new ArrayList<>(jobs.keySet());}for(String id:ids){cancel(id);cancelTiktok(id);}}
    synchronized JSONObject enqueueBatch(JSONArray entries) throws Exception {
        if(entries.length()==0 || entries.length()>50) throw new IOException("عدد الصور غير صحيح؛ الحد 50.");
        int retained=0;for(JSONObject job:jobs.values()) if(DownloadState.preserveFiles(job.optString("status"),false)) retained++;
        if(retained+entries.length()>100) throw new IOException("قائمة الانتظار لا تتسع للألبوم كله؛ الحد 100 مهمة.");
        for(int i=0;i<entries.length();i++){
            JSONObject data=entries.getJSONObject(i);NativeEngine.validate(data.optString("url"));NativeEngine.validate(data.optString("image_url"));
            if(!Arrays.asList("best","lowest","4k","240","360","480","720","1080","1440","2160","3840").contains(data.optString("quality","best"))) throw new IOException("جودة الصورة غير صحيحة.");
        }
        JSONArray ids=new JSONArray();List<String> added=new ArrayList<>();
        try{for(int i=0;i<entries.length();i++){String id=enqueueInternal(entries.getJSONObject(i),false);added.add(id);ids.put(id);}if(!persist()) throw new IOException("تعذّر حفظ الألبوم في السجل.");}
        catch(Exception e){for(String id:added) jobs.remove(id);persist();throw e;}
        return Json.object("job_id",ids.getString(0),"job_ids",ids,"count",ids.length());
    }
    synchronized void remove(String id) throws IOException {
        JSONObject j=jobs.get(id);if(j==null) throw new IOException("عنصر السجل غير موجود.");
        if(exporting.contains(id) || (scheduled.contains(id) && "done".equals(j.optString("status")))) throw new IOException("انتظر انتهاء حفظ الملف ثم احذفه.");
        JSONObject deleted=Json.object("id",id,"status","deleted","created_at",j.optLong("created_at"));jobs.put(id,deleted);
        if(!persist()){jobs.put(id,j);throw new IOException("تعذّر حفظ حذف عنصر السجل.");}
        engine.cancel(id);if(!scheduled.contains(id)){engine.resetCancellation(id);cleanJob(id,deleted);}
        DownloadService.changed(context);
    }
    synchronized void clearHistory(){
        for(String id:new ArrayList<>(jobs.keySet())){
            JSONObject j=jobs.get(id);String status=j.optString("status");
            if(!exporting.contains(id) && DownloadState.canClear(status,scheduled.contains(id))) try{remove(id);}catch(IOException ignored){}
            else if("deleted".equals(status) && !new File(root,id).exists()) jobs.remove(id);
        }
        persist();
    }
    private long cleanJob(String id,JSONObject j){
        File folder=new File(root,id),keep=null;
        if("done".equals(j.optString("status")) && !j.optBoolean("local_removed")) keep=new File(folder,j.optString("filename"));
        try{return DownloadFiles.clean(folder,keep);}catch(IOException ignored){return 0;}
    }
    synchronized JSONObject storage(){
        long resumable=0,ready=0,temporary=0;
        for(Map.Entry<String,JSONObject> entry:jobs.entrySet()){
            JSONObject j=entry.getValue();File folder=new File(root,entry.getKey());long bytes=DownloadFiles.size(folder);
            if(DownloadState.preserveFiles(j.optString("status"),scheduled.contains(entry.getKey()))) resumable+=bytes;
            else if("done".equals(j.optString("status")) && !j.optBoolean("local_removed")){long finalSize=new File(folder,j.optString("filename")).length();ready+=finalSize;temporary+=Math.max(0,bytes-finalSize);}
            else temporary+=bytes;
        }
        temporary+=orphans(false);
        return Json.object("temporary",temporary,"retained",resumable,"unsaved",ready,"free",root.getUsableSpace());
    }
    private long orphans(boolean remove){
        long bytes=0;File[] folders=root.listFiles();if(folders!=null) for(File folder:folders) if(folder.getName().matches("[a-f0-9]{32}") && !jobs.containsKey(folder.getName())) try{bytes+=DownloadFiles.orphan(folder,remove);}catch(IOException ignored){}
        return bytes;
    }
    synchronized JSONObject clean(){long bytes=orphans(true);for(Map.Entry<String,JSONObject> e:jobs.entrySet()) if(!exporting.contains(e.getKey()) && !DownloadState.preserveFiles(e.getValue().optString("status"),scheduled.contains(e.getKey()))) bytes+=cleanJob(e.getKey(),e.getValue());return Json.object("removed",bytes,"storage",storage());}
    JSONObject export(String id) throws Exception {
        synchronized(this){if(!exporting.add(id)) throw new IOException("انتظر انتهاء حفظ الملف أو تحويله.");}
        try{return exportInternal(id);}finally{synchronized(this){exporting.remove(id);}}
    }
    JSONObject tiktok(String id,Runnable onStart) throws Exception {
        JSONObject job;
        synchronized(this){
            JSONObject source=jobs.get(id);
            if(source==null || !"done".equals(source.optString("status")) || !"video".equals(source.optString("kind")) || !Arrays.asList("mp4","webm").contains(source.optString("format"))) throw new IOException("هذا العنصر ليس فيديو جاهزًا.");
            if(!exporting.add(id)) throw new IOException("جاري تجهيز الملف بالفعل. انتظر اكتمال العملية.");
            job=new JSONObject(source.toString());
        }
        File temporarySource=null,output=null;
        try{
            JSONObject previous=job.optJSONObject("tiktok_copy");
            if(previous!=null){
                try(android.os.ParcelFileDescriptor file=context.getContentResolver().openFileDescriptor(Uri.parse(previous.getString("uri")),"r")){if(file!=null) return previous;}catch(Exception ignored){}
            }
            synchronized(this){if(!converting.isEmpty()) throw new IOException("انتظر انتهاء تحويل الفيديو الجاري أولًا.");converting.add(id);JSONObject source=jobs.get(id);Json.put(source,"tiktok_status","processing");Json.put(source,"tiktok_percent",JSONObject.NULL);source.remove("tiktok_error");source.remove("tiktok_copy");persist();}
            onStart.run();
            File folder=new File(root,id),local=new File(folder,job.optString("filename"));
            if(!local.getCanonicalPath().startsWith(folder.getCanonicalPath()+File.separator)) throw new IOException("مسار الفيديو غير صالح.");
            File cache=new File(context.getCacheDir(),"luqta-tiktok");cache.mkdirs();
            File input=local;
            if(!local.isFile()){
                String saved=job.optString("export_uri");if(saved.isEmpty()) throw new IOException("الفيديو لم يعد موجودًا. نزّله مجددًا ثم جرّب.");
                temporarySource=new File(cache,id+"-source."+job.optString("format","mp4"));
                try(InputStream in=context.getContentResolver().openInputStream(Uri.parse(saved));OutputStream out=new FileOutputStream(temporarySource)){if(in==null) throw new IOException("تعذّر قراءة الفيديو المحفوظ.");NativeEngine.copy(in,out);}
                input=temporarySource;
            }
            if(input.length()<1) throw new IOException("الفيديو فارغ.");
            if(cache.getUsableSpace()<Math.min(Math.max(input.length()*2,300L*1024*1024),2L*1024*1024*1024)) throw new IOException("المساحة المتاحة لا تكفي لإنشاء نسخة تيك توك.");
            output=new File(cache,id+"-TikTok.mp4");if(output.exists()) output.delete();
            JSONObject actual=engine.tiktok(input,output,id+"-tiktok",event -> {
                synchronized(JobManager.this){JSONObject source=jobs.get(id);if(source!=null && "processing".equals(source.optString("tiktok_status")) && event.has("percent")) Json.put(source,"tiktok_percent",event.opt("percent"));}
                DownloadService.changed(context);
            });
            requireTiktokActive(id);
            String base=job.optString("filename","Luqta").replaceFirst("\\.[^.]+$","");if(base.length()>180) base=base.substring(0,180);
            ContentValues values=new ContentValues();values.put(MediaStore.MediaColumns.DISPLAY_NAME,base+"_TikTok.mp4");values.put(MediaStore.MediaColumns.MIME_TYPE,"video/mp4");values.put(MediaStore.MediaColumns.RELATIVE_PATH,Environment.DIRECTORY_MOVIES+"/Luqta");values.put(MediaStore.MediaColumns.IS_PENDING,1);
            ContentResolver resolver=context.getContentResolver();Uri uri=resolver.insert(MediaStore.Video.Media.EXTERNAL_CONTENT_URI,values);if(uri==null) throw new IOException("تعذّر إنشاء نسخة تيك توك في الهاتف.");
            try{
                try(InputStream in=new FileInputStream(output);OutputStream out=resolver.openOutputStream(uri,"w")){if(out==null) throw new IOException("تعذّر حفظ نسخة تيك توك.");NativeEngine.copy(in,out);}
                JSONObject saved=Json.object("uri",uri.toString(),"mime","video/mp4","saved_to",Environment.DIRECTORY_MOVIES+"/Luqta","message","حُفظت نسخة تيك توك. اختر تيك توك من خيارات المشاركة.","width",actual.optInt("width"),"height",actual.optInt("height"),"fps",actual.optDouble("fps"),"size",actual.optLong("size"));
                synchronized(this){requireTiktokActive(id);ContentValues ready=new ContentValues();ready.put(MediaStore.MediaColumns.IS_PENDING,0);if(resolver.update(uri,ready,null,null)<1) throw new IOException("تعذّر اعتماد نسخة تيك توك.");JSONObject source=jobs.get(id);Json.put(source,"tiktok_copy",saved);Json.put(source,"tiktok_status","ready");Json.put(source,"tiktok_percent",100);persist();}
                return saved;
            }catch(Exception e){resolver.delete(uri,null,null);throw e;}
        }catch(Exception e){
            synchronized(this){JSONObject source=jobs.get(id);if(source!=null && converting.contains(id) && !"cancelled".equals(source.optString("tiktok_status"))){Json.put(source,"tiktok_status","error");Json.put(source,"tiktok_error",Json.error(e));persist();}}
            throw e;
        }finally{
            if(temporarySource!=null) temporarySource.delete();if(output!=null) output.delete();
            engine.resetCancellation(id+"-tiktok");
            synchronized(this){exporting.remove(id);converting.remove(id);}
            DownloadService.changed(context);
        }
    }
    private synchronized void requireTiktokActive(String id) throws IOException {JSONObject job=jobs.get(id);if(job==null || !"processing".equals(job.optString("tiktok_status"))) throw new IOException("أُلغي تجهيز نسخة تيك توك.");}
    synchronized void cancelTiktok(String id){
        if(!converting.contains(id)) return;
        JSONObject job=jobs.get(id);if(job!=null && "processing".equals(job.optString("tiktok_status"))){Json.put(job,"tiktok_status","cancelled");job.remove("tiktok_percent");engine.cancel(id+"-tiktok");persist();}
    }
    private JSONObject exportInternal(String id) throws Exception {
        JSONObject job;
        synchronized(this){JSONObject source=jobs.get(id);if(source==null || !"done".equals(source.optString("status"))) throw new IOException("الملف غير جاهز للحفظ.");job=new JSONObject(source.toString());}
        String format=job.optString("format"),previous=job.optString("export_uri");
        if(!previous.isEmpty()){
            Uri uri=Uri.parse(previous);
            try(android.os.ParcelFileDescriptor p=context.getContentResolver().openFileDescriptor(uri,"r")){if(p!=null) return exportResult(uri,format);}catch(Exception ignored){}
        }
        File folder=new File(root,id),file=new File(folder,job.getString("filename"));
        if(!file.getCanonicalPath().startsWith(folder.getCanonicalPath()+File.separator) || !file.isFile()) throw new IOException("الملف المحفوظ لم يعد موجودًا. أضف الرابط لتنزيله مجددًا.");
        boolean audio=Arrays.asList("mp3","m4a").contains(format),image="image".equals(job.optString("kind"));
        ContentValues values=new ContentValues();values.put(MediaStore.MediaColumns.DISPLAY_NAME,file.getName());values.put(MediaStore.MediaColumns.MIME_TYPE,mime(format));values.put(MediaStore.MediaColumns.RELATIVE_PATH,savePath(format));values.put(MediaStore.MediaColumns.IS_PENDING,1);
        Uri collection=image?MediaStore.Images.Media.EXTERNAL_CONTENT_URI:audio?MediaStore.Audio.Media.EXTERNAL_CONTENT_URI:MediaStore.Video.Media.EXTERNAL_CONTENT_URI;
        ContentResolver resolver=context.getContentResolver();Uri uri=resolver.insert(collection,values);if(uri==null) throw new IOException("تعذّر إنشاء الملف في الهاتف.");
        try {
            try(InputStream in=new FileInputStream(file);OutputStream out=resolver.openOutputStream(uri,"w")){if(out==null) throw new IOException("تعذّر فتح مسار الحفظ.");NativeEngine.copy(in,out);}
            ContentValues ready=new ContentValues();ready.put(MediaStore.MediaColumns.IS_PENDING,0);if(resolver.update(uri,ready,null,null)<1) throw new IOException("تعذّر اعتماد الملف المحفوظ.");
            synchronized(this){JSONObject source=jobs.get(id);if(source!=null){Json.put(source,"export_uri",uri.toString());source.remove("save_error");if(persist()){if(file.delete()) Json.put(source,"local_removed",true);cleanJob(id,source);persist();}}}
            return exportResult(uri,format);
        }catch(Exception e){resolver.delete(uri,null,null);throw e;}
    }
    static String mime(String format){switch(format){case "jpg":case "jpeg":return "image/jpeg";case "png":return "image/png";case "webp":return "image/webp";case "mp3":return "audio/mpeg";case "m4a":return "audio/mp4";case "webm":return "video/webm";default:return "video/mp4";}}
    static String savePath(String format){return (Arrays.asList("jpg","jpeg","png","webp").contains(format)?Environment.DIRECTORY_PICTURES:Arrays.asList("mp3","m4a").contains(format)?Environment.DIRECTORY_MUSIC:Environment.DIRECTORY_MOVIES)+"/Luqta";}
    static JSONObject exportResult(Uri uri,String format){return Json.object("uri",uri.toString(),"mime",mime(format),"saved_to",savePath(format));}
    synchronized String notificationText(){
        for(String id:converting){JSONObject j=jobs.get(id);if(j!=null) return "جاري تجهيز نسخة تيك توك"+(j.isNull("tiktok_percent")?"":" • "+Math.round(j.optDouble("tiktok_percent"))+"%");}
        for(JSONObject j:jobs.values()){String status=j.optString("status");if("downloading".equals(status)) return j.optString("title")+(j.isNull("percent")?"":" • "+Math.round(j.optDouble("percent"))+"%");if("processing".equals(status)) return "جاري تجهيز الملف داخل الهاتف";}
        return activeCount()>0?"التحميلات الجارية: "+activeCount():"توقفت عمليات التحميل";
    }
    synchronized int notificationProgress(){for(String id:converting){JSONObject j=jobs.get(id);if(j!=null && !j.isNull("tiktok_percent")) return (int)Math.round(j.optDouble("tiktok_percent"));}for(JSONObject j:jobs.values()) if("downloading".equals(j.optString("status")) && !j.isNull("percent")) return (int)Math.round(j.optDouble("percent"));return -1;}
    private synchronized boolean persist(){
        AtomicFile storage=new AtomicFile(history);FileOutputStream out=null;
        try{JSONArray data=new JSONArray();for(JSONObject j:jobs.values()){JSONObject copy=new JSONObject(j.toString());if("done".equals(copy.optString("status"))){copy.remove("url");copy.remove("image_url");copy.remove("image_variants");}data.put(copy);}out=storage.startWrite();out.write(data.toString().getBytes("UTF-8"));storage.finishWrite(out);lastPersist=System.currentTimeMillis();return true;}
        catch(Exception ignored){if(out!=null) storage.failWrite(out);return false;}
    }
    static byte[] readAll(InputStream in) throws IOException {try(InputStream source=in;ByteArrayOutputStream out=new ByteArrayOutputStream()){NativeEngine.copy(source,out);return out.toByteArray();}}
}
