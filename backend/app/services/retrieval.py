"""
Multi-signal retrieval service combining semantic search, keyword matching, and dependency analysis.
"""
import logging
import re
from pathlib import Path
from typing import Optional

from app.services.embeddings import index_project, search_similar
from app.services.diff import list_project_files, read_file_content

logger = logging.getLogger(__name__)


def retrieve_relevant_files(
    project: str,
    project_path: Path,
    query: str,
    hints: Optional[list[str]] = None,
    top_k: int = 5
) -> list[dict]:
    """
    Multi-signal retrieval combining:
    1. Semantic search (embeddings)
    2. Keyword/pattern matching
    3. File hint matching
    
    Args:
        project: Project name
        project_path: Path to project
        query: User's instruction/query
        hints: Optional file/component hints from intent parsing
        top_k: Max files to return
        
    Returns:
        List of {file_path, content, score, signals} sorted by relevance
    """
    # Ensure project is indexed
    index_project(project, project_path)
    
    # Signal 1: Semantic search
    semantic_results = search_similar(project, query, top_k=top_k * 2)
    
    # Signal 2: Keyword matching
    keyword_results = _keyword_search(project_path, query, top_k=top_k)
    
    # Signal 3: Hint matching (if provided)
    hint_results = []
    if hints:
        hint_results = _match_hints(project_path, hints)
    
    # Merge and score results
    merged = _merge_results(
        semantic_results,
        keyword_results,
        hint_results,
        top_k=top_k
    )
    
    logger.info(f"Retrieved {len(merged)} files for query: {query[:50]}...")
    for r in merged:
        logger.debug(f"  - {r['file_path']} (score={r['score']:.3f}, signals={r['signals']})")
    
    return merged


def _keyword_search(
    project_path: Path,
    query: str,
    top_k: int = 5
) -> list[dict]:
    """Search files by keyword matching."""
    # Extract potential keywords (component names, function names, etc.)
    keywords = _extract_keywords(query)
    
    if not keywords:
        return []
    
    files = list_project_files(project_path)
    results = []
    
    for file_path in files:
        try:
            content = read_file_content(project_path, file_path)
            
            # Count keyword matches
            match_count = 0
            matched_keywords = []
            
            for keyword in keywords:
                pattern = re.compile(re.escape(keyword), re.IGNORECASE)
                matches = pattern.findall(content)
                if matches:
                    match_count += len(matches)
                    matched_keywords.append(keyword)
            
            if match_count > 0:
                results.append({
                    "file_path": file_path,
                    "content": f"File: {file_path}\n\n{content}",
                    "score": min(match_count / 10, 1.0),  # Normalize
                    "metadata": {"matched_keywords": matched_keywords}
                })
                
        except Exception:
            pass
    
    # Sort by match count and return top_k
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]


def _extract_keywords(query: str) -> list[str]:
    """Extract potential keywords from query."""
    keywords = []
    
    # Look for PascalCase words (component names)
    pascal_pattern = re.compile(r'\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b')
    keywords.extend(pascal_pattern.findall(query))
    
    # Look for camelCase words
    camel_pattern = re.compile(r'\b[a-z]+(?:[A-Z][a-z]+)+\b')
    keywords.extend(camel_pattern.findall(query))
    
    # Look for file-like patterns
    file_pattern = re.compile(r'\b[\w-]+\.(jsx?|tsx?|css|html)\b', re.IGNORECASE)
    keywords.extend(file_pattern.findall(query))
    
    # Common UI terms
    ui_terms = ["button", "header", "footer", "navbar", "sidebar", "modal", "form", 
                "input", "card", "list", "todo", "item", "component", "page"]
    for term in ui_terms:
        if term.lower() in query.lower():
            keywords.append(term)
    
    # Common style terms
    style_terms = ["color", "background", "font", "margin", "padding", "border",
                   "dark", "light", "theme", "style", "css"]
    for term in style_terms:
        if term.lower() in query.lower():
            keywords.append(term)
    
    return list(set(keywords))


def _match_hints(project_path: Path, hints: list[str]) -> list[dict]:
    """Match files based on parsed hints (component names, file patterns)."""
    files = list_project_files(project_path)
    results = []
    
    for file_path in files:
        for hint in hints:
            # Check if hint matches file path or name
            if hint.lower() in file_path.lower():
                try:
                    content = read_file_content(project_path, file_path)
                    results.append({
                        "file_path": file_path,
                        "content": f"File: {file_path}\n\n{content}",
                        "score": 0.9,  # High score for direct hint match
                        "metadata": {"matched_hint": hint}
                    })
                    break
                except Exception:
                    pass
    
    return results


def _merge_results(
    semantic: list[dict],
    keyword: list[dict],
    hints: list[dict],
    top_k: int
) -> list[dict]:
    """Merge results from different signals with weighted scoring."""
    # Weights for each signal
    SEMANTIC_WEIGHT = 0.5
    KEYWORD_WEIGHT = 0.3
    HINT_WEIGHT = 0.8  # Hints from intent parsing are very valuable
    
    # Merge by file path
    merged: dict[str, dict] = {}
    
    def add_result(result: dict, weight: float, signal: str):
        path = result["file_path"]
        if path not in merged:
            merged[path] = {
                "file_path": path,
                "content": result["content"],
                "score": 0,
                "signals": [],
                "metadata": result.get("metadata", {})
            }
        merged[path]["score"] += result["score"] * weight
        merged[path]["signals"].append(signal)
    
    for r in semantic:
        add_result(r, SEMANTIC_WEIGHT, "semantic")
    
    for r in keyword:
        add_result(r, KEYWORD_WEIGHT, "keyword")
    
    for r in hints:
        add_result(r, HINT_WEIGHT, "hint")
    
    # Sort by combined score
    sorted_results = sorted(merged.values(), key=lambda x: x["score"], reverse=True)
    
    return sorted_results[:top_k]


def get_file_dependencies(project_path: Path, file_path: str) -> list[str]:
    """
    Analyze imports to find dependencies of a file.
    Returns list of related file paths in the project.
    """
    try:
        content = read_file_content(project_path, file_path)
    except Exception:
        return []
    
    # Find import statements
    import_patterns = [
        # ES6 imports: import X from './path'
        re.compile(r"import\s+.*?\s+from\s+['\"](\.[^'\"]+)['\"]"),
        # Require: require('./path')
        re.compile(r"require\s*\(\s*['\"](\.[^'\"]+)['\"]\s*\)"),
    ]
    
    dependencies = []
    project_files = set(list_project_files(project_path))
    
    for pattern in import_patterns:
        for match in pattern.finditer(content):
            import_path = match.group(1)
            
            # Resolve relative path
            base_dir = Path(file_path).parent
            resolved = (base_dir / import_path).as_posix()
            
            # Try with common extensions
            for ext in ["", ".js", ".jsx", ".ts", ".tsx", ".css"]:
                candidate = resolved + ext
                candidate = candidate.lstrip("./")
                if candidate in project_files:
                    dependencies.append(candidate)
                    break
    
    return dependencies
