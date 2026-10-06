/**
 * PWA secret store: localStorage, scoped to this origin. Browsers have no Keystore equivalent, so the device
 * key here is protected by the browser profile and same-origin policy only (Brain serves this app; there are no
 * third-party scripts). Revoke a lost phone with `brain devices revoke`.
 */
export async function getSecret(k: string): Promise<string | null> {
  return window.localStorage.getItem(k);
}
export async function setSecret(k: string, v: string): Promise<void> {
  window.localStorage.setItem(k, v);
}
export async function deleteSecret(k: string): Promise<void> {
  window.localStorage.removeItem(k);
}
