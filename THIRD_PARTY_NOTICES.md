# Third-party components

Luqta Android is provided under GPL-3.0. Bundled components retain their original licenses.

| Component | Version | License/source |
| --- | --- | --- |
| youtubedl-android library, common, FFmpeg module | 0.18.1 | GPL-3.0; https://github.com/yausername/youtubedl-android |
| yt-dlp | 2026.8.19 | Unlicense; https://github.com/yt-dlp/yt-dlp |
| yt-dlp-ejs | 0.8.0 | https://github.com/yt-dlp/ejs |
| Python and Android runtime packages | Bundled by youtubedl-android | Python license and respective sysroot licenses; see upstream BUILD_PYTHON.md |
| FFmpeg native runtime | Bundled by youtubedl-android | FFmpeg/linked-library licenses; see upstream BUILD_FFMPEG.md |
| QuickJS | Bundled by youtubedl-android | MIT; https://bellard.org/quickjs/ |
| Kotlin standard library | 1.7.22 | Apache-2.0 |
| Apache Commons IO | 2.5 | Apache-2.0 |
| Apache Commons Compress | 1.12 | Apache-2.0 |
| Jackson | 2.11.1 | Apache-2.0 |
| JetBrains annotations | 13.0 | Apache-2.0 |

Pinned Maven coordinates and SHA-256 digests are in dependency-lock.json. Source JARs for youtubedl-android's Android wrapper are included in third-party-source/. Its original native runtime build instructions and source locations are in the upstream repository. The yt-dlp and yt-dlp-ejs Python/JavaScript source trees are contained, unmodified, in app/src/main/res/raw/ytdlp (a ZIP file).

The app uses only initialization/updater APIs from the Android wrapper; it supplies its own Python worker protocol and process-group cancellation. The wrapper's unused AppCompat/core-ktx dependencies are unnecessary for these APIs, whose bundled source imports have been inspected.
