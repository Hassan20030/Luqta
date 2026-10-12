package com.hassan.luqta;
import java.io.*;
import java.nio.file.*;

/** Restarts, stale worker events and actual cleanup with protected files. */
public final class DownloadLifecycleTest {
    static int checks;
    static void check(boolean b,String why){checks++;if(!b) throw new AssertionError(why);}
    public static void main(String[] args) throws Exception {
        String status="downloading";
        status=DownloadState.restore(status,true,true);check("queued".equals(status),"Interrupted job queues on reopen");
        status="paused";
        if(DownloadState.acceptsEvent(status)) status="error";
        check("paused".equals(status),"Stop exception cannot erase user pause");
        if(DownloadState.acceptsEvent(status)) status="done";
        check("paused".equals(status),"Late done event cannot publish paused job");
        check("paused".equals(DownloadState.restore(status,true,true)),"Auto restore respects user pause");
        check("paused".equals(DownloadState.restore("downloading",false,true)),"Restore option off preserves partial data");
        check(!DownloadState.canClear("paused",false),"History clear protects paused tasks");
        check(DownloadState.canClear("done",false),"Confirmed history deletion includes completed unsaved file");
        check(!DownloadState.acceptsEvent("deleted"),"Late worker done cannot resurrect a deleted row");
        check("deleted".equals(DownloadState.restore("deleted",true,false)),"Deleted row stays deleted after reopening");
        check(DownloadState.preserveFiles("deleted",true),"Deletion waits for the worker before file cleanup");
        check(DownloadState.preserveFiles("cancelled",true),"Cancellation waits for worker exit before deleting bytes");
        File root=Files.createTempDirectory("luqta-files-").toFile();File outside=Files.createTempDirectory("luqta-outside-").toFile();
        try {
            File finished=new File(root,"image.png");Files.write(finished.toPath(),new byte[21]);
            File temp=new File(root,".tmp");temp.mkdir();Files.write(new File(temp,"fragment.part").toPath(),new byte[45]);
            File external=new File(outside,"original.png");Files.write(external.toPath(),new byte[31]);
            File link=new File(root,"outside");Files.createSymbolicLink(link.toPath(),outside.toPath());
            check(DownloadFiles.size(root)==66,"Size scan does not follow symbolic links");
            check(DownloadFiles.clean(root,finished)==45,"Only temporary bytes removed");
            check(finished.isFile(),"Unsaved image survives cleanup");check(external.length()==31,"External files survive cleanup");
            check(DownloadFiles.clean(root,null)==21,"Terminal job cleanup removes owned final copy when permitted");
            check(external.length()==31,"Repeated cleanup does not traverse links");link.delete();
            File old=new File(root,"orphan");old.mkdirs();File untouched=new File(old,"previous.mp4");Files.write(untouched.toPath(),new byte[30]);
            File partial=new File(old,"previous.mp4.part");Files.write(partial.toPath(),new byte[17]);
            check(DownloadFiles.orphan(old,false)==17,"Orphan accounting preserves original media");
            check(DownloadFiles.orphan(old,true)==17 && untouched.length()==30,"Orphan cleanup keeps old unsaved media");
        } finally {DownloadFiles.clean(root,null);DownloadFiles.clean(outside,null);}
        System.out.println("Download lifecycle and storage checks passed ("+checks+").");
    }
}
