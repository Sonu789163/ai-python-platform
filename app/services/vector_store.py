"""
Pinecone vector store service.
Handles upserting document chunks to the correct index.
"""
from typing import List, Dict, Any
from pinecone import Pinecone
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class VectorStoreService:
    """Service for interacting with Pinecone vector store."""
    
    def __init__(self):
        """Initialize Pinecone client."""
        self.pc = Pinecone(api_key=settings.PINECONE_API_KEY)

    @staticmethod
    def _extract_index_name(name_or_url: str) -> str:
        """Extract index name from URL if necessary."""
        if name_or_url.startswith("https://"):
            # URL format: https://index_name-project_id.svc.region.pinecone.io
            return name_or_url.split("https://")[1].split("-")[0]
        return name_or_url

    def get_index(self, index_name: str, host: str = ""):
        """Get a Pinecone index instance."""
        clean_name = self._extract_index_name(index_name)
        try:
            if host:
                return self.pc.Index(clean_name, host=host)
            # If index_name looks like a URL, use it as host
            if index_name.startswith("https://"):
                return self.pc.Index(clean_name, host=index_name)
            return self.pc.Index(clean_name)
        except Exception as e:
            logger.error("Failed to get Pinecone index", index_name=clean_name, host=host or index_name, error=str(e))
            raise

    def upsert_chunks(
        self,
        chunks: List[Dict[str, Any]],
        index_name: str,
        namespace: str = "",
        host: str = ""
    ) -> Dict[str, Any]:
        """
        Upsert embeddings to Pinecone.
        
        Args:
            chunks: List of chunk dicts (must contain 'embedding', 'chunk_text', 'chunk_index')
            index_name: Pinecone index name (drhpdocuments or rhpdocuments)
            namespace: Optional namespace (e.g. filename)
            host: Optional index host URL
        """
        index = self.get_index(index_name, host=host)
        
        vectors = []
        for chunk in chunks:
            # Metadata as stored in n8n workflow
            metadata = {
                "text": chunk["chunk_text"],
                "chunk_index": chunk["chunk_index"],
                "documentName": namespace
            }
            # Merge extra metadata if any
            if "metadata" in chunk:
                metadata.update(chunk["metadata"])
            
            # Create a unique ID for the vector
            vector_id = f"{namespace}_{chunk['chunk_index']}"
            
            vectors.append({
                "id": vector_id,
                "values": chunk["embedding"],
                "metadata": metadata
            })
            
        logger.info(
            "Upserting vectors to Pinecone",
            index=index_name,
            namespace=namespace,
            vector_count=len(vectors)
        )
        
        # Pinecone upsert in batches
        # Ensure namespace is not "__default__" which is forbidden in newer API versions
        safe_namespace = "" if namespace == "__default__" or not namespace else namespace
        
        upsert_response = index.upsert(
            vectors=vectors,
            namespace=safe_namespace
        )
        
        # In newer SDK, upsert_response is an object with .upserted_count
        count = getattr(upsert_response, "upserted_count", 0)
        
        logger.info(
            "Pinecone upsert completed",
            index=index_name,
            namespace=namespace,
            upserted_count=count
        )
        
        return {
            "upserted_count": count,
            "namespace": namespace,
            "index": index_name
        }


# Global service instance
vector_store_service = VectorStoreService()
