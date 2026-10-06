/**
 * Chat Markdown: `marked` parses (GFM), this file renders with the app's own type ramp, on web and native.
 * Headings, emphasis, strikethrough, inline/fenced code, nested and task lists, quotes, rules, tables, links.
 */
import { Fragment, useMemo, type ReactNode } from 'react';
import { Linking, ScrollView, Text, View, type TextStyle } from 'react-native';
import { Marked, type Token, type TokenizerAndRendererExtension, type Tokens } from 'marked';
import { colors, fonts, type } from '@/src/theme';
import { MathBlock, MathInline } from './Math';

type MathToken = { type: 'math' | 'mathBlock'; raw: string; tex: string };

/** $$…$$ and \[…\] display math; $…$ and \(…\) inline. "$5 and $10" stays text: an inline $ must not touch
 *  whitespace on its inner side, and the closing $ must not be followed by a digit (pandoc's rule). */
const mathBlock: TokenizerAndRendererExtension = {
  name: 'mathBlock',
  level: 'block',
  start: (src) => src.match(/\$\$|\\\[/)?.index,
  tokenizer(src) {
    const m = /^\$\$([\s\S]+?)\$\$[^\S\n]*(?:\n|$)/.exec(src) ?? /^\\\[([\s\S]+?)\\\][^\S\n]*(?:\n|$)/.exec(src);
    return m ? { type: 'mathBlock', raw: m[0], tex: m[1].trim() } : undefined;
  },
};
const mathInline: TokenizerAndRendererExtension = {
  name: 'math',
  level: 'inline',
  start: (src) => src.match(/\$|\\\(/)?.index,
  tokenizer(src) {
    const d = /^\$\$([^$]+?)\$\$/.exec(src); // $$…$$ used inline in a sentence
    if (d) return { type: 'mathBlock', raw: d[0], tex: d[1].trim() };
    // no backticks inside: "$10 per run, and `$HOME`" is prose and a code span, not math
    const m = /^\$(?!\s)((?:\\.|[^$\\\n`])+?)(?<!\s)\$(?!\d)/.exec(src) ?? /^\\\(([^`]+?)\\\)/.exec(src);
    return m ? { type: 'math', raw: m[0], tex: m[1] } : undefined;
  },
};
const md = new Marked({ gfm: true, extensions: [mathBlock, mathInline] });

const BASE: TextStyle = type.bodyChat;
const CODE_BG = 'rgba(17, 17, 17, 0.07)'; // translucent: visible on white prose and on the grey user bubble
const HEADING: Record<number, TextStyle> = {
  1: { fontFamily: fonts.serifSemi, fontSize: 22, lineHeight: 28 },
  2: { fontFamily: fonts.serifSemi, fontSize: 19, lineHeight: 25 },
  3: { fontFamily: fonts.sansBold, fontSize: 16, lineHeight: 22 },
};

function openLink(href: string) {
  if (/^(https?:|mailto:)/i.test(href)) Linking.openURL(href).catch(() => undefined);
}

function inline(tokens: Token[] | undefined, key = 'i'): ReactNode[] {
  return (tokens ?? []).map((t, i) => {
    const k = `${key}.${i}`;
    switch (t.type) {
      case 'strong':
        return <Text key={k} style={{ fontFamily: fonts.sansBold }}>{inline((t as Tokens.Strong).tokens, k)}</Text>;
      case 'em':
        return <Text key={k} style={{ fontStyle: 'italic' }}>{inline((t as Tokens.Em).tokens, k)}</Text>;
      case 'del':
        return <Text key={k} style={{ textDecorationLine: 'line-through' }}>{inline((t as Tokens.Del).tokens, k)}</Text>;
      case 'codespan':
        return (
          <Text key={k} style={{ fontFamily: fonts.mono, fontSize: 13, backgroundColor: CODE_BG }}>
            {decode((t as Tokens.Codespan).text)}
          </Text>
        );
      case 'link': {
        const l = t as Tokens.Link;
        return (
          <Text key={k} accessibilityRole="link" onPress={() => openLink(l.href)} style={{ color: colors.accent, textDecorationLine: 'underline' }}>
            {inline(l.tokens, k)}
          </Text>
        );
      }
      case 'image': {
        const im = t as Tokens.Image;
        return (
          <Text key={k} accessibilityRole="link" onPress={() => openLink(im.href)} style={{ color: colors.accent }}>
            [image: {im.text || im.href}]
          </Text>
        );
      }
      case 'br':
        return <Text key={k}>{'\n'}</Text>;
      case 'text': {
        const tt = t as Tokens.Text;
        return tt.tokens ? <Fragment key={k}>{inline(tt.tokens, k)}</Fragment> : <Text key={k}>{decode(tt.text)}</Text>;
      }
      case 'math':
        return <MathInline key={k} tex={(t as unknown as MathToken).tex} />;
      case 'mathBlock': // display math written inside a sentence: still typeset, just not on its own line
        return <MathInline key={k} tex={(t as unknown as MathToken).tex} />;
      case 'checkbox': // task items show ☑/☐ in the list gutter
        return null;
      case 'escape':
      case 'html':
        return <Text key={k}>{decode((t as Tokens.Escape).text)}</Text>;
      default:
        return <Text key={k}>{decode((t as { raw?: string }).raw ?? '')}</Text>;
    }
  });
}

/** marked HTML-escapes text tokens; we render text, not HTML. */
function decode(s: string): string {
  return s.replace(/&(amp|lt|gt|quot|#39);/g, (_, e) => ({ amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'" })[e as string]!);
}

function ListView({ list, depth }: { list: Tokens.List; depth: number }) {
  const start = typeof list.start === 'number' ? list.start : 1;
  return (
    <View style={{ gap: 4 }}>
      {list.items.map((item, i) => (
        <View key={i} style={{ flexDirection: 'row', gap: 8, paddingLeft: depth ? 4 : 0 }}>
          <Text style={[BASE, { minWidth: list.ordered ? 20 : 12, color: colors.text2, textAlign: list.ordered ? 'right' : 'left' }]}>
            {item.task ? (item.checked ? '☑' : '☐') : list.ordered ? `${start + i}.` : depth % 2 ? '◦' : '•'}
          </Text>
          <View style={{ flex: 1, minWidth: 0, gap: 4 }}>
            {blocks(item.tokens.filter((x) => x.type !== 'checkbox'), `l${depth}.${i}`, depth + 1)}
          </View>
        </View>
      ))}
    </View>
  );
}

function TableView({ t }: { t: Tokens.Table }) {
  const cell = (c: Tokens.TableCell, head: boolean, j: number, k: string) => (
    <View key={k} style={{ minWidth: 90, maxWidth: 260, paddingVertical: 6, paddingHorizontal: 10, borderLeftWidth: j ? 1 : 0, borderLeftColor: colors.hairline }}>
      <Text style={[BASE, { fontSize: 14, lineHeight: 20, textAlign: t.align[j] ?? 'left' }, head && { fontFamily: fonts.sansSemi }]}>
        {inline(c.tokens, k)}
      </Text>
    </View>
  );
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false}>
      <View style={{ borderWidth: 1, borderColor: colors.hairline, borderRadius: 10, overflow: 'hidden' }}>
        <View style={{ flexDirection: 'row', backgroundColor: colors.surface2 }}>{t.header.map((c, j) => cell(c, true, j, `h${j}`))}</View>
        {t.rows.map((r, i) => (
          <View key={i} style={{ flexDirection: 'row', borderTopWidth: 1, borderTopColor: colors.hairline }}>
            {r.map((c, j) => cell(c, false, j, `r${i}.${j}`))}
          </View>
        ))}
      </View>
    </ScrollView>
  );
}

function blocks(tokens: Token[], key = 'b', depth = 0): ReactNode[] {
  return tokens.map((t, i) => {
    const k = `${key}.${i}`;
    switch (t.type) {
      case 'space':
        return null;
      case 'heading': {
        const h = t as Tokens.Heading;
        return (
          <Text key={k} style={[BASE, HEADING[h.depth] ?? { fontFamily: fonts.sansBold }]} accessibilityRole="header" selectable>
            {inline(h.tokens, k)}
          </Text>
        );
      }
      case 'paragraph':
        return (
          <Text key={k} style={BASE} selectable>
            {inline((t as Tokens.Paragraph).tokens, k)}
          </Text>
        );
      case 'text': // loose text inside list items
        return (
          <Text key={k} style={BASE} selectable>
            {inline((t as Tokens.Text).tokens ?? [t], k)}
          </Text>
        );
      case 'list':
        return <ListView key={k} list={t as Tokens.List} depth={depth} />;
      case 'code': {
        const c = t as Tokens.Code;
        return (
          <View key={k} style={{ backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline, borderRadius: 10, overflow: 'hidden' }}>
            {c.lang ? <Text style={{ fontFamily: fonts.mono, fontSize: 11, color: colors.text3, paddingHorizontal: 12, paddingTop: 8 }}>{c.lang}</Text> : null}
            <ScrollView horizontal showsHorizontalScrollIndicator={false}>
              <Text selectable style={{ fontFamily: fonts.mono, fontSize: 13, lineHeight: 19, color: colors.text, padding: 12 }}>
                {c.text}
              </Text>
            </ScrollView>
          </View>
        );
      }
      case 'blockquote':
        return (
          <View key={k} style={{ borderLeftWidth: 3, borderLeftColor: colors.border, paddingLeft: 10, gap: 6 }}>
            {blocks((t as Tokens.Blockquote).tokens, k, depth)}
          </View>
        );
      case 'hr':
        return <View key={k} style={{ height: 1, backgroundColor: colors.hairline, marginVertical: 4 }} />;
      case 'mathBlock':
        return <MathBlock key={k} tex={(t as unknown as MathToken).tex} />;
      case 'table':
        return <TableView key={k} t={t as Tokens.Table} />;
      default: // html and anything unknown: show the source rather than drop it
        return (
          <Text key={k} style={BASE} selectable>
            {(t as { raw?: string }).raw ?? ''}
          </Text>
        );
    }
  });
}

export function Markdown({ text }: { text: string }) {
  const tokens = useMemo(() => {
    try {
      return md.lexer(text);
    } catch {
      return null;
    }
  }, [text]);
  if (!tokens) return <Text style={BASE} selectable>{text}</Text>;
  return <View style={{ gap: 8 }}>{blocks(tokens)}</View>;
}

/** One-line preview text for lists and notifications: Markdown symbols removed. */
export function plainText(md: string): string {
  return md
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/^\s{0,3}(#{1,6}\s+|>\s?|[-*+]\s+(\[[ xX]\]\s+)?|\d+[.)]\s+)/gm, '')
    .replace(/(\*\*|__|~~)(.+?)\1/g, '$2')
    .replace(/(^|[^\w*])[*_]([^*_\n]+)[*_](?=[^\w*]|$)/g, '$1$2')
    .replace(/^\|?[\s:|-]+\|?$/gm, '')
    .replace(/\s+/g, ' ')
    .trim();
}
