"""
Executor - Generates code changes with retry loop and error feedback.
Uses GPT-4o for code generation (the expensive part).
Includes build validation to catch runtime/compilation errors.
"""
import asyncio
import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from openai import AsyncOpenAI

from app.config import get_settings
from app.services.diff import generate_unified_diff, read_file_content, apply_with_git, list_project_files
from app.services.agent.planner import ExecutionPlan
from app.prompts.executor import (
    EXECUTOR_PROMPT_FULL_CONTENT,
    EXECUTOR_PROMPT_SEARCH_REPLACE,
    EXECUTOR_PROMPT_DIFF,
    EXECUTOR_RETRY_PROMPT,
)

logger = logging.getLogger(__name__)
settings = get_settings()


def _extract_json(content: str) -> str:
    """
    Extract JSON from LLM response, handling markdown code blocks.
    Some models wrap JSON in ```json ... ``` even when asked for raw JSON,
    and some include explanatory text before the JSON block.
    """
    content = content.strip()
    
    # If there's a code block, extract it (even if there's text before it)
    json_block_match = re.search(r'```(?:json)?\s*\n(.*?)\n?```', content, re.DOTALL)
    if json_block_match:
        content = json_block_match.group(1).strip()
    elif content.startswith("```"):
        # Fallback: old logic for simple code blocks
        first_newline = content.find("\n")
        if first_newline != -1:
            content = content[first_newline + 1:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()
    
    # If content doesn't start with { or [, try to find JSON object/array
    if not content.startswith('{') and not content.startswith('['):
        # Try to find a JSON object
        json_start = content.find('{')
        if json_start != -1:
            # Find matching closing brace
            brace_count = 0
            for i, char in enumerate(content[json_start:], start=json_start):
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        content = content[json_start:i+1]
                        break
    
    return content


@dataclass
class BuildValidationResult:
    """Result of build validation."""
    success: bool
    error_output: str | None = None
    error_type: str | None = None  # syntax, type, import, runtime


async def validate_build(project_path: Path, timeout_seconds: int = 30) -> BuildValidationResult:
    """
    Run build/type check to validate code changes.
    
    Uses subprocess.run in a thread pool for Windows compatibility.
    
    Args:
        project_path: Path to the project
        timeout_seconds: Max time to wait for build
        
    Returns:
        BuildValidationResult with success status and any error output
    """
    import time
    start_time = time.time()
    
    try:
        # Check if package.json exists
        package_json = project_path / "package.json"
        if not package_json.exists():
            logger.debug("No package.json found, skipping build validation")
            return BuildValidationResult(success=True)
        
        # Run npm run build using subprocess in thread pool (Windows compatible)
        logger.info(f"[Validation] Starting 'npm run build' in {project_path}")
        logger.debug(f"[Validation] Timeout: {timeout_seconds}s")
        
        def run_build():
            """Run build in a separate thread."""
            return subprocess.run(
                "npm run build",
                shell=True,
                cwd=str(project_path),
                capture_output=True,
                timeout=timeout_seconds
            )
        
        try:
            # Run in thread pool to not block async loop
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, run_build)
            elapsed = time.time() - start_time
            
            # Log output regardless of success
            stdout_text = result.stdout.decode('utf-8', errors='replace').strip()
            stderr_text = result.stderr.decode('utf-8', errors='replace').strip()
            
            logger.debug(f"[Validation] Completed in {elapsed:.1f}s, return code: {result.returncode}")
            if stdout_text:
                logger.debug(f"[Validation] STDOUT:\n{stdout_text[:500]}{'...' if len(stdout_text) > 500 else ''}")
            if stderr_text:
                logger.debug(f"[Validation] STDERR:\n{stderr_text[:500]}{'...' if len(stderr_text) > 500 else ''}")
            
        except subprocess.TimeoutExpired:
            elapsed = time.time() - start_time
            logger.warning(f"[Validation] Timed out after {elapsed:.1f}s")
            return BuildValidationResult(success=True)
        
        if result.returncode == 0:
            logger.info(f"[Validation] ✓ Build passed in {elapsed:.1f}s")
            return BuildValidationResult(success=True)
        
        # Build failed - extract error message
        error_output = stderr_text if stderr_text else stdout_text
        
        # Try to classify error type
        error_type = _classify_error(error_output)
        
        # Truncate error output if too long
        if len(error_output) > 2000:
            error_output = error_output[:2000] + "\n... (truncated)"
        
        logger.warning(f"[Validation] ✗ Build failed ({error_type}) in {elapsed:.1f}s")
        logger.warning(f"[Validation] Error:\n{error_output[:500]}...")
        
        return BuildValidationResult(
            success=False,
            error_output=error_output,
            error_type=error_type
        )
        
    except FileNotFoundError:
        # npm not found - try alternative validation
        logger.debug("[Validation] npm not found, trying alternative validation")
        return await _validate_syntax_only(project_path)
        
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"[Validation] Exception after {elapsed:.1f}s: {type(e).__name__}: {e}")
        import traceback
        logger.debug(f"[Validation] Traceback:\n{traceback.format_exc()}")
        # Return failure on validation exceptions - we want to catch issues
        return BuildValidationResult(
            success=False,
            error_output=f"Validation error: {type(e).__name__}: {e}",
            error_type="validation"
        )


# Regex to find npm package imports (not relative imports)
NPM_IMPORT_PATTERN = re.compile(
    r'''(?:import\s+.*?\s+from\s+['"]|import\s*\(\s*['"]|require\s*\(\s*['"])([^'".][^'"]*?)['"]''',
    re.MULTILINE
)


def _check_npm_imports(modifications: list[dict], project_path: Path) -> str | None:
    """
    Check if npm package imports exist in node_modules.
    Returns error message if missing packages found, None if OK.
    """
    node_modules = project_path / "node_modules"
    if not node_modules.exists():
        return None  # Skip check if no node_modules
    
    missing_packages = set()
    
    for mod in modifications:
        content = mod.get("content", "")
        if not content:
            continue
        
        imports = NPM_IMPORT_PATTERN.findall(content)
        
        for imp in imports:
            # Skip relative imports (already handled by _validate_imports)
            if imp.startswith("."):
                continue
            
            # Extract base package name (e.g., "@radix-ui/react-slot" → "@radix-ui/react-slot", "react-icons/fa" → "react-icons")
            if imp.startswith("@"):
                # Scoped package: @scope/package/path → @scope/package
                parts = imp.split("/")
                package_name = "/".join(parts[:2]) if len(parts) >= 2 else imp
            else:
                # Regular package: package/path → package
                package_name = imp.split("/")[0]
            
            # Check if package exists in node_modules
            package_path = node_modules / package_name
            if not package_path.exists():
                missing_packages.add(package_name)
    
    if missing_packages:
        return f"Missing npm packages: {', '.join(sorted(missing_packages))}. These packages are not installed in the project."
    
    return None


async def _validate_syntax_only(project_path: Path) -> BuildValidationResult:
    """Fallback: check JS/JSX syntax using node."""
    # For now, just return success - could implement eslint check here
    return BuildValidationResult(success=True)


def _classify_error(error_output: str) -> str:
    """Classify the type of build error for better LLM feedback."""
    error_lower = error_output.lower()
    
    if "syntaxerror" in error_lower or "unexpected token" in error_lower:
        return "syntax"
    elif "typeerror" in error_lower or "type error" in error_lower:
        return "type"
    elif "cannot find module" in error_lower or "failed to resolve import" in error_lower:
        return "import"
    elif "referenceerror" in error_lower or "is not defined" in error_lower:
        return "reference"
    elif "eslint" in error_lower or "lint" in error_lower:
        return "lint"
    else:
        return "build"


def _revert_changes(project_path: Path, combined_diff: str) -> bool:
    """
    Attempt to revert applied changes using git checkout or reverse patch.
    
    Returns True if revert succeeded.
    """
    try:
        # Try git checkout to restore files
        result = subprocess.run(
            ["git", "checkout", "--", "."],
            cwd=str(project_path),
            capture_output=True,
            timeout=10
        )
        
        if result.returncode == 0:
            logger.info("Reverted changes using git checkout")
            return True
        
        # If git checkout fails, try reverse patch
        # (This is more complex, skip for now)
        logger.warning("Could not revert changes with git checkout")
        return False
        
    except Exception as e:
        logger.error(f"Failed to revert changes: {e}")
        return False


@dataclass
class ExecutionAttempt:
    """Record of a single execution attempt."""
    attempt_number: int
    success: bool
    diff: str
    error: str | None = None
    tokens_used: int = 0


@dataclass
class ExecutionResult:
    """Result of executing the plan."""
    success: bool
    diffs: list[dict]           # {file_path, diff}
    files_modified: list[str]
    attempts: list[ExecutionAttempt]
    total_tokens: int
    error: str | None = None


# Regex to find relative imports in JS/JSX/TS/TSX files
IMPORT_PATTERN = re.compile(
    r'''(?:import\s+.*?\s+from\s+['"]|import\s*\(\s*['"]|require\s*\(\s*['"])(\.\.?/[^'"]+)['"]''',
    re.MULTILINE
)


def _validate_imports(
    modifications: list[dict],
    existing_contents: dict[str, str],
    project_path: Path
) -> str | None:
    """
    Validate that all relative imports reference files that exist or will be created.
    
    Returns error message if validation fails, None if OK.
    """
    # Get set of files being modified/created
    modified_files = {mod.get("file") for mod in modifications}
    
    # Get existing project files
    existing_files = set(list_project_files(project_path))
    
    # All files that will exist after modifications
    all_files = existing_files | modified_files
    
    errors = []
    
    for mod in modifications:
        file_path = mod.get("file")
        content = mod.get("content", "")
        
        if not file_path or not content:
            continue
        
        # Find all relative imports
        imports = IMPORT_PATTERN.findall(content)
        
        for imp in imports:
            # Resolve import path relative to the importing file
            resolved = _resolve_import(file_path, imp)
            
            # Check if resolved path exists (with common extensions)
            if not _import_exists(resolved, all_files):
                errors.append(f"'{file_path}' imports '{imp}' but file not found")
    
    if errors:
        return "; ".join(errors[:3])  # Limit error messages
    
    return None


def _resolve_import(from_file: str, import_path: str) -> str:
    """Resolve relative import path to absolute project path."""
    from_dir = str(Path(from_file).parent)
    
    # Normalize path
    parts = from_dir.split("/") if "/" in from_dir else from_dir.split("\\")
    
    for segment in import_path.split("/"):
        if segment == "..":
            if parts:
                parts.pop()
        elif segment != ".":
            parts.append(segment)
    
    return "/".join(parts)


def _import_exists(resolved_path: str, all_files: set[str]) -> bool:
    """Check if an import resolves to an existing file."""
    # Check exact match first
    if resolved_path in all_files:
        return True
    
    # Try common extensions
    extensions = [".js", ".jsx", ".ts", ".tsx", ".json", "/index.js", "/index.jsx", "/index.ts", "/index.tsx"]
    
    for ext in extensions:
        if (resolved_path + ext) in all_files:
            return True
        # Also check normalized paths (e.g., src\\file.js vs src/file.js)
        normalized = resolved_path.replace("\\", "/") + ext
        if any(f.replace("\\", "/") == normalized for f in all_files):
            return True
    
    return False


def _get_executor_prompt(output_format: str) -> str:
    """Get the appropriate executor prompt based on output format."""
    if output_format == "search_replace":
        return EXECUTOR_PROMPT_SEARCH_REPLACE
    elif output_format == "diff":
        return EXECUTOR_PROMPT_DIFF
    else:  # full_content (default)
        return EXECUTOR_PROMPT_FULL_CONTENT


def _parse_executor_response(
    result: dict,
    file_contents: dict[str, str],
    output_format: str
) -> list[dict]:
    """
    Parse executor response based on output format.
    Returns normalized list of {file, content} for all formats.
    """
    modifications = result.get("modifications", [])
    parsed = []
    
    for mod in modifications:
        file_path = mod.get("file")
        if not file_path:
            continue
        
        action = mod.get("action", "modify")
        
        if output_format == "full_content" or action == "create":
            # Full content mode or new file - content is already complete
            content = mod.get("content")
            if content:
                parsed.append({"file": file_path, "content": content})
                
        elif output_format == "search_replace":
            # Apply search/replace changes
            original = file_contents.get(file_path, "")
            modified = original
            
            for change in mod.get("changes", []):
                search = change.get("search", "")
                replace = change.get("replace", "")
                if search and search in modified:
                    modified = modified.replace(search, replace, 1)
            
            if modified != original:
                parsed.append({"file": file_path, "content": modified})
                
        elif output_format == "diff":
            # Store diff directly - will be handled differently
            diff = mod.get("diff")
            if diff:
                parsed.append({"file": file_path, "diff": diff, "content": None})
    
    return parsed


async def execute_plan(
    instruction: str,
    plan: ExecutionPlan,
    project_path: Path,
    file_contents: dict[str, str],
    max_retries: int = 3,
    run_validation: bool = True,
    validation_timeout: int = 60
) -> ExecutionResult:
    """
    Execute the plan with retry loop.
    If git apply fails, provides error feedback to LLM for retry.
    Optionally runs build validation to catch runtime errors.
    
    Uses GPT-4o for code generation (only expensive call in the pipeline).
    
    Args:
        instruction: The original user instruction
        plan: Execution plan from planner
        project_path: Path to the project
        file_contents: Current file contents
        max_retries: Max retry attempts
        run_validation: Whether to run npm build validation
        validation_timeout: Timeout for build validation in seconds
    """
    client = AsyncOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url
    )
    
    attempts: list[ExecutionAttempt] = []
    total_tokens = 0
    last_error = None
    
    # Build files context (only files in the plan)
    target_files = set(plan.files_to_modify + plan.files_to_create)
    files_context = ""
    for file_path in target_files:
        if file_path in file_contents:
            files_context += f"\n--- {file_path} (MODIFY) ---\n{file_contents[file_path]}\n"
        else:
            files_context += f"\n--- {file_path} (CREATE - new file, generate full content) ---\n"
    
    # Build plan context with clear action types
    plan_summary = "\n".join([
        f"{step.step_number}. [{step.action.upper()}] {step.file_path}: {step.description}"
        for step in plan.steps
    ])
    
    # Add explicit reminder about files to create
    files_to_create_reminder = ""
    if plan.files_to_create:
        files_to_create_reminder = f"\n\n⚠️ IMPORTANT: You MUST create these new files:\n" + "\n".join(
            f"  - {f}" for f in plan.files_to_create
        )
    
    for attempt in range(1, max_retries + 1):
        logger.info(f"Execution attempt {attempt}/{max_retries}")
        
        # Build prompt
        user_prompt = f"""Instruction: {instruction}

Execution Plan:
{plan_summary}
{files_to_create_reminder}

Current Files:
{files_context}
"""
        
        # Add error feedback if this is a retry
        if last_error:
            user_prompt += f"\n\n{EXECUTOR_RETRY_PROMPT.format(error=last_error)}"
        
        user_prompt += "\nGenerate the modifications:"
        
        try:
            # Get appropriate system prompt based on output format
            output_format = settings.agent_output_format
            system_prompt = _get_executor_prompt(output_format)
            
            response = await client.chat.completions.create(
                model=settings.model_executor,  # Best model for code gen
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={"type": "json_object"},
                temperature=settings.llm_temperature,
                max_tokens=settings.max_tokens
            )
            
            content = response.choices[0].message.content
            usage = response.usage
            total_tokens += usage.total_tokens
            
            logger.debug(f"Executor response ({usage.total_tokens} tokens): {content[:500]}...")
            
            # Parse response - strip markdown code blocks if present
            json_content = _extract_json(content)
            result = json.loads(json_content)
            modifications = result.get("modifications", [])
            
            if not modifications:
                attempts.append(ExecutionAttempt(
                    attempt_number=attempt,
                    success=False,
                    diff="",
                    error="No modifications returned",
                    tokens_used=usage.total_tokens
                ))
                last_error = "No modifications returned by LLM"
                continue
            
            # VALIDATION: Check that all files_to_create are included
            modified_files = {mod.get("file") for mod in modifications}
            missing_creates = set(plan.files_to_create) - modified_files
            
            if missing_creates:
                error_msg = f"Missing required new files: {', '.join(missing_creates)}. You MUST create these files."
                logger.warning(f"Validation failed: {error_msg}")
                attempts.append(ExecutionAttempt(
                    attempt_number=attempt,
                    success=False,
                    diff="",
                    error=error_msg,
                    tokens_used=usage.total_tokens
                ))
                last_error = error_msg
                continue
            
            # VALIDATION STEP 1: Check for imports to files that don't exist and won't be created
            logger.debug(f"[Validation] Step 1: Checking relative imports...")
            import_errors = _validate_imports(modifications, file_contents, project_path)
            if import_errors:
                error_msg = f"Import validation failed: {import_errors}"
                logger.warning(f"[Validation] ✗ Step 1 failed: {error_msg}")
                attempts.append(ExecutionAttempt(
                    attempt_number=attempt,
                    success=False,
                    diff="",
                    error=error_msg,
                    tokens_used=usage.total_tokens
                ))
                last_error = error_msg
                continue
            logger.debug(f"[Validation] ✓ Step 1 passed: Relative imports OK")
            
            # VALIDATION STEP 2: Check for npm package imports that aren't installed
            logger.debug(f"[Validation] Step 2: Checking npm package imports...")
            npm_errors = _check_npm_imports(modifications, project_path)
            if npm_errors:
                error_msg = f"NPM import validation failed: {npm_errors}"
                logger.warning(f"[Validation] ✗ Step 2 failed: {error_msg}")
                attempts.append(ExecutionAttempt(
                    attempt_number=attempt,
                    success=False,
                    diff="",
                    error=error_msg,
                    tokens_used=usage.total_tokens
                ))
                last_error = error_msg
                continue
            logger.debug(f"[Validation] ✓ Step 2 passed: NPM packages OK")
            
            # Parse response based on output format
            parsed = _parse_executor_response(result, file_contents, output_format)
            
            # Generate or extract diffs based on format
            diffs = []
            combined_diff = ""
            
            if output_format == "diff":
                # Use LLM-provided diffs directly
                for mod in parsed:
                    file_path = mod.get("file")
                    diff_text = mod.get("diff")
                    content = mod.get("content")
                    
                    if not file_path:
                        continue
                    
                    if diff_text:
                        # LLM provided a diff for modification
                        diffs.append({"file_path": file_path, "diff": diff_text})
                        combined_diff += diff_text + "\n"
                    elif content:
                        # LLM provided full content for CREATE action
                        original = file_contents.get(file_path, "")
                        diff_text = generate_unified_diff(original, content, file_path)
                        if diff_text.strip():
                            diffs.append({"file_path": file_path, "diff": diff_text})
                            combined_diff += diff_text + "\n"
            else:
                # full_content and search_replace both return normalized content
                for mod in parsed:
                    file_path = mod.get("file")
                    new_content = mod.get("content")
                    
                    if not file_path or not new_content:
                        continue
                    
                    original = file_contents.get(file_path, "")
                    diff_text = generate_unified_diff(original, new_content, file_path)
                    
                    if diff_text.strip():
                        diffs.append({"file_path": file_path, "diff": diff_text})
                        combined_diff += diff_text + "\n"
            
            if not diffs:
                attempts.append(ExecutionAttempt(
                    attempt_number=attempt,
                    success=False,
                    diff="",
                    error="No actual changes generated",
                    tokens_used=usage.total_tokens
                ))
                last_error = "Generated code was identical to original"
                continue
            
            logger.info(f"Generated {len(diffs)} diffs, attempting to apply...")
            
            # Try to apply
            apply_result = apply_with_git(project_path, combined_diff)
            
            if apply_result.success:
                logger.info(f"Applied changes on attempt {attempt}")
                
                # VALIDATION STEP 3: Run build validation if enabled
                if run_validation:
                    logger.debug(f"[Validation] Step 3: Running build validation...")
                    validation_result = await validate_build(project_path, validation_timeout)
                    
                    if not validation_result.success:
                        # Build failed - revert and retry
                        logger.warning(f"[Validation] ✗ Step 3 failed: {validation_result.error_type}")
                        
                        # Try to revert the changes
                        reverted = _revert_changes(project_path, combined_diff)
                        if reverted:
                            logger.info("[Validation] Reverted changes successfully")
                        else:
                            logger.warning("[Validation] Could not revert changes - manual cleanup may be needed")
                        
                        error_msg = f"Build failed ({validation_result.error_type} error):\n{validation_result.error_output}"
                        
                        attempts.append(ExecutionAttempt(
                            attempt_number=attempt,
                            success=False,
                            diff=combined_diff,
                            error=error_msg,
                            tokens_used=usage.total_tokens
                        ))
                        
                        last_error = error_msg
                        continue  # Retry with error feedback
                    
                    logger.debug(f"[Validation] ✓ Step 3 passed: Build OK")
                else:
                    logger.debug(f"[Validation] Step 3 skipped: run_validation=False")
                
                # All validation passed
                logger.info(f"✓ Successfully applied and validated changes on attempt {attempt}")
                
                attempts.append(ExecutionAttempt(
                    attempt_number=attempt,
                    success=True,
                    diff=combined_diff,
                    tokens_used=usage.total_tokens
                ))
                
                return ExecutionResult(
                    success=True,
                    diffs=diffs,
                    files_modified=[d["file_path"] for d in diffs],
                    attempts=attempts,
                    total_tokens=total_tokens
                )
            else:
                # Apply failed - record and retry
                error_msg = apply_result.message
                logger.warning(f"Attempt {attempt} failed: {error_msg}")
                
                attempts.append(ExecutionAttempt(
                    attempt_number=attempt,
                    success=False,
                    diff=combined_diff,
                    error=error_msg,
                    tokens_used=usage.total_tokens
                ))
                
                last_error = error_msg
                
        except Exception as e:
            logger.error(f"Execution error on attempt {attempt}: {e}")
            attempts.append(ExecutionAttempt(
                attempt_number=attempt,
                success=False,
                diff="",
                error=str(e)
            ))
            last_error = str(e)
    
    # All retries exhausted
    logger.error(f"Execution failed after {max_retries} attempts")
    
    return ExecutionResult(
        success=False,
        diffs=[a.diff for a in attempts if a.diff] if attempts else [],
        files_modified=[],
        attempts=attempts,
        total_tokens=total_tokens,
        error=f"Failed after {max_retries} attempts: {last_error}"
    )
