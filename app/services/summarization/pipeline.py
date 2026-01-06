"""
Main Summarization Pipeline (Layer 2)
Orchestrates multi-stage generation, validation, and research.
"""
import asyncio
import time
from typing import Dict, Any, List
from app.core.config import settings
from app.core.logging import get_logger
from app.services.vector_store import vector_store_service
from app.services.embedding import EmbeddingService
from app.services.summarization.prompts import SUBQUERIES, GENERATOR_SYSTEM_PROMPT, VALIDATOR_SYSTEM_PROMPT
from app.services.summarization.research import research_service
from app.services.summarization.valuation import valuation_service
import openai
from app.services.summarization.formatter import formatter

logger = get_logger(__name__)

class SummaryPipeline:
    def __init__(self):
        self.embedding = EmbeddingService()
        self.client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

    async def _retrieve_context(self, queries: List[str], namespace: str, index_name: str, host: str, vector_top_k: int = 100, rerank_top_n: int = 25) -> str:
        """
        Runs sub-queries to retrieve context from Pinecone and reranks using Cohere.
        Matches n8n logic (vector topK: 50, rerank topN: 15).
        """
        from app.services.rerank import rerank_service
        
        all_context = []
        for query in queries:
            # 1. Vector Search
            query_vector = await self.embedding.embed_text(query)
            index = vector_store_service.get_index(index_name, host=host)
            # Prepare query filter
            query_filter = {"documentName": namespace} if namespace and namespace != "__default__" else None

            search_res = index.query(
                vector=query_vector,
                top_k=vector_top_k,
                namespace=namespace,
                include_metadata=True,
                filter=query_filter
            )
            initial_chunks = [m['metadata']['text'] for m in search_res['matches']]
            logger.info("Namespace search completed", query=query[:50], matches=len(initial_chunks), namespace=namespace)
            
            # Fallback to __default__ if specified namespace is empty
            if not initial_chunks and namespace and namespace != "__default__":
                search_res = index.query(
                    vector=query_vector,
                    top_k=vector_top_k,
                    namespace="__default__",
                    include_metadata=True,
                    filter=query_filter
                )
                initial_chunks = [m['metadata']['text'] for m in search_res['matches']]
                if initial_chunks:
                    logger.info("Retrieved chunks from __default__ with metadata filter", query=query[:50], count=len(initial_chunks))
                else:
                    # Final fallback: query __default__ WITHOUT filter (could be noisy)
                    search_res = index.query(
                        vector=query_vector,
                        top_k=vector_top_k,
                        namespace="__default__",
                        include_metadata=True
                    )
                    initial_chunks = [m['metadata']['text'] for m in search_res['matches']]
                    if initial_chunks:
                         logger.warning("Falling back to UNFILTERED __default__ search", query=query[:50])
            
            # 2. Rerank
            if initial_chunks:
                reranked_chunks = rerank_service.rerank(query, initial_chunks, top_n=rerank_top_n)
                all_context.extend(reranked_chunks)
            
        # Preserve order while removing duplicates
        seen = set()
        unique_context = []
        for chunk in all_context:
            if chunk not in seen:
                unique_context.append(chunk)
                seen.add(chunk)
                
        final_context = "\n---\n".join(unique_context)
        logger.info("Context retrieval finished", 
                    total_queries=len(queries), 
                    total_chars=len(final_context),
                    namespace=namespace)
        return final_context

    async def generate(self, namespace: str, doc_type: str = "drhp") -> Dict[str, Any]:
        """
        Executes the full 3-agent summarization pipeline.
        """
        start_time = time.time()
        logger.info("Starting 3-agent summary pipeline", namespace=namespace, doc_type=doc_type)
        usage_stats = {}

        try:
            # 0. Determine Pinecone settings
            if doc_type == "rhp":
                index_name = settings.PINECONE_RHP_INDEX
                host = settings.PINECONE_RHP_HOST
            else:
                index_name = settings.PINECONE_DRHP_INDEX
                host = settings.PINECONE_DRHP_HOST

            # STAGE 1: Investor & Share Capital Extractor
            logger.info("Stage 1: Running Investor Extractor Agent")
            investor_context = await self._retrieve_context(
                ["Extract all equity share capital history and list of investors"],
                namespace, index_name, host, vector_top_k=20
            )
            logger.info("Investor context retrieved", length=len(investor_context))
            
            from app.services.summarization.prompts import INVESTOR_EXTRACTOR_SYSTEM_PROMPT
            investor_resp = await self.client.chat.completions.create(
                model=settings.SUMMARY_MODEL,
                messages=[
                    {"role": "system", "content": INVESTOR_EXTRACTOR_SYSTEM_PROMPT},
                    {"role": "user", "content": f"Extract data for {namespace} from: {investor_context}"}
                ],
                response_format={"type": "json_object"},
                max_tokens=4000
            )
            
            # Log usage
            usage = investor_resp.usage
            usage_stats["stage1"] = {
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens
            }
            logger.info("Stage 1 LLM completed", **usage_stats["stage1"])
            
            import json
            investor_data = json.loads(investor_resp.choices[0].message.content)
            
            # Start research in parallel while parsing investor data
            research_task = asyncio.create_task(research_service.get_adverse_findings(namespace, "Promoters from DRHP"))
            
            # Handle list or object response
            if isinstance(investor_data, list):
                results = investor_data
            elif isinstance(investor_data, dict):
                results = investor_data.get("results", []) or [investor_data]
            else:
                results = []
            
            logger.info("Stage 1 data parsed", result_count=len(results))
            
            investor_html_snippet = ""
            if results:
                # Find by type 'summary_report' or just the first content
                report_obj = next((item for item in results if isinstance(item, dict) and item.get("type") == "summary_report"), None)
                if report_obj:
                    investor_html_snippet = formatter.markdown_to_html(report_obj.get("content", ""))
                elif len(results) > 0 and isinstance(results[0], dict):
                    investor_html_snippet = formatter.markdown_to_html(results[0].get("content", ""))
                
                calc_obj = next((item for item in results if isinstance(item, dict) and item.get("type") == "calculation_data"), None)
                if calc_obj:
                    calc_data = calc_obj.get("calculation_parameters", {})
                    premium_params = calc_data.get("premium_rounds", [])
                    if premium_params:
                        calc_rounds = valuation_service.calculate_premium_rounds(premium_params)
                        valuation_html = valuation_service.generate_valuation_html(calc_rounds)
                        investor_html_snippet += f"\n\n{valuation_html}"
            
            # Keep markdown version for prompt injection
            investor_md = ""
            if report_obj:
                 investor_md = report_obj.get("content", "")
                 if calc_obj:
                      investor_md += f"\n\n{valuation_service.generate_valuation_html(calc_rounds)}" # valuation is html-ish but markdown handles it

            if not investor_html_snippet:
                logger.warning("No investor content extracted in Stage 1")

            # STAGE 2: Main Generator with Sub-queries
            logger.info("Stage 2: Running Main Summary Generator")
            main_context = await self._retrieve_context(SUBQUERIES, namespace, index_name, host)
            
            # Prepare Stage 1 integration prompt
            stage1_integration = ""
            if investor_md:
                stage1_integration = f"\n\n### MANDATORY DATA FOR SECTION VI (INTEGRATE THIS TABLES/DATA INTO SECTION VI):\n{investor_md}\n\n"

            main_gen_resp = await self.client.chat.completions.create(
                model=settings.SUMMARY_MODEL,
                messages=[
                    {"role": "system", "content": GENERATOR_SYSTEM_PROMPT},
                    {"role": "user", "content": f"Target Document: {namespace}\n\n{stage1_integration}\n\nContext: {main_context[:300000]}"}
                ],
                max_tokens=16384
            )
            logger.info("Main Generator context sent", length=len(main_context[:300000]))
            
            usage = main_gen_resp.usage
            usage_stats["stage2"] = {
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens
            }
            logger.info("Stage 2 LLM completed", **usage_stats["stage2"])
            
            draft_summary = main_gen_resp.choices[0].message.content

            # STAGE 3: Validation Agent
            logger.info("Stage 3: Running Validator Agent")
            final_resp = await self.client.chat.completions.create(
                model=settings.SUMMARY_MODEL,
                messages=[
                    {"role": "system", "content": VALIDATOR_SYSTEM_PROMPT},
                    {"role": "user", "content": f"Target Document: {namespace}\n\nValidate this DRAFT: {draft_summary}\n\nAgainst CONTEXT: {main_context[:300000]}"}
                ],
                max_tokens=16384
            )
            logger.info("Validator context sent", length=len(main_context[:300000]))
            
            usage = final_resp.usage
            usage_stats["stage3"] = {
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens
            }
            logger.info("Stage 3 LLM completed", **usage_stats["stage3"])
            
            final_md = final_resp.choices[0].message.content

            # Wait for research to finish
            research_data = await research_task
            research_html_snippet = formatter.format_research_report(research_data)

            # 6. Final Formatting and Injection
            # Replicate n8n merge logic
            
            # Step 1: Base summary to HTML snippet
            full_raw_report = f"<h1>DRHP Summary Report: {namespace}</h1>\n\n" + final_md
            html_content = formatter.markdown_to_html(full_raw_report)
            
            # Step 2: Insert Investor Data before SECTION VII
            if investor_html_snippet:
                html_content = formatter.insert_html_before_section(
                    html_content,
                    investor_html_snippet,
                    "SECTION VII",
                    "Matched Investors & Analysis"
                )
            
            # Step 3: Insert Research Data before SECTION XII
            if research_html_snippet:
                html_content = formatter.insert_html_before_section(
                    html_content,
                    research_html_snippet,
                    "SECTION XII",
                    "Adverse Findings & Research"
                )
            
            # Step 4: Wrap in Enhanced HTML
            final_html = formatter.wrap_enhanced_html(html_content, namespace)

            duration = time.time() - start_time
            return {
                "status": "success",
                "html": final_html,
                "summary": final_md,
                "duration": duration,
                "namespace": namespace,
                "usage_stats": usage_stats,
                "research_data": research_data
            }

        except Exception as e:
            logger.error("Pipeline failed", error=str(e), exc_info=True)
            raise

    # Removed _convert_to_html in favor of formatter service

summary_pipeline = SummaryPipeline()
