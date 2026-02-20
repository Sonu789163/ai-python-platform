"""
Synchronous Ingestion Pipeline.
Handles document processing and vector storage directly.
"""
import time
import asyncio
from typing import Dict, Any, Optional
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

    async def process(
        self, 
        file_url: str, 
        file_type: str, 
        job_id: str, 
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Processes document immediately and returns result.
        """
        start_time = time.time()
        metadata = metadata or {}
        doc_type = metadata.get("doc_type", "drhp").lower()
        filename = metadata.get("filename", "document.pdf")
        
        logger.info("Starting direct document ingestion", job_id=job_id, filename=filename)
        
        try:
            # 1. Download document
            resp = requests.get(file_url, timeout=30)
            resp.raise_for_status()
            file_content = resp.content
            
            # 2. Extract and Clean
            extraction_result = self.extraction.extract_text(file_content, file_type)
            text = extraction_result["text"]
            
            # 3. Chunk
            chunk_metadata = {
                "source": file_url,
                "job_id": job_id,
                "documentName": filename,
                "documentId": metadata.get("documentId", ""),
                "domain": metadata.get("domain", ""),
                "domainId": metadata.get("domainId", ""),
                "type": doc_type.upper() if doc_type else "DRHP"
            }
            
            chunks = self.chunking.chunk_with_metadata(
                text,
                metadata=chunk_metadata
            )
            
            if not chunks:
               logger.warning("No text extracted or chunks created", job_id=job_id)
               return {"success": False, "error": "No text extracted from document"}

            # 4. Embed
            chunks_with_embeddings = await self.embedding.embed_chunks(chunks)
            
            # 5. Store in Pinecone (Unified Index)
            # Both DRHP and RHP are stored in the same index: drhp-summarizer
            index_name = settings.PINECONE_DRHP_INDEX
            host = settings.PINECONE_DRHP_HOST
                
            pinecone_res = vector_store_service.upsert_chunks(
                chunks=chunks_with_embeddings,
                index_name=index_name,
                namespace=filename,
                host=host
            )
            
            # 6. MongoDB record
            try:
                if not mongodb.sync_db:
                    mongodb.connect_sync()
                collection = mongodb.get_sync_collection("document_processing")
                collection.insert_one({
                    "job_id": job_id,
                    "filename": filename,
                    "doc_type": doc_type,
                    "status": "completed",
                    "pinecone_count": pinecone_res.get("upserted_count", 0),
                    "created_at": time.time()
                })
            except Exception as mongo_err:
                logger.warning("MongoDB record skipped", error=str(mongo_err))

            # 7. Notify Backend
            backend_notifier.notify_status(
                job_id=job_id,
                status="completed",
                namespace=filename
            )
            
            execution_time = time.time() - start_time
            logger.info("Direct ingestion successful", job_id=job_id, duration=execution_time)
            
            return {
                "success": True,
                "filename": filename,
                "chunk_count": len(chunks),
                "pinecone": pinecone_res,
                "duration": execution_time
            }
            
        except Exception as e:
            logger.error("Direct ingestion failed", error=str(e), job_id=job_id)
            backend_notifier.notify_status(
                job_id=job_id,
                status="failed",
                namespace=filename,
                error={"message": str(e)}
            )
            raise

ingestion_pipeline = IngestionPipeline()
