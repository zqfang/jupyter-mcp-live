"""Output filtering for MCP responses to reduce token usage."""

import logging
from typing import Any, ClassVar

from jupyter_mcp_executor.notebook_config import OUTPUT_FILTER_CONFIG

logger = logging.getLogger(__name__)


def _as_text(value: Any) -> str:
    """Normalize an output text field (str or list of str) to a string."""
    if isinstance(value, list):
        return "".join(value)
    return value if isinstance(value, str) else str(value)


def _truncate_text(text: str) -> str:
    """Truncate oversized text, preserving the head and tail.

    The head usually carries the meaningful content and the tail carries the
    final state (last log lines, the actual exception at the end of a
    traceback), so both are kept.
    """
    max_len = OUTPUT_FILTER_CONFIG.max_text_length
    if len(text) <= max_len:
        return text

    head = OUTPUT_FILTER_CONFIG.truncation_head
    tail = OUTPUT_FILTER_CONFIG.truncation_tail
    omitted = len(text) - head - tail
    return (
        f"{text[:head]}\n"
        f"... [output truncated: {omitted} characters omitted, "
        f"full output available in notebook] ...\n"
        f"{text[-tail:]}"
    )


class OutputFilter:
    """Filter notebook outputs to reduce token count in MCP responses.

    - Large binary/markup data (images, HTML) is replaced by a placeholder.
    - Large plain text (stream output, tracebacks, text/plain results) is
      truncated head+tail.

    Filtering never mutates the input dicts — the same output objects are
    also stored in the notebook, which must keep the full data.
    """

    # MIME types that should be considered for placeholder filtering
    FILTERABLE_TYPES: ClassVar[set[str]] = {
        "image/png",
        "image/jpeg",
        "image/svg+xml",
        "text/html",
    }

    @staticmethod
    def should_filter(mime_types: list[str], size_estimate: int = 0) -> bool:
        """Determine if output should be filtered based on mime types and size.

        Args:
            mime_types: List of MIME types in the output
            size_estimate: Estimated size in bytes

        Returns:
            True if output should be filtered
        """
        # Only filter if size is above threshold
        if size_estimate < OUTPUT_FILTER_CONFIG.filter_threshold:
            return False

        # Check if any MIME type should be filtered
        return any(mt in OutputFilter.FILTERABLE_TYPES for mt in mime_types)

    @staticmethod
    def create_placeholder(mime_types: list[str]) -> dict[str, Any]:
        """Create a placeholder for filtered output.

        Args:
            mime_types: Original MIME types that were filtered

        Returns:
            Placeholder output dict
        """
        return {
            "output_type": "execute_result",
            "data": {
                "text/plain": f"[Filtered {', '.join(mime_types)} - available in notebook]"
            },
            "metadata": {},
            "execution_count": None,
        }

    @staticmethod
    def _filter_data_output(output: dict[str, Any]) -> dict[str, Any]:
        """Filter an execute_result/display_data output."""
        data = output.get("data", {})
        if not data:
            return output

        mime_types = list(data.keys())

        # Estimate size without copying large payloads
        size_estimate = sum(
            len(v) if isinstance(v, str) else len(str(v)) for v in data.values()
        )

        if OutputFilter.should_filter(mime_types, size_estimate):
            logger.debug(
                f"Filtering output with types {mime_types} (size ~{size_estimate} bytes)"
            )
            placeholder = OutputFilter.create_placeholder(mime_types)

            # Preserve execution_count if present
            if "execution_count" in output:
                placeholder["execution_count"] = output["execution_count"]

            return placeholder

        # Not placeholder-filtered: still truncate an oversized text/plain repr
        # (e.g., printing a huge DataFrame)
        text_plain = data.get("text/plain")
        if text_plain is not None:
            text = _as_text(text_plain)
            if len(text) > OUTPUT_FILTER_CONFIG.max_text_length:
                filtered = dict(output)
                filtered["data"] = {**data, "text/plain": _truncate_text(text)}
                return filtered

        return output

    @staticmethod
    def _filter_stream_output(output: dict[str, Any]) -> dict[str, Any]:
        """Truncate an oversized stream output (stdout/stderr)."""
        text = _as_text(output.get("text", ""))
        if len(text) <= OUTPUT_FILTER_CONFIG.max_text_length:
            return output

        filtered = dict(output)
        filtered["text"] = _truncate_text(text)
        return filtered

    @staticmethod
    def _filter_error_output(output: dict[str, Any]) -> dict[str, Any]:
        """Truncate an oversized error traceback."""
        traceback = output.get("traceback", [])
        text = "\n".join(_as_text(line) for line in traceback)
        if len(text) <= OUTPUT_FILTER_CONFIG.max_text_length:
            return output

        filtered = dict(output)
        filtered["traceback"] = _truncate_text(text).split("\n")
        return filtered

    @staticmethod
    def filter_single_output(output: dict[str, Any]) -> dict[str, Any]:
        """Filter a single output if it contains large data.

        Args:
            output: Output dict to potentially filter

        Returns:
            Original output, or a filtered copy (input is never mutated)
        """
        output_type = output.get("output_type")

        if output_type in ("execute_result", "display_data"):
            return OutputFilter._filter_data_output(output)

        if output_type == "stream":
            return OutputFilter._filter_stream_output(output)

        if output_type == "error":
            return OutputFilter._filter_error_output(output)

        return output

    @staticmethod
    def filter_outputs(outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter a list of outputs.

        At most ``OUTPUT_FILTER_CONFIG.max_outputs`` are kept (the first and
        the last ones, with a marker for those omitted), so a cell with
        thousands of outputs (a display() per loop iteration) cannot flood
        the response.

        Args:
            outputs: List of output dicts

        Returns:
            List with filtered outputs
        """
        if not outputs:
            return outputs

        return [
            OutputFilter.filter_single_output(out)
            for out in OutputFilter._cap_count(outputs)
        ]

    @staticmethod
    def _cap_count(outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep the first and last outputs of a long list, marking the gap."""
        limit = OUTPUT_FILTER_CONFIG.max_outputs
        if len(outputs) <= limit:
            return outputs
        head = limit // 2
        tail = limit - head
        omitted = len(outputs) - limit
        marker = {
            "output_type": "display_data",
            "data": {
                "text/plain": (
                    f"[{omitted} outputs omitted from this response; "
                    "the notebook keeps all of them]"
                )
            },
            "metadata": {},
        }
        return [*outputs[:head], marker, *outputs[-tail:]]

    @staticmethod
    def filter_cells(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter outputs in a list of cells.

        Args:
            cells: List of cell dicts

        Returns:
            List of cells with filtered outputs
        """
        filtered_cells = []

        for cell in cells:
            cell_copy = cell.copy()

            # Only filter code cells with outputs
            if cell.get("cell_type") == "code" and "outputs" in cell:
                cell_copy["outputs"] = OutputFilter.filter_outputs(cell["outputs"])

            filtered_cells.append(cell_copy)

        return filtered_cells
