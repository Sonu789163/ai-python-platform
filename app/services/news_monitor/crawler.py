
import logging
import json
from google import genai
from google.genai import types
import httpx
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
from pymongo import MongoClient
from app.core.config import settings

logger = logging.getLogger(__name__)

class QuotaExhaustedError(Exception):
    """Raised when an AI model's quota is exhausted."""
    pass

class NewsMonitorCrawler:
    """
    Automated News Crawler Service using Gemini with Google Search Grounding.
    Migrated to google-genai (V2 SDK) for better tool support.
    """
    
    def __init__(self):
        self.client_db = MongoClient(settings.MONGO_URI)
        self.db = self.client_db[settings.MONGO_DB_NAME]
        self.domains_collection = self.db["domains"]
        self.articles_collection = self.db["newsarticles"]
        
        # Configure Gemini
        api_key = settings.GEMINI_API_KEY
        if api_key:
            try:
                self.client = genai.Client(api_key=api_key)
                
                # Preferred models
                model_options = [
                    'gemini-2.0-flash-lite',
                    'gemini-2.0-flash',
                ]
                
                # Check availability and pick the best one
                available_models = [m.name for m in self.client.models.list()]
                self.model_name = None
                for opt in model_options:
                    # check for full path or just name
                    clean_opt = opt if opt.startswith('models/') else f'models/{opt}'
                    if any(clean_opt in m for m in available_models):
                        self.model_name = opt
                        break
                
                if not self.model_name:
                    # Fallback to any flash model
                    flash_models = [m.name for m in available_models if 'flash' in m.lower()]
                    self.model_name = flash_models[0].replace('models/', '') if flash_models else 'gemini-1.5-flash'
                
                logger.info(f"Initialized Gemini Client. Using model: {self.model_name}")
                
                # Default config for research with search grounding
                self.research_config = types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    response_mime_type="application/json"
                )
            except Exception as e:
                logger.error(f"Failed to initialize Gemini Client: {e}")
                self.client = None
        else:
            logger.error("GEMINI_API_KEY not found. News Monitor will not function.")
            self.client = None

    def discover_entities(self, company_name: str) -> Dict[str, List[str]]:
        """Task 1: Discover Promoters, KMPs, and Group Companies for a given company."""
        default_data = {"promoters": [], "kmp": [], "group_companies": []}
        if not self.client:
            return default_data
            
        prompt = f"""
        Identify the following for the Indian company '{company_name}':
        1. Promoters (Individuals or entities)
        2. Key Managerial Personnel (KMP) like CEO, CFO, Directors
        3. Subsidiary or Group Companies
        
        Return the result as a strictly valid JSON object with the following structure:
        {{
            "promoters": ["Name 1", "Name 2"],
            "kmp": ["Name 1", "Name 2"],
            "group_companies": ["Company 1", "Company 2"]
        }}
        Use Google Search to find the most recent and accurate data.
        """
        
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=self.research_config
            )
            
            # Extract JSON from response
            data = None
            if hasattr(response, 'parsed') and response.parsed:
                data = response.parsed 
            else:
                text = response.text
                if "```json" in text:
                    text = text.split("```json")[1].split("```")[0].strip()
                elif "```" in text:
                    text = text.split("```")[1].split("```")[0].strip()
                data = json.loads(text)
                
            if not isinstance(data, dict):
                data = default_data

            logger.info(f"Discovered entities for {company_name}: P:{len(data.get('promoters', []))}, K:{len(data.get('kmp', []))}, G:{len(data.get('group_companies', []))}")
            return data
        except Exception as e:
            # Check for rate limit or search tool errors
            error_str = str(e).lower()
            if "exhausted" in error_str or "retry" in error_str or "quota" in error_str:
                msg = f"Gemini Quota Limit for {company_name} (Discovery)"
                logger.warning(msg)
                # We return default but we need a way to track the error
                raise QuotaExhaustedError(msg)
            else:
                logger.error(f"Error discovering entities for {company_name}: {e}")
                raise e

    def crawl_adverse_news(self, company_name: str, entities: Dict[str, List[str]], domain_id: str) -> List[Dict[str, Any]]:
        """Task 2 & 3: Search for negative news and analyze risk."""
        if not self.client:
            return []
            
        all_entities = [company_name] + entities.get("promoters", []) + entities.get("kmp", []) + entities.get("group_companies", [])
        entities_str = ", ".join(all_entities)
        
        prompt = f"""
        You are an elite financial risk analyst and due diligence investigator. Your job is to analyze real-time news for regulatory breaches and fraud. 
        I am providing you with a specific Indian company and its officially verified Promoters and Key Managerial Personnel (KMPs). 
        Target Company: {company_name} 
        Verified Entities (Promoters, KMPs, Group): {entities_str}

        YOUR INSTRUCTIONS:
        1. Use your Google Search capability to find news articles published strictly within the LAST 1 HOUR regarding the Target Company or any individual in the Verified list.
        2. Search specifically for negative news, including: SEBI violations, show-cause notices, financial fraud, accounting irregularities, loan defaults, insider trading, or arrests.
        3. CRITICAL RULE: Do NOT hallucinate names. Do NOT analyze individuals who share the same name unless the article explicitly links them to the Target Company. Rely ONLY on the search results you fetch right now. Do not rely on your internal training data.
        
        OUTPUT FORMAT: 
        If you find relevant negative news from the last hour, output a structured JSON response in this list format:
        [
            {{
                "status": "FLAGGED",
                "entity": "Name of Company or KMP",
                "title": "Headline of the news",
                "issue_summary": "Short description of the fraud/SEBI issue",
                "citation_url": "The exact URL from your search",
                "source": "Source Name",
                "publishedDate": "YYYY-MM-DD HH:MM",
                "riskLevel": "CRITICAL"
            }}
        ]
        
        If you find NO negative news from the past hour, output exactly: []
        """
        
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=self.research_config
            )
            
            data = None
            if hasattr(response, 'parsed') and response.parsed:
                data = response.parsed
            else:
                text = response.text
                if "```json" in text:
                    text = text.split("```json")[1].split("```")[0].strip()
                elif "```" in text:
                    text = text.split("```")[1].split("```")[0].strip()
                
                if not text or text.strip() == "[]":
                    return []
                data = json.loads(text)
            
            articles = data if isinstance(data, list) else [data] if data else []
            
            # Enrich and map user-provided keys to system keys
            enriched_articles = []
            # (Loop continues below)
            for article in articles:
                if not isinstance(article, dict): continue
                
                # Map user's "issue_summary" to "description" and "citation_url" to "url"
                article["description"] = article.get("issue_summary", "")
                article["url"] = article.get("citation_url", "")
                article["findings"] = article.get("issue_summary", "")
                article["sentiment"] = "negative"
                article["category"] = "regulatory"
                
                article["domainId"] = domain_id
                article["crawledAt"] = datetime.now(timezone.utc)
                try:
                    if "publishedDate" in article:
                        article["publishedDate"] = datetime.fromisoformat(article["publishedDate"].replace('Z', '+00:00'))
                except:
                    pass
                enriched_articles.append(article)
            
            return enriched_articles
        except Exception as e:
            # If search tool limit or other transient error, let the fallback handle it
            error_str = str(e).lower()
            if "exhausted" in error_str or "retry" in error_str or "quota" in error_str:
                msg = f"Gemini Quota Limit for {company_name} (Adverse News)"
                logger.warning(msg)
                raise QuotaExhaustedError(msg)
            else:
                logger.error(f"Gemini Crawl Error for {company_name}: {e}")
                raise e

    def batch_crawl_adverse_news(self, companies: List[str], domain_id: str) -> List[Dict[str, Any]]:
        """Task 2 & 3 (Batched): Search for negative news for multiple companies at once to save quota."""
        if not self.client or not companies:
            return []
            
        companies_str = ", ".join(companies)
        
        current_date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        prompt = f"""
        You are an elite financial risk analyst. 
        Current System Time: {current_date_str}
        
        Your job is to analyze real-time news for regulatory breaches and fraud across multiple Indian companies simultaneously.
        
        TARGET LIST OF COMPANIES: {companies_str}

        YOUR INSTRUCTIONS:
        1. Use your Google Search capability to find news articles published strictly within the LAST 24 HOURS regarding ANY of the companies in the list.
        2. DO NOT return any news published before { (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M") }.
        3. Search specifically for negative news, including: SEBI violations, show-cause notices, financial fraud, accounting irregularities, loan defaults, insider trading, or arrests.
        4. CRITICAL RULE: Focus ONLY on these specific companies. Do NOT hallucinate. Do NOT analyze unrelated entities or historical data.
        
        OUTPUT FORMAT: 
        If you find relevant adverse news, output a structured JSON response as a list of objects.
        If NO negative news is found for any company, output exactly: []

        JSON fields:
        - status: "FLAGGED"
        - entity: the exact company name from the list
        - title: the discovery headline
        - issue_summary: short description of the fraud/SEBI issue
        - citation_url: exact URL
        - source: source name
        - publishedDate: YYYY-MM-DD HH:MM
        - riskLevel: "CRITICAL" or "HIGH"
        """
        
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=self.research_config
            )
            
            data = None
            if hasattr(response, 'parsed') and response.parsed:
                data = response.parsed
            else:
                text = response.text
                if "```json" in text:
                    text = text.split("```json")[1].split("```")[0].strip()
                elif "```" in text:
                    text = text.split("```")[1].split("```")[0].strip()
                
                if not text or text.strip() == "[]":
                    return []
                # Use regex to find the first valid list if loads fails
                try:
                    data = json.loads(text)
                except:
                    import re
                    match = re.search(r'\[.*\]', text, re.DOTALL)
                    if match:
                        data = json.loads(match.group(0))
            
            articles = data if isinstance(data, list) else [data] if data else []
            
            enriched_articles = []
            for article in articles:
                if not isinstance(article, dict): continue
                
                article["description"] = article.get("issue_summary", "")
                article["url"] = article.get("citation_url", "")
                article["findings"] = article.get("issue_summary", "")
                article["sentiment"] = "negative"
                article["category"] = "regulatory"
                article["company"] = article.get("entity", "Unknown")
                
                article["domainId"] = domain_id
                article["crawledAt"] = datetime.now(timezone.utc)
                enriched_articles.append(article)
            
            return enriched_articles
        except Exception as e:
            error_str = str(e).lower()
            if "exhausted" in error_str or "retry" in error_str or "quota" in error_str:
                logger.warning("Gemini Batch Crawl Quota Limit hit.")
                raise QuotaExhaustedError("Gemini Batch Quota Exhausted")
            else:
                logger.error(f"Gemini Batch Crawl Error: {e}")
                raise e

    def merge_articles(self, articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Deduplicate and merge articles by company name."""
        if not articles:
            return []
            
        merged = {}
        for art in articles:
            company = art.get("company") or art.get("entityName") or "Unknown"
            if company not in merged:
                # First time seeing this company
                merged[company] = art.copy()
                # Ensure we have a list for URLs if we want to show multiple
                if "url" in art:
                    merged[company]["citations"] = [art["url"]]
            else:
                # Merge logic
                existing = merged[company]
                
                # Combine descriptions/findings
                new_desc = art.get("description") or art.get("summary") or ""
                if new_desc and new_desc not in existing.get("description", ""):
                    existing["description"] = existing.get("description", "") + " | " + new_desc
                    existing["findings"] = existing.get("findings", "") + " | " + new_desc
                
                # Combine URLs
                new_url = art.get("url")
                if "citations" not in existing:
                    existing["citations"] = [existing.get("url")] if existing.get("url") else []
                
                if new_url and new_url not in existing["citations"]:
                    existing["citations"].append(new_url)
                    # Update primary url to the most recent one
                    existing["url"] = new_url
                
                # Update source to show multiple if different
                new_source = art.get("source")
                if new_source and new_source not in existing.get("source", ""):
                    existing["source"] = existing.get("source", "") + ", " + new_source
                    
                # Pick higher risk if applicable
                risk_map = {"CLEAR": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
                current_risk = risk_map.get(existing.get("riskLevel", "LOW"), 1)
                new_risk = risk_map.get(art.get("riskLevel", "LOW"), 1)
                if new_risk > current_risk:
                    existing["riskLevel"] = art.get("riskLevel")

        return list(merged.values())

    def crawl_with_perplexity(self, company_name: str, entities: Dict[str, List[str]], domain_id: str) -> List[Dict[str, Any]]:
        """Fallback task using Perplexity API (Looking for news in last 2 days)."""
        if not settings.PERPLEXITY_API_KEY:
            logger.warning("PERPLEXITY_API_KEY not found. Fallback skipped.")
            return []
            
        all_entities = [company_name] + entities.get("promoters", []) + entities.get("kmp", []) + entities.get("group_companies", [])
        entities_str = ", ".join(all_entities)

        current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        prompt = f"""
        You are a Financial Risk Intelligence Agent.
        Current System Time: {current_date}

        Your task is to search the web for strictly RECENT (published within the last 24 HOURS) and FACTUAL news articles related to the following company and its management.
        
        STRICT TIME RULE: DO NOT return any results published before { (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M") }. If news is older than 24 hours, IGNORE IT.

        TARGET ENTITIES:
        Primary Company: {company_name}
        Promoters/KMPs/Group: {entities_str}

        IDENTIFY ONLY NEGATIVE/ADVERSE INFORMATION:
        - Legal cases, lawsuits, FIRs, arrests
        - Regulatory actions (SEBI, RBI, MCA, NCLT, Courts)
        - Fraud, financial misstatements, defaults, debt issues
        - Raids, investigations (ED, CBI, SFIO)
        - Insolvency, bankruptcy, liquidation
        - Business shutdowns or major operational failures

        STRICT QUALITY RULES:
        1. Use ONLY real, verifiable web sources from the LAST 24 HOURS.
        2. IGNORE all historical data, past news, or generic company profiles.
        3. DO NOT speculate or infer risk; only report documented facts.
        4. Always provide the exact source URL.
        5. Prioritize Indian news sources (Economic Times, Reuters India, etc.).
        6. Clearly mention which SPECIFIC ENTITY the news pertains to.

        OUTPUT FORMAT (STRICT JSON ONLY):
        [
            {{
                "entityName": "Specific Name",
                "headline": "Headline",
                "summary": "Summary",
                "date": "YYYY-MM-DD",
                "source": "Source",
                "url": "URL"
            }}
        ]
        If no negative news is found, return exactly [].
        """

        try:
            url = "https://api.perplexity.ai/chat/completions"
            headers = {
                "Authorization": f"Bearer {settings.PERPLEXITY_API_KEY}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": "sonar",
                "messages": [
                    {"role": "system", "content": "You are a specialized risk analyst. Return JSON output only. Ensure the output is a valid JSON array of objects."},
                    {"role": "user", "content": prompt}
                ]
            }
            
            with httpx.Client(timeout=60.0) as client:
                response = client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
                
                content = data["choices"][0]["message"]["content"]
                # Perplexity json_object mode still might return a wrapper
                try:
                    articles = json.loads(content)
                    # If it returned a dict with a list, extract it
                    if isinstance(articles, dict):
                        for key in ["articles", "negativeNews", "results"]:
                            if key in articles:
                                articles = articles[key]
                                break
                    
                    if not isinstance(articles, list):
                        articles = [articles] if isinstance(articles, dict) and "title" in articles else []
                except Exception as je:
                    logger.warning(f"Perplexity JSON parse failed for {company_name}: {je}. Attempting robust recovery.")
                    # Robust recovery: find from first [ to last ]
                    try:
                        import re
                        match = re.search(r'\[.*\]', content, re.DOTALL)
                        if match:
                            articles = json.loads(match.group(0))
                        else:
                            # Try finding first { and last } if it returned a single object instead
                            match_obj = re.search(r'\{.*\}', content, re.DOTALL)
                            if match_obj:
                                articles = [json.loads(match_obj.group(0))]
                            else:
                                articles = []
                    except Exception as e2:
                        logger.error(f"Perplexity Robust Parse Error for {company_name}: {e2}")
                        articles = []

                if not isinstance(articles, list):
                    articles = [articles] if isinstance(articles, dict) else []

                # Clean and enrich
                valid_articles = []
                for article in articles:
                    if isinstance(article, dict) and article.get("url"):
                        # Map Perplexity keys to system keys
                        article["title"] = article.get("headline", "News Finding")
                        article["description"] = article.get("summary", "")
                        article["publishedDate"] = article.get("date", "")
                        article["findings"] = article.get("summary", "")
                        article["company"] = company_name
                        article["category"] = "legal"
                        article["sentiment"] = "negative"
                        article["riskLevel"] = "HIGH"
                        
                        article["domainId"] = domain_id
                        article["crawledAt"] = datetime.now(timezone.utc)
                        article["workspaceId"] = "unknown" # Will be set by save_articles
                        valid_articles.append(article)
                
                logger.info(f"Perplexity found {len(valid_articles)} articles for {company_name}")
                return valid_articles

        except Exception as e:
            logger.error(f"Perplexity Fallback Error for {company_name}: {e}")
            return []

    def save_articles(self, articles: List[Dict[str, Any]], workspace_id: str):
        """Saves articles to MongoDB, avoiding duplicates by URL."""
        if not articles:
            return
            
        for article in articles:
            article["workspaceId"] = workspace_id
            try:
                self.articles_collection.update_one(
                    {"url": article["url"]},
                    {"$set": article},
                    upsert=True
                )
            except Exception as e:
                logger.error(f"Error saving article {article.get('url')}: {e}")

    def run_daily_monitor(self):
        """Runs the daily monitoring job for all enabled domains."""
        logger.info("Starting Daily News Monitor Job...")
        
        # 1. Get all domains with News Monitor enabled
        domains = list(self.domains_collection.find({
            "news_monitor_enabled": True, 
            "status": "active"
        }))
        
        logger.info(f"Found {len(domains)} domains with News Monitor enabled.")
        
        for domain in domains:
            self.run_for_domain(domain.get("domainId"))
        
        logger.info("Daily News Monitor Job Completed.")

    def run_for_domain(self, domain_id: str) -> Dict[str, Any]:
        """Runs the monitoring job for a specific domain immediately."""
        logger.info(f"Starting News Monitor for domain {domain_id}...")
        
        domain = self.domains_collection.find_one({"domainId": domain_id})
        if not domain:
            logger.error(f"Domain {domain_id} not found.")
            return {"success": False, "error": f"Domain {domain_id} not found."}
            
        raw_companies = domain.get("monitored_companies", [])
        monitored_companies = []
        for item in raw_companies:
            # Replace various separators with a uniform one (comma)
            text = item.replace("\r\n", "\n").replace("\r", "\n")
            # Split by newline or comma
            import re
            parts = re.split(r'[\n,]', text)
            monitored_companies.extend([p.strip() for p in parts if p.strip()])
                
        workspace_id = domain.get("workspaceId") or "ws_1758689602670_z3pxonjqn"
        
        if not monitored_companies:
            logger.info(f"No monitored companies for domain {domain_id}. Skipping.")
            return {"success": True, "article_count": 0, "message": "No companies to monitor."}
            
        # Consolidate all news found
        all_news = []
        total_articles = 0
        errors = []
        
        # Try Batch Crawl with Gemini first (Most efficient)
        companies_to_individual_check = monitored_companies.copy()
        try:
            batch_news = self.batch_crawl_adverse_news(monitored_companies, domain_id)
            if batch_news:
                logger.info(f"Batch Gemini found {len(batch_news)} adverse articles.")
                all_news.extend(batch_news)
                
                # Check which companies got news, we might still want to check others with Perplexity
                for art in batch_news:
                    ent = art.get("company", "")
                    if ent in companies_to_individual_check:
                        companies_to_individual_check.remove(ent)
        except QuotaExhaustedError as qe:
            errors.append(str(qe))
            logger.warning("Gemini Batch failed, falling back to individual checks...")
        except Exception as e:
            logger.error(f"Gemini Batch error: {e}")
            errors.append(f"Batch Error: {str(e)}")

        # For remaining companies or if batch failed, use Perplexity fallback
        for company in companies_to_individual_check:
            try:
                logger.info(f"Fallback/Deep search: {company}")
                entities = {"promoters": [], "kmp": [], "group_companies": []}
                news = self.crawl_with_perplexity(company, entities, domain_id)
                if news:
                    all_news.extend(news)
            except Exception as e:
                errors.append(f"Fallback Error for {company}: {str(e)}")

        # Step 3: Merge and Save
        if all_news:
            merged_news = self.merge_articles(all_news)
            logger.info(f"Consolidated into {len(merged_news)} company cards from {len(all_news)} findings.")
            self.save_articles(merged_news, workspace_id)
            total_articles = len(merged_news)
        else:
            logger.info("No adverse articles found for any company.")
            total_articles = 0
                
        logger.info(f"News Monitor for domain {domain_id} Completed. Total Unique Companies: {total_articles}")
        
        return {
            "success": len(errors) < len(monitored_companies),
            "article_count": total_articles,
            "errors": errors if errors else None,
            "message": f"Crawl completed. Found findings for {total_articles} companies."
        }

def run_monitor(domain_id: Optional[str] = None):
    """Entry point for Celery task or instant trigger."""
    crawler = NewsMonitorCrawler()
    if domain_id:
        return crawler.run_for_domain(domain_id)
    else:
        return crawler.run_daily_monitor()
