package com.hassan.luqta;

import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;

/** Shared lifecycle rules; stopped workers cannot overwrite paused jobs. */
final class DownloadState {
    static final Set<String> ACTIVE=new HashSet<>(Arrays.asList("queued","extracting","retrying","downloading","processing"));
    static boolean active(String status){return ACTIVE.contains(status);}
    static String restore(String status,boolean restore,boolean hasUrl){
        return active(status)?(restore && hasUrl?"queued":"paused"):status;
    }
    static boolean preserveFiles(String status,boolean scheduled){return scheduled || active(status) || "paused".equals(status);}
    static boolean acceptsEvent(String status){return active(status);}
    static boolean canRestart(String status){return Arrays.asList("paused","error","cancelled").contains(status);}
    static boolean canClear(String status,boolean scheduled){return !scheduled && Arrays.asList("done","error","cancelled").contains(status);}
}
