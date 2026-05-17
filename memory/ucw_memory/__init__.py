"""UCW memory subsystem — durable, queryable cross-session facts."""

from .db import MemoryDB, init_db
from .retrieval import recall

__all__ = ["MemoryDB", "init_db", "recall"]
__version__ = "0.1.0"
