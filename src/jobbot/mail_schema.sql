-- No se guardan cuerpos de correo, asuntos ni credenciales.
create table if not exists email_imports (
    mailbox_key text not null,
    uid_validity text not null,
    uid text not null,
    message_hash text not null,
    status text not null,
    job_count integer not null default 0,
    processed_at timestamptz not null,
    parser_version integer not null default 0,
    primary key (mailbox_key, uid_validity, uid)
);
create index if not exists idx_email_imports_hash on email_imports(mailbox_key, message_hash);
