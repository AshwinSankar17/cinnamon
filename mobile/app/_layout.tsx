import { useEffect, useState } from 'react';
import { AppState, View, useWindowDimensions } from 'react-native';
import { useFonts } from 'expo-font';
import { Stack } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { Newsreader_500Medium } from '@expo-google-fonts/newsreader/500Medium';
import { Newsreader_500Medium_Italic } from '@expo-google-fonts/newsreader/500Medium_Italic';
import { Newsreader_600SemiBold } from '@expo-google-fonts/newsreader/600SemiBold';
import { Manrope_400Regular } from '@expo-google-fonts/manrope/400Regular';
import { Manrope_500Medium } from '@expo-google-fonts/manrope/500Medium';
import { Manrope_600SemiBold } from '@expo-google-fonts/manrope/600SemiBold';
import { Manrope_700Bold } from '@expo-google-fonts/manrope/700Bold';
import { IBMPlexMono_400Regular } from '@expo-google-fonts/ibm-plex-mono/400Regular';
import { IBMPlexMono_500Medium } from '@expo-google-fonts/ibm-plex-mono/500Medium';

import { health, loadPairing, refreshEndpoints } from '@/src/api';
import { startNetworkTracking, useConnection } from '@/src/connection';
import { registerForPush, useNotificationRouting } from '@/src/push';
import { db, setMeta } from '@/src/store';
import { colors } from '@/src/theme';
import { brainSocket } from '@/src/ws';
import { WorldProvider } from '@/src/world';

export { ErrorBoundary } from 'expo-router';

export const unstable_settings = { initialRouteName: 'index' };

SplashScreen.preventAutoHideAsync();

export default function RootLayout() {
  const [fontsLoaded, fontError] = useFonts({
    Newsreader_500Medium,
    Newsreader_500Medium_Italic,
    Newsreader_600SemiBold,
    Manrope_400Regular,
    Manrope_500Medium,
    Manrope_600SemiBold,
    Manrope_700Bold,
    IBMPlexMono_400Regular,
    IBMPlexMono_500Medium,
  });
  const [booted, setBooted] = useState(false);
  const wide = useWindowDimensions().width > 800;
  const { paired } = useConnection();

  useEffect(() => {
    if (fontError) throw fontError;
  }, [fontError]);

  // Boot: local DB, pairing, network tracking.
  useEffect(() => {
    (async () => {
      startNetworkTracking();
      await db();
      await loadPairing();
      setBooted(true);
    })().catch(() => setBooted(true));
  }, []);

  // Once paired: live socket while foregrounded, health/paused probe, quiet push token refresh.
  useEffect(() => {
    if (!booted || !paired) return;
    brainSocket.start();
    health().catch(() => undefined);
    refreshEndpoints().catch(() => undefined); // failover relay list
    registerForPush(false).catch(() => undefined);
    return () => brainSocket.stop();
  }, [booted, paired]);

  // Remember when the user left, for the Inbox "since you left" line.
  useEffect(() => {
    const sub = AppState.addEventListener('change', (s) => {
      if (s === 'background') setMeta('left_at', String(Date.now())).catch(() => undefined);
      if (s === 'active' && paired) health().catch(() => undefined);
    });
    return () => sub.remove();
  }, [paired]);

  useNotificationRouting(booted && paired);

  const ready = fontsLoaded && booted;
  useEffect(() => {
    if (ready) SplashScreen.hideAsync();
  }, [ready]);
  if (!ready) return null;

  return (
    <SafeAreaProvider>
      <WorldProvider>
      <StatusBar style="dark" />
      {/* Phone-first: on a wide screen the app sits in a centred column on the strip board. */}
      <View style={{ flex: 1, backgroundColor: colors.ground }}>
      <View style={{ flex: 1, width: '100%', maxWidth: 760, alignSelf: 'center', borderLeftWidth: wide ? 1 : 0, borderRightWidth: wide ? 1 : 0, borderColor: colors.hairline }}>
      <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.ground } }}>
        <Stack.Screen name="index" />
        <Stack.Screen name="inbox" />
        <Stack.Screen name="threads" />
        <Stack.Screen name="runs" />
        <Stack.Screen name="import" />
        <Stack.Screen name="pair" />
        <Stack.Screen name="thread/[id]" options={{ contentStyle: { backgroundColor: colors.surface } }} />
        <Stack.Screen name="approval/[id]" />
        <Stack.Screen name="new" options={{ presentation: 'modal' }} />
        <Stack.Screen name="listen/[id]" />
        <Stack.Screen name="files/[id]" />
      </Stack>
      </View>
      </View>
      </WorldProvider>
    </SafeAreaProvider>
  );
}
