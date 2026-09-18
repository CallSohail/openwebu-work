# RAGFlow Advanced Connector Changelog

All notable changes to the RAGFlow Advanced Connector are documented here.
This project follows [Semantic Versioning](https://semver.org/).

Released as `ragflow-v<version>`. See [RELEASING.md](../../RELEASING.md) for the release process.

## [3.0.0] - 2026-08-26

First version published in this repository, as a portable Tool with no deployment-specific configuration in source.

### Added

- Valve-configured RAGFlow base URL and API key, with no credentials in source.
- `list_available_datasets`, with optional dataset statistics.
- `retrieve_from_ragflow`, hybrid vector and keyword retrieval across datasets.
- `retrieve_with_multi_query`, multi-query retrieval with optional query expansion.
- `search_specific_documents`, retrieval scoped to named documents.
- `get_dataset_knowledge_graph`, knowledge-graph inspection for a dataset.
- `list_rerank_models`, discovery of the rerank models the server exposes.
- Retrieval controls as Valves: `top_k`, `page_size`, `similarity_threshold`, `vector_similarity_weight`, `keyword_search`, `highlight_matches`, `enable_knowledge_graph`, `use_reranking`, `rerank_model_id`, `max_context_length`, `enable_query_expansion` and `query_expansion_count`.
- Cross-language retrieval through the `cross_languages` Valve.
- Per-user dataset scoping through the `selected_datasets` User Valve.
- Disclosure controls as Valves: `show_technical_details` and `include_reasoning_trace`.
- Dataset and rerank-model response caching within a Tool instance.
- Formatted retrieval context with source information for the model.

### Security

- No hard-coded API key, endpoint, or institutional identifier in the published source.
- `ragflow_base_url` defaults to the local Docker address `http://host.docker.internal:9380`, which each deployment is expected to change.
- Retrieval requests go only to the RAGFlow server the administrator configures.

### Compatibility

- Function type: Tool (`Tools` class).
- Requires `requests` and `pydantic`.
- No hard minimum Open WebUI version; the Tool uses the stable Workspace Tools interface.

---

Releases before 3.0.0 predate this repository and are not documented here.
