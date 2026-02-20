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
        vector_top_k: int = 15,
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
                    # Create filtered copy without "documentName"
                    legacy_filter_dict = {k: v for k, v in (query_filter or {}).items() if k != "documentName"}
                    legacy_filter: Optional[Dict[str, Any]] = legacy_filter_dict if legacy_filter_dict else None
                        
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
                temperature=0.1,
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
        custom_subqueries: Optional[List[str]] = None,
        index_name: str = None,
        host: str = None,
        metadata_filter: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Agent 3: DRHP Summary Generator (n8n-style: Collect-then-Generate)
        Node: A-3:-DRHP Summary Generator Agent1
        
        Flow (matching n8n workflow):
          Phase 1: Loop through ALL subqueries → retrieve chunks for each → collect ALL chunks
          Phase 2: ONE single LLM call with ALL collected context → generate full summary
        """
        logger.info("Agent 3: Summary Generator - Starting (n8n-style Collect-then-Generate)", namespace=namespace)
        
        # Resolve subqueries: use custom if provided, else fall back to defaults
        active_subqueries = custom_subqueries if custom_subqueries else SUBQUERIES
        logger.info(f"Agent 3: Using {len(active_subqueries)} subqueries (custom={bool(custom_subqueries)})")
        
        # ── PHASE 1: Collect ALL chunks from ALL subqueries ──
        logger.info("Agent 3 Phase 1: Retrieving chunks for all subqueries...")
        all_chunks = []
        seen_chunks = set()
        
        for i, query in enumerate(active_subqueries):
            try:
                context = await self._retrieve_context(
                    [query],
                    namespace,
                    index_name,
                    host,
                    vector_top_k=15,
                    rerank_top_n=10,
                    metadata_filter=metadata_filter
                )
                
                if not context:
                    logger.warning(f"Agent 3: No context found for subquery {i+1}/{len(active_subqueries)}", query=query[:80])
                    continue
                
                # Split retrieved context into individual chunks and deduplicate
                chunks = context.split("\n---\n")
                new_chunks = 0
                for chunk in chunks:
                    chunk_stripped = chunk.strip()
                    if chunk_stripped and chunk_stripped not in seen_chunks:
                        all_chunks.append(chunk_stripped)
                        seen_chunks.add(chunk_stripped)
                        new_chunks += 1
                
                logger.debug(f"Agent 3: Subquery {i+1}/{len(active_subqueries)} retrieved {new_chunks} new chunks (total: {len(all_chunks)})")
                
            except Exception as e:
                logger.error(f"Agent 3: Failed to retrieve for subquery {i+1}", error=str(e))
                continue
        
        if not all_chunks:
            return {"markdown": "# Error\n\nNo DRHP data found for summary generation.", "usage": {"input": 0, "output": 0}}
        
        logger.info(f"Agent 3 Phase 1 Complete: Collected {len(all_chunks)} unique chunks from {len(active_subqueries)} subqueries")
        
        # ── PHASE 2: Single LLM call with ALL collected context ──
        logger.info("Agent 3 Phase 2: Generating full summary from collected context...")
        
        # Combine all chunks into one context block
        full_context = "\n\n---\n\n".join(all_chunks)
        
        # Build the subqueries reference for the user message
        subqueries_list = "\n".join([f"{i+1}. {sq}" for i, sq in enumerate(active_subqueries)])
        
        # Use the domain SOP as the system prompt
        system_prompt = custom_sop
        
        try:
            response = await self.client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": (
                        f"Generate a complete, comprehensive DRHP summary covering ALL of the following areas:\n\n"
                        f"AREAS TO COVER:\n{subqueries_list}\n\n"
                        f"DRHP CONTEXT DATA:\n{full_context}"
                    )}
                ],
                temperature=0.1,
                max_tokens=16384
            )
            
            usage = response.usage
            full_summary = response.choices[0].message.content
            
            logger.info("Agent 3: Completed Full Summary Generation", 
                        context_chunks=len(all_chunks),
                        input_tokens=usage.prompt_tokens,
                        output_tokens=usage.completion_tokens)
            
            return {
                "markdown": full_summary,
                "usage": {
                    "input": usage.prompt_tokens,
                    "output": usage.completion_tokens
                }
            }
            
        except Exception as e:
            logger.error("Agent 3: Summary generation failed", error=str(e), exc_info=True)
            return {
                "markdown": f"# Error\n\nSummary generation failed: {str(e)}",
                "usage": {"input": 0, "output": 0}
            }
    
    async def _agent_4_summary_validator(
        self,
        draft_summary: str,
        namespace: str,
        custom_validator_prompt: Optional[str] = None,
        formatting_sop: Optional[str] = None,
        custom_subqueries: Optional[List[str]] = None,
        index_name: str = None,
        host: str = None,
        metadata_filter: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Agent 4: DRHP Summary Validator/Previewer (n8n-style: Collect-then-Validate)
        Node: A-4:-DRHP Summary Previewer
        
        Flow (matching n8n workflow):
          Phase 1: Loop through ALL subqueries → retrieve chunks for each → collect ALL chunks
          Phase 2: ONE single LLM call with draft summary + ALL collected context → validate & correct
        
        In n8n, Agent 4 receives $json.output (draft summary) as user prompt and has
        Pinecone Vector Store3 as a tool. The agent autonomously queries Pinecone to
        cross-verify every data point. We replicate this by pre-retrieving context
        for all subqueries and passing it alongside the draft summary.
        
        Returns: {markdown: str, usage: dict}
        """
        logger.info("Agent 4: Summary Validator - Starting (n8n-style Collect-then-Validate)", namespace=namespace)
        
        # Resolve subqueries: use custom if provided, else fall back to defaults (same as Agent 3)
        active_subqueries = custom_subqueries if custom_subqueries else SUBQUERIES
        logger.info(f"Agent 4: Using {len(active_subqueries)} subqueries for validation (custom={bool(custom_subqueries)})")
        
        # ── PHASE 1: Collect ALL chunks from ALL subqueries (same pattern as Agent 3) ──
        logger.info("Agent 4 Phase 1: Retrieving chunks for validation...")
        all_chunks = []
        seen_chunks = set()
        
        for i, query in enumerate(active_subqueries):
            try:
                context = await self._retrieve_context(
                    [query],
                    namespace,
                    index_name,
                    host,
                    vector_top_k=10,
                    rerank_top_n=10,
                    metadata_filter=metadata_filter
                )
                
                if not context:
                    logger.warning(f"Agent 4: No context found for subquery {i+1}/{len(active_subqueries)}", query=query[:80])
                    continue
                
                # Split retrieved context into individual chunks and deduplicate
                chunks = context.split("\n---\n")
                new_chunks = 0
                for chunk in chunks:
                    chunk_stripped = chunk.strip()
                    if chunk_stripped and chunk_stripped not in seen_chunks:
                        all_chunks.append(chunk_stripped)
                        seen_chunks.add(chunk_stripped)
                        new_chunks += 1
                
                logger.debug(f"Agent 4: Subquery {i+1}/{len(active_subqueries)} retrieved {new_chunks} new chunks (total: {len(all_chunks)})")
                
            except Exception as e:
                logger.error(f"Agent 4: Failed to retrieve for subquery {i+1}", error=str(e))
                continue
        
        if not all_chunks:
            logger.warning("Agent 4: No context for validation, returning draft as-is")
            return {"markdown": draft_summary, "usage": {"input": 0, "output": 0}}
        
        logger.info(f"Agent 4 Phase 1 Complete: Collected {len(all_chunks)} unique chunks from {len(active_subqueries)} subqueries")
        
        # ── PHASE 2: Single LLM call with draft summary + ALL collected context ──
        logger.info("Agent 4 Phase 2: Validating and correcting summary...")
        
        # Combine all chunks into one context block
        full_context = "\n\n---\n\n".join(all_chunks)
        
        # Build system prompt: validator prompt + formatting SOP (matches n8n Agent 4 system message)
        system_prompt = custom_validator_prompt
        
        # Append the Formatting SOP so the validator knows the target structure
        if formatting_sop:
            system_prompt += f"\n\n----------------------------------------------------------------\nREFERENCE STANDARD OPERATING PROCEDURE (SOP) / FORMAT:\n----------------------------------------------------------------\n{formatting_sop}"

        try:
            # n8n Agent 4 receives draft summary as user prompt ($json.output)
            # We provide it along with the collected DRHP context for cross-verification
            response = await self.client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": (
                        f"{draft_summary}\n\n"
                        f"----------------------------------------------------------------\n"
                        f"DRHP/RHP CONTEXT DATA FOR CROSS-VERIFICATION:\n"
                        f"----------------------------------------------------------------\n"
                        f"{full_context}"
                    )}
                ],
                temperature=0.1,
                max_tokens=16384
            )
            
            usage = response.usage
            final_summary = response.choices[0].message.content
            
            logger.info("Agent 4: Completed Full Validation", 
                        context_chunks=len(all_chunks),
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
            logger.error("Agent 4: Validation failed, returning draft", error=str(e), exc_info=True)
            return {"markdown": draft_summary, "error": str(e), "usage": {"input": 0, "output": 0}}
    
    async def generate_summary(
        self,
        namespace: str,
        domain_id: str,
        tenant_config: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
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
            metadata: Document metadata for filtering (documentId, documentType, etc.)
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
        
        # Strict metadata filtering based on user requirement:
        # documentName, documentId, domain, domainId, type = documentType
        
        # 1. documentName (namespace)
        if namespace:
            metadata_filter["documentName"] = namespace
            
        # 2. domainId & domain
        if domain_id:
            metadata_filter["domainId"] = domain_id
            # If domain name is available in metadata, add it too
            if metadata and "domain" in metadata:
                 metadata_filter["domain"] = metadata["domain"]
        
        # 3. documentId
        if metadata and "documentId" in metadata:
            metadata_filter["documentId"] = metadata["documentId"]
            
        # 4. type (documentType)
        if metadata and "documentType" in metadata:
            metadata_filter["type"] = metadata["documentType"]
            
        logger.info("Using strict metadata filter", filter=metadata_filter)
            
        # Default tenant config
        if not tenant_config:
            tenant_config = {}
        
        # Feature Toggles
        investor_match_enabled = tenant_config.get("investor_match_only", False)
        valuation_enabled = tenant_config.get("valuation_matching", False)
        adverse_enabled = tenant_config.get("adverse_finding", False)
        
        # Agent 3 prompt: agent3_prompt (from onboarding) -> fallback to DEFAULT
        custom_sop = tenant_config.get("agent3_prompt")
        
        # If custom prompt is missing or empty, fallback to default
        if not custom_sop or not custom_sop.strip():
             logger.info("Agent 3: Using Default System SOP from prompts.py")
             custom_sop = MAIN_SUMMARY_SYSTEM_PROMPT
        else:
             logger.info("Agent 3: Using Custom SOP from Domain Schema", preview=custom_sop[:100])

        
        # Agent 4 prompt: agent4_prompt (from onboarding) -> fallback to DEFAULT
        custom_validator = tenant_config.get("agent4_prompt")
        
        if not custom_validator or not custom_validator.strip():
             logger.info("Agent 4: Using Default Validator Prompt from prompts.py")
             custom_validator = SUMMARY_VALIDATOR_SYSTEM_PROMPT
        else:
             logger.info("Agent 4: Using Custom Validator Prompt from Domain Schema")

        # Subqueries: custom_subqueries (from onboarding) -> default SUBQUERIES
        custom_subqueries = tenant_config.get("custom_subqueries", []) or []
        # Validate: must be a non-empty list of strings
        if custom_subqueries and isinstance(custom_subqueries, list) and len(custom_subqueries) > 0:
            custom_subqueries = [sq for sq in custom_subqueries if isinstance(sq, str) and sq.strip()]
        else:
            custom_subqueries = None  # Will fall back to default SUBQUERIES in agents

        logger.info("Tenant config resolved", 
                    investor_match=investor_match_enabled,
                    valuation=valuation_enabled,
                    adverse=adverse_enabled,
                    has_custom_sop=bool(custom_sop),
                    has_custom_validator=bool(custom_validator),
                    custom_subqueries_count=len(custom_subqueries) if custom_subqueries else 0)
        
        try:
            # PHASE 1: Parallel Data Extraction
            logger.info("Phase 1: Parallel Data Extraction")
            
            agent_1_task = self._agent_1_investor_extractor(namespace, index_name, host, metadata_filter)
            agent_2_task = self._agent_2_capital_history_extractor(namespace, index_name, host, metadata_filter)
            agent_3_task = self._agent_3_summary_generator(namespace, custom_sop, custom_subqueries, index_name, host, metadata_filter)
            
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
                # LOGGING AGENT 3 OUTPUT (FULL)
                logger.info("=== AGENT 3 OUTPUT (DRAFT SUMMARY) START ===")
                print(draft_markdown)
                logger.info("=== AGENT 3 OUTPUT (DRAFT SUMMARY) END ===")
            
            # PHASE 2: Validation
            logger.info("Phase 2: Validation & Verification")
            
            # Determine SOP for validation context: Use our resolved custom_sop (which has default if needed)
            validation_sop = custom_sop

            validation_result = await self._agent_4_summary_validator(
                draft_markdown, 
                namespace, 
                custom_validator, 
                formatting_sop=validation_sop, # Pass the reference SOP
                custom_subqueries=custom_subqueries, # Same subqueries for consistent coverage
                index_name=index_name, 
                host=host, 
                metadata_filter=metadata_filter
            )
            final_markdown = validation_result.get("markdown", draft_markdown)
            u = validation_result.get("usage", {"input": 0, "output": 0})
            total_usage["input"] += u["input"]
            total_usage["output"] += u["output"]
            
            # LOGGING AGENT 4 OUTPUT (FULL)
            logger.info("=== AGENT 4 OUTPUT (FINAL VALIDATED SUMMARY) START ===")
            print(final_markdown)
            logger.info("=== AGENT 4 OUTPUT (FINAL VALIDATED SUMMARY) END ===")
            
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
