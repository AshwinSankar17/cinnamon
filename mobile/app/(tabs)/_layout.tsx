import { Redirect, Tabs } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Icon, type IconName } from '@/components/Icon';
import { useConnection } from '@/src/connection';
import { colors, fonts } from '@/src/theme';

const TABS: { name: string; title: string; icon: IconName }[] = [
  { name: 'index', title: 'Inbox', icon: 'inbox' },
  { name: 'threads', title: 'Threads', icon: 'threads' },
  { name: 'runs', title: 'Runs', icon: 'runs' },
];

export default function TabLayout() {
  const { paired } = useConnection();
  const insets = useSafeAreaInsets();
  if (!paired) return <Redirect href="/pair" />;
  // The bar owns its full height: 56 for icon + label, plus the home-indicator area on phones that have one
  // (env(safe-area-inset-bottom) in the installed PWA; index.html sets viewport-fit=cover).
  const bottom = Math.max(insets.bottom, 6);

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        sceneStyle: { backgroundColor: colors.ground },
        tabBarActiveTintColor: colors.accent,
        tabBarInactiveTintColor: colors.text2,
        tabBarStyle: {
          backgroundColor: colors.surface,
          borderTopColor: colors.hairline,
          borderTopWidth: 1,
          elevation: 0,
          shadowOpacity: 0,
          height: 58 + bottom,
          paddingTop: 6,
          paddingBottom: bottom,
        },
        tabBarItemStyle: { height: 52 },
        tabBarLabelStyle: { fontFamily: fonts.sansSemi, fontSize: 12, lineHeight: 16 },
      }}
    >
      {TABS.map((t) => (
        <Tabs.Screen
          key={t.name}
          name={t.name}
          options={{
            title: t.title,
            tabBarAccessibilityLabel: t.title,
            tabBarIcon: ({ color }) => <Icon name={t.icon} color={color as string} size={20} />,
          }}
        />
      ))}
    </Tabs>
  );
}
