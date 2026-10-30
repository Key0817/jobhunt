import { useViewState } from '../viewCache';
import { STATUS_LABELS } from '../types';
import type { JobStatus } from '../types';
import { useJobPage } from '../useJobPage';
import JobCard from './JobCard';
import Pagination from './Pagination';

export default function DashboardColumn({ status, onMove, onViewDetail }: {
  status: JobStatus;
  onMove: (id: string, status: JobStatus) => Promise<boolean>; onViewDetail: (id: string) => void;
}) {
  const [page, setPage] = useViewState('dashboard:page:' + status, 1);
  const { data, loading, refreshing, error, refresh } = useJobPage(new URLSearchParams({ status, page: String(page), page_size: '25', sort: 'score' }).toString());
  function changePage(value: number) { if (value === page) void refresh(); else setPage(value); }
  return <section aria-label={STATUS_LABELS[status]} aria-busy={loading} className="flex-1 min-w-80">
    <div className="flex justify-between items-center bg-dark-800 border border-dark-700 rounded-full px-3 py-1 mb-4 text-xs font-semibold">
      <span>{STATUS_LABELS[status]}</span><span className="text-gray-400">{data.total}</span>
    </div>
    <Pagination page={data.page} totalPages={data.total_pages} total={data.total} pageSize={data.page_size} loading={loading || refreshing} onPageChange={changePage} label={STATUS_LABELS[status]} />
    {error ? <div role="alert" className="text-sm text-red-300">{error} <button type="button" onClick={() => void refresh()}>Reintentar</button></div>
      : loading ? <p role="status" className="text-sm text-gray-400">Cargando ofertas…</p>
      : <div className="flex flex-col">{data.items.map(job => <JobCard key={job.id} job={job} onMove={onMove} onViewDetail={onViewDetail} />)}
        {data.total === 0 && <p className="text-xs text-gray-500">No hay ofertas en este estado.</p>}</div>}
    {data.total_pages > 1 && <Pagination page={data.page} totalPages={data.total_pages} total={data.total} pageSize={data.page_size} loading={loading || refreshing} onPageChange={changePage} label={STATUS_LABELS[status]} />}
  </section>;
}
