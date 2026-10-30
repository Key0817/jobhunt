import { createClient } from '@supabase/supabase-js';

export const API_URL = (import.meta.env.VITE_API_URL || (import.meta.env.DEV ? 'http://localhost:8000' : window.location.origin)).replace(/\/$/, '');
export const API_ORIGIN = new URL(API_URL, window.location.origin).origin;
const supabaseUrl = import.meta.env.VITE_SUPABASE_URL;
const supabaseKey = import.meta.env.VITE_SUPABASE_ANON_KEY;
export const supabase = supabaseUrl && supabaseKey ? createClient(supabaseUrl, supabaseKey) : null;
export const deploymentProblem = import.meta.env.PROD && (!supabase || !import.meta.env.VITE_API_URL);

let refreshing: Promise<string | null> | null = null;
let signingOut: Promise<void> | null = null;
export function logoutSession() {
  if (!supabase) return Promise.resolve();
  if (!signingOut) signingOut = (async () => {
    const { error } = await supabase.auth.signOut({ scope: 'local' });
    if (error) throw new Error('No se pudo cerrar la sesión. Inténtalo de nuevo.');
  })().finally(() => { signingOut = null; });
  return signingOut;
}
export function refreshAccessToken(failedToken?: string) {
  if (!supabase) return Promise.resolve(null);
  if (!refreshing) refreshing = (async () => {
    const { data: current } = await supabase.auth.getSession();
    if (!current.session) return null;
    if (failedToken && current.session.access_token !== failedToken) return current.session.access_token;
    const { data, error } = await supabase.auth.refreshSession();
    return error ? null : data.session?.access_token || null;
  })().finally(() => { refreshing = null; });
  return refreshing;
}

export async function apiFetch(url: string, options: RequestInit = {}) {
  if (new URL(url, API_URL).origin !== API_ORIGIN) throw new Error('Destino de API no permitido.');
  const headers = new Headers(options.headers);
  let token: string | undefined;
  if (supabase) {
    const { data, error } = await supabase.auth.getSession();
    if (error) throw new Error('No se pudo recuperar la sesión.');
    token = data.session?.access_token;
    if (!token) throw new Error('La sesión ha caducado. Inicia sesión de nuevo.');
    headers.set('Authorization', `Bearer ${token}`);
  }
  let response = await fetch(url, { ...options, headers });
  if (response.status === 401 && supabase && !options.signal?.aborted) {
    const renewed = await refreshAccessToken(token);
    if (renewed && !options.signal?.aborted) {
      headers.set('Authorization', `Bearer ${renewed}`);
      response = await fetch(url, { ...options, headers });
    }
    if (response.status === 401) window.dispatchEvent(new Event('jobhunt-session-expired'));
  }
  return response;
}
