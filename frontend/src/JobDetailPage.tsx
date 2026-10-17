import { useEffect, useState } from 'react';
import { ArrowLeft, ExternalLink, FileText } from 'lucide-react';
import { STATUSES, STATUS_LABELS } from './types';
import type { Job, JobStatus } from './types';

const API_URL = 'http://localhost:8000';

interface JobDetailPageProps {
  jobId: string;
  onBack: () => void;
  onMove: (id: string, status: JobStatus) => Promise<boolean>;
}

export default function JobDetailPage({ jobId, onBack, onMove }: JobDetailPageProps) {
  const [job, setJob] = useState<Job | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchDetail = async () => {
      try {
        const res = await fetch(`${API_URL}/api/jobs/${jobId}`);
        if (res.ok) {
          setJob(await res.json());
        }
      } catch (error) {
        console.error("Error cargando detalle:", error);
      } finally {
        setLoading(false);
      }
    };
    fetchDetail();
  }, [jobId]);

  const handleMove = async (status: JobStatus) => {
    if (!job) return;
    if (await onMove(job.id, status)) {
      setJob((currentJob) => currentJob && { ...currentJob, status });
    }
  };

  if (loading) return <div className="text-gray-400">Cargando detalles...</div>;
  if (!job) return <div className="text-red-400">Error: No se encontró la oferta.</div>;

  return (
    <div className="max-w-4xl mx-auto">
      <button onClick={onBack} className="flex items-center gap-2 text-gray-400 hover:text-white mb-6 transition">
        <ArrowLeft size={16} /> Volver
      </button>

      <div className="bg-dark-800 border border-dark-700 rounded-xl p-8">
        <div className="flex justify-between items-start mb-6">
          <div>
            <h1 className="text-3xl font-bold mb-2">{job.title}</h1>
            <div className="flex items-center gap-4 text-gray-400 text-sm">
              <span className="font-semibold text-white">{job.company || 'Empresa no indicada'}</span>
              <span>{job.location || job.country}</span>
              {job.remote && <span className="bg-dark-700 px-2 py-0.5 rounded">Remoto</span>}
            </div>
          </div>
          <div className="flex flex-col gap-2 items-end">
            <span className="bg-emerald-500/15 text-emerald-300 border border-emerald-500/20 px-3 py-1 rounded-full text-sm font-semibold">
              Puntuación: {job.score}
            </span>
            <select
              value={job.status}
              onChange={(e) => void handleMove(e.target.value as JobStatus)}
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

        <div className="flex gap-4 mb-8 border-b border-dark-700 pb-6">
          <button className="flex items-center gap-2 bg-white text-black px-4 py-2 rounded font-medium hover:bg-gray-200 transition">
            <FileText size={18} /> Generar CV Adaptado
          </button>
          {job.url && (
            <a href={job.url} target="_blank" rel="noreferrer" className="flex items-center gap-2 bg-dark-700 text-white px-4 py-2 rounded font-medium hover:bg-dark-600 transition">
              Ver original <ExternalLink size={16} />
            </a>
          )}
        </div>

        <div>
          <h3 className="text-lg font-semibold mb-4 border-b border-dark-700 pb-2">Descripción del puesto</h3>
          <div className="text-gray-300 text-sm leading-relaxed whitespace-pre-wrap">
            {job.description || "No hay descripción detallada disponible en la base de datos."}
          </div>
        </div>
      </div>
    </div>
  );
}