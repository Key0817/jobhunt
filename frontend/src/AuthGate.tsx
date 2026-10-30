import { useEffect, useState } from 'react';
import type { ReactNode, FormEvent } from 'react';
import type { Session } from '@supabase/supabase-js';
import { API_URL, supabase, deploymentProblem } from './api';

export default function AuthGate({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(!supabase);
  const [authorized, setAuthorized] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!supabase) return;
    const { data } = supabase.auth.onAuthStateChange((_event, current) => { setAuthorized(false); setSession(current); setReady(true); });
    return () => data.subscription.unsubscribe();
  }, []);
  useEffect(() => {
    if (!session) return;
    const controller = new AbortController();
    fetch(`${API_URL}/api/auth/me`, { signal: controller.signal, headers: { Authorization: `Bearer ${session.access_token}` } })
      .then(async response => {
        if (!response.ok) throw new Error(response.status === 403 ? 'Esta cuenta no tiene acceso a JobHunt.' : 'No se pudo verificar tu acceso. Vuelve a iniciar sesión.');
        if (!controller.signal.aborted) { setAuthorized(true); setError(''); }
      })
      .catch(failure => { if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : 'No se pudo verificar la sesión.'); });
    return () => controller.abort();
  }, [session]);
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
  if (session && authorized) return <><div className="flex justify-end gap-3 bg-dark-900 p-2 text-sm text-white"><span>{session.user.email}</span><button onClick={() => void supabase!.auth.signOut()}>Cerrar sesión</button></div>{children}</>;
  return <main className="flex min-h-screen items-center justify-center bg-dark-900 p-6 text-white">
    <form onSubmit={login} className="w-full max-w-md space-y-5 rounded-xl border border-dark-700 p-8">
      <h1 className="text-2xl font-bold">Acceder a JobHunt</h1>
      {error && <p role="alert" className="text-red-300">{error}</p>}
      {session ? <><p>Verificando acceso…</p><button type="button" onClick={() => void supabase!.auth.signOut()}>Usar otra cuenta</button></> : <>
        <label className="block">Correo<input autoComplete="username" type="email" required value={email} onChange={e => setEmail(e.target.value)} className="mt-2 w-full rounded border border-dark-700 bg-dark-800 p-3" /></label>
        <label className="block">Contraseña<input autoComplete="current-password" type="password" required value={password} onChange={e => setPassword(e.target.value)} className="mt-2 w-full rounded border border-dark-700 bg-dark-800 p-3" /></label>
        <button disabled={working} className="rounded bg-white px-5 py-3 text-black disabled:opacity-50">{working ? 'Entrando…' : 'Iniciar sesión'}</button>
      </>}
    </form>
  </main>;
}
