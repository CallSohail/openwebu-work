# RAGFlow Advanced Connector

**Type:** Tool  
**Version:** 3.0.0  
**Requires:** `requests`, `pydantic`, and a reachable RAGFlow server  
**Tags:** `tool`, `rag`, `ragflow`, `retrieval`, `knowledge-base`

This Open WebUI Tool connects models to a RAGFlow instance for retrieval across datasets and documents.

## Features

- configurable RAGFlow base URL and API key through Valves;
- dataset discovery;
- hybrid vector and keyword retrieval controls;
- similarity and candidate-pool tuning;
- cross-language retrieval settings;
- optional reranking settings;
- multi-query retrieval helpers;
- document-specific search;
- knowledge-graph inspection helpers;
- formatted retrieval context with source information.

## Installation

1. Open **Workspace > Tools** in Open WebUI.
2. Create or import a Tool.
3. Paste `ragflow.py`.
4. Configure the Tool Valves:
   - `ragflow_base_url`
   - `ragflow_api_key`
5. Enable the Tool for the intended model or chat.

## Requirements

```text
requests
pydantic
```

## Tool methods

| Method | Purpose |
| --- | --- |
| `list_available_datasets` | List the datasets on the server, optionally with statistics |
| `retrieve_from_ragflow` | Hybrid vector and keyword retrieval across datasets |
| `retrieve_with_multi_query` | Retrieval across several generated query variants |
| `search_specific_documents` | Retrieval scoped to named documents |
| `get_dataset_knowledge_graph` | Inspect a dataset's knowledge graph |
| `list_rerank_models` | List the rerank models the server exposes |

## Configuration

Valves, grouped by purpose.

**Connection**

| Valve | Default | Purpose |
| --- | --- | --- |
| `ragflow_base_url` | `http://host.docker.internal:9380` | RAGFlow server address. Change it for your deployment. |
| `ragflow_api_key` | empty | RAGFlow API key. Required, and never committed to source. |

**Retrieval**

| Valve | Default | Range | Purpose |
| --- | --- | --- | --- |
| `top_k` | `1024` | 1-10000 | Chunks in the vector-search candidate pool |
| `page_size` | `30` | 1-100 | Chunks returned per query |
| `similarity_threshold` | `0.2` | 0.0-1.0 | Minimum similarity score for a chunk to qualify |
| `vector_similarity_weight` | `0.3` | 0.0-1.0 | Weight for vector similarity; `1 - x` is the keyword weight |
| `keyword_search` | `True` | | Keyword matching alongside vector search |
| `highlight_matches` | `False` | | Highlight matched terms in results |
| `cross_languages` | `["fr", "en"]` | | Languages used for cross-language retrieval |
| `enable_knowledge_graph` | `False` | | Multi-hop reasoning over the dataset's knowledge graph. Slower, more comprehensive. |
| `use_reranking` | `False` | | Rerank results for quality |
| `rerank_model_id` | empty | | Reranker model id; empty uses the server default |
| `max_context_length` | `6000` | 500-20000 | Maximum total context length, in characters |
| `enable_query_expansion` | `False` | | Automatic query expansion for recall |
| `query_expansion_count` | `2` | 1-5 | Expanded queries generated per request |

**Disclosure**

| Valve | Default | Purpose |
| --- | --- | --- |
| `show_technical_details` | `False` | Show `kb_id`, `doc_id` and similarity scores in the reply |
| `include_reasoning_trace` | `False` | Include the retrieval reasoning trace in the reply |

**User Valves**

| Valve | Purpose |
| --- | --- |
| `selected_datasets` | Restrict retrieval to specific datasets for that user |

Every Valve carries its own description in the Open WebUI Tool settings.

## Data flow

Queries and the configured API key go to the RAGFlow server set in `ragflow_base_url`, and retrieved chunks come back into the conversation. Nothing is sent anywhere else, and the Tool makes no other outbound request.

## Known limitations

- The Tool has no built-in retry or circuit breaker; a RAGFlow outage surfaces as a failed Tool call.
- Dataset and rerank-model lookups are cached per Tool instance, so a dataset added on the server may need a new chat to appear.
- Retrieval quality depends on how the datasets were chunked and embedded in RAGFlow itself.

## Security

The API key is configured through Valves and should never be committed to the repository. The Tool sends retrieval requests to the RAGFlow server configured by the administrator.

Workspace Tools execute Python in the Open WebUI server process. Review the source before installation.

## Example prompts

```text
List the available RAGFlow datasets.
```

```text
Search the connected knowledge base for information about <topic>.
```

```text
Search only the documents related to <document name> for <question>.
```

## Changelog

See [CHANGELOG.md](CHANGELOG.md). Released as `ragflow-v3.0.0`; see [RELEASING.md](../../RELEASING.md) for the release process.

## License

Released under the repository's [MIT License](../../LICENSE).
