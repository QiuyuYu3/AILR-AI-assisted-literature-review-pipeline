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
from typing import Any, Literal

import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from ailr.exceptions import ConfigError, InputNotFoundError, ProjectNotFoundError


class ProjectMeta(BaseModel):
    name: str
    type: Literal["scoping", "systematic"] = "scoping"
    description: str = ""
    mode: Literal["strict", "assisted", "custom"] = "assisted"
    mode_preset: str | None = None
    # PRISMA 2020 item 24: the register the review is filed with, its number, and where the
    # protocol can be read. Blank is a valid answer and reports as "not registered".
    registry: str = ""
    registration_number: str = ""
    protocol_url: str = ""


class LLMConfig(BaseModel):
    provider: Literal["anthropic", "openai", "gemini"] = "anthropic"
    # No default: model names date fast, and a stale one shipped as a default is worse than
    # being asked to pick. Set it per stage in Settings -> Models.
    model: str | None = None
    temperature: float = 0.0
    # No default: only some provider APIs take a seed (see llm/base._SEED_PROVIDERS). Defaulting it
    # made every project's config advertise a reproducibility control the call never sent.
    seed: int | None = None
    max_retries: int = 3


class StageLLMOverride(BaseModel):
    provider: Literal["anthropic", "openai", "gemini"] | None = None
    model: str | None = None
    temperature: float | None = None
    seed: int | None = None
    max_retries: int | None = None


class CalibrationConfig(BaseModel):
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
    full_text_workflow: Literal["assisted", "independent"] | None = None
    calibration: CalibrationConfig = Field(default_factory=CalibrationConfig)
    llm: StageLLMOverride | None = None
    workers: int = 4  # concurrent LLM screening calls (1 = serial)
    flag_check: bool = True  # per-criterion verdicts on every AI screening decision (auditable); escape hatch to disable


class ExtractionConfig(BaseModel):
    model_config = ConfigDict(protected_namespaces=(), populate_by_name=True)
    prompt: str = "prompts/extraction.txt"
    additional: str = "prompts/extraction_additional.txt"
    schema_path: str = "schema.yaml"
    codebook: str | None = "codebook.yaml"
    workflow: Literal["verify", "independent"] = Field(
        default="verify",
        validation_alias=AliasChoices("workflow", "blinding"),
        description="verify = AI extracts, human verifies/edits. independent = human extracts blind, AI hidden until submit.",
    )
    output_format: Literal["with_quotes", "value_only"] = "with_quotes"
    flag_check: bool = True
    calibration: CalibrationConfig = Field(
        default_factory=lambda: CalibrationConfig(min=10)
    )
    llm: StageLLMOverride | None = None
    workers: int = 2  # concurrent LLM extraction calls (1 = serial; full-paper prompts are large)


class CrossCheckConfig(BaseModel):
    """Post-hoc verification of records that already exist. Orthogonal to the stage workflows:
    any of them can turn it on, and it never feeds conflict resolution or agreement statistics."""
    # Which side's records get checked. The deterministic layer is free and runs on demand, so
    # there is no separate on/off for it — not pressing the button is the off switch.
    targets: list[Literal["ai", "human"]] = Field(default_factory=lambda: ["ai"])
    llm_enabled: bool = False
    prompt: str = "prompts/crosscheck.txt"  # falls back to the built-in prompt when absent
    additional: str = "prompts/crosscheck_additional.txt"
    # The screening layer judges a decision against an abstract, not a value against a paper, so it
    # gets its own prompt rather than a stage marker inside the extraction one.
    screening_prompt: str = "prompts/crosscheck_screening.txt"
    screening_additional: str = "prompts/crosscheck_screening_additional.txt"
    llm: StageLLMOverride | None = None
    # A checker on the same model as the extractor agrees with itself far more often than an
    # independent one would, which makes the agreement rate it produces unreportable.
    allow_same_model: bool = False


class PreprocessConfig(BaseModel):
    pdf_backend: Literal["pymupdf", "marker", "grobid"] = "pymupdf"
    strip_references: bool = True
    low_text_threshold: int = 2000  # converted markdown shorter than this ~ likely scanned/failed PDF
    workers: int = 4  # parallel PDF->markdown conversions (pymupdf only; marker forced to 1)


class StorageConfig(BaseModel):
    database: str = "data/review.sqlite"
    # Optional SQLAlchemy URL for a shared DB (e.g. "postgresql+psycopg://user:pw@host/db").
    # When set it takes precedence over `database` (the local SQLite file path).
    database_url: str | None = None


class LoggingConfig(BaseModel):
    level: str = "INFO"
    audit_log: str = "data/audit.jsonl"


class Config(BaseModel):
    project: ProjectMeta
    llm: LLMConfig = Field(default_factory=LLMConfig)
    screening: ScreeningConfig = Field(default_factory=ScreeningConfig)
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)
    crosscheck: CrossCheckConfig = Field(default_factory=CrossCheckConfig)
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


def ai_votes_for(workflow: str) -> bool:
    """Whether a stage waits for the AI's vote as well as the humans' before a paper is settled."""
    return workflow == "assisted"


def extractors_for(workflow: str) -> int:
    """Human extractors an extraction workflow calls for: 2 in `independent` (both extract blind,
    then reconcile), 1 in `verify` (one human checks the AI's fields)."""
    return 2 if workflow == "independent" else 1


def resolve_stage_llm(top_level: LLMConfig, override: StageLLMOverride | None) -> LLMConfig:
    if override is None:
        return top_level
    fields = top_level.model_dump()
    fields.update(override.model_dump(exclude_none=True))
    return LLMConfig(**fields)


def crosscheck_llm_blocked(config: "Config", stage: str = "extraction") -> str | None:
    """Why the LLM cross-check must not run, or None when it may. A checker sharing the model it
    checks is the failure mode worth refusing by default, not just warning about."""
    cc = config.crosscheck
    if not cc.llm_enabled:
        return "LLM cross-check is disabled (crosscheck.llm_enabled)."
    checker = resolve_stage_llm(config.llm, cc.llm)
    if not checker.model:
        return "No cross-check model configured (crosscheck.llm.model)."
    if cc.allow_same_model:
        return None
    stage_override = config.screening.llm if stage != "extraction" else config.extraction.llm
    target = resolve_stage_llm(config.llm, stage_override)
    if (checker.provider, checker.model) == (target.provider, target.model):
        return (
            f"Cross-check model is the same as the {stage} model "
            f"({checker.provider}:{checker.model}). Pick a different model, or set "
            f"crosscheck.allow_same_model to override."
        )
    return None


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


def save_crosscheck_config(
    project_dir: Path,
    targets: list[str],
    llm_enabled: bool,
    provider: str | None = None,
    model: str | None = None,
    allow_same_model: bool = False,
) -> None:
    """Update the `crosscheck` block in lit_review.yaml."""
    def mutate(block: dict) -> None:
        block["targets"] = [t for t in targets if t in ("ai", "human")]
        block["llm_enabled"] = bool(llm_enabled)
        block["allow_same_model"] = bool(allow_same_model)
        llm = block.get("llm")
        if not isinstance(llm, dict):
            llm = {}
        if provider:
            llm["provider"] = provider
        llm["model"] = (model or "").strip() or None
        block["llm"] = llm

    _edit_config_block(project_dir, "crosscheck", mutate)


def save_stage_llm_config(
    project_dir: Path,
    stage: Literal["screening", "extraction"],
    provider: str | None,
    model: str | None,
    temperature: float | None = None,
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
