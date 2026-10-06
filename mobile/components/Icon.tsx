/** Line icons copied from the v2 mockups (24×24 viewBox, 1.75 stroke). */
import Svg, { Circle, Path, Rect } from 'react-native-svg';
import { colors } from '@/src/theme';

export type IconName =
  | 'inbox'
  | 'threads'
  | 'runs'
  | 'repos'
  | 'pause'
  | 'play'
  | 'plus'
  | 'search'
  | 'back'
  | 'chevronRight'
  | 'chevronDown'
  | 'chevronUp'
  | 'dots'
  | 'pin'
  | 'check'
  | 'cross'
  | 'stop'
  | 'send'
  | 'fork'
  | 'info'
  | 'mute'
  | 'archive'
  | 'scan'
  | 'refresh'
  | 'attach';

type Props = { name: IconName; size?: number; color?: string; strokeWidth?: number };

export function Icon({ name, size = 20, color = colors.text, strokeWidth = 1.75 }: Props) {
  const stroke = { stroke: color, strokeWidth, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, fill: 'none' };
  const body = (() => {
    switch (name) {
      case 'inbox':
        return (
          <>
            <Path d="M5 4h14l2 8v8H3v-8z" {...stroke} />
            <Path d="M3 12h5l2 3h4l2-3h5" {...stroke} />
          </>
        );
      case 'threads':
        return <Path d="M4 5h16v11H9l-5 4z" {...stroke} />;
      case 'runs':
        return (
          <>
            <Circle cx="12" cy="12" r="8" {...stroke} />
            <Path d="M12 8v4l3 2" {...stroke} />
          </>
        );
      case 'repos':
        return (
          <>
            <Circle cx="6" cy="5" r="2" {...stroke} />
            <Circle cx="6" cy="19" r="2" {...stroke} />
            <Circle cx="18" cy="9" r="2" {...stroke} />
            <Path d="M6 7v10M18 11c0 4-12 2-12 6" {...stroke} />
          </>
        );
      case 'pause':
        return <Path d="M8 5v14M16 5v14" {...stroke} />;
      case 'play':
        return <Path d="M8 5v14l11-7z" {...stroke} />;
      case 'plus':
        return <Path d="M12 5v14M5 12h14" {...stroke} />;
      case 'search':
        return (
          <>
            <Circle cx="11" cy="11" r="6" {...stroke} />
            <Path d="M20 20l-4.5-4.5" {...stroke} />
          </>
        );
      case 'back':
        return <Path d="M15 6l-6 6 6 6" {...stroke} />;
      case 'chevronRight':
        return <Path d="M9 6l6 6-6 6" {...stroke} />;
      case 'chevronDown':
        return <Path d="M6 9l6 6 6-6" {...stroke} />;
      case 'chevronUp':
        return <Path d="M6 15l6-6 6 6" {...stroke} />;
      case 'dots':
        return (
          <>
            <Circle cx="5" cy="12" r="2" fill={color} />
            <Circle cx="12" cy="12" r="2" fill={color} />
            <Circle cx="19" cy="12" r="2" fill={color} />
          </>
        );
      case 'pin':
        return (
          <>
            <Path d="M9 3h6l-1 6 3 3v2H7v-2l3-3z" {...stroke} />
            <Path d="M12 14v7" {...stroke} />
          </>
        );
      case 'check':
        return <Path d="M5 12l5 5 9-9" {...stroke} />;
      case 'cross':
        return <Path d="M6 6l12 12M18 6L6 18" {...stroke} />;
      case 'stop':
        return <Rect x="5" y="5" width="14" height="14" rx="2" fill={color} />;
      case 'send':
        return <Path d="M12 19V5M6 11l6-6 6 6" {...stroke} />;
      case 'attach':
        return <Path d="M20 11.5l-7.8 7.8a5 5 0 0 1-7.1-7.1l8.5-8.5a3.3 3.3 0 0 1 4.7 4.7l-8.5 8.5a1.7 1.7 0 0 1-2.4-2.4l7.8-7.8" {...stroke} />;
      case 'fork':
        return (
          <>
            <Circle cx="6" cy="5" r="2" {...stroke} />
            <Circle cx="6" cy="19" r="2" {...stroke} />
            <Circle cx="18" cy="7" r="2" {...stroke} />
            <Path d="M6 7v10M18 9c0 5-12 3-12 8" {...stroke} />
          </>
        );
      case 'info':
        return (
          <>
            <Circle cx="12" cy="12" r="9" {...stroke} />
            <Path d="M12 11v5M12 8h.01" {...stroke} />
          </>
        );
      case 'mute':
        return (
          <>
            <Path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" {...stroke} />
            <Path d="M10 21h4M3 3l18 18" {...stroke} />
          </>
        );
      case 'archive':
        return (
          <>
            <Rect x="3" y="4" width="18" height="4" rx="1" {...stroke} />
            <Path d="M5 8v11h14V8M10 12h4" {...stroke} />
          </>
        );
      case 'scan':
        return <Path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3M7 12h10" {...stroke} />;
      case 'refresh':
        return (
          <>
            <Path d="M20 12a8 8 0 1 1-2.3-5.6" {...stroke} />
            <Path d="M20 4v5h-5" {...stroke} />
          </>
        );
    }
  })();
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24">
      {body}
    </Svg>
  );
}
