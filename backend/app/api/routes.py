"""
Simplified API routes - Single endpoint for code changes.
"""
import logging
from fastapi import APIRouter, HTTPException
from pathlib import Path

from app.schemas import CodeChangeRequest, CodeChangeResponse, FileDiff, OutputFormat
from app.services.llm import generate_code_changes
from app.services.diff import (
    generate_unified_diff, 
    read_all_project_files,
    apply_with_git
)
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


@router.post("/generate", response_model=CodeChangeResponse)
async def generate_and_apply(request: CodeChangeRequest) -> CodeChangeResponse:
    """
    Generate and apply code changes based on natural language instruction.
    
    Final output is always a unified diff applied via 'git apply'.
    The output_format (configured in settings) controls HOW we get the diff:
    - full_content: LLM → full file → difflib generates diff (most reliable, more tokens)
    - search_replace: LLM → search/replace blocks → apply → difflib generates diff (balanced)
    - diff: LLM → outputs diff directly (fewest tokens, less reliable)
    """
    # Get output format from config
    output_format = OutputFormat(settings.output_format)
    
    logger.info(f"Generate: project={request.project}, format={output_format.value}")
    
    project_path = get_project_path(request.project)
    
    # Step 1: Read all project files
    all_files = read_all_project_files(project_path)
    
    if not all_files:
        raise HTTPException(
            status_code=400,
            detail=f"No source files found in project '{request.project}'"
        )
    
    logger.info(f"Found {len(all_files)} files in project")
    
    # Step 2: Generate code changes via LLM
    try:
        modifications = await generate_code_changes(
            instruction=request.instruction,
            files=all_files,
            output_format=output_format
        )
    except Exception as e:
        logger.error(f"LLM generation failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"LLM generation failed: {str(e)}"
        )
    
    if not modifications:
        # Minimal response when verbose is disabled
        if not settings.agent_verbose:
            return CodeChangeResponse(
                success=True,
                diffs=[]
            )
        return CodeChangeResponse(
            success=True,
            diffs=[],
            message="No changes needed",
            files_modified=[]
        )
    
    logger.info(f"LLM returned {len(modifications)} file modifications")
    
    # Step 3: Generate diffs for each modification
    diffs = []
    combined_diff = ""
    files_modified = []
    
    for mod in modifications:
        file_path = mod["file"]
        
        # Handle diff format (LLM provides diff directly)
        if output_format == OutputFormat.DIFF:
            diff_text = mod.get("diff", "")
            if diff_text.strip():
                diffs.append(FileDiff(filename=file_path, diff=diff_text))
                combined_diff += diff_text + "\n"
                files_modified.append(file_path)
        else:
            # full_content or search_replace: generate diff from content
            new_content = mod.get("content")
            if not new_content:
                continue
                
            original_content = all_files.get(file_path, "")
            diff_text = generate_unified_diff(original_content, new_content, file_path)
            
            if diff_text.strip():
                diffs.append(FileDiff(filename=file_path, diff=diff_text))
                combined_diff += diff_text + "\n"
                files_modified.append(file_path)
                logger.info(f"Generated diff for {file_path}")
    
    if not diffs:
        # Minimal response when verbose is disabled
        if not settings.agent_verbose:
            return CodeChangeResponse(
                success=True,
                diffs=[]
            )
        return CodeChangeResponse(
            success=True,
            diffs=[],
            message="No actual changes detected",
            files_modified=[]
        )
    
    # Step 4: Apply changes using git apply
    apply_result = apply_with_git(project_path, combined_diff)
    
    if not apply_result.success:
        logger.error(f"Failed to apply changes: {apply_result.message}")
        # Minimal response when verbose is disabled
        if not settings.agent_verbose:
            return CodeChangeResponse(
                success=False,
                diffs=diffs
            )
        return CodeChangeResponse(
            success=False,
            diffs=diffs,
            message=f"Generated diffs but failed to apply: {apply_result.message}",
            files_modified=[]
        )
    
    logger.info(f"Successfully applied changes to {len(files_modified)} files")
    
    # Minimal response when verbose is disabled
    if not settings.agent_verbose:
        return CodeChangeResponse(
            success=True,
            diffs=diffs
        )
    
    return CodeChangeResponse(
        success=True,
        diffs=diffs,
        message=apply_result.message,
        files_modified=files_modified
    )
