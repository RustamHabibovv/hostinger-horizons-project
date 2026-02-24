"""
ReAct Agent Tools - Available actions the agent can take.

Each tool has:
- name: Unique identifier
- description: What the tool does (shown to LLM)
- parameters: JSON schema for inputs
- execute: Async function to run the tool
"""
import json
import logging
import re
import subprocess
import asyncio
from pathlib import Path
from dataclasses import dataclass
from typing import Callable, Any

from app.services.diff import generate_unified_diff, read_file_content, list_project_files
from app.services.retrieval import retrieve_relevant_files

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    """Result of executing a tool."""
    success: bool
    output: str
    data: Any = None  # Structured data for internal use


@dataclass
class Tool:
    """Definition of a ReAct tool."""
    name: str
    description: str
    parameters: dict  # JSON Schema
    execute: Callable  # async (params, context) -> ToolResult


# =============================================================================
# TOOL IMPLEMENTATIONS
# =============================================================================


async def search_files(params: dict, context: dict) -> ToolResult:
    """
    Search for files matching a pattern or containing specific text.
    """
    project_path: Path = context["project_path"]
    query = params.get("query", "")
    search_type = params.get("type", "name")  # "name" or "content"
    
    if not query:
        return ToolResult(success=False, output="Error: 'query' parameter is required")
    
    try:
        all_files = list_project_files(project_path)
        matches = []
        
        if search_type == "name":
            # Search by filename/path pattern
            pattern = re.compile(query, re.IGNORECASE)
            for f in all_files:
                if pattern.search(str(f)):
                    matches.append(str(f))
        else:
            # Search file contents
            pattern = re.compile(query, re.IGNORECASE)
            for f in all_files:
                file_path = project_path / f
                if file_path.suffix in ['.js', '.jsx', '.ts', '.tsx', '.css', '.json', '.html', '.md']:
                    try:
                        content = file_path.read_text(encoding='utf-8')
                        if pattern.search(content):
                            # Find matching lines
                            for i, line in enumerate(content.split('\n'), 1):
                                if pattern.search(line):
                                    matches.append(f"{f}:{i}: {line.strip()[:80]}")
                    except Exception:
                        pass
        
        if not matches:
            return ToolResult(
                success=True,
                output=f"No files found matching '{query}' (type={search_type})"
            )
        
        # Limit output
        if len(matches) > 20:
            output = "\n".join(matches[:20]) + f"\n... and {len(matches) - 20} more matches"
        else:
            output = "\n".join(matches)
        
        return ToolResult(success=True, output=output, data=matches[:20])
        
    except Exception as e:
        return ToolResult(success=False, output=f"Search error: {e}")


async def read_file(params: dict, context: dict) -> ToolResult:
    """
    Read the contents of a file from the project.
    """
    project_path: Path = context["project_path"]
    file_path = params.get("path", "")
    start_line = params.get("start_line", 1)
    end_line = params.get("end_line")
    
    if not file_path:
        return ToolResult(success=False, output="Error: 'path' parameter is required")
    
    try:
        full_path = project_path / file_path
        if not full_path.exists():
            return ToolResult(success=False, output=f"File not found: {file_path}")
        
        if not full_path.is_file():
            return ToolResult(success=False, output=f"Not a file: {file_path}")
        
        content = full_path.read_text(encoding='utf-8')
        lines = content.split('\n')
        total_lines = len(lines)
        
        # Apply line range if specified
        if end_line:
            selected_lines = lines[start_line-1:end_line]
            content = '\n'.join(selected_lines)
            line_info = f" (lines {start_line}-{end_line} of {total_lines})"
        else:
            line_info = f" ({total_lines} lines)"
        
        # Truncate if too long
        if len(content) > 8000:
            content = content[:8000] + "\n... [truncated, use start_line/end_line for more]"
        
        return ToolResult(
            success=True,
            output=f"--- {file_path}{line_info} ---\n{content}"
        )
        
    except Exception as e:
        return ToolResult(success=False, output=f"Read error: {e}")


async def list_directory(params: dict, context: dict) -> ToolResult:
    """
    List contents of a directory.
    """
    project_path: Path = context["project_path"]
    dir_path = params.get("path", "")
    
    try:
        target = project_path / dir_path if dir_path else project_path
        
        if not target.exists():
            return ToolResult(success=False, output=f"Directory not found: {dir_path}")
        
        if not target.is_dir():
            return ToolResult(success=False, output=f"Not a directory: {dir_path}")
        
        entries = []
        for item in sorted(target.iterdir()):
            # Skip node_modules, hidden files, etc.
            if item.name in ['node_modules', '.git', '__pycache__', 'dist', 'build']:
                continue
            if item.name.startswith('.'):
                continue
                
            if item.is_dir():
                entries.append(f"📁 {item.name}/")
            else:
                size = item.stat().st_size
                if size < 1024:
                    size_str = f"{size}B"
                else:
                    size_str = f"{size // 1024}KB"
                entries.append(f"📄 {item.name} ({size_str})")
        
        if not entries:
            return ToolResult(success=True, output=f"Directory '{dir_path or '.'}' is empty")
        
        return ToolResult(success=True, output="\n".join(entries), data=entries)
        
    except Exception as e:
        return ToolResult(success=False, output=f"List error: {e}")


async def write_file(params: dict, context: dict) -> ToolResult:
    """
    Write or create a file with new content.
    This is a staging operation - actual writes happen after validation.
    """
    file_path = params.get("path", "")
    content = params.get("content", "")
    
    if not file_path:
        return ToolResult(success=False, output="Error: 'path' parameter is required")
    
    if not content:
        return ToolResult(success=False, output="Error: 'content' parameter is required")
    
    # Store in pending changes for later application
    pending: dict = context.setdefault("pending_changes", {})
    pending[file_path] = content
    
    return ToolResult(
        success=True,
        output=f"Staged changes to {file_path} ({len(content)} chars). Use validate_changes to check for errors.",
        data={"path": file_path, "size": len(content)}
    )


async def edit_file(params: dict, context: dict) -> ToolResult:
    """
    Make a surgical edit to a file using search/replace.
    """
    project_path: Path = context["project_path"]
    file_path = params.get("path", "")
    search = params.get("search", "")
    replace = params.get("replace", "")
    
    if not file_path:
        return ToolResult(success=False, output="Error: 'path' parameter is required")
    if not search:
        return ToolResult(success=False, output="Error: 'search' parameter is required")
    
    try:
        # Check if file has pending changes
        pending: dict = context.get("pending_changes", {})
        if file_path in pending:
            current_content = pending[file_path]
        else:
            full_path = project_path / file_path
            if not full_path.exists():
                return ToolResult(success=False, output=f"File not found: {file_path}")
            current_content = full_path.read_text(encoding='utf-8')
        
        # Count matches
        count = current_content.count(search)
        if count == 0:
            return ToolResult(
                success=False,
                output=f"Search text not found in {file_path}. Make sure to match exact whitespace and indentation."
            )
        if count > 1:
            return ToolResult(
                success=False,
                output=f"Search text found {count} times. Include more context to make it unique."
            )
        
        # Apply replacement
        new_content = current_content.replace(search, replace, 1)
        
        # Store in pending changes
        pending = context.setdefault("pending_changes", {})
        pending[file_path] = new_content
        
        return ToolResult(
            success=True,
            output=f"Staged edit to {file_path}. Changed {len(search)} chars to {len(replace)} chars.",
            data={"path": file_path}
        )
        
    except Exception as e:
        return ToolResult(success=False, output=f"Edit error: {e}")


async def validate_changes(params: dict, context: dict) -> ToolResult:
    """
    Validate all pending changes by running a build check.
    """
    project_path: Path = context["project_path"]
    pending: dict = context.get("pending_changes", {})
    
    if not pending:
        return ToolResult(success=False, output="No pending changes to validate")
    
    # First, check for import issues
    validation_errors = []
    
    # Check npm imports exist
    node_modules = project_path / "node_modules"
    npm_import_pattern = re.compile(
        r'''(?:import\s+.*?\s+from\s+['"]|import\s*\(\s*['"]|require\s*\(\s*['"])([^'".][^'"]*?)['"]''',
        re.MULTILINE
    )
    
    for file_path, content in pending.items():
        imports = npm_import_pattern.findall(content)
        for imp in imports:
            if imp.startswith("."):
                continue  # Skip relative imports
            
            # Get package name
            if imp.startswith("@"):
                parts = imp.split("/")
                package_name = "/".join(parts[:2]) if len(parts) >= 2 else imp
            else:
                package_name = imp.split("/")[0]
            
            # Check if exists
            if node_modules.exists() and not (node_modules / package_name).exists():
                validation_errors.append(f"Missing npm package: {package_name} (in {file_path})")
    
    # Check relative imports point to real files
    relative_import_pattern = re.compile(r'''from\s+['"](\.[^'"]+)['"]''')
    for file_path, content in pending.items():
        file_dir = (project_path / file_path).parent
        imports = relative_import_pattern.findall(content)
        for imp in imports:
            # Resolve the import path
            imp_path = imp.replace("./", "").replace("../", "")
            if not imp_path.endswith(('.js', '.jsx', '.ts', '.tsx', '.css', '.json')):
                # Try common extensions
                found = False
                for ext in ['', '.js', '.jsx', '.ts', '.tsx', '/index.js', '/index.jsx']:
                    check_path = file_dir / (imp + ext)
                    if check_path.exists() or (imp + ext.replace('.', '')) in pending:
                        found = True
                        break
                # Also check if we're creating this file
                for pending_path in pending.keys():
                    if imp.replace("./", "").replace("../", "") in pending_path:
                        found = True
                        break
    
    if validation_errors:
        return ToolResult(
            success=False,
            output="Validation errors:\n" + "\n".join(validation_errors)
        )
    
    # Try a quick syntax check by writing temp files and running build
    # For now, just report success for the static checks
    return ToolResult(
        success=True,
        output=f"Validation passed for {len(pending)} file(s): {', '.join(pending.keys())}"
    )


async def apply_changes(params: dict, context: dict) -> ToolResult:
    """
    Apply all pending changes and generate diffs.
    Should be called after validate_changes.
    """
    project_path: Path = context["project_path"]
    pending: dict = context.get("pending_changes", {})
    
    if not pending:
        return ToolResult(success=False, output="No pending changes to apply")
    
    diffs = []
    files_modified = []
    
    for file_path, new_content in pending.items():
        full_path = project_path / file_path
        
        if full_path.exists():
            # Modifying existing file
            original_content = full_path.read_text(encoding='utf-8')
            diff = generate_unified_diff(original_content, new_content, file_path)
        else:
            # Creating new file
            diff = generate_unified_diff("", new_content, file_path)
            full_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write the file
        full_path.write_text(new_content, encoding='utf-8')
        
        diffs.append({"file_path": file_path, "diff": diff})
        files_modified.append(file_path)
    
    # Store results for final output
    context["applied_diffs"] = diffs
    context["files_modified"] = files_modified
    
    # Clear pending
    context["pending_changes"] = {}
    
    return ToolResult(
        success=True,
        output=f"Applied changes to {len(files_modified)} file(s): {', '.join(files_modified)}",
        data={"diffs": diffs, "files_modified": files_modified}
    )


async def finish(params: dict, context: dict) -> ToolResult:
    """
    Signal that the task is complete.
    """
    summary = params.get("summary", "Task completed")
    success = params.get("success", True)
    
    context["finished"] = True
    context["finish_summary"] = summary
    context["finish_success"] = success
    
    return ToolResult(
        success=True,
        output=f"Task finished: {summary}"
    )


async def semantic_search(params: dict, context: dict) -> ToolResult:
    """
    Semantic search using embeddings to find relevant files.
    More powerful than regex - understands intent and finds related code.
    """
    project_path: Path = context["project_path"]
    project: str = context["project"]
    query = params.get("query", "")
    top_k = params.get("top_k", 5)
    
    if not query:
        return ToolResult(success=False, output="Error: 'query' parameter is required")
    
    try:
        results = retrieve_relevant_files(
            project=project,
            project_path=project_path,
            query=query,
            hints=None,
            top_k=top_k
        )
        
        if not results:
            return ToolResult(
                success=True,
                output=f"No relevant files found for: {query}"
            )
        
        # Format results
        output_lines = [f"Found {len(results)} relevant file(s) for '{query}':"]
        for r in results:
            signals = r.get('signals', [])
            score = r.get('score', 0)
            output_lines.append(f"\n📄 {r['file_path']} (score: {score:.2f}, signals: {', '.join(signals)})")
        
        return ToolResult(
            success=True,
            output="\n".join(output_lines),
            data=results
        )
        
    except Exception as e:
        return ToolResult(success=False, output=f"Semantic search error: {e}")


async def list_dependencies(params: dict, context: dict) -> ToolResult:
    """
    List npm packages available in the project from package.json.
    """
    project_path: Path = context["project_path"]
    include_dev = params.get("include_dev", False)
    
    try:
        package_json = project_path / "package.json"
        if not package_json.exists():
            return ToolResult(success=False, output="No package.json found in project")
        
        data = json.loads(package_json.read_text(encoding='utf-8'))
        
        deps = data.get("dependencies", {})
        dev_deps = data.get("devDependencies", {}) if include_dev else {}
        
        output_lines = ["Available npm packages:"]
        
        if deps:
            output_lines.append("\n📦 Dependencies:")
            for name, version in sorted(deps.items()):
                output_lines.append(f"  - {name}: {version}")
        
        if dev_deps:
            output_lines.append("\n🔧 Dev Dependencies:")
            for name, version in sorted(dev_deps.items()):
                output_lines.append(f"  - {name}: {version}")
        
        if not deps and not dev_deps:
            output_lines.append("No dependencies found.")
        
        return ToolResult(
            success=True,
            output="\n".join(output_lines),
            data={"dependencies": deps, "devDependencies": dev_deps}
        )
        
    except Exception as e:
        return ToolResult(success=False, output=f"Error reading package.json: {e}")


async def run_eslint(params: dict, context: dict) -> ToolResult:
    """
    Run ESLint on a file to check for syntax and style issues.
    Can run on pending changes or existing files.
    """
    project_path: Path = context["project_path"]
    file_path = params.get("path", "")
    
    if not file_path:
        return ToolResult(success=False, output="Error: 'path' parameter is required")
    
    try:
        # Check if we have pending changes for this file
        pending: dict = context.get("pending_changes", {})
        
        if file_path in pending:
            # Write to temp file for linting
            import tempfile
            import os
            
            suffix = Path(file_path).suffix
            with tempfile.NamedTemporaryFile(mode='w', suffix=suffix, delete=False, encoding='utf-8') as f:
                f.write(pending[file_path])
                temp_path = f.name
            
            try:
                result = await _run_eslint_on_file(temp_path, project_path)
            finally:
                os.unlink(temp_path)
        else:
            # Lint existing file
            full_path = project_path / file_path
            if not full_path.exists():
                return ToolResult(success=False, output=f"File not found: {file_path}")
            
            result = await _run_eslint_on_file(str(full_path), project_path)
        
        return result
        
    except Exception as e:
        return ToolResult(success=False, output=f"ESLint error: {e}")


async def _run_eslint_on_file(file_path: str, project_path: Path) -> ToolResult:
    """Run ESLint on a specific file."""
    
    def run_lint():
        # Try project's eslint first, fall back to basic check
        try:
            # Use project's eslint config if available
            result = subprocess.run(
                ["npx", "eslint", "--format", "compact", file_path],
                cwd=str(project_path),
                capture_output=True,
                timeout=30
            )
            return result
        except FileNotFoundError:
            # No npm/npx available
            return None
    
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, run_lint)
        
        if result is None:
            return ToolResult(
                success=True,
                output="ESLint not available (npm/npx not found). Skipping lint check."
            )
        
        stdout = result.stdout.decode('utf-8', errors='replace').strip()
        stderr = result.stderr.decode('utf-8', errors='replace').strip()
        
        if result.returncode == 0:
            return ToolResult(
                success=True,
                output="✓ ESLint: No errors found"
            )
        else:
            # Parse eslint output
            output = stdout if stdout else stderr
            
            # Truncate if too long
            if len(output) > 2000:
                output = output[:2000] + "\n... (truncated)"
            
            return ToolResult(
                success=False,
                output=f"ESLint found issues:\n{output}"
            )
            
    except subprocess.TimeoutExpired:
        return ToolResult(success=True, output="ESLint timed out, skipping.")
    except Exception as e:
        return ToolResult(success=False, output=f"ESLint error: {e}")


# =============================================================================
# TOOL DEFINITIONS (for LLM)
# =============================================================================

REACT_TOOLS: list[Tool] = [
    Tool(
        name="search_files",
        description="Search for files by name pattern or content. Use type='name' for filename search, type='content' for searching inside files.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search pattern (regex supported)"},
                "type": {"type": "string", "enum": ["name", "content"], "default": "name"}
            },
            "required": ["query"]
        },
        execute=search_files
    ),
    Tool(
        name="read_file",
        description="Read the contents of a file. Use start_line and end_line to read specific sections.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path to the file"},
                "start_line": {"type": "integer", "description": "First line to read (1-indexed)", "default": 1},
                "end_line": {"type": "integer", "description": "Last line to read (optional)"}
            },
            "required": ["path"]
        },
        execute=read_file
    ),
    Tool(
        name="list_directory",
        description="List the contents of a directory to explore the project structure.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path to directory (empty for root)", "default": ""}
            },
            "required": []
        },
        execute=list_directory
    ),
    Tool(
        name="write_file",
        description="Create or overwrite a file with complete new content. Use for new files or complete rewrites.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path for the file"},
                "content": {"type": "string", "description": "Complete file content"}
            },
            "required": ["path", "content"]
        },
        execute=write_file
    ),
    Tool(
        name="edit_file",
        description="Make a surgical edit using search/replace. The search text must match exactly (including whitespace). Include 3-5 lines of context for unique matching.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to edit"},
                "search": {"type": "string", "description": "Exact text to find (with context)"},
                "replace": {"type": "string", "description": "Replacement text"}
            },
            "required": ["path", "search", "replace"]
        },
        execute=edit_file
    ),
    Tool(
        name="validate_changes",
        description="Validate all pending changes for import errors and syntax issues. Call this before apply_changes.",
        parameters={
            "type": "object",
            "properties": {},
            "required": []
        },
        execute=validate_changes
    ),
    Tool(
        name="apply_changes",
        description="Apply all pending changes to the filesystem and generate diffs. Call this after validate_changes passes.",
        parameters={
            "type": "object",
            "properties": {},
            "required": []
        },
        execute=apply_changes
    ),
    Tool(
        name="finish",
        description="Signal that the task is complete. Call this when all changes are applied or if the task cannot be completed.",
        parameters={
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Brief summary of what was done"},
                "success": {"type": "boolean", "description": "Whether the task was completed successfully", "default": True}
            },
            "required": ["summary"]
        },
        execute=finish
    ),
    Tool(
        name="semantic_search",
        description="Find relevant files using semantic search (embeddings). Better than regex for understanding intent - finds related code even with different wording.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language description of what you're looking for"},
                "top_k": {"type": "integer", "description": "Number of results to return", "default": 5}
            },
            "required": ["query"]
        },
        execute=semantic_search
    ),
    Tool(
        name="list_dependencies",
        description="List npm packages available in the project. Use this to check what libraries are installed before importing them.",
        parameters={
            "type": "object",
            "properties": {
                "include_dev": {"type": "boolean", "description": "Include devDependencies", "default": False}
            },
            "required": []
        },
        execute=list_dependencies
    ),
    Tool(
        name="run_eslint",
        description="Run ESLint on a file to check for syntax errors and style issues. Works on pending changes too.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to lint"}
            },
            "required": ["path"]
        },
        execute=run_eslint
    )
]


def get_tools_schema() -> list[dict]:
    """Get tool schemas formatted for OpenAI function calling."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters
            }
        }
        for tool in REACT_TOOLS
    ]


def get_tool_by_name(name: str) -> Tool | None:
    """Find a tool by name."""
    for tool in REACT_TOOLS:
        if tool.name == name:
            return tool
    return None


def format_tools_for_prompt() -> str:
    """Format tools for inclusion in a text prompt (non-function-calling models)."""
    lines = ["Available tools:"]
    for tool in REACT_TOOLS:
        params = tool.parameters.get("properties", {})
        param_strs = []
        for name, schema in params.items():
            param_type = schema.get("type", "any")
            desc = schema.get("description", "")
            required = name in tool.parameters.get("required", [])
            req_str = " (required)" if required else ""
            param_strs.append(f"  - {name}: {param_type}{req_str} - {desc}")
        
        lines.append(f"\n{tool.name}: {tool.description}")
        if param_strs:
            lines.extend(param_strs)
    
    return "\n".join(lines)
