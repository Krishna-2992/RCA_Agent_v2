-- RCA Agent v2 - initial schema (Supabase Postgres + pgvector).
-- Run once in the Supabase SQL editor, or with psql against SUPABASE_DB_URL.
-- Every searchable row carries a dense embedding (vector, 1536-d from
-- text-embedding-3-small) and BM25 weights (sparsevec, computed in Python).

create extension if not exists vector;

-- What was loaded, from where, and its content hash (re-loads skip unchanged sources).
create table if not exists sources (
    id            text primary key,              -- e.g. 'samples:INC', 'doc:TechnicalDocuments/03RMNLMS-cbl-0.4.4.xml'
    kind          text not null,
    path          text not null,
    content_hash  text not null,
    loaded_at     timestamptz not null default now()
);

-- Duplicate tickets merged into one occurrence (parent/child, same JCL + day).
create table if not exists events (
    id            text primary key,
    kind          text not null check (kind in ('parent_child', 'alert_same_day', 'single')),
    ticket_count  int  not null default 1,
    first_seen    timestamptz,
    last_seen     timestamptz
);

create table if not exists incidents (
    number            text primary key,
    event_id          text not null references events (id),
    parent_number     text,
    priority          text,
    service           text,
    channel           text,
    opened_at         timestamptz,
    resolved_at       timestamptz,
    short_description text not null,
    description       text,
    root_cause        text,
    action_taken      text,
    resolution_notes  text,
    resolved_by       text,
    search_text       text not null,
    embedding         vector(1536),
    bm25              sparsevec,
    content_hash      text not null
);

create table if not exists service_requests (
    number            text primary key,
    opened_at         timestamptz,
    closed_at         timestamptz,
    service           text,
    short_description text not null,
    description       text,
    work_notes        text,
    search_text       text not null,
    embedding         vector(1536),
    bm25              sparsevec,
    content_hash      text not null
);

create table if not exists changes (
    number            text primary key,
    created_at        timestamptz,
    closed_at         timestamptz,
    short_description text not null,
    change_type       text,
    risk              text,
    category          text,
    programs          text[] not null default '{}'
);

create table if not exists pattern_cards (
    id            text primary key check (id ~ '^PC-[0-9]{2}$'),
    version       int  not null,
    status        text not null check (status in ('draft', 'approved')),
    title         text not null,
    system        text not null,
    body          jsonb not null,                -- the full validated card
    search_text   text not null,
    embedding     vector(1536),
    bm25          sparsevec,
    content_hash  text not null,
    updated_at    timestamptz not null default now()
);

create table if not exists card_evidence (
    card_id    text not null references pattern_cards (id) on delete cascade,
    item_type  text not null check (item_type in ('incident', 'service_request', 'change', 'problem', 'doc')),
    item_id    text not null,
    primary key (card_id, item_type, item_id)
);

create table if not exists doc_chunks (
    id            text primary key,
    source_file   text not null,
    program       text,                          -- F8RH id
    application   text,
    section_path  text not null,
    doc_type      text not null,                 -- technical_xml | program_summary | business_rules | catalogue | overview
    trust_tier    smallint not null check (trust_tier between 1 and 5),
    role          text not null check (role in ('mechanism', 'procedure', 'background')),
    text          text not null,
    embedding     vector(1536),
    bm25          sparsevec,
    content_hash  text not null
);

-- Every error code, abend, file, job, program and dataset, plus the spellings seen.
create table if not exists entities (
    id           bigserial primary key,
    type         text not null check (type in ('error_code', 'abend', 'reason', 'file', 'job', 'program', 'dataset', 'application')),
    canonical    text not null,
    description  text,
    source       text,
    unique (type, canonical)
);

create table if not exists entity_aliases (
    alias      text   not null,
    entity_id  bigint not null references entities (id) on delete cascade,
    primary key (alias, entity_id)
);

-- Which rows mention which IDs (drives the exact-ID search channel and agent tools).
create table if not exists entity_links (
    entity_id  bigint not null references entities (id) on delete cascade,
    item_type  text   not null check (item_type in ('incident', 'service_request', 'pattern_card', 'doc_chunk')),
    item_id    text   not null,
    primary key (entity_id, item_type, item_id)
);
create index if not exists entity_links_item_idx on entity_links (item_type, item_id);

-- BM25 vocabulary: token -> sparse dimension index, with document frequency.
create table if not exists bm25_vocab (
    token  text primary key,
    idx    int  not null unique,
    df     int  not null
);

-- Online: each query, its answer, and the engineer's verdict.
create table if not exists runs (
    id                uuid primary key default gen_random_uuid(),
    created_at        timestamptz not null default now(),
    query             text not null,
    result            jsonb,
    pattern_id        text,
    confidence        real,
    confidence_level  text check (confidence_level in ('high', 'medium', 'low')),
    trace_url         text,
    latency_ms        int,
    app_version       text
);

create table if not exists feedback (
    id          bigserial primary key,
    run_id      uuid not null references runs (id) on delete cascade,
    verdict     text not null check (verdict in ('correct', 'partly', 'wrong')),
    actual_fix  text,
    comment     text,
    created_at  timestamptz not null default now()
);

-- Dense search indexes. BM25 uses an exact scan: at a few thousand rows it is fast.
create index if not exists incidents_embedding_idx         on incidents         using hnsw (embedding vector_cosine_ops);
create index if not exists service_requests_embedding_idx  on service_requests  using hnsw (embedding vector_cosine_ops);
create index if not exists pattern_cards_embedding_idx     on pattern_cards     using hnsw (embedding vector_cosine_ops);
create index if not exists doc_chunks_embedding_idx        on doc_chunks        using hnsw (embedding vector_cosine_ops);
