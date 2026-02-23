"""
LLM service for code generation with multiple output format strategies.
"""
import json
import logging
import re
from openai import AsyncOpenAI
from app.config import get_settings
from app.schemas import OutputFormat
from app.prompts.simple import (
    SYSTEM_PROMPT_FULL_CONTENT,
    SYSTEM_PROMPT_SEARCH_REPLACE,
    SYSTEM_PROMPT_DIFF,
)

logger = logging.getLogger(__name__)
settings = get_settings()


def _extract_json(content: str) -> str:
    """Extract JSON from LLM response, handling markdown code blocks."""
    content = content.strip()
    if content.startswith("```"):
        first_newline = content.find("\n")
        if first_newline != -1:
            content = content[first_newline + 1:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()
    return content


# ============================================================================
# MAIN FUNCTION
# ============================================================================

async def generate_code_changes(
    instruction: str,
    files: dict[str, str],
    output_format: OutputFormat = OutputFormat.FULL_CONTENT
) -> list[dict[str, str]]:
    """
    Generate code modifications using specified output format.
    
    Args:
        instruction: Natural language instruction
        files: Dict of {file_path: file_content}
        output_format: Which LLM output format to use
        
    Returns:
        List of {file: path, content: modified_content}
        (normalized format regardless of output_format)
    """
    client = AsyncOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url
    )
    
    # Build context with all files
    files_context = ""
    for path, content in files.items():
        files_context += f"\n--- {path} ---\n{content}\n"
    
    user_prompt = f"""Project files:
{files_context}

Instruction: {instruction}"""

    # Select prompt and parser based on format
    if output_format == OutputFormat.FULL_CONTENT:
        system_prompt = SYSTEM_PROMPT_FULL_CONTENT
        parser = _parse_full_content
    elif output_format == OutputFormat.SEARCH_REPLACE:
        system_prompt = SYSTEM_PROMPT_SEARCH_REPLACE
        parser = lambda resp, f: _parse_search_replace(resp, f)
    else:  # DIFF
        system_prompt = SYSTEM_PROMPT_DIFF
        parser = lambda resp, f: _parse_diff_output(resp, f)

    logger.info(f"Generating changes using format={output_format.value}, instruction={instruction[:80]}...")
    logger.info(f"Files in context: {list(files.keys())}")
    
    response = await client.chat.completions.create(
        model=settings.model_simple,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        response_format={"type": "json_object"},  # Force valid JSON
        temperature=settings.llm_temperature,
        max_tokens=settings.max_tokens
    )
    
    content = response.choices[0].message.content
    usage = response.usage
    
    logger.info(f"Tokens: prompt={usage.prompt_tokens}, completion={usage.completion_tokens}, total={usage.total_tokens}")
    logger.debug(f"LLM raw response:\n{content}")
    
    # Parse based on format
    try:
        json_content = _extract_json(content)
        result = json.loads(json_content)
        logger.debug(f"Parsed JSON result: {json.dumps(result, indent=2)[:2000]}")
        modifications = parser(result, files)
        logger.info(f"Files to modify: {[m['file'] for m in modifications]}")
        return modifications
        
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse LLM response: {e}")
        logger.debug(f"Raw: {content[:1000]}")
        raise ValueError(f"LLM returned invalid JSON: {e}")


# ============================================================================
# PARSERS FOR EACH FORMAT
# ============================================================================

def _parse_full_content(result: dict, files: dict) -> list[dict]:
    """Parse full content format - already in correct format."""
    return result.get("modifications", [])


def _parse_search_replace(result: dict, files: dict) -> list[dict]:
    """
    Parse search/replace blocks and apply to get full content.
    Returns normalized {file, content} list.
    """
    changes = result.get("changes", [])
    
    # Group changes by file
    file_changes: dict[str, list] = {}
    for change in changes:
        file_path = change.get("file")
        if file_path not in file_changes:
            file_changes[file_path] = []
        file_changes[file_path].append(change)
    
    # Apply changes to each file
    modifications = []
    for file_path, changes_list in file_changes.items():
        original = files.get(file_path, "")
        modified = original
        
        for change in changes_list:
            search = change.get("search", "")
            replace = change.get("replace", "")
            
            if search in modified:
                modified = modified.replace(search, replace, 1)
                logger.debug(f"Applied search/replace in {file_path}")
            else:
                logger.warning(f"Search text not found in {file_path}: {search[:50]}...")
        
        if modified != original:
            modifications.append({"file": file_path, "content": modified})
    
    return modifications


def _parse_diff_output(result: dict, files: dict) -> list[dict]:
    """
    Parse LLM-generated diffs. Returns {file, content, diff} list.
    Note: LLM diffs are often malformed, so we store the diff directly.
    """
    patches = result.get("patches", [])
    
    modifications = []
    for patch in patches:
        file_path = patch.get("file")
        diff_text = patch.get("diff", "")
        
        # For diff format, we return the diff directly
        # The route will need to handle this differently
        modifications.append({
            "file": file_path,
            "diff": diff_text,
            "content": None  # Signal that we have diff, not content
        })
    
    return modifications
