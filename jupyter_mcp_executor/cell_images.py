"""Extract viewable images from notebook cell outputs for MCP responses."""

import base64
import binascii
from dataclasses import dataclass
from typing import Any

from mcp.types import ImageContent

from jupyter_mcp_executor.notebook_config import IMAGE_CONFIG


@dataclass(frozen=True)
class CellImages:
    """The viewable images of one cell plus a JSON-serializable summary."""

    position: int
    cell_id: str
    images: tuple[ImageContent, ...]
    attached: tuple[dict[str, Any], ...]
    skipped: tuple[dict[str, Any], ...]

    def summary(self) -> dict[str, Any]:
        """Return the structured result a Code Mode script receives."""
        return {
            "position": self.position,
            "cell_id": self.cell_id,
            "images": list(self.attached),
            "skipped": list(self.skipped),
        }


_IMAGE_OUTPUT_TYPES = frozenset({"display_data", "execute_result"})
_SVG_MIME = "image/svg+xml"


def _join_base64(value: Any) -> str:
    """Return nbformat image data as one base64 string without whitespace.

    nbformat stores it as a string (possibly wrapped with newlines) or as a
    list of string chunks.
    """
    text = "".join(value) if isinstance(value, list) else str(value)
    return "".join(text.split())


def _skip(index: int, mime_type: str, reason: str) -> dict[str, Any]:
    return {"output_index": index, "mime_type": mime_type, "reason": reason}


def extract_cell_images(
    outputs: list[dict[str, Any]],
    max_images: int = IMAGE_CONFIG.max_images_per_call,
    max_bytes: int = IMAGE_CONFIG.max_image_bytes,
) -> tuple[list[ImageContent], list[dict[str, Any]], list[dict[str, Any]]]:
    """Collect the raster images of a cell's outputs as MCP image content.

    At most one image is taken per output (the first viewable MIME type in
    ``IMAGE_CONFIG.viewable_mime_types`` order). The outputs are not mutated.

    Args:
        outputs: Unfiltered nbformat output dicts of one cell
        max_images: Maximum number of images to return
        max_bytes: Maximum decoded size of one image

    Returns:
        (images, attached, skipped): the image content blocks; one
        ``{output_index, mime_type, bytes}`` dict per image; and one
        ``{output_index, mime_type, reason}`` dict per image output that was
        not returned.
    """
    images: list[ImageContent] = []
    attached: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for index, output in enumerate(outputs):
        if output.get("output_type") not in _IMAGE_OUTPUT_TYPES:
            continue
        data = output.get("data") or {}
        mime_type = next(
            (m for m in IMAGE_CONFIG.viewable_mime_types if m in data), None
        )
        if mime_type is None:
            if _SVG_MIME in data:
                skipped.append(
                    _skip(
                        index,
                        _SVG_MIME,
                        "SVG cannot be viewed; re-plot as PNG (Python: "
                        "%config InlineBackend.figure_formats = ['png']; R: "
                        "options(jupyter.plot_mimetypes = c('text/plain', "
                        "'image/png')))",
                    )
                )
            continue

        if len(images) >= max_images:
            skipped.append(
                _skip(index, mime_type, f"limit of {max_images} images reached")
            )
            continue

        encoded = _join_base64(data[mime_type])
        try:
            size = len(base64.b64decode(encoded, validate=True))
        except (binascii.Error, ValueError):
            skipped.append(_skip(index, mime_type, "invalid base64 image data"))
            continue
        if size > max_bytes:
            skipped.append(
                _skip(index, mime_type, f"too large ({size} > {max_bytes} bytes)")
            )
            continue

        images.append(ImageContent(type="image", data=encoded, mimeType=mime_type))
        attached.append({"output_index": index, "mime_type": mime_type, "bytes": size})

    return images, attached, skipped
