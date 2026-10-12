"""Configuration constants for notebook operations."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionConfig:
    """Configuration for code execution."""

    default_timeout: int = 36000  # seconds (10 hours), for long-running cells
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


@dataclass(frozen=True)
class ProgressConfig:
    """Configuration for MCP progress notifications during execution."""

    # Send a notification at least this often while nothing else was sent,
    # so silent cells still report. Must stay well below 60s: clients that
    # reset their request timeout on progress (e.g. the MCP TS SDK, default
    # timeout 60s) would otherwise give up on a long, silent cell.
    heartbeat_interval: float = 30.0
    # Never send output-driven notifications more often than this.
    min_interval: float = 1.0
    message_max_chars: int = 120  # whole progress message, truncated with "…"


# Singleton instances
EXECUTION_CONFIG = ExecutionConfig()
OUTPUT_FILTER_CONFIG = OutputFilterConfig()
PROGRESS_CONFIG = ProgressConfig()
