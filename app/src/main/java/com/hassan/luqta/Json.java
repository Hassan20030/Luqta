package com.hassan.luqta;
import org.json.JSONObject;
import org.json.JSONException;

final class Json {
    static JSONObject object(Object... pairs) {
        JSONObject result = new JSONObject();
        try { for (int i=0; i<pairs.length; i+=2) result.put((String)pairs[i], pairs[i+1]); }
        catch (JSONException e) { throw new IllegalArgumentException(e); }
        return result;
    }
    static void put(JSONObject object, String key, Object value) {
        try { object.put(key,value); } catch (JSONException e) { throw new IllegalArgumentException(e); }
    }
    static String error(Throwable e) {
        String text=e.getMessage();
        if (text==null || text.isEmpty()) text="تعذّر تنفيذ الطلب. جرّب مجددًا.";
        text=text.replaceAll("https?://\\S+", "[رابط المصدر]").replaceAll("(?i)(token|cookie|password|authorization)\\s*[:=]\\s*\\S+", "$1=[hidden]");
        return text.substring(0,Math.min(600,text.length()));
    }
}
