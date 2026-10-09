-- doc_chunks keeps the exact text that was embedded and BM25-indexed, like the other searchable tables.
alter table doc_chunks add column if not exists search_text text not null default '';
alter table doc_chunks alter column search_text drop default;
