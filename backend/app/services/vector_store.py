"""宏曦标书 - Vector Store Service (ChromaDB).

Semantic chapter search powered by ChromaDB + BGE Chinese embeddings.
Supports graceful degradation when dependencies are not installed.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import logging
import re
from typing import List, Dict, Optional, Any

from app.config import settings

logger = logging.getLogger(__name__)


class VectorStore:
    """Singleton vector store backed by ChromaDB with BGE Chinese embeddings.

    Provides:
    - Chinese-aware paragraph chunking
    - Chapter / project indexing
    - Semantic similarity search with optional project filtering
    - Graceful degradation when dependencies are unavailable
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def _ensure_initialized(self) -> None:
        """Lazy-init ChromaDB client, collection, and embedding function.

        Sets self._available = False on any failure so all public methods
        become safe no-ops.
        """
        if self._initialized:
            return
        self._initialized = True
        self._available = False
        self._client = None
        self._collection = None
        self._embedding_fn = None

        if not settings.VECTOR_STORE_ENABLED:
            logger.info("Vector store disabled via VECTOR_STORE_ENABLED config")
            return

        # --- ChromaDB PersistentClient ---
        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings

            self._client = chromadb.PersistentClient(
                path=settings.CHROMA_PERSIST_DIR,
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            logger.info(
                "ChromaDB client ready (persist_dir=%s)", settings.CHROMA_PERSIST_DIR
            )
        except Exception as exc:
            logger.warning("ChromaDB unavailable: %s", exc)
            return

        # --- Embedding function ---
        try:
            from chromadb.utils.embedding_functions import (
                SentenceTransformerEmbeddingFunction,
            )

            self._embedding_fn = SentenceTransformerEmbeddingFunction(
                model_name=settings.EMBEDDING_MODEL,
            )
            logger.info("Embedding model loaded: %s", settings.EMBEDDING_MODEL)
        except Exception as exc:
            logger.warning("SentenceTransformer embedding unavailable: %s", exc)
            # ChromaDB will use its default (all-MiniLM-L6-v2) as fallback
            self._embedding_fn = None

        # --- Collection ---
        try:
            self._collection = self._client.get_or_create_collection(
                name=settings.CHROMA_COLLECTION_NAME,
                embedding_function=self._embedding_fn,
                metadata={"hnsw:space": "cosine"},
            )
            logger.info(
                "ChromaDB collection '%s' ready", settings.CHROMA_COLLECTION_NAME
            )
        except Exception as exc:
            logger.warning("Failed to access ChromaDB collection: %s", exc)
            return

        self._available = True
        logger.info("VectorStore initialized successfully")

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Return True if the vector store is ready for use."""
        self._ensure_initialized()
        return self._available

    # ------------------------------------------------------------------
    # Text chunking (Chinese-aware)
    # ------------------------------------------------------------------

    @staticmethod
    def chunk_text(
        text: str, chunk_size: int = 500, chunk_overlap: int = 100
    ) -> List[str]:
        """Split Chinese text into overlapping chunks by paragraph boundaries.

        Strategy:
        1. Split on double-newline (paragraphs).
        2. Merge short paragraphs into the current chunk.
        3. Once a chunk reaches *chunk_size* characters, emit it and start
           the next chunk with *chunk_overlap* characters of overlap.

        Returns an empty list for empty / whitespace-only input.
        """
        if not text or not text.strip():
            return []

        # Split into paragraphs on consecutive newlines (Chinese doc style)
        paragraphs = re.split(r"\n\s*\n", text.strip())
        # Further split very long single paragraphs on single newlines
        refined: List[str] = []
        for para in paragraphs:
            if not para.strip():
                continue
            if len(para) > chunk_size * 2:
                refined.extend(
                    p.strip()
                    for p in re.split(r"\n", para)
                    if p.strip()
                )
            else:
                refined.append(para.strip())

        chunks: List[str] = []
        current = ""

        for para in refined:
            if not current:
                current = para
            elif len(current) + len(para) + 1 <= chunk_size:
                current += "\n" + para
            else:
                # Emit current chunk
                chunks.append(current)
                # Start new chunk with overlap from tail of previous chunk
                if chunk_overlap > 0 and len(current) > chunk_overlap:
                    current = current[-chunk_overlap:] + "\n" + para
                else:
                    current = para

        if current.strip():
            chunks.append(current)

        return chunks

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def index_chapter(
        self,
        chapter_id: str,
        project_id: str,
        title: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Chunk and index a single chapter into the vector store.

        Parameters
        ----------
        chapter_id : str
            Unique database ID of the chapter.
        project_id : str
            Parent project ID (stored as metadata for filtering).
        title : str
            Chapter title.
        content : str
            Full chapter text.
        metadata : dict or None
            Extra key-value pairs attached to every chunk.

        Returns
        -------
        bool
            True on success, False on failure.
        """
        self._ensure_initialized()
        if not self._available:
            return False

        if not content or not content.strip():
            logger.debug("Skipping empty chapter %s", chapter_id)
            return False

        try:
            chunks = self.chunk_text(content)
            if not chunks:
                logger.debug("No chunks produced for chapter %s", chapter_id)
                return False

            total = len(chunks)
            ids: List[str] = []
            documents: List[str] = []
            metadatas: List[Dict[str, Any]] = []

            base_meta: Dict[str, Any] = {
                "project_id": project_id,
                "chapter_id": chapter_id,
                "title": title,
                "total_chunks": total,
            }
            if metadata:
                base_meta.update(metadata)

            for i, chunk_text in enumerate(chunks):
                ids.append(f"{chapter_id}_chunk_{i}")
                documents.append(chunk_text)
                meta = dict(base_meta)
                meta["chunk_index"] = i
                metadatas.append(meta)

            self._collection.upsert(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
            )
            logger.info(
                "Indexed chapter %s (%d chunks)", chapter_id, total
            )
            return True

        except Exception as exc:
            logger.error("Failed to index chapter %s: %s", chapter_id, exc)
            return False

    def index_project(
        self,
        project_id: str,
        chapters: List[Dict[str, Any]],
    ) -> int:
        """Index every chapter in a project.

        Parameters
        ----------
        project_id : str
            Parent project ID.
        chapters : list[dict]
            Each dict must have keys: id, title, content.
            May optionally include a 'metadata' key.

        Returns
        -------
        int
            Total number of chunks indexed across all chapters.
        """
        self._ensure_initialized()
        if not self._available:
            return 0

        indexed_count = 0
        for ch in chapters:
            if not ch.get("content"):
                continue
            ok = self.index_chapter(
                chapter_id=ch["id"],
                project_id=project_id,
                title=ch.get("title", ""),
                content=ch["content"],
                metadata=ch.get("metadata"),
            )
            if ok:
                indexed_count += 1

        logger.info(
            "Indexed project %s (%d chapters processed)", project_id, len(chapters)
        )
        return indexed_count

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def search_similar(
        self,
        query: str,
        n_results: int = 5,
        filter_project_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Semantic search for similar chapter chunks.

        Parameters
        ----------
        query : str
            Search query text.
        n_results : int
            Max number of results to return.
        filter_project_id : str or None
            If provided, EXCLUDE chunks belonging to this project
            (useful for cross-project retrieval during AI generation).

        Returns
        -------
        list[dict]
            Each result has keys: content, metadata, distance.
            Returns empty list if store is unavailable or collection is empty.
        """
        self._ensure_initialized()
        if not self._available:
            return []

        try:
            count = self._collection.count()
            if count == 0:
                logger.debug("Collection is empty, returning no results")
                return []

            where_filter = None
            if filter_project_id:
                # ChromaDB where clause: exclude matching project_id
                where_filter = {
                    "project_id": {"$ne": filter_project_id}
                }

            results = self._collection.query(
                query_texts=[query],
                n_results=min(n_results, count),
                where=where_filter,
                include=["documents", "metadatas", "distances"],
            )

            formatted: List[Dict[str, Any]] = []
            if results and results["ids"] and results["ids"][0]:
                for i, chunk_id in enumerate(results["ids"][0]):
                    formatted.append(
                        {
                            "content": results["documents"][0][i],
                            "metadata": results["metadatas"][0][i]
                            if results["metadatas"]
                            else {},
                            "distance": results["distances"][0][i]
                            if results["distances"]
                            else None,
                        }
                    )

            return formatted

        except Exception as exc:
            logger.error("Search failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Collection management
    # ------------------------------------------------------------------

    def get_collection_stats(self) -> Dict[str, Any]:
        """Return basic statistics about the collection.

        Returns
        -------
        dict
            Keys: total_chunks, unique_projects, unique_chapters.
            All values are 0 when the store is unavailable.
        """
        self._ensure_initialized()
        if not self._available:
            return {"total_chunks": 0, "unique_projects": 0, "unique_chapters": 0}

        try:
            total = self._collection.count()
            unique_projects = 0
            unique_chapters = 0

            if total > 0:
                # Fetch all metadata to count distinct values
                all_data = self._collection.get(include=["metadatas"])
                if all_data and all_data["metadatas"]:
                    projects = set()
                    chapters = set()
                    for meta in all_data["metadatas"]:
                        if meta.get("project_id"):
                            projects.add(meta["project_id"])
                        if meta.get("chapter_id"):
                            chapters.add(meta["chapter_id"])
                    unique_projects = len(projects)
                    unique_chapters = len(chapters)

            return {
                "total_chunks": total,
                "unique_projects": unique_projects,
                "unique_chapters": unique_chapters,
            }
        except Exception as exc:
            logger.error("Failed to get collection stats: %s", exc)
            return {"total_chunks": 0, "unique_projects": 0, "unique_chapters": 0}

    def delete_project(self, project_id: str) -> bool:
        """Remove all chunks belonging to a project from the collection.

        Parameters
        ----------
        project_id : str
            The project whose chunks should be removed.

        Returns
        -------
        bool
            True if deletion succeeded, False otherwise.
        """
        self._ensure_initialized()
        if not self._available:
            return False

        try:
            # Find all document IDs for this project
            existing = self._collection.get(
                where={"project_id": project_id},
                include=[],
            )
            if existing and existing["ids"]:
                self._collection.delete(ids=existing["ids"])
                logger.info(
                    "Deleted %d chunks for project %s",
                    len(existing["ids"]),
                    project_id,
                )
            else:
                logger.debug("No chunks found for project %s", project_id)
            return True
        except Exception as exc:
            logger.error("Failed to delete project %s: %s", exc, project_id)
            return False

    def rebuild_index(self, chapters: List[Dict[str, Any]]) -> int:
        """Delete all existing chunks and re-index from a full chapter list.

        Parameters
        ----------
        chapters : list[dict]
            Every chapter across all projects. Each dict must have:
            id, title, content, project_id.

        Returns
        -------
        int
            Total chapters indexed.
        """
        self._ensure_initialized()
        if not self._available:
            return 0

        try:
            # Delete all existing data
            all_ids = self._collection.get(include=[])
            if all_ids and all_ids["ids"]:
                self._collection.delete(ids=all_ids["ids"])
                logger.info("Cleared %d existing chunks for rebuild", len(all_ids["ids"]))

            # Re-index all chapters
            indexed = 0
            for ch in chapters:
                if not ch.get("content"):
                    continue
                ok = self.index_chapter(
                    chapter_id=ch["id"],
                    project_id=ch.get("project_id", ""),
                    title=ch.get("title", ""),
                    content=ch["content"],
                )
                if ok:
                    indexed += 1

            logger.info("Rebuild complete: %d chapters indexed", indexed)
            return indexed

        except Exception as exc:
            logger.error("Rebuild failed: %s", exc)
            return 0


# Singleton instance — import this throughout the app
vector_store = VectorStore()
