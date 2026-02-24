"""
ReAct Agent API routes - Reasoning + Acting code generation.

This provides an alternative to the multi-agent pipeline, using a
single agent that reasons step-by-step with tool use.
"""
import logging
from fastapi import APIRouter, HTTPException
from pathlib import Path
from pydantic import BaseModel, Field

from app.services.react_agent import run_react_agent
from app.schemas import FileDiff
from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()


class ReactRequest(BaseModel):
    """Request for ReAct agent code generation."""
    instruction: str = Field(
        ...,
        description="Natural language instruction describing the desired code changes",
        min_length=1,
        max_length=4000
    )
    project: str = Field(
        ...,
        description="Project name (e.g., '1-todo-app')"
    )


class ReactStepInfo(BaseModel):
    """Information about a single ReAct step."""
    iteration: int
    thought: str
    action: str | None = None
    action_input: dict = Field(default_factory=dict)
    observation: str | None = None
    duration_ms: int = 0
    tokens_used: int = 0


class ReactResponse(BaseModel):
    """Response from ReAct agent execution."""
    success: bool = Field(..., description="Whether changes were applied successfully")
    diffs: list[FileDiff] = Field(default_factory=list, description="List of file diffs")
    files_modified: list[str] = Field(default_factory=list)
    message: str = Field(default="")
    
    # Execution stats
    total_tokens: int = Field(default=0, description="Total LLM tokens used")
    total_duration_ms: int = Field(default=0, description="Total execution time in ms")
    
    # Execution trace
    steps: list[ReactStepInfo] = Field(default_factory=list)


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


@router.post("/generate", response_model=ReactResponse)
async def react_generate(request: ReactRequest) -> ReactResponse:
    """
    ReAct agent for code generation using reasoning + acting loop.
    
    This approach:
    1. Explores the project structure to understand patterns
    2. Reasons about each step before taking action
    3. Makes surgical edits using search/replace
    4. Validates changes before applying
    5. Self-corrects on errors
    
    Advantages over multi-agent:
    - More flexible exploration
    - Adaptive to project structure  
    - Better at complex multi-file changes
    - Self-correcting behavior
    
    Disadvantages:
    - More API calls (potentially more expensive)
    - Can get stuck in loops
    - Less predictable execution path
    """
    logger.info(f"[ReAct] Request: project={request.project}, instruction={request.instruction[:50]}...")
    
    project_path = get_project_path(request.project)
    
    try:
        result = await run_react_agent(
            instruction=request.instruction,
            project=request.project,
            project_path=project_path,
            max_iterations=settings.react_max_iterations,
            verbose=settings.agent_verbose
        )
        
        # Convert to response schema
        steps = [
            ReactStepInfo(
                iteration=step.iteration,
                thought=step.thought,
                action=step.action,
                action_input=step.action_input,
                observation=step.observation[:500] if step.observation else None,  # Truncate long observations
                duration_ms=step.duration_ms,
                tokens_used=step.tokens_used
            )
            for step in result.steps
        ] if settings.agent_verbose else []
        
        diffs = [
            FileDiff(filename=d["file_path"], diff=d["diff"])
            for d in result.diffs
        ] if result.diffs else []
        
        return ReactResponse(
            success=result.success,
            diffs=diffs,
            files_modified=result.files_modified,
            message=result.message,
            total_tokens=result.total_tokens,
            total_duration_ms=result.total_duration_ms,
            steps=steps
        )
        
    except Exception as e:
        logger.error(f"[ReAct] Error: {e}")
        import traceback
        logger.debug(f"[ReAct] Traceback: {traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail=f"ReAct agent execution failed: {str(e)}"
        )
