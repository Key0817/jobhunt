import { API_URL, apiFetch } from './api';
import { useCallback, useEffect, useRef, useState } from 'react';
import { rememberJobs, subscribeJobChanges, anyJobsPending, subscribePendingJobs } from './jobChanges';
import { getCached, setCached, cacheGeneration } from './viewCache';
import { updateJobPage, matchesPageFilters } from './jobPageState';
import type { JobPage } from './jobPageState';

export function useJobPage(query: string, refreshToken = 0) {
  const cacheKey = 'jobs:' + query;
  const [data, setData] = useState<JobPage>(() => getCached<JobPage>(cacheKey) || { items: [], total: 0, page: 1, page_size: 25, total_pages: 1 });
  const [loading, setLoading] = useState(!getCached(cacheKey));
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const request = useRef<AbortController | null>(null);
  const needsRefresh = useRef(false);
  const refresh = useCallback(async () => { setRevision(value => value + 1); }, []);
  useEffect(() => {
    const params = new URLSearchParams(query);
    const unsubscribe = subscribeJobChanges(change => {
      if (!matchesPageFilters(change.after, params)) return;
      const status = params.get('status');
      if (status && status !== change.before.status && status !== change.after.status) return;
      if (change.phase !== 'commit') {
        request.current?.abort();
        setLoading(false); setRefreshing(false);
        setData(current => {
          const next = updateJobPage(current, status, change.before, change.after, params.get('sort') || 'score');
          setCached(cacheKey, next);
          return next;
        });
      }
      if (change.phase === 'rollback' || (change.phase === 'commit' && status)) {
        needsRefresh.current = true;
        if (!anyJobsPending()) { needsRefresh.current = false; void refresh(); }
      }
    });
    const pending = subscribePendingJobs(() => {
      if (needsRefresh.current && !anyJobsPending()) { needsRefresh.current = false; void refresh(); }
    });
    return () => { unsubscribe(); pending(); };
  }, [query, cacheKey, refresh]);
  useEffect(() => {
    const controller = new AbortController();
    request.current = controller;
    const generation = cacheGeneration();
    const cached = getCached<JobPage>(cacheKey);
    if (cached) setData(cached);
    async function load() {
      setLoading(!cached);
      setRefreshing(true); setError('');
      try {
        const response = await apiFetch(`${API_URL}/api/jobs/page?${query}`, { signal: controller.signal });
        if (!response.ok) throw new Error('No se pudieron cargar las ofertas.');
        const result: JobPage = await response.json();
        if (!controller.signal.aborted && generation === cacheGeneration()) {
          rememberJobs(result.items); setCached(cacheKey, result); setData(result);
        }
      } catch (failure) {
        if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : 'Error al consultar ofertas.');
      } finally {
        if (!controller.signal.aborted) { setLoading(false); setRefreshing(false); }
      }
    }
    void load();
    return () => controller.abort();
  }, [query, cacheKey, revision, refreshToken]);
  return { data, loading, refreshing, error, refresh };
}
