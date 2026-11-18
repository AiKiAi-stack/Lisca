const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

async function capture({deviceSignals = true} = {}) {
  const records = [];
  const media = Object.assign(new EventTarget(), {volume: 1, muted: false, currentTime: 0});
  const start = new EventTarget();
  const mediaDevices = deviceSignals ? Object.assign(new EventTarget(), {
    ondevicechange: null,
    enumerateDevices() { assert.fail('device enumeration is outside capture scope'); },
    getUserMedia() { assert.fail('microphone access is outside capture scope'); },
  }) : undefined;
  let context, clockSample;
  let stamp = 0;
  class AudioContext extends EventTarget {
    constructor() {
      super();
      context = this;
      this.state = 'suspended';
      this.currentTime = 1;
      this.sampleRate = 48000;
      this.destination = {channelCount: 2, maxChannelCount: 2};
      this.audioWorklet = {addModule: async () => {}};
      if (deviceSignals) this.onsinkchange = null;
    }
    async suspend() { this.state = 'suspended'; }
    async close() { this.state = 'closed'; }
    async resume() {
      this.state = 'running';
      this.dispatchEvent(new Event('statechange'));
    }
    getOutputTimestamp() {
      stamp++;
      return {contextTime: stamp / 20, performanceTime: 100 + stamp * 50};
    }
    createMediaElementSource() { return {connect() {}}; }
  }
  class AudioWorkletNode extends EventTarget {
    constructor() {
      super();
      let listener;
      this.port = {
        set onmessage(value) {
          listener = value;
          queueMicrotask(() => listener({data: {kind: 'ready'}}));
        },
        postMessage(message) {
          if (message.kind === 'stop') queueMicrotask(() => listener({data: {
            kind: 'final', context_id: 'node-fixture', worklet_failed: false,
          }}));
        },
      };
    }
    connect() {}
  }
  const window = {
    __realAcceptanceSampler: {started: 100},
    __engineeringPcmWrite: async record => { records.push(record); return {status: 'accepted'}; },
    isSecureContext: true, AudioContext, AudioWorkletNode,
  };
  const install = vm.runInNewContext(fs.readFileSync(path.join(
    __dirname, '../acceptance/browser_pcm_capture.js'), 'utf8'), {
    window, AudioContext, AudioWorkletNode, navigator: {mediaDevices},
    document: {getElementById: id => id === 'start' ? start : media},
    crypto: {randomUUID: () => 'node-fixture'}, performance: {now: () => 200},
    URL: {createObjectURL: () => 'blob:node-fixture', revokeObjectURL() {}}, Blob,
    setTimeout() {}, setInterval(fn) { clockSample = fn; return 1; }, clearInterval() {},
  });
  const installation = await install({});
  assert.equal(installation.status, 'installed');
  start.dispatchEvent(new Event('click'));
  return {installation, context, mediaDevices, records,
    sample: () => clockSample(), save: () => window.__engineeringCapture.save()};
}

test('both observed device signals split clock mapping and stop writing after save', async () => {
  const tap = await capture();
  tap.sample();
  tap.context.dispatchEvent(new Event('sinkchange'));
  tap.sample();
  tap.mediaDevices.dispatchEvent(new Event('devicechange'));
  tap.sample();
  const saved = await tap.save();
  const changes = tap.records.filter(record => record.kind === 'device_change');
  assert.equal(changes.length, 2);
  assert.deepEqual(changes.map(record => record.source), [
    'audio_context_sinkchange', 'media_devices_devicechange',
  ]);
  assert.deepEqual(changes.map(record => record.clock_segment), [3, 4]);
  assert.ok(changes.every(record => record.mapping_barrier === true));
  assert.deepEqual(tap.records.filter(record => record.kind === 'clock_sample')
    .map(record => record.clock_segment), [2, 3, 4, 4]);
  assert.equal(saved.device_change_observability.status, 'best_effort');
  assert.equal(saved.device_change_observability.audio_context_sinkchange_supported, true);
  assert.equal(saved.device_change_observability.media_devices_devicechange_supported, true);
  assert.equal(saved.device_change_observability.system_output_route, 'not_independently_observed');
  const count = tap.records.length;
  tap.context.dispatchEvent(new Event('sinkchange'));
  tap.mediaDevices.dispatchEvent(new Event('devicechange'));
  await new Promise(setImmediate);
  assert.equal(tap.records.length, count);
});

test('unavailable device signals remain unknown without requesting device access', async () => {
  const tap = await capture({deviceSignals: false});
  const saved = await tap.save();
  assert.equal(tap.installation.device_change_observability.status, 'unknown');
  assert.equal(saved.device_change_observability.audio_context_sinkchange_supported, false);
  assert.equal(saved.device_change_observability.media_devices_devicechange_supported, false);
  assert.equal(saved.device_change_observability.system_output_route, 'not_independently_observed');
});
