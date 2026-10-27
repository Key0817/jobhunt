import { useCallback, useEffect, useState } from 'react';
import { Mail, RefreshCw } from 'lucide-react';

type Summary = {
  total_offers: number; pending_review_messages: number;
  last_run: null | { status: string; found: number; inserted: number; finished_at: string;
    fetched_messages: number; more_pending: boolean; review_messages: number; read_errors: number };
};

export default function EmailImportPanel({ onOpenOffers, onRefresh }: {
  onOpenOffers?: () => void; onRefresh?: () => Promise<void>;
}) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    try {
      const response = await fetch('http://localhost:8000/api/collection/email', { signal });
      if (!response.ok) throw new Error('No se pudo consultar la última importación.');
      setSummary(await response.json());
      setError('');
    } catch (failure) {
      if (!signal?.aborted) setError(failure instanceof Error ? failure.message : 'Error al consultar las alertas.');
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);
  const last = summary?.last_run;
  return (
    <section className="mb-6 rounded-xl border border-dark-700 bg-dark-800 p-5" aria-busy={loading}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="flex items-center gap-2 font-semibold"><Mail size={18} />Ofertas importadas por correo{summary && <span className="rounded-full bg-blue-500/15 px-2 py-0.5 text-sm text-blue-300">{summary.total_offers}</span>}</h2>
          {last ? <p className="mt-2 text-sm text-gray-400">Última importación: {new Date(last.finished_at).toLocaleString('es-CR')} · {last.inserted} nuevas · {last.status === 'partial' ? 'Parcial' : last.status === 'success' ? 'Completada' : 'Fallida'}</p> : <p className="mt-2 text-sm text-gray-400">Las alertas que importes aparecerán como ofertas con su enlace original.</p>}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" onClick={() => { void load(); void onRefresh?.(); }} disabled={loading} className="inline-flex items-center gap-2 rounded-lg border border-dark-700 px-3 py-2 text-sm text-gray-300 hover:bg-dark-700 disabled:opacity-50"><RefreshCw size={14} className={loading ? 'animate-spin' : ''} />Actualizar</button>
          {onOpenOffers && <button type="button" onClick={onOpenOffers} className="rounded-lg bg-white px-3 py-2 text-sm font-medium text-black hover:bg-gray-200">Ver ofertas de correo</button>}
        </div>
      </div>
      {last?.more_pending && <p className="mt-3 text-sm text-amber-300">Quedan correos para la próxima pasada. Las ofertas ya guardadas están disponibles.</p>}
      {summary && summary.pending_review_messages > 0 && <p className="mt-2 text-sm text-amber-300">{summary.pending_review_messages} mensajes requieren revisión de formato.</p>}
      {!!last?.read_errors && <p className="mt-2 text-sm text-amber-300">Hubo {last.read_errors} errores de lectura que se reintentarán.</p>}
      {error && <p role="alert" className="mt-3 text-sm text-red-300">{error}</p>}
    </section>
  );
}
