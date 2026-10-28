export default function Pagination({ page, totalPages, total, pageSize, loading, onPageChange, label = 'ofertas' }: {
  page: number; totalPages: number; total: number; pageSize: number; loading: boolean;
  onPageChange: (page: number) => void; label?: string;
}) {
  const first = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const button = 'rounded-lg border border-dark-700 px-3 py-2 text-xs hover:bg-dark-800 disabled:opacity-40 disabled:cursor-not-allowed';
  return <nav aria-label={`Paginación de ${label}`} className="my-4 flex flex-wrap items-center justify-between gap-3">
    <p aria-live="polite" className="text-xs text-gray-400">{first}–{Math.min(page * pageSize, total)} de {total}</p>
    <div className="flex items-center gap-2">
      <button type="button" aria-label={`Página anterior de ${label}`} className={button} disabled={loading || page <= 1} onClick={() => onPageChange(page - 1)}>Anterior</button>
      <span className="text-xs text-gray-400">{page} / {totalPages}</span>
      <button type="button" aria-label={`Página siguiente de ${label}`} className={button} disabled={loading || page >= totalPages} onClick={() => onPageChange(page + 1)}>Siguiente</button>
    </div>
  </nav>;
}
