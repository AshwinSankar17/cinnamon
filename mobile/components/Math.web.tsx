/**
 * PWA math: KaTeX → MathML, which browsers typeset natively (no KaTeX CSS or fonts to ship).
 * Bad TeX renders in red with its source instead of throwing.
 */
import { createElement, useMemo } from 'react';
import { ScrollView } from 'react-native';
import katex from 'katex';
import { colors } from '@/src/theme';

// MathML Core (Chrome) ignores mathvariant, so \mathcal{L} would show as a plain L. Map letters to the Unicode
// Mathematical Alphanumeric Symbols instead: [A, a, digit 0] code points, plus the letters that live elsewhere.
const VARIANTS: Record<string, [number, number, number | null, Record<string, string>]> = {
  bold: [0x1d400, 0x1d41a, 0x1d7ce, {}],
  italic: [0x1d434, 0x1d44e, null, { h: 'ℎ' }],
  'bold-italic': [0x1d468, 0x1d482, null, {}],
  script: [0x1d49c, 0x1d4b6, null, { B: 'ℬ', E: 'ℰ', F: 'ℱ', H: 'ℋ', I: 'ℐ', L: 'ℒ', M: 'ℳ', R: 'ℛ', e: 'ℯ', g: 'ℊ', o: 'ℴ' }],
  fraktur: [0x1d504, 0x1d51e, null, { C: 'ℭ', H: 'ℌ', I: 'ℑ', R: 'ℜ', Z: 'ℨ' }],
  'double-struck': [0x1d538, 0x1d552, 0x1d7d8, { C: 'ℂ', H: 'ℍ', N: 'ℕ', P: 'ℙ', Q: 'ℚ', R: 'ℝ', Z: 'ℤ' }],
  'sans-serif': [0x1d5a0, 0x1d5ba, 0x1d7e2, {}],
  monospace: [0x1d670, 0x1d68a, 0x1d7f6, {}],
};

function styled(text: string, variant: string): string | null {
  const v = VARIANTS[variant];
  if (!v) return null;
  const [upper, lower, digit, special] = v;
  return Array.from(text)
    .map((c) => {
      if (special[c]) return special[c];
      const n = c.charCodeAt(0);
      if (n >= 65 && n <= 90) return String.fromCodePoint(upper + n - 65);
      if (n >= 97 && n <= 122) return String.fromCodePoint(lower + n - 97);
      if (digit !== null && n >= 48 && n <= 57) return String.fromCodePoint(digit + n - 48);
      return c;
    })
    .join('');
}

function unicodeVariants(html: string): string {
  return html.replace(/<(mi|mn|mtext)([^>]*?) mathvariant="([a-z-]+)"([^>]*)>([^<]*)<\/\1>/g, (all, tag, a, variant, b, text) => {
    const out = styled(text, variant);
    // keep italic single-letter identifiers to the browser's default; normal is already upright
    return out === null ? all : `<${tag}${a}${b} mathvariant="normal">${out}</${tag}>`;
  });
}

function useMathML(tex: string, displayMode: boolean): string {
  return useMemo(
    () =>
      unicodeVariants(
        katex.renderToString(tex, { output: 'mathml', displayMode, throwOnError: false, errorColor: colors.failed, strict: 'ignore' }),
      ),
    [tex, displayMode],
  );
}

export function MathInline({ tex }: { tex: string }) {
  const html = useMathML(tex, false);
  return createElement('span', { dangerouslySetInnerHTML: { __html: html }, style: { fontSize: '1.05em' } });
}

export function MathBlock({ tex }: { tex: string }) {
  const html = useMathML(tex, true);
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ flexGrow: 1, justifyContent: 'center' }}>
      {createElement('div', { dangerouslySetInnerHTML: { __html: html }, style: { fontSize: '1.15em', padding: '4px 0' } })}
    </ScrollView>
  );
}
