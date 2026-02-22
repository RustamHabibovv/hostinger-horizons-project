"""
Simplified diff service for code change generation and application.
Uses only git apply for applying diffs.
"""
import difflib
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class ApplyResult:
    """Result of applying diffs."""
    success: bool
    message: str


def generate_unified_diff(
    original_content: str,
    modified_content: str,
    filename: str
) -> str:
    """
    Generate a unified diff between original and modified content.
    """
    original_lines = original_content.splitlines(keepends=True)
    modified_lines = modified_content.splitlines(keepends=True)
    
    # Ensure files end with newline for proper diff format
    if original_lines and not original_lines[-1].endswith('\n'):
        original_lines[-1] += '\n'
    if modified_lines and not modified_lines[-1].endswith('\n'):
        modified_lines[-1] += '\n'
    
    diff_lines = list(difflib.unified_diff(
        original_lines,
        modified_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}"
    ))
    
    # unified_diff output lines already have newlines from the input
    # but header lines (---, +++, @@) don't - add them
    result = []
    for line in diff_lines:
        if not line.endswith('\n'):
            line += '\n'
        result.append(line)
    
    return "".join(result)


def read_file_content(project_path: Path, relative_path: str) -> str:
    """Read file content from project."""
    file_path = project_path / relative_path
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {relative_path}")
    return file_path.read_text(encoding="utf-8")


def list_project_files(
    project_path: Path, 
    extensions: tuple = (".jsx", ".js", ".tsx", ".ts", ".css", ".html", ".json")
) -> list[str]:
    """List all relevant source files in a project."""
    files = []
    src_path = project_path / "src"
    
    if not src_path.exists():
        # If no src folder, scan project root (excluding node_modules, etc.)
        src_path = project_path
    
    exclude_dirs = {"node_modules", ".git", "dist", "build", ".venv", "__pycache__"}
    
    for ext in extensions:
        for file in src_path.rglob(f"*{ext}"):
            # Skip excluded directories
            if any(excluded in file.parts for excluded in exclude_dirs):
                continue
            relative = file.relative_to(project_path)
            files.append(str(relative).replace("\\", "/"))
    
    return sorted(files)


def read_all_project_files(project_path: Path) -> dict[str, str]:
    """Read all source files in a project and return as dict."""
    files = list_project_files(project_path)
    contents = {}
    
    for file_path in files:
        try:
            contents[file_path] = read_file_content(project_path, file_path)
        except Exception as e:
            logger.warning(f"Could not read {file_path}: {e}")
    
    return contents


def apply_with_git(project_path: Path, combined_diff: str) -> ApplyResult:
    """
    Apply a combined diff to the project using git apply.
    Creates backups before applying.
    """
    if not combined_diff.strip():
        return ApplyResult(success=True, message="No changes to apply")
    
    # Create backup of modified files before applying
    backup_dir = _create_project_backup(project_path, combined_diff)
    
    try:
        # Write diff to temp file
        with tempfile.NamedTemporaryFile(
            mode='w', 
            suffix='.patch', 
            delete=False, 
            encoding='utf-8'
        ) as f:
            f.write(combined_diff)
            patch_file = f.name
        
        # Apply with git
        result = subprocess.run(
            ["git", "apply", patch_file],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        # Clean up temp file
        Path(patch_file).unlink()
        
        if result.returncode == 0:
            return ApplyResult(
                success=True, 
                message=f"Changes applied successfully. Backup: {backup_dir}"
            )
        else:
            return ApplyResult(
                success=False,
                message=f"git apply failed: {result.stderr}"
            )
            
    except FileNotFoundError:
        return ApplyResult(success=False, message="Git not available")
    except subprocess.TimeoutExpired:
        return ApplyResult(success=False, message="git apply timed out")
    except Exception as e:
        return ApplyResult(success=False, message=str(e))


def _create_project_backup(project_path: Path, diff_text: str) -> str:
    """Create backups of files that will be modified by the diff."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = project_path / f".backups/{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    
    # Parse diff to find affected files
    for line in diff_text.split('\n'):
        if line.startswith('--- a/'):
            file_path = line[6:]
            source_file = project_path / file_path
            if source_file.exists():
                backup_file = backup_dir / file_path
                backup_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_file, backup_file)
                logger.info(f"Backed up: {file_path}")
    
    return str(backup_dir)
