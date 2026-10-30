import { createClient } from '@supabase/supabase-js';

export const API_URL = (import.meta.env.VITE_API_URL || (import.meta.env.DEV ? 'http://localhost:8000' : window.location.origin)).replace(/\/$/, '');
export const API_ORIGIN = new URL(API_URL, window.location.origin).origin;
const supabaseUrl = import.meta.env.VITE_SUPABASE_URL;
const supabaseKey = import.meta.env.VITE_SUPABASE_ANON_KEY;
export const supabase = supabaseUrl && supabaseKey ? createClient(supabaseUrl, supabaseKey) : null;
export const deploymentProblem = import.meta.env.PROD && (!supabase || !import.meta.env.VITE_API_URL);

export async function apiFetch(url: string, options: RequestInit = {}) {
  if (new URL(url, API_URL).origin !== API_ORIGIN) throw new Error('Destino de API no permitido.');
  const headers = new Headers(options.headers);
  if (supabase) {
    const { data, error } = await supabase.auth.getSession();
    if (error) throw new Error('No se pudo recuperar la sesión.');
    if (data.session) headers.set('Authorization', `Bearer ${data.session.access_token}`);
  }
  return fetch(url, { ...options, headers });
}
