import { useState, useEffect } from 'react';
import { 
  LayoutDashboard, 
  Briefcase, 
  Beaker, 
  Settings, 
  User, 
  PanelLeftClose, 
  PanelLeftOpen 
} from 'lucide-react';
import type { Job, JobStatus } from './types';

// Componente de la Barra Lateral modificado
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

// ... (El componente JobCard se mantiene igual)
const JobCard = ({ job }: { job: Job }) => (
  <div className="bg-dark-800 border border-dark-700 p-4 rounded-lg mb-3 hover:border-gray-500 transition cursor-pointer">
    <h4 className="font-semibold text-sm">{job.title}</h4>
    <p className="text-xs text-gray-400 mt-1">{job.company}</p>
  </div>
);

// Componente Principal
function App() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [isSidebarOpen, setIsSidebarOpen] = useState(true); 

  useEffect(() => {
    const mockData: Job[] = [
      { id: '1', title: 'Desarrollador Backend', company: 'Nubank', status: 'Guardada', score: 90 },
      { id: '2', title: 'Frontend React', company: 'Vercel', status: 'Guardada', score: 85 },
      { id: '3', title: 'Full-stack Engineer', company: 'Shopify', status: 'Aplicada', score: 88 },
      { id: '4', title: 'Desarrollador Node.js', company: 'Red Hat', status: 'Entrevista', score: 92 },
      { id: '5', title: 'Ingeniero de software', company: 'Datadog', status: 'Oferta', score: 95 },
      { id: '6', title: 'Ingeniero DevOps', company: 'OnPrem Inc.', status: 'Rechazada', score: 70 },
    ];
    setJobs(mockData);
  }, []);

  const columns: JobStatus[] = ['Guardada', 'Aplicada', 'Entrevista', 'Oferta', 'Rechazada'];

  return (
<div className="min-h-screen bg-dark-900 text-white font-sans overflow-x-hidden">
      <Sidebar isOpen={isSidebarOpen} toggleSidebar={() => setIsSidebarOpen(!isSidebarOpen)} />
      
      {/* Se quitó "flex-1" de main */}
      <main className={`${isSidebarOpen ? 'ml-64' : 'ml-20'} p-8 transition-all duration-300`}>
        
        {/* Tarjetas de Resumen (KPIs) */}
        <div className="grid grid-cols-4 gap-4 mb-8">
          {[
            { label: 'Ofertas', count: 24 },
            { label: 'Entrevistas', count: 5 },
            { label: 'Ofertas recibidas', count: 2 },
            { label: 'Aplicadas', count: 12 },
          ].map((stat, i) => (
            <div key={i} className="bg-dark-800 border border-dark-700 rounded-xl p-5">
              <div className="text-3xl font-bold mb-1">{stat.count}</div>
              <div className="text-sm text-gray-400">{stat.label}</div>
            </div>
          ))}
        </div>

        {/* Tablero Kanban */}
        <div className="flex gap-4 overflow-x-auto pb-4">
          {columns.map((col) => {
            const columnJobs = jobs.filter(j => j.status === col);
            return (
              <div key={col} className="flex-1 min-w-62.5">
                <div className="flex justify-between items-center bg-dark-800 border border-dark-700 rounded-full px-3 py-1 mb-4 text-xs font-semibold">
                  <div className="flex items-center gap-2">
                    <div className="w-2 h-2 rounded-full bg-gray-400"></div>
                    {col}
                  </div>
                  <span className="text-gray-400">{columnJobs.length}</span>
                </div>
                <div className="flex flex-col">
                  {columnJobs.map(job => (
                    <JobCard key={job.id} job={job} />
                  ))}
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