# RAGFlow Advanced Connector

**Type:** Tool  
**Version:** 3.0.0  
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
