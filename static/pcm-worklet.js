// AudioWorklet that captures Float32 mono samples from the mic
// at the AudioContext's native rate (usually 48 kHz) and forwards them
// to the main thread as small Float32Array chunks. The main thread
// downsamples to 16 kHz before sending to the server.

class PcmCaptureProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) return true;
    const channel = input[0];
    if (!channel || channel.length === 0) return true;
    // Copy because the underlying buffer is reused.
    const copy = new Float32Array(channel.length);
    copy.set(channel);
    this.port.postMessage(copy, [copy.buffer]);
    return true;
  }
}

registerProcessor("pcm-capture", PcmCaptureProcessor);
