package com.hassan.luqta;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.webkit.*;
import android.widget.*;
import java.util.Locale;

/** Official source login, isolated from Luqta assets and its JavaScript bridge. */
public final class SourceLoginActivity extends Activity {
    private WebView view;
    private String domain;
    @Override public void onCreate(Bundle saved){
        super.onCreate(saved);
        domain="tiktok".equals(getIntent().getStringExtra("source"))?"tiktok.com":"instagram.com";
        LinearLayout root=new LinearLayout(this);root.setOrientation(LinearLayout.VERTICAL);root.setFitsSystemWindows(true);
        LinearLayout bar=new LinearLayout(this);bar.setPadding(8,12,8,4);
        Button done=new Button(this);done.setText("تم، العودة إلى لُقطة");done.setOnClickListener(v -> {CookieManager.getInstance().flush();finish();});
        Button logout=new Button(this);logout.setText("فصل الحساب");logout.setOnClickListener(v -> {clearCookies();view.loadUrl("https://www."+domain+("instagram.com".equals(domain)?"/accounts/login/":"/login"));});
        bar.addView(done,new LinearLayout.LayoutParams(0,-2,1));bar.addView(logout,new LinearLayout.LayoutParams(-2,-2));root.addView(bar);
        TextView label=new TextView(this);label.setText("تسجيل الدخول على الموقع الرسمي: www."+domain+"\nلُقطة يستخدم جلسة الموقع لجلب المنشور؛ لا يقرأ كلمة مرورك.");label.setPadding(16,8,16,8);root.addView(label);
        view=new WebView(this);WebSettings settings=view.getSettings();settings.setJavaScriptEnabled(true);settings.setDomStorageEnabled(true);settings.setAllowFileAccess(false);settings.setAllowContentAccess(false);settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setUserAgentString(settings.getUserAgentString().replace("; wv", "").replace("Version/4.0 ",""));
        CookieManager.getInstance().setAcceptCookie(true);CookieManager.getInstance().setAcceptThirdPartyCookies(view,false);
        view.setWebViewClient(new WebViewClient(){
            @Override public boolean shouldOverrideUrlLoading(WebView v,WebResourceRequest r){
                Uri u=r.getUrl();String host=u.getHost()==null?"":u.getHost().toLowerCase(Locale.ROOT);
                if(WebPolicy.https(u.toString()) && (host.equals(domain) || host.endsWith("."+domain))) return false;
                if(r.isForMainFrame() && WebPolicy.https(u.toString())) try{startActivity(new Intent(Intent.ACTION_VIEW,u));}catch(Exception ignored){}
                return true;
            }
        });
        root.addView(view,new LinearLayout.LayoutParams(-1,0,1));setContentView(root);
        view.loadUrl("https://www."+domain+("instagram.com".equals(domain)?"/accounts/login/":"/login"));
    }
    private void clearCookies(){
        CookieManager cookies=CookieManager.getInstance();String url="https://www."+domain+"/",value=cookies.getCookie(url);
        if(value!=null) for(String pair:value.split(";")){
            String name=pair.trim().split("=",2)[0];
            cookies.setCookie(url,name+"=; Path=/; Max-Age=0");cookies.setCookie(url,name+"=; Domain=."+domain+"; Path=/; Max-Age=0");cookies.setCookie(url,name+"=; Domain=www."+domain+"; Path=/; Max-Age=0");
        }
        cookies.flush();WebStorage.getInstance().deleteOrigin("https://www."+domain);
    }
    @Override protected void onDestroy(){CookieManager.getInstance().flush();if(view!=null) view.destroy();super.onDestroy();}
}
