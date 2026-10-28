import { useCallback, useEffect, useState } from 'react';
import {
  LayoutDashboard,
  Briefcase,
  Settings,
  User,
  PanelLeftClose,
  PanelLeftOpen,
} from 'lucide-react';
import { STATUSES } from './types';
import type { JobStatus, Stats } from './types';
import JobDetailPage from './JobDetailPage';
import MyCVsPage from './MyCVsPage';
import CVDetailPage from './CVDetailPage';
import SettingsPage from './SettingsPage';
import OffersPage from './OffersPage';
import DashboardColumn from './components/DashboardColumn';

const API_URL = 'http://localhost:8000';

type ViewType = 'dashboard' | 'offers' | 'detail' | 'my-cvs' | 'cv-detail' | 'settings';
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
      <button onClick={() => setView('settings')} className={`w-full flex items-center gap-3 px-3 py-2 rounded-md transition ${currentView === 'settings' ? 'bg-white text-black' : 'hover:bg-dark-800'} ${!isOpen && 'justify-center'}`} title="Ajustes">
        <Settings size={18} /> {isOpen && <span>Ajustes</span>}
      </button>
    </nav>
  </aside>
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

// Componente Principal
function App() {
  const [boardRevision, setBoardRevision] = useState(0);
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);

  // Estados para controlar la navegación
  const [currentView, setCurrentView] = useState<ViewType>('dashboard');
  const [offersSource, setOffersSource] = useState('all');
  const [selectedCVId, setSelectedCVId] = useState<string | null>(null);
  const [cvBackView, setCVBackView] = useState<'detail' | 'my-cvs'>('my-cvs');
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    try {
      const statsResponse = await fetch(`${API_URL}/api/stats`);
      if (!statsResponse.ok) throw new Error(`La API respondió ${statsResponse.status}`);
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
      setBoardRevision(value => value + 1);
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
    if (currentView === 'settings') return <SettingsPage />;
    if (currentView === 'detail' && selectedJobId) {
      return (
        <JobDetailPage
          jobId={selectedJobId}
          onBack={() => setCurrentView('offers')}
          onMove={moveJob}
          onViewCV={(versionId) => {
            setSelectedCVId(versionId);
            setCVBackView('detail');
            setCurrentView('cv-detail');
          }}
        />
      );
    }

    if (currentView === 'my-cvs') {
      return (
        <MyCVsPage
          onViewCV={(versionId) => {
            setSelectedCVId(versionId);
            setCVBackView('my-cvs');
            setCurrentView("cv-detail");
          }}
        />
      );
    }

    if (currentView === 'cv-detail' && selectedCVId) {
      return (
        <CVDetailPage
          versionId={selectedCVId}
          onBack={() => setCurrentView(cvBackView)}
          backLabel={cvBackView === 'detail' ? 'Volver a la oferta' : 'Volver a mis CV'}
        />
      );
    }

    if (currentView === 'offers') {
      return <OffersPage source={offersSource} onSourceChange={setOffersSource} onMove={moveJob} onViewDetail={handleViewDetail} />;
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
          {STATUSES.map(status => <DashboardColumn key={status} status={status} refreshToken={boardRevision} onMove={moveJob} onViewDetail={handleViewDetail} />)}
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
