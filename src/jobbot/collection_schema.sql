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
