---
status: accepted
---

# Group knowledge citations by document while preserving excerpt links

The sources panel will identify a Knowledge Source by `knowledge_base_id + document_id`, so multiple cited chunks from the same document appear once with an accumulated citation count. Each inline Citation Occurrence retains its own chunk-level link, preserving exact source navigation without presenting retrieval chunks as separate documents.

## Considered Options

- Grouping by citation URL would split one document whenever different chunks produce different URLs.
- Removing chunk identifiers would simplify grouping but lose precise navigation to the evidence used for a claim.

## Consequences

The citation parser must separate source identity from occurrence URL: the document-level source owns the aggregate count, while each occurrence stores its exact chunk URL. Web citations continue to use their normalized external URL as source identity.
