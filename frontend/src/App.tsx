import { useCallback, useEffect, useState } from 'react';
import {
  LayoutDashboard,
  Briefcase,
  Beaker,
  Settings,
  User,
  PanelLeftClose,
  PanelLeftOpen,
  ExternalLink,
} from 'lucide-react';
import { STATUSES, STATUS_LABELS } from './types';
import type { Job, JobStatus, Stats } from './types';

const API_URL = 'http://localhost:8000';
const CARDS_PER_COLUMN = 25; 

// Componente de la Barra Lateral
const Sidebar = ({ isOpen, toggleSidebar }: { isOpen: boolean; toggleSidebar: () => void }) => (
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
      <a href="#" className={`flex items-center gap-3 px-3 py-2 rounded-md font-medium transition ${isOpen ? 'bg-white text-black' : 'hover:bg-dark-800 justify-center'}`} title="Resumen">
        <LayoutDashboard size={18} /> {isOpen && <span>Resumen</span>}
      </a>

      {isOpen && <div className="text-xs font-semibold mt-4 mb-1 px-2 text-gray-500">GESTIÓN</div>}
      <a href="#" className={`flex items-center gap-3 px-3 py-2 hover:bg-dark-800 rounded-md transition ${!isOpen && 'justify-center'}`} title="Mi Currículum">
        <User size={18} /> {isOpen && <span>Mi Currículum</span>}
      </a>
      <a href="#" className={`flex items-center gap-3 px-3 py-2 hover:bg-dark-800 rounded-md transition ${!isOpen && 'justify-center'}`} title="Ofertas">
        <Briefcase size={18} /> {isOpen && <span>Ofertas</span>}
      </a>
      <a href="#" className={`flex items-center gap-3 px-3 py-2 hover:bg-dark-800 rounded-md transition ${!isOpen && 'justify-center'}`} title="Pruebas">
        <Beaker size={18} /> {isOpen && <span>Pruebas</span>}
      </a>

      {isOpen && <div className="text-xs font-semibold mt-4 mb-1 px-2 text-gray-500">SISTEMA</div>}
      <a href="#" className={`flex items-center gap-3 px-3 py-2 hover:bg-dark-800 rounded-md transition ${!isOpen && 'justify-center'}`} title="Ajustes">
        <Settings size={18} /> {isOpen && <span>Ajustes</span>}
      </a>
    </nav>
  </aside>
);

// Insignia con el puntaje: verde si encaja bien, ámbar si es regular, gris si encaja poco
const ScoreBadge = ({ score }: { score: number }) => {
  const color =
    score >= 70
      ? 'bg-emerald-500/15 text-emerald-300'
      : score >= 40
        ? 'bg-amber-500/15 text-amber-300'
        : 'bg-gray-500/15 text-gray-400';
  return <span className={`${color} text-xs font-semibold px-2 py-0.5 rounded-full`}>{Math.round(score)}</span>;
};

const JobCard = ({ job, onMove }: { job: Job; onMove: (id: string, status: JobStatus) => void }) => (
  <div className="bg-dark-800 border border-dark-700 p-4 rounded-lg mb-3 hover:border-gray-500 transition">
    <div className="flex items-start justify-between gap-2">
      <h4 className="font-semibold text-sm">{job.title}</h4>
      <ScoreBadge score={job.score} />
    </div>
    <p className="text-xs text-gray-400 mt-1">{job.company || 'Empresa no indicada'}</p>
    {job.location && <p className="text-xs text-gray-500 mt-0.5">{job.location}</p>}
    <div className="flex items-center justify-between gap-2 mt-3">
      <a
        href={job.url}
        target="_blank"
        rel="noopener noreferrer"
        className="flex items-center gap-1 text-xs text-gray-400 hover:text-white transition"
      >
        <ExternalLink size={12} /> Abrir oferta
      </a>
      <select
        value={job.status}
        onChange={(e) => onMove(job.id, e.target.value as JobStatus)}
        className="bg-dark-900 border border-dark-700 text-xs rounded px-2 py-1 text-gray-300"
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

// Barras simples (sin librería de gráficos): postulaciones por semana
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

// Componente Principal
function App() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);

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

  // Cambia el estado de una oferta y vuelve a cargar, para que las estadísticas se actualicen
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
    } catch (e) {
      console.error('Error cambiando el estado:', e);
      setError('No se pudo cambiar el estado de la oferta.');
    }
  };

  return (
    <div className="min-h-screen bg-dark-900 text-white font-sans overflow-x-hidden">
      <Sidebar isOpen={isSidebarOpen} toggleSidebar={() => setIsSidebarOpen(!isSidebarOpen)} />

      <main className={`${isSidebarOpen ? 'ml-64' : 'ml-20'} p-8 transition-all duration-300`}>
        {error && (
          <div className="bg-red-500/10 border border-red-500/30 text-red-300 text-sm rounded-lg px-4 py-3 mb-6">
            {error}
          </div>
        )}

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

        {/* Tablero Kanban: una columna por estado, con las mejores ofertas arriba */}
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
                    <JobCard key={job.id} job={job} onMove={moveJob} />
                  ))}
                  {hidden > 0 && <p className="text-xs text-gray-500 text-center">+{hidden} más con menor puntaje</p>}
                </div>
              </div>
            );
          })}
        </div>
      </main>
    </div>
  );
}

export default App;
