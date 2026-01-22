"""
Main Summarization Pipeline (Layer 2)
Orchestrates multi-stage generation, validation, and research.
"""
import asyncio
import time
import json
from typing import Dict, Any, List
from app.core.config import settings
from app.core.logging import get_logger
from app.services.vector_store import vector_store_service
from app.services.embedding import EmbeddingService
from app.services.summarization.prompts import (
    SUBQUERIES, 
    INVESTOR_EXTRACTOR_SYSTEM_PROMPT,
    CAPITAL_HISTORY_EXTRACTOR_PROMPT,
    MAIN_SUMMARY_SYSTEM_PROMPT,
    SUMMARY_VALIDATOR_SYSTEM_PROMPT,
    TARGET_INVESTORS
)
from app.services.summarization.research import research_service
from app.services.summarization.valuation import valuation_service
import openai
from app.services.summarization.formatter import formatter

logger = get_logger(__name__)

class SummaryPipeline:
    def __init__(self):
        self.embedding = EmbeddingService()
        self.client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

    async def _retrieve_context(self, queries: List[str], namespace: str, index_name: str, host: str, vector_top_k: int = 50, rerank_top_n: int = 15) -> str:
        """
        Runs sub-queries to retrieve context from Pinecone and reranks using Cohere.
        """
        from app.services.rerank import rerank_service
        
        all_context = []
        for query in queries:
            query_vector = await self.embedding.embed_text(query)
            index = vector_store_service.get_index(index_name, host=host)
            query_filter = {"documentName": namespace} if namespace and namespace != "" else None

            search_res = index.query(
                vector=query_vector,
                top_k=vector_top_k,
                namespace=namespace or "",
                include_metadata=True,
                filter=query_filter
            )
            initial_chunks = [m['metadata']['text'] for m in search_res['matches']]
            
            if initial_chunks:
                reranked_chunks = rerank_service.rerank(query, initial_chunks, top_n=rerank_top_n)
                all_context.extend(reranked_chunks)
            
        seen = set()
        unique_context = []
        for chunk in all_context:
            if chunk not in seen:
                unique_context.append(chunk)
                seen.add(chunk)
                
        return "\n---\n".join(unique_context)

    def _recalculate_investor_percentages(self, investors: List[Dict], total_shares: float) -> List[Dict]:
        """Code-based recalculation of shareholding percentages."""
        if not total_shares or total_shares <= 0:
            return investors
        
        results = []
        for inv in investors:
            shares = inv.get("number_of_equity_shares", 0)
            if isinstance(shares, str):
                try: shares = float(shares.replace(',', ''))
                except: shares = 0
            
            percentage = (shares / total_shares) * 100
            # Matches n8n logic: toFixed(10) then replace trailing zeros
            percentage_str = f"{percentage:.10f}".rstrip('0').rstrip('.') + "%"
            
            results.append({
                **inv,
                "number_of_equity_shares": shares,
                "percentage_of_pre_issue_capital": percentage_str
            })
        return results

    def _match_investors(self, extracted: List[Dict], target_list: List[str]) -> List[Dict]:
        """Exact matching of extracted investors against target list."""
        matched = []
        target_lower = [t.lower().strip() for t in target_list]
        
        for inv in extracted:
            name = inv.get("investor_name") or inv.get("name")
            if not name: continue
            
            if name.lower().strip() in target_lower:
                matched.append({
                    "investor_name": name,
                    "number_of_equity_shares": inv.get("number_of_equity_shares", 0),
                    "percentage_of_capital": inv.get("percentage_of_pre_issue_capital", "0%"),
                    "investor_category": inv.get("investor_category", "Unknown")
                })
        return matched

    async def generate(self, namespace: str, doc_type: str = "drhp", fund_config: Dict = None) -> Dict[str, Any]:
        """
        Executes the refactored 4-agent summarization pipeline with Fund-specific toggles.
        """
        start_time = time.time()
        fund_config = fund_config or {}
        
        # Extract fund configuration/toggles
        use_matching = fund_config.get("investor_match_only", True)
        use_valuation = fund_config.get("valuation_matching", True)
        use_research = fund_config.get("adverse_finding", True)
        target_investor_list = fund_config.get("target_investors") or TARGET_INVESTORS
        custom_sop = fund_config.get("custom_summary_sop") or MAIN_SUMMARY_SYSTEM_PROMPT
        custom_checklist = fund_config.get("validator_checklist", [])

        try:
            index_name = settings.PINECONE_RHP_INDEX if doc_type == "rhp" else settings.PINECONE_DRHP_INDEX
            host = settings.PINECONE_RHP_HOST if doc_type == "rhp" else settings.PINECONE_DRHP_HOST

            # STAGE 1: Context Retrieval (Parallel)
            logger.info("Retrieving context for enabled agents")
            ctx_tasks = [
                self._retrieve_context(["Extract all types of shareholders, promoter, public shareholders and total capital"], namespace, index_name, host, 40, 15),
                self._retrieve_context(["Extract equity share capital history table and identify premium rounds"], namespace, index_name, host, 40, 15),
                self._retrieve_context(SUBQUERIES, namespace, index_name, host)
            ]
            
            ctx_results = await asyncio.gather(*ctx_tasks)
            investor_ctx = ctx_results[0]
            capital_ctx = ctx_results[1]
            main_ctx = ctx_results[2]

            # STAGE 2: Parallel Agent Execution
            llm_tasks = []
            
            # Agent 1: Investor Extractor
            llm_tasks.append(self.client.chat.completions.create(
                model=settings.SUMMARY_MODEL,
                messages=[{"role": "system", "content": INVESTOR_EXTRACTOR_SYSTEM_PROMPT}, {"role": "user", "content": f"Extract for {namespace}: {investor_ctx}"}],
                response_format={"type": "json_object"}
            ))
            
            # Agent 2: Capital History Extractor (Always called for raw table)
            llm_tasks.append(self.client.chat.completions.create(
                model=settings.SUMMARY_MODEL,
                messages=[{"role": "system", "content": CAPITAL_HISTORY_EXTRACTOR_PROMPT}, {"role": "user", "content": f"Extract for {namespace}: {capital_ctx}"}],
                response_format={"type": "json_object"}
            ))
            
            # Agent 3: Main Summary Generator
            llm_tasks.append(self.client.chat.completions.create(
                model=settings.SUMMARY_MODEL,
                messages=[{"role": "system", "content": custom_sop}, {"role": "user", "content": f"Generate for {namespace}: {main_ctx[:400000]}"}],
                max_tokens=16000
            ))
            
            # Research Task (Conditional)
            research_task = None
            if use_research:
                research_task = asyncio.create_task(research_service.get_adverse_findings(namespace, "Promoters from DRHP"))
            
            llm_results = await asyncio.gather(*llm_tasks)
            
            # --- POST-PROCESSING ---

            # Parse Agent 1 (Investors)
            data1 = json.loads(llm_results[0].choices[0].message.content)
            extracted_inv = data1.get("section_a_extracted_investors", [])
            total_shares = data1.get("total_share_issue", 0)
            
            recalculated_inv = self._recalculate_investor_percentages(extracted_inv, total_shares)
            matched_inv = self._match_investors(recalculated_inv, target_investor_list) if use_matching else []
            investor_html = formatter.generate_investor_report_html(recalculated_inv, matched_inv, show_matches=use_matching)

            # Parse Agent 2 (Capital & Valuation)
            data2 = json.loads(llm_results[1].choices[0].message.content)
            raw_table_md = data2.get("content", "")
            valuation_html = ""
            if use_valuation:
                calc_params = data2.get("calculation_parameters", {}).get("premium_rounds", [])
                calculated_rounds = valuation_service.calculate_premium_rounds(calc_params) if calc_params else []
                calc_html = valuation_service.generate_valuation_html(calculated_rounds)
                valuation_html = formatter.generate_valuation_report_html(raw_table_md, calc_html, show_calculations=True)
            else:
                valuation_html = formatter.generate_valuation_report_html(raw_table_md, show_calculations=False)

            # Parse Agent 3 (Draft Summary)
            draft_summary = llm_results[2].choices[0].message.content

            # STAGE 3: Agent 4 (Validator)
            validator_prompt = SUMMARY_VALIDATOR_SYSTEM_PROMPT
            if custom_checklist:
                validator_prompt += f"\n\n### FUND-SPECIFIC VERIFICATION CHECKLIST:\n" + "\n".join([f"- {c}" for c in custom_checklist])

            agent4_resp = await self.client.chat.completions.create(
                model=settings.SUMMARY_MODEL,
                messages=[
                    {"role": "system", "content": validator_prompt},
                    {"role": "user", "content": f"Draft: {draft_summary}\n\nContext: {main_ctx[:100000]}"}
                ],
                max_tokens=16000
            )
            final_md = agent4_resp.choices[0].message.content

            # ASSEMBLY
            final_report_html = formatter.markdown_to_html(final_md)
            if investor_html:
                final_report_html = formatter.insert_html_before_section(final_report_html, investor_html, "SECTION VII", "Investor Analysis")
            if valuation_html:
                final_report_html = formatter.insert_html_before_section(final_report_html, valuation_html, "SECTION VII", "Valuation Analysis")

            if research_task:
                research_data = await research_task
                research_html = formatter.format_research_report(research_data)
                if research_html:
                    final_report_html = formatter.insert_html_before_section(final_report_html, research_html, "SECTION XII", "Adverse Findings")

            final_html = formatter.wrap_enhanced_html(final_report_html, data1.get("company_name", namespace))

            return {
                "status": "success",
                "html": final_html,
                "summary": final_md,
                "duration": time.time() - start_time,
                "namespace": namespace
            }

        except Exception as e:
            logger.error(f"Pipeline failed: {str(e)}", exc_info=True)
            raise

summary_pipeline = SummaryPipeline()
