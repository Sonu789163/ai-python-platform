"""
Document pipeline worker.
Complete AI pipeline for document processing: extraction, chunking, embedding.
"""
import time
from typing import Dict, Any
from celery import Task

from app.workers.celery_app import celery_app
from app.services.extraction import extraction_service
from app.services.chunking import chunking_service
from app.services.embedding import embedding_service
from app.db.mongo import mongodb
from app.core.logging import get_logger, log_job_start, log_job_complete, log_job_error

logger = get_logger(__name__)


import asyncio
import time
import traceback
from typing import Dict, Any
from celery import Task

from app.workers.celery_app import celery_app
from app.services.extraction import extraction_service
from app.services.chunking import chunking_service
from app.services.embedding import embedding_service
from app.services.vector_store import vector_store_service
from app.services.backend_notifier import backend_notifier
from app.db.mongo import mongodb
from app.core.config import settings
from app.core.logging import get_logger, log_job_start, log_job_complete, log_job_error

logger = get_logger(__name__)


@celery_app.task(bind=True, name="process_document", autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def process_document(
    self,
    file_url: str,
    file_type: str,
    job_id: str,
    metadata: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Process document through the Data Ingestion Pipeline (Matched to n8n workflow).
    """
    start_time = time.time()
    metadata = metadata or {}
    doc_type = metadata.get("doc_type", "drhp").lower()  # drhp or rhp
    filename = metadata.get("filename", "document.pdf")
    
    bound_logger = log_job_start(logger, job_id, "data_ingestion", file_type=file_type, doc_type=doc_type)
    
    try:
        # Determine Pinecone Index
        if doc_type == "rhp":
            index_name = settings.PINECONE_RHP_INDEX
        else:
            index_name = settings.PINECONE_DRHP_INDEX
            
        # Stage 1: Retrieve document (In prod, you would download from S3/Vercel Blob)
        bound_logger.info("Retrieving document", file_url=file_url)
        import requests
        resp = requests.get(file_url, timeout=30)
        resp.raise_for_status()
        file_content = resp.content
        
        # Stage 2: Extract and Clean Text (Matched to n8n "Cleaned text1" logic)
        bound_logger.info("Extracting and cleaning text")
        extraction_result = extraction_service.extract_text(file_content, file_type)
        text = extraction_result["text"]
        
        # Stage 3: Chunk text (Matched to n8n "Recursive Character Text Splitter" 4000/800)
        bound_logger.info("Splitting text into chunks")
        chunks = chunking_service.chunk_with_metadata(
            text,
            metadata={"source": file_url, "doc_type": doc_type, "job_id": job_id}
        )
        
        # Stage 4: Generate Embeddings (Matched to n8n "text-embedding-3-large")
        bound_logger.info("Generating OpenAI embeddings", count=len(chunks))
        # Embedding service call (sync wrapper around LangChain)
        chunks_with_embeddings = asyncio.run(embedding_service.embed_chunks(chunks))
        
        # Stage 5: Upsert to Pinecone
        bound_logger.info("Upserting to Pinecone", index=index_name)
        upsert_res = vector_store_service.upsert_chunks(
            chunks=chunks_with_embeddings,
            index_name=index_name,
            namespace=filename
        )
        
        # Stage 6: Store processing record in MongoDB
        if not mongodb.sync_db:
            mongodb.connect_sync()
        collection = mongodb.get_sync_collection("document_processing")
        
        collection.insert_one({
            "job_id": job_id,
            "filename": filename,
            "doc_type": doc_type,
            "index_name": index_name,
            "char_count": len(text),
            "chunk_count": len(chunks),
            "status": "completed",
            "created_at": time.time()
        })
        
        # Stage 7: Notify Backend (Matched to n8n "Respond to Webhook9")
        backend_notifier.notify_status(
            job_id=job_id,
            status="completed",
            namespace=filename
        )
        
        execution_time = time.time() - start_time
        log_job_complete(bound_logger, job_id, execution_time, status="success")
        
        return {
            "success": True,
            "status": "completed",
            "namespace": filename,
            "message": "Document processed and stored successfully"
        }
    
    except Exception as e:
        execution_time = time.time() - start_time
        error_msg = str(e)
        error_stack = traceback.format_exc()
        
        log_job_error(bound_logger, job_id, e, execution_time)
        
        # Notify Backend of Failure (Matched to n8n "Send Error to Backend3")
        backend_notifier.notify_status(
            job_id=job_id,
            status="failed",
            namespace=filename,
            error={
                "message": error_msg,
                "stack": error_stack
            }
        )
        
        raise
    
    except Exception as e:
        execution_time = time.time() - start_time
        log_job_error(bound_logger, job_id, e, execution_time)
        
        # Store error in MongoDB
        if mongodb.sync_db:
            collection = mongodb.get_sync_collection("processed_documents")
            error_record = {
                "job_id": job_id,
                "file_url": file_url,
                "file_type": file_type,
                "status": "failed",
                "error": str(e),
                "error_type": type(e).__name__,
                "execution_time": execution_time,
                "created_at": time.time()
            }
            collection.insert_one(error_record)
        
        # Re-raise for Celery retry mechanism
        raise


@celery_app.task(name="process_news_article")
def process_news_article(
    article_url: str,
    job_id: str,
    metadata: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Process news article pipeline.
    
    Args:
        article_url: URL to news article
        job_id: Unique job identifier
        metadata: Additional metadata
    
    Returns:
        Dict containing processing results
    """
    start_time = time.time()
    bound_logger = log_job_start(logger, job_id, "news_article_pipeline", article_url=article_url)
    
    try:
        # Placeholder implementation
        bound_logger.info("Processing news article", url=article_url)
        
        result = {
            "job_id": job_id,
            "status": "success",
            "article_url": article_url,
            "execution_time": time.time() - start_time
        }
        
        log_job_complete(bound_logger, job_id, result["execution_time"])
        return result
    
    except Exception as e:
        execution_time = time.time() - start_time
        log_job_error(bound_logger, job_id, e, execution_time)
        raise


@celery_app.task(name="generate_summary")
def generate_summary(
    text: str,
    job_id: str,
    summary_type: str = "brief"
) -> Dict[str, Any]:
    """
    Generate text summary.
    
    Args:
        text: Input text to summarize
        job_id: Unique job identifier
        summary_type: Type of summary (brief, detailed, etc.)
    
    Returns:
        Dict containing summary
    """
    start_time = time.time()
    bound_logger = log_job_start(
        logger,
        job_id,
        "summary_generation",
        summary_type=summary_type,
        text_length=len(text)
    )
    
    try:
        # Placeholder implementation
        bound_logger.info("Generating summary")
        
        summary = f"Summary of {len(text)} characters (placeholder)"
        
        result = {
            "job_id": job_id,
            "status": "success",
            "summary": summary,
            "summary_type": summary_type,
            "execution_time": time.time() - start_time
        }
        
        log_job_complete(bound_logger, job_id, result["execution_time"])
        return result
    
    except Exception as e:
        execution_time = time.time() - start_time
        log_job_error(bound_logger, job_id, e, execution_time)
        raise
