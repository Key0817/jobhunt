-- Idempotente; conserva las versiones y cartas existentes.
alter table public.cv_versions add column if not exists cover_letter text;
alter table public.cv_versions add column if not exists cover_letter_model_name text;
