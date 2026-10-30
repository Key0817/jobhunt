import { API_URL, apiFetch, API_ORIGIN } from './api';
import { useCallback, useEffect, useRef, useState } from 'react';
import { CheckCircle2, ExternalLink, LoaderCircle, Mail, RefreshCw, Unplug } from 'lucide-react';



type GmailStatus = {
  configured: boolean;
  missing: string[];
  configuration_error: string | null;
  connected: boolean;
  email: string | null;
  folder: string;
  redirect_uri: string;
};

export default function SettingsPage() {
  const [status, setStatus] = useState<GmailStatus | null>(null);
  const [folder, setFolder] = useState('JobAlerts');
  const [loading, setLoading] = useState(true);
  const [connecting, setConnecting] = useState(false);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const popup = useRef<Window | null>(null);

  const loadStatus = useCallback(async () => {
    try {
      const response = await apiFetch(`${API_URL}/api/settings/gmail`, { credentials: 'include' });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'No se pudo consultar la conexión Gmail.');
      setStatus(data);
      setFolder(data.folder);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'No se pudo cargar Ajustes.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadStatus();
    const receive = (event: MessageEvent) => {
      if (event.origin !== API_ORIGIN || event.source !== popup.current || event.data?.type !== 'jobhunt-gmail-oauth') return;
      setConnecting(false);
      popup.current = null;
      if (event.data.success) {
        setMessage('Gmail conectado. Las próximas recolecciones importarán las alertas de tu etiqueta.');
        setError('');
      } else {
        setError(event.data.message || 'No se completó la autorización.');
      }
      void loadStatus();
    };
    window.addEventListener('message', receive);
    return () => {
      window.removeEventListener('message', receive);
      popup.current?.close();
    };
  }, [loadStatus]);

  useEffect(() => {
    if (!connecting) return;
    const timer = window.setInterval(() => {
      if (popup.current?.closed) {
        popup.current = null;
        setConnecting(false);
        void loadStatus();
      }
    }, 500);
    return () => window.clearInterval(timer);
  }, [connecting, loadStatus]);

  async function connect() {
    setError('');
    setMessage('');
    popup.current = window.open('about:blank', 'jobhunt-gmail', 'width=600,height=760');
    if (!popup.current) {
      setError('Permite las ventanas emergentes de JobHunt para conectar Gmail.');
      return;
    }
    setConnecting(true);
    try {
      const response = await apiFetch(`${API_URL}/api/settings/gmail/connect`, {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'No se pudo iniciar la conexión.');
      if (popup.current && !popup.current.closed) popup.current.location.href = data.launch_url || data.authorization_url;
    } catch (failure) {
      popup.current?.close();
      popup.current = null;
      setConnecting(false);
      setError(failure instanceof Error ? failure.message : 'No se pudo conectar Gmail.');
    }
  }

  async function checkLabel() {
    setWorking(true);
    setError('');
    setMessage('');
    try {
      const response = await apiFetch(`${API_URL}/api/settings/gmail/check`, {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: '{}',
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'No se pudo comprobar la etiqueta.');
      if (data.label_exists) setMessage(`La etiqueta ${status?.folder} está disponible para importar alertas.`);
      else setError(`No se encontró la etiqueta ${status?.folder}. Créala en Gmail y añade tus alertas.`);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'No se pudo comprobar Gmail.');
    } finally {
      setWorking(false);
    }
  }

  async function disconnect() {
    setWorking(true);
    setError('');
    setMessage('');
    try {
      const response = await apiFetch(`${API_URL}/api/settings/gmail/disconnect`, {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: '{}',
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'No se pudo desconectar Gmail.');
      setMessage(data.revoked ? 'Gmail desconectado. Se revocó el acceso.' : 'La conexión se eliminó de JobHunt. No se pudo confirmar la revocación en Google; puedes revisarla en tu cuenta Google.');
      await loadStatus();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'No se pudo desconectar Gmail.');
    } finally {
      setWorking(false);
    }
  }

  if (loading) return <p role="status" className="flex items-center gap-2 text-gray-400"><LoaderCircle size={18} className="animate-spin" />Cargando ajustes...</p>;

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div><h1 className="text-2xl font-bold">Ajustes</h1><p className="mt-2 text-sm text-gray-400">Conecta los servicios que usa tu búsqueda de empleo.</p></div>
      <section className="rounded-xl border border-dark-700 bg-dark-800 p-6">
        <div className="flex items-start gap-3">
          <Mail className="mt-1 text-gray-300" size={26} />
          <div><h2 className="text-lg font-semibold">Alertas de empleo por Gmail</h2>
            <p className="mt-2 text-sm leading-6 text-gray-400">Autoriza a JobHunt a leer las alertas de LinkedIn, Indeed y Computrabajo en una etiqueta dedicada. No envía, elimina ni marca mensajes como leídos.</p>
          </div>
        </div>
        {error && <p role="alert" className="mt-4 rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-300">{error}</p>}
        {message && <p role="status" className="mt-4 rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-3 text-sm text-emerald-300">{message}</p>}
        {status?.connected ? (
          <div className="mt-5 space-y-4 border-t border-dark-700 pt-5">
            <p className="flex items-center gap-2 text-emerald-300"><CheckCircle2 size={18} />Conectado: {status.email}</p>
            <p className="text-sm text-gray-400">Etiqueta de alertas: <span className="font-medium text-white">{status.folder}</span></p>
            <div className="flex flex-wrap gap-3">
              <button type="button" onClick={() => void checkLabel()} disabled={working || connecting} className="flex items-center gap-2 rounded-lg bg-white px-4 py-2 text-sm font-medium text-black disabled:opacity-50"><RefreshCw size={16} />Comprobar etiqueta</button>
              <button type="button" onClick={() => void connect()} disabled={working || connecting || !status.configured} className="rounded-lg border border-dark-700 px-4 py-2 text-sm hover:bg-dark-700 disabled:opacity-50">{connecting ? 'Esperando autorización...' : 'Reconectar'}</button>
              <button type="button" onClick={() => void disconnect()} disabled={working || connecting} className="flex items-center gap-2 rounded-lg border border-dark-700 px-4 py-2 text-sm text-gray-300 hover:bg-dark-700 disabled:opacity-50"><Unplug size={16} />Desconectar</button>
            </div>
          </div>
        ) : (
          <div className="mt-5 space-y-4 border-t border-dark-700 pt-5">
            <label className="block text-sm text-gray-300" htmlFor="gmail-folder">Etiqueta de Gmail para las alertas</label>
            <input id="gmail-folder" value={folder} maxLength={150} onChange={event => setFolder(event.target.value)} disabled={connecting} className="w-full rounded-lg border border-dark-700 bg-dark-900 px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-blue-400" />
            <p className="text-xs leading-5 text-gray-400">Crea esta etiqueta en Gmail y añade un filtro para colocar allí tus alertas. La autorización permite leer Gmail; JobHunt limita la importación a esta etiqueta.</p>
            <button type="button" onClick={() => void connect()} disabled={!status?.configured || connecting || !folder.trim()} className="flex items-center gap-2 rounded-lg bg-white px-4 py-2 text-sm font-semibold text-black hover:bg-gray-200 disabled:opacity-50">{connecting ? <LoaderCircle size={18} className="animate-spin" /> : <Mail size={18} />}{connecting ? 'Esperando autorización...' : 'Conectar con Google'}</button>
          </div>
        )}
        {status && !status.configured && (
          <div className="mt-5 rounded-lg border border-amber-500/30 bg-amber-500/10 p-4 text-sm leading-6 text-amber-200">
            <p className="font-semibold">Configuración inicial de Google pendiente</p>
            <p>Habilita Gmail API, crea un cliente OAuth de tipo Aplicación web y añade tu cuenta como usuario de prueba si la aplicación está en pruebas.</p>
            <p className="mt-2">URL de retorno autorizada:</p><code className="block break-all text-xs">{status.redirect_uri}</code>
            {status.missing.length > 0 && <p className="mt-2">Variables pendientes del backend: {status.missing.join(', ')}.</p>}
            {status.configuration_error && <p className="mt-2">{status.configuration_error}</p>}
            <a className="mt-2 inline-flex items-center gap-1 underline" href="https://console.cloud.google.com/apis/credentials" target="_blank" rel="noreferrer">Abrir Google Cloud<ExternalLink size={14} /></a>
          </div>
        )}
        <p className="mt-5 text-xs leading-5 text-gray-500">Después de conectar, la cuenta se utiliza en collect. GitHub Actions necesita los mismos secretos OAuth y la clave de cifrado para reutilizar la conexión guardada.</p>
      </section>
    </div>
  );
}
