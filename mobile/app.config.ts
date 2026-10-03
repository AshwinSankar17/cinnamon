/**
 * Extends app.json. Adds the Firebase config for FCM only when it is available, so `expo start`
 * and prebuild work before Firebase is set up:
 *   - locally: put google-services.json next to this file (gitignored), or
 *   - EAS: upload it as a file environment variable named GOOGLE_SERVICES_JSON.
 */
import { existsSync } from "fs";
import type { ConfigContext, ExpoConfig } from 'expo/config';

export default ({ config }: ConfigContext): ExpoConfig => {
  const googleServicesFile =
    process.env.GOOGLE_SERVICES_JSON ?? (existsSync('./google-services.json') ? './google-services.json' : undefined);
  return {
    ...(config as ExpoConfig),
    android: {
      ...config.android,
      ...(googleServicesFile ? { googleServicesFile } : {}),
    },
  };
};
