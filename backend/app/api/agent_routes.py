"""
Agent API routes - Multi-step code generation with semantic retrieval.
"""
import logging
from fastapi import APIRouter, HTTPException
from pathlib import Path

from app.schemas import (
    AgentRequest, 
    AgentResponse, 
    AgentStepInfo,
    IntentInfo,
    PlanInfo,
    FileDiff
)
from app.services.agent.loop import run_agent
from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()


def get_project_path(project: str) -> Path:
    """Resolve and validate project path."""
    backend_dir = Path(__file__).parent.parent.parent
    project_path = backend_dir / settings.projects_base_path / project
    
    if not project_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Project '{project}' not found"
        )
    
    return project_path


@router.post("/generate", response_model=AgentResponse)
async def agent_generate(request: AgentRequest) -> AgentResponse:
    """
    Multi-step agent for code generation.
    
    Pipeline:
    1. Intent parsing (model_intent) - Classify task, extract hints
    2. Retrieval (embeddings + multi-signal) - Find relevant files
    3. Planning (model_planner) - Create execution plan
    4. Execution (model_executor) - Generate code with retry loop
    5. Build validation (npm run build)
    
    All models are configurable in .env (MODEL_INTENT, MODEL_PLANNER, MODEL_EXECUTOR).
    """
    logger.info(f"[Agent] Request: project={request.project}, instruction={request.instruction[:50]}...")
    
    project_path = get_project_path(request.project)
    
    try:
        result = await run_agent(
            instruction=request.instruction,
            project=request.project,
            project_path=project_path,
            max_retries=settings.agent_max_retries,
            retrieval_top_k=settings.agent_retrieval_top_k,
            validate_build=settings.agent_validate_build,
            validation_timeout=settings.agent_validation_timeout,
            verbose=settings.agent_verbose
        )
        
        # Convert to response schema
        trace = [
            AgentStepInfo(
                name=step.name,
                status=step.status,
                duration_ms=step.duration_ms,
                details=step.details
            )
            for step in result.trace
        ] if settings.agent_verbose else []
        
        intent_info = None
        if result.intent:
            intent_info = IntentInfo(
                type=result.intent.intent_type.value,
                complexity=result.intent.complexity.value,
                summary=result.intent.summary,
                file_hints=result.intent.file_hints,
                component_hints=result.intent.component_hints
            )
        
        plan_info = None
        if result.plan:
            plan_info = PlanInfo(
                files_to_modify=result.plan.files_to_modify,
                files_to_create=result.plan.files_to_create,
                reasoning=result.plan.reasoning
            )
        
        diffs = [
            FileDiff(filename=d["file_path"], diff=d["diff"])
            for d in result.diffs
        ] if isinstance(result.diffs, list) and result.diffs else []
        
        # Minimal response when verbose is disabled
        if not settings.agent_verbose:
            return AgentResponse(
                success=result.success,
                diffs=diffs
            )
        
        return AgentResponse(
            success=result.success,
            diffs=diffs,
            files_modified=result.files_modified,
            message=result.message,
            total_tokens=result.total_tokens,
            total_duration_ms=result.total_duration_ms,
            trace=trace,
            intent=intent_info,
            plan=plan_info
        )
        
    except Exception as e:
        logger.error(f"[Agent] Error: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Agent execution failed: {str(e)}"
        )
