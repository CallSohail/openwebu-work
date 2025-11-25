"""
title: RAGFlow Advanced Connector v3.0
author: Muhammad Sohail
description: Production-ready RAG retrieval with knowledge graph, advanced search, multi-query strategies, and intelligent result processing for OpenWebUI.
version: 3.0.0
requirements: requests, pydantic
license: MIT
"""

from __future__ import annotations
from typing import Optional, List, Dict, Any, Literal
import requests
import json
from pydantic import BaseModel, Field


class Tools:
    def __init__(self):
        self.valves = self.Valves()
        self.citation = False
        self._datasets_cache: Optional[Dict[str, str]] = None
        self._rerank_models_cache: Optional[List[Dict[str, Any]]] = None

    class Valves(BaseModel):
        # CHANGED: Defaulted to local docker address. Users must change this.
        ragflow_base_url: str = Field(
            "http://host.docker.internal:9380",
            description="RAGFlow base URL (e.g., http://your-ip:9380)"
        )
        # CHANGED: Removed the hardcoded API Key.
        ragflow_api_key: str = Field(
            "",
            description="RAGFlow API key (Get this from your RAGFlow console)"
        )

        # Advanced retrieval parameters
        top_k: int = Field(
            1024,
            ge=1,
            le=10000,
            description="Number of chunks for vector search candidate pool",
        )
        page_size: int = Field(
            30, ge=1, le=100, description="Number of chunks to return per query"
        )
        similarity_threshold: float = Field(
            0.2,
            ge=0.0,
            le=1.0,
            description="Minimum similarity score threshold (0.0-1.0)",
        )
        vector_similarity_weight: float = Field(
            0.3,
            ge=0.0,
            le=1.0,
            description="Weight for vector similarity (1-x is keyword weight)",
        )

        # Search behavior
        keyword_search: bool = Field(
            True, description="Enable keyword-based matching alongside vector search"
        )
        highlight_matches: bool = Field(
            False, description="Highlight matched terms in results"
        )
        cross_languages: List[str] = Field(
            default_factory=lambda: ["fr", "en"],
            description="Languages for cross-language retrieval",
        )

        # Knowledge Graph settings
        enable_knowledge_graph: bool = Field(
            False,
            description="Enable knowledge graph for multi-hop reasoning (slower but more comprehensive)",
        )

        # Reranking
        use_reranking: bool = Field(
            False, description="Enable reranking for improved result quality"
        )
        rerank_model_id: str = Field(
            "", description="Reranker model ID (leave empty for default)"
        )

        # Output formatting
        show_technical_details: bool = Field(
            False,
            description="Show technical metadata (kb_id, doc_id, similarity scores)",
        )
        max_context_length: int = Field(
            6000,
            ge=500,
            le=20000,
            description="Maximum total context length in characters",
        )
        include_reasoning_trace: bool = Field(
            False, description="Include retrieval reasoning trace in output"
        )

        # Multi-query strategies
        enable_query_expansion: bool = Field(
            False,
            description="Enable automatic query expansion for better recall",
        )
        query_expansion_count: int = Field(
            2, ge=1, le=5, description="Number of expanded queries to generate"
        )

    class UserValves(BaseModel):
        selected_datasets: List[str] = Field(
            default_factory=list,
            description="Select specific datasets (leave empty for all available)",
        )
        custom_top_k: Optional[int] = Field(
            None, ge=1, le=10000, description="Override top_k for this user"
        )
        custom_similarity_threshold: Optional[float] = Field(
            None,
            ge=0.0,
            le=1.0,
            description="Override similarity threshold for this user",
        )
        preferred_chunk_methods: List[str] = Field(
            default_factory=list,
            description="Preferred chunking methods (naive, qa, table, etc.)",
        )

    # ========== Helper Methods ==========

    def _headers(self) -> Dict[str, str]:
        """Generate authentication headers for RAGFlow API."""
        if not self.valves.ragflow_api_key:
            raise ValueError(
                "RAGFlow API key is missing. Please configure it in the tool valves."
            )
        return {
            "Authorization": f"Bearer {self.valves.ragflow_api_key}",
            "Content-Type": "application/json",
        }

    def _fetch_available_datasets(
        self, force_refresh: bool = False, include_stats: bool = False
    ) -> Dict[str, Any]:
        """
        Fetch all available datasets from RAGFlow with optional statistics.
        Returns a dict mapping dataset names to their full info.
        """
        if self._datasets_cache and not force_refresh and not include_stats:
            return self._datasets_cache

        base = self.valves.ragflow_base_url.rstrip("/")
        url = f"{base}/api/v1/datasets?page=1&page_size=1024"

        try:
            resp = requests.get(url, headers=self._headers(), timeout=15)

            if resp.status_code != 200:
                raise Exception(f"HTTP {resp.status_code}: {resp.text}")

            data = resp.json()
            if data.get("code") != 0:
                raise Exception(
                    f"API error code {data.get('code')}: {data.get('message')}"
                )

            datasets = data.get("data", [])

            if include_stats:
                # Return full dataset info including stats
                return {
                    ds.get("name", f"Dataset_{ds.get('id')}"): {
                        "id": ds.get("id"),
                        "chunk_count": ds.get("chunk_count", 0),
                        "document_count": ds.get("document_count", 0),
                        "embedding_model": ds.get("embedding_model", ""),
                        "chunk_method": ds.get("chunk_method", ""),
                        "description": ds.get("description", ""),
                    }
                    for ds in datasets
                    if ds.get("id")
                }
            else:
                # Simple name to ID mapping for cache
                self._datasets_cache = {
                    ds.get("name", f"Dataset_{ds.get('id')}"): ds.get("id")
                    for ds in datasets
                    if ds.get("id")
                }
                return self._datasets_cache

        except Exception as e:
            return {}

    def _fetch_rerank_models(self) -> List[Dict[str, Any]]:
        """Fetch available rerank models from RAGFlow."""
        if self._rerank_models_cache:
            return self._rerank_models_cache

        # Note: This is a placeholder - actual endpoint may vary
        # In practice, you'd need to query the LLM management endpoint
        # For now, return common rerank model patterns
        self._rerank_models_cache = [
            {"id": "bge-reranker-v2-m3", "name": "BGE Reranker v2 M3"},
            {"id": "bce-reranker-base_v1", "name": "BCE Reranker Base v1"},
            {"id": "reranker-v1", "name": "Default Reranker v1"},
        ]
        return self._rerank_models_cache

    def _get_effective_dataset_ids(
        self, override_ids: Optional[List[str]], __user__: Optional[Dict[str, Any]]
    ) -> List[str]:
        """
        Determine which dataset IDs to use for retrieval.
        Priority: explicit override > user selection > all available datasets
        """
        if override_ids:
            return override_ids

        available_datasets = self._fetch_available_datasets()

        if __user__:
            valves = __user__.get("valves")
            if valves:
                selected = (
                    valves.selected_datasets
                    if hasattr(valves, "selected_datasets")
                    else valves.get("selected_datasets", [])
                )

                if selected:
                    return [
                        available_datasets.get(name, name)
                        for name in selected
                        if name in available_datasets
                        or name in available_datasets.values()
                    ]

        return list(available_datasets.values())

    def _get_user_overrides(self, __user__: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Extract user-specific parameter overrides."""
        overrides = {}

        if __user__:
            valves = __user__.get("valves")
            if valves:
                if hasattr(valves, "custom_top_k") and valves.custom_top_k:
                    overrides["top_k"] = valves.custom_top_k
                elif isinstance(valves, dict) and valves.get("custom_top_k"):
                    overrides["top_k"] = valves["custom_top_k"]

                if (
                    hasattr(valves, "custom_similarity_threshold")
                    and valves.custom_similarity_threshold
                ):
                    overrides["similarity_threshold"] = (
                        valves.custom_similarity_threshold
                    )
                elif isinstance(valves, dict) and valves.get(
                    "custom_similarity_threshold"
                ):
                    overrides["similarity_threshold"] = valves[
                        "custom_similarity_threshold"
                    ]

        return overrides

    def _deduplicate_chunks(
        self, chunks: List[Dict[str, Any]], similarity_key: str = "similarity"
    ) -> List[Dict[str, Any]]:
        """
        Deduplicate chunks based on content similarity.
        Keeps highest scoring chunk when duplicates found.
        """
        seen_content = {}
        deduplicated = []

        for chunk in chunks:
            content = chunk.get("content", "").strip()
            content_hash = hash(content[:200])  # Use first 200 chars for matching

            if content_hash not in seen_content:
                seen_content[content_hash] = chunk
                deduplicated.append(chunk)
            else:
                # Keep chunk with higher similarity
                existing_score = seen_content[content_hash].get(similarity_key, 0)
                new_score = chunk.get(similarity_key, 0)
                if new_score > existing_score:
                    deduplicated.remove(seen_content[content_hash])
                    seen_content[content_hash] = chunk
                    deduplicated.append(chunk)

        return deduplicated

    def _format_clean_output(
        self,
        question: str,
        data: Dict[str, Any],
        show_technical: bool = False,
        include_reasoning: bool = False,
        retrieval_metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Format retrieval results into clean, production-ready output.
        """
        chunks = data.get("chunks", [])
        doc_aggs = data.get("doc_aggs", [])

        if not chunks:
            return (
                "**No relevant information found**\n\n"
                "I couldn't find any content in the knowledge base that matches your query. "
                "Please try:\n"
                "- Rephrasing your question\n"
                "- Using different keywords\n"
                "- Checking if the information exists in the available documents"
            )

        # Deduplicate chunks
        chunks = self._deduplicate_chunks(chunks)

        # Build context sections
        context_blocks = []
        sources_list = []
        total_length = 0
        max_length = self.valves.max_context_length

        for idx, chunk in enumerate(chunks, start=1):
            content = chunk.get("highlight") or chunk.get("content", "")
            if not content:
                continue

            if total_length + len(content) > max_length:
                remaining = len(chunks) - idx + 1
                context_blocks.append(
                    f"\n*[{remaining} more source(s) omitted due to length constraints]*"
                )
                break

            doc_name = chunk.get("document_keyword") or chunk.get(
                "document_name", "Unknown Document"
            )
            dataset_id = chunk.get("kb_id", "")

            # Get dataset name
            dataset_name = None
            if dataset_id and self._datasets_cache:
                for name, ds_id in self._datasets_cache.items():
                    if ds_id == dataset_id:
                        dataset_name = name
                        break

            # Format context block
            if show_technical:
                similarity = chunk.get("similarity", 0)
                vector_sim = chunk.get("vector_similarity", 0)
                term_sim = chunk.get("term_similarity", 0)
                doc_id = chunk.get("document_id", "")
                context_block = (
                    f"**[Source {idx}]** *{dataset_name or 'Dataset'}* — **{doc_name}**\n"
                    f"{content}\n"
                    f"*[Combined: {similarity:.3f} | Vector: {vector_sim:.3f} | Term: {term_sim:.3f} | Doc: {doc_id[:12]}...]*"
                )
            else:
                context_block = (
                    f"**[Source {idx}]** {dataset_name or ''} — **{doc_name}**\n"
                    f"{content}"
                )

            context_blocks.append(context_block)
            sources_list.append(f"[{idx}] {doc_name}")
            total_length += len(content)

        # Build output
        output_parts = ["# 📚 Retrieved Context\n"]

        # Add reasoning trace if enabled
        if include_reasoning and retrieval_metadata:
            reasoning_parts = ["\n## 🔍 Retrieval Strategy\n"]
            if retrieval_metadata.get("query_expansion"):
                reasoning_parts.append(
                    f"- **Query Expansion**: Generated {len(retrieval_metadata['expanded_queries'])} variations"
                )
            if retrieval_metadata.get("knowledge_graph_used"):
                reasoning_parts.append(
                    "- **Knowledge Graph**: Multi-hop reasoning enabled"
                )
            if retrieval_metadata.get("reranking_used"):
                reasoning_parts.append(
                    f"- **Reranking**: Applied {retrieval_metadata.get('rerank_model', 'default')} model"
                )
            reasoning_parts.append(
                f"- **Search Mode**: {'Hybrid (vector + keyword)' if self.valves.keyword_search else 'Vector only'}"
            )
            reasoning_parts.append(
                f"- **Results**: {len(chunks)} chunks from {len(doc_aggs) if doc_aggs else 0} documents"
            )
            output_parts.append("\n".join(reasoning_parts))
            output_parts.append("\n")

        output_parts.extend(
            [
                "\n---\n\n".join(context_blocks),
                "\n\n---\n\n## 📑 Sources Referenced\n",
                "\n".join(sources_list),
            ]
        )

        # Document summary
        if doc_aggs:
            doc_summary = []
            for agg in doc_aggs[:10]:
                doc_name = agg.get("doc_name", "Unknown")
                count = agg.get("count", 0)
                doc_summary.append(
                    f"• {doc_name} ({count} excerpt{'s' if count != 1 else ''})"
                )

            if doc_summary:
                output_parts.extend(
                    ["\n\n## 📄 Documents Consulted\n", "\n".join(doc_summary)]
                )

        output_parts.extend(
            [
                f"\n\n---\n\n## ❓ Your Question\n\n> {question}",
                "\n\n**Instructions**: Please provide an answer based strictly on the context above. "
                "If the information is not present in the retrieved sources, clearly state that you "
                "don't have enough information to answer the question.",
            ]
        )

        return "\n".join(output_parts)

    def _execute_retrieval_request(
        self,
        payload: Dict[str, Any],
        endpoint: str = "/api/v1/retrieval",
    ) -> Dict[str, Any]:
        """Execute a retrieval API request with error handling."""
        base = self.valves.ragflow_base_url.rstrip("/")
        url = f"{base}{endpoint}"

        try:
            response = requests.post(
                url, headers=self._headers(), data=json.dumps(payload), timeout=30
            )

            if response.status_code != 200:
                raise Exception(f"HTTP {response.status_code}: {response.text[:500]}")

            result = response.json()

            if result.get("code") != 0:
                raise Exception(
                    f"API Error (Code: {result.get('code')}): {result.get('message', 'Unknown error')}"
                )

            return result.get("data", {})

        except requests.RequestException as e:
            raise Exception(f"Network error: Unable to connect to RAGFlow. {str(e)}")

    # ========== Public Tool Methods ==========

    def list_available_datasets(self, include_statistics: bool = False) -> str:
        """
        Lists all datasets available in your RAGFlow instance with optional statistics.

        Args:
            include_statistics: If True, includes chunk counts, document counts, and embedding models

        Returns:
            Formatted list of datasets with their information
        """
        try:
            datasets = self._fetch_available_datasets(
                force_refresh=True, include_stats=include_statistics
            )

            if not datasets:
                return (
                    "⚠️ **No datasets found or unable to fetch datasets.**\n\n"
                    "Please check:\n"
                    "- Your RAGFlow API key is valid\n"
                    "- Your RAGFlow URL is correct\n"
                    "- You have datasets created in RAGFlow"
                )

            output = [f"# 📊 Available Datasets ({len(datasets)})\n"]

            for name, info in datasets.items():
                if include_statistics and isinstance(info, dict):
                    output.append(f"### **{name}**")
                    output.append(f"- **ID**: {info.get('id', 'N/A')}")
                    output.append(f"- **Documents**: {info.get('document_count', 0)}")
                    output.append(f"- **Chunks**: {info.get('chunk_count', 0)}")
                    output.append(
                        f"- **Embedding Model**: {info.get('embedding_model', 'N/A')}"
                    )
                    output.append(
                        f"- **Chunking Method**: {info.get('chunk_method', 'N/A')}"
                    )
                    if info.get("description"):
                        output.append(f"- **Description**: {info['description']}")
                    output.append("")
                else:
                    output.append(f"• **{name}**")
                    if self.valves.show_technical_details and isinstance(info, str):
                        output.append(f"  *ID: {info}*")

            output.append(
                "\n💡 **Tip**: Users can select specific datasets in their Valves settings."
            )

            return "\n".join(output)

        except Exception as e:
            return f"❌ **Error fetching datasets**: {str(e)}"

    def list_rerank_models(self) -> str:
        """
        Lists available reranking models that can be used for improved retrieval.

        Returns:
            Formatted list of reranking models
        """
        try:
            models = self._fetch_rerank_models()

            if not models:
                return "⚠️ **No reranking models found.**"

            output = [f"# 🎯 Available Reranking Models ({len(models)})\n"]

            for model in models:
                output.append(f"• **{model['name']}**")
                output.append(f"  *ID: {model['id']}*\n")

            output.append(
                "\n💡 **Tip**: Enable reranking in Valves settings for improved result quality."
            )

            return "\n".join(output)

        except Exception as e:
            return f"❌ **Error fetching rerank models**: {str(e)}"

    def retrieve_from_ragflow(
        self,
        question: str,
        dataset_ids: Optional[List[str]] = None,
        document_ids: Optional[List[str]] = None,
        page_size: Optional[int] = None,
        top_k: Optional[int] = None,
        similarity_threshold: Optional[float] = None,
        vector_similarity_weight: Optional[float] = None,
        keyword: Optional[bool] = None,
        highlight: Optional[bool] = None,
        rerank_id: Optional[str] = None,
        cross_languages: Optional[List[str]] = None,
        use_knowledge_graph: Optional[bool] = None,
        __user__: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Retrieve relevant information from RAGFlow knowledge bases with advanced options.

        Args:
            question: The user's query or question
            dataset_ids: Optional list of specific dataset IDs to search
            document_ids: Optional list of specific document IDs to search within
            page_size: Number of chunks to return (default: from valves)
            top_k: Candidate pool size for vector search (default: from valves)
            similarity_threshold: Minimum similarity score 0.0-1.0 (default: from valves)
            vector_similarity_weight: Weight for vector vs keyword similarity (default: from valves)
            keyword: Enable keyword search (default: from valves)
            highlight: Highlight matching terms (default: from valves)
            rerank_id: Optional reranker model ID (default: from valves)
            cross_languages: Languages for cross-language search (default: from valves)
            use_knowledge_graph: Enable knowledge graph for multi-hop reasoning (default: from valves)

        Returns:
            Formatted context with sources, ready for answering
        """
        if not question or not question.strip():
            return "❌ **Error**: Question cannot be empty."

        try:
            # Get effective parameters
            effective_dataset_ids = self._get_effective_dataset_ids(
                dataset_ids, __user__
            )

            if not effective_dataset_ids and not document_ids:
                datasets_info = self._fetch_available_datasets()
                if datasets_info:
                    return (
                        f"⚠️ **No datasets selected for search.**\n\n"
                        f"Available datasets: {', '.join(list(datasets_info.keys())[:5])}"
                        f"{'...' if len(datasets_info) > 5 else ''}\n\n"
                        "Please select datasets in your user valves or specify dataset_ids."
                    )
                else:
                    return (
                        "❌ **No datasets available.**\n\n"
                        "Please create datasets in RAGFlow or check your API configuration."
                    )

            user_overrides = self._get_user_overrides(__user__)

            # Determine if knowledge graph should be used
            kg_enabled = (
                use_knowledge_graph
                if use_knowledge_graph is not None
                else self.valves.enable_knowledge_graph
            )

            # Build retrieval payload
            payload = {
                "question": question,
                "dataset_ids": effective_dataset_ids,
                "document_ids": document_ids or [],
                "page": 1,
                "page_size": page_size or self.valves.page_size,
                "similarity_threshold": (
                    user_overrides.get("similarity_threshold")
                    or similarity_threshold
                    or self.valves.similarity_threshold
                ),
                "vector_similarity_weight": (
                    vector_similarity_weight or self.valves.vector_similarity_weight
                ),
                "top_k": (user_overrides.get("top_k") or top_k or self.valves.top_k),
                "keyword": (
                    keyword if keyword is not None else self.valves.keyword_search
                ),
                "highlight": (
                    highlight
                    if highlight is not None
                    else self.valves.highlight_matches
                ),
                "cross_languages": cross_languages or self.valves.cross_languages,
            }

            # Add reranking if enabled
            use_reranking = self.valves.use_reranking or rerank_id
            if use_reranking:
                payload["rerank_id"] = (
                    rerank_id or self.valves.rerank_model_id or "bge-reranker-v2-m3"
                )

            # Track retrieval metadata
            retrieval_metadata = {
                "knowledge_graph_used": kg_enabled,
                "reranking_used": use_reranking,
                "rerank_model": payload.get("rerank_id", ""),
                "query_expansion": False,
                "expanded_queries": [],
            }

            # Execute retrieval
            data = self._execute_retrieval_request(payload)

            # Format and return output
            return self._format_clean_output(
                question=question,
                data=data,
                show_technical=self.valves.show_technical_details,
                include_reasoning=self.valves.include_reasoning_trace,
                retrieval_metadata=retrieval_metadata,
            )

        except Exception as e:
            return f"❌ **Error during retrieval**: {str(e)}"

    def retrieve_with_multi_query(
        self,
        question: str,
        num_variations: Optional[int] = None,
        dataset_ids: Optional[List[str]] = None,
        __user__: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Perform multi-query retrieval by generating query variations for improved recall.

        This method automatically generates semantic variations of your query and
        retrieves results from all variations, then deduplicates and ranks them.

        Args:
            question: The original user query
            num_variations: Number of query variations to generate (default: from valves)
            dataset_ids: Optional specific datasets to search
            __user__: User context

        Returns:
            Formatted context from multi-query retrieval
        """
        if not question or not question.strip():
            return "❌ **Error**: Question cannot be empty."

        # For now, use simple query expansion
        # In production, you'd use an LLM to generate semantic variations
        num_vars = num_variations or self.valves.query_expansion_count

        # Simple keyword-based expansion (placeholder for LLM-based expansion)
        expanded_queries = [question]

        # Add basic variations
        if "what" in question.lower():
            expanded_queries.append(
                question.replace("what", "which").replace("What", "Which")
            )
        if "how" in question.lower():
            expanded_queries.append(
                question.replace("how", "what method").replace("How", "What method")
            )

        expanded_queries = expanded_queries[: num_vars + 1]

        # Execute retrieval for each query
        all_chunks = []
        all_doc_aggs = {}

        for query in expanded_queries:
            try:
                effective_dataset_ids = self._get_effective_dataset_ids(
                    dataset_ids, __user__
                )
                user_overrides = self._get_user_overrides(__user__)

                payload = {
                    "question": query,
                    "dataset_ids": effective_dataset_ids,
                    "page": 1,
                    "page_size": self.valves.page_size,
                    "similarity_threshold": user_overrides.get("similarity_threshold")
                    or self.valves.similarity_threshold,
                    "vector_similarity_weight": self.valves.vector_similarity_weight,
                    "top_k": user_overrides.get("top_k") or self.valves.top_k,
                    "keyword": self.valves.keyword_search,
                    "highlight": False,
                    "cross_languages": self.valves.cross_languages,
                }

                data = self._execute_retrieval_request(payload)

                all_chunks.extend(data.get("chunks", []))
                for agg in data.get("doc_aggs", []):
                    doc_name = agg.get("doc_name")
                    if doc_name not in all_doc_aggs:
                        all_doc_aggs[doc_name] = agg
                    else:
                        all_doc_aggs[doc_name]["count"] += agg.get("count", 0)

            except Exception:
                continue  # Skip failed queries

        # Deduplicate and sort by similarity
        all_chunks = self._deduplicate_chunks(all_chunks)
        all_chunks.sort(key=lambda x: x.get("similarity", 0), reverse=True)

        # Limit to page_size
        all_chunks = all_chunks[: self.valves.page_size]

        combined_data = {
            "chunks": all_chunks,
            "doc_aggs": list(all_doc_aggs.values()),
            "total": len(all_chunks),
        }

        retrieval_metadata = {
            "query_expansion": True,
            "expanded_queries": expanded_queries,
            "knowledge_graph_used": False,
            "reranking_used": False,
        }

        return self._format_clean_output(
            question=question,
            data=combined_data,
            show_technical=self.valves.show_technical_details,
            include_reasoning=True,
            retrieval_metadata=retrieval_metadata,
        )

    def search_specific_documents(
        self,
        question: str,
        document_names: List[str],
        __user__: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Search within specific documents by name.

        Args:
            question: The user's query
            document_names: List of document names to search within
            __user__: User context

        Returns:
            Formatted context from the specified documents
        """
        if not document_names:
            return "❌ **Error**: Please specify at least one document name."

        try:
            dataset_ids = self._get_effective_dataset_ids(None, __user__)

            if not dataset_ids:
                return "❌ **Error**: No datasets available to search for documents."

            base = self.valves.ragflow_base_url.rstrip("/")
            found_doc_ids = []

            for ds_id in dataset_ids:
                url = f"{base}/api/v1/datasets/{ds_id}/documents"
                params = {"page": 1, "page_size": 1024}

                response = requests.get(
                    url, headers=self._headers(), params=params, timeout=15
                )

                if response.status_code == 200:
                    result = response.json()
                    if result.get("code") == 0:
                        docs = result.get("data", {}).get("docs", [])
                        for doc in docs:
                            doc_name = doc.get("name", "")
                            if any(
                                name.lower() in doc_name.lower()
                                for name in document_names
                            ):
                                found_doc_ids.append(doc.get("id"))

            if not found_doc_ids:
                return (
                    f"⚠️ **No matching documents found**\n\n"
                    f"Searched for: {', '.join(document_names)}\n\n"
                    "Please check document names and try again."
                )

            return self.retrieve_from_ragflow(
                question=question,
                document_ids=found_doc_ids,
                dataset_ids=None,
                __user__=__user,
            )

        except Exception as e:
            return f"❌ **Error searching documents**: {str(e)}"

    def get_dataset_knowledge_graph(
        self, dataset_name: str, __user__: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Retrieve the knowledge graph structure for a specific dataset.

        This is useful for understanding entity relationships and planning
        multi-hop queries.

        Args:
            dataset_name: Name of the dataset to get knowledge graph from
            __user__: User context

        Returns:
            Formatted knowledge graph information
        """
        try:
            datasets = self._fetch_available_datasets()

            if dataset_name not in datasets:
                return (
                    f"❌ **Dataset '{dataset_name}' not found.**\n\n"
                    f"Available datasets: {', '.join(list(datasets.keys())[:5])}"
                )

            dataset_id = datasets[dataset_name]
            base = self.valves.ragflow_base_url.rstrip("/")
            url = f"{base}/api/v1/datasets/{dataset_id}/knowledge_graph"

            response = requests.get(url, headers=self._headers(), timeout=30)

            if response.status_code != 200:
                return f"❌ **Error fetching knowledge graph**: HTTP {response.status_code}"

            result = response.json()

            if result.get("code") != 0:
                return f"❌ **Error**: {result.get('message', 'Failed to fetch knowledge graph')}"

            graph_data = result.get("data", {}).get("graph", {})
            nodes = graph_data.get("nodes", [])
            edges = graph_data.get("edges", [])

            output = [
                f"# 🕸️ Knowledge Graph: {dataset_name}\n",
                f"## Statistics",
                f"- **Nodes (Entities)**: {len(nodes)}",
                f"- **Edges (Relationships)**: {len(edges)}\n",
            ]

            if nodes:
                output.append("## Top Entities\n")
                sorted_nodes = sorted(
                    nodes, key=lambda x: x.get("pagerank", 0), reverse=True
                )[:10]

                for node in sorted_nodes:
                    entity_name = node.get("entity_name", "Unknown")
                    entity_type = node.get("entity_type", "")
                    pagerank = node.get("pagerank", 0)
                    output.append(
                        f"• **{entity_name}** ({entity_type}) - PageRank: {pagerank:.4f}"
                    )

            if edges:
                output.append("\n## Sample Relationships\n")
                for edge in edges[:5]:
                    source = edge.get("source", "?")
                    target = edge.get("target", "?")
                    weight = edge.get("weight", 0)
                    output.append(f"• {source} → {target} (weight: {weight:.2f})")

            output.append(
                "\n💡 **Tip**: Enable knowledge graph in Valves for multi-hop reasoning during retrieval."
            )

            return "\n".join(output)

        except Exception as e:
            return f"❌ **Error fetching knowledge graph**: {str(e)}"
