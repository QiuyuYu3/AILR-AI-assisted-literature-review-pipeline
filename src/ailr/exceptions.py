"""Custom exception hierarchy for ailr."""


class AILRError(Exception):
    """Base class for all ailr runtime errors."""


class ProjectNotFoundError(AILRError):
    """Raised when a project directory does not exist or lacks lit_review.yaml."""


class ConfigError(AILRError):
    """Raised when configuration is malformed or fails validation."""


class ValidationError(AILRError):
    """Raised when user-provided content fails schema validation."""


class InputNotFoundError(AILRError):
    """Raised when an input file (RIS, BibTeX, PDF) cannot be found."""


class IngestError(AILRError):
    """Raised when parsing or importing source records fails."""


class DuplicateError(AILRError):
    """Raised when a duplicate source is detected during ingest."""


class UnsupportedFormatError(AILRError):
    """Raised when a file extension or format is not supported by the ingest layer."""


class DatabaseError(AILRError):
    """Raised on SQLite schema or query failures."""


class ModeError(AILRError):
    """Raised when a mode preset is missing, malformed, or references an unknown name."""


class LLMError(AILRError):
    """Raised on LLM API errors (auth, malformed response, exhausted retries)."""
