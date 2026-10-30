alter table public.email_imports add column if not exists parser_version integer not null default 0;
