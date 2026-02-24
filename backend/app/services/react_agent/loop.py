"""
ReAct Loop Engine - Implements the Thought/Action/Observation reasoning loop.

Uses OpenAI function calling for reliable tool execution.
"""
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from app.config import get_settings
from app.prompts.react_agent import (
    REACT_SYSTEM_PROMPT,
    REACT_INITIAL_USER_PROMPT,
    REACT_OBSERVATION_PROMPT,
    REACT_ERROR_PROMPT,
    REACT_MAX_ITERATIONS_PROMPT,
)
from .tools import (
    REACT_TOOLS,
    get_tools_schema,
    get_tool_by_name,
    format_tools_for_prompt,
    ToolResult,
)

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class ReactStep:
    """A single step in the ReAct loop."""
    iteration: int
    thought: str
    action: str | None = None
    action_input: dict = field(default_factory=dict)
    observation: str | None = None
    duration_ms: int = 0
    tokens_used: int = 0


@dataclass
class ReactResult:
    """Result of the ReAct agent execution."""
    success: bool
    diffs: list[dict]
    files_modified: list[str]
    message: str
    steps: list[ReactStep]
    total_tokens: int
    total_duration_ms: int


async def run_react_agent(
    instruction: str,
    project: str,
    project_path: Path,
    max_iterations: int = 15,
    verbose: bool = True
) -> ReactResult:
    """
    Run the ReAct agent loop.
    
    Args:
        instruction: Natural language instruction
        project: Project name
        project_path: Path to the project directory
        max_iterations: Maximum reasoning/action cycles
        verbose: Whether to log detailed output
        
    Returns:
        ReactResult with diffs and execution trace
    """
    start_time = time.time()
    
    client = AsyncOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url
    )
    
    # Context shared across tool executions
    context = {
        "project_path": project_path,
        "project": project,
        "pending_changes": {},
        "applied_diffs": [],
        "files_modified": [],
        "finished": False,
        "finish_summary": "",
        "finish_success": True,
    }
    
    # Build messages
    messages = [
        {"role": "system", "content": REACT_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": REACT_INITIAL_USER_PROMPT.format(
                instruction=instruction,
                project=project,
                project_path=str(project_path)
            )
        }
    ]
    
    # Get tool schemas for function calling
    tools = get_tools_schema()
    
    steps: list[ReactStep] = []
    total_tokens = 0
    
    logger.info(f"[ReAct] Starting agent for: {instruction[:80]}...")
    
    for iteration in range(max_iterations):
        step_start = time.time()
        step = ReactStep(iteration=iteration + 1, thought="")
        
        logger.info(f"[ReAct] Iteration {iteration + 1}/{max_iterations}")
        
        try:
            # Check if we're at max iterations - force finish
            if iteration == max_iterations - 1:
                messages.append({
                    "role": "user",
                    "content": REACT_MAX_ITERATIONS_PROMPT.format(max_iterations=max_iterations)
                })
            
            # Call LLM with function calling
            response = await client.chat.completions.create(
                model=settings.model_react,  # Use react model for ReAct agent
                messages=messages,
                tools=tools,
                tool_choice="auto",  # Let model decide
                temperature=settings.llm_temperature,
                max_tokens=settings.max_tokens
            )
            
            # Track tokens
            if response.usage:
                total_tokens += response.usage.total_tokens
                step.tokens_used = response.usage.total_tokens
            
            choice = response.choices[0]
            message = choice.message
            
            # Extract thought from content (if any)
            if message.content:
                step.thought = message.content
                logger.debug(f"[ReAct] Thought: {message.content[:200]}...")
            
            # Check for tool calls
            if message.tool_calls:
                tool_call = message.tool_calls[0]  # Take first tool call
                step.action = tool_call.function.name
                
                try:
                    step.action_input = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    step.action_input = {"raw": tool_call.function.arguments}
                
                logger.info(f"[ReAct] Action: {step.action}({json.dumps(step.action_input)[:100]}...)")
                
                # Execute the tool
                tool = get_tool_by_name(step.action)
                if tool:
                    try:
                        result: ToolResult = await tool.execute(step.action_input, context)
                        step.observation = result.output
                        
                        if result.success:
                            logger.debug(f"[ReAct] Observation: {result.output[:200]}...")
                        else:
                            logger.warning(f"[ReAct] Tool failed: {result.output}")
                    except Exception as e:
                        step.observation = f"Tool execution error: {e}"
                        logger.error(f"[ReAct] Tool error: {e}")
                else:
                    step.observation = f"Unknown tool: {step.action}"
                    logger.warning(f"[ReAct] Unknown tool: {step.action}")
                
                # Add assistant message with tool call
                messages.append({
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [
                        {
                            "id": tool_call.id,
                            "type": "function",
                            "function": {
                                "name": tool_call.function.name,
                                "arguments": tool_call.function.arguments
                            }
                        }
                    ]
                })
                
                # Add tool result
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": step.observation
                })
                
            else:
                # No tool call - model is just thinking
                # Add the message and prompt to continue
                messages.append({
                    "role": "assistant",
                    "content": message.content
                })
                messages.append({
                    "role": "user",
                    "content": "Continue with your next action. Call a tool to proceed."
                })
            
            # Calculate step duration
            step.duration_ms = int((time.time() - step_start) * 1000)
            steps.append(step)
            
            # Check if agent signaled completion
            if context.get("finished"):
                logger.info(f"[ReAct] Agent finished: {context.get('finish_summary', '')}")
                break
                
        except Exception as e:
            logger.error(f"[ReAct] Iteration error: {e}")
            step.observation = f"Error: {e}"
            step.duration_ms = int((time.time() - step_start) * 1000)
            steps.append(step)
            
            # Add error context and continue
            messages.append({
                "role": "user",
                "content": REACT_ERROR_PROMPT.format(error=str(e))
            })
    
    total_duration_ms = int((time.time() - start_time) * 1000)
    
    # Get results from context
    diffs = context.get("applied_diffs", [])
    files_modified = context.get("files_modified", [])
    success = context.get("finish_success", len(diffs) > 0)
    message = context.get("finish_summary", "Agent completed")
    
    logger.info(f"[ReAct] Completed in {total_duration_ms}ms, {len(steps)} steps, {total_tokens} tokens")
    logger.info(f"[ReAct] Modified files: {files_modified}")
    
    return ReactResult(
        success=success,
        diffs=diffs,
        files_modified=files_modified,
        message=message,
        steps=steps,
        total_tokens=total_tokens,
        total_duration_ms=total_duration_ms
    )
