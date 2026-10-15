/**
 * One shared <audio> element for the PWA. play(url, keepPosition) switches source while keeping the playback time,
 * which is what A/B comparison needs: hear the same moment in sample A, then in sample B.
 */
export const AUDIO_SUPPORTED = true;

let el: HTMLAudioElement | null = null;
let src: string | null = null;
const subs = new Set<() => void>();
const emit = () => subs.forEach((f) => f());

function audio(): HTMLAudioElement {
  if (!el) {
    el = new Audio();
    el.preload = 'auto';
    for (const ev of ['play', 'pause', 'ended', 'timeupdate']) el.addEventListener(ev, emit);
  }
  return el;
}

export const player = {
  async play(url: string, keepPosition = false): Promise<void> {
    const a = audio();
    if (src === url) {
      if (a.paused) await a.play();
      else a.pause();
      return;
    }
    const t = keepPosition && !a.paused ? a.currentTime : 0;
    src = url;
    a.src = url;
    await new Promise<void>((resolve) => {
      const done = () => {
        a.removeEventListener('loadedmetadata', done);
        resolve();
      };
      a.addEventListener('loadedmetadata', done);
    });
    a.currentTime = Math.min(t, Math.max(0, (a.duration || t) - 0.05));
    await a.play();
    emit();
  },
  pause(): void {
    el?.pause();
  },
  get current(): string | null {
    return el && !el.paused ? src : null;
  },
  get progress(): number {
    return el && el.duration ? el.currentTime / el.duration : 0;
  },
  subscribe(fn: () => void): () => void {
    subs.add(fn);
    return () => subs.delete(fn);
  },
};
