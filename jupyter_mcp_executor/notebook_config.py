"""Configuration constants for notebook operations."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionConfig:
    """Configuration for code execution."""

    default_timeout: int = 3600
    kernel_ready_timeout: int = 1200
    kernel_startup_delay: float = 1.0
    iopub_msg_rate_limit: float = 0.01  # seconds between message checks


@dataclass(frozen=True)
class OutputFilterConfig:
    """Configuration for output filtering."""

    filter_threshold: int = 1000  # minimum size to consider filtering


# Singleton instances
EXECUTION_CONFIG = ExecutionConfig()
OUTPUT_FILTER_CONFIG = OutputFilterConfig()
