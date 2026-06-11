"""Configuration constants for notebook operations."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionConfig:
    """Configuration for code execution."""

    default_timeout: int = 3600
    kernel_ready_timeout: int = 1200
    iopub_msg_rate_limit: float = 0.01  # seconds between message checks
    iopub_poll_interval: float = 0.5  # seconds per iopub poll while collecting


@dataclass(frozen=True)
class OutputFilterConfig:
    """Configuration for output filtering."""

    filter_threshold: int = 1000  # minimum size to consider filtering
    # Plain-text outputs (stream/error/text-plain) larger than this many
    # characters are truncated in MCP responses (head + tail preserved).
    max_text_length: int = 4000
    truncation_head: int = 3000  # chars kept from the start when truncating
    truncation_tail: int = 500  # chars kept from the end when truncating


# Singleton instances
EXECUTION_CONFIG = ExecutionConfig()
OUTPUT_FILTER_CONFIG = OutputFilterConfig()
