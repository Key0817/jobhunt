import { useCallback, useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { STATUSES, STATUS_LABELS } from './types';
import type { Job, JobStatus } from './types';
import JobCard from './components/JobCard';
import EmailImportPanel from './components/EmailImportPanel';

const SOURCES = [['all', 'Todas las fuentes'], ['email', 'Todas las alertas de correo'], ['linkedin_email', 'LinkedIn · correo'],
  ['indeed_email', 'Indeed · correo'], ['computrabajo_email', 'Computrabajo · correo'], ['trabajosdiarios_email', 'Trabajos Diarios · correo'], ['getonboard', 'Get on Board'], ['greenhouse', 'Greenhouse'], ['smartrecruiters', 'SmartRecruiters'], ['lever', 'Lever'], ['ashby', 'Ashby'], ['workday', 'Workday'], ['workable', 'Workable'], ['bamboohr', 'BambooHR'], ['breezy', 'Breezy'], ['recruitee', 'Recruitee'], ['public_web', 'Webs de empresas'], ['dynamitejobs', 'Dynamite Jobs'], ['elempleo_email', 'Elempleo · correo'], ['dynamitejobs_email', 'Dynamite Jobs · correo'], ['procomer', 'Talent Costa Rica · PROCOMER']];

export default function OffersPage({ source, onSourceChange, onMove, onViewDetail }: {
  source: string; onSourceChange: (source: string) => void;
  onMove: (id: string, status: JobStatus) => Promise<boolean>; onViewDetail: (id: string) => void;
}) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [sort, setSort] = useState('recent');
  const [status, setStatus] = useState('all');
  const [score, setScore] = useState(0);
  const [modality, setModality] = useState('all');
  const [days, setDays] = useState('all');
  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    const query = new URLSearchParams({ limit: '1000', sort, min_score: String(score) });
    if (source !== 'all') query.set('source', source);
    if (status !== 'all') query.set('status', status);
    try {
      const response = await fetch(`http://localhost:8000/api/jobs?${query}`, { signal });
      if (!response.ok) throw new Error('No se pudieron cargar las ofertas.');
      const data: Job[] = await response.json();
      if (!signal?.aborted) { setJobs(data); setError(''); }
    } catch (failure) {
      if (!signal?.aborted) setError(failure instanceof Error ? failure.message : 'Error al consultar ofertas.');
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [source, sort, score, status]);
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);
  async function move(id: string, target: JobStatus) {
    if (await onMove(id, target)) await load();
  }
  const filtered = jobs.filter(job => {
    if (modality === 'remote' && job.remote !== true) return false;
    if (modality === 'onsite' && job.remote !== false) return false;
    if (modality === 'unknown' && !job.location_needs_review && job.location) return false;
    if (days !== 'all') {
      if (!job.detected_at) return false;
      if (Date.now() - new Date(job.detected_at).getTime() > Number(days) * 86400000) return false;
    }
    return true;
  });
  return (
    <div>
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3"><h1 className="text-2xl font-bold">Ofertas de empleo</h1><button type="button" onClick={() => void load()} disabled={loading} className="inline-flex items-center gap-2 rounded-lg border border-dark-700 px-3 py-2 text-sm text-gray-300 disabled:opacity-50"><RefreshCw size={16} className={loading ? 'animate-spin' : ''} />Actualizar ofertas</button></div>
      {source.includes('email') && <EmailImportPanel onRefresh={load} />}
      <div className="mb-6 grid grid-cols-1 gap-4 rounded-xl border border-dark-700 bg-dark-800 p-5 sm:grid-cols-2 xl:grid-cols-3">
        <label className="offers-filter-label">Fuente<select aria-label="Filtrar por fuente" value={source} onChange={e => onSourceChange(e.target.value)} className="offers-filter-select mt-2">{SOURCES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label className="offers-filter-label">Orden<select value={sort} onChange={e => setSort(e.target.value)} className="offers-filter-select mt-2"><option value="recent">Más recientes primero</option><option value="score">Mayor puntuación primero</option></select></label>
        <label className="offers-filter-label">Estado<select value={status} onChange={e => setStatus(e.target.value)} className="offers-filter-select mt-2"><option value="all">Todos los estados</option>{STATUSES.map(value => <option key={value} value={value}>{STATUS_LABELS[value]}</option>)}</select></label>
        <label className="offers-filter-label">Puntuación mínima: {score}<input type="range" min="0" max="100" step="5" value={score} onChange={e => setScore(Number(e.target.value))} className="offers-filter-slider mt-3 w-full" /></label>
        <label className="offers-filter-label">Modalidad<select value={modality} onChange={e => setModality(e.target.value)} className="offers-filter-select mt-2"><option value="all">Cualquier modalidad</option><option value="remote">Remoto</option><option value="onsite">Presencial / híbrido</option><option value="unknown">Ubicación por verificar</option></select></label>
        <label className="offers-filter-label">Añadidas al sistema<select value={days} onChange={e => setDays(e.target.value)} className="offers-filter-select mt-2"><option value="all">Cualquier fecha</option><option value="1">Últimas 24 horas</option><option value="7">Últimos 7 días</option><option value="30">Últimos 30 días</option></select></label>
      </div>
      {error && <p role="alert" className="mb-4 text-red-300">{error}</p>}
      <p role="status" className="mb-4 text-sm text-gray-400">{loading ? 'Cargando ofertas...' : `${filtered.length} ofertas encontradas`}</p>
      {!loading && jobs.length === 1000 && <p className="mb-4 text-sm text-amber-300">Se muestran hasta 1000 ofertas. Ajusta la fuente o el estado para acotar la búsqueda.</p>}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3 xl:grid-cols-5">{filtered.map(job => <JobCard key={job.id} job={job} onMove={(id, target) => void move(id, target)} onViewDetail={onViewDetail} />)}</div>
      {!loading && !error && filtered.length === 0 && <p className="rounded-xl border border-dashed border-dark-700 p-10 text-center text-gray-400">No hay ofertas con estos filtros. Reduce la puntuación mínima o selecciona otra fuente.</p>}
    </div>
  );
}
