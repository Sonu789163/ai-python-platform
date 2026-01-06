"""
Research service for adverse findings using Perplexity API.
"""
import httpx
import json
from typing import Dict
from app.core.config import settings
from app.core.logging import get_logger
from .prompts import RESEARCH_SYSTEM_PROMPT

logger = get_logger(__name__)

class ResearchService:
    def __init__(self):
        self.api_key = settings.OPENAI_API_KEY # Assuming same as AI key or add PERPLEXITY_API_KEY
        self.url = "https://api.perplexity.ai/chat/completions"

    async def get_adverse_findings(self, company_name: str, promoters: str) -> Dict:
        """
        Calls Perplexity API to research adverse findings.
        """
        if not hasattr(settings, "PERPLEXITY_API_KEY") or not settings.PERPLEXITY_API_KEY:
            logger.warning("PERPLEXITY_API_KEY not set, skipping deep research")
            return {"executive_summary": {"key_findings": "Deep research disabled (no API key)"}}

        payload = {
            "model": "sonar-pro",
            "messages": [
                {"role": "system", "content": RESEARCH_SYSTEM_PROMPT},
                {"role": "user", "content": f"Company: {company_name}, Promoters: {promoters}"}
            ],
            "max_tokens": 4000
        }
        
        headers = {
            "Authorization": f"Bearer {settings.PERPLEXITY_API_KEY}",
            "Content-Type": "application/json"
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(self.url, json=payload, headers=headers, timeout=60.0)
                response.raise_for_status()
                result = response.json()
                content = result["choices"][0]["message"]["content"]
                # Attempt to parse JSON from response
                start = content.find("{")
                end = content.rfind("}") + 1
                return json.loads(content[start:end])
            except Exception as e:
                logger.error("Perplexity research failed", error=str(e))
                return {"error": str(e)}

research_service = ResearchService()
