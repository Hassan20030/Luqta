package com.hassan.luqta;

import java.io.*;
import java.nio.file.*;
import java.util.regex.*;
import java.util.zip.*;

/** Keeps bundled upgrades and downloaded upgrades on the same validated path. */
final class EngineArchive {
    static String version(File file) {
        try(ZipFile zip=new ZipFile(file)) {
            ZipEntry entry=zip.getEntry("yt_dlp/version.py");
            if(entry==null || entry.getSize()>8192) return "";
            try(InputStream in=zip.getInputStream(entry)) {
                Matcher m=Pattern.compile("__version__\\s*=\\s*['\"]([0-9.]+)['\"]").matcher(new String(read(in),"UTF-8"));
                return m.find()?m.group(1):"";
            }
        } catch(IOException e) { return ""; }
    }
    static boolean hasEjs(File file) {
        try(ZipFile zip=new ZipFile(file)) { return zip.getEntry("yt_dlp_ejs/__init__.py")!=null; }
        catch(IOException e) { return false; }
    }
    static boolean shouldInstall(File bundled,File installed) throws IOException {
        String next=version(bundled),current=version(installed);
        if(next.isEmpty() || !hasEjs(bundled)) throw new IOException("حزمة محرك التحميل غير مكتملة.");
        if(current.isEmpty()) return true;
        int comparison=compare(next,current);
        return comparison>0 || (comparison==0 && !hasEjs(installed));
    }
    private static int compare(String left,String right) {
        String[] a=left.split("\\."),b=right.split("\\.");
        for(int i=0;i<Math.max(a.length,b.length);i++) {
            int n=Integer.compare(i<a.length?Integer.parseInt(a[i]):0,i<b.length?Integer.parseInt(b[i]):0);
            if(n!=0) return n;
        }
        return 0;
    }
    static void install(File source,File target) throws IOException {
        Files.move(source.toPath(),target.toPath(),StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING);
    }
    private static byte[] read(InputStream in) throws IOException {
        ByteArrayOutputStream out=new ByteArrayOutputStream();
        byte[] b=new byte[1024];int n;
        while((n=in.read(b))!=-1) { if(out.size()+n>8192) throw new IOException("Invalid engine metadata");out.write(b,0,n); }
        return out.toByteArray();
    }
}
