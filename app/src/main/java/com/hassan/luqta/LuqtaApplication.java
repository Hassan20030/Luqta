package com.hassan.luqta;
import android.app.Application;
public final class LuqtaApplication extends Application {
    @Override public void onCreate() { super.onCreate(); NativeEngine.get(this); }
}
