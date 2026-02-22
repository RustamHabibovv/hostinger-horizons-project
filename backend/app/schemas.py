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
    output_format: OutputFormat = Field(
        default=OutputFormat.FULL_CONTENT,
        description="LLM output format: full_content, search_replace, or diff"
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
