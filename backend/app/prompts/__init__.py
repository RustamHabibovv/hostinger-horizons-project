"""
Centralized prompts for the AI code editor.

All prompts are organized by component:
- simple: Simple /generate endpoint prompts
- intent: Intent parsing prompts
- planner: Execution planning prompts  
- executor: Code generation/execution prompts
"""

from app.prompts.simple import (
    SYSTEM_PROMPT_FULL_CONTENT as SIMPLE_PROMPT_FULL_CONTENT,
    SYSTEM_PROMPT_SEARCH_REPLACE as SIMPLE_PROMPT_SEARCH_REPLACE,
    SYSTEM_PROMPT_DIFF as SIMPLE_PROMPT_DIFF,
)

from app.prompts.intent import (
    INTENT_SYSTEM_PROMPT,
)

from app.prompts.planner import (
    PLANNER_SYSTEM_PROMPT,
)

from app.prompts.executor import (
    EXECUTOR_PROMPT_FULL_CONTENT,
    EXECUTOR_PROMPT_SEARCH_REPLACE,
    EXECUTOR_PROMPT_DIFF,
    EXECUTOR_RETRY_PROMPT,
)

from app.prompts.react_agent import (
    REACT_SYSTEM_PROMPT,
    REACT_INITIAL_USER_PROMPT,
    REACT_OBSERVATION_PROMPT,
    REACT_ERROR_PROMPT,
    REACT_MAX_ITERATIONS_PROMPT,
)

__all__ = [
    # Simple endpoint
    "SIMPLE_PROMPT_FULL_CONTENT",
    "SIMPLE_PROMPT_SEARCH_REPLACE", 
    "SIMPLE_PROMPT_DIFF",
    # Agent - Intent
    "INTENT_SYSTEM_PROMPT",
    # Agent - Planner
    "PLANNER_SYSTEM_PROMPT",
    # Agent - Executor
    "EXECUTOR_PROMPT_FULL_CONTENT",
    "EXECUTOR_PROMPT_SEARCH_REPLACE",
    "EXECUTOR_PROMPT_DIFF",
    "EXECUTOR_RETRY_PROMPT",
    # ReAct Agent
    "REACT_SYSTEM_PROMPT",
    "REACT_INITIAL_USER_PROMPT",
    "REACT_OBSERVATION_PROMPT",
    "REACT_ERROR_PROMPT",
    "REACT_MAX_ITERATIONS_PROMPT",
]
