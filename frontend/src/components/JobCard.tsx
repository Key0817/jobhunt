import { useJobPending } from '../jobChanges';
import { ExternalLink, Mail } from 'lucide-react';
import { STATUSES, STATUS_LABELS } from '../types';
import type { Job, JobStatus } from '../types';

export default function JobCard({ job, onMove, onViewDetail }: {
  job: Job; onMove: (id: string, status: JobStatus) => void; onViewDetail: (id: string) => void;
}) {
  const pending = useJobPending(job.id);
  const color = job.score >= 70 ? 'bg-emerald-500/15 text-emerald-300' : job.score >= 40 ? 'bg-amber-500/15 text-amber-300' : 'bg-gray-500/15 text-gray-400';
  const emailOrigin = job.origins?.find(origin => origin.source.endsWith('_email'));
  return (
    <article className="flex h-full flex-col justify-between rounded-lg border border-dark-700 bg-dark-800 p-4 transition hover:border-gray-500">
      <div>
        <div className="flex items-start justify-between gap-2">
          <button type="button" onClick={() => onViewDetail(job.id)} className="text-left text-sm font-semibold leading-6 hover:text-blue-400 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400">
            {job.title}
          </button>
          <span className={`${color} shrink-0 rounded-full px-2 py-0.5 text-xs font-semibold`}>{Math.round(job.score)}</span>
        </div>
        <p className="mt-2 text-xs text-gray-400">{job.company || 'Empresa no indicada en el origen'}</p>
        <p className="mt-2 flex items-center gap-1 text-xs text-blue-300">{emailOrigin && <Mail size={12} />}{emailOrigin?.label || job.source_display || job.source}</p>
        {job.description_incomplete && <p className="mt-1 text-xs text-amber-300">Resumen de alerta · ver oferta completa</p>}
        <p className="mt-2 text-xs text-gray-400">{job.location_needs_review ? 'Ubicación por verificar' : job.location || job.country || 'Ubicación no indicada'}</p>
        {job.detected_at && <p className="mt-1 text-xs text-gray-500">Añadida: {new Date(job.detected_at).toLocaleDateString('es-CR')}</p>}
      </div>
      <div className="mt-4 space-y-3 border-t border-dark-700 pt-3">
        <div className="flex flex-wrap items-center gap-3 text-xs">
          <button type="button" onClick={() => onViewDetail(job.id)} className="font-medium text-blue-300 hover:underline">Ver detalles</button>
          {/^https?:\/\//i.test(job.url) && <a href={job.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-gray-300 hover:text-white">Abrir oferta<ExternalLink size={12} /></a>}
        </div>
        <select disabled={pending} aria-busy={pending} value={job.status} onChange={event => onMove(job.id, event.target.value as JobStatus)} className="status-select w-full" aria-label={`Estado de ${job.title}`}>
          {STATUSES.map(status => <option key={status} value={status}>{STATUS_LABELS[status]}</option>)}
        </select>
      </div>
    </article>
  );
}
