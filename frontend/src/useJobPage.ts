import { useCallback, useEffect, useState } from 'react';
import type { Job } from './types';

interface JobPage { items: Job[]; total: number; page: number; page_size: number; total_pages: number }

export function useJobPage(query: string, refreshToken = 0) {
  const [data, setData] = useState<JobPage>({ items: [], total: 0, page: 1, page_size: 25, total_pages: 1 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(async () => { setRevision(value => value + 1); }, []);
  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      setLoading(true);
      setError('');
      try {
        const response = await fetch(`http://localhost:8000/api/jobs/page?${query}`, { signal: controller.signal });
        if (!response.ok) throw new Error('No se pudieron cargar las ofertas.');
        const result: JobPage = await response.json();
        if (!controller.signal.aborted) setData(result);
      } catch (failure) {
        if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : 'Error al consultar ofertas.');
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    void load();
    return () => controller.abort();
  }, [query, revision, refreshToken]);
  return { data, loading, error, refresh };
}
