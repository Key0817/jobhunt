import type { Job } from './types';

export interface JobPage { items: Job[]; total: number; page: number; page_size: number; total_pages: number }
export function matchesPageFilters(job: Job, params: URLSearchParams): boolean {
  const source = params.get('source');
  const origins = [job.source, ...(job.origins || []).map(origin => origin.source)];
  if (source && !origins.some(value => value === source || value.startsWith(source + ':') || (source === 'email' && value.endsWith('_email')))) return false;
  if (job.score < Number(params.get('min_score') || 0)) return false;
  const modality = params.get('modality');
  if (modality === 'remote' && job.remote !== true) return false;
  if (modality === 'onsite' && job.remote !== false) return false;
  if (modality === 'unknown' && job.location && !job.location_needs_review) return false;
  const days = Number(params.get('days'));
  return !days || Boolean(job.detected_at && Date.now() - new Date(job.detected_at).getTime() <= days * 86400000);
}
export function updateJobPage(data: JobPage, status: string | null, before: Job, after: Job, sort = 'score'): JobPage {
  const was = !status || before.status === status;
  const now = !status || after.status === status;
  if (!was && !now) return data;
  const total = Math.max(0, data.total + Number(now) - Number(was));
  let items = data.items.filter(job => job.id !== before.id);
  if (was && now) items = data.items.map(job => job.id === before.id ? { ...job, status: after.status } : job);
  else if (now && data.page === 1) {
    items = [...items, after].sort((a, b) => (sort === 'score' ? b.score - a.score : 0)
      || (b.detected_at || '').localeCompare(a.detected_at || '') || b.id.localeCompare(a.id)).slice(0, data.page_size);
  }
  // Keep the current page until its background response clamps/refills it.
  return { ...data, items, total, total_pages: Math.max(data.page, Math.ceil(total / data.page_size), 1) };
}
