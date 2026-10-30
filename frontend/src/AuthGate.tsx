import { clearViewCache } from './viewCache';
import { useEffect, useRef, useState } from 'react';
import type { ReactNode, FormEvent } from 'react';
import type { Session } from '@supabase/supabase-js';
import { API_URL, supabase, deploymentProblem, logoutSession, refreshAccessToken } from './api';

export default function AuthGate({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(!supabase);
  const [authorizedUser, setAuthorizedUser] = useState('');
  const activeUser = useRef<string | null>(null);
  const verifiedToken = useRef('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!supabase) return;
    const { data } = supabase.auth.onAuthStateChange((_event, current) => {
      const id = current?.user.id || null;
      if (id !== activeUser.current || !current) {
        clearViewCache(); setAuthorizedUser(''); verifiedToken.current = '';
      }
      activeUser.current = id;
      setSession(current); setReady(true);
    });
    return () => data.subscription.unsubscribe();
  }, []);
  const token = session?.access_token;
  const userId = session?.user.id;
  useEffect(() => {
    if (!token || !userId || verifiedToken.current === token) return;
    const controller = new AbortController();
    async function verify() {
      try {
        let response = await fetch(`${API_URL}/api/auth/me`, { signal: controller.signal, headers: { Authorization: `Bearer ${token}` } });
        if (controller.signal.aborted) return;
        if (response.status === 401) {
          const renewed = await refreshAccessToken(token);
          if (controller.signal.aborted) return;
          if (renewed && renewed !== token) return; // Auth event starts verification with the new token.
        }
        if (!response.ok) {
          if (response.status === 401 || response.status === 403) {
            setAuthorizedUser(''); clearViewCache();
          }
          throw new Error(response.status === 403 ? 'Esta cuenta no tiene acceso a JobHunt.' : response.status === 401 ? 'Tu sesión ha caducado. Inicia sesión de nuevo.' : 'No se pudo verificar el acceso. Inténtalo de nuevo.');
        }
        if (!controller.signal.aborted && activeUser.current === userId) {
          verifiedToken.current = token!; setAuthorizedUser(userId!); setError('');
        }
      } catch (failure) {
        // A transient network error must not unmount an already authorized user's app.
        if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : 'No se pudo verificar la sesión.');
      }
    }
    void verify();
    return () => controller.abort();
  }, [token, userId]);
  useEffect(() => {
    function expired() { setAuthorizedUser(''); clearViewCache(); setError('Tu sesión ha caducado. Inicia sesión de nuevo.'); }
    window.addEventListener('jobhunt-session-expired', expired);
    return () => window.removeEventListener('jobhunt-session-expired', expired);
  }, []);
  async function logout() {
    if (working) return;
    setWorking(true); setError('');
    try { await logoutSession(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : 'No se pudo cerrar sesión.'); }
    finally { setWorking(false); }
  }
  async function login(event: FormEvent) {
    event.preventDefault();
    setWorking(true); setError('');
    try {
      const result = await supabase!.auth.signInWithPassword({ email, password });
      if (result.error) throw new Error('Correo o contraseña incorrectos, o cuenta sin confirmar.');
      setPassword('');
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'No se pudo iniciar sesión.'); }
    finally { setWorking(false); }
  }
  if (deploymentProblem) return <main className="min-h-screen bg-dark-900 p-10 text-white"><p role="alert">Falta configurar VITE_API_URL, VITE_SUPABASE_URL y VITE_SUPABASE_ANON_KEY en el despliegue.</p></main>;
  if (!supabase) return children;
  if (!ready) return <p role="status">Comprobando sesión…</p>;
  if (session && authorizedUser === session.user.id) return <><div className="flex flex-wrap justify-end gap-3 bg-dark-900 p-2 text-sm text-white">{error && <span role="alert" className="text-amber-300">{error}</span>}<span>{session.user.email}</span><button disabled={working} onClick={() => void logout()}>Cerrar sesión</button></div>{children}</>;
  return <main className="flex min-h-screen items-center justify-center bg-dark-900 p-6 text-white">
    <form onSubmit={login} className="w-full max-w-md space-y-5 rounded-xl border border-dark-700 p-8">
      <h1 className="text-2xl font-bold">Acceder a JobHunt</h1>
      {error && <p role="alert" className="text-red-300">{error}</p>}
      {session ? <><p>Verificando acceso…</p><button type="button" disabled={working} onClick={() => void logout()}>Usar otra cuenta</button></> : <>
        <label className="block">Correo<input autoComplete="username" type="email" required value={email} onChange={e => setEmail(e.target.value)} className="mt-2 w-full rounded border border-dark-700 bg-dark-800 p-3" /></label>
        <label className="block">Contraseña<input autoComplete="current-password" type="password" required value={password} onChange={e => setPassword(e.target.value)} className="mt-2 w-full rounded border border-dark-700 bg-dark-800 p-3" /></label>
        <button disabled={working} className="rounded bg-white px-5 py-3 text-black disabled:opacity-50">{working ? 'Entrando…' : 'Iniciar sesión'}</button>
      </>}
    </form>
  </main>;
}
