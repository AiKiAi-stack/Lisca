# Single-speaker local acceptance media (2026-10-24)

This prepares fixed Mandarin and English audio plus source transcripts for ticket 09. It is **media preparation evidence only**: no real ASR call was made, no API key was read, and the samples have not been manually auditioned or transcript-checked. The reference text is copied from the corpus transcript, so it is independent of this application's recognition output; human verification remains pending.

The reproducible downloader is [`test/prepare_original_acceptance_media.py`](../../test/prepare_original_acceptance_media.py). Per-utterance IDs, source SHA-256 values, transcript text, exact sample counts, combined-file hashes, and source/license metadata are in [`single-speaker-acceptance-media-2026-10-24.json`](single-speaker-acceptance-media-2026-10-24.json). Generated WAVs are in the ignored local directory `uploads/acceptance-media/`; binary media is intentionally not tracked by Git.

## Sources and fixed selections

**Mandarin: AISHELL-3.** The [official dataset card](https://huggingface.co/datasets/AISHELL/AISHELL-3) describes a Mandarin corpus with multiple speakers and lists Apache-2.0. The selection pins repository revision [`f20d5db4a31fe779ef07bb1af4ea92da5c786622`](https://huggingface.co/datasets/AISHELL/AISHELL-3/tree/f20d5db4a31fe779ef07bb1af4ea92da5c786622). The speaker identity/profile comes from the pinned [`spk-info.txt`](https://huggingface.co/datasets/AISHELL/AISHELL-3/resolve/f20d5db4a31fe779ef07bb1af4ea92da5c786622/spk-info.txt); the transcript comes from pinned [`train/content.txt`](https://huggingface.co/datasets/AISHELL/AISHELL-3/resolve/f20d5db4a31fe779ef07bb1af4ea92da5c786622/train/content.txt). The deterministic rule chooses the lexicographically first speaker ID present in both files, then takes that speaker's training utterances in numeric filename order until decoded source audio reaches 300 seconds. This selected `SSB0005` (dataset profile: age band B, female, north accent) and 59 utterances, `SSB00050001` through `SSB00050061` with gaps where the corpus filename sequence has them. The published transcript alternates Chinese characters and pinyin; the manifest retains the character tokens in source order.

**English: LibriSpeech test-clean.** The [OpenSLR corpus page](https://www.openslr.org/12) describes LibriSpeech as read English speech from LibriVox; the [Hugging Face dataset card](https://huggingface.co/datasets/openslr/librispeech_asr) provides the hosted `test.clean` split and lists CC BY 4.0. The selected dataset revision is [`71cacbfb7e2354c4226d01e70d77d5fca3d04ba1`](https://huggingface.co/datasets/openslr/librispeech_asr/tree/71cacbfb7e2354c4226d01e70d77d5fca3d04ba1). The selection chooses the lowest numeric `speaker_id` in the pinned `test.clean` row order, then takes that speaker's utterances in row order until decoded source audio reaches 300 seconds. This selected speaker `61`, 67 utterances across chapters `70968` and `70970`. Text is the corpus `test.clean` row text as published. The script obtains audio through the [Dataset Viewer rows API](https://huggingface.co/docs/dataset-viewer/en/rows), verifies that the current `main` reference still equals the pinned commit, and checks that each temporary audio source identifies that commit.

Both selections use corpus IDs and fixed order only; no ASR output or perceived recognition quality influenced selection. Dataset speaker labels are recorded as corpus metadata and have not been independently verified by listening.

## Prepared assets

All WAVs are mono, 16 kHz, signed 16-bit PCM. Every complete source utterance is preserved and utterances are separated by exactly 0.25 seconds of inserted digital silence. No source clip was trimmed, denoised, or speed-adjusted. `source utterance audio` below is the full decoded audio duration of the source clips and includes any natural pauses already inside a clip. It is separated from the exact inserted silence; no VAD or phonation-only measurement was performed. The five-minute threshold is met by source utterance clips alone (302.119 s Mandarin and 301.2249375 s English); the 14.500 s and 16.500 s pads are excluded from that comparison.

| Language / role | Source utterances | Source utterance audio | Inserted silence | WAV duration | Combined WAV SHA-256 |
|---|---:|---:|---:|---:|---|
| Mandarin reference | 59 | 302.119 s | 14.500 s | 316.619 s | `d40bb3a49d88cf35f52574650d593573048f89f95aba09b704b2fdbe25811317` |
| Mandarin fast-speech listening candidate | 11 | 62.9561875 s | 2.500 s | 65.4561875 s | `6ac3ae3417bd23ba862fca14288b0c33cb07be73ad24858778589fa7f49a872e` |
| English reference | 67 | 301.2249375 s | 16.500 s | 317.7249375 s | `4b7d55148fed309e1d71d941d75e89a64b1068a1473163f5f6a9ac4ab6fd7523` |
| English fast-speech listening candidate | 13 | 62.035 s | 3.000 s | 65.035 s | `4ed3d5949fe19567f04e3adfa80a991e73de5db6698845fe23500b99875fdbbe` |

Each candidate is the highest transcript-rate contiguous window of at least 60 seconds within the fixed speaker sample, with earliest start breaking ties. The rate proxy is 2.604986 Han characters per source-audio second for Mandarin and 3.143387 whitespace-delimited words per second for English. These are search aids, not a fast-speech verdict; especially for Mandarin the proxy does not establish that a listener would call the segment fast. Both candidate assets are original recordings at their original speed and remain **pending human listening**. They must not be cited as confirmed fast-speech evidence until somebody reviews them.

The combined WAV hash is over the complete produced file, including inserted silence. The manifest also records an ordered source-utterance digest and every source clip's SHA-256 and reference text. The source payload totals 32,857,277 bytes across 126 unique clips; the fast candidates reuse clips from their language's reference sample.

## Reproduction and limits

From the repository root, run:

```bash
/home/wbb/.local/share/stream-with-asr/dev-venv/bin/python test/prepare_original_acceptance_media.py
```

The script caches corpus data and selected source audio outside the repository under `/home/wbb/.local/share/stream-with-asr/media-source-cache` by default. It uses at most 25 seconds per HTTP request, makes at most two attempts per request, caps one audio response at 12 MB, and stops a run if response bodies exceed 500 MB in total. The first run that completed both media selections reported 6,432,997 response-body bytes and reused the already cached Mandarin source clips. A later cache-only reproducibility run reported 225,148 response-body bytes in the manifest. The earlier bounded pass had fetched those 59 Mandarin clips before a local script error; their exact aggregate payload is recorded through the per-utterance `source_bytes` in the manifest. The unique source-audio payload for both languages is 32,857,277 bytes. No complete dataset archive was downloaded. Temporary Dataset Viewer signed URLs are held in memory only and are not written to the cache, manifest, or logs.

AISHELL-3 attribution and license: “AISHELL-3, AISHELL”, [dataset card](https://huggingface.co/datasets/AISHELL/AISHELL-3), [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0). LibriSpeech attribution and license: “LibriSpeech ASR corpus, OpenSLR 12”, [corpus page](https://www.openslr.org/12), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Confirm any additional source terms before redistributing these local derivatives.

## Remaining acceptance work

- Manually listen to the four prepared assets, confirm that the chosen spans contain the expected language and one speaker, and review transcript/audio correspondence. The dataset speaker label and transcript alone do not prove those observations.
- Confirm whether each listening candidate is genuinely fast speech; replace or mark it unavailable if it is not. No synthetic acceleration may stand in for a natural fast-speech sample.
- Run the bounded, user-authorized real recognition/quality check separately and report CER/WER, synchronization, readability, and sample identity. No accuracy result is claimed here.
- Ticket 09's full local playback, long-file controls/resource measurements, and browser evidence remain outstanding; these assets do not complete ticket 09.
