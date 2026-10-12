package com.hassan.luqta;

import java.net.URI;
import java.util.Arrays;

/** Exact local documents permitted to own the native WebView bridge. */
final class WebPolicy {
    static boolean https(String value) {
        try {
            URI u=new URI(value);
            return value.length()<=4096 && !value.matches("(?s).*\\p{Cntrl}.*") && "https".equalsIgnoreCase(u.getScheme()) && u.getHost()!=null && u.getRawUserInfo()==null && (u.getPort()==-1 || u.getPort()==443);
        } catch(Exception e) { return false; }
    }
    static String asset(String value) {
        if(!https(value)) return null;
        try {
            URI u=new URI(value);
            if(!"appassets.androidplatform.net".equalsIgnoreCase(u.getHost())) return null;
            String path=u.getRawPath();if(path==null || path.isEmpty() || "/".equals(path)) path="/index.html";
            return Arrays.asList("/index.html","/app.css","/app.js","/favicon.svg").contains(path)?path:null;
        } catch(Exception e) { return null; }
    }
    static boolean document(String value) { return "/index.html".equals(asset(value)); }
}
