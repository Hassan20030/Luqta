package com.hassan.luqta;

public final class WebPolicyTest {
    public static void main(String[] args) {
        int checked=0;
        for(String url:new String[]{"https://appassets.androidplatform.net/","https://appassets.androidplatform.net/index.html","https://appassets.androidplatform.net:443/index.html#dialog"}){
            if(!WebPolicy.document(url)) throw new AssertionError(url);checked++;
        }
        for(String url:new String[]{"http://appassets.androidplatform.net/","https://appassets.androidplatform.net:444/","https://attacker.test/","https://appassets.androidplatform.net.attacker.test/","https://appassets.androidplatform.net@attacker.test/","https://@appassets.androidplatform.net/","https://appassets.androidplatform.net/app.js","file:///index.html","https://appassets.androidplatform.net/%69ndex.html","https://appassets.androidplatform.net/../index.html","https://appassets.androidplatform.net/\nindex.html"}){
            if(WebPolicy.document(url)) throw new AssertionError(url);checked++;
        }
        if(!"/app.js".equals(WebPolicy.asset("https://appassets.androidplatform.net/app.js"))) throw new AssertionError("Local script blocked");checked++;
        System.out.println("WebView document boundary: "+checked+" checks passed");
    }
}
