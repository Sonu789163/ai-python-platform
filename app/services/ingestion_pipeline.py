"""
Synchronous Ingestion Pipeline.
Replicates the n8n embedding workflow exactly:

  PDF → pdfplumber section extraction
     → per-section cleaning (already done in ExtractionService)
     → RecursiveCharacterTextSplitter (chunkSize=4000, overlap=800)
     → OpenAI text-embedding-3-large (batch 50)
     → Pinecone upsert with metadata:
           documentName, documentId, domain, domainId, type,
           sectionName, sectionPageRange   ← NEW (n8n Default Data Loader3)
"""
import time
import asyncio
from typing import Dict, Any, Optional, List
import requests

from app.services.extraction import ExtractionService
from app.services.chunking import ChunkingService
from app.services.embedding import EmbeddingService
from app.services.vector_store import vector_store_service
from app.services.backend_notifier import backend_notifier
from app.db.mongo import mongodb
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class IngestionPipeline:
    def __init__(self):
        self.extraction = ExtractionService()
        self.chunking = ChunkingService()
        self.embedding = EmbeddingService()

    # ----------------------------------------------------------------------- #
    # Internal: process one section's text into Pinecone chunks
    # ----------------------------------------------------------------------- #
    async def _process_section(
        self,
        section: Dict[str, Any],
        base_metadata: Dict[str, Any],
        index_name: str,
        host: str,
        namespace: str,
        section_offset: int,
    ) -> int:
        """
        Chunk → embed → upsert one section.
        Returns number of vectors upserted.
        """
        text = section.get("text", "").strip()
        if len(text) < 20:
            return 0

        section_name = section.get("sectionName", "General")
        section_range = section.get("sectionStart&End", "")

        # Build per-section metadata (matches n8n Default Data Loader3 metadataValues)
        chunk_metadata = {
            **base_metadata,
            "sectionName": section_name,
            "sectionPageRange": section_range,
        }

        chunks = self.chunking.chunk_with_metadata(text, metadata=chunk_metadata)
        if not chunks:
            return 0

        # Re-index chunk_index to be globally unique across all sections
        for i, chunk in enumerate(chunks):
            chunk["chunk_index"] = section_offset + i

        # Embed
        chunks_with_embeddings = await self.embedding.embed_chunks(chunks)

        # Build Pinecone vectors manually so we include sectionName/sectionPageRange
        index = vector_store_service.get_index(index_name, host=host)
        vectors = []
        for chunk in chunks_with_embeddings:
            meta = chunk.get("metadata", {})
            vector_id = f"{namespace}_{chunk['chunk_index']}"
            vectors.append(
                {
                    "id": vector_id,
                    "values": chunk["embedding"],
                    "metadata": {
                        "text": chunk["chunk_text"],
                        "chunk_index": chunk["chunk_index"],
                        "documentName": meta.get("documentName", namespace),
                        "documentId": meta.get("documentId", ""),
                        "domain": meta.get("domain", ""),
                        "domainId": meta.get("domainId", ""),
                        "type": meta.get("type", "DRHP"),
                        "sectionName": meta.get("sectionName", ""),
                        "sectionPageRange": meta.get("sectionPageRange", ""),
                    },
                }
            )

        # Upsert in batches of 50 (matches n8n embeddingBatchSize: 50)
        batch_size = 60
        total_upserted = 0
        for i in range(0, len(vectors), batch_size):
            batch = vectors[i : i + batch_size]
            try:
                resp = index.upsert(vectors=batch, namespace="")
                total_upserted += getattr(resp, "upserted_count", len(batch))
            except Exception as e:
                logger.error(
                    "Batch upsert failed",
                    section=section_name,
                    batch_start=i,
                    error=str(e),
                )
                raise

        logger.info(
            "Section embedded and upserted",
            section=section_name,
            chunks=len(vectors),
            upserted=total_upserted,
        )
        return total_upserted

    # ----------------------------------------------------------------------- #
    # Public entry-point
    # ----------------------------------------------------------------------- #
    async def process(
        self,
        file_url: str,
        file_type: str,
        job_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Full ingestion pipeline matching the n8n embedding workflow.

        n8n flow replicated:
          Webhook → parse PDF (pdfplumber, section-wise)
                 → Cleaned text3 (cleaning already done in ExtractionService)
                 → Default Data Loader3 (metadata attachment)
                 → Recursive Character Text Splitter (4000 / 800)
                 → Pinecone Vector Store (text-embedding-3-large, batch 50)
        """
        start_time = time.time()
        metadata = metadata or {}
        doc_type = metadata.get("doc_type", "drhp").upper()
        filename = metadata.get("filename", "document.pdf")

        logger.info(
            "Starting section-wise ingestion pipeline",
            job_id=job_id,
            filename=filename,
            doc_type=doc_type,
        )

        try:
            # 1. Download document
            resp = requests.get(file_url, timeout=60)
            resp.raise_for_status()
            file_content = resp.content

            # 2. Extract section-wise (pdfplumber + TOC + table→Markdown)
            sections: List[Dict[str, Any]] = (
                self.extraction.extract_sections_from_pdf(file_content)
            )

            if not sections:
                logger.warning("No sections extracted from document", job_id=job_id)
                return {"success": False, "error": "No text extracted from document"}

            logger.info(
                "Sections extracted",
                job_id=job_id,
                section_count=len(sections),
            )

            # 3. Base metadata (everything except section-level fields)
            base_metadata = {
                "source": file_url,
                "job_id": job_id,
                "documentName": filename,
                "documentId": metadata.get("documentId", ""),
                "domain": metadata.get("domain", ""),
                "domainId": metadata.get("domainId", ""),
                "type": doc_type,
            }

            # 4. Pinecone index
            index_name = settings.PINECONE_DRHP_INDEX
            host = settings.PINECONE_DRHP_HOST

            # 5. Chunk → embed → upsert each section
            total_upserted = 0
            chunk_offset = 0
            for section in sections:
                n = await self._process_section(
                    section=section,
                    base_metadata=base_metadata,
                    index_name=index_name,
                    host=host,
                    namespace=filename,
                    section_offset=chunk_offset,
                )
                total_upserted += n
                chunk_offset += n  # keep chunk_index globally unique

            # 6. MongoDB record
            try:
                if not mongodb.sync_db:
                    mongodb.connect_sync()
                collection = mongodb.get_sync_collection("document_processing")
                collection.insert_one(
                    {
                        "job_id": job_id,
                        "filename": filename,
                        "doc_type": doc_type,
                        "status": "completed",
                        "sections_processed": len(sections),
                        "pinecone_count": total_upserted,
                        "created_at": time.time(),
                    }
                )
            except Exception as mongo_err:
                logger.warning("MongoDB record skipped", error=str(mongo_err))

            # 7. Notify backend
            backend_notifier.notify_status(
                job_id=job_id,
                status="completed",
                namespace=filename,
            )

            execution_time = time.time() - start_time
            logger.info(
                "Ingestion pipeline completed",
                job_id=job_id,
                sections=len(sections),
                total_vectors=total_upserted,
                duration=execution_time,
            )

            return {
                "success": True,
                "filename": filename,
                "sections_processed": len(sections),
                "total_vectors": total_upserted,
                "duration": execution_time,
            }

        except Exception as e:
            logger.error("Ingestion pipeline failed", error=str(e), job_id=job_id)
            backend_notifier.notify_status(
                job_id=job_id,
                status="failed",
                namespace=filename,
                error={"message": str(e)},
            )
            raise


ingestion_pipeline = IngestionPipeline()
