package com.hassan.luqta;

import java.io.*;
import java.nio.file.Files;

/** Cleanup is scoped to app-owned job folders, with no symbolic-link traversal. */
final class DownloadFiles {
    static boolean media(File file){return file.getName().toLowerCase(java.util.Locale.ROOT).matches(".*\\.(mp4|webm|mp3|m4a|jpg|jpeg|png|webp)$");}
    static long orphan(File file,boolean remove) throws IOException {
        if(Files.isSymbolicLink(file.toPath())) return 0;
        if(file.isDirectory() && ".tmp".equals(file.getName())) return remove?clean(file,null):size(file);
        if(file.isFile()) {if(media(file)) return 0;long bytes=file.length();return !remove || file.delete()?bytes:0;}
        long bytes=0;File[] children=file.listFiles();if(children!=null) for(File child:children) bytes+=orphan(child,remove);
        if(remove) file.delete();return bytes;
    }
    static long size(File file){
        if(Files.isSymbolicLink(file.toPath())) return 0;
        if(file.isFile()) return file.length();
        long n=0;File[] files=file.listFiles();if(files!=null) for(File child:files) n+=size(child);return n;
    }
    static long clean(File root,File keep) throws IOException {
        if(Files.isSymbolicLink(root.toPath())) return 0;
        String allowed=root.getCanonicalPath()+File.separator;
        String protectedPath=keep==null?null:keep.getCanonicalPath();
        return cleanInside(root,allowed,protectedPath);
    }
    private static long cleanInside(File file,String allowed,String keep) throws IOException {
        if(Files.isSymbolicLink(file.toPath())) return 0;
        String path=file.getCanonicalPath();
        if((!path.startsWith(allowed) && !path.equals(allowed.substring(0,allowed.length()-1))) || path.equals(keep)) return 0;
        long bytes=0;
        File[] children=file.listFiles();
        if(children!=null) for(File child:children) bytes+=cleanInside(child,allowed,keep);
        if(file.isFile()){long length=file.length();if(file.delete()) bytes+=length;}
        else file.delete();
        return bytes;
    }
}
