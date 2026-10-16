-- Esquema portable: corre igual en PostgreSQL (Supabase) y en SQLite (desarrollo y tests).
-- Los ids y las fechas se generan en Python, por eso no hay defaults propios de Postgres.

create table if not exists jobs (
    id           text primary key,
    dedupe_key   text not null unique,
    source       text not null,
    external_id  text,
    title        text not null,
    company      text not null default '',
    country      text,
    location     text,
    remote       boolean,
    url          text not null,
    description  text not null default '',
    posted_at    timestamptz,
    detected_at  timestamptz not null,
    score        real not null default 0,
    score_detail text not null default '{}',
    status       text not null default 'encontrada'
        check (status in ('encontrada', 'aplicable', 'cv_listo', 'solicitada',
                          'entrevista', 'oferta', 'rechazada', 'descartada')),
    applied_at   timestamptz,
    notes        text not null default '',
    raw          text not null default '{}'
);
create index if not exists idx_jobs_status   on jobs (status);
create index if not exists idx_jobs_detected on jobs (detected_at);

create table if not exists status_history (
    id           text primary key,
    job_id       text not null references jobs (id) on delete cascade,
    from_status  text,
    to_status    text not null,
    changed_at   timestamptz not null
);
create index if not exists idx_history_job on status_history (job_id);

create table if not exists cv_versions (
    id            text primary key,
    job_id        text not null references jobs (id) on delete cascade,
    variant       text not null default '',
    profile_text  text not null,
    approved      boolean not null default false,
    created_at    timestamptz not null
);
create index if not exists idx_cv_job on cv_versions (job_id);

create table if not exists runs (
    id           text primary key,
    source       text not null,
    started_at   timestamptz not null,
    finished_at  timestamptz,
    found        integer not null default 0,
    inserted     integer not null default 0,
    error        text
);