package com.hassan.luqta;
import android.app.*;
import android.content.*;
import android.content.pm.ServiceInfo;
import android.os.*;

public final class DownloadService extends Service {
    static final String CHANNEL="luqta-downloads";
    static final int NOTIFICATION=101;
    private static volatile DownloadService current;
    private final Handler handler=new Handler(Looper.getMainLooper());
    private PowerManager.WakeLock wake;
    private long lastUpdate;
    private long lastWakeRenew;
    @Override public void onCreate(){
        super.onCreate(); current=this;
        NotificationManager nm=(NotificationManager)getSystemService(NOTIFICATION_SERVICE);
        nm.createNotificationChannel(new NotificationChannel(CHANNEL,"تحميلات لُقطة",NotificationManager.IMPORTANCE_LOW));
        wake=((PowerManager)getSystemService(POWER_SERVICE)).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK,"Luqta:download");
        wake.setReferenceCounted(false);
    }
    @Override public int onStartCommand(Intent intent,int flags,int startId){
        if(intent!=null && "cancel-all".equals(intent.getAction())) { NativeEngine.get(this).jobs.cancelAll(); stopSelf(); return START_NOT_STICKY; }
        if(intent!=null && "pause-all".equals(intent.getAction())) { NativeEngine.get(this).jobs.pauseAll();stopSelf();return START_NOT_STICKY; }
        int types=ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC;
        if(Build.VERSION.SDK_INT>=35) types|=ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROCESSING;
        startForeground(NOTIFICATION,notification(),types);
        if(!wake.isHeld()) {wake.acquire(35*60*1000L);lastWakeRenew=SystemClock.elapsedRealtime();}
        NativeEngine.get(this).jobs.startPending(); update(); return START_NOT_STICKY;
    }
    static void changed(Context c){ DownloadService service=current; if(service!=null) service.handler.post(service::update); }
    private Notification notification(){
        JobManager jobs=NativeEngine.get(this).jobs;
        Intent open=new Intent(this,MainActivity.class); PendingIntent content=PendingIntent.getActivity(this,0,open,PendingIntent.FLAG_IMMUTABLE|PendingIntent.FLAG_UPDATE_CURRENT);
        Intent pause=new Intent(this,DownloadService.class).setAction("pause-all");PendingIntent stop=PendingIntent.getService(this,1,pause,PendingIntent.FLAG_IMMUTABLE|PendingIntent.FLAG_UPDATE_CURRENT);
        int progress=jobs.notificationProgress();
        return new Notification.Builder(this,CHANNEL).setSmallIcon(R.drawable.ic_notification).setContentTitle("لُقطة — التحميلات").setContentText(jobs.notificationText()).setContentIntent(content).setOngoing(true).setOnlyAlertOnce(true).setProgress(100,Math.max(progress,0),progress<0).addAction(new Notification.Action.Builder(null,"إيقاف مؤقت",stop).build()).build();
    }
    private void update(){
        if(NativeEngine.get(this).jobs.activeCount()==0) { stopForeground(STOP_FOREGROUND_REMOVE); stopSelf(); return; }
        long now=SystemClock.elapsedRealtime(); if(now-lastUpdate<500) return; lastUpdate=now;
        if(now-lastWakeRenew>60000){wake.acquire(35*60*1000L);lastWakeRenew=now;}
        ((NotificationManager)getSystemService(NOTIFICATION_SERVICE)).notify(NOTIFICATION,notification());
    }
    @Override public void onTimeout(int startId,int fgsType){NativeEngine.get(this).jobs.pauseAll();stopForeground(STOP_FOREGROUND_REMOVE);stopSelf();}
    @Override public void onDestroy(){if(wake!=null && wake.isHeld()) wake.release();current=null;handler.removeCallbacksAndMessages(null);super.onDestroy();}
    @Override public IBinder onBind(Intent i){return null;}
}
