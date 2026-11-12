// AudioWorklet: converts Float32 audio to 16-bit PCM and posts ~100 ms chunks to the main thread.
class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.chunkSize = Math.round(sampleRate / 10); // 100 ms at the context's sample rate
    this.buffer = new Int16Array(this.chunkSize);
    this.offset = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) return true;
    const left = input[0];
    const right = input[1];
    for (let i = 0; i < left.length; i++) {
      // Downmix stereo to mono.
      let s = right ? (left[i] + right[i]) / 2 : left[i];
      s = Math.max(-1, Math.min(1, s));
      this.buffer[this.offset++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      if (this.offset === this.chunkSize) {
        this.port.postMessage(this.buffer.buffer, [this.buffer.buffer]);
        this.buffer = new Int16Array(this.chunkSize);
        this.offset = 0;
      }
    }
    return true;
  }
}

registerProcessor("pcm-capture", PcmCaptureProcessor);
