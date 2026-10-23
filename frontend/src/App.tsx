import { useCallback, useEffect, useState } from 'react';
import {
  LayoutDashboard,
  Briefcase,
  Settings,
  User,
  PanelLeftClose,
  PanelLeftOpen,
  ExternalLink,
} from 'lucide-react';
import { STATUSES, STATUS_LABELS } from './types';
import type { Job, JobStatus, Stats } from './types';
import JobDetailPage from './JobDetailPage';
import MyCVsPage from './MyCVsPage';

const API_URL = 'http://localhost:8000';
const CARDS_PER_COLUMN = 25;
type ViewType = 'dashboard' | 'offers' | 'detail' | 'my-cvs';
// Componente de la Barra Lateral modificado para recibir y cambiar la vista
const Sidebar = ({ isOpen, toggleSidebar, currentView, setView }: { isOpen: boolean; toggleSidebar: () => void; currentView: ViewType; setView: (view: ViewType) => void; }) => (
  <aside className={`${isOpen ? 'w-64' : 'w-20'} border-r border-dark-700 h-screen p-4 flex flex-col gap-6 fixed transition-all duration-300 z-20 bg-dark-900`}>
    <div className={`flex items-center ${isOpen ? 'justify-between' : 'justify-center'} px-2`}>
      {isOpen && (
        <div className="flex items-center gap-2 font-bold text-xl">
          <div className="bg-white text-black p-1 rounded font-black">JH</div>
          JobHunt
        </div>
      )}
      <button
        onClick={toggleSidebar}
        className="p-1 hover:bg-dark-800 rounded-md text-gray-400 hover:text-white transition"
      >
        {isOpen ? <PanelLeftClose size={20} /> : <PanelLeftOpen size={20} />}
      </button>
    </div>

    <nav className="flex flex-col gap-2 text-sm text-gray-400 mt-4">
      {isOpen && <div className="text-xs font-semibold mb-1 px-2 text-gray-500">PRINCIPAL</div>}
      <button onClick={() => setView('dashboard')} className={`w-full flex items-center gap-3 px-3 py-2 rounded-md font-medium transition ${currentView === 'dashboard' ? 'bg-white text-black' : 'hover:bg-dark-800'} ${!isOpen && 'justify-center'}`} title="Resumen">
        <LayoutDashboard size={18} /> {isOpen && <span>Resumen</span>}
      </button>

      {isOpen && <div className="text-xs font-semibold mt-4 mb-1 px-2 text-gray-500">GESTIÓN</div>}
      <button onClick={() => setView('my-cvs')} className={`w-full flex items-center gap-3 px-3 py-2 rounded-md transition ${currentView === 'my-cvs' ? 'bg-white text-black'
        : 'hover:bg-dark-800'
        } ${!isOpen && 'justify-center'}`}
        title="Mi Currículum"
      >
        <User size={18} />
        {isOpen && <span>Mi Currículum</span>}
      </button>
      <button onClick={() => setView('offers')} className={`w-full flex items-center gap-3 px-3 py-2 rounded-md transition ${currentView === 'offers' ? 'bg-white text-black' : 'hover:bg-dark-800'} ${!isOpen && 'justify-center'}`} title="Ofertas">
        <Briefcase size={18} /> {isOpen && <span>Ofertas</span>}
      </button>


      {isOpen && <div className="text-xs font-semibold mt-4 mb-1 px-2 text-gray-500">SISTEMA</div>}
      <button className={`w-full flex items-center gap-3 px-3 py-2 hover:bg-dark-800 rounded-md transition ${!isOpen && 'justify-center'}`} title="Ajustes">
        <Settings size={18} /> {isOpen && <span>Ajustes</span>}
      </button>
    </nav>
  </aside>
);

const ScoreBadge = ({ score }: { score: number }) => {
  const color =
    score >= 70
      ? 'bg-emerald-500/15 text-emerald-300'
      : score >= 40
        ? 'bg-amber-500/15 text-amber-300'
        : 'bg-gray-500/15 text-gray-400';
  return <span className={`${color} text-xs font-semibold px-2 py-0.5 rounded-full`}>{Math.round(score)}</span>;
};

// Se añadió onViewDetail al JobCard
const JobCard = ({ job, onMove, onViewDetail }: { job: Job; onMove: (id: string, status: JobStatus) => void; onViewDetail: (id: string) => void }) => (
  <div className="bg-dark-800 border border-dark-700 p-4 rounded-lg flex flex-col justify-between h-full hover:border-gray-500 transition">
    <div>
      <div className="flex items-start justify-between gap-2 mb-2">
        <h4
          onClick={() => onViewDetail(job.id)}
          className="font-semibold text-sm cursor-pointer hover:text-blue-400 hover:underline line-clamp-2"
        >
          {job.title}
        </h4>
        <ScoreBadge score={job.score} />
      </div>
      <p className="text-xs text-gray-400 mt-1">{job.company || 'Empresa no indicada'}</p>
      {job.location && <p className="text-xs text-gray-500 mt-0.5">{job.location}</p>}
    </div>
    <div className="flex items-center justify-between gap-2 mt-4 pt-3 border-t border-dark-700">
      <a
        href={job.url}
        target="_blank"
        rel="noopener noreferrer"
        className="flex items-center gap-1 text-xs text-gray-400 hover:text-white transition"
      >
        <ExternalLink size={12} /> Abrir
      </a>
      <select
        value={job.status}
        onChange={(e) => onMove(job.id, e.target.value as JobStatus)}
        className="status-select"
        aria-label="Mover a otro estado"
      >
        {STATUSES.map((s) => (
          <option key={s} value={s}>
            {STATUS_LABELS[s]}
          </option>
        ))}
      </select>
    </div>
  </div>
);

const StatCard = ({ label, value, hint }: { label: string; value: number | string; hint?: string }) => (
  <div className="bg-dark-800 border border-dark-700 rounded-xl p-5">
    <div className="text-3xl font-bold mb-1">{value}</div>
    <div className="text-sm text-gray-400">{label}</div>
    {hint && <div className="text-xs text-gray-500 mt-1">{hint}</div>}
  </div>
);

const WeeklyChart = ({ data }: { data: Stats['weekly_applications'] }) => {
  const max = Math.max(1, ...data.map((d) => d.count));
  return (
    <div className="bg-dark-800 border border-dark-700 rounded-xl p-5 mb-8">
      <div className="text-sm font-semibold mb-4">Postulaciones por semana</div>
      <div className="flex items-end gap-3 h-32">
        {data.map((d) => (
          <div key={d.week} className="flex-1 flex flex-col items-center justify-end h-full gap-1">
            <span className="text-xs text-gray-400">{d.count}</span>
            <div
              className="w-full bg-white/80 rounded-t"
              style={{ height: `${Math.max(2, (d.count / max) * 100)}%` }}
              title={`Semana del ${d.week}: ${d.count}`}
            />
            <span className="text-[10px] text-gray-500">{d.week.slice(5).replace('-', '/')}</span>
          </div>
        ))}
      </div>
    </div>
  );
};

// Componente para la página de ofertas en Grid
const OffersPage = ({ jobs, onMove, onViewDetail }: { jobs: Job[]; onMove: (id: string, status: JobStatus) => void; onViewDetail: (id: string) => void }) => {
  const [filterStatus, setFilterStatus] = useState<string>('Todos');
  const [filterMinScore, setFilterMinScore] = useState<number>(0);
  const [filterModality, setFilterModality] = useState<string>('Todas');
  const [filterDate, setFilterDate] = useState<string>('Todas');

  // Lógica de filtrado en cliente
  const filteredJobs = jobs.filter(job => {
    // 1. Filtro por estado
    if (filterStatus !== 'Todos' && job.status !== filterStatus) return false;

    // 2. Filtro por puntaje
    if (job.score < filterMinScore) return false;

    // 3. Filtro por modalidad (Asumiendo que remoto es un booleano en tu BD, o se infiere por "remote")
    if (filterModality === 'Remoto' && job.remote !== true) return false;
    if (filterModality === 'Presencial' && job.remote === true) return false;

    // 4. Filtro por fecha (Asumiendo que posted_at es un string ISO)
    if (filterDate !== 'Todas' && job.posted_at) {
      const jobDate = new Date(job.posted_at);
      const now = new Date();
      const diffDays = (now.getTime() - jobDate.getTime()) / (1000 * 3600 * 24);

      if (filterDate === 'Hoy' && diffDays > 1) return false;
      if (filterDate === 'Últimos 7 días' && diffDays > 7) return false;
      if (filterDate === 'Último mes' && diffDays > 30) return false;
    }

    return true;
  }).sort((a, b) => b.score - a.score);

  return (
    <div>
      <h2 className="text-2xl font-bold mb-6">Todas las Ofertas</h2>

      {/* Barra de Filtros */}
      <div className="bg-dark-800 border border-dark-700 p-5 rounded-xl mb-6 grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-5 items-end">
        <div className="flex flex-col gap-2">
          <label className="offers-filter-label">Estado</label>
          <select
            value={filterStatus}
            onChange={e => setFilterStatus(e.target.value)}
            className="offers-filter-select"
            aria-label="Filtrar por estado"
          >
            <option value="Todos">Todos</option>
            {STATUSES.map(s => <option key={s} value={s}>{STATUS_LABELS ? STATUS_LABELS[s] : s}</option>)}
          </select>
        </div>

        <div className="flex flex-col gap-2">
          <label className="offers-filter-label" htmlFor="minimum-score">Puntaje mínimo</label>
          <div className="offers-filter-range">
            <input
              id="minimum-score"
              type="range"
              min="0" max="100" step="5"
              value={filterMinScore}
              onChange={e => setFilterMinScore(Number(e.target.value))}
              className="offers-filter-slider"
            />
            <span className="offers-filter-value">{filterMinScore}</span>
          </div>
        </div>

        <div className="flex flex-col gap-2">
          <label className="offers-filter-label">Modalidad</label>
          <select
            value={filterModality}
            onChange={e => setFilterModality(e.target.value)}
            className="offers-filter-select"
            aria-label="Filtrar por modalidad"
          >
            <option value="Todas">Cualquier modalidad</option>
            <option value="Remoto">Remoto</option>
            <option value="Presencial">Presencial / Híbrido</option>
          </select>
        </div>

        <div className="flex flex-col gap-2">
          <label className="offers-filter-label">Publicación</label>
          <select
            value={filterDate}
            onChange={e => setFilterDate(e.target.value)}
            className="offers-filter-select"
            aria-label="Filtrar por fecha de publicación"
          >
            <option value="Todas">Cualquier fecha</option>
            <option value="Hoy">Hoy</option>
            <option value="Últimos 7 días">Últimos 7 días</option>
            <option value="Último mes">Último mes</option>
          </select>
        </div>
      </div>

      {/* Grid de Tarjetas */}
      <div className="grid grid-cols-1 md:grid-cols-3 xl:grid-cols-5 gap-4">
        {filteredJobs.length > 0 ? (
          filteredJobs.map(job => (
            <JobCard key={job.id} job={job} onMove={onMove} onViewDetail={onViewDetail} />
          ))
        ) : (
          <div className="col-span-full py-16 flex flex-col items-center justify-center border-2 border-dashed border-dark-700 rounded-xl text-gray-500">
            <p className="text-lg mb-1">No se encontraron ofertas</p>
            <p className="text-sm">Prueba ajustando los filtros de búsqueda.</p>
          </div>
        )}
      </div>
    </div>
  );
};

// Componente Principal
function App() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);

  // Estados para controlar la navegación
  const [currentView, setCurrentView] = useState<ViewType>('dashboard');
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    try {
      const [jobsResponse, statsResponse] = await Promise.all([
        fetch(`${API_URL}/api/jobs?limit=500`),
        fetch(`${API_URL}/api/stats`),
      ]);
      if (!jobsResponse.ok || !statsResponse.ok) {
        throw new Error(`La API respondió ${jobsResponse.status} / ${statsResponse.status}`);
      }
      setJobs((await jobsResponse.json()) as Job[]);
      setStats((await statsResponse.json()) as Stats);
      setError(null);
    } catch (e) {
      console.error('Error obteniendo datos de la API:', e);
      setError('No se pudo cargar la información. ¿Está corriendo la API en ' + API_URL + '?');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  const moveJob = async (id: string, status: JobStatus) => {
    try {
      const response = await fetch(`${API_URL}/api/jobs/${id}/status`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status }),
      });
      if (!response.ok) {
        throw new Error(`La API respondió ${response.status}`);
      }
      await loadData();
      return true;
    } catch (e) {
      console.error('Error cambiando el estado:', e);
      setError('No se pudo cambiar el estado de la oferta.');
      return false;
    }
  };

  const handleViewDetail = (id: string) => {
    setSelectedJobId(id);
    setCurrentView('detail');
  };

  // Renderizado dinámico de la vista central
  const renderView = () => {
    if (currentView === 'detail' && selectedJobId) {
      return (
        <JobDetailPage
          jobId={selectedJobId}
          onBack={() => setCurrentView('offers')}
          onMove={moveJob}
        />
      );
    }

    if (currentView === 'my-cvs') {
      return (
        <MyCVsPage
          onViewCV={(versionId) => {
            console.log('CV seleccionado:', versionId);
          }}
        />
      );
    }

    if (currentView === 'offers') {
      return <OffersPage jobs={jobs} onMove={moveJob} onViewDetail={handleViewDetail} />;
    }

    return (
      <>
        {/* Tarjetas de resumen (datos reales) */}
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-4 mb-8">
          <StatCard label="Ofertas" value={stats?.total_jobs ?? '-'} />
          <StatCard label="Nuevas" value={stats?.new_last_7_days ?? '-'} hint="últimos 7 días" />
          <StatCard label="Postuladas" value={stats?.applied ?? '-'} />
          <StatCard label="Entrevistas" value={stats?.interviews ?? '-'} />
          <StatCard label="Ofertas recibidas" value={stats?.offers ?? '-'} />
        </div>

        {stats && <WeeklyChart data={stats.weekly_applications} />}

        {loading && <p className="text-sm text-gray-400 mb-4">Cargando ofertas…</p>}

        {/* Tablero Kanban */}
        <div className="flex gap-4 overflow-x-auto pb-4">
          {STATUSES.map((status) => {
            const columnJobs = jobs.filter((j) => j.status === status);
            const hidden = columnJobs.length - CARDS_PER_COLUMN;
            return (
              <div key={status} className="flex-1 min-w-64">
                <div className="flex justify-between items-center bg-dark-800 border border-dark-700 rounded-full px-3 py-1 mb-4 text-xs font-semibold">
                  <div className="flex items-center gap-2">
                    <div className="w-2 h-2 rounded-full bg-gray-400"></div>
                    {STATUS_LABELS[status]}
                  </div>
                  <span className="text-gray-400">{stats?.by_status[status] ?? columnJobs.length}</span>
                </div>
                <div className="flex flex-col">
                  {columnJobs.slice(0, CARDS_PER_COLUMN).map((job) => (
                    <JobCard key={job.id} job={job} onMove={moveJob} onViewDetail={handleViewDetail} />
                  ))}
                  {hidden > 0 && <p className="text-xs text-gray-500 text-center">+{hidden} más con menor puntaje</p>}
                </div>
              </div>
            );
          })}
        </div>
      </>
    );
  };

  return (
    <div className="min-h-screen bg-dark-900 text-white font-sans overflow-x-hidden">
      <Sidebar
        isOpen={isSidebarOpen}
        toggleSidebar={() => setIsSidebarOpen(!isSidebarOpen)}
        currentView={currentView}
        setView={setCurrentView}
      />

      <main className={`${isSidebarOpen ? 'ml-64' : 'ml-20'} p-8 transition-all duration-300`}>
        {error && (
          <div className="bg-red-500/10 border border-red-500/30 text-red-300 text-sm rounded-lg px-4 py-3 mb-6">
            {error}
          </div>
        )}


        {renderView()}
      </main>
    </div>
  );
}

export default App;