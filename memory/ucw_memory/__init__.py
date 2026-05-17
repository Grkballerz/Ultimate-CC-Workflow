"""UCW memory subsystem — durable, queryable cross-session facts."""

from .db import MemoryDB, init_db
from .distill import extract_from_transcript, write_candidates_to_db
from .privacy import strip_private
from .retrieval import recall

__all__ = [
    "MemoryDB", "init_db", "recall",
    "extract_from_transcript", "write_candidates_to_db",
    "strip_private",
]
__version__ = "0.1.0"
