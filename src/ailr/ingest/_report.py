"""Shared validation report for the import-with-preview (dry-run) flow."""

from dataclasses import dataclass, field

from pydantic import ValidationError


@dataclass
class ValidationItem:
    level: str  # "error" | "warning"
    message: str
    field: str | None = None


@dataclass
class ValidationReport:
    ok_count: int = 0
    items: list[ValidationItem] = field(default_factory=list)

    def add(self, level: str, message: str, field: str | None = None) -> None:
        self.items.append(ValidationItem(level=level, message=message, field=field))

    @property
    def errors(self) -> list[ValidationItem]:
        return [i for i in self.items if i.level == "error"]

    @property
    def warnings(self) -> list[ValidationItem]:
        return [i for i in self.items if i.level == "warning"]

    @property
    def has_errors(self) -> bool:
        return any(i.level == "error" for i in self.items)


def _short_error(e: ValidationError) -> str:
    errs = e.errors()
    if not errs:
        return "invalid field"
    first = errs[0]
    loc = ".".join(str(x) for x in first.get("loc", ()))
    msg = first.get("msg", "invalid")
    return f"{loc}: {msg}" if loc else msg
