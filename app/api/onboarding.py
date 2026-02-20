
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
    
    This endpoint handles both initial onboarding and re-onboarding.
    When called with a new SOP file, the onboarding agent will:
      1. Parse and analyze the SOP
      2. Refactor subqueries (Task 1)
      3. Customize Agent 3 prompt (Task 2)
      4. Customize Agent 4 prompt (Task 3)
      5. Store all configs in MongoDB
    
    Accepts optional file (PDF/DOCX) for SOP analysis.
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
        agent = OnboardingAgent()
        extracted_text = agent.extract_text(content, file.filename)
        if extracted_text:
            custom_sop_input = extracted_text
            is_raw_text = True
        else:
            logger.warning("Failed to extract text from uploaded file")

    # Run in background (onboarding can take 30-60s due to LLM calls)
    background_tasks.add_task(
        onboard_tenant, 
        domainId, 
        custom_sop_input,
        is_raw_text,
        toggles
    )
    
    return {
        "status": "processing",
        "message": "Onboarding started in background",
        "domain_id": domainId,
        "has_sop": bool(custom_sop_input),
        "tasks": [
            "Task 1: Subquery refactoring",
            "Task 2: Agent 3 prompt customization",
            "Task 3: Agent 4 prompt customization",
        ] if custom_sop_input else ["Storing toggle configuration only"]
    }


@router.post("/re-onboard")
async def re_onboard_tenant(
    domainId: str = Form(...),
    config: Json[OnboardingConfig] = Form(...),
    file: Optional[UploadFile] = File(None),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    """
    Re-onboarding endpoint. Called when tenant updates their SOP.
    
    This triggers the full onboarding flow again:
      1. Re-analyze updated SOP
      2. Refactor subqueries
      3. Regenerate Agent 3 prompt
      4. Regenerate Agent 4 prompt
      5. Overwrite stored configs in MongoDB
    """
    logger.info(f"Received RE-onboarding request for {domainId}")
    
    # Merge targetInvestors into toggles
    toggles = config.toggles.copy()
    if config.targetInvestors:
        toggles["target_investors"] = config.targetInvestors
    
    custom_sop_input = ""
    is_raw_text = False
    
    # File is required for re-onboarding (must provide updated SOP)
    if file:
        logger.info(f"Processing updated SOP file: {file.filename}")
        content = await file.read()
        agent = OnboardingAgent()
        extracted_text = agent.extract_text(content, file.filename)
        if extracted_text:
            custom_sop_input = extracted_text
            is_raw_text = True
        else:
            raise HTTPException(
                status_code=400,
                detail="Failed to extract text from uploaded SOP file"
            )
    else:
        raise HTTPException(
            status_code=400,
            detail="SOP file is required for re-onboarding. Please upload updated SOP."
        )

    # Run in background
    background_tasks.add_task(
        onboard_tenant, 
        domainId, 
        custom_sop_input,
        is_raw_text,
        toggles
    )
    
    return {
        "status": "processing",
        "message": "Re-onboarding started. Pipeline configs will be updated.",
        "domain_id": domainId,
        "tasks": [
            "Task 1: Subquery re-analysis",
            "Task 2: Agent 3 prompt regeneration",
            "Task 3: Agent 4 prompt regeneration",
            "Task 4: MongoDB config overwrite",
        ]
    }


@router.get("/status/{domain_id}")
async def get_onboarding_status(domain_id: str):
    """
    Get the current onboarding status and configuration for a tenant.
    Returns the stored SOP analysis, custom prompts, and toggle settings.
    """
    try:
        agent = OnboardingAgent()
        config = agent.collection.find_one({"domainId": domain_id})
        
        if not config:
            return {
                "status": "not_found",
                "message": f"No configuration found for domain {domain_id}",
                "onboarding_required": True
            }
        
        # Remove MongoDB _id and large prompt texts for status endpoint
        if "_id" in config:
            del config["_id"]
        
        return {
            "status": "found",
            "domain_id": domain_id,
            "onboarding_status": config.get("onboarding_status", "unknown"),
            "last_onboarded": config.get("last_onboarded"),
            "has_sop": bool(config.get("sop_text")),
            "has_custom_subqueries": bool(config.get("custom_subqueries")),
            "custom_subqueries_count": len(config.get("custom_subqueries", [])),
            "has_agent3_prompt": bool(config.get("agent3_prompt")),
            "has_agent4_prompt": bool(config.get("agent4_prompt")),
            "toggles": {
                "investor_match_only": config.get("investor_match_only", False),
                "valuation_matching": config.get("valuation_matching", False),
                "adverse_finding": config.get("adverse_finding", False),
            },
            "target_investors": config.get("target_investors", []),
            "subquery_analysis": config.get("subquery_analysis", {}),
        }
    except Exception as e:
        logger.error(f"Failed to get onboarding status: {e}")
        raise HTTPException(status_code=500, detail=str(e))
