package com.hassan.luqta;

import android.Manifest;
import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.graphics.Insets;
import android.net.Uri;
import android.os.*;
import android.webkit.*;
import android.widget.*;
import org.json.*;
import java.io.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.regex.*;

public final class MainActivity extends Activity {
    private static final String ORIGIN="https://appassets.androidplatform.net";
    private WebView view;
    private boolean loaded;
    private volatile boolean trustedPage;
    private volatile boolean closed;
    private volatile boolean foreground;
    private boolean notificationAsked;
    private String sharedLink;
    private final ExecutorService requests=Executors.newFixedThreadPool(4);
    private final android.os.Handler handler=new android.os.Handler(android.os.Looper.getMainLooper());
    private NativeEngine engine;
    @Override public void onCreate(Bundle saved){
        super.onCreate(saved); engine=NativeEngine.get(this);
        FrameLayout frame=new FrameLayout(this); frame.setBackgroundColor(0xff111419);
        view=new WebView(this); view.setBackgroundColor(0xff111419);
        frame.addView(view,new FrameLayout.LayoutParams(-1,-1)); setContentView(frame);
        frame.setOnApplyWindowInsetsListener((v,i) -> {
            if(Build.VERSION.SDK_INT>=30) { Insets s=i.getInsets(android.view.WindowInsets.Type.systemBars()|android.view.WindowInsets.Type.displayCutout());v.setPadding(s.left,s.top,s.right,s.bottom); }
            else v.setPadding(i.getSystemWindowInsetLeft(),i.getSystemWindowInsetTop(),i.getSystemWindowInsetRight(),i.getSystemWindowInsetBottom());
            return i;
        });
        WebSettings settings=view.getSettings(); settings.setJavaScriptEnabled(true); settings.setDomStorageEnabled(true); settings.setAllowFileAccess(false); settings.setAllowContentAccess(false); settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW); settings.setSupportMultipleWindows(false);
        CookieManager.getInstance().setAcceptThirdPartyCookies(view,false);
        view.addJavascriptInterface(new Bridge(),"NativeLuqta");
        view.setWebChromeClient(new WebChromeClient());
        view.setWebViewClient(new WebViewClient(){
            @Override public WebResourceResponse shouldInterceptRequest(WebView v,WebResourceRequest r){
                Uri u=r.getUrl();String path=WebPolicy.asset(u.toString());
                if(path==null){
                    if(r.isForMainFrame() || "appassets.androidplatform.net".equalsIgnoreCase(u.getHost()) || !WebPolicy.https(u.toString())) return new WebResourceResponse("text/plain","UTF-8",403,"Forbidden",Collections.emptyMap(),new ByteArrayInputStream(new byte[0]));
                    return null;
                }
                try {
                    String mime=path.endsWith(".js")?"application/javascript":path.endsWith(".css")?"text/css":path.endsWith(".svg")?"image/svg+xml":"text/html";
                    Map<String,String> headers=new HashMap<>();headers.put("Cache-Control","no-store");headers.put("X-Content-Type-Options","nosniff");
                    headers.put("Content-Security-Policy","default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https: data:; connect-src 'self'; media-src 'self' blob:; frame-src 'none'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'none'");
                    return new WebResourceResponse(mime,"UTF-8",200,"OK",headers,getAssets().open(path.substring(1)));
                } catch(IOException e){return new WebResourceResponse("text/plain","UTF-8",404,"Not Found",Collections.emptyMap(),new ByteArrayInputStream(new byte[0]));}
            }
            @Override public boolean shouldOverrideUrlLoading(WebView v,WebResourceRequest r){
                Uri uri=r.getUrl();
                if(r.isForMainFrame() && WebPolicy.document(uri.toString())) return false;
                if(r.isForMainFrame() && WebPolicy.https(uri.toString())) { try { startActivity(new Intent(Intent.ACTION_VIEW,uri)); } catch(Exception ignored){} }
                return true;
            }
            @Override public void onPageStarted(WebView v,String url,android.graphics.Bitmap icon){
                loaded=false;trustedPage=WebPolicy.document(url);
                if(!trustedPage){v.removeJavascriptInterface("NativeLuqta");v.stopLoading();}
            }
            @Override public void onPageFinished(WebView v,String url){loaded=trustedPage && WebPolicy.document(url);if(loaded) deliverLink();}
        });
        sharedLink=extract(getIntent());
        view.loadUrl(ORIGIN+"/index.html");
    }
    @Override protected void onNewIntent(Intent intent){super.onNewIntent(intent);setIntent(intent);sharedLink=extract(intent);deliverLink();}
    private String extract(Intent intent){
        if(intent==null) return null;
        String text=Intent.ACTION_SEND.equals(intent.getAction())?intent.getStringExtra(Intent.EXTRA_TEXT):intent.getDataString();
        if(text==null) return null;
        if(text.startsWith("luqta:")) text=Uri.parse(text).getQueryParameter("url");
        if(text==null) return null;
        Matcher match=Pattern.compile("https?://[^\\s<>\"']+").matcher(text);
        return match.find()?match.group():null;
    }
    private void deliverLink(){
        if(!loaded || sharedLink==null) return;
        String link=sharedLink;sharedLink=null;
        view.evaluateJavascript("window.__luqtaImportLink && window.__luqtaImportLink("+JSONObject.quote(link)+");",null);
    }
    private void reply(String id,JSONObject data,Throwable error){
        JSONObject response=error==null?Json.object("ok",true,"data",data):Json.object("ok",false,"error",Json.error(error));
        handler.post(() -> {if(!closed && trustedPage) view.evaluateJavascript("window.__luqtaReply && window.__luqtaReply("+JSONObject.quote(id)+","+response.toString()+");",null);});
    }
    private void startDownloads(){
        handler.post(() -> {
            if(closed || !foreground) return;
            if(!notificationAsked && Build.VERSION.SDK_INT>=33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED){notificationAsked=true;requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS},11);}
            try { startForegroundService(new Intent(this,DownloadService.class)); }
            catch(Exception e){engine.jobs.pauseAll();Toast.makeText(this,"تعذّر تشغيل خدمة التحميل. ملفات الاستكمال محفوظة: "+Json.error(e),Toast.LENGTH_LONG).show();}
        });
    }
    @Override protected void onResume(){super.onResume();foreground=true;if(engine.jobs.hasPending()) startDownloads();}
    @Override protected void onPause(){foreground=false;super.onPause();}
    private JSONObject route(String path,String method,JSONObject body) throws Exception {
        if("GET".equals(method) && "/api/health".equals(path)) return engine.health();
        if("GET".equals(method) && "/api/jobs".equals(path)) return engine.jobs.list();
        if("GET".equals(method) && "/api/settings".equals(path)) return engine.jobs.preferences();
        if("GET".equals(method) && "/api/storage".equals(path)) return engine.jobs.storage();
        if(!"POST".equals(method)) throw new IOException("الطلب غير مدعوم.");
        if("/api/info".equals(path)) return engine.inspect(body);
        if("/api/settings".equals(path)) return engine.jobs.preferences(body);
        if("/api/storage/clean".equals(path)) return engine.jobs.clean();
        if("/api/source/login".equals(path)){
            String source=body.optString("source");if(!Arrays.asList("instagram","tiktok").contains(source)) throw new IOException("اختر مصدرًا صحيحًا.");
            handler.post(() -> {if(!closed) startActivity(new Intent(this,SourceLoginActivity.class).putExtra("source",source));});return Json.object("ok",true);
        }
        if("/api/jobs".equals(path)) {engine.awaitReady();JSONObject queued="image".equals(body.optString("kind"))?engine.jobs.enqueueBatch(engine.imageRequests(body)):Json.object("job_id",engine.jobs.enqueue(body),"count",1);startDownloads();return queued;}
        if("/api/history/clear".equals(path)){engine.jobs.clearHistory();return Json.object("ok",true);}
        if("/api/engine/update".equals(path)) return engine.update();
        Matcher m=Pattern.compile("/api/jobs/([a-f0-9]{32})/(cancel|pause|resume|retry|export|open|share|tiktok|tiktok-cancel|remove)").matcher(path);
        if(m.matches()) {
            String id=m.group(1),action=m.group(2);
            if("cancel".equals(action)){engine.jobs.cancel(id);return Json.object("ok",true);}
            if("remove".equals(action)){engine.jobs.remove(id);return Json.object("ok",true);}
            if("pause".equals(action)){engine.jobs.stop(id,true);return Json.object("ok",true);}
            if("tiktok-cancel".equals(action)){engine.jobs.cancelTiktok(id);return Json.object("ok",true);}
            if("retry".equals(action) || "resume".equals(action)){engine.awaitReady();engine.jobs.retry(id);startDownloads();return Json.object("job_id",id);}
            JSONObject saved="tiktok".equals(action)?engine.jobs.tiktok(id,this::startDownloads):engine.jobs.export(id);
            if("open".equals(action) || "share".equals(action) || "tiktok".equals(action)) {
                Uri uri=Uri.parse(saved.getString("uri"));String mime=saved.getString("mime");
                handler.post(() -> {
                    if(closed || !foreground) return;
                    try {
                        boolean sharing="share".equals(action) || "tiktok".equals(action);
                        Intent intent=sharing?new Intent(Intent.ACTION_SEND).setType(mime).putExtra(Intent.EXTRA_STREAM,uri):new Intent(Intent.ACTION_VIEW).setDataAndType(uri,mime);
                        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);intent.setClipData(ClipData.newRawUri("Luqta",uri));
                        startActivity(Intent.createChooser(intent,"tiktok".equals(action)?"نشر نسخة تيك توك":sharing?"مشاركة الملف":"فتح الملف"));
                    } catch(Exception e){Toast.makeText(this,"لا يوجد تطبيق مناسب لفتح الملف.",Toast.LENGTH_LONG).show();}
                });
            }
            return saved;
        }
        throw new IOException("الطلب غير مدعوم.");
    }
    public final class Bridge {
        @JavascriptInterface public String readClipboard(){
            if(closed || !trustedPage) return "";
            ClipboardManager clipboard=(ClipboardManager)getSystemService(CLIPBOARD_SERVICE);
            try { if(clipboard.hasPrimaryClip() && clipboard.getPrimaryClip().getItemCount()>0) return clipboard.getPrimaryClip().getItemAt(0).coerceToText(MainActivity.this).toString(); }
            catch(Exception ignored){}
            return "";
        }
        @JavascriptInterface public void postMessage(String value){
            if(value==null || value.length()>16384 || closed || !trustedPage) return;
            try {
                JSONObject input=new JSONObject(value);String id=input.getString("id");
                if(!id.matches("[A-Za-z0-9-]{1,80}")) return;
                String path=input.getString("path"),method=input.optString("method","GET");JSONObject body=input.optJSONObject("body");
                requests.execute(() -> {if(closed || !trustedPage) return;try{reply(id,route(path,method,body==null?new JSONObject():body),null);}catch(Exception e){reply(id,null,e);}});
            } catch(Exception ignored){}
        }
    }
    @Override public void onBackPressed(){
        if(!loaded){super.onBackPressed();return;}
        view.evaluateJavascript("(function(){var d=Array.from(document.querySelectorAll('dialog[open]')).pop(); if(d && d.open){d.close();return true;}return false;})()",r -> {if(!"true".equals(r)) moveTaskToBack(true);});
    }
    @Override protected void onDestroy(){closed=true;requests.shutdown();handler.removeCallbacksAndMessages(null);if(view!=null){view.removeJavascriptInterface("NativeLuqta");view.destroy();}super.onDestroy();}
}
