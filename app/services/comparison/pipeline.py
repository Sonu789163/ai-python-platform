"""
DRHP vs RHP Comparison Pipeline.
Orchestrates context retrieval from two separate indexes and generates comparison report.
"""
import asyncio
import time
from typing import Dict, Any, List
from app.core.config import settings
from app.core.logging import get_logger
from app.services.vector_store import vector_store_service
from app.services.embedding import EmbeddingService
from app.services.rerank import rerank_service
from app.services.comparison.prompts import COMPARISON_SYSTEM_PROMPT, COMPARISON_QUERIES
from app.services.comparison.formatter import comparison_formatter
import openai

logger = get_logger(__name__)

class ComparisonPipeline:
    def __init__(self):
        self.embedding = EmbeddingService()
        self.client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

    async def _retrieve_context_from_index(
        self, 
        queries: List[str], 
        namespace: str, 
        index_name: str, 
        host: str = "",
        vector_top_k: int = 50,
        rerank_top_n: int = 15
    ) -> str:
        """
        Retrieves context from a specific Pinecone index.
        Matches the robust fallback logic in SummaryPipeline.
        """
        all_context = []
        for query in queries:
            try:
                # 1. Vector Search
                query_vector = await self.embedding.embed_text(query)
                index = vector_store_service.get_index(index_name, host=host)
                
                # Filter by documentName metadata
                query_filter = {"documentName": namespace} if namespace and namespace != "" else None

                # First try: Query the specified namespace with filter
                search_res = index.query(
                    vector=query_vector,
                    top_k=vector_top_k,
                    namespace=namespace or "",
                    include_metadata=True,
                    filter=query_filter
                )
                initial_chunks = [m['metadata']['text'] for m in search_res['matches']]
                
                # Fallback 1: Try "" namespace WITH metadata filter
                if not initial_chunks and namespace and namespace != "":
                    logger.info(f"Retrying search in \"\" namespace for {namespace}")
                    search_res = index.query(
                        vector=query_vector,
                        top_k=vector_top_k,
                        namespace="",
                        include_metadata=True,
                        filter=query_filter
                    )
                    initial_chunks = [m['metadata']['text'] for m in search_res['matches']]

                # Fallback 2: Try "" namespace WITHOUT filter (last resort)
                if not initial_chunks and namespace and namespace != "":
                    logger.warning(f"Final fallback: UNFILTERED \"\" search for {namespace}")
                    search_res = index.query(
                        vector=query_vector,
                        top_k=vector_top_k,
                        namespace="",
                        include_metadata=True
                    )
                    initial_chunks = [m['metadata']['text'] for m in search_res['matches']]

                # 2. Rerank
                if initial_chunks:
                    reranked_chunks = rerank_service.rerank(query, initial_chunks, top_n=rerank_top_n)
                    all_context.extend(reranked_chunks)
            except Exception as e:
                logger.error(f"Context retrieval failed for index {index_name}", query=query, error=str(e))
                continue
            
        # Deduplicate
        unique_context = []
        seen = set()
        for chunk in all_context:
            if chunk not in seen:
                unique_context.append(chunk)
                seen.add(chunk)
                
        return "\n---\n".join(unique_context)

    async def compare(
        self, 
        drhp_namespace: str, 
        rhp_namespace: str,
        drhp_index: str = None,
        rhp_index: str = None,
        drhp_host: str = None,
        rhp_host: str = None
    ) -> Dict[str, Any]:
        """
        Main comparison method.
        """
        drhp_index = drhp_index or settings.PINECONE_DRHP_INDEX
        rhp_index = rhp_index or settings.PINECONE_RHP_INDEX
        drhp_host = drhp_host or settings.PINECONE_DRHP_HOST
        rhp_host = rhp_host or settings.PINECONE_RHP_HOST
        start_time = time.time()
        logger.info("Starting DRHP vs RHP Comparison Pipeline", 
                    drhp=drhp_namespace, rhp=rhp_namespace)
        
        # Parallel retrieval from both indexes
        drhp_task = self._retrieve_context_from_index(
            COMPARISON_QUERIES, drhp_namespace, drhp_index, drhp_host
        )
        rhp_task = self._retrieve_context_from_index(
            COMPARISON_QUERIES, rhp_namespace, rhp_index, rhp_host
        )
        
        drhp_context, rhp_context = await asyncio.gather(drhp_task, rhp_task)
        
        if not drhp_context and not rhp_context:
            return {
                "status": "error",
                "message": "No context found for either document."
            }

        # Combine contexts with clear separation
        full_context = f"=== DRHP CONTEXT (FOR {drhp_namespace}) ===\n{drhp_context}\n\n"
        full_context += f"=== RHP CONTEXT (FOR {rhp_namespace}) ===\n{rhp_context}"

        logger.info("Context retrieval finished", 
                    drhp_len=len(drhp_context), 
                    rhp_len=len(rhp_context))

        try:
            response = await self.client.chat.completions.create(
                model=settings.SUMMARY_MODEL,
                messages=[
                    {"role": "system", "content": COMPARISON_SYSTEM_PROMPT},
                    {"role": "user", "content": f"Compare these contexts and highlight material changes:\n\n{full_context}"}
                ],
                temperature=0.1,
                max_tokens=16384
            )
            
            comparison_md = response.choices[0].message.content
            html_report = comparison_formatter.markdown_to_html(comparison_md)
            
            duration = time.time() - start_time
            return {
                "status": "success",
                "markdown": comparison_md,
                "html": html_report,
                "duration": duration,
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens
                }
            }

        except Exception as e:
            logger.error("LLM comparison failed", error=str(e), exc_info=True)
            return {
                "status": "error",
                "message": f"Comparison failed: {str(e)}"
            }

comparison_pipeline = ComparisonPipeline()
