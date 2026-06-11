"""Shared validation report for the import-with-preview (dry-run) flow."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ValidationItem:
    level: str  # "error" | "warning"
    message: str
    field: Optional[str] = None


@dataclass
class ValidationReport:
    ok_count: int = 0
    items: list[ValidationItem] = field(default_factory=list)

    def add(self, level: str, message: str, field: Optional[str] = None) -> None:
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
