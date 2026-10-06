/** Native: not wired yet (needs expo-audio + a native rebuild). Listening tests play in the PWA. */
export const AUDIO_SUPPORTED = false;

export const player = {
  async play(_url: string, _keepPosition = false): Promise<void> {
    throw new Error('Audio playback is available in the web app for now.');
  },
  pause(): void {},
  get current(): string | null {
    return null;
  },
  get progress(): number {
    return 0;
  },
  subscribe(_fn: () => void): () => void {
    return () => undefined;
  },
};
