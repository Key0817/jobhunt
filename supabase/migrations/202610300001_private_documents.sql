-- Ejecutar en Supabase SQL Editor. Sin políticas de lectura pública:
-- el backend accede usando service_role y autoriza la descarga con APP_OWNER_ID.
insert into storage.buckets (id, name, public, file_size_limit)
values ('jobhunt-private', 'jobhunt-private', false, 20000000)
on conflict (id) do update set public = false, file_size_limit = 20000000;
