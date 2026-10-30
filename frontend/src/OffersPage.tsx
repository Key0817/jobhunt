import { useViewState } from './viewCache';
import { RefreshCw } from 'lucide-react';
import { STATUSES, STATUS_LABELS } from './types';
import type { JobStatus } from './types';
import JobCard from './components/JobCard';
import EmailImportPanel from './components/EmailImportPanel';
import Pagination from './components/Pagination';
import { useJobPage } from './useJobPage';

const SOURCES = [['all', 'Todas las fuentes'], ['email', 'Todas las alertas de correo'], ['linkedin_email', 'LinkedIn · correo'],
  ['indeed_email', 'Indeed · correo'], ['computrabajo_email', 'Computrabajo · correo'], ['trabajosdiarios_email', 'Trabajos Diarios · correo'], ['getonboard', 'Get on Board'], ['greenhouse', 'Greenhouse'], ['smartrecruiters', 'SmartRecruiters'], ['lever', 'Lever'], ['ashby', 'Ashby'], ['workday', 'Workday'], ['workable', 'Workable'], ['bamboohr', 'BambooHR'], ['breezy', 'Breezy'], ['recruitee', 'Recruitee'], ['public_web', 'Webs de empresas'], ['dynamitejobs', 'Dynamite Jobs'], ['elempleo_email', 'Elempleo · correo'], ['dynamitejobs_email', 'Dynamite Jobs · correo'], ['procomer', 'Talent Costa Rica · PROCOMER']];

export default function OffersPage({ source, onSourceChange, onMove, onViewDetail }: {
  source: string; onSourceChange: (source: string) => void;
  onMove: (id: string, status: JobStatus) => Promise<boolean>; onViewDetail: (id: string) => void;
}) {
  const [page, setPage] = useViewState('offers:page', 1);
  const pageSize = 50;
  const [sort, setSort] = useViewState('offers:sort', 'recent');
  const [status, setStatus] = useViewState('offers:status', 'all');
  const [score, setScore] = useViewState('offers:score', 0);
  const [modality, setModality] = useViewState('offers:modality', 'all');
  const [days, setDays] = useViewState('offers:days', 'all');
  const query = new URLSearchParams({ page: String(page), page_size: String(pageSize), sort, min_score: String(score), modality });
  if (source !== 'all') query.set('source', source);
  if (status !== 'all') query.set('status', status);
  if (days !== 'all') query.set('days', days);
  const { data, loading, refreshing, error, refresh: load } = useJobPage(query.toString());
  async function move(id: string, target: JobStatus) {
    await onMove(id, target);
  }
  function changeFilter(update: () => void) { setPage(1); update(); }
  function changePage(value: number) { if (value === page) void load(); else setPage(value); }
  return (
    <div>
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3"><h1 className="text-2xl font-bold">Ofertas de empleo</h1><button type="button" onClick={() => void load()} disabled={loading} className="inline-flex items-center gap-2 rounded-lg border border-dark-700 px-3 py-2 text-sm text-gray-300 disabled:opacity-50"><RefreshCw size={16} className={loading ? 'animate-spin' : ''} />Actualizar ofertas</button></div>
      {source.includes('email') && <EmailImportPanel onRefresh={load} />}
      <div className="mb-6 grid grid-cols-1 gap-4 rounded-xl border border-dark-700 bg-dark-800 p-5 sm:grid-cols-2 xl:grid-cols-3">
        <label className="offers-filter-label">Fuente<select aria-label="Filtrar por fuente" value={source} onChange={e => changeFilter(() => onSourceChange(e.target.value))} className="offers-filter-select mt-2">{SOURCES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label className="offers-filter-label">Orden<select value={sort} onChange={e => changeFilter(() => setSort(e.target.value))} className="offers-filter-select mt-2"><option value="recent">Más recientes primero</option><option value="score">Mayor puntuación primero</option></select></label>
        <label className="offers-filter-label">Estado<select value={status} onChange={e => changeFilter(() => setStatus(e.target.value))} className="offers-filter-select mt-2"><option value="all">Todos los estados</option>{STATUSES.map(value => <option key={value} value={value}>{STATUS_LABELS[value]}</option>)}</select></label>
        <label className="offers-filter-label">Puntuación mínima: {score}<input type="range" min="0" max="100" step="5" value={score} onChange={e => changeFilter(() => setScore(Number(e.target.value)))} className="offers-filter-slider mt-3 w-full" /></label>
        <label className="offers-filter-label">Modalidad<select value={modality} onChange={e => changeFilter(() => setModality(e.target.value))} className="offers-filter-select mt-2"><option value="all">Cualquier modalidad</option><option value="remote">Remoto</option><option value="onsite">Presencial / híbrido</option><option value="unknown">Ubicación por verificar</option></select></label>
        <label className="offers-filter-label">Añadidas al sistema<select value={days} onChange={e => changeFilter(() => setDays(e.target.value))} className="offers-filter-select mt-2"><option value="all">Cualquier fecha</option><option value="1">Últimas 24 horas</option><option value="7">Últimos 7 días</option><option value="30">Últimos 30 días</option></select></label>
      </div>
      {error && <p role="alert" className="mb-4 text-red-300">{error}</p>}
      <p role="status" className="mb-4 text-sm text-gray-400">{loading ? 'Cargando ofertas...' : `${data.total} ofertas encontradas`}</p>
      <Pagination page={data.page} totalPages={data.total_pages} total={data.total} pageSize={data.page_size} loading={loading || refreshing} onPageChange={changePage} />
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3 xl:grid-cols-5">{!loading && !error && data.items.map(job => <JobCard key={job.id} job={job} onMove={(id, target) => void move(id, target)} onViewDetail={onViewDetail} />)}</div>
      {!loading && !error && data.total === 0 && <p className="rounded-xl border border-dashed border-dark-700 p-10 text-center text-gray-400">No hay ofertas con estos filtros. Reduce la puntuación mínima o selecciona otra fuente.</p>}
      <Pagination page={data.page} totalPages={data.total_pages} total={data.total} pageSize={data.page_size} loading={loading || refreshing} onPageChange={changePage} />
    </div>
  );
}
