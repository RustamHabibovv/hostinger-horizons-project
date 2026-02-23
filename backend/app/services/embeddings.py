"""
Embeddings service for semantic code search using FAISS.
Uses text-embedding-3-small for cost efficiency ($0.02/1M tokens).
"""
import hashlib
import json
import logging
from pathlib import Path
from typing import Optional

import faiss
import numpy as np
from openai import OpenAI

from app.config import get_settings
from app.services.diff import list_project_files, read_file_content

logger = logging.getLogger(__name__)
settings = get_settings()

# Embedding dimension for text-embedding-3-small
EMBEDDING_DIM = 1536

# In-memory cache of indices
_indices: dict[str, dict] = {}


def _get_index_dir() -> Path:
    """Get directory for storing FAISS indices."""
    backend_dir = Path(__file__).parent.parent.parent
    index_dir = backend_dir / ".faiss_indices"
    index_dir.mkdir(exist_ok=True)
    return index_dir


def _get_index_path(project: str) -> tuple[Path, Path]:
    """Get paths for FAISS index and metadata files."""
    safe_name = project.replace("-", "_").replace(" ", "_")
    index_dir = _get_index_dir()
    return (
        index_dir / f"{safe_name}.index",
        index_dir / f"{safe_name}.meta.json"
    )


def compute_project_hash(project_path: Path) -> str:
    """Compute hash of project files to detect changes."""
    files = list_project_files(project_path)
    content_hash = hashlib.md5()
    
    for file_path in sorted(files):
        try:
            content = read_file_content(project_path, file_path)
            content_hash.update(f"{file_path}:{content}".encode())
        except Exception:
            pass
    
    return content_hash.hexdigest()[:16]


def get_embeddings(texts: list[str]) -> np.ndarray:
    """
    Get embeddings using configured embedding model.
    Default: OpenAI text-embedding-3-small ($0.02 per 1M tokens).
    
    Returns numpy array of shape (n_texts, 1536)
    """
    client = OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url
    )
    
    response = client.embeddings.create(
        model=settings.model_embedding,
        input=texts
    )
    
    embeddings = [item.embedding for item in response.data]
    return np.array(embeddings, dtype=np.float32)


def index_project(project: str, project_path: Path, force: bool = False) -> dict:
    """
    Index a project's source files for semantic search using FAISS.
    
    Args:
        project: Project name
        project_path: Path to project
        force: Re-index even if already indexed
        
    Returns:
        Dict with indexing stats
    """
    index_path, meta_path = _get_index_path(project)
    current_hash = compute_project_hash(project_path)
    
    # Check if already indexed (and up to date)
    if not force and index_path.exists() and meta_path.exists():
        try:
            with open(meta_path, 'r') as f:
                metadata = json.load(f)
            
            if metadata.get("project_hash") == current_hash:
                logger.info(f"Project {project} already indexed (hash match)")
                return {
                    "indexed": False,
                    "reason": "already_indexed",
                    "files_count": metadata.get("files_count", 0)
                }
        except Exception:
            pass
    
    # Read all project files
    files = list_project_files(project_path)
    if not files:
        return {"indexed": False, "reason": "no_files", "files_count": 0}
    
    logger.info(f"Indexing {len(files)} files for {project}")
    
    # Prepare documents for embedding
    documents = []
    file_metadata = []
    
    for file_path in files:
        try:
            content = read_file_content(project_path, file_path)
            
            # Create document with file path context
            doc = f"File: {file_path}\n\n{content}"
            
            documents.append(doc)
            file_metadata.append({
                "file_path": file_path,
                "file_type": Path(file_path).suffix,
                "char_count": len(content),
                "content": doc
            })
            
        except Exception as e:
            logger.warning(f"Failed to read {file_path}: {e}")
    
    if not documents:
        return {"indexed": False, "reason": "no_readable_files", "files_count": 0}
    
    # Get embeddings
    logger.debug(f"Getting embeddings for {len(documents)} documents")
    embeddings = get_embeddings(documents)
    
    # Create FAISS index - normalize for cosine similarity
    faiss.normalize_L2(embeddings)
    
    index = faiss.IndexFlatIP(EMBEDDING_DIM)  # Inner product = cosine after normalization
    index.add(embeddings)
    
    # Save index
    faiss.write_index(index, str(index_path))
    
    # Save metadata
    metadata = {
        "project": project,
        "project_hash": current_hash,
        "files_count": len(documents),
        "files": file_metadata
    }
    with open(meta_path, 'w') as f:
        json.dump(metadata, f)
    
    # Cache in memory
    _indices[project] = {
        "index": index,
        "metadata": metadata
    }
    
    logger.info(f"Indexed {len(documents)} files for {project}")
    
    return {
        "indexed": True,
        "files_count": len(documents),
        "project_hash": current_hash
    }


def _load_index(project: str) -> Optional[dict]:
    """Load index from disk or cache."""
    # Check cache first
    if project in _indices:
        return _indices[project]
    
    index_path, meta_path = _get_index_path(project)
    
    if not index_path.exists() or not meta_path.exists():
        return None
    
    try:
        index = faiss.read_index(str(index_path))
        with open(meta_path, 'r') as f:
            metadata = json.load(f)
        
        _indices[project] = {
            "index": index,
            "metadata": metadata
        }
        return _indices[project]
        
    except Exception as e:
        logger.error(f"Failed to load index for {project}: {e}")
        return None


def search_similar(
    project: str,
    query: str,
    top_k: int = 5
) -> list[dict]:
    """
    Search for files similar to query using semantic search.
    
    Args:
        project: Project name
        query: Search query (natural language or code)
        top_k: Number of results to return
        
    Returns:
        List of {file_path, content, score, metadata}
    """
    data = _load_index(project)
    
    if data is None:
        logger.warning(f"No index found for {project}")
        return []
    
    index = data["index"]
    metadata = data["metadata"]
    files = metadata.get("files", [])
    
    if not files:
        return []
    
    # Get query embedding
    query_embedding = get_embeddings([query])
    faiss.normalize_L2(query_embedding)
    
    # Search
    k = min(top_k, len(files))
    distances, indices = index.search(query_embedding, k)
    
    # Format results
    results = []
    for i, idx in enumerate(indices[0]):
        if idx < 0 or idx >= len(files):
            continue
            
        file_info = files[idx]
        results.append({
            "file_path": file_info["file_path"],
            "content": file_info.get("content", ""),
            "score": float(distances[0][i]),  # Cosine similarity (0-1)
            "metadata": {
                "file_type": file_info.get("file_type"),
                "char_count": file_info.get("char_count")
            }
        })
    
    logger.debug(f"Search returned {len(results)} results for query: {query[:50]}...")
    return results


def get_index_stats(project: str) -> dict:
    """Get indexing statistics for a project."""
    index_path, meta_path = _get_index_path(project)
    
    if not meta_path.exists():
        return {"indexed": False, "files_count": 0}
    
    try:
        with open(meta_path, 'r') as f:
            metadata = json.load(f)
        
        return {
            "indexed": True,
            "files_count": metadata.get("files_count", 0),
            "project_hash": metadata.get("project_hash")
        }
    except Exception:
        return {"indexed": False, "files_count": 0}
