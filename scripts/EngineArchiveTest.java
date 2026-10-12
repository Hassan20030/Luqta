package com.hassan.luqta;

import java.io.*;
import java.nio.file.*;
import java.util.zip.*;

/** Host regression checks for upgrades that must preserve a newer engine. */
public final class EngineArchiveTest {
    private static File archive(File directory,String name,String version,boolean ejs) throws Exception {
        File file=new File(directory,name);
        try(ZipOutputStream zip=new ZipOutputStream(new FileOutputStream(file))) {
            zip.putNextEntry(new ZipEntry("yt_dlp/version.py"));
            zip.write(("__version__ = '"+version+"'\n").getBytes("UTF-8"));zip.closeEntry();
            if(ejs){zip.putNextEntry(new ZipEntry("yt_dlp_ejs/__init__.py"));zip.closeEntry();}
        }
        return file;
    }
    private static void check(boolean condition,String reason){if(!condition) throw new AssertionError(reason);}
    public static void main(String[] args) throws Exception {
        File directory=Files.createTempDirectory("luqta-upgrade-").toFile();
        try {
            File bundled=archive(directory,"bundled","2026.08.19",true);
            check(EngineArchive.shouldInstall(bundled,new File(directory,"missing")),"Install first run");
            check(EngineArchive.shouldInstall(bundled,archive(directory,"old","2025.12.1",true)),"Upgrade cached old engine");
            check(!EngineArchive.shouldInstall(bundled,archive(directory,"same","2026.8.19",true)),"Preserve same healthy engine");
            check(EngineArchive.shouldInstall(bundled,archive(directory,"no-ejs","2026.8.19",false)),"Repair missing solver");
            check(!EngineArchive.shouldInstall(bundled,archive(directory,"newer","2026.10.12",true)),"Preserve user-updated newer engine");
            File bad=new File(directory,"bad");Files.write(bad.toPath(),new byte[]{1,2,3});
            check(EngineArchive.shouldInstall(bundled,bad),"Repair corrupt cache");
            EngineArchive.install(bundled,bad);
            check("2026.08.19".equals(EngineArchive.version(bad)) && EngineArchive.hasEjs(bad),"Atomic replacement content");
            check(!bundled.exists(),"Move source only on success");
            System.out.println("Engine archive checks passed (8).");
        } finally {for(File file:directory.listFiles()) file.delete();directory.delete();}
    }
}
