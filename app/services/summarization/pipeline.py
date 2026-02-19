"""
DRHP Summary Pipeline - 4-Agent Orchestration
Matches n8n-workflows/summaryWorkflow.json implementation
Stores summaries in markdown format with toggle-based conditional sections
"""
import asyncio
import time
from typing import Dict, Any, List, Optional
from datetime import datetime
from app.core.config import settings
from app.core.logging import get_logger
from app.services.vector_store import vector_store_service
from app.services.embedding import EmbeddingService
from app.services.rerank import rerank_service
from app.services.summarization.prompts import (
    SUBQUERIES,
    INVESTOR_EXTRACTOR_SYSTEM_PROMPT,
    CAPITAL_HISTORY_EXTRACTOR_SYSTEM_PROMPT,
    MAIN_SUMMARY_SYSTEM_PROMPT,
    MAIN_SUMMARY_INSTRUCTIONS,
    SUMMARY_VALIDATOR_SYSTEM_PROMPT
)
from app.services.summarization.markdown_converter import MarkdownConverter
from app.services.summarization.research import research_service
import openai
import json

logger = get_logger(__name__)


class SummaryPipeline:
    """
    4-Agent Summary Pipeline:
    - Agent 1: Investor Extractor (returns JSON)
    - Agent 2: Capital History & Valuation Extractor (returns JSON)
    - Agent 3: DRHP Summary Generator (returns markdown)
    - Agent 4: Summary Validator/Previewer (returns verified markdown)
    
    All outputs converted to markdown and merged based on tenant toggles.
    """
    
    def __init__(self):
        self.embedding = EmbeddingService()
        self.client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        self.md_converter = MarkdownConverter()
    
    async def _retrieve_context(
        self,
        queries: List[str],
        namespace: str,
        index_name: str = None,
        host: str = None,
        vector_top_k: int = 10,
        rerank_top_n: int = 10,
        metadata_filter: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Retrieves context from Pinecone with Cohere reranking.
        Matches n8n workflow retrieval logic.
        """
        index_name = index_name or settings.PINECONE_DRHP_INDEX
        host = host or settings.PINECONE_DRHP_HOST
        
        all_context = []
        for query in queries:
            try:
                # 1. Vector Search
                query_vector = await self.embedding.embed_text(query)
                index = vector_store_service.get_index(index_name, host=host)
                
                # Construct Filter
                # Default filter by documentName (namespace)
                filter_criteria = {"documentName": namespace} if namespace and namespace != "" else {}
                
                # Merge with metadata_filter if provided via API (e.g. documentId, domainId)
                if metadata_filter:
                    filter_criteria.update(metadata_filter)
                
                # If criteria is empty, set to None to allow querying (though generally unsafe without namespace)
                query_filter = filter_criteria if filter_criteria else None
                
                # First try: Query the default namespace ("") with filters
                # This matches the single-index strategy where we rely on metadata for separation
                safe_namespace = ""
                
                # Try default namespace with filter
                search_res = index.query(
                    vector=query_vector,
                    top_k=vector_top_k,
                    namespace=safe_namespace,
                    include_metadata=True,
                    filter=query_filter
                )
                initial_chunks = [m['metadata']['text'] for m in search_res['matches']]
                
                # Fallback: Query specific namespace (legacy support)
                if not initial_chunks and namespace and namespace != "":
                    # Remove "documentName" from filter for legacy namespace search
                    # Legacy documents using namespace for isolation might NOT have documentName metadata
                    legacy_filter = query_filter.copy() if query_filter else {}
                    if "documentName" in legacy_filter:
                        del legacy_filter["documentName"]
                    if not legacy_filter:
                        legacy_filter = None
                        
                    # logger.info(f"Fallback search in legacy namespace {namespace} with filter {legacy_filter}")
                    search_res = index.query(
                        vector=query_vector,
                        top_k=vector_top_k,
                        namespace=namespace,
                        include_metadata=True,
                        filter=legacy_filter
                    )
                    initial_chunks = [m['metadata']['text'] for m in search_res['matches']]

                # 2. Reranking (Disabled as requested)
                if initial_chunks:
                    all_context.extend(initial_chunks[:rerank_top_n])
                    
            except Exception as e:
                logger.error(f"Context retrieval failed for query", query=query, error=str(e))
                continue
        
        # Deduplicate
        unique_context = []
        seen = set()
        for chunk in all_context:
            if chunk not in seen:
                unique_context.append(chunk)
                seen.add(chunk)
        
        return "\n---\n".join(unique_context)
    
    async def _agent_1_investor_extractor(
        self,
        namespace: str,
        index_name: str = None,
        host: str = None,
        metadata_filter: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Agent 1: Investor Extractor
        Node: A-1:-sectionVI investor extractor
        Returns: JSON with investor data
        """
        logger.info("Agent 1: Investor Extractor - Starting", namespace=namespace)
        
        # Retrieve context (50 chunks, reranked via Cohere)
        investor_query = ["Extract complete shareholding pattern, investor list, and capital structure from DRHP"]
        context = await self._retrieve_context(
            investor_query,
            namespace,
            index_name,
            host,
            vector_top_k=10,
            rerank_top_n=10,
            metadata_filter=metadata_filter
        )
        
        if not context:
            logger.warning("Agent 1: No context found")
            return {"error": "No investor data found", "extraction_status": "failed"}
        
        try:
            response = await self.client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {"role": "system", "content": INVESTOR_EXTRACTOR_SYSTEM_PROMPT},
                    {"role": "user", "content": f"Extract investor data from this DRHP context:\n\n{context}"}
                ],
                temperature=0.0,
                max_tokens=8192,
                response_format={"type": "json_object"}
            )
            
            investor_json = json.loads(response.choices[0].message.content)
            usage = response.usage
            
            logger.info("Agent 1: Completed", 
                        investors_count=investor_json.get("extraction_metadata", {}).get("total_investors_extracted", 0),
                        input_tokens=usage.prompt_tokens,
                        output_tokens=usage.completion_tokens)
            
            # Store usage in dict for pipeline aggregation
            investor_json["_usage"] = {
                "input": usage.prompt_tokens,
                "output": usage.completion_tokens
            }
            return investor_json
            
        except Exception as e:
            logger.error("Agent 1: Failed", error=str(e), exc_info=True)
            return {"error": str(e), "extraction_status": "failed"}
    
    async def _agent_2_capital_history_extractor(
        self,
        namespace: str,
        index_name: str = None,
        host: str = None,
        metadata_filter: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Agent 2: Capital History & Valuation Extractor
        Node: A-2:-sectionVI capital history extractor3
        Returns: JSON with share capital table and premium rounds
        """
        logger.info("Agent 2: Capital History Extractor - Starting", namespace=namespace)
        
        # Retrieve context (10 chunks, reranked via Cohere)
        capital_query = ["Extract complete equity share capital history table and premium rounds from DRHP"]
        context = await self._retrieve_context(
            capital_query,
            namespace,
            index_name,
            host,
            vector_top_k=10,
            rerank_top_n=10,
            metadata_filter=metadata_filter
        )
        
        if not context:
            logger.warning("Agent 2: No context found")
            return {"error": "No capital history data found", "type": "calculation_data"}
        
        try:
            response = await self.client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {"role": "system", "content": CAPITAL_HISTORY_EXTRACTOR_SYSTEM_PROMPT},
                    {"role": "user", "content": f"Extract share capital history from this DRHP context:\n\n{context}"}
                ],
                temperature=0.0,
                max_tokens=8192,
                response_format={"type": "json_object"}
            )
            
            capital_json = json.loads(response.choices[0].message.content)
            usage = response.usage
            
            logger.info("Agent 2: Completed", 
                        premium_rounds=capital_json.get("calculation_parameters", {}).get("total_premium_rounds", 0),
                        input_tokens=usage.prompt_tokens,
                        output_tokens=usage.completion_tokens)
            
            # Store usage
            capital_json["_usage"] = {
                "input": usage.prompt_tokens,
                "output": usage.completion_tokens
            }
            return capital_json
            
        except Exception as e:
            logger.error("Agent 2: Failed", error=str(e), exc_info=True)
            return {"error": str(e), "type": "calculation_data"}
    
    async def _agent_3_summary_generator(
        self,
        namespace: str,
        custom_sop: Optional[str] = None,
        index_name: str = None,
        host: str = None,
        metadata_filter: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Agent 3: DRHP Summary Generator
        Node: A-3:-DRHP Summary Generator Agent1
        Uses iterative sub-queries to generate comprehensive summary section-by-section.
        """
        logger.info("Agent 3: Summary Generator - Starting Iterative Generation", namespace=namespace)
        
        full_summary_parts = []
        total_input_tokens = 0
        total_output_tokens = 0
        
        # Iterate through each subquery to generate focused sections
        # This addresses the issue of missing fields by ensuring each topic gets dedicated context and generation
        for i, query in enumerate(SUBQUERIES):
            try:
                # Retrieve context specific to this subquery
                # We use a focused retrieval (top_k=20) to ensure we get deep details for this specific section
                context = await self._retrieve_context(
                    [query],
                    namespace,
                    index_name,
                    host,
                    vector_top_k=15, # Higher top_k for focused single query
                    rerank_top_n=10,
                    metadata_filter=metadata_filter
                )
                
                if not context:
                    logger.warning(f"Agent 3: No context found for subquery {i+1}", query=query[:50])
                    continue
                
                # Construct focused prompt for this section
                # If custom_sop is provided (e.g. for Excollo domain), use it as the base prompt
                # overriding the default MAIN_SUMMARY_SYSTEM_PROMPT.
                # This ensures we strictly follow the domain's specific schema/format.
                base_prompt = custom_sop if custom_sop else MAIN_SUMMARY_SYSTEM_PROMPT
                
                section_system_prompt = f"""
                {base_prompt}
                
                ----------------------------------------------------------------
                ⚡ CURRENT COMPONENT GENERATION MODE ⚡
                ----------------------------------------------------------------
                You are currently generating ONLY ONE PART of the full DRHP summary.
                
                YOUR CURRENT FOCUS:
                "{query}"
                
                INSTRUCTIONS:
                1. Based strictly on the provided context, generate the markdown sections/tables relevant to the focus area above.
                2. Do NOT generate a full document introduction or conclusion unless the focus area explicitly asks for "Basic company details".
                3. Ensure the output is formatted as proper Markdown that can be appended to the submitted report.
                4. EXTRACT ALL SPECIFIC DETAILS mentioned in the focus query. Do not summarize broadly if specific metrics are asked.
                5. If data is missing for specific requested fields, explicitly state it for that field.
                """
                
                response = await self.client.chat.completions.create(
                    model="gpt-4.1-mini",
                    messages=[
                        {"role": "system", "content": section_system_prompt},
                        {"role": "user", "content": f"Generate the summary section for this specific focus area:\n\nFOCUS:\n{query}\n\nCONTEXT:\n{context}"}
                    ],
                    temperature=0.1,
                    max_tokens=4096 
                )
                
                usage = response.usage
                part_content = response.choices[0].message.content
                
                full_summary_parts.append(part_content)
                total_input_tokens += usage.prompt_tokens
                total_output_tokens += usage.completion_tokens
                
                logger.debug(f"Agent 3: Completed Part {i+1}/{len(SUBQUERIES)}", output_len=len(part_content))
                
            except Exception as e:
                logger.error(f"Agent 3: Failed to generate part {i+1}", error=str(e))
                # Continue to next part instead of failing completely
                continue
        
        if not full_summary_parts:
             return {"markdown": "# Error\n\nNo DRHP data found for summary generation.", "usage": {"input": 0, "output": 0}}

        # Combine all parts
        full_draft_summary = "\n\n".join(full_summary_parts)
        
        logger.info("Agent 3: Completed Iterative Generation", 
                    total_parts=len(full_summary_parts),
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens)
        
        return {
            "markdown": full_draft_summary,
            "usage": {
                "input": total_input_tokens,
                "output": total_output_tokens
            }
        }
    
    async def _agent_4_summary_validator(
        self,
        draft_summary: str,
        namespace: str,
        custom_validator_prompt: Optional[str] = None,
        formatting_sop: Optional[str] = None,
        index_name: str = None,
        host: str = None,
        metadata_filter: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Agent 4: DRHP Summary Validator/Previewer
        Node: A-4:-DRHP Summary Previewer
        Validates and corrects the draft summary
        Returns: {markdown: str, usage: dict}
        """
        logger.info("Agent 4: Summary Validator - Starting", namespace=namespace)
        
        # Retrieve context for validation (same as Agent 3)
        context = await self._retrieve_context(
            SUBQUERIES,
            namespace,
            index_name,
            host,
            vector_top_k=10,
            rerank_top_n=10,
            metadata_filter=metadata_filter
        )
        
        if not context:
            logger.warning("Agent 4: No context for validation, returning draft as-is")
            return {"markdown": draft_summary, "usage": {"input": 0, "output": 0}}
        
        # Use custom validator prompt if provided, else format default
        system_prompt = custom_validator_prompt if custom_validator_prompt else SUMMARY_VALIDATOR_SYSTEM_PROMPT
        
        # Append the Formatting SOP so the validator knows the target structure
        if formatting_sop:
            system_prompt += f"\n\n----------------------------------------------------------------\nREFERENCE STANDARD OPERATING PROCEDURE (SOP) / FORMAT:\n----------------------------------------------------------------\n{formatting_sop}"

        try:
            response = await self.client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Validate and correct this summary against DRHP context:\n\nDRAFT SUMMARY:\n{draft_summary}\n\nDRHP CONTEXT:\n{context}"}
                ],
                temperature=0.0,
                max_tokens=16384
            )
            
            usage = response.usage
            final_summary = response.choices[0].message.content
            
            logger.info("Agent 4: Completed", 
                        input_tokens=usage.prompt_tokens,
                        output_tokens=usage.completion_tokens)
            
            return {
                "markdown": final_summary,
                "usage": {
                    "input": usage.prompt_tokens,
                    "output": usage.completion_tokens
                }
            }
            
        except Exception as e:
            logger.error("Agent 4: Failed, returning draft", error=str(e), exc_info=True)
            return {"markdown": draft_summary, "error": str(e), "usage": {"input": 0, "output": 0}}
    
    async def generate_summary(
        self,
        namespace: str,
        domain_id: str,
        tenant_config: Optional[Dict[str, Any]] = None,
        index_name: str = None,
        host: str = None
    ) -> Dict[str, Any]:
        """
        Main summary generation method.
        Orchestrates 4-agent pipeline with toggle-based conditional merging.
        
        Args:
            namespace: Document namespace/fileName
            domain_id: Tenant domain ID
            tenant_config: Tenant configuration with toggles and custom SOP
                {
                    "investor_match_only": bool,
                    "valuation_matching": bool,
                    "adverse_finding": bool,
                    "target_investors": List[str],
                    "custom_summary_sop": str
                }
            index_name: Pinecone index name (optional)
            host: Pinecone host (optional)
        
        Returns:
            {
                "status": "success" | "error",
                "markdown": str,  # Final markdown summary
                "duration": float,
                "usage": dict
            }
        """
        start_time = time.time()
        logger.info("Starting 4-Agent Summary Pipeline", namespace=namespace, domain=domain_id)
        
        # Build Metadata Filter for Tenant Isolation
        metadata_filter = {}
        if domain_id:
            metadata_filter["domainId"] = domain_id
            
        # Default tenant config
        if not tenant_config:
            tenant_config = {
                "investor_match_only": True,
                "valuation_matching": True,
                "adverse_finding": True,
                "target_investors": [],
                "custom_summary_sop": ""
            }
        
        # Extract toggles
        investor_match_enabled = tenant_config.get("investor_match_only", True)
        valuation_enabled = tenant_config.get("valuation_matching", True)
        adverse_enabled = tenant_config.get("adverse_finding", True)
        custom_sop = tenant_config.get("custom_summary_sop", "")
        custom_validator = tenant_config.get("custom_validator_prompt", "")
        target_investors = tenant_config.get("target_investors", [])
        
        # Ensure strict override for custom SOP, handling potential empty strings or whitespace
        if custom_sop and not custom_sop.strip():
             custom_sop = None

        logger.info("Tenant toggles", 
                    investor_match=investor_match_enabled,
                    valuation=valuation_enabled,
                    adverse=adverse_enabled,
                    has_custom_sop=bool(custom_sop),
                    has_custom_validator=bool(custom_validator))
        
        try:
            # PHASE 1: Parallel Data Extraction
            logger.info("Phase 1: Parallel Data Extraction")
            
            agent_1_task = self._agent_1_investor_extractor(namespace, index_name, host, metadata_filter)
            agent_2_task = self._agent_2_capital_history_extractor(namespace, index_name, host, metadata_filter)
            agent_3_task = self._agent_3_summary_generator(namespace, custom_sop, index_name, host, metadata_filter)
            
            # Run agents 1, 2, 3 in parallel
            investor_json, capital_json, draft_summary_result = await asyncio.gather(
                agent_1_task,
                agent_2_task,
                agent_3_task,
                return_exceptions=True
            )
            
            # Initialize usage tracking
            total_usage = {"input": 0, "output": 0}

            # Handle exceptions and ensure dict types
            if isinstance(investor_json, Exception):
                logger.error("Agent 1 exception", error=str(investor_json))
                investor_json = {"error": str(investor_json), "extraction_status": "failed"}
            else:
                u = investor_json.get("_usage", {"input": 0, "output": 0})
                total_usage["input"] += u["input"]
                total_usage["output"] += u["output"]
            
            if isinstance(capital_json, Exception):
                logger.error("Agent 2 exception", error=str(capital_json))
                capital_json = {"error": str(capital_json), "type": "calculation_data"}
            else:
                u = capital_json.get("_usage", {"input": 0, "output": 0})
                total_usage["input"] += u["input"]
                total_usage["output"] += u["output"]
            
            if isinstance(draft_summary_result, Exception):
                logger.error("Agent 3 exception", error=str(draft_summary_result))
                draft_markdown = f"# Error\n\nSummary generation failed: {str(draft_summary_result)}"
            else:
                draft_markdown = draft_summary_result.get("markdown", "")
                u = draft_summary_result.get("usage", {"input": 0, "output": 0})
                total_usage["input"] += u["input"]
                total_usage["output"] += u["output"]
            
            # PHASE 2: Validation
            logger.info("Phase 2: Validation & Verification")
            
            # Determine SOP for validation context: Use custom if available, else default
            validation_sop = custom_sop if custom_sop else MAIN_SUMMARY_SYSTEM_PROMPT

            validation_result = await self._agent_4_summary_validator(
                draft_markdown, 
                namespace, 
                custom_validator, 
                formatting_sop=validation_sop, # Pass the reference SOP
                index_name=index_name, 
                host=host, 
                metadata_filter=metadata_filter
            )
            final_markdown = validation_result.get("markdown", draft_markdown)
            u = validation_result.get("usage", {"input": 0, "output": 0})
            total_usage["input"] += u["input"]
            total_usage["output"] += u["output"]
            
            # PHASE 3: Markdown Conversion
            logger.info("Phase 3: Markdown Conversion")
            
            # Convert Agent 1 output to markdown (if enabled)
            investor_markdown = ""
            if investor_match_enabled and "error" not in investor_json:
                investor_markdown = self.md_converter.convert_investor_json_to_markdown(
                    investor_json
                )
            
            # Convert Agent 2 output to markdown
            # Share capital table ALWAYS included, valuation analysis conditional
            capital_markdown = ""
            if "error" not in capital_json:
                capital_markdown = self.md_converter.convert_capital_json_to_markdown(
                    capital_json,
                    include_valuation_analysis=valuation_enabled
                )
            
            # PHASE 4: Research (Deep Adverse Findings via Perplexity)
            research_markdown = ""
            if adverse_enabled:
                logger.info("Phase 4: Perplexity Research")
                # Extract company name from investor or capital JSON
                company_name = (
                    investor_json.get("company_name") or 
                    capital_json.get("calculation_parameters", {}).get("company_name") or
                    namespace
                )
                # Extract promoter names to improve research accuracy
                investors = self.md_converter._safe_get_list(investor_json, "section_a_extracted_investors")
                promoter_names = [inv.get("investor_name") for inv in investors if inv and "promoter" in str(inv.get("investor_category", "")).lower()]
                promoter_str = ", ".join([str(p) for p in promoter_names[:5]]) if promoter_names else ""

                research_json = await research_service.research_company(
                    company_name=company_name,
                    promoters=promoter_str
                )
                research_markdown = self.md_converter.convert_research_json_to_markdown(research_json)
                
                # Add research usage
                u = research_json.get("_usage", {"input": 0, "output": 0})
                total_usage["input"] += u["input"]
                total_usage["output"] += u["output"]
            
            # PHASE 5: Final Assembly
            logger.info("Phase 5: Final Assembly & Merging")
            
            # Combine Agent 1 (Investors) and Agent 2 (Capital/Valuation)
            combined_capital_investor = ""
            if investor_markdown:
                combined_capital_investor += investor_markdown + "\n\n"
            if capital_markdown:
                combined_capital_investor += capital_markdown

            # Step 1: Insert combined investor/capital data before SECTION VII
            if combined_capital_investor:
                final_markdown = self.md_converter.insert_markdown_before_section(
                    final_markdown,
                    combined_capital_investor,
                    "SECTION VII: FINANCIAL PERFORMANCE",
                    "Matched Investors & Analysis"
                )

            # Step 2: Insert research before Section XII
            if research_markdown:
                final_markdown = self.md_converter.insert_markdown_before_section(
                    final_markdown,
                    research_markdown,
                    "SECTION XII: INVESTMENT INSIGHTS FOR FUND MANAGERS",
                    "Adverse Findings & Research"
                )
            
            # Wrap final doc with metadata (date/time)
            dateTime = datetime.now().strftime("%d/%m/%Y, %I:%M:%S %p")
            header_metadata = f"---\nDate: {dateTime}\n---\n\n"
            final_markdown = header_metadata + final_markdown

            duration = time.time() - start_time
            logger.info("Pipeline Complete", 
                        duration=duration, 
                        total_input_tokens=total_usage["input"],
                        total_output_tokens=total_usage["output"])
            
            return {
                "status": "success",
                "markdown": final_markdown,
                "duration": duration,
                "usage": {
                    "agents_executed": 4,
                    "investor_match_enabled": investor_match_enabled,
                    "valuation_enabled": valuation_enabled,
                    "adverse_enabled": adverse_enabled
                }
            }
            
        except Exception as e:
            logger.error("Summary pipeline failed", error=str(e), exc_info=True)
            return {
                "status": "error",
                "message": f"Summary generation failed: {str(e)}",
                "duration": time.time() - start_time
            }


# Singleton instance
summary_pipeline = SummaryPipeline()
