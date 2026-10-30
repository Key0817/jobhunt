-- Expansión de recolección. No cambia estados de postulación ni CVs.
alter table public.jobs add column if not exists last_seen_at timestamptz;
alter table public.runs add column if not exists status text not null default 'unknown';
alter table public.runs add column if not exists details text not null default '{}';
-- Identidades y observaciones por fuente, separadas del estado de postulación.
create table if not exists job_sources (
    source text not null,
    source_key text not null,
    job_id text not null references jobs(id) on delete cascade,
    external_id text,
    url text not null,
    canonical_url text not null,
    first_seen_at timestamptz not null,
    last_seen_at timestamptz not null,
    primary key (source, source_key)
);
create index if not exists idx_job_sources_job on job_sources(job_id);
create index if not exists idx_job_sources_url on job_sources(canonical_url);

alter table public.job_sources enable row level security;
update public.jobs set last_seen_at = detected_at where last_seen_at is null;
insert into public.job_sources (source, source_key, job_id, external_id, url, canonical_url, first_seen_at, last_seen_at)
select source, case when external_id is not null and external_id <> '' then 'id:' || external_id else 'url:' || url end,
       id, external_id, url, url, detected_at, coalesce(last_seen_at, detected_at)
from public.jobs
on conflict (source, source_key) do nothing;
