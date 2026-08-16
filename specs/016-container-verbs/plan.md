---
title: "The Vault on Container Verbs"
spec: "./spec.md"
status: ready
---
# Plan
All repoint concentrates in `hades_client.py` (the seam the engines
inject): write_document → dissolve_note translation + result
normalization; read_document / read_document_by_source_path →
materialize_note + `_note_to_document_row` projection. `server.py`'s
de-tooled waves browse answers the enumeration gap honestly. plan_
freshness / plan_reconcile are UNTOUCHED — the master plan's RR named
them, but the engines take injected callables and never knew the verb
(divergence noted; the seam was the right cut).
Decisions: one-block dissolve [Rob's de-inference rule]; non-destructive
reconcile is STRUCTURAL (calliope's verb cannot delete; the vault delete
stays lifecycle_verbs') — the MP's "split or parameterize" gap dissolves
[Default]; miss = empty list [carried contract]; enumeration gap [OPEN →
Rob: needs a registered graph shape]. Cross-star: calliope#142
(schema_type/file_path cross the verb — the sink accepted both since F9;
only the zod schema omitted them).
RR verified: hades_client.py:46-47/174/206/223 [MP] · server.py:1496/
1864/2619/2735 [MP] · calliope server.ts dissolve_note [RR addition,
surfaced].
