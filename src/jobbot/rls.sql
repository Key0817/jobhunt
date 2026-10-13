-- Solo PostgreSQL / Supabase. Seguridad: Supabase expone por defecto las tablas del esquema
-- "public" mediante su API REST con la clave "anon". Activar RLS SIN políticas la bloquea
-- por completo. Nuestro backend se conecta con el usuario postgres (cadena de conexión),
-- que no pasa por la API REST y no se ve afectado.
alter table jobs            enable row level security;
alter table status_history  enable row level security;
alter table cv_versions     enable row level security;
alter table runs            enable row level security;