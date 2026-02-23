"""
Planner - Creates execution plan based on intent and retrieved files.
Uses GPT-4o-mini for cost efficiency.
"""
import json
import logging
from dataclasses import dataclass

from openai import AsyncOpenAI

from app.config import get_settings
from app.services.agent.intent import ParsedIntent

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


@dataclass
class ExecutionStep:
    """A single step in the execution plan."""
    step_number: int
    action: str              # modify, create, delete
    file_path: str           # Target file
    description: str         # What to do
    depends_on: list[int]    # Step numbers this depends on


@dataclass
class ExecutionPlan:
    """Plan for executing the code change."""
    steps: list[ExecutionStep]
    files_to_modify: list[str]
    files_to_create: list[str]
    estimated_changes: int        # Rough estimate of lines changed
    reasoning: str                # Why this plan


PLANNER_SYSTEM_PROMPT = """You are a code change planner for React projects. Given:
1. User instruction
2. Parsed intent (type, complexity, hints)
3. Retrieved relevant files with their content

Create an execution plan specifying which files to modify and in what order.

Consider:
- Dependencies between files (modify imports before importers)
- Minimal changes needed
- Whether new files need to be created

Respond with JSON:
{
  "steps": [
    {
      "step_number": 1,
      "action": "modify",
      "file_path": "src/App.jsx",
      "description": "Add dark mode state and toggle function",
      "depends_on": []
    }
  ],
  "files_to_modify": ["src/App.jsx"],
  "files_to_create": [],
  "estimated_changes": 20,
  "reasoning": "Brief explanation of the plan"
}

Return ONLY valid JSON. Be concise."""


async def create_plan(
    instruction: str,
    intent: ParsedIntent,
    retrieved_files: list[dict]
) -> ExecutionPlan:
    """
    Create an execution plan based on intent and retrieved files.
    Uses fast/cheap model for cost efficiency.
    """
    client = AsyncOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url
    )
    
    # Build context
    files_summary = []
    for f in retrieved_files[:5]:  # Limit to top 5 for context
        # Truncate content for planning (we just need structure)
        content = f.get("content", "")
        if len(content) > 1000:
            content = content[:1000] + "\n... (truncated)"
        files_summary.append(f"File: {f['file_path']}\n{content}")
    
    user_prompt = f"""Instruction: {instruction}

Intent Analysis:
- Type: {intent.intent_type.value}
- Complexity: {intent.complexity.value}
- Summary: {intent.summary}
- File hints: {intent.file_hints}
- Component hints: {intent.component_hints}
- May need new files: {intent.requires_new_files}

Retrieved Files:
{chr(10).join(files_summary)}

Create an execution plan:"""

    logger.info(f"Creating execution plan...")
    logger.debug(f"Planning with {len(retrieved_files)} retrieved files")
    
    response = await client.chat.completions.create(
        model=settings.model_planner,  # Cheap model for planning
        messages=[
            {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=1000
    )
    
    content = response.choices[0].message.content
    usage = response.usage
    
    logger.debug(f"Planner tokens: {usage.total_tokens}")
    logger.debug(f"Plan result: {content}")
    
    try:
        json_content = _extract_json(content)
        result = json.loads(json_content)
        
        steps = []
        for s in result.get("steps", []):
            steps.append(ExecutionStep(
                step_number=s.get("step_number", 1),
                action=s.get("action", "modify"),
                file_path=s.get("file_path", ""),
                description=s.get("description", ""),
                depends_on=s.get("depends_on", [])
            ))
        
        plan = ExecutionPlan(
            steps=steps,
            files_to_modify=result.get("files_to_modify", []),
            files_to_create=result.get("files_to_create", []),
            estimated_changes=result.get("estimated_changes", 10),
            reasoning=result.get("reasoning", "")
        )
        
        logger.info(f"Created plan: {len(plan.steps)} steps, "
                   f"modify={plan.files_to_modify}, create={plan.files_to_create}")
        logger.info(f"Plan reasoning: {plan.reasoning}")
        
        return plan
        
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(f"Planning failed: {e}, using fallback plan")
        
        # Fallback: modify all retrieved files
        files = [f["file_path"] for f in retrieved_files[:3]]
        steps = [
            ExecutionStep(
                step_number=i+1,
                action="modify",
                file_path=f,
                description=f"Apply changes to {f}",
                depends_on=[]
            )
            for i, f in enumerate(files)
        ]
        
        return ExecutionPlan(
            steps=steps,
            files_to_modify=files,
            files_to_create=[],
            estimated_changes=10,
            reasoning="Fallback plan: modify top retrieved files"
        )
