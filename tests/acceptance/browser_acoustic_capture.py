"""Optional capture of media-element output and visible transition screenshots."""
import base64
import hashlib
from pathlib import Path
import struct


AUDIO_CAPTURE_INSTALL = r"""async (mediaElementId = 'audioEl') => {
  if (window.__acousticCapture) return window.__acousticCapture.installation;
  // Local WebUI playback uses a muted video clock plus a separate audible audioEl.
  // Local sources use audioEl; direct HLS uses the audible player element.
  if (!['audioEl', 'player'].includes(mediaElementId)) return {status: 'unsupported'};
  const media = document.getElementById(mediaElementId);
  const clockMedia = document.querySelector('#player');
  const start = document.querySelector('#start');
  if (!media || !start || !window.MediaRecorder) {
    return {status: 'unsupported'};
  }
  if (!Number.isFinite(media.volume) || media.volume <= 0) {
    return {status: 'media_element_muted', media_element_id: media.id || null,
      media_element_muted: media.muted, media_element_volume: media.volume};
  }
  const Context = window.AudioContext || window.webkitAudioContext;
  if (!Context) return {status: 'audio_context_unsupported'};
  let context, recorder, destination, source, analyser;
  try {
    context = new Context({latencyHint: 'interactive'});
    source = context.createMediaElementSource(media);
    destination = context.createMediaStreamDestination();
    analyser = context.createAnalyser();
    analyser.fftSize = 1024;
    source.connect(analyser);
    analyser.connect(context.destination);
    source.connect(destination);
  } catch {
    return {status: 'audio_graph_unavailable'};
  }
  const mimeType = ['audio/webm;codecs=opus', 'audio/webm']
    .find(type => MediaRecorder.isTypeSupported(type));
  if (!mimeType) return {status: 'audio_recorder_unsupported'};
  recorder = new MediaRecorder(destination.stream, {mimeType});
  const chunks = [];
  const outputClockSamples = [];
  const signalSamples = new Float32Array(analyser.fftSize);
  let signalObserved = false;
  let status = 'installed';
  let clockError = false;
  let startPerformanceAfterMs = null;
  let startContextTimeSeconds = null;
  let clockTimer = null;
  const sampler = () => window.__realAcceptanceSampler;
  const pcmBlocks = [], pcmSamples = [], pcmActions = [], witnesses = [];
  const pendingWitnesses = new Map();
  let witnessSequence = 0, witnessTimer;
  let graphError = null;
  // Operational failure deadline; recorded causal intervals retain their full width.
  const graphReplyDeadlineMs = 1000;
  const failGraph = reason => {
    if (graphError) return new Error(graphError);
    graphError = reason; status = reason;
    clearInterval(witnessTimer); clearInterval(clockTimer);
    for (const pending of pendingWitnesses.values()) {
      clearTimeout(pending.timer); pending.reject(new Error(reason));
    }
    pendingWitnesses.clear();
    if (stopPCM) {
      clearTimeout(stopPCM.timer); stopPCM.reject(new Error(reason)); stopPCM = null;
    }
    if (window.__immutableCaptionFixture?.held) window.__immutableCaptionFixture.release();
    if (recorder.state === 'recording') recorder.stop();
    context.close().catch(() => {});
    return new Error(reason);
  };
  const observe = () => {
    if (graphError) return Promise.reject(new Error(graphError));
    const nonce = 'graph-' + (++witnessSequence);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => failGraph('causal_graph_reply_timeout'), graphReplyDeadlineMs);
      pendingWitnesses.set(nonce, {resolve, reject, timer,
        send_after_ms: performance.now() - sampler().started});
      worklet.port.postMessage({kind: 'observe', nonce});
    });
  };
  let worklet, stopPCM;
  try {
    const module = new Blob([`class Capture extends AudioWorkletProcessor {
      constructor() { super(); this.sequence = 0; this.active = true; this.nonces = [];
        this.port.onmessage = event => { if (event.data?.kind === 'observe')
          this.nonces.push(event.data.nonce); else if (event.data === 'stop') {
          this.active = false; this.port.postMessage({kind: 'stopped'}); } }; }
      process(inputs) {
        if (this.active) {
          const samples = new Float32Array(inputs[0]?.[0] || 128);
          this.port.postMessage({kind: 'block', sequence: this.sequence++,
            start_frame: currentFrame, frame_count: samples.length,
            context_time_seconds: currentTime, witness_nonces: this.nonces.splice(0),
            samples}, [samples.buffer]);
        }
        return true;
      }
    } registerProcessor('acoustic-pcm', Capture);`], {type: 'text/javascript'});
    const moduleURL = URL.createObjectURL(module);
    try { await context.audioWorklet.addModule(moduleURL); }
    finally { URL.revokeObjectURL(moduleURL); }
    worklet = new AudioWorkletNode(context, 'acoustic-pcm', {channelCount: 1,
      channelCountMode: 'explicit'});
    worklet.port.onmessage = ({data}) => {
      if (graphError) return;
      if (data.kind === 'stopped') {
        if (stopPCM) { clearTimeout(stopPCM.timer); stopPCM.resolve(); stopPCM = null; }
        return;
      }
      const {samples, kind, ...block} = data;
      pcmBlocks.push(block); pcmSamples.push(samples);
      for (const nonce of block.witness_nonces) {
        const pending = pendingWitnesses.get(nonce);
        if (!pending) { status = 'causal_nonce_invalid'; continue; }
        const witness = {nonce, send_after_ms: pending.send_after_ms,
          receive_after_ms: performance.now() - sampler().started,
          sequence: block.sequence, start_frame: block.start_frame, frame_count: block.frame_count,
          sample_rate_hz: context.sampleRate, channels: 1};
        witnesses.push(witness); pendingWitnesses.delete(nonce);
        clearTimeout(pending.timer); pending.resolve(witness);
      }
    };
    const silent = context.createGain(); silent.gain.value = 0;
    source.connect(worklet); worklet.connect(silent); silent.connect(context.destination);
    const snapshot = () => ({frame: Math.round(context.currentTime * context.sampleRate),
      performance_after_ms: performance.now() - (sampler()?.started || 0),
      media_time_seconds: media.currentTime, paused: media.paused,
      seeking: media.seeking, output_timestamp: context.getOutputTimestamp()});
    for (const name of ['pause', 'play']) {
      const original = media[name].bind(media);
      media[name] = (...args) => {
        const before = snapshot(); const result = original(...args); const after = snapshot();
        if (name === 'pause' ? !before.paused : before.paused)
          pcmActions.push({kind: name === 'play' ? 'resume' : 'pause', before, after});
        return result;
      };
    }
    for (const kind of ['seeking', 'seeked']) media.addEventListener(kind, () => {
      const before = snapshot(); pcmActions.push({kind, before, after: snapshot()});
    });
  } catch { return {status: 'pcm_capture_unsupported'}; }
  const recordOutputTime = () => {
    analyser.getFloatTimeDomainData(signalSamples);
    let energy = 0;
    for (const value of signalSamples) energy += value * value;
    if (Math.sqrt(energy / signalSamples.length) > 0.0001) signalObserved = true;
    if (!sampler() || context.state !== 'running') return;
    if (typeof context.getOutputTimestamp !== 'function') { clockError = true; return; }
    let stamp;
    try { stamp = context.getOutputTimestamp(); }
    catch { clockError = true; return; }
    if (!Number.isFinite(stamp.contextTime) || !Number.isFinite(stamp.performanceTime)) {
      clockError = true;
      return;
    }
    outputClockSamples.push({
      performance_time_after_ms: stamp.performanceTime - sampler().started,
      context_time_seconds: stamp.contextTime,
      audio_element_media_time_seconds: Number.isFinite(media.currentTime)
        ? media.currentTime : null,
      player_media_time_seconds: Number.isFinite(clockMedia?.currentTime)
        ? clockMedia.currentTime : null
    });
  };
  recorder.addEventListener('dataavailable', event => {
    if (event.data && event.data.size) chunks.push(event.data);
  });
  recorder.addEventListener('start', () => {
    status = 'recording';
    startPerformanceAfterMs = performance.now() - sampler().started;
    startContextTimeSeconds = context.currentTime;
    recordOutputTime();
    clockTimer = setInterval(recordOutputTime, 50);
    if (window.__immutableCaptionFixture)
      witnessTimer = setInterval(() => { observe().catch(() => {}); }, 10);
  });
  recorder.addEventListener('error', () => { status = 'recorder_error'; });
  start.addEventListener('click', () => {
    context.resume().then(() => {
      if (recorder.state === 'inactive') recorder.start(100);
    }).catch(() => { status = 'audio_context_resume_failed'; });
  }, {once: true});
  window.__acousticCapture = {
    observe,
    installation: {status: 'installed', mime_type: mimeType,
      media_element_id: media.id || null, clock_media_element_id: clockMedia?.id || null,
      sample_rate_hz: context.sampleRate,
      output_clock_supported: typeof context.getOutputTimestamp === "function",
      base_latency_seconds: Number.isFinite(context.baseLatency) ? context.baseLatency : null,
      output_latency_seconds: Number.isFinite(context.outputLatency) ? context.outputLatency : null},
    status: () => graphError || (['recorder_error', 'audio_context_resume_failed'].includes(status)
      ? status : (clockError ? 'clock_error' : status)),
    save: () => new Promise((resolve, reject) => {
      const finish = async () => {
        try {
        if (clockTimer) clearInterval(clockTimer);
        if (witnessTimer) clearInterval(witnessTimer);
        recordOutputTime();
        if (window.__immutableCaptionFixture) await observe();
        await new Promise((done, fail) => {
          stopPCM = {resolve: done, reject: fail,
            timer: setTimeout(() => failGraph('pcm_stop_reply_timeout'), graphReplyDeadlineMs)};
          worklet.port.postMessage('stop');
        });
        const blob = new Blob(chunks, {type: mimeType});
        const link = document.createElement('a');
        const url = URL.createObjectURL(blob);
        link.href = url;
        link.download = 'browser-playback.webm';
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 5000);
        resolve({status, mime_type: mimeType, sample_rate_hz: context.sampleRate,
          media_element_id: media.id || null, clock_media_element_id: clockMedia?.id || null,
          signal_observed: signalObserved,
          media_element_muted: media.muted, media_element_volume: media.volume,
          clock_media_element_muted: clockMedia?.muted ?? null,
          start_performance_after_ms: startPerformanceAfterMs,
          start_context_time_seconds: startContextTimeSeconds,
          clock_uncertainty_ms: 5,
          base_latency_seconds: Number.isFinite(context.baseLatency) ? context.baseLatency : null,
          output_latency_seconds: Number.isFinite(context.outputLatency) ? context.outputLatency : null,
          output_clock_samples: outputClockSamples,
          pcm_capture: {schema_version: 1, sample_rate_hz: context.sampleRate, channels: 1,
            blocks: pcmBlocks, actions: pcmActions,
            ...(window.__immutableCaptionFixture ? {causal_graph: {schema_version: 1,
              sample_contract: 'pcm16_source_quantization_lsb1', witnesses}} : {})},
          physical_output_latency: 'unknown'});
        } catch (error) { reject(failGraph(error.message)); }
      };
      if (recorder.state === 'recording') {
        recorder.addEventListener('stop', finish, {once: true});
        recorder.stop();
      } else {
        finish();
      }
    }),
    savePCM: () => {
      const count = pcmSamples.reduce((sum, block) => sum + block.length, 0);
      const buffer = new ArrayBuffer(44 + count * 2), view = new DataView(buffer);
      const label = (offset, value) => { for (let i = 0; i < value.length; i++)
        view.setUint8(offset + i, value.charCodeAt(i)); };
      label(0, 'RIFF'); view.setUint32(4, 36 + count * 2, true); label(8, 'WAVE');
      label(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
      view.setUint16(22, 1, true); view.setUint32(24, context.sampleRate, true);
      view.setUint32(28, context.sampleRate * 2, true); view.setUint16(32, 2, true);
      view.setUint16(34, 16, true); label(36, 'data'); view.setUint32(40, count * 2, true);
      let offset = 44;
      for (const block of pcmSamples) for (const sample of block) {
        view.setInt16(offset, Math.round(Math.max(-1, Math.min(1, sample)) * 32767), true);
        offset += 2;
      }
      const url = URL.createObjectURL(new Blob([buffer], {type: 'audio/wav'}));
      const link = document.createElement('a'); link.href = url;
      link.download = 'browser-playback.pcm.wav'; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
    }
  };
  window.__acousticCapture.installation = {
    ...window.__acousticCapture.installation,
    media_element_muted: media.muted, media_element_volume: media.volume,
  };
  return window.__acousticCapture.installation;
}"""


VISIBLE_STATE = r"""() => {
  const caption = document.querySelector('#current');
  if (!caption) return {visible: false, visible_text: '', observed_after_ms: null};
  const text = window.__immutableCaptionFixture?.text ?? caption.innerText ?? '';
  const visible = !!text.trim() && !/(正在准备字幕|跳转中|正在跳转)/.test(text.trim())
    && !/^(⏳|⚠)/.test(text.trim()) && caption.getClientRects().length > 0;
  const rect = caption.getBoundingClientRect();
  const captionBounds = window.__immutableCaptionFixture
    ? (visible ? window.__immutableCaptionFixture.metadata?.caption_bounds : null)
    : rect.width > 0 && rect.height > 0
      ? {x: rect.x, y: rect.y, width: rect.width, height: rect.height} : null;
  if (visible && captionBounds && window.__acousticCapture)
    window.__acousticCapture.lastVisibleCaptionBounds = captionBounds;
  const started = window.__realAcceptanceSampler?.started;
  return {visible, visible_text: visible ? text : '',
    observed_after_ms: Number.isFinite(started) ? performance.now() - started : null,
    caption_bounds: captionBounds,
    last_visible_caption_bounds: window.__acousticCapture?.lastVisibleCaptionBounds || null,
    viewport: {width: innerWidth, height: innerHeight},
    device_scale_factor: devicePixelRatio};
}"""


def install_audio_capture(page, media_element_id="audioEl"):
    """Connect a pre-playback audio tap and return capability metadata."""
    if media_element_id not in ("audioEl", "player"):
        raise ValueError("unsupported audio capture element")
    return page.evaluate(AUDIO_CAPTURE_INSTALL, media_element_id)


def save_audio_capture(page, output_path, report_root):
    """Download the browser-rendered output track without overwriting prior evidence."""
    destination = Path(output_path).expanduser().resolve()
    if destination.exists():
        raise FileExistsError("refusing to overwrite existing playback audio")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with page.expect_download(timeout=15_000) as download_event:
        metadata = page.evaluate("() => window.__acousticCapture?.save()")
    if not isinstance(metadata, dict) or metadata.get("status") != "recording":
        return {"status": metadata.get("status") if isinstance(metadata, dict) else "missing"}
    download_event.value.save_as(str(destination))
    if not destination.is_file() or destination.stat().st_size == 0:
        return {"status": "empty_capture"}
    metadata.update({
        "status": "captured",
        "path": destination.relative_to(Path(report_root).resolve()).as_posix(),
        "sha256": _sha256(destination),
    })
    pcm = destination.with_suffix(".pcm.wav")
    if pcm.exists():
        raise FileExistsError("refusing to overwrite existing PCM audio")
    with page.expect_download(timeout=15_000) as pcm_download:
        page.evaluate("() => window.__acousticCapture.savePCM()")
    pcm_download.value.save_as(str(pcm))
    metadata["pcm_capture"].update({
        "path": pcm.relative_to(Path(report_root).resolve()).as_posix(),
        "sha256": _sha256(pcm),
    })
    return metadata


def capture_visible_screenshot(page, output_dir, report_root, index):
    """Save a real screenshot only while the requested visible state stays stable."""
    immutable = page.evaluate("() => window.__immutableCaptionFixture?.hold() || null")
    completion = {}
    try:
        result = _capture_visible_screenshot(page, output_dir, report_root, index, immutable)
    finally:
        if immutable is not None:
            completion = page.evaluate("() => window.__immutableCaptionFixture.release()")
    if result is not None and immutable is not None:
        result["immutable_caption"].update(completion)
    return result


def _capture_visible_screenshot(page, output_dir, report_root, index, immutable):
    before = page.evaluate(VISIBLE_STATE)
    if not isinstance(before, dict):
        return None
    crop_source = "caption" if before.get("caption_bounds") else "last_visible_caption"
    crop_bounds = before.get("caption_bounds") or before.get("last_visible_caption_bounds")
    if not crop_bounds:
        return None
    directory = Path(output_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"transition-{index:04d}.png"
    if destination.exists():
        raise FileExistsError("refusing to overwrite existing transition screenshot")
    page.screenshot(path=str(destination), mask=[page.locator("#url")], scale="css")
    after = page.evaluate(VISIBLE_STATE)
    if (not isinstance(after, dict)
            or before.get("visible") != after.get("visible")
            or before.get("visible_text") != after.get("visible_text")
            or before.get("viewport") != after.get("viewport")
            or before.get("device_scale_factor") != after.get("device_scale_factor")
            or crop_source != ("caption" if after.get("caption_bounds")
                              else "last_visible_caption")
            or crop_bounds != (after.get("caption_bounds")
                               or after.get("last_visible_caption_bounds"))):
        destination.unlink(missing_ok=True)
        return None
    uncertainty_ms = (float(after["observed_after_ms"])
                      - float(before["observed_after_ms"])) / 2
    with destination.open("rb") as image_file:
        header = image_file.read(24)
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        destination.unlink(missing_ok=True)
        return None
    screenshot_width, screenshot_height = struct.unpack(">II", header[16:24])
    if immutable is not None:
        raster = directory / f"raster-{immutable['version']:04d}.png"
        if raster.exists():
            raise FileExistsError("refusing to overwrite an immutable caption raster")
        raster.write_bytes(base64.b64decode(immutable.pop("raster_data_url").split(",", 1)[1], validate=True))
        immutable.update(raster_path=raster.relative_to(Path(report_root).resolve()).as_posix(),
                         raster_sha256=_sha256(raster))
    return {
        "path": destination.relative_to(Path(report_root).resolve()).as_posix(),
        "sha256": _sha256(destination),
        "observed_after_ms": (float(before["observed_after_ms"])
                               + float(after["observed_after_ms"])) / 2,
        "uncertainty_ms": max(1.0, uncertainty_ms),
        "visible": after["visible"], "visible_text": after["visible_text"],
        "viewport_before": before["viewport"], "viewport_after": after["viewport"],
        "device_scale_factor_before": before["device_scale_factor"],
        "device_scale_factor_after": after["device_scale_factor"],
        "caption_bounds_before": before["caption_bounds"],
        "caption_bounds_after": after["caption_bounds"],
        "ocr_crop_bounds": crop_bounds, "ocr_crop_source": crop_source,
        "screenshot_width_px": screenshot_width,
        "screenshot_height_px": screenshot_height,
        **({"immutable_caption": immutable} if immutable is not None else {}),
    }


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
