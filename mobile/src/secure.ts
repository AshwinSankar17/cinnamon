/** Small secret store. Native: expo-secure-store (Android Keystore / iOS Keychain). Web: see secure.web.ts. */
import * as SecureStore from 'expo-secure-store';

const OPTS: SecureStore.SecureStoreOptions = { keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY };

export const getSecret = (k: string) => SecureStore.getItemAsync(k, OPTS);
export const setSecret = (k: string, v: string) => SecureStore.setItemAsync(k, v, OPTS);
export const deleteSecret = (k: string) => SecureStore.deleteItemAsync(k, OPTS);
