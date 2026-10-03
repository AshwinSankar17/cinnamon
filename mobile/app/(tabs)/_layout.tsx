import { Redirect, Tabs } from 'expo-router';
import { Icon, type IconName } from '@/components/Icon';
import { useConnection } from '@/src/connection';
import { colors, fonts } from '@/src/theme';

const TABS: { name: string; title: string; icon: IconName }[] = [
  { name: 'index', title: 'Inbox', icon: 'inbox' },
  { name: 'threads', title: 'Threads', icon: 'threads' },
  { name: 'runs', title: 'Runs', icon: 'runs' },
  { name: 'repos', title: 'Repos', icon: 'repos' },
];

export default function TabLayout() {
  const { paired } = useConnection();
  if (!paired) return <Redirect href="/pair" />;

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
        },
        tabBarItemStyle: { minHeight: 56 },
        tabBarLabelStyle: { fontFamily: fonts.sansSemi, fontSize: 12 },
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
