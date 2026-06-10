"""Configuration constants and enums for notebook operations."""

from dataclasses import dataclass
from enum import Enum


class KernelLanguage(Enum):
    """Supported kernel languages."""

    PYTHON = "python"
    R = "R"
    JULIA = "julia"
    JAVASCRIPT = "javascript"


# Language detection patterns for kernel names
# These are used as fallback heuristics when kernel spec is not available
LANGUAGE_PATTERNS = {
    "python": ["python", "py", "ipython", "ipykernel"],
    "R": ["r", "ir", "irkernel"],
    "julia": ["julia", "jl"],
    "javascript": ["javascript", "js", "node"],
}


@dataclass(frozen=True)
class ExecutionConfig:
    """Configuration for code execution."""

    default_timeout: int = 3600
    kernel_ready_timeout: int = 1200
    kernel_startup_delay: float = 1.0
    max_execution_count: int = 10000
    iopub_msg_rate_limit: float = 0.01  # seconds between message checks


@dataclass(frozen=True)
class OutputFilterConfig:
    """Configuration for output filtering."""

    max_image_size: int = 100_000  # bytes
    max_html_size: int = 50_000
    filter_mime_types: tuple[str, ...] = (
        "image/png",
        "image/jpeg",
        "image/svg+xml",
        "text/html",
    )
    filter_threshold: int = 1000  # minimum size to consider filtering


# Singleton instances
EXECUTION_CONFIG = ExecutionConfig()
OUTPUT_FILTER_CONFIG = OutputFilterConfig()
