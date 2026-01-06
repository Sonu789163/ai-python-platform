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
        
        # Store error in MongoDB
        if not mongodb.sync_db:
            mongodb.connect_sync()
        collection = mongodb.get_sync_collection("document_processing")
        collection.update_one(
            {"job_id": job_id},
            {"$set": {"status": "failed", "error": error_msg, "error_stack": error_stack}}
        )
        
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


@celery_app.task(name="generate_summary", bind=True)
def generate_summary(
    self,
    namespace: str,
    doc_type: str,
    job_id: str,
    metadata: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Generate a full RHP/DRHP summary using the 2-stage agent pipeline.
    """
    from app.services.summarization.pipeline import summary_pipeline
    
    start_time = time.time()
    bound_logger = log_job_start(
        logger,
        job_id,
        "summary_generation",
        namespace=namespace,
        doc_type=doc_type
    )
    
    try:
        # Run the async pipeline in a sync context for Celery
        result = asyncio.run(summary_pipeline.generate(namespace, doc_type))
        
        # Notify Backend of Success
        backend_notifier.notify_status(
            job_id=job_id,
            status="completed",
            namespace=namespace,
            result=result
        )
        
        execution_time = time.time() - start_time
        log_job_complete(bound_logger, job_id, execution_time)
        
        return {
            "job_id": job_id,
            "status": "success",
            "namespace": namespace,
            "duration": execution_time,
            "result": result
        }
    
    except Exception as e:
        execution_time = time.time() - start_time
        log_job_error(bound_logger, job_id, e, execution_time)
        
        # Notify Backend of Failure
        backend_notifier.notify_status(
            job_id=job_id,
            status="failed",
            namespace=namespace,
            error={"message": str(e)}
        )
        raise
@celery_app.task(name="generate_comparison", bind=True)
def generate_comparison(
    self,
    drhp_namespace: str,
    rhp_namespace: str,
    job_id: str,
    metadata: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Generate a comparison report between DRHP and RHP.
    """
    from app.services.comparison.pipeline import comparison_pipeline
    
    start_time = time.time()
    metadata = metadata or {}
    authorization = metadata.get("authorization", "")
    session_id = metadata.get("sessionId", "")
    drhp_id = metadata.get("drhpDocumentId", "")
    rhp_id = metadata.get("rhpDocumentId", "")
    domain = metadata.get("domain", "")
    domain_id = metadata.get("domainId", "")
    
    bound_logger = log_job_start(
        logger,
        job_id,
        "comparison_generation",
        drhp=drhp_namespace,
        rhp=rhp_namespace
    )
    
    try:
        # 1. Run Pipeline
        result = asyncio.run(comparison_pipeline.compare(
            drhp_namespace=drhp_namespace,
            rhp_namespace=rhp_namespace
        ))
        
        if result["status"] == "success":
            # 2. Create Report in Backend
            backend_notifier.create_report(
                drhp_namespace=drhp_namespace,
                drhp_id=drhp_id,
                title=f"Comparison: {drhp_namespace} vs {rhp_namespace}",
                content=result["html"],
                session_id=session_id,
                rhp_namespace=rhp_namespace,
                rhp_id=rhp_id,
                domain=domain,
                domain_id=domain_id,
                authorization=authorization
            )
            
            # 3. Update Status
            backend_notifier.update_report_status(
                job_id=job_id,
                namespace=drhp_namespace,
                status="success",
                authorization=authorization
            )
        else:
            raise Exception(result.get("message", "Comparison failed"))

        execution_time = time.time() - start_time
        log_job_complete(bound_logger, job_id, execution_time)
        
        return {
            "job_id": job_id,
            "status": "success",
            "duration": execution_time,
            "markdown": result.get("markdown"),
            "html": result.get("html"),
            "usage": result.get("usage")
        }
    
    except Exception as e:
        execution_time = time.time() - start_time
        log_job_error(bound_logger, job_id, e, execution_time)
        
        # Notify Backend of Failure
        backend_notifier.update_report_status(
            job_id=job_id,
            namespace=drhp_namespace,
            status="failed",
            error={"message": str(e), "stack": traceback.format_exc(), "timestamp": str(time.time())},
            authorization=authorization
        )
        raise
