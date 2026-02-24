"""
ReAct Agent - Reasoning and Acting loop for code generation.

This approach uses a thought/action/observation loop where the LLM:
1. Thinks about what to do next
2. Selects and executes a tool
3. Observes the result
4. Repeats until the task is complete
"""

from .loop import run_react_agent
from .tools import REACT_TOOLS

__all__ = ["run_react_agent", "REACT_TOOLS"]
