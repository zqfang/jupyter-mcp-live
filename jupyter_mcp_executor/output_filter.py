"""Output filtering for MCP responses to reduce token usage."""

import logging
from typing import Any, ClassVar

from jupyter_mcp_executor.notebook_config import OUTPUT_FILTER_CONFIG

logger = logging.getLogger(__name__)


class OutputFilter:
    """Filter notebook outputs to reduce token count in MCP responses.

    Filters large binary data (images, HTML) that would consume too many tokens.
    """

    # MIME types that should be considered for filtering
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
    def filter_single_output(output: dict[str, Any]) -> dict[str, Any]:
        """Filter a single output if it contains large binary data.

        Args:
            output: Output dict to potentially filter

        Returns:
            Original or filtered output
        """
        if output.get("output_type") in ("execute_result", "display_data"):
            data = output.get("data", {})
            if not data:
                return output

            mime_types = list(data.keys())

            # Estimate size (rough approximation)
            size_estimate = sum(len(str(v)) for v in data.values())

            if OutputFilter.should_filter(mime_types, size_estimate):
                logger.debug(
                    f"Filtering output with types {mime_types} (size ~{size_estimate} bytes)"
                )
                placeholder = OutputFilter.create_placeholder(mime_types)

                # Preserve execution_count if present
                if "execution_count" in output:
                    placeholder["execution_count"] = output["execution_count"]

                return placeholder

        return output

    @staticmethod
    def filter_outputs(outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter a list of outputs.

        Args:
            outputs: List of output dicts

        Returns:
            List with filtered outputs
        """
        if not outputs:
            return outputs

        return [OutputFilter.filter_single_output(out) for out in outputs]

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
