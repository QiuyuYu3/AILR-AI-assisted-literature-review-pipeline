"""ailr — AI-assisted literature review pipeline."""

from ailr.core.project import Project
from ailr.core.source import Source
from ailr.exceptions import (
    AILRError,
    ConfigError,
    DatabaseError,
    DuplicateError,
    IngestError,
    InputNotFoundError,
    LLMError,
    ModeError,
    ProjectNotFoundError,
    UnsupportedFormatError,
    ValidationError,
)
from ailr.llm import CallMetadata, LLMClient, ToolSchema, make_llm_client
from ailr.llm.mock import MockLLMClient
from ailr.extraction import (
    FieldSpec,
    UserSchema,
    build_extraction_tool_schema,
    compose_schema,
    schema_to_markdown,
)
from ailr.metrics import cohen_kappa, confusion_matrix, percent_agreement
from ailr.reviewers import (
    ExtractionResult,
    LLMReviewer,
    Reviewer,
    ScreeningDecision,
    SourceExtraction,
)
from ailr.tasks import (
    CalibrationSummary,
    CalibrationTask,
    ExtractionTask,
    ExtractRunSummary,
    PreprocessSummary,
    PreprocessTask,
    ScreeningTask,
)

__version__ = "0.18.0"

__all__ = [
    "Project",
    "Source",
    "AILRError",
    "ConfigError",
    "DatabaseError",
    "DuplicateError",
    "IngestError",
    "InputNotFoundError",
    "LLMError",
    "ModeError",
    "ProjectNotFoundError",
    "UnsupportedFormatError",
    "ValidationError",
    "CallMetadata",
    "LLMClient",
    "MockLLMClient",
    "ToolSchema",
    "make_llm_client",
    "CalibrationSummary",
    "CalibrationTask",
    "PreprocessSummary",
    "PreprocessTask",
    "ExtractRunSummary",
    "ExtractionResult",
    "ExtractionTask",
    "LLMReviewer",
    "Reviewer",
    "ScreeningDecision",
    "ScreeningTask",
    "SourceExtraction",
    "FieldSpec",
    "UserSchema",
    "build_extraction_tool_schema",
    "compose_schema",
    "schema_to_markdown",
    "cohen_kappa",
    "confusion_matrix",
    "percent_agreement",
    "__version__",
]
