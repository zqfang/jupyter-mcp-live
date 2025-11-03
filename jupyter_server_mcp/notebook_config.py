"""Configuration constants and enums for notebook operations."""

from dataclasses import dataclass
from enum import Enum
from typing import Literal


class KernelLanguage(Enum):
    """Supported kernel languages."""
    PYTHON = "python"
    R = "R"
    JULIA = "julia"
    JAVASCRIPT = "javascript"


# Kernel name aliases for common variations
KERNEL_ALIASES = {
    "ir": KernelLanguage.R,
    "r": KernelLanguage.R,
    "irkernel": KernelLanguage.R,
    "python3": KernelLanguage.PYTHON,
    "python3.11": KernelLanguage.PYTHON,
    "python3.12": KernelLanguage.PYTHON,
    "julia-1.9": KernelLanguage.JULIA,
    "julia-1.10": KernelLanguage.JULIA,
}


@dataclass(frozen=True)
class ExecutionConfig:
    """Configuration for code execution."""
    default_timeout: int = 30
    kernel_ready_timeout: int = 10
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
        "text/html"
    )
    filter_threshold: int = 1000  # minimum size to consider filtering


# Type aliases for operation types
QueryType = Literal[
    "view_source",
    "check_server",
    "list_sessions",
    "list_kernels",
    "get_position_index"
]

ExecutionType = Literal["execute_cell", "install_packages"]

CellOperation = Literal[
    "add_code",
    "edit_code",
    "add_markdown",
    "edit_markdown",
    "delete"
]


# Singleton instances
EXECUTION_CONFIG = ExecutionConfig()
OUTPUT_FILTER_CONFIG = OutputFilterConfig()
