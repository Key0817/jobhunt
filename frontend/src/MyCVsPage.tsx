import { API_URL, apiFetch } from './api';
import { useCallback, useEffect, useState } from 'react';
import {
  FileText,
  RefreshCw,
  Eye,
  AlertCircle,
} from 'lucide-react';



export interface CVVersion {
  id: string;
  job_id: string;
  job_title?: string;
  company?: string;
  variant: string;
  profile_key: string;
  status: string;
  approved: boolean;
  model_name?: string | null;
  created_at: string;
}

interface MyCVsPageProps {
  onViewCV: (versionId: string) => void;
}

const STATUS_LABELS: Record<string, string> = {
  pending_review: 'Pendiente de revisión',
  approved: 'Aprobado',
  rejected: 'Rechazado',
  archived: 'Archivado',
};

const STATUS_STYLES: Record<string, string> = {
  pending_review: 'bg-amber-500/10 text-amber-300 border-amber-500/20',
  approved: 'bg-emerald-500/10 text-emerald-300 border-emerald-500/20',
  rejected: 'bg-red-500/10 text-red-300 border-red-500/20',
  archived: 'bg-gray-500/10 text-gray-300 border-gray-500/20',
};

const FILTER_OPTIONS = [
  { value: 'all', label: 'Todos los estados' },
  { value: 'pending_review', label: 'Pendientes de revisión' },
  { value: 'approved', label: 'Aprobados' },
  { value: 'rejected', label: 'Rechazados' },
  { value: 'archived', label: 'Archivados' },
];

export default function MyCVsPage({ onViewCV }: MyCVsPageProps) {
  const [versions, setVersions] = useState<CVVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filterStatus, setFilterStatus] = useState('all');

  const loadCVs = useCallback(async () => {
    setLoading(true);

    try {
      const response = await apiFetch(`${API_URL}/api/cvs`);

      if (!response.ok) {
        throw new Error(
          response.status === 404
            ? 'El endpoint GET /api/cvs todavía no está implementado.'
            : `La API respondió ${response.status}.`,
        );
      }

      const data: CVVersion[] = await response.json();
      setVersions(data);
      setError(null);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : 'No se pudieron cargar los currículums.',
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadCVs();
  }, [loadCVs]);

  const filteredVersions = versions.filter(
    (cv) => filterStatus === 'all' || cv.status === filterStatus,
  );

  const formatDate = (value: string) => {
    const date = new Date(value);

    return Number.isNaN(date.getTime())
      ? 'Fecha no disponible'
      : date.toLocaleDateString('es-CR', {
          year: 'numeric',
          month: 'short',
          day: 'numeric',
        });
  };

  return (
    <section>
      <div className="flex flex-wrap items-start justify-between gap-4 mb-6">
        <div>
          <h2 className="text-2xl font-bold">Mi Currículum</h2>
          <p className="text-sm text-gray-400 mt-1">
            Gestioná, revisá y prepará tus CV para cada oferta.
          </p>
        </div>

        <button
          onClick={() => void loadCVs()}
          disabled={loading}
          className="flex items-center gap-2 rounded-lg border border-dark-700 px-3 py-2 text-sm hover:bg-dark-800 disabled:opacity-50"
        >
          <RefreshCw size={16} />
          Actualizar
        </button>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
        <div className="bg-dark-800 border border-dark-700 rounded-xl p-4">
          <p className="text-sm text-gray-400">Total de versiones</p>
          <p className="text-2xl font-bold mt-1">{versions.length}</p>
        </div>

        <div className="bg-dark-800 border border-dark-700 rounded-xl p-4">
          <p className="text-sm text-gray-400">Pendientes de revisión</p>
          <p className="text-2xl font-bold mt-1">
            {versions.filter((cv) => cv.status === 'pending_review').length}
          </p>
        </div>

        <div className="bg-dark-800 border border-dark-700 rounded-xl p-4">
          <p className="text-sm text-gray-400">Aprobados</p>
          <p className="text-2xl font-bold mt-1">
            {versions.filter((cv) => cv.status === 'approved').length}
          </p>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <h3 className="font-semibold">Versiones generadas</h3>

        <select
          value={filterStatus}
          onChange={(event) => setFilterStatus(event.target.value)}
          className="status-select min-w-48"
          aria-label="Filtrar currículums por estado"
        >
          {FILTER_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </div>

      {error && (
        <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-4 text-sm text-amber-200 mb-4">
          <AlertCircle size={18} className="shrink-0 mt-0.5" />
          <div>
            <p>{error}</p>
            <p className="mt-1 text-amber-100/70">
              La pantalla está preparada; primero debemos conectar el endpoint
              del backend.
            </p>
          </div>
        </div>
      )}

      {loading ? (
        <p className="text-sm text-gray-400 py-8">
          Cargando currículums…
        </p>
      ) : filteredVersions.length === 0 ? (
        <div className="border border-dashed border-dark-700 rounded-xl py-14 px-4 text-center">
          <FileText size={32} className="mx-auto mb-3 text-gray-500" />
          <p className="font-semibold">No hay currículums para mostrar</p>
          <p className="text-sm text-gray-400 mt-1">
            Generá un CV desde el detalle de una oferta y aparecerá aquí.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          {filteredVersions.map((cv) => (
            <article
              key={cv.id}
              className="bg-dark-800 border border-dark-700 rounded-xl p-5"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex gap-3 min-w-0">
                  <div className="bg-dark-700 rounded-lg p-3 h-fit">
                    <FileText size={22} />
                  </div>

                  <div className="min-w-0">
                    <h4 className="font-semibold wrap-break-word">
                      {cv.job_title || `CV para oferta ${cv.job_id}`}
                    </h4>
                    <p className="text-sm text-gray-400 mt-1">
                      {cv.company || 'Empresa no indicada'}
                    </p>
                  </div>
                </div>

                <span
                  className={`text-xs border rounded-full px-2 py-1 whitespace-nowrap ${
                    STATUS_STYLES[cv.status] ||
                    'border-dark-700 text-gray-300'
                  }`}
                >
                  {STATUS_LABELS[cv.status] || cv.status}
                </span>
              </div>

              <div className="mt-4 text-sm text-gray-400 space-y-1">
                <p>
                  Perfil: <span className="text-gray-200">{cv.profile_key || cv.variant}</span>
                </p>
                <p>Creado: {formatDate(cv.created_at)}</p>
              </div>

              <div className="flex justify-end mt-4 pt-3 border-t border-dark-700">
                <button
                  onClick={() => onViewCV(cv.id)}
                  className="flex items-center gap-2 rounded-lg bg-white text-black px-3 py-2 text-sm font-semibold hover:bg-gray-200"
                >
                  <Eye size={16} />
                  Ver y editar
                </button>
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
