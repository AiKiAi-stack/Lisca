async ({mediaElementId = 'audioEl', maxSeconds = 600}) => {
  if (window.__engineeringCapture) return window.__engineeringCapture.installation;
  const media = document.getElementById(mediaElementId);
  const start = document.getElementById('start');
  const origin = window.__realAcceptanceSampler?.started;
  if (!['audioEl', 'player'].includes(mediaElementId) || !media || !start
      || !Number.isFinite(origin) || typeof window.__engineeringPcmWrite !== 'function'
      || !window.isSecureContext || !window.AudioWorkletNode || !window.AudioContext) {
    return {status: 'audio_worklet_unsupported'};
  }
  if (!Number.isFinite(media.volume) || media.volume <= 0) {
    return {status: 'media_element_muted', media_element_id: mediaElementId};
  }
  const contextId = crypto.randomUUID();
  let context, node, source;
  const maxQueuedBatches = 4;
  const moduleCode = `
    class EngineeringPcmTap extends AudioWorkletProcessor {
      constructor(options) {
        super();
        this.id = options.processorOptions.contextId;
        this.maxFrames = options.processorOptions.maxFrames;
        this.active = false;
        this.failed = false;
        this.sequence = 0;
        this.totalFrames = 0;
        this.firstFrame = null;
        this.endFrame = null;
        this.channels = null;
        this.parts = [];
        this.quanta = [];
        this.bufferedFrames = 0;
        this.inFlight = new Set();
        this.peakQueued = 0;
        this.signal = false;
        this.port.onmessage = ({data}) => {
          if (data.kind === 'start') this.active = !this.failed;
          if (data.kind === 'ack') this.inFlight.delete(data.sequence);
          if (data.kind === 'abort') { this.active = false; this.failed = true; }
          if (data.kind === 'stop') {
            this.active = false;
            this.flush();
            this.port.postMessage({kind: 'final', context_id: this.id,
              batch_count: this.sequence, frame_count: this.totalFrames,
              first_frame: this.firstFrame, end_frame: this.endFrame,
              sample_rate_hz: sampleRate, channel_count: this.channels,
              signal_observed: this.signal, worklet_failed: this.failed,
              peak_queued_batches: this.peakQueued});
          }
        };
        this.port.postMessage({kind: 'ready'});
      }
      fail(reason) {
        if (!this.failed) this.port.postMessage({kind: 'capture_error',
          context_id: this.id, reason, current_frame: currentFrame});
        this.failed = true;
        this.active = false;
      }
      flush() {
        if (!this.parts.length) return;
        if (this.inFlight.size >= ${maxQueuedBatches}) {
          this.fail('worklet_queue_overflow');
          this.parts = []; this.quanta = []; this.bufferedFrames = 0;
          return;
        }
        const bytes = new Uint8Array(this.bufferedFrames * this.channels * 4);
        let offset = 0;
        for (const part of this.parts) { bytes.set(part, offset); offset += part.length; }
        const sequence = this.sequence++;
        this.inFlight.add(sequence);
        this.peakQueued = Math.max(this.peakQueued, this.inFlight.size);
        this.port.postMessage({kind: 'pcm_batch', context_id: this.id, sequence,
          sample_rate_hz: sampleRate, channel_count: this.channels,
          quanta: this.quanta, bytes: bytes.buffer}, [bytes.buffer]);
        this.parts = []; this.quanta = []; this.bufferedFrames = 0;
      }
      process(inputs, outputs) {
        const input = inputs[0];
        const output = outputs[0];
        for (let channel = 0; channel < output.length; channel++) {
          if (input[channel]) output[channel].set(input[channel]);
          else output[channel].fill(0);
        }
        if (!this.active || (!input.length && this.firstFrame === null)) return true;
        const count = output[0]?.length || 0;
        if (!count || (input.length && input.length !== output.length)
            || (this.channels !== null && this.channels !== output.length)) {
          this.fail('output_channel_format_changed'); return true;
        }
        if (this.endFrame !== null && currentFrame !== this.endFrame) {
          this.fail('worklet_frame_gap_or_overlap'); return true;
        }
        if (this.totalFrames + count > this.maxFrames) {
          this.fail('capture_duration_limit'); return true;
        }
        this.channels = output.length;
        if (this.firstFrame === null) this.firstFrame = currentFrame;
        const bytes = new Uint8Array(count * this.channels * 4);
        const view = new DataView(bytes.buffer);
        for (let frame = 0; frame < count; frame++) {
          for (let channel = 0; channel < this.channels; channel++) {
            const value = output[channel][frame];
            view.setFloat32((frame * this.channels + channel) * 4, value, true);
            if (Math.abs(value) > 0.000001) this.signal = true;
          }
        }
        this.parts.push(bytes);
        this.quanta.push({current_frame: currentFrame, frame_count: count});
        this.totalFrames += count;
        this.endFrame = currentFrame + count;
        this.bufferedFrames += count;
        if (this.bufferedFrames >= 4096) this.flush();
        return true;
      }
    }
    registerProcessor('engineering-pcm-tap', EngineeringPcmTap);
  `;
  try {
    context = new AudioContext({latencyHint: 'interactive'});
    await context.suspend();
    if (typeof context.getOutputTimestamp !== 'function') {
      await context.close();
      return {status: 'output_clock_unsupported'};
    }
    const moduleUrl = URL.createObjectURL(new Blob([moduleCode], {type: 'text/javascript'}));
    try { await context.audioWorklet.addModule(moduleUrl); }
    finally { URL.revokeObjectURL(moduleUrl); }
    node = new AudioWorkletNode(context, 'engineering-pcm-tap', {
      numberOfInputs: 1, numberOfOutputs: 1,
      channelCount: context.destination.channelCount,
      channelCountMode: 'explicit', channelInterpretation: 'speakers',
      outputChannelCount: [context.destination.channelCount],
      processorOptions: {contextId, maxFrames: Math.ceil(maxSeconds * context.sampleRate)},
    });
    source = context.createMediaElementSource(media);
  } catch {
    if (context) await context.close().catch(() => {});
    return {status: 'audio_graph_unavailable'};
  }

  let status = 'installed';
  let failure = null;
  let segment = 0;
  let lastClock = null;
  let clockTimer = null;
  let pendingWrites = 0;
  let writeChain = Promise.resolve();
  let finishResolve = null;
  let closing = false;
  let finalSummary = null;
  const fail = reason => {
    if (!failure) failure = reason;
    status = 'capture_error';
    node.port.postMessage({kind: 'abort'});
  };
  const send = payload => {
    if (pendingWrites >= 16) {
      fail('bridge_queue_overflow');
      return Promise.resolve({status: 'rejected'});
    }
    pendingWrites++;
    const promise = writeChain.then(() => window.__engineeringPcmWrite(payload));
    writeChain = promise.then(result => {
      if (result?.status !== 'accepted') fail('sink_rejected');
      return result;
    }).catch(() => { fail('sink_write_failed'); }).finally(() => { pendingWrites--; });
    return writeChain;
  };
  const event = (kind, extra = {}) => ({kind, context_id: contextId, ...extra});
  const contextState = () => {
    const before = performance.now() - origin;
    const time = context.currentTime;
    segment++;
    lastClock = null;
    send(event('context_state', {clock_segment: segment, state: context.state,
      mapping_barrier: true, context_time_seconds: time,
      context_frame: Math.round(time * context.sampleRate),
      call_before_after_ms: before, call_after_after_ms: performance.now() - origin}));
  };
  const sampleClock = () => {
    if (context.state !== 'running') return;
    const before = performance.now() - origin;
    let stamp;
    try { stamp = context.getOutputTimestamp(); }
    catch { fail('output_timestamp_failed'); return; }
    const after = performance.now() - origin;
    const valid = Number.isFinite(stamp.contextTime) && Number.isFinite(stamp.performanceTime);
    let reason = !valid ? 'output_timestamp_nonfinite' : null;
    if (valid && (stamp.contextTime <= 0 || stamp.performanceTime <= 0)) reason = 'output_timestamp_unstarted';
    if (valid && lastClock && (stamp.contextTime <= lastClock.contextTime
        || stamp.performanceTime <= lastClock.performanceTime)) reason = 'output_timestamp_not_advanced';
    send(event('clock_sample', {clock_segment: segment, state: context.state,
      context_time_seconds: valid ? stamp.contextTime : null,
      performance_time_ms: valid ? stamp.performanceTime : null,
      performance_time_after_ms: valid ? stamp.performanceTime - origin : null,
      call_before_after_ms: before, call_after_after_ms: after,
      usable: reason === null, ...(reason ? {invalid_reason: reason, mapping_barrier: true} : {})}));
    if (reason) segment++;
    if (valid) lastClock = stamp;
    else fail(reason);
  };
  const ready = new Promise(resolve => {
    node.port.onmessage = ({data}) => {
      if (data.kind === 'ready') { resolve(); return; }
      if (data.kind === 'pcm_batch') {
        const bytes = new Uint8Array(data.bytes);
        let binary = '';
        for (let index = 0; index < bytes.length; index += 8192)
          binary += String.fromCharCode(...bytes.subarray(index, index + 8192));
        const {bytes: _bytes, ...metadata} = data;
        send({...metadata, pcm_base64: btoa(binary), delivered_after_ms: performance.now() - origin})
          .then(result => {
            if (result?.status === 'accepted') node.port.postMessage({kind: 'ack', sequence: data.sequence});
          });
      } else if (data.kind === 'capture_error') {
        send(data);
        fail(data.reason);
      } else if (data.kind === 'final') {
        finalSummary = data;
        if (finishResolve) finishResolve();
      }
    };
  });
  source.connect(node);
  node.connect(context.destination);
  await Promise.race([ready, new Promise((_, reject) => setTimeout(() => reject(new Error('worklet ready timeout')), 3000))]);
  context.addEventListener('statechange', contextState);
  node.addEventListener('processorerror', () => {
    send(event('capture_error', {reason: 'worklet_processor_error'}));
    fail('worklet_processor_error');
  });
  const mediaDevices = typeof navigator === 'undefined' ? null : navigator.mediaDevices;
  const sinkChangeSupported = 'onsinkchange' in context;
  const deviceChangeSupported = !!mediaDevices && 'ondevicechange' in mediaDevices
    && typeof mediaDevices.addEventListener === 'function'
    && typeof mediaDevices.removeEventListener === 'function';
  const deviceChange = source => {
    if (closing) return;
    segment++;
    lastClock = null;
    send(event('device_change', {source, state: context.state,
      clock_segment: segment, mapping_barrier: true,
      observed_after_ms: performance.now() - origin}));
  };
  const onSinkChange = () => deviceChange('audio_context_sinkchange');
  const onDeviceChange = () => deviceChange('media_devices_devicechange');
  if (sinkChangeSupported) context.addEventListener('sinkchange', onSinkChange);
  if (deviceChangeSupported) mediaDevices.addEventListener('devicechange', onDeviceChange);
  for (const name of ['play', 'playing', 'pause', 'waiting', 'ended', 'volumechange']) {
    media.addEventListener(name, () => {
      if (!closing) send(event('media_state', {event: name, state: context.state,
        observed_after_ms: performance.now() - origin, media_time_seconds: media.currentTime,
        media_element_muted: media.muted, media_element_volume: media.volume}));
    });
  }
  contextState();
  await writeChain;
  if (failure) return {status: 'capture_error'};
  start.addEventListener('click', () => {
    status = 'recording';
    node.port.postMessage({kind: 'start'});
    context.resume().catch(() => {
      send(event('capture_error', {reason: 'audio_context_resume_failed'}));
      fail('audio_context_resume_failed');
    });
    clockTimer = setInterval(sampleClock, 50);
  }, {once: true, capture: true});
  const installation = {status: 'installed', media_element_id: mediaElementId,
    media_element_muted: media.muted, media_element_volume: media.volume,
    context_id: contextId, sample_rate_hz: context.sampleRate,
    sampler_performance_origin_ms: origin, output_clock_supported: true,
    measurement_scope: 'browser_output_timestamp_estimate',
    routing: 'media_element_audio_worklet_destination', transparent_pcm: true,
    captured_channel_domain: 'audio_context_destination_speaker_mix',
    channel_count_mode: 'explicit', channel_interpretation: 'speakers',
    worklet_output_channel_count: context.destination.channelCount,
    destination_channel_count: context.destination.channelCount,
    destination_max_channel_count: context.destination.maxChannelCount,
    device_change_observability: {
      audio_context_sinkchange_supported: sinkChangeSupported,
      media_devices_devicechange_supported: deviceChangeSupported,
      status: sinkChangeSupported || deviceChangeSupported ? 'best_effort' : 'unknown',
      system_output_route: 'not_independently_observed'},
    output_clock_error_bound_ms: null, max_queued_batches: maxQueuedBatches,
    batch_target_frames: 4096, base_latency_seconds: context.baseLatency ?? null,
    output_latency_seconds: context.outputLatency ?? null};
  window.__engineeringCapture = {
    installation, context, status: () => status,
    save: async () => {
      closing = true;
      clearInterval(clockTimer);
      context.removeEventListener('statechange', contextState);
      if (sinkChangeSupported) context.removeEventListener('sinkchange', onSinkChange);
      if (deviceChangeSupported) mediaDevices.removeEventListener('devicechange', onDeviceChange);
      sampleClock();
      if (context.state === 'running') {
        await new Promise(resolve => {
          finishResolve = resolve;
          node.port.postMessage({kind: 'stop'});
          setTimeout(() => { if (!finalSummary) fail('worklet_flush_timeout'); resolve(); }, 3000);
        });
      } else fail('flush_context_not_running');
      await writeChain;
      if (failure) await send(event('capture_error', {reason: failure}));
      const summary = {...installation, ...(finalSummary || {}),
        status: !failure && finalSummary && !finalSummary.worklet_failed ? 'captured' : 'unknown',
        media_element_muted: media.muted, media_element_volume: media.volume,
        output_clock_error_bound_ms: null, physical_output_latency: 'unknown'};
      status = summary.status;
      return summary;
    },
  };
  // The existing screenshot helper stores its last real caption bounds here.
  window.__acousticCapture = window.__engineeringCapture;
  return installation;
}
