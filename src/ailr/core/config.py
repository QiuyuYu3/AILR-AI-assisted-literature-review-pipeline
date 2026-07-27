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
    type: Literal["scoping", "systematic"] = "scoping"
    description: str = ""
    mode: Literal["strict", "assisted", "custom"] = "assisted"
    mode_preset: Optional[str] = None
    # PRISMA 2020 item 24: the register the review is filed with, its number, and where the
    # protocol can be read. Blank is a valid answer and reports as "not registered".
    registry: str = ""
    registration_number: str = ""
    protocol_url: str = ""


class LLMConfig(BaseModel):
    provider: Literal["anthropic", "openai", "gemini"] = "anthropic"
    # No default: model names date fast, and a stale one shipped as a default is worse than
    # being asked to pick. Set it per stage in Settings -> Models.
    model: Optional[str] = None
    temperature: float = 0.0
    # No default: only some provider APIs take a seed (see llm/base._SEED_PROVIDERS). Defaulting it
    # made every project's config advertise a reproducibility control the call never sent.
    seed: Optional[int] = None
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
    additional: str = "prompts/screening_additional.txt"
    criteria_structured: str = "criteria.yaml"  # the criteria the review runs on — shared by both stages
    batch_size: int = 20
    workflow: Literal["assisted", "independent"] = Field(
        default="assisted",
        validation_alias=AliasChoices("workflow", "blinding"),
        description="assisted = AI + 1 human, both blinded. independent = 2 humans, AI optional reference.",
    )
    # Full-text screening runs its own workflow: the common design is AI-assisted at title/abstract
    # (thousands of records) and two humans at full text (dozens). None = same as `workflow`.
    full_text_workflow: Optional[Literal["assisted", "independent"]] = None
    target_kappa: float = 0.7
    calibration: CalibrationConfig = Field(default_factory=CalibrationConfig)
    llm: Optional[StageLLMOverride] = None
    workers: int = 4  # concurrent LLM screening calls (1 = serial)
    flag_check: bool = True  # per-criterion verdicts on every AI screening decision (auditable); escape hatch to disable


class ExtractionConfig(BaseModel):
    model_config = ConfigDict(protected_namespaces=(), populate_by_name=True)
    prompt: str = "prompts/extraction.txt"
    additional: str = "prompts/extraction_additional.txt"
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
    workers: int = 2  # concurrent LLM extraction calls (1 = serial; full-paper prompts are large)


class PreprocessConfig(BaseModel):
    pdf_backend: Literal["pymupdf", "marker", "grobid"] = "pymupdf"
    strip_references: bool = True
    keep_sections: list[str] = Field(default_factory=list)
    low_text_threshold: int = 2000  # converted markdown shorter than this ~ likely scanned/failed PDF
    workers: int = 4  # parallel PDF->markdown conversions (pymupdf only; marker forced to 1)


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

    def screening_workflow(self, stage: str = "abstract") -> str:
        """The screening workflow governing a stage. THE resolver for the whole codebase — the
        queues, conflict rules, dashboard, and methods export all go through it rather than
        reading `screening.workflow` directly, which would silently ignore the full-text override."""
        if stage == "full_text":
            return self.screening.full_text_workflow or self.screening.workflow
        return self.screening.workflow


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


def team_size_for(workflow: str) -> int:
    """Human reviewers a workflow calls for: 2 in `independent` (dual-blind), 1 in `assisted`
    (one human plus the AI as the blinded second opinion). A stage is only finished for a paper
    once this many humans have voted on it."""
    return 2 if workflow == "independent" else 1


def extractors_for(workflow: str) -> int:
    """Human extractors an extraction workflow calls for: 2 in `independent` (both extract blind,
    then reconcile), 1 in `verify` (one human checks the AI's fields)."""
    return 2 if workflow == "independent" else 1


def resolve_stage_llm(top_level: LLMConfig, override: Optional[StageLLMOverride]) -> LLMConfig:
    if override is None:
        return top_level
    fields = top_level.model_dump()
    fields.update(override.model_dump(exclude_none=True))
    return LLMConfig(**fields)


def _edit_config_block(project_dir: Path, key: str, mutate) -> None:
    """Load lit_review.yaml, apply `mutate(block)` to the dict at `key`, write it back.

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

    block = data.setdefault(key, {})
    if not isinstance(block, dict):
        raise ConfigError(f"Expected dict at {key}: in {config_path}, got {type(block).__name__}")
    mutate(block)

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)


def save_llm_config(
    project_dir: Path,
    provider: str,
    model: str,
    temperature: float,
    seed: Optional[int] = None,
) -> None:
    """Update the top-level `llm:` block in lit_review.yaml (used by AI screening/extraction)."""
    def mutate(llm: dict) -> None:
        llm["provider"] = provider
        llm["model"] = model
        llm["temperature"] = temperature
        if seed is not None:
            llm["seed"] = seed

    _edit_config_block(project_dir, "llm", mutate)


def save_stage_llm_config(
    project_dir: Path,
    stage: Literal["screening", "extraction"],
    provider: Optional[str],
    model: Optional[str],
    temperature: Optional[float] = None,
) -> None:
    """Set or clear a stage's `llm:` override (screening.llm / extraction.llm).
    Blank model clears the override so the stage inherits the top-level `llm:`.
    seed/max_retries always inherit from top-level."""
    def mutate(stage_block: dict) -> None:
        if not model or not str(model).strip():
            stage_block.pop("llm", None)
        else:
            override: dict = {"model": str(model).strip()}
            if provider:
                override["provider"] = provider
            if temperature is not None:
                override["temperature"] = float(temperature)
            stage_block["llm"] = override

    _edit_config_block(project_dir, stage, mutate)


def save_project_type(project_dir: Path, project_type: str) -> None:
    """Update `project.type` (scoping / systematic). It is what the methods and PRISMA exports
    call the review, so it has to be settable after `init`."""
    if project_type not in ("scoping", "systematic"):
        raise ConfigError(f"Unknown review type: {project_type}")
    _edit_config_block(project_dir, "project", lambda block: block.update({"type": project_type}))


def save_registration(project_dir: Path, registry: str, registration_number: str, protocol_url: str) -> None:
    """Update the PRISMA item 24 fields. Blanks are kept as blanks, not dropped, so the methods
    export can say "not registered" rather than leaving the reader to guess."""
    _edit_config_block(project_dir, "project", lambda block: block.update({
        "registry": (registry or "").strip(),
        "registration_number": (registration_number or "").strip(),
        "protocol_url": (protocol_url or "").strip(),
    }))


def save_stage_workflow(
    project_dir: Path,
    stage: Literal["screening", "full_text_screening", "extraction"],
    workflow: str,
) -> None:
    """Update a stage's workflow in the project's lit_review.yaml. `full_text_screening` writes
    screening.full_text_workflow; the other two write their own block's `workflow`."""
    if stage == "full_text_screening":
        _edit_config_block(project_dir, "screening", lambda block: block.update({"full_text_workflow": workflow}))
        return

    def mutate(stage_block: dict) -> None:
        stage_block.pop("blinding", None)
        stage_block["workflow"] = workflow

    _edit_config_block(project_dir, stage, mutate)


def _save_preprocess_fields(project_dir: Path, fields: dict) -> None:
    _edit_config_block(project_dir, "preprocess", lambda block: block.update(fields))


def save_preprocess_threshold(project_dir: Path, low_text_threshold: int) -> None:
    _save_preprocess_fields(project_dir, {"low_text_threshold": low_text_threshold})


def save_preprocess_workers(project_dir: Path, workers: int) -> None:
    _save_preprocess_fields(project_dir, {"workers": workers})


def save_preprocess_backend(project_dir: Path, pdf_backend: str) -> None:
    _save_preprocess_fields(project_dir, {"pdf_backend": pdf_backend})
