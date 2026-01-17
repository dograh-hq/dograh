"""Knowledge Base retrieval tool for workflow execution.

This module provides vector similarity search capabilities for retrieving
relevant information from the knowledge base during conversations.

Implements OpenTelemetry tracing for observability in Langfuse.
"""

import json
from typing import Any, Dict, List, Optional

from loguru import logger
from opentelemetry import trace

from api.db import db_client
from api.services.gen_ai import OpenAIEmbeddingService
from api.services.pipecat.tracing_config import is_tracing_enabled
from pipecat.utils.tracing.context_registry import (
    get_current_conversation_context,
    get_current_turn_context,
)


async def retrieve_from_knowledge_base(
    query: str,
    organization_id: int,
    document_uuids: Optional[List[str]] = None,
    limit: int = 3,
    embeddings_api_key: Optional[str] = None,
    embeddings_model: Optional[str] = None,
) -> Dict[str, Any]:
    """Retrieve relevant information from the knowledge base using vector similarity search.

    Uses OpenAI text-embedding-3-small for embeddings by default. This provides
    high-quality 1536-dimensional embeddings for accurate retrieval.

    This function includes OpenTelemetry tracing for Langfuse observability.

    Args:
        query: The search query to find relevant information
        organization_id: Organization ID for scoping the search
        document_uuids: Optional list of document UUIDs to filter by
        limit: Maximum number of chunks to return (default: 3)
        embeddings_api_key: Optional API key for embedding service
        embeddings_model: Optional model ID for embedding service

    Returns:
        Dictionary containing:
        - chunks: List of relevant text chunks with metadata
        - query: The original query
        - total_results: Number of results returned
    """
    # Create span for retrieval operation if tracing is enabled
    if is_tracing_enabled():
        try:
            # Get parent context from turn or conversation
            turn_context = get_current_turn_context()
            conversation_context = get_current_conversation_context()
            parent_context = turn_context or conversation_context

            # Get tracer
            tracer = trace.get_tracer("pipecat")
        except Exception as e:
            logger.debug(f"Failed to setup tracing context: {e}")
            # Fall back to non-traced execution
            return await _perform_retrieval(
                query,
                organization_id,
                document_uuids,
                limit,
                embeddings_api_key,
                embeddings_model,
            )

        # Create span with parent context
        if parent_context:
            with tracer.start_as_current_span(
                "knowledge_base_retrieval", context=parent_context
            ) as span:
                try:
                    # Mark trace as public for Langfuse
                    span.set_attribute("langfuse.trace.public", True)

                    # Add operation metadata
                    span.set_attribute(
                        "gen_ai.operation.name", "knowledge_base_retrieval"
                    )
                    span.set_attribute("retrieval.query", query)
                    span.set_attribute("retrieval.limit", limit)
                    span.set_attribute("retrieval.organization_id", organization_id)

                    # Add document filter info
                    if document_uuids:
                        span.set_attribute(
                            "retrieval.document_count", len(document_uuids)
                        )
                        span.set_attribute(
                            "retrieval.document_uuids", json.dumps(document_uuids)
                        )

                    # Perform the actual retrieval
                    result = await _perform_retrieval(
                        query,
                        organization_id,
                        document_uuids,
                        limit,
                        embeddings_api_key,
                        embeddings_model,
                    )

                    # Add result metadata to span
                    span.set_attribute(
                        "retrieval.results_count", result["total_results"]
                    )

                    if result.get("error"):
                        span.set_attribute("retrieval.error", result["error"])
                        span.set_status(
                            trace.Status(trace.StatusCode.ERROR, result["error"])
                        )
                    else:
                        # Add similarity scores
                        if result["chunks"]:
                            similarities = [
                                chunk["similarity"] for chunk in result["chunks"]
                            ]
                            span.set_attribute(
                                "retrieval.avg_similarity",
                                round(sum(similarities) / len(similarities), 4),
                            )
                            span.set_attribute(
                                "retrieval.max_similarity", max(similarities)
                            )
                            span.set_attribute(
                                "retrieval.min_similarity", min(similarities)
                            )

                        # Add retrieved documents info
                        filenames = list(
                            set(chunk["filename"] for chunk in result["chunks"])
                        )
                        span.set_attribute(
                            "retrieval.source_files", json.dumps(filenames)
                        )

                        # Add output as JSON for Langfuse
                        output_data = {
                            "query": query,
                            "chunks_retrieved": len(result["chunks"]),
                            "chunks": [
                                {
                                    "text": chunk["text"][:200] + "..."
                                    if len(chunk["text"]) > 200
                                    else chunk["text"],
                                    "filename": chunk["filename"],
                                    "similarity": chunk["similarity"],
                                }
                                for chunk in result["chunks"]
                            ],
                        }
                        span.set_attribute("output", json.dumps(output_data))

                    return result

                except Exception as e:
                    logger.error(f"Error in traced retrieval: {e}")
                    span.record_exception(e)
                    span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
                    raise
        else:
            # No parent context - perform retrieval without tracing
            logger.debug(
                "No parent context available for knowledge base retrieval tracing"
            )
            return await _perform_retrieval(
                query,
                organization_id,
                document_uuids,
                limit,
                embeddings_api_key,
                embeddings_model,
            )
    else:
        # Tracing is disabled - perform retrieval without tracing
        return await _perform_retrieval(
            query,
            organization_id,
            document_uuids,
            limit,
            embeddings_api_key,
            embeddings_model,
        )


async def _perform_retrieval(
    query: str,
    organization_id: int,
    document_uuids: Optional[List[str]],
    limit: int,
    embeddings_api_key: Optional[str] = None,
    embeddings_model: Optional[str] = None,
) -> Dict[str, Any]:
    """Internal function to perform the actual retrieval operation.

    Separated from tracing logic for cleaner code organization.
    Uses OpenAI embeddings by default for high-quality retrieval.
    """
    try:
        # Create a new embedding service instance
        # Uses OpenAI text-embedding-3-small by default, or user-provided config
        embedding_service = OpenAIEmbeddingService(
            db_client=db_client,
            max_tokens=128,  # This is only used for chunking, not for retrieval
            api_key=embeddings_api_key,
            model_id=embeddings_model or "text-embedding-3-small",
        )

        # Perform vector similarity search
        results = await embedding_service.search_similar_chunks(
            query=query,
            organization_id=organization_id,
            limit=limit,
            document_uuids=document_uuids,
        )

        # Format results for LLM consumption
        chunks = []
        for result in results:
            chunk_info = {
                "text": result.get("contextualized_text") or result.get("chunk_text"),
                "filename": result.get("filename"),
                "similarity": round(result.get("similarity", 0), 4),
                "chunk_index": result.get("chunk_index"),
            }
            chunks.append(chunk_info)

        logger.info(
            f"Knowledge base retrieval: query='{query}', "
            f"results={len(chunks)}, "
            f"document_filter={document_uuids}"
        )

        return {
            "chunks": chunks,
            "query": query,
            "total_results": len(chunks),
        }

    except Exception as e:
        logger.error(f"Error retrieving from knowledge base: {e}")
        return {
            "error": str(e),
            "chunks": [],
            "query": query,
            "total_results": 0,
        }


def get_knowledge_base_tool(
    document_uuids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Get knowledge base retrieval tool definition for LLM function calling.

    Args:
        document_uuids: Optional list of document UUIDs to include in description

    Returns:
        Tool definition compatible with LLM function calling
    """
    # Build description based on whether specific documents are filtered
    if document_uuids and len(document_uuids) > 0:
        description = (
            "Retrieve relevant information from specific documents in the knowledge base. "
            "Use this tool when you need to look up facts, policies, procedures, or any information "
            "that might be stored in the available documents. The search will only look in the "
            f"documents associated with this conversation step ({len(document_uuids)} document(s) available)."
        )
    else:
        description = (
            "Retrieve relevant information from the knowledge base. "
            "Use this tool when you need to look up facts, policies, procedures, or any information "
            "that might be stored in the knowledge base documents."
        )

    return {
        "type": "function",
        "function": {
            "name": "retrieve_from_knowledge_base",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "The search query to find relevant information. "
                            "Be specific and use natural language. "
                            "Example: 'What is the refund policy for canceled orders?'"
                        ),
                    }
                },
                "required": ["query"],
            },
        },
    }
