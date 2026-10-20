
import { useCallback, useEffect, useState } from 'react';
import { ArrowLeft, ExternalLink, FileText, RefreshCw } from 'lucide-react';
import { STATUSES, STATUS_LABELS } from './types';
import type { Job, JobStatus } from './types';

const API_URL = 'http://localhost:8000';

interface JobDetailPageProps {
  jobId: string;
  onBack: () => void;
  onMove: (id: string, status: JobStatus) => Promise<boolean>;
}

interface CVVersion {
  id: string;
  job_id: string;
  variant: string;
  profile_key: string;
  status: string;
  approved: boolean;
  model_name: string | null;
  created_at: string;
}

interface CVGenerationResult {
  job_id: string;
  version_id: string;
  profile_key: string;
  status: string;
  file_path: string;
}

export default function JobDetailPage({jobId,onBack,onMove,}: JobDetailPageProps) {
  const [job, setJob] = useState<Job | null>(null);
  const [loading, setLoading] = useState(true);
  const [generatingCV, setGeneratingCV] = useState(false);
  const [loadingVersions, setLoadingVersions] = useState(false);
  const [versions, setVersions] = useState<CVVersion[]>([]);
  const [cvMessage, setCvMessage] = useState('');
  const [cvError, setCvError] = useState('');

  const fetchVersions = useCallback(async () => {
    setLoadingVersions(true);

    try {
      const response = await fetch(
        `${API_URL}/api/jobs/${jobId}/cv`,
      );

      if (!response.ok) {
        throw new Error(`Error al consultar versiones: ${response.status}`);
      }

      const data = (await response.json()) as CVVersion[];
      setVersions(data);
    } catch (error) {
      console.error('Error cargando versiones de CV:', error);
      setCvError('No se pudo cargar el historial de CV.');
    } finally {
      setLoadingVersions(false);
    }
  }, [jobId]);

  useEffect(() => {
    let cancelled = false;

    const fetchDetail = async () => {
      setLoading(true);

      try {
        const response = await fetch(`${API_URL}/api/jobs/${jobId}`);

        if (!response.ok) {
          throw new Error(`Error al cargar la oferta: ${response.status}`);
        }

        const data = (await response.json()) as Job;

        if (!cancelled) {
          setJob(data);
        }
      } catch (error) {
        console.error('Error cargando detalle:', error);

        if (!cancelled) {
          setJob(null);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    setVersions([]);
    setCvMessage('');
    setCvError('');
    void fetchDetail();
    void fetchVersions();

    return () => {
      cancelled = true;
    };
  }, [jobId, fetchVersions]);

  const handleMove = async (status: JobStatus) => {
    if (!job) return;

    if (await onMove(job.id, status)) {
      setJob((current) =>
        current ? { ...current, status } : current,
      );
    }
  };

  const handleGenerateCV = async () => {
    if (!job || generatingCV) return;

    setGeneratingCV(true);
    setCvMessage('');
    setCvError('');

    try {
      const response = await fetch(
        `${API_URL}/api/jobs/${job.id}/cv`,
        { method: 'POST' },
      );

      const data = (await response.json()) as
        | CVGenerationResult
        | { detail?: string };

      if (!response.ok) {
        const detail =
          'detail' in data && data.detail
            ? data.detail
            : `Error HTTP ${response.status}`;

        throw new Error(detail);
      }

      const result = data as CVGenerationResult;

      setCvMessage(
        `CV generado correctamente. Perfil: ${result.profile_key}. Estado: ${result.status}.`,
      );

      await fetchVersions();
    } catch (error) {
      console.error('Error generando CV:', error);
      setCvError(
        error instanceof Error
          ? error.message
          : 'No se pudo generar el CV.',
      );
    } finally {
      setGeneratingCV(false);
    }
  };

  if (loading) {
    return <div className="text-gray-400">Cargando detalles...</div>;
  }

  if (!job) {
    return (
      <div className="text-red-400">
        Error: No se encontró la oferta.
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto">
      <button
        onClick={onBack}
        className="flex items-center gap-2 text-gray-400 hover:text-white mb-6 transition"
      >
        <ArrowLeft size={16} /> Volver
      </button>

      <div className="bg-dark-800 border border-dark-700 rounded-xl p-8">
        <div className="flex justify-between items-start mb-6 gap-4">
          <div>
            <h1 className="text-3xl font-bold mb-2">{job.title}</h1>
            <div className="flex items-center gap-4 text-gray-400 text-sm flex-wrap">
              <span className="font-semibold text-white">
                {job.company || 'Empresa no indicada'}
              </span>
              <span>{job.location || job.country}</span>
              {job.remote && (
                <span className="bg-dark-700 px-2 py-0.5 rounded">
                  Remoto
                </span>
              )}
            </div>
          </div>

          <div className="flex flex-col gap-2 items-end">
            <span className="bg-emerald-500/15 text-emerald-300 border border-emerald-500/20 px-3 py-1 rounded-full text-sm font-semibold">
              Puntuación: {job.score}
            </span>

            <select
              value={job.status}
              onChange={(event) =>
                void handleMove(event.target.value as JobStatus)
              }
              className="status-select"
              aria-label="Mover a otro estado"
            >
              {STATUSES.map((status) => (
                <option key={status} value={status}>
                  {STATUS_LABELS[status]}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="flex gap-4 mb-8 border-b border-dark-700 pb-6 flex-wrap">
          <button
            onClick={() => void handleGenerateCV()}
            disabled={generatingCV}
            className="flex items-center gap-2 bg-white text-black px-4 py-2 rounded font-medium hover:bg-gray-200 disabled:opacity-50 disabled:cursor-not-allowed transition"
          >
            {generatingCV ? (
              <RefreshCw size={18} className="animate-spin" />
            ) : (
              <FileText size={18} />
            )}
            {generatingCV ? 'Generando CV...' : 'Generar CV Adaptado'}
          </button>

          {job.url && (
            <a
              href={job.url}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-2 bg-dark-700 text-white px-4 py-2 rounded font-medium hover:bg-dark-600 transition"
            >
              Ver original <ExternalLink size={16} />
            </a>
          )}
        </div>

        {cvMessage && (
          <div
            role="status"
            className="mb-5 rounded-lg border border-emerald-500/30 bg-emerald-500/10 text-emerald-300 px-4 py-3 text-sm"
          >
            {cvMessage}
          </div>
        )}

        {cvError && (
          <div
            role="alert"
            className="mb-5 rounded-lg border border-red-500/30 bg-red-500/10 text-red-300 px-4 py-3 text-sm"
          >
            {cvError}
          </div>
        )}

        <section className="mb-8">
          <div className="flex justify-between items-center gap-3 mb-4 border-b border-dark-700 pb-2">
            <h3 className="text-lg font-semibold">
              Historial de CV adaptados
            </h3>

            <button
              onClick={() => void fetchVersions()}
              disabled={loadingVersions}
              className="text-sm text-gray-400 hover:text-white disabled:opacity-50"
            >
              {loadingVersions ? 'Actualizando...' : 'Actualizar'}
            </button>
          </div>

          {loadingVersions && versions.length === 0 ? (
            <p className="text-sm text-gray-400">
              Cargando versiones...
            </p>
          ) : versions.length === 0 ? (
            <p className="text-sm text-gray-400">
              Todavía no se han generado CV para esta oferta.
            </p>
          ) : (
            <div className="space-y-3">
              {versions.map((version) => (
                <div
                  key={version.id}
                  className="border border-dark-700 rounded-lg p-4 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3"
                >
                  <div>
                    <div className="font-medium">
                      Perfil: {version.profile_key || version.variant}
                    </div>
                    <div className="text-xs text-gray-400 mt-1">
                      {new Date(version.created_at).toLocaleString()}
                    </div>
                    <div className="text-xs text-gray-500 mt-1 break-all">
                      ID: {version.id}
                    </div>
                  </div>

                  <span
                    className={`text-xs px-3 py-1 rounded-full self-start sm:self-auto ${
                      version.approved
                        ? 'bg-emerald-500/15 text-emerald-300'
                        : 'bg-amber-500/15 text-amber-300'
                    }`}
                  >
                    {version.approved
                      ? 'Aprobado'
                      : version.status === 'pending_review'
                        ? 'Pendiente de revisión'
                        : version.status}
                  </span>
                </div>
              ))}
            </div>
          )}
        </section>

        <section>
          <h3 className="text-lg font-semibold mb-4 border-b border-dark-700 pb-2">
            Descripción del puesto
          </h3>
          <div className="text-gray-300 text-sm leading-relaxed whitespace-pre-wrap">
            {job.description ||
              'No hay descripción detallada disponible en la base de datos.'}
          </div>
        </section>
      </div>
    </div>
  );
}
