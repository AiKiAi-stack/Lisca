# English listening candidate: MIT OpenCourseWare

**Status:** prepared for user audition only. Natural fastness, clarity, single-speaker identity in the excerpt, and caption accuracy remain unconfirmed.

## Source and excerpt

The candidate is a continuous **83.471-second** interval from MIT OpenCourseWare’s [6.006, Introduction to Algorithms, Fall 2011, Lecture 2: “Models of Computation, Document Distance”](https://ocw.mit.edu/courses/6-006-introduction-to-algorithms-fall-2011/resources/lecture-2-models-of-computation-document-distance/). The page identifies **Prof. Erik Demaine** as the instructor and links the lecture video and transcript. The selected interval is **00:47:10.729–00:48:34.200**, covering a BFS/Rubik’s Cube explanation. The timestamps are the boundaries of the official [MIT VTT caption track](https://ocw.mit.edu/courses/6-006-introduction-to-algorithms-fall-2011/1d2fc994429153ec89f4bd0c3c88565b_s-CYnVz-uh4.vtt). The passage comes from one continuous source interval; it is not assembled from sentence clips.

MIT’s linked [video file](https://archive.org/download/MIT6.006F11/MIT6_006F11_lec02_300k.mp4) is served from the [Archive.org item](https://archive.org/details/MIT6.006F11). The item metadata lists a 111,088,551-byte MP4, MD5 `78596cf2bb9698938c12ca18e3037273`, and SHA-1 `e5d8ba4284112e40156bbca81b5a86f5985c425e`. The HTTP response observed on 2026-10-25 14:50:51 Asia/Shanghai (06:50:51 UTC) reported ETag `"55b79778-69f13a7"`, `Accept-Ranges: bytes`, and the same content length. The full video was not downloaded: bounded byte-range reads totaling 7,279,527 bytes supplied the selected audio. The full-file SHA-256 is therefore null / not computed. The decoded source interval has SHA-256 `81f9f58238cdb53f802b2ae76ef5084b9035b8f5ecc50108b14bd0d8b03643a8`.

## Prepared files

The WAV, timed transcript, source manifest, and scorer-ready reference manifest are in the ignored `uploads/acceptance-media/new-candidates/` directory:

- `en-fast-speech-mitocw-erik-demaine-lecture-2.wav` — 16 kHz, mono, signed 16-bit PCM; 83.471 seconds; SHA-256 `f9633213924e851c9d7ef83504fa0e165150ccd7a33cb0829405a9e06fb02529`.
- `en-fast-speech-mitocw-erik-demaine-lecture-2.txt` — the 38 timed caption cues covering the interval; text-file SHA-256 `64c9f22b27bb3fed84d1e227f9a5e69adb3a081da4309ee3a5f41041950185a4`.
- `en-fast-speech-mitocw-erik-demaine-lecture-2.json` — source identifiers, official source hashes, excerpt and output hashes, timing, format, license, and review status.
- `en-fast-speech-mitocw-erik-demaine-lecture-2-reference.json` — 38 scorer-ready reference items mapped to the clip timeline, bound to the WAV hash; natural speed, clarity, and reference checks remain pending.

Source audio is 48 kHz stereo AAC. The WAV was downmixed and resampled to 16 kHz mono; there was no time stretch, speed change, segment stitching, or added silence. The pause inside the selected interval remains in the audio. The reference text is copied from the MIT caption track without ASR generation or transcript-quality claims; **`manual_review_pending` is true**.

## License and limits

MIT OCW’s [Terms of Use](https://ocw.mit.edu/pages/privacy-and-terms-of-use/) state that the course content is under **CC BY-NC-SA 4.0**; the [Creative Commons deed](https://creativecommons.org/licenses/by-nc-sa/4.0/) permits adaptation with attribution, prohibits commercial use, and requires share-alike for redistributed adaptations. Attribute the material to MIT OpenCourseWare and Prof. Erik Demaine, link to the license, and identify the excerpt and audio conversion as changes. Prepared files stay in the ignored local research directory; later reuse remains subject to the cited license.

This prepares one audition file; it does not accept the material as naturally fast or clear. The user must listen to the WAV, confirm it is single-speaker and suitably fast/clear, and review the captions against the sound before any later recognition evaluation. Existing AMI, LibriSpeech speaker-61, and JFK decisions remain unchanged.
