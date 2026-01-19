"""Database client for managing knowledge base documents and chunks."""

import hashlib
from pathlib import Path
from typing import List, Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from api.db.base_client import BaseDBClient
from api.db.models import KnowledgeBaseChunkModel, KnowledgeBaseDocumentModel


class KnowledgeBaseClient(BaseDBClient):
    """Client for managing knowledge base documents and vector embeddings."""

    async def create_document(
        self,
        organization_id: int,
        created_by: int,
        filename: str,
        file_size_bytes: int,
        file_hash: str,
        mime_type: str,
        source_url: Optional[str] = None,
        custom_metadata: Optional[dict] = None,
        docling_metadata: Optional[dict] = None,
        document_uuid: Optional[str] = None,
    ) -> KnowledgeBaseDocumentModel:
        """Create a new knowledge base document record.

        Args:
            organization_id: ID of the organization
            created_by: ID of the user uploading the document
            filename: Name of the file
            file_size_bytes: Size of the file in bytes
            file_hash: SHA-256 hash of the file
            mime_type: MIME type of the file
            source_url: Optional URL if document was fetched from web
            custom_metadata: Optional custom metadata dictionary
            docling_metadata: Optional docling processing metadata
            document_uuid: Optional UUID to use (if not provided, one will be generated)

        Returns:
            The created KnowledgeBaseDocumentModel
        """
        async with self.async_session() as session:
            document = KnowledgeBaseDocumentModel(
                organization_id=organization_id,
                created_by=created_by,
                filename=filename,
                file_size_bytes=file_size_bytes,
                file_hash=file_hash,
                mime_type=mime_type,
                source_url=source_url,
                custom_metadata=custom_metadata or {},
                docling_metadata=docling_metadata or {},
                processing_status="pending",
                total_chunks=0,
            )

            # Use provided UUID or let the model generate one
            if document_uuid:
                document.document_uuid = document_uuid

            session.add(document)
            await session.commit()
            await session.refresh(document)

            logger.info(
                f"Created document '{filename}' ({document.document_uuid}) "
                f"for organization {organization_id}"
            )
            return document

    async def get_document_by_id(
        self,
        document_id: int,
    ) -> Optional[KnowledgeBaseDocumentModel]:
        """Get a document by its database ID.

        Args:
            document_id: The database ID of the document

        Returns:
            KnowledgeBaseDocumentModel if found, None otherwise
        """
        async with self.async_session() as session:
            query = select(KnowledgeBaseDocumentModel).where(
                KnowledgeBaseDocumentModel.id == document_id
            )

            result = await session.execute(query)
            return result.scalar_one_or_none()

    async def get_document_by_uuid(
        self,
        document_uuid: str,
        organization_id: int,
    ) -> Optional[KnowledgeBaseDocumentModel]:
        """Get a document by its UUID, scoped to organization.

        Args:
            document_uuid: The unique document UUID
            organization_id: ID of the organization

        Returns:
            KnowledgeBaseDocumentModel if found, None otherwise
        """
        async with self.async_session() as session:
            query = (
                select(KnowledgeBaseDocumentModel)
                .where(
                    KnowledgeBaseDocumentModel.document_uuid == document_uuid,
                    KnowledgeBaseDocumentModel.organization_id == organization_id,
                    KnowledgeBaseDocumentModel.is_active == True,
                )
                .options(selectinload(KnowledgeBaseDocumentModel.created_by_user))
            )

            result = await session.execute(query)
            return result.scalar_one_or_none()

    async def get_document_by_hash(
        self,
        file_hash: str,
        organization_id: int,
    ) -> Optional[KnowledgeBaseDocumentModel]:
        """Check if a document with the same hash already exists.

        Returns the first matching document if multiple exist (can happen with duplicates).

        Args:
            file_hash: SHA-256 hash of the file
            organization_id: ID of the organization

        Returns:
            KnowledgeBaseDocumentModel if found, None otherwise
        """
        async with self.async_session() as session:
            query = (
                select(KnowledgeBaseDocumentModel)
                .where(
                    KnowledgeBaseDocumentModel.file_hash == file_hash,
                    KnowledgeBaseDocumentModel.organization_id == organization_id,
                    KnowledgeBaseDocumentModel.is_active == True,
                )
                .order_by(KnowledgeBaseDocumentModel.created_at.asc())
                .limit(1)
            )

            result = await session.execute(query)
            return result.scalars().first()

    async def get_documents_for_organization(
        self,
        organization_id: int,
        processing_status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[KnowledgeBaseDocumentModel]:
        """Get all documents for an organization.

        Args:
            organization_id: ID of the organization
            processing_status: Optional filter by status
            limit: Maximum number of documents to return
            offset: Number of documents to skip

        Returns:
            List of KnowledgeBaseDocumentModel instances
        """
        async with self.async_session() as session:
            query = select(KnowledgeBaseDocumentModel).where(
                KnowledgeBaseDocumentModel.organization_id == organization_id,
                KnowledgeBaseDocumentModel.is_active == True,
            )

            if processing_status:
                query = query.where(
                    KnowledgeBaseDocumentModel.processing_status == processing_status
                )

            query = (
                query.order_by(KnowledgeBaseDocumentModel.created_at.desc())
                .limit(limit)
                .offset(offset)
            )

            result = await session.execute(query)
            return list(result.scalars().all())

    async def update_document_metadata(
        self,
        document_id: int,
        file_size_bytes: Optional[int] = None,
        file_hash: Optional[str] = None,
        mime_type: Optional[str] = None,
    ) -> Optional[KnowledgeBaseDocumentModel]:
        """Update document file metadata.

        Args:
            document_id: ID of the document
            file_size_bytes: Optional file size in bytes
            file_hash: Optional SHA-256 hash of the file
            mime_type: Optional MIME type

        Returns:
            Updated KnowledgeBaseDocumentModel
        """
        async with self.async_session() as session:
            query = select(KnowledgeBaseDocumentModel).where(
                KnowledgeBaseDocumentModel.id == document_id
            )
            result = await session.execute(query)
            document = result.scalar_one_or_none()

            if not document:
                return None

            if file_size_bytes is not None:
                document.file_size_bytes = file_size_bytes
            if file_hash is not None:
                document.file_hash = file_hash
            if mime_type is not None:
                document.mime_type = mime_type

            await session.commit()
            await session.refresh(document)

            logger.info(f"Updated document {document_id} metadata")
            return document

    async def update_document_status(
        self,
        document_id: int,
        status: str,
        error_message: Optional[str] = None,
        total_chunks: Optional[int] = None,
        docling_metadata: Optional[dict] = None,
    ) -> Optional[KnowledgeBaseDocumentModel]:
        """Update document processing status.

        Args:
            document_id: ID of the document
            status: New status (pending, processing, completed, failed)
            error_message: Optional error message if status is failed
            total_chunks: Optional total number of chunks
            docling_metadata: Optional docling metadata

        Returns:
            Updated KnowledgeBaseDocumentModel
        """
        async with self.async_session() as session:
            query = select(KnowledgeBaseDocumentModel).where(
                KnowledgeBaseDocumentModel.id == document_id
            )
            result = await session.execute(query)
            document = result.scalar_one_or_none()

            if not document:
                return None

            document.processing_status = status
            if error_message:
                document.processing_error = error_message
            if total_chunks is not None:
                document.total_chunks = total_chunks
            if docling_metadata:
                document.docling_metadata = docling_metadata

            await session.commit()
            await session.refresh(document)

            logger.info(f"Updated document {document_id} status to {status}")
            return document

    async def create_chunks_batch(
        self,
        chunks: List[KnowledgeBaseChunkModel],
    ) -> List[KnowledgeBaseChunkModel]:
        """Create multiple chunks in a batch.

        Args:
            chunks: List of KnowledgeBaseChunkModel instances

        Returns:
            List of created chunks with IDs
        """
        async with self.async_session() as session:
            session.add_all(chunks)
            await session.commit()

            for chunk in chunks:
                await session.refresh(chunk)

            logger.info(f"Created {len(chunks)} chunks")
            return chunks

    async def get_chunks_for_document(
        self,
        document_id: int,
        organization_id: int,
    ) -> List[KnowledgeBaseChunkModel]:
        """Get all chunks for a document.

        Args:
            document_id: ID of the document
            organization_id: ID of the organization (for authorization)

        Returns:
            List of KnowledgeBaseChunkModel instances
        """
        async with self.async_session() as session:
            query = (
                select(KnowledgeBaseChunkModel)
                .where(
                    KnowledgeBaseChunkModel.document_id == document_id,
                    KnowledgeBaseChunkModel.organization_id == organization_id,
                )
                .order_by(KnowledgeBaseChunkModel.chunk_index)
            )

            result = await session.execute(query)
            return list(result.scalars().all())

    async def search_similar_chunks(
        self,
        query_embedding: List[float],
        organization_id: int,
        limit: int = 5,
        document_ids: Optional[List[int]] = None,
        document_uuids: Optional[List[str]] = None,
        embedding_model: Optional[str] = None,
    ) -> List[dict]:
        """Search for similar chunks using vector similarity.

        Returns top-k most similar chunks without any similarity threshold filtering.
        Filtering and reranking should be done at the application layer.

        Args:
            query_embedding: The query embedding vector
            organization_id: Organization ID for scoping
            limit: Maximum number of results to return
            document_ids: Optional list of document IDs to filter by
            document_uuids: Optional list of document UUIDs to filter by
            embedding_model: Optional embedding model to filter by (for dimension compatibility)

        Returns:
            List of dictionaries with chunk data and similarity scores, ordered by similarity (highest first)
        """
        async with self.async_session() as session:
            # Get the raw connection to execute directly with asyncpg
            # This avoids parameter binding issues with text() and asyncpg
            connection = await session.connection()
            raw_connection = await connection.get_raw_connection()

            # Build WHERE clause conditions (no similarity threshold)
            where_conditions = [
                "c.organization_id = $2",
                "d.is_active = true",
            ]
            params = [
                None,
                organization_id,
                limit,
            ]  # $1 will be embedding_str, $3 is limit
            param_index = 4  # Next available parameter index

            # Add document_ids filter if provided
            if document_ids:
                placeholders = ", ".join(
                    f"${param_index + i}" for i in range(len(document_ids))
                )
                where_conditions.append(f"c.document_id IN ({placeholders})")
                params.extend(document_ids)
                param_index += len(document_ids)

            # Add document_uuids filter if provided
            if document_uuids:
                placeholders = ", ".join(
                    f"${param_index + i}" for i in range(len(document_uuids))
                )
                where_conditions.append(f"d.document_uuid IN ({placeholders})")
                params.extend(document_uuids)
                param_index += len(document_uuids)

            # Add embedding_model filter if provided (for dimension compatibility)
            if embedding_model:
                where_conditions.append(f"c.embedding_model = ${param_index}")
                params.append(embedding_model)
                param_index += 1

            # Build the complete SQL query
            where_clause = " AND ".join(where_conditions)
            query_sql = f"""
                SELECT
                    c.id,
                    c.document_id,
                    c.chunk_text,
                    c.contextualized_text,
                    c.chunk_metadata,
                    c.chunk_index,
                    d.filename,
                    d.document_uuid,
                    1 - (c.embedding <=> $1::vector) as similarity
                FROM knowledge_base_chunks c
                JOIN knowledge_base_documents d ON c.document_id = d.id
                WHERE {where_clause}
                ORDER BY c.embedding <=> $1::vector
                LIMIT $3
            """

            # Convert embedding to string format for PostgreSQL vector type
            embedding_str = "[" + ",".join(map(str, query_embedding)) + "]"
            params[0] = embedding_str  # Set $1

            # Execute query directly with asyncpg
            rows = await raw_connection.driver_connection.fetch(
                query_sql,
                *params,
            )

            # Convert asyncpg records to dictionaries
            return [dict(row) for row in rows]

    async def delete_document(
        self,
        document_uuid: str,
        organization_id: int,
    ) -> bool:
        """Soft delete a document by setting is_active to False.

        This will also cascade delete all chunks via the database foreign key.

        Args:
            document_uuid: The unique document UUID
            organization_id: ID of the organization (for authorization)

        Returns:
            True if document was deleted, False if not found
        """
        async with self.async_session() as session:
            query = select(KnowledgeBaseDocumentModel).where(
                KnowledgeBaseDocumentModel.document_uuid == document_uuid,
                KnowledgeBaseDocumentModel.organization_id == organization_id,
            )

            result = await session.execute(query)
            document = result.scalar_one_or_none()

            if not document:
                return False

            document.is_active = False
            await session.commit()

            logger.info(
                f"Deleted document {document_uuid} for organization {organization_id}"
            )
            return True

    @staticmethod
    def compute_file_hash(file_path: str) -> str:
        """Compute SHA-256 hash of a file.

        Args:
            file_path: Path to the file

        Returns:
            SHA-256 hash as hex string
        """
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    @staticmethod
    def get_mime_type(file_path: str) -> str:
        """Get MIME type based on file extension.

        Args:
            file_path: Path to the file

        Returns:
            MIME type string
        """
        extension = Path(file_path).suffix.lower()
        mime_types = {
            ".pdf": "application/pdf",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".doc": "application/msword",
            ".txt": "text/plain",
            ".html": "text/html",
            ".md": "text/markdown",
        }
        return mime_types.get(extension, "application/octet-stream")
