
from fastapi import APIRouter, HTTPException, BackgroundTasks, UploadFile, File, Form
from pydantic import BaseModel, Json
from typing import Optional, Dict, List
from app.services.onboarding.agent import onboard_tenant, OnboardingAgent
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

# Request model for JSON part (toggles)
class OnboardingConfig(BaseModel):
    toggles: Optional[Dict[str, bool]] = {}
    targetInvestors: Optional[List[str]] = []

@router.post("/setup")
async def setup_tenant(
    domainId: str = Form(...),
    config: Json[OnboardingConfig] = Form(...),
    file: Optional[UploadFile] = File(None),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    """
    Trigger the Onboarding Agent to setup tenant configuration.
    Accepts an optional file (PDF/DOCX) for SOP analysis.
    """
    logger.info(f"Received onboarding request for {domainId}")
    
    # Merge targetInvestors into toggles
    toggles = config.toggles.copy()
    if config.targetInvestors:
        toggles["target_investors"] = config.targetInvestors
    
    custom_sop_input = ""
    is_raw_text = False
    
    # Process File if Uploaded
    if file:
        logger.info(f"Processing uploaded file: {file.filename}")
        content = await file.read()
        # Extract text immediately (sync) or pass content to background?
        # Better to extract text here quickly using the agent utility
        agent = OnboardingAgent()
        extracted_text = agent.extract_text(content, file.filename)
        if extracted_text:
            custom_sop_input = extracted_text
            is_raw_text = True
        else:
            logger.warning("Failed to extract text from uploaded file")

    # Run in background
    background_tasks.add_task(
        onboard_tenant, 
        domainId, 
        custom_sop_input,
        is_raw_text,
        toggles
    )
    
    return {"status": "processing", "message": "Onboarding started in background"}
