"""
Backend notification service.
Sends job status updates back to the Node.js backend.
"""
from typing import Dict, Any, Optional
import requests
import time
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class BackendNotifier:
    """Service to notify the Node.js backend of job status."""
    
    @staticmethod
    def notify_status(
        job_id: str,
        status: str,
        namespace: str,
        error: Optional[Dict[str, Any]] = None,
        execution_id: Optional[str] = None
    ) -> bool:
        """
        Send status update to backend.
        Matched to n8n node "Send Error to Backend3" and webhook response logic.
        """
        payload = {
            "jobId": job_id,
            "status": status,
            "namespace": namespace,
            "execution": {
                "workflowId": "python-platform",
                "executionId": execution_id or job_id
            }
        }
        
        if error:
            payload["error"] = {
                "message": error.get("message", "Unknown error"),
                "stack": error.get("stack", "No stack trace"),
                "timestamp": str(time.time())
            }
            
        try:
            logger.info("Notifying backend of status", job_id=job_id, status=status)
            response = requests.post(
                settings.BACKEND_STATUS_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            response.raise_for_status()
            logger.info("Backend notified successfully", status_code=response.status_code)
            return True
        except Exception as e:
            logger.error("Failed to notify backend", error=str(e), job_id=job_id)
            return False


# Global service instance
backend_notifier = BackendNotifier()
