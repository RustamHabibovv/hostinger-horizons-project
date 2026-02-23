"""
Intent parser - Classifies user instruction and extracts hints.
Uses GPT-4o-mini for cost efficiency ($0.15/1M input, $0.60/1M output).
"""
import json
import logging
from dataclasses import dataclass
from enum import Enum
from openai import AsyncOpenAI

from app.config import get_settings
from app.prompts.intent import INTENT_SYSTEM_PROMPT

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


class IntentType(str, Enum):
    """Types of code change intents."""
    FEATURE = "feature"        # Add new functionality
    BUGFIX = "bugfix"          # Fix an issue
    REFACTOR = "refactor"      # Restructure without changing behavior
    STYLE = "style"            # Visual/CSS changes
    DOCS = "docs"              # Documentation/comments
    UNKNOWN = "unknown"


class Complexity(str, Enum):
    """Estimated task complexity."""
    LOW = "low"          # Single file, simple change
    MEDIUM = "medium"    # 2-3 files, moderate change
    HIGH = "high"        # Multiple files, complex change


@dataclass
class ParsedIntent:
    """Parsed intent from user instruction."""
    intent_type: IntentType
    complexity: Complexity
    summary: str                    # One-line summary
    file_hints: list[str]           # Likely file names/patterns
    component_hints: list[str]      # Component/function names
    keywords: list[str]             # Important keywords
    requires_new_files: bool        # Might need to create files
    confidence: float               # 0-1 confidence in parsing


async def parse_intent(instruction: str) -> ParsedIntent:
    """
    Parse user instruction to extract intent and hints.
    Uses fast/cheap model for cost efficiency.
    """
    client = AsyncOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url
    )
    
    logger.info(f"Parsing intent: {instruction[:80]}...")
    
    response = await client.chat.completions.create(
        model=settings.model_intent,  # Cheap model for parsing
        messages=[
            {"role": "system", "content": INTENT_SYSTEM_PROMPT},
            {"role": "user", "content": f"Instruction: {instruction}"}
        ],
        response_format={"type": "json_object"},
        temperature=0.1,  # Low temp for consistent parsing
        max_tokens=500
    )
    
    content = response.choices[0].message.content
    usage = response.usage
    
    logger.debug(f"Intent parse tokens: {usage.total_tokens}")
    logger.debug(f"Intent parse result: {content}")
    
    try:
        json_content = _extract_json(content)
        result = json.loads(json_content)
        
        parsed = ParsedIntent(
            intent_type=IntentType(result.get("intent_type", "unknown")),
            complexity=Complexity(result.get("complexity", "medium")),
            summary=result.get("summary", instruction[:100]),
            file_hints=result.get("file_hints", []),
            component_hints=result.get("component_hints", []),
            keywords=result.get("keywords", []),
            requires_new_files=result.get("requires_new_files", False),
            confidence=result.get("confidence", 0.5)
        )
        
        logger.info(f"Parsed intent: type={parsed.intent_type.value}, "
                   f"complexity={parsed.complexity.value}, "
                   f"hints={parsed.file_hints + parsed.component_hints}")
        
        return parsed
        
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(f"Intent parsing failed: {e}, using defaults")
        return ParsedIntent(
            intent_type=IntentType.UNKNOWN,
            complexity=Complexity.MEDIUM,
            summary=instruction[:100],
            file_hints=[],
            component_hints=[],
            keywords=[],
            requires_new_files=False,
            confidence=0.0
        )
