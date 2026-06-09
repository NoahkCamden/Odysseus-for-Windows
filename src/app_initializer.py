# src/app_initializer.py
"""Initialize all application components and dependencies."""
import os
import logging
from typing import Dict, Any

from src.constants import (
    DATA_DIR, PERSONAL_DIR, RUNBOOK_DIR, UPLOAD_DIR,
    SESSIONS_FILE, DEFAULT_HOST, OPENAI_API_KEY
)
from src.memory import MemoryManager
from services.memory.skills import SkillsManager
from core.session_manager import SessionManager
from core.models import set_session_manager
from src.personal_docs import PersonalDocsManager
from src.api_key_manager import APIKeyManager
from src.preset_manager import PresetManager
from src.chat_processor import ChatProcessor
from src.model_discovery import ModelDiscovery
from src.chat_handler import ChatHandler
from src.research_handler import ResearchHandler
from src.upload_handler import UploadHandler
from src.search import update_search_config

logger = logging.getLogger(__name__)


class LazyMemoryVectorStore:
    """Delay memory-vector initialization until a route actually needs it."""

    def __init__(self, data_dir: str, embedding_model=None):
        self._data_dir = data_dir
        self._embedding_model = embedding_model
        self._store = None
        self._init_attempted = False

    def _get_store(self):
        if self._store is not None:
            return self._store
        if self._init_attempted:
            return None
        from src.embeddings import embedding_backend_likely_available

        if not embedding_backend_likely_available():
            return None

        self._init_attempted = True
        try:
            from src.memory_vector import MemoryVectorStore
            self._store = MemoryVectorStore(self._data_dir, embedding_model=self._embedding_model)
            if self._store and self._store.healthy:
                logger.info("LazyMemoryVectorStore initialized on demand")
                return self._store
        except Exception as e:
            logger.warning(f"LazyMemoryVectorStore init failed: {e}")
        self._store = None
        return None

    @property
    def healthy(self) -> bool:
        store = self._get_store()
        return bool(store and store.healthy)

    def count(self) -> int:
        store = self._get_store()
        return store.count() if store and store.healthy else 0

    def add(self, *args, **kwargs):
        store = self._get_store()
        if store and store.healthy:
            return store.add(*args, **kwargs)

    def remove(self, *args, **kwargs):
        store = self._get_store()
        if store and store.healthy:
            return store.remove(*args, **kwargs)

    def search(self, *args, **kwargs):
        store = self._get_store()
        if store and store.healthy:
            return store.search(*args, **kwargs)
        return []

    def find_similar(self, *args, **kwargs):
        store = self._get_store()
        if store and store.healthy:
            return store.find_similar(*args, **kwargs)
        return None

    def rebuild(self, *args, **kwargs):
        store = self._get_store()
        if store and store.healthy:
            return store.rebuild(*args, **kwargs)

    def clear(self):
        store = self._get_store()
        if store and store.healthy and hasattr(store, "clear"):
            return store.clear()

def create_directories():
    """Create necessary directories if they don't exist."""
    for directory in (DATA_DIR, PERSONAL_DIR, RUNBOOK_DIR, UPLOAD_DIR):
        os.makedirs(directory, exist_ok=True)
        
def initialize_managers(base_dir: str, rag_manager=None) -> Dict[str, Any]:
    """
    Initialize all manager and handler instances.

    Args:
        base_dir: Base directory path
        rag_manager: RAG manager instance (optional)
    Returns:
        Dictionary containing all initialized components
    """
    # Create directories first
    create_directories()

    # Initialize core managers
    memory_manager = MemoryManager(DATA_DIR)
    skills_manager = SkillsManager(DATA_DIR)
    session_manager = SessionManager(SESSIONS_FILE)
    set_session_manager(session_manager)  # Enable Session.add_message() persistence
    upload_handler = UploadHandler(base_dir, UPLOAD_DIR)
    personal_docs_manager = PersonalDocsManager(PERSONAL_DIR, rag_manager)
    api_key_manager = APIKeyManager(DATA_DIR)
    preset_manager = PresetManager(DATA_DIR)

    # Defer memory-vector initialization until a request actually needs it.
    embedding_model = getattr(rag_manager, '_model', None) if rag_manager else None
    memory_vector = LazyMemoryVectorStore(DATA_DIR, embedding_model=embedding_model)

    # Initialize processors
    chat_processor = ChatProcessor(memory_manager, personal_docs_manager, memory_vector=memory_vector, skills_manager=skills_manager)
    research_handler = ResearchHandler()
    
    # Initialize chat handler with all dependencies
    chat_handler = ChatHandler(
        session_manager=session_manager,
        memory_manager=memory_manager,
        chat_processor=chat_processor,
        research_handler=research_handler,
        preset_manager=preset_manager,
        upload_handler=upload_handler,
    )
    
    # Initialize model discovery
    model_discovery = ModelDiscovery(DEFAULT_HOST, OPENAI_API_KEY)
    
    # Load and apply saved API keys
    saved_keys = api_key_manager.load()
    if "brave" in saved_keys:
        update_search_config(api_key=saved_keys["brave"])
        logger.info("Loaded Brave API key from saved configuration")
    
    return {
        "memory_manager": memory_manager,
        "memory_vector": memory_vector,
        "skills_manager": skills_manager,
        "session_manager": session_manager,
        "upload_handler": upload_handler,
        "personal_docs_manager": personal_docs_manager,
        "api_key_manager": api_key_manager,
        "preset_manager": preset_manager,
        "chat_processor": chat_processor,
        "research_handler": research_handler,
        "chat_handler": chat_handler,
        "model_discovery": model_discovery,
        "current_presets": preset_manager.presets,
        "PERSONAL_INDEX": personal_docs_manager.index
    }
