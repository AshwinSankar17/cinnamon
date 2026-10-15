/** Native fallback: show the TeX source in mono (math is typeset in the PWA; see Math.web.tsx). */
import { Text, View } from 'react-native';
import { colors, fonts } from '@/src/theme';

export function MathInline({ tex }: { tex: string }) {
  return <Text style={{ fontFamily: fonts.mono, fontSize: 13, color: colors.text }}>{tex}</Text>;
}

export function MathBlock({ tex }: { tex: string }) {
  return (
    <View style={{ paddingVertical: 4 }}>
      <Text selectable style={{ fontFamily: fonts.mono, fontSize: 13, color: colors.text, textAlign: 'center' }}>
        {tex}
      </Text>
    </View>
  );
}
