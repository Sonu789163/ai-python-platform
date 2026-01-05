"""
Document extraction service.
Handles text extraction from various document formats.
"""
from typing import Dict, Any, Optional
import io
import re
from PyPDF2 import PdfReader
from app.core.logging import get_logger

logger = get_logger(__name__)


class ExtractionService:
    """Service for extracting and cleaning text from documents."""
    
    @staticmethod
    def clean_text(text: str) -> str:
        """
        Clean text based on n8n workflow logic.
        """
        if not text:
            return ""
            
        # 1. Remove newlines
        text = text.replace('\n', ' ')
        
        # 2. Collapse whitespace
        text = re.sub(r'\s{2,}', ' ', text)
        
        # 3. Remove "Page 1", "Page 2", etc. (case-insensitive)
        text = re.sub(r'Page\s\d+', '', text, flags=re.IGNORECASE)
        
        # 4. Remove repeated dashes or underscores (3 or more)
        text = re.sub(r'\s*[\-_]{3,}\s*', '', text)
        
        return text.strip()

    @staticmethod
    def extract_text_from_pdf(file_content: bytes) -> str:
        """
        Extract text from PDF document using PyPDF2.
        """
        try:
            logger.info("Extracting text from PDF")
            pdf_file = io.BytesIO(file_content)
            reader = PdfReader(pdf_file)
            
            raw_text = ""
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    raw_text += text + "\n"
            
            cleaned_text = ExtractionService.clean_text(raw_text)
            return cleaned_text
        
        except Exception as e:
            logger.error("PDF extraction failed", error=str(e), exc_info=True)
            raise

    @staticmethod
    def extract_text(
        file_content: bytes,
        file_type: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Extract and clean text from document based on file type.
        """
        logger.info("Starting document extraction", file_type=file_type)
        
        text = ""
        if file_type.lower() == "pdf":
            text = ExtractionService.extract_text_from_pdf(file_content)
        elif file_type.lower() == "txt":
            raw_text = file_content.decode("utf-8")
            text = ExtractionService.clean_text(raw_text)
        else:
            # For this phase, we primary support PDF as per n8n workflow
            raise ValueError(f"Unsupported file type for this pipeline: {file_type}")
        
        result = {
            "text": text,
            "file_type": file_type,
            "char_count": len(text),
            "metadata": metadata or {}
        }
        
        logger.info(
            "Document extraction completed",
            file_type=file_type,
            char_count=len(text)
        )
        
        return result


# Global service instance
extraction_service = ExtractionService()
