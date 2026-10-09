-- Report IDs (RPT068 for MISCRPT068.CSV / .HTM) become entities, so a question naming a report
-- finds the documentation sections that describe it through the exact-ID channel.
alter table entities drop constraint if exists entities_type_check;
alter table entities add constraint entities_type_check
    check (type in ('error_code', 'abend', 'reason', 'file', 'job', 'program', 'dataset', 'application', 'report'));
