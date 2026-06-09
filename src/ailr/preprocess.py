"""PDF -> Markdown converters. Pluggable backend; PyMuPDF default."""

import re
import subprocess
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path

from ailr.exceptions import AILRError, ConfigError


class PDFConverter(ABC):
    @abstractmethod
    def convert(self, pdf_path: Path) -> str:
        ...

    @property
    @abstractmethod
    def backend_name(self) -> str:
        ...


class PyMuPDFConverter(PDFConverter):
    """Default backend. Uses pymupdf4llm if installed (better markdown), falls back to raw pymupdf."""

    @property
    def backend_name(self) -> str:
        return "pymupdf"

    def convert(self, pdf_path: Path) -> str:
        try:
            import pymupdf4llm
            return pymupdf4llm.to_markdown(str(pdf_path), show_progress=False)
        except ImportError:
            pass
        try:
            import pymupdf
        except ImportError as e:
            raise AILRError(
                "pymupdf not installed. Run: pip install ailr[pdf]"
            ) from e
        doc = pymupdf.open(str(pdf_path))
        try:
            return "\n\n".join(page.get_text() for page in doc)
        finally:
            doc.close()


class MarkerConverter(PDFConverter):
    """marker_single subprocess wrapper. Requires marker CLI installed and on PATH."""

    @property
    def backend_name(self) -> str:
        return "marker"

    def convert(self, pdf_path: Path) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                result = subprocess.run(
                    ["marker_single", str(pdf_path), "--output_dir", tmp],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except FileNotFoundError as e:
                raise AILRError(
                    "marker_single not found on PATH. Install marker or switch preprocess.pdf_backend to pymupdf."
                ) from e
            if result.returncode != 0:
                raise AILRError(f"marker_single failed: {result.stderr.strip()[:500]}")
            md_files = list(Path(tmp).rglob("*.md"))
            if not md_files:
                raise AILRError("marker_single produced no .md output.")
            return md_files[0].read_text(encoding="utf-8")


def make_converter(backend: str) -> PDFConverter:
    if backend == "pymupdf":
        return PyMuPDFConverter()
    if backend == "marker":
        return MarkerConverter()
    if backend == "grobid":
        raise ConfigError("Grobid backend not yet implemented.")
    raise ConfigError(f"Unknown PDF backend: {backend!r}. Supported: pymupdf, marker.")


_REFERENCES_PATTERNS = [
    r"(?im)^#{1,6}\s*(references|bibliography|literature\s+cited|works\s+cited)\s*$",
    r"(?im)^(references|bibliography|literature\s+cited|works\s+cited)\s*$",
]


def strip_references(md_text: str) -> str:
    """Cut everything from the first References/Bibliography heading onward."""
    earliest = len(md_text)
    for pattern in _REFERENCES_PATTERNS:
        for match in re.finditer(pattern, md_text):
            if match.start() < earliest:
                earliest = match.start()
            break
    if earliest < len(md_text):
        return md_text[:earliest].rstrip()
    return md_text
