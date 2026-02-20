"""
Job intake API endpoints.
Handles job submission from Node.js backend and returns job_id immediately.
"""
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
import uuid

from app.workers.celery_app import celery_app
from app.core.logging import get_logger
from app.services.ingestion_pipeline import ingestion_pipeline

logger = get_logger(__name__)
router = APIRouter(prefix="/jobs", tags=["jobs"])


# Request/Response Models
class DocumentJobRequest(BaseModel):
    """Document processing job request."""
    file_url: str = Field(..., description="URL or path to document")
    file_type: str = Field(..., description="Document type (pdf, docx, txt)")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Additional metadata")


class NewsJobRequest(BaseModel):
    """News article processing job request."""
    article_url: str = Field(..., description="URL to news article")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Additional metadata")


class SummaryJobRequest(BaseModel):
    """Summary generation job request."""
    namespace: str = Field(..., description="The filename/namespace in Pinecone to summarize")
    doc_type: str = Field(default="drhp", description="Type of document (drhp or rhp)")
    authorization: Optional[str] = None
    documentId: Optional[str] = None
    domainId: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Additional metadata")


class ComparisonJobRequest(BaseModel):
    """Comparison generation job request."""
    drhpNamespace: str
    rhpNamespace: str
    drhpDocumentId: str
    rhpDocumentId: str
    sessionId: str
    domain: Optional[str] = None
    domainId: Optional[str] = None
    authorization: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Additional metadata")


class JobResponse(BaseModel):
    """Job submission response."""
    job_id: str = Field(..., description="Unique job identifier")
    status: str = Field(..., description="Job status")
    message: str = Field(..., description="Response message")


class JobStatusResponse(BaseModel):
    """Job status check response."""
    job_id: str
    state: str
    result: Optional[Any] = None
    error: Optional[str] = None


@router.post("/document", status_code=status.HTTP_200_OK)
async def submit_document_job(request: DocumentJobRequest):
    """
    Direct Document Ingestion (Synchronous).
    Extracts, cleans, chunks, and embeds document into Pinecone.
    """
    job_id = str(uuid.uuid4())
    
    try:
        logger.info("Processing document ingestion request", job_id=job_id, file_url=request.file_url)
        
        # Process immediately
        result = await ingestion_pipeline.process(
            file_url=request.file_url,
            file_type=request.file_type,
            job_id=job_id,
            metadata=request.metadata
        )
        
        return {
            "job_id": job_id,
            "status": "success",
            "message": "Document processed and stored in Pinecone successfully",
            "details": result
        }
    
    except Exception as e:
        logger.error("Failed document ingestion", job_id=job_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ingestion failed: {str(e)}"
        )


@router.delete("/document", status_code=status.HTTP_200_OK)
async def delete_document_vectors(
    namespace: str,
    doc_type: str = "drhp"
):
    """
    Delete document vectors from Pinecone.
    """
    try:
        from app.core.config import settings
        from app.services.vector_store import vector_store_service
        
        # Determine Pinecone Index (Single Index Strategy)
        index_name = settings.PINECONE_DRHP_INDEX
        host = settings.PINECONE_DRHP_HOST
            
        vector_store_service.delete_vectors(index_name, namespace, host=host)
        
        return {
            "status": "success",
            "message": f"Vectors for {namespace} deleted from {index_name}"
        }
    except Exception as e:
        logger.error("Failed to delete vectors", error=str(e), namespace=namespace)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/news", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def submit_news_job(request: NewsJobRequest) -> JobResponse:
    """
    Submit news article processing job.
    
    Args:
        request: News job request
    
    Returns:
        JobResponse with job_id
    """
    try:
        job_id = str(uuid.uuid4())
        
        logger.info(
            "News job submitted",
            job_id=job_id,
            article_url=request.article_url
        )
        
        celery_app.send_task(
            "process_news_article",
            args=[request.article_url, job_id, request.metadata],
            task_id=job_id
        )
        
        return JobResponse(
            job_id=job_id,
            status="accepted",
            message="News processing job enqueued successfully"
        )
    
    except Exception as e:
        logger.error("Failed to submit news job", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to enqueue job: {str(e)}"
        )


@router.post("/summary", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def submit_summary_job(request: SummaryJobRequest) -> JobResponse:
    """
    Submit summary generation job.
    """
    try:
        job_id = str(uuid.uuid4())
        
        # Prepare metadata - prioritize top-level fields but fallback to nested metadata
        task_metadata = request.metadata or {}
        
        # Ensure critical fields for BackendNotifier are present 
        # (Node.js backend often nests these in 'metadata' object)
        final_auth = request.authorization or task_metadata.get("authorization")
        final_doc_id = request.documentId or task_metadata.get("documentId")
        final_domain_id = request.domainId or task_metadata.get("domainId")
        
        task_metadata.update({
            "authorization": final_auth,
            "documentId": final_doc_id,
            "domainId": final_domain_id
        })
        
        logger.info(
            "Summary job submitted",
            job_id=job_id,
            namespace=request.namespace,
            doc_type=request.doc_type
        )
        
        celery_app.send_task(
            "generate_summary",
            args=[request.namespace, request.doc_type, job_id, task_metadata],
            task_id=job_id
        )
        
        return JobResponse(
            job_id=job_id,
            status="accepted",
            message="Summary generation job enqueued successfully"
        )
    
    except Exception as e:
        logger.error("Failed to submit summary job", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to enqueue job: {str(e)}"
        )


@router.post("/comparison", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def submit_comparison_job(request: ComparisonJobRequest) -> JobResponse:
    """
    Submit DRHP vs RHP comparison job.
    """
    try:
        job_id = str(uuid.uuid4())
        
        # Prepare metadata for worker
        worker_metadata = request.metadata or {}
        
        final_auth = request.authorization or worker_metadata.get("authorization")
        final_domain_id = request.domainId or worker_metadata.get("domainId")
        
        worker_metadata.update({
            "authorization": final_auth,
            "sessionId": request.sessionId,
            "drhpDocumentId": request.drhpDocumentId,
            "rhpDocumentId": request.rhpDocumentId,
            "domain": request.domain,
            "domainId": final_domain_id
        })
        
        logger.info(
            "Comparison job submitted",
            job_id=job_id,
            drhp=request.drhpNamespace,
            rhp=request.rhpNamespace
        )
        
        celery_app.send_task(
            "generate_comparison",
            args=[request.drhpNamespace, request.rhpNamespace, job_id, worker_metadata],
            task_id=job_id
        )
        
        return JobResponse(
            job_id=job_id,
            status="accepted",
            message="Comparison generation job enqueued successfully"
        )
    
    except Exception as e:
        logger.error("Failed to submit comparison job", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to enqueue job: {str(e)}"
        )


@router.get("/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str) -> JobStatusResponse:
    """
    Get job status and result.
    
    Args:
        job_id: Job identifier
    
    Returns:
        JobStatusResponse with current status and result
    """
    try:
        # Get task result from Celery
        task_result = celery_app.AsyncResult(job_id)
        
        response = JobStatusResponse(
            job_id=job_id,
            state=task_result.state,
        )
        
        if task_result.successful():
            response.result = task_result.result
        elif task_result.failed():
            response.error = str(task_result.info)
        
        return response
    
    except Exception as e:
        logger.error("Failed to get job status", job_id=job_id, error=str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get job status: {str(e)}"
        )
