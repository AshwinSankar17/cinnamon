/**
 * STYLE-V2 "editorial light" tokens. Every colour used in the app comes from here.
 * Source: canvas/STYLE-V2.md + project/v2/E-*.dc.html exemplars.
 */
import type { TextStyle } from 'react-native';

export const colors = {
  ground: '#faf8f4',
  surface: '#ffffff',
  surface2: '#f4f4f5',
  surfaceMuted: '#fafafa', // card headers / inline rows in E-Chat
  hairline: '#e7e5e4',
  border: '#d6d3d1',
  scrim: '#a8a29e', // sheet scrim (E-ThreadActions)

  text: '#111111',
  text2: '#57534e',
  text3: '#78716c', // only on white surfaces, >= 12 px

  accent: '#c2410c',
  onAccent: '#ffffff',
  accentTint: '#fff1e8',

  black: '#111111',
  onBlack: '#ffffff',

  running: '#15803d',
  runningTint: '#ecfdf3',
  waiting: '#1d4ed8',
  waitingTint: '#eff6ff',
  needsYou: '#b45309',
  needsYouTint: '#fff7ed',
  failed: '#b91c1c',
  failedTint: '#fef2f2',
  failedBorder: '#fecaca',
  idle: '#78716c',

  diffAddBg: '#ecfdf3',
  diffAddFg: '#14532d',
  diffAddGutter: '#dcfce7',
  diffDelBg: '#fef2f2',
  diffDelFg: '#7f1d1d',
  diffDelGutter: '#fee2e2',
  diffHunkBg: '#f4f4f5',

  terminalBg: '#1c1917',
  terminalFg: '#e7e5e4',
} as const;

export type ColorToken = keyof typeof colors;

/** Font family names as registered in app/_layout.tsx via expo-font. */
export const fonts = {
  serif: 'Newsreader_500Medium',
  serifSemi: 'Newsreader_600SemiBold',
  serifItalic: 'Newsreader_500Medium_Italic',
  sans: 'Manrope_400Regular',
  sansMedium: 'Manrope_500Medium',
  sansSemi: 'Manrope_600SemiBold',
  sansBold: 'Manrope_700Bold',
  mono: 'IBMPlexMono_400Regular',
  monoMedium: 'IBMPlexMono_500Medium',
} as const;

export const radius = { card: 14, button: 10, input: 12, pill: 999, bubble: 18, composer: 22 } as const;
export const space = { gutter: 16, touch: 44, chip: 36 } as const;

/** Type ramp (STYLE-V2 "Type"). Fonts carry the weight, so no fontWeight here. */
export const type = {
  screenTitle: { fontFamily: fonts.serif, fontSize: 28, lineHeight: 32, letterSpacing: -0.28, color: colors.text },
  subTitle: { fontFamily: fonts.serif, fontSize: 19, lineHeight: 24, color: colors.text },
  sheetTitle: { fontFamily: fonts.serif, fontSize: 21, lineHeight: 26, color: colors.text },
  cardTitle: { fontFamily: fonts.serif, fontSize: 18, lineHeight: 24, color: colors.text },
  rowTitle: { fontFamily: fonts.serif, fontSize: 17, lineHeight: 22, color: colors.text },
  section: { fontFamily: fonts.serifItalic, fontSize: 15, lineHeight: 20, color: colors.text2 },
  prose: { fontFamily: fonts.serif, fontSize: 19, lineHeight: 26, color: colors.text },
  proseSm: { fontFamily: fonts.serif, fontSize: 18, lineHeight: 26, color: colors.text },
  body: { fontFamily: fonts.sans, fontSize: 15, lineHeight: 22, color: colors.text },
  bodyChat: { fontFamily: fonts.sans, fontSize: 15, lineHeight: 23, color: colors.text },
  small: { fontFamily: fonts.sans, fontSize: 13, lineHeight: 18, color: colors.text2 },
  button: { fontFamily: fonts.sansSemi, fontSize: 15, lineHeight: 20 },
  label: { fontFamily: fonts.sansSemi, fontSize: 13, lineHeight: 18, color: colors.text2 },
  mono: { fontFamily: fonts.mono, fontSize: 12, lineHeight: 16, color: colors.text2 },
  monoBody: { fontFamily: fonts.mono, fontSize: 14, lineHeight: 20, color: colors.text },
  monoPill: { fontFamily: fonts.mono, fontSize: 11, lineHeight: 14, letterSpacing: 0.22 },
  status: { fontFamily: fonts.sansSemi, fontSize: 12, lineHeight: 16, letterSpacing: 0.48 },
} satisfies Record<string, TextStyle>;

export type ThreadState = 'idle' | 'running' | 'waiting' | 'needs_you' | 'paused';

export const stateColor: Record<ThreadState, string> = {
  idle: colors.idle,
  running: colors.running,
  waiting: colors.waiting,
  needs_you: colors.needsYou,
  paused: colors.needsYou,
};

export const stateLabel: Record<ThreadState, string> = {
  idle: 'Idle',
  running: 'Running',
  waiting: 'Waiting',
  needs_you: 'Needs you',
  paused: 'Paused',
};

export const providerShort: Record<string, string> = { claude: 'CC', codex: 'CX', agy: 'AG', fake: 'FK' };
export const providerName: Record<string, string> = {
  claude: 'Claude Code',
  codex: 'Codex',
  agy: 'Antigravity',
  fake: 'Fake',
};
