"""Build an ARM64 APK with the official Android SDK and Eclipse's Java compiler.
Dependencies are pinned Maven artifacts. Signing material stays outside source.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DEPS=ROOT/'dependencies'
BUILD=ROOT/'build'
OUT=ROOT/'outputs'
COORDS=[
 ('io.github.junkfood02.youtubedl-android','library','0.18.1','aar'),
 ('io.github.junkfood02.youtubedl-android','ffmpeg','0.18.1','aar'),
 ('io.github.junkfood02.youtubedl-android','common','0.18.1','aar'),
 ('org.jetbrains.kotlin','kotlin-stdlib','1.7.22','jar'),
 ('org.jetbrains.kotlin','kotlin-stdlib-jdk7','1.7.22','jar'),
 ('org.jetbrains.kotlin','kotlin-stdlib-jdk8','1.7.22','jar'),
 ('org.jetbrains','annotations','13.0','jar'),
 ('commons-io','commons-io','2.5','jar'),
 ('org.apache.commons','commons-compress','1.12','jar'),
 ('com.fasterxml.jackson.core','jackson-databind','2.11.1','jar'),
 ('com.fasterxml.jackson.core','jackson-core','2.11.1','jar'),
 ('com.fasterxml.jackson.core','jackson-annotations','2.11.1','jar')]

def command(args):
 print('Running',Path(str(args[0])).name,flush=True)
 subprocess.run([str(a) for a in args],check=True,cwd=ROOT,stdin=subprocess.DEVNULL)

def fetch(coordinate):
 g,a,v,ext=coordinate; name=f'{a}-{v}.{ext}'; path=DEPS/name
 if path.is_file() and zipfile.is_zipfile(path): return path
 url='https://repo.maven.apache.org/maven2/'+g.replace('.','/')+'/'+a+'/'+v+'/'+name
 temporary=DEPS/(name+'.download')
 with urllib.request.urlopen(url,timeout=90) as response,temporary.open('wb') as out:
  shutil.copyfileobj(response,out)
 if not zipfile.is_zipfile(temporary): raise RuntimeError('Invalid artifact '+name)
 temporary.replace(path)
 return path

def main():
 parser=argparse.ArgumentParser()
 parser.add_argument('--sdk',default=os.environ.get('ANDROID_SDK_ROOT') or os.environ.get('ANDROID_HOME'))
 parser.add_argument('--ecj',default=os.environ.get('LUQTA_ECJ'))
 parser.add_argument('--platform',default=os.environ.get('LUQTA_PLATFORM_JAR'))
 parser.add_argument('--keystore',default=os.environ.get('LUQTA_SIGNING_KEYSTORE'))
 parser.add_argument('--key-alias',default=os.environ.get('LUQTA_SIGNING_ALIAS'))
 parser.add_argument('--password-file',default=os.environ.get('LUQTA_SIGNING_PASSWORD_FILE'))
 parser.add_argument('--key-password-file',default=os.environ.get('LUQTA_SIGNING_KEY_PASSWORD_FILE'))
 args=parser.parse_args()
 if not args.sdk or not args.ecj: parser.error('Specify --sdk and --ecj, or ANDROID_SDK_ROOT and LUQTA_ECJ.')
 if not args.keystore or not args.key_alias or not args.password_file:
  parser.error('Provide an external --keystore, --key-alias and --password-file. Signing keys are never generated or bundled.')
 key=Path(args.keystore).resolve();password=Path(args.password_file).resolve()
 key_password=Path(args.key_password_file).resolve() if args.key_password_file else password
 for p in (key,password,key_password):
  if not p.is_file(): parser.error('Missing signing file: '+str(p))
  if p==ROOT or ROOT in p.parents: parser.error('Keep signing files outside the source project.')
  if p.stat().st_mode & 0o077: parser.error('Signing files must be private to their owner (chmod 600).')
 sdk=Path(args.sdk); compiler=Path(args.ecj)
 tools=sdk/'build-tools/35.0.0'; android=Path(args.platform) if args.platform else sdk/'platforms/android-36/android.jar'
 for p in [compiler,android,tools/'aapt2',tools/'d8',tools/'zipalign',tools/'apksigner']:
  if not p.is_file(): raise RuntimeError('Missing build tool '+str(p))
 if BUILD.exists(): shutil.rmtree(BUILD)
 for d in [DEPS,BUILD,OUT,BUILD/'generated',BUILD/'classes',BUILD/'dex',BUILD/'jni/arm64-v8a']: d.mkdir(parents=True,exist_ok=True)
 for cache in (ROOT/'app/src/main/assets').rglob('__pycache__'): shutil.rmtree(cache)
 jars=[]; hashes=[]
 for coord in COORDS:
  path=fetch(coord); hashes.append({'coordinate':':'.join(coord[:3]),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
  if path.suffix=='.jar': jars.append(path)
  else:
   with zipfile.ZipFile(path) as z:
    name=coord[1]; classes=DEPS/(name+'-classes.jar');classes.write_bytes(z.read('classes.jar'));jars.append(classes)
    for entry in z.namelist():
     if entry.startswith('jni/arm64-v8a/') and entry.endswith('.so'):
      target=BUILD/entry;target.write_bytes(z.read(entry))
 (OUT/'dependency-lock.json').write_text(json.dumps(hashes,indent=2))
 command([tools/'aapt2','compile','--dir',ROOT/'app/src/main/res','-o',BUILD/'resources.zip'])
 command([tools/'aapt2','link','--auto-add-overlay','-I',android,'--manifest',ROOT/'app/src/main/AndroidManifest.xml','--java',BUILD/'generated','--extra-packages','com.yausername.youtubedl_android','--min-sdk-version','29','--target-sdk-version','35','-A',ROOT/'app/src/main/assets','-R',BUILD/'resources.zip','-o',BUILD/'base.apk'])
 sources=list((ROOT/'app/src/main/java').rglob('*.java'))+list((BUILD/'generated').rglob('*.java'))
 command(['java','-jar',compiler,'-1.8','-encoding','UTF-8','-warn:none','-classpath',os.pathsep.join(str(p) for p in [android,*jars]),'-d',BUILD/'classes',*sources])
 classes=BUILD/'app-classes.jar'
 with zipfile.ZipFile(classes,'w',zipfile.ZIP_DEFLATED) as z:
  for p in (BUILD/'classes').rglob('*.class'): z.write(p,str(p.relative_to(BUILD/'classes')))
 command([tools/'d8','--release','--min-api','29','--lib',android,'--output',BUILD/'dex',classes,*jars])
 unsigned=BUILD/'unsigned.apk'
 with zipfile.ZipFile(BUILD/'base.apk') as base, zipfile.ZipFile(unsigned,'w') as apk:
  for e in base.infolist(): apk.writestr(e,base.read(e.filename))
  for p in (BUILD/'dex').glob('*.dex'): apk.write(p,p.name,compress_type=zipfile.ZIP_DEFLATED)
  for p in (BUILD/'jni/arm64-v8a').glob('*.so'): apk.write(p,'lib/arm64-v8a/'+p.name,compress_type=zipfile.ZIP_STORED)
 command([tools/'zipalign','-P','16','-f','4',unsigned,BUILD/'aligned.apk'])
 apk=OUT/'Luqta-Android.apk'
 signing=[tools/'apksigner','sign','--ks',key,'--ks-key-alias',args.key_alias,'--ks-pass','file:'+str(password)]
 if key_password!=password: signing.extend(['--key-pass','file:'+str(key_password)])
 command([*signing,'--out',apk,BUILD/'aligned.apk'])
 command([tools/'apksigner','verify','--verbose',apk])
 command([tools/'zipalign','-c','-P','16','4',apk])
 print(json.dumps({'apk':str(apk),'bytes':apk.stat().st_size,'sha256':hashlib.sha256(apk.read_bytes()).hexdigest()}),flush=True)

if __name__=='__main__': main()
