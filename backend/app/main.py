import logging
import sys
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import get_settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

# Set DEBUG level for our app modules
logging.getLogger("app").setLevel(logging.DEBUG)

logger = logging.getLogger(__name__)
settings = get_settings()

app = FastAPI(
    title="AI Code Editor API",
    description="API for generating code changes from natural language instructions",
    version="0.1.0"
)

# CORS middleware for future frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routes
app.include_router(router, prefix="/api/v1", tags=["code-generation"])


@app.on_event("startup")
async def startup_event():
    logger.info(f"Starting AI Code Editor API")
    logger.info(f"LLM Model: {settings.llm_model}")
    logger.info(f"Projects path: {settings.projects_base_path}")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "model": settings.llm_model}
