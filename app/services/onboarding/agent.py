
import logging
from pymongo import MongoClient
from app.core.config import settings
from app.services.summarization.prompts import (
    SUMMARY_VALIDATOR_SYSTEM_PROMPT, 
    MAIN_SUMMARY_INSTRUCTIONS
)
import openai

logger = logging.getLogger(__name__)

class OnboardingAgent:
    def __init__(self):
        self.client = MongoClient(settings.MONGO_URI)
        self.db = self.client[settings.MONGO_DB_NAME]
        self.collection = self.db["domains"]
        self.openai_client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)

    def generate_custom_validator_prompt(self, custom_sop_text: str) -> str:
        """
        Generates a custom validator prompt based on the tenant's specific SOP format.
        Takes the existing expert validator rules but adapts the checklist to the new format.
        """
        system_instructions = """
        You are an expert AI prompt engineer.
        Your task is to create a specialized System Prompt for a Validation Agent based on a custom Summary SOP provided by a new tenant.

        GOAL:
        Create a new, comprehensive System Prompt for the Validation Agent that:
        1. Retains the core identity and strict validation rules from the BASE VALIDATION PROMPT (accuracy, verification, etc.).
        2. Replaces any generic format references with SPECIFIC checks for the sections defined in the CUSTOM SOP FORMAT.
        3. Ensuring the validator checks for every section, table, and data point explicitly mentioned in the CUSTOM SOP.
        4. The output must be the FULL system prompt, ready to be used by the validation AI agent.

        BASE VALIDATION PROMPT:
        {base_prompt}

        CUSTOM SOP FORMAT:
        {custom_sop}

        OUTPUT FORMAT:
        Return ONLY the generated system prompt text. Do not include markdown code block formatting or explanations.
        """

        try:
            response = self.openai_client.chat.completions.create(
                model="gpt-4.1-mini",  # Using a strong model for prompt generation
                messages=[
                    {"role": "system", "content": system_instructions.format(
                        base_prompt=SUMMARY_VALIDATOR_SYSTEM_PROMPT,
                        custom_sop=custom_sop_text
                    )},
                    {"role": "user", "content": "Generate the custom validator system prompt now."}
                ],
                temperature=0.2,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Failed to generate custom validator prompt: {e}")
            # Fallback to default if generation fails, ensuring system robustness
            return SUMMARY_VALIDATOR_SYSTEM_PROMPT


    def extract_text(self, file_content: bytes, filename: str) -> str:
        """
        Extracts text from PDF or DOCX file content.
        """
        import io
        text = ""
        try:
            if filename.lower().endswith(".pdf"):
                from PyPDF2 import PdfReader
                reader = PdfReader(io.BytesIO(file_content))
                for page in reader.pages:
                    text += page.extract_text() + "\n"
            elif filename.lower().endswith(".docx"):
                from docx import Document
                doc = Document(io.BytesIO(file_content))
                for para in doc.paragraphs:
                    text += para.text + "\n"
            else:
                # Assume text
                text = file_content.decode("utf-8", errors="ignore")
        except Exception as e:
            logger.error(f"Error extracting text from {filename}: {e}")
            return ""
        return text

    def analyze_and_create_format_prompt(self, raw_sop_text: str) -> str:
        """
        Analyzes the RAW SOP text (extracted from file) and converts it into a 
        clean, structured PROMPT TEMPLATE similar to DEFAULT_SUMMARY_FORMAT.
        """
        system_instructions = """
        You are an expert Prompt Engineer.
        Your task is to convert a raw "Standard Operating Procedure" (SOP) document into a specialized "REQUIRED FORMAT AND STRUCTURE" prompt template.

        GOAL:
        Create a clean, markdown-formatted template that an AI agent uses to structure a financial summary.
        The output must be ONLY the format/structure part (like headers, bullet points, tables with placeholders).
        
        RULES:
        1. Read the raw SOP content.
        2. Identify all required sections, tables, and data points.
        3. Convert them into a template format using Markdown.
        4. Use placeholders like [Amount], [Date], [%], etc.
        5. Do NOT include instructions on "how" to extract (unless critical formatting notes).
        6. Start with "## REQUIRED FORMAT AND STRUCTURE:"
        
        RAW SOP CONTENT:
        {raw_sop}

        OUTPUT:
        Return ONLY the generated template string.
        """
        try:
            response = self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": system_instructions.format(raw_sop=raw_sop_text)},
                    {"role": "user", "content": "Generate the format template."}
                ],
                temperature=0.2,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Failed to generate format prompt: {e}")
            return ""

    def process_new_tenant(self, domain_id: str, custom_sop_input: str, is_raw_text: bool = False, toggles: dict = {}):
        """
        Processes a new tenant's onboarding configuration.
        Args:
            domain_id: Tenant ID
            custom_sop_input: Either the final template (if is_raw_text=False) or RAW SOP text/file content (if is_raw_text=True)
            is_raw_text: Flag to indicate if input allows AI analysis/transformation
            toggles: Feature flags
        """
        logger.info(f"Processing onboarding for domain: {domain_id}")

        final_summary_format = custom_sop_input

        # 1. If input is Raw Text (from file upload), Analyze & Convert to Template
        if is_raw_text and custom_sop_input:
            logger.info("Analyzing raw SOP text to generate format template...")
            generated_format = self.analyze_and_create_format_prompt(custom_sop_input)
            if generated_format:
                final_summary_format = generated_format
        
        # 2. Generate Custom Validator Prompt based on the FINAL format
        custom_validator_prompt = ""
        if final_summary_format:
            logger.info("Generating custom validator prompt...")
            custom_validator_prompt = self.generate_custom_validator_prompt(final_summary_format)
        
        # 3. Prepare Update Data
        update_data = {
            "custom_summary_sop": final_summary_format if final_summary_format else "",
            "custom_validator_prompt": custom_validator_prompt,
            "investor_match_only": toggles.get("investor_match_only", False),
            "valuation_matching": toggles.get("valuation_matching", False),
            "adverse_finding": toggles.get("adverse_finding", False),
        }
        
        if "target_investors" in toggles:
            update_data["target_investors"] = toggles["target_investors"]

        try:
            result = self.collection.update_one(
                {"domainId": domain_id},
                {"$set": update_data},
                upsert=True 
            )
            logger.info(f"Successfully updated tenant config for {domain_id}. Modified: {result.modified_count}")
            return True
        except Exception as e:
            logger.error(f"Failed to update tenant config: {e}")
            return False

# Function to be called from API or Script
def onboard_tenant(domain_id: str, custom_sop_input: str, is_raw_text: bool, toggles: dict):
    agent = OnboardingAgent()
    return agent.process_new_tenant(domain_id, custom_sop_input, is_raw_text, toggles)
