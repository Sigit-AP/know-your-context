# Design Spec

The system ingests code and documents into one graph.

## Ingestion

Extraction is deterministic and bounded.

### Languages

Thirty-three languages are supported via tree-sitter.

### Documents

Markdown, plaintext and PDF text layers.

## Resolution

References resolve in two phases.

### Exact matching

Names match exactly before any tail matching.

## Retrieval

Retrieval ranks by relevance, not recency.
