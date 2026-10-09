-- Embeddings keyed by the exact text that produced them, saved batch by batch,
-- so an interrupted load never loses finished work and re-runs only embed new text.
create table if not exists embedding_cache (
    content_hash  text not null,
    model         text not null,
    embedding     vector(1536) not null,
    created_at    timestamptz not null default now(),
    primary key (content_hash, model)
);
