// Captures the meeting tab audio (interviewer) and the microphone (candidate) as 16-bit PCM chunks.

export type Channel = 0 | 1; // 0 = interviewer (tab), 1 = candidate (mic)

export interface CaptureHandle {
  sampleRate: number;
  hasMic: boolean;
  stop: () => void;
}

export class CaptureError extends Error {}

const TARGET_SAMPLE_RATE = 16000;

export async function startCapture(
  onChunk: (channel: Channel, pcm: ArrayBuffer) => void,
  onTabShareEnded: () => void,
): Promise<CaptureHandle> {
  if (!navigator.mediaDevices?.getDisplayMedia) {
    throw new CaptureError("This browser cannot capture tab audio. Use Google Chrome or Microsoft Edge.");
  }

  // 1. Meeting tab. Chrome requires video: true; the user must tick "Share tab audio".
  let display: MediaStream;
  try {
    display = await navigator.mediaDevices.getDisplayMedia({
      video: true,
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
    });
  } catch {
    throw new CaptureError("Tab sharing was cancelled.");
  }
  if (display.getAudioTracks().length === 0) {
    display.getTracks().forEach((t) => t.stop());
    throw new CaptureError(
      'No audio was shared. Choose the "Chrome Tab" with your meeting and turn on "Share tab audio".',
    );
  }

  // 2. Microphone (optional: the session still works for the interviewer without it).
  let mic: MediaStream | null = null;
  try {
    mic = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
  } catch {
    mic = null;
  }

  // 3. Audio graph: one worklet per channel, never connected to the speakers (no echo).
  const ctx = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE });
  await ctx.audioWorklet.addModule("/pcm-worklet.js");

  const nodes: AudioNode[] = [];
  const wire = (stream: MediaStream, channel: Channel) => {
    const source = ctx.createMediaStreamSource(new MediaStream(stream.getAudioTracks()));
    const worklet = new AudioWorkletNode(ctx, "pcm-capture");
    worklet.port.onmessage = (e: MessageEvent<ArrayBuffer>) => onChunk(channel, e.data);
    source.connect(worklet);
    nodes.push(source, worklet);
  };
  wire(display, 0);
  if (mic) wire(mic, 1);

  display.getAudioTracks()[0].addEventListener("ended", onTabShareEnded);
  display.getVideoTracks()[0]?.addEventListener("ended", onTabShareEnded);

  return {
    sampleRate: ctx.sampleRate,
    hasMic: mic !== null,
    stop: () => {
      nodes.forEach((n) => n.disconnect());
      display.getTracks().forEach((t) => t.stop());
      mic?.getTracks().forEach((t) => t.stop());
      void ctx.close();
    },
  };
}
