"""Config models and four-tier merge.

Merge order (low -> high precedence):
    1. Built-in defaults (pydantic field defaults)
    2. Built-in mode preset (modes/strict.yaml or assisted.yaml; skipped if mode == "custom")
    3. User-supplied preset file (optional, path in project config or via CLI --preset)
    4. Project's own lit_review.yaml

Per-stage LLM override:
    The top-level `llm:` block sets defaults for every LLM call. Each stage
    (screening, extraction) may declare its own `llm:` sub-block that overrides
    individual fields; missing fields inherit from the top-level block.
"""

from importlib.resources import files
from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError as PydanticValidationError

from ailr.exceptions import ConfigError, InputNotFoundError, ProjectNotFoundError


class ProjectMeta(BaseModel):
    name: str
    type: Literal["scoping", "systematic", "methodological_scoping"] = "scoping"
    description: str = ""
    mode: Literal["strict", "assisted", "custom"] = "assisted"
    mode_preset: Optional[str] = None


class LLMConfig(BaseModel):
    provider: Literal["anthropic", "openai", "gemini"] = "anthropic"
    model: str = "claude-sonnet-4-6"
    temperature: float = 0.0
    seed: Optional[int] = 42
    max_retries: int = 3


class StageLLMOverride(BaseModel):
    provider: Optional[Literal["anthropic", "openai", "gemini"]] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    seed: Optional[int] = None
    max_retries: Optional[int] = None


class CalibrationConfig(BaseModel):
    fraction: float = 0.10
    n: Optional[int] = None
    min: int = 30


class ScreeningConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    prompt: str = "prompts/screening.txt"
    criteria: str = "inclusion_criteria.md"
    batch_size: int = 20
    workflow: Literal["assisted", "independent"] = Field(
        default="assisted",
        validation_alias=AliasChoices("workflow", "blinding"),
        description="assisted = AI + 1 human, both blinded. independent = 2 humans, AI optional reference.",
    )
    target_kappa: float = 0.7
    calibration: CalibrationConfig = Field(default_factory=CalibrationConfig)
    llm: Optional[StageLLMOverride] = None


class ExtractionConfig(BaseModel):
    model_config = ConfigDict(protected_namespaces=(), populate_by_name=True)
    prompt: str = "prompts/extraction.txt"
    schema_path: str = "schema.yaml"
    codebook: Optional[str] = "codebook.yaml"
    workflow: Literal["verify", "independent"] = Field(
        default="verify",
        validation_alias=AliasChoices("workflow", "blinding"),
        description="verify = AI extracts, human verifies/edits. independent = human extracts blind, AI hidden until submit.",
    )
    chunk_strategy: Literal["full", "methods_only", "custom"] = "full"
    output_format: Literal["with_quotes", "value_only"] = "with_quotes"
    flag_check: bool = True
    target_kappa: float = 0.7
    calibration: CalibrationConfig = Field(
        default_factory=lambda: CalibrationConfig(min=10)
    )
    llm: Optional[StageLLMOverride] = None


class PreprocessConfig(BaseModel):
    pdf_backend: Literal["pymupdf", "marker", "grobid"] = "pymupdf"
    strip_references: bool = True
    keep_sections: list[str] = Field(default_factory=list)


class StorageConfig(BaseModel):
    database: str = "data/review.sqlite"
    # Optional SQLAlchemy URL for a shared DB (e.g. "postgresql+psycopg://user:pw@host/db").
    # When set it takes precedence over `database` (the local SQLite file path).
    database_url: Optional[str] = None


class LoggingConfig(BaseModel):
    level: str = "INFO"
    audit_log: str = "data/audit.jsonl"


class Config(BaseModel):
    project: ProjectMeta
    llm: LLMConfig = Field(default_factory=LLMConfig)
    screening: ScreeningConfig = Field(default_factory=ScreeningConfig)
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)
    preprocess: PreprocessConfig = Field(default_factory=PreprocessConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


def load_config(project_dir: Path) -> Config:
    config_path = project_dir / "lit_review.yaml"
    if not config_path.exists():
        raise ProjectNotFoundError(f"lit_review.yaml not found in {project_dir}")

    try:
        with open(config_path, encoding="utf-8") as f:
            user_config = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"Failed to parse {config_path}: {e}") from e

    project_meta = user_config.get("project", {}) or {}
    mode = project_meta.get("mode", "assisted")
    custom_preset_rel = project_meta.get("mode_preset")

    merged: dict[str, Any] = {}

    if mode in ("strict", "assisted"):
        merged = merge_preset_into(merged, load_builtin_preset(mode))

    if custom_preset_rel:
        preset_path = Path(custom_preset_rel)
        if not preset_path.is_absolute():
            preset_path = project_dir / preset_path
        merged = merge_preset_into(merged, load_custom_preset(preset_path))

    merged = merge_preset_into(merged, user_config)

    try:
        return Config(**merged)
    except PydanticValidationError as e:
        raise ConfigError(f"Invalid config in {config_path}:\n{e}") from e


def load_builtin_preset(mode: Literal["strict", "assisted"]) -> dict[str, Any]:
    preset_text = (files("ailr.modes") / f"{mode}.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(preset_text) or {}


def load_custom_preset(preset_path: Path) -> dict[str, Any]:
    if not preset_path.exists():
        raise InputNotFoundError(f"Preset file not found: {preset_path}")
    try:
        with open(preset_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"Failed to parse preset {preset_path}: {e}") from e


def merge_preset_into(base: dict[str, Any], preset: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in preset.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_preset_into(result[key], value)
        else:
            result[key] = value
    return result


def resolve_stage_llm(top_level: LLMConfig, override: Optional[StageLLMOverride]) -> LLMConfig:
    if override is None:
        return top_level
    fields = top_level.model_dump()
    fields.update(override.model_dump(exclude_none=True))
    return LLMConfig(**fields)


def save_llm_config(
    project_dir: Path,
    provider: str,
    model: str,
    temperature: float,
    seed: Optional[int] = None,
) -> None:
    """Update the top-level `llm:` block in lit_review.yaml (used by AI screening/extraction)."""
    config_path = project_dir / "lit_review.yaml"
    if not config_path.exists():
        raise ProjectNotFoundError(f"lit_review.yaml not found in {project_dir}")
    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"Failed to parse {config_path}: {e}") from e

    llm = data.setdefault("llm", {})
    if not isinstance(llm, dict):
        raise ConfigError(f"Expected dict at llm: in {config_path}, got {type(llm).__name__}")
    llm["provider"] = provider
    llm["model"] = model
    llm["temperature"] = temperature
    if seed is not None:
        llm["seed"] = seed

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)


def save_stage_llm_config(
    project_dir: Path,
    stage: Literal["screening", "extraction"],
    provider: Optional[str],
    model: Optional[str],
) -> None:
    """Set or clear a stage's `llm:` override (screening.llm / extraction.llm).
    Blank model clears the override so the stage inherits the top-level `llm:`.
    temperature/seed/max_retries always inherit from top-level."""
    config_path = project_dir / "lit_review.yaml"
    if not config_path.exists():
        raise ProjectNotFoundError(f"lit_review.yaml not found in {project_dir}")
    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"Failed to parse {config_path}: {e}") from e

    stage_block = data.setdefault(stage, {})
    if not isinstance(stage_block, dict):
        raise ConfigError(f"Expected dict at {stage}: in {config_path}, got {type(stage_block).__name__}")

    if not model or not str(model).strip():
        stage_block.pop("llm", None)
    else:
        override: dict = {"model": str(model).strip()}
        if provider:
            override["provider"] = provider
        stage_block["llm"] = override

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)


def save_stage_workflow(project_dir: Path, stage: Literal["screening", "extraction"], workflow: str) -> None:
    """Update screening.workflow or extraction.workflow in the project's lit_review.yaml.

    Note: pyyaml's safe_dump rewrites the file and does not preserve comments.
    """
    config_path = project_dir / "lit_review.yaml"
    if not config_path.exists():
        raise ProjectNotFoundError(f"lit_review.yaml not found in {project_dir}")

    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"Failed to parse {config_path}: {e}") from e

    stage_block = data.setdefault(stage, {})
    if not isinstance(stage_block, dict):
        raise ConfigError(f"Expected dict at {stage}: in {config_path}, got {type(stage_block).__name__}")
    stage_block.pop("blinding", None)
    stage_block["workflow"] = workflow

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
