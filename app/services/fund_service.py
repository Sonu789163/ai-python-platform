"""
Fund Service to fetch and cache Domain (Fund) configurations.
"""
from typing import Dict, Any, Optional
from app.db.mongo import mongodb
from app.core.logging import get_logger

logger = get_logger(__name__)

class FundService:
    def __init__(self):
        self._collection_name = "domains" # MongoDB collections are usually lowercase plurals

    async def get_fund_config(self, domain_id: str) -> Dict[str, Any]:
        """
        Fetch fund configuration by domain_id.
        """
        if not domain_id:
            return {}

        try:
            # We use the sync client for now as it's simpler in Celery tasks
            # but this service could be used in async context too.
            if not mongodb.sync_db:
                mongodb.connect_sync()
            
            collection = mongodb.get_sync_collection(self._collection_name)
            config = collection.find_one({"domainId": domain_id})
            
            if not config:
                logger.warning(f"Fund config not found for domain_id: {domain_id}")
                return {}
            
            # Remove MongoDB _id
            if "_id" in config:
                del config["_id"]
                
            return config
        except Exception as e:
            logger.error(f"Failed to fetch fund config: {str(e)}")
            return {}

fund_service = FundService()
