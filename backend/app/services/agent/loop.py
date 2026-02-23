"""
Agent Loop - Main orchestrator for multi-step code generation.
Coordinates: Intent → Retrieval → Planning → Execution
"""
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.services.agent.intent import parse_intent, ParsedIntent
from app.services.agent.planner import create_plan, ExecutionPlan
from app.services.agent.executor import execute_plan, ExecutionResult
from app.services.retrieval import retrieve_relevant_files
from app.services.diff import read_file_content

logger = logging.getLogger(__name__)


@dataclass
class AgentStep:
    """A single step in the agent execution trace."""
    name: str
    status: str          # running, completed, failed
    duration_ms: int
    details: dict = field(default_factory=dict)


@dataclass
class AgentResult:
    """Complete result of agent execution."""
    success: bool
    diffs: list[dict]
    files_modified: list[str]
    message: str
    trace: list[AgentStep]         # Execution trace for debugging
    total_tokens: int
    total_duration_ms: int
    
    # Intermediate results for debugging
    intent: ParsedIntent | None = None
    plan: ExecutionPlan | None = None


async def run_agent(
    instruction: str,
    project: str,
    project_path: Path,
    max_retries: int = 3,
    retrieval_top_k: int = 5,
    validate_build: bool = True,
    validation_timeout: int = 60,
    verbose: bool = True
) -> AgentResult:
    """
    Run the full agent pipeline:
    1. Parse intent (GPT-4o-mini)
    2. Retrieve relevant files (embeddings + multi-signal)
    3. Create execution plan (GPT-4o-mini)
    4. Execute with retry loop (GPT-4o)
    5. Validate build (npm run build)
    
    Args:
        instruction: Natural language instruction
        project: Project name
        project_path: Path to project
        max_retries: Max execution retries
        retrieval_top_k: Number of files to retrieve
        validate_build: Run npm build to catch errors
        validation_timeout: Build timeout in seconds
        verbose: Include detailed trace
        
    Returns:
        AgentResult with diffs, trace, and stats
    """
    trace: list[AgentStep] = []
    total_tokens = 0
    start_time = time.time()
    
    def log_step(name: str, status: str, duration_ms: int, details: dict = None):
        step = AgentStep(
            name=name,
            status=status,
            duration_ms=duration_ms,
            details=details or {}
        )
        trace.append(step)
        
        status_icon = "✓" if status == "completed" else "✗" if status == "failed" else "→"
        logger.info(f"[Agent] {status_icon} {name} ({duration_ms}ms)")
        if details and verbose:
            for k, v in details.items():
                logger.debug(f"  {k}: {v}")
    
    # =========================================================================
    # STEP 1: Parse Intent
    # =========================================================================
    step_start = time.time()
    try:
        intent = await parse_intent(instruction)
        log_step(
            "parse_intent",
            "completed",
            int((time.time() - step_start) * 1000),
            {
                "type": intent.intent_type.value,
                "complexity": intent.complexity.value,
                "hints": intent.file_hints + intent.component_hints
            }
        )
    except Exception as e:
        log_step("parse_intent", "failed", int((time.time() - step_start) * 1000), {"error": str(e)})
        return AgentResult(
            success=False,
            diffs=[],
            files_modified=[],
            message=f"Intent parsing failed: {e}",
            trace=trace,
            total_tokens=0,
            total_duration_ms=int((time.time() - start_time) * 1000)
        )
    
    # =========================================================================
    # STEP 2: Retrieve Relevant Files
    # =========================================================================
    step_start = time.time()
    try:
        hints = intent.file_hints + intent.component_hints
        retrieved = retrieve_relevant_files(
            project=project,
            project_path=project_path,
            query=instruction,
            hints=hints if hints else None,
            top_k=retrieval_top_k
        )
        
        log_step(
            "retrieve_files",
            "completed",
            int((time.time() - step_start) * 1000),
            {
                "files_found": len(retrieved),
                "top_files": [r["file_path"] for r in retrieved[:3]]
            }
        )
        
        if not retrieved:
            return AgentResult(
                success=False,
                diffs=[],
                files_modified=[],
                message="No relevant files found",
                trace=trace,
                total_tokens=0,
                total_duration_ms=int((time.time() - start_time) * 1000),
                intent=intent
            )
            
    except Exception as e:
        log_step("retrieve_files", "failed", int((time.time() - step_start) * 1000), {"error": str(e)})
        return AgentResult(
            success=False,
            diffs=[],
            files_modified=[],
            message=f"File retrieval failed: {e}",
            trace=trace,
            total_tokens=0,
            total_duration_ms=int((time.time() - start_time) * 1000),
            intent=intent
        )
    
    # =========================================================================
    # STEP 3: Create Execution Plan
    # =========================================================================
    step_start = time.time()
    try:
        plan = await create_plan(instruction, intent, retrieved)
        log_step(
            "create_plan",
            "completed",
            int((time.time() - step_start) * 1000),
            {
                "steps": len(plan.steps),
                "files_to_modify": plan.files_to_modify,
                "reasoning": plan.reasoning[:100] if plan.reasoning else ""
            }
        )
    except Exception as e:
        log_step("create_plan", "failed", int((time.time() - step_start) * 1000), {"error": str(e)})
        return AgentResult(
            success=False,
            diffs=[],
            files_modified=[],
            message=f"Planning failed: {e}",
            trace=trace,
            total_tokens=0,
            total_duration_ms=int((time.time() - start_time) * 1000),
            intent=intent
        )
    
    # =========================================================================
    # STEP 4: Read File Contents for Execution
    # =========================================================================
    step_start = time.time()
    file_contents: dict[str, str] = {}
    
    # Get all files that might be needed
    files_needed = set(plan.files_to_modify)
    for r in retrieved:
        files_needed.add(r["file_path"])
    
    for file_path in files_needed:
        try:
            file_contents[file_path] = read_file_content(project_path, file_path)
        except FileNotFoundError:
            # File might need to be created
            pass
    
    log_step(
        "read_files",
        "completed",
        int((time.time() - step_start) * 1000),
        {"files_read": len(file_contents)}
    )
    
    # =========================================================================
    # STEP 5: Execute Plan with Retry and Validation
    # =========================================================================
    step_start = time.time()
    try:
        exec_result = await execute_plan(
            instruction=instruction,
            plan=plan,
            project_path=project_path,
            file_contents=file_contents,
            max_retries=max_retries,
            run_validation=validate_build,
            validation_timeout=validation_timeout
        )
        
        total_tokens = exec_result.total_tokens
        
        log_step(
            "execute",
            "completed" if exec_result.success else "failed",
            int((time.time() - step_start) * 1000),
            {
                "attempts": len(exec_result.attempts),
                "success": exec_result.success,
                "files_modified": exec_result.files_modified,
                "tokens": exec_result.total_tokens
            }
        )
        
    except Exception as e:
        log_step("execute", "failed", int((time.time() - step_start) * 1000), {"error": str(e)})
        return AgentResult(
            success=False,
            diffs=[],
            files_modified=[],
            message=f"Execution failed: {e}",
            trace=trace,
            total_tokens=total_tokens,
            total_duration_ms=int((time.time() - start_time) * 1000),
            intent=intent,
            plan=plan
        )
    
    # =========================================================================
    # Build Final Result
    # =========================================================================
    total_duration = int((time.time() - start_time) * 1000)
    
    if exec_result.success:
        message = f"Applied changes to {len(exec_result.files_modified)} files"
    else:
        message = exec_result.error or "Execution failed"
    
    logger.info(f"[Agent] {'✓' if exec_result.success else '✗'} Complete in {total_duration}ms, {total_tokens} tokens")
    
    return AgentResult(
        success=exec_result.success,
        diffs=exec_result.diffs,
        files_modified=exec_result.files_modified,
        message=message,
        trace=trace,
        total_tokens=total_tokens,
        total_duration_ms=total_duration,
        intent=intent,
        plan=plan
    )
