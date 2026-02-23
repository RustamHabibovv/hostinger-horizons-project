from enum import Enum
from pydantic import BaseModel, Field


class OutputFormat(str, Enum):
    """
    LLM intermediate output format.
    All approaches produce a unified diff for git apply - this controls HOW we get there.
    """
    FULL_CONTENT = "full_content"  # LLM → full file content → difflib generates diff
    SEARCH_REPLACE = "search_replace"  # LLM → search/replace blocks → apply → difflib generates diff 
    DIFF = "diff"  # LLM → unified diff directly (fewer output tokens)


class CodeChangeRequest(BaseModel):
    """Request to generate and apply code changes."""
    instruction: str = Field(
        ...,
        description="Natural language instruction describing the desired code changes",
        min_length=1,
        max_length=2000
    )
    project: str = Field(
        ...,
        description="Project name (e.g., '1-todo-app', '2-candy-pop-landing', '3-qr-generator')"
    )


class FileDiff(BaseModel):
    """A single file diff."""
    filename: str = Field(..., description="Relative path to the file")
    diff: str = Field(..., description="Unified diff format")


class CodeChangeResponse(BaseModel):
    """Response containing the generated and applied code changes."""
    success: bool = Field(..., description="Whether changes were applied successfully")
    diffs: list[FileDiff] = Field(default_factory=list, description="List of file diffs")
    message: str = Field(default="", description="Status message")
    files_modified: list[str] = Field(default_factory=list, description="Files that were modified")


# =============================================================================
# Phase 2: Agent Schemas
# =============================================================================

class AgentRequest(BaseModel):
    """Request for multi-step agent code generation."""
    instruction: str = Field(
        ...,
        description="Natural language instruction describing the desired code changes",
        min_length=1,
        max_length=4000
    )
    project: str = Field(
        ...,
        description="Project name"
    )


class AgentStepInfo(BaseModel):
    """Information about a single agent step."""
    name: str
    status: str
    duration_ms: int
    details: dict = Field(default_factory=dict)


class IntentInfo(BaseModel):
    """Parsed intent information."""
    type: str
    complexity: str
    summary: str
    file_hints: list[str] = Field(default_factory=list)
    component_hints: list[str] = Field(default_factory=list)


class PlanInfo(BaseModel):
    """Execution plan information."""
    files_to_modify: list[str] = Field(default_factory=list)
    files_to_create: list[str] = Field(default_factory=list)
    reasoning: str = ""


class AgentResponse(BaseModel):
    """Response from agent execution."""
    success: bool = Field(..., description="Whether changes were applied successfully")
    diffs: list[FileDiff] = Field(default_factory=list, description="List of file diffs")
    files_modified: list[str] = Field(default_factory=list)
    message: str = Field(default="")
    
    # Execution stats
    total_tokens: int = Field(default=0, description="Total LLM tokens used")
    total_duration_ms: int = Field(default=0, description="Total execution time in ms")
    
    # Execution trace (verbose)
    trace: list[AgentStepInfo] = Field(default_factory=list)
    intent: IntentInfo | None = None
    plan: PlanInfo | None = None
