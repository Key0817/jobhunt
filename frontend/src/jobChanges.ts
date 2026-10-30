import { useSyncExternalStore } from 'react';
import type { Job, JobStatus } from './types';
import { onCacheReset, cacheGeneration } from './viewCache';

export type JobChange = { before: Job; after: Job; phase: 'optimistic' | 'commit' | 'rollback' };
const jobs = new Map<string, Job>();
const pending = new Set<string>();
const listeners = new Set<(change: JobChange) => void>();
const pendingListeners = new Set<() => void>();
export function rememberJobs(values: Job[]) {
  values.forEach(job => { if (!pending.has(job.id)) jobs.set(job.id, job); });
  while (jobs.size > 2000) jobs.delete(jobs.keys().next().value!);
}
export function subscribeJobChanges(listener: (change: JobChange) => void) { listeners.add(listener); return () => { listeners.delete(listener); }; }
function emit(change: JobChange) { listeners.forEach(listener => listener(change)); }
export function isJobPending(id: string) { return pending.has(id); }
export function anyJobsPending() { return pending.size > 0; }
export function subscribePendingJobs(listener: () => void) { pendingListeners.add(listener); return () => { pendingListeners.delete(listener); }; }
export function useJobPending(id: string) {
  return useSyncExternalStore(listener => { pendingListeners.add(listener); return () => { pendingListeners.delete(listener); }; }, () => pending.has(id));
}
onCacheReset(() => { jobs.clear(); pending.clear(); pendingListeners.forEach(listener => listener()); });
export function beginJobChange(id: string, status: JobStatus) {
  const generation = cacheGeneration();
  const before = jobs.get(id);
  pending.add(id); pendingListeners.forEach(listener => listener());
  const after = before ? { ...before, status } : undefined;
  if (before && after) { jobs.set(id, after); emit({ before, after, phase: 'optimistic' }); }
  return {
    before,
    finish(success: boolean) {
      if (generation !== cacheGeneration()) return;
      pending.delete(id);
      if (before && after) {
        if (!success) jobs.set(id, before);
        emit(success ? { before, after, phase: 'commit' } : { before: after, after: before, phase: 'rollback' });
      }
      pendingListeners.forEach(listener => listener());
    },
  };
}
