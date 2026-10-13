"""Configuration constants for notebook operations."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionConfig:
    """Configuration for code execution."""

    default_timeout: int = 36000  # seconds (10 hours), for long-running cells
    kernel_ready_timeout: int = 1200
    iopub_msg_rate_limit: float = 0.01  # seconds between message checks
    iopub_poll_interval: float = 0.5  # seconds per iopub poll while collecting
    # After a timeout interrupt, wait this long for the kernel to go idle
    interrupt_grace_secs: float = 10.0


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
class ImageConfig:
    """Configuration for showing cell image outputs to the model."""

    # Raster formats vision models accept, in order of preference when one
    # output carries several (e.g. matplotlib emitting both png and jpeg).
    viewable_mime_types: tuple[str, ...] = (
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
    )
    max_images_per_call: int = 4  # view_cell_image, per cell
    # One Code Mode execute response may gather images from several calls.
    max_images_per_response: int = 8
    max_image_bytes: int = 5 * 1024 * 1024  # decoded size; Claude API limit


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
IMAGE_CONFIG = ImageConfig()
PROGRESS_CONFIG = ProgressConfig()
