create table if not exists gmail_connections (
    id text primary key,
    email text not null default '',
    folder text not null default 'JobAlerts',
    encrypted_credentials text,
    enabled boolean not null default false,
    connected_at timestamptz
);
create table if not exists gmail_oauth_states (
    state_hash text primary key,
    encrypted_verifier text not null,
    frontend_origin text not null,
    folder text not null,
    expires_at bigint not null
);

alter table public.gmail_connections enable row level security;
alter table public.gmail_oauth_states enable row level security;
