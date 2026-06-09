import json
import logging
import os
import re
import time
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from core.database import Memory as DbMemory
from core.database import Session as DbSession
from core.database import SessionLocal

logger = logging.getLogger(__name__)


def tokenize(text: str) -> List[str]:
    """Simple tokenizer that splits on whitespace and removes punctuation."""
    return [word.strip('.,!?";') for word in text.split()]



def get_text_similarity(text1: str, text2: str) -> float:
    """Calculate Jaccard similarity between two texts."""
    if not text1 or not text2:
        return 0.0

    tokens1 = set(tokenize(text1.lower()))
    tokens2 = set(tokenize(text2.lower()))

    if not tokens1 and not tokens2:
        return 1.0
    if not tokens1 or not tokens2:
        return 0.0

    intersection = tokens1.intersection(tokens2)
    union = tokens1.union(tokens2)
    return len(intersection) / len(union)


class MemoryManager:
    def __init__(self, data_dir: str, db_session_factory=None):
        self.memory_file = os.path.join(data_dir, "memory.json")
        self.db_session_factory = db_session_factory or SessionLocal
        self._cache_entries: Optional[List[Dict]] = None
        self._cache_generation = 0
        self._legacy_import_attempted = False
        self.ensure_file_exists()

    def _cache_copy(self) -> List[Dict]:
        if not self._cache_entries:
            return []
        return [dict(entry) for entry in self._cache_entries]

    def _set_cache(self, entries: List[Dict]) -> None:
        self._cache_entries = [dict(entry) for entry in entries]
        self._cache_generation += 1

    def _invalidate_cache(self) -> None:
        self._cache_entries = None
        self._cache_generation += 1

    def get_cache_token(self) -> int:
        return self._cache_generation

    def _db_row_to_entry(self, row: DbMemory) -> Dict:
        return {
            "id": row.id,
            "text": row.text,
            "timestamp": int(row.timestamp or int(time.time())),
            "source": row.source or "unknown",
            "category": row.category or "fact",
            "uses": int(getattr(row, "uses", 0) or 0),
            "pinned": bool(getattr(row, "pinned", False)),
            "owner": row.owner,
            "session_id": row.session_id,
        }

    def _load_db_entries(self) -> List[Dict]:
        db = self.db_session_factory()
        try:
            rows = (
                db.query(DbMemory)
                .order_by(DbMemory.timestamp.asc(), DbMemory.id.asc())
                .all()
            )
            return [self._db_row_to_entry(row) for row in rows]
        finally:
            db.close()

    def _normalize_session_ids(self, entries: List[Dict], db) -> List[Dict]:
        session_ids = {
            entry.get("session_id") for entry in entries if entry.get("session_id")
        }
        if not session_ids:
            return entries

        valid_session_ids = {
            row_id for (row_id,) in db.query(DbSession.id).filter(DbSession.id.in_(session_ids)).all()
        }
        normalized = []
        for entry in entries:
            item = dict(entry)
            if item.get("session_id") and item["session_id"] not in valid_session_ids:
                item["session_id"] = None
            normalized.append(item)
        return normalized

    def _migrate_json_file_to_db(self) -> None:
        if self._legacy_import_attempted:
            return
        self._legacy_import_attempted = True
        if not os.path.exists(self.memory_file):
            return
        try:
            with open(self.memory_file, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Legacy memory.json import failed: %s", e)
            return

        if not isinstance(data, list) or not data:
            return

        validated = self._validate_entries(data)
        db = self.db_session_factory()
        try:
            if db.query(DbMemory).count() > 0:
                return
            validated = self._normalize_session_ids(validated, db)
            for entry in validated:
                db.add(DbMemory(
                    id=entry["id"],
                    text=entry["text"],
                    category=entry.get("category", "fact"),
                    source=entry.get("source", "user"),
                    owner=entry.get("owner"),
                    session_id=entry.get("session_id"),
                    timestamp=int(entry.get("timestamp", int(time.time()))),
                    pinned=bool(entry.get("pinned", False)),
                    uses=int(entry.get("uses", 0) or 0),
                ))
            db.commit()
            self._invalidate_cache()
            logger.info("Imported %d memory entries from memory.json into SQLite", len(validated))
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def extract_memory_from_chat(self, chat_history: List[Dict], session_id: str = None) -> List[Dict]:
        """Extract memory entries from chat history as a fallback when LLM fails."""
        memories = []

        for msg in chat_history:
            if msg.get("role") == "assistant":
                content = str(msg.get("content", ""))
                lines = content.split("\n")

                for line in lines:
                    line = line.strip()
                    if re.match(r"^[-*•]|\d+\.", line):
                        text_match = re.match(r"^(?:[-*•]|\d+\.)\s*(.*)", line)
                        if text_match:
                            text = text_match.group(1).strip()
                            if text:
                                memories.append({
                                    "text": text,
                                    "timestamp": int(datetime.now().timestamp()),
                                    "session_id": session_id,
                                })
                    elif re.search(r"memory|fact|note|remember", line, re.I):
                        pass
                    elif re.match(r"^={3,}|-{3,}|_{3,}", line):
                        pass

        return memories

    def process_inline_memory_command(self, message: str) -> Tuple[bool, str]:
        """Check if a message is an inline memory command."""
        pattern = r"^(?:remember|memorize|save|note|store)[:\-]?\s+(.+)$"
        match = re.match(pattern, message.strip(), re.IGNORECASE)
        if match:
            return True, match.group(1).strip()
        return False, ""

    def ensure_file_exists(self):
        """Keep the legacy memory file present for compatibility-only sidecars/imports."""
        if not os.path.exists(self.memory_file):
            with open(self.memory_file, "w", encoding="utf-8") as handle:
                json.dump([], handle, ensure_ascii=False, indent=2)

    def load_all(self) -> List[Dict]:
        """Load all memory entries from SQLite, importing legacy JSON once if needed."""
        if self._cache_entries is not None:
            return self._cache_copy()
        self._migrate_json_file_to_db()
        entries = self._load_db_entries()
        self._set_cache(entries)
        return self._cache_copy()

    def load(self, owner: str = None) -> List[Dict]:
        """Load memory entries, optionally filtered by owner."""
        entries = self.load_all()
        if owner is None:
            return entries
        return [entry for entry in entries if entry.get("owner") == owner]

    def _validate_entries(self, entries: List[Dict]) -> List[Dict]:
        """Ensure all entries have required fields."""
        validated = []
        for entry in entries:
            item = dict(entry)
            if "id" not in item:
                item["id"] = str(uuid.uuid4())
            if "timestamp" not in item:
                item["timestamp"] = int(time.time())
            if "source" not in item:
                item["source"] = "unknown"
            if "category" not in item:
                item["category"] = "fact"
            if "uses" not in item:
                item["uses"] = 0
            if "pinned" not in item:
                item["pinned"] = False
            validated.append(item)
        return validated

    def _migrate_from_legacy(self) -> List[Dict]:
        """Migrate from old text format to the SQLite-backed store if needed."""
        legacy_path = os.path.join(os.path.dirname(self.memory_file), "memory.txt")
        if not os.path.exists(legacy_path):
            return []

        logger.info("Converting legacy memory.txt to SQLite-backed memories")
        try:
            with open(legacy_path, "r", encoding="utf-8") as handle:
                lines = [line.strip() for line in handle.readlines() if line.strip()]

            entries = []
            for line in lines:
                entries.append({
                    "id": str(uuid.uuid4()),
                    "text": line,
                    "timestamp": int(time.time()),
                    "source": "user",
                    "category": "fact",
                    "uses": 0,
                    "pinned": False,
                })

            self.save(entries)
            return self.load_all()
        except Exception as e:
            logger.error("Failed to convert legacy memory: %s", e)
            return []

    def save(self, entries: List[Dict]):
        """Replace the memory table contents with the provided entries."""
        validated = self._validate_entries(entries)
        db = self.db_session_factory()
        try:
            validated = self._normalize_session_ids(validated, db)
            existing = {row.id: row for row in db.query(DbMemory).all()}
            incoming_ids = {entry["id"] for entry in validated}

            for row_id, row in existing.items():
                if row_id not in incoming_ids:
                    db.delete(row)

            for entry in validated:
                row = existing.get(entry["id"])
                if row is None:
                    row = DbMemory(id=entry["id"])
                    db.add(row)
                row.text = entry["text"]
                row.category = entry.get("category", "fact")
                row.source = entry.get("source", "user")
                row.owner = entry.get("owner")
                row.session_id = entry.get("session_id")
                row.timestamp = int(entry.get("timestamp", int(time.time())))
                row.pinned = bool(entry.get("pinned", False))
                row.uses = int(entry.get("uses", 0) or 0)

            db.commit()
            self._set_cache(validated)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def claim_ownerless(self, owner: str):
        """Assign ownerless memory entries to the given owner."""
        db = self.db_session_factory()
        try:
            rows = db.query(DbMemory).filter((DbMemory.owner == None) | (DbMemory.owner == "")).all()
            if not rows:
                return
            for row in rows:
                row.owner = owner
            db.commit()
            self._invalidate_cache()
            logger.info("Claimed %d ownerless memories for %s", len(rows), owner)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def add_entry(self, text: str, source: str = "user", category: str = "fact", owner: str = None) -> Dict:
        """Add a new memory entry."""
        if not text.strip():
            raise ValueError("Memory text cannot be empty")

        entry = {
            "id": str(uuid.uuid4()),
            "text": text.strip(),
            "timestamp": int(time.time()),
            "source": source,
            "category": category,
            "uses": 0,
            "pinned": False,
        }
        if owner:
            entry["owner"] = owner
        return entry

    def increment_uses(self, ids: List[str]) -> None:
        """Bump the uses counter for each memory id."""
        if not ids:
            return
        id_set = set(ids)
        db = self.db_session_factory()
        try:
            rows = db.query(DbMemory).filter(DbMemory.id.in_(id_set)).all()
            changed = False
            for row in rows:
                row.uses = int(getattr(row, "uses", 0) or 0) + 1
                changed = True
            if changed:
                db.commit()
                self._invalidate_cache()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def find_duplicates(self, text: str, entries: List[Dict] = None) -> List[Dict]:
        """Find duplicate memory entries based on text content."""
        if entries is None:
            entries = self.load()

        text_lower = text.strip().lower()
        return [entry for entry in entries if entry["text"].lower() == text_lower]

    def categorize_memory_by_relevance(self, message: str, memories: list):
        """Categorize memories by type and relevance."""
        categories = {
            "contacts": [],
            "preferences": [],
            "facts": [],
            "tasks": [],
        }

        msg_lower = message.lower()

        for mem in memories:
            text_lower = mem["text"].lower()

            if any(word in text_lower for word in ["phone", "email", "address", "lives", "works"]):
                if any(word in msg_lower for word in ["contact", "phone", "address", "email"]):
                    categories["contacts"].append(mem)
            elif any(word in text_lower for word in ["likes", "dislikes", "prefers", "favorite"]):
                if any(word in msg_lower for word in ["like", "prefer", "favorite", "want"]):
                    categories["preferences"].append(mem)
            elif any(word in text_lower for word in ["todo", "task", "remind", "meeting"]):
                if any(word in msg_lower for word in ["todo", "task", "schedule", "remind"]):
                    categories["tasks"].append(mem)
            else:
                if get_text_similarity(message, mem["text"]) > 0.4:
                    categories["facts"].append(mem)

        return categories

    def get_relevant_memories(self, query: str, memories: list, threshold: float = 0.05, max_items: int = 8):
        """Get memories that are relevant to the query based on text similarity and semantic keyword matching."""
        if not memories or not query.strip():
            return []

        identity_words = ["name", "who", "i", "am", "called", "identity", "myself", "me", "my"]
        contact_words = ["phone", "email", "address", "contact", "number", "where", "located", "reach"]
        preference_words = ["like", "prefer", "favorite", "want", "love", "hate", "dislike", "enjoy", "interested"]
        task_words = ["todo", "task", "remind", "meeting", "appointment", "schedule", "deadline"]
        fact_words = ["what", "when", "where", "how", "why", "explain", "describe", "information", "know"]

        query_lower = query.lower()

        query_type = None
        if any(word in query_lower for word in identity_words):
            query_type = "identity"
        elif any(word in query_lower for word in contact_words):
            query_type = "contact"
        elif any(word in query_lower for word in preference_words):
            query_type = "preference"
        elif any(word in query_lower for word in task_words):
            query_type = "task"
        elif any(word in query_lower for word in fact_words):
            query_type = "fact"

        relevant = []
        identity_memories = []
        other_memories = []

        for memory in memories:
            memory_text = memory["text"].lower()
            is_identity = any([
                re.search(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b", memory["text"]),
                any(word in memory_text for word in ["name is", "i'm", "i am", "called", "my name", "named", "call me"]),
            ])
            if is_identity:
                identity_memories.append(memory)
            else:
                other_memories.append(memory)

        if query_type == "identity" and identity_memories:
            for memory in identity_memories:
                relevant.append((0.9, memory))

        for memory in other_memories:
            memory_text = memory["text"].lower()
            memory_tokens = set(tokenize(memory_text))
            query_tokens = set(tokenize(query_lower))

            if not query_tokens or not memory_tokens:
                continue

            base_similarity = len(query_tokens & memory_tokens) / len(query_tokens | memory_tokens)
            final_score = base_similarity

            if query_type == "contact":
                has_contact_info = any(word in memory_text for word in ["@gmail.com", "@", ".com", "phone", "number", "address", "http", "www", "tel:"])
                if has_contact_info:
                    final_score *= 1.4
            elif query_type == "preference":
                has_preference = any(word in memory_text for word in ["like", "love", "hate", "dislike", "prefer", "favorite", "enjoy", "interested"])
                if has_preference:
                    final_score *= 1.3
            elif query_type == "task":
                has_task = any(word in memory_text for word in ["todo", "task", "remind", "meeting", "appointment", "schedule", "deadline", "need to"])
                if has_task:
                    final_score *= 1.3

            if query.lower() in memory["text"].lower():
                final_score = max(final_score, 0.8)

            if final_score >= threshold:
                relevant.append((final_score, memory))

        relevant.sort(key=lambda item: item[0], reverse=True)
        return [mem for _, mem in relevant[:max_items]]
