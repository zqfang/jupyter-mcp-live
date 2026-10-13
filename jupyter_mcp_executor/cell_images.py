"""Extract viewable images from notebook cells for MCP responses.

Code cells: image outputs (plots). Markdown cells: images the cell renders
from its own data — ``attachment:`` references (pasted/dragged images) and
``data:`` URIs. Remote URLs and local image files are reported as skipped.
"""

import base64
import binascii
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote

from mcp.types import ImageContent

from jupyter_mcp_executor.notebook_config import IMAGE_CONFIG
from jupyter_mcp_executor.notebook_models import CellContent

_IMAGE_OUTPUT_TYPES = frozenset({"display_data", "execute_result"})
_SVG_MIME = "image/svg+xml"
_PLOT_SVG_REASON = (
    "SVG cannot be viewed; re-plot as PNG (Python: "
    "%config InlineBackend.figure_formats = ['png']; R: "
    "options(jupyter.plot_mimetypes = c('text/plain', 'image/png')))"
)
_SVG_REASON = "SVG cannot be viewed"

# ![alt](target) or ![alt](<target with spaces> "title")
_MARKDOWN_IMAGE = re.compile(
    r"!\[[^\]]*\]\(\s*(?:<([^>]*)>|([^\s)]+))(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)"
)
# <img ... src="target" ...>
_HTML_IMAGE = re.compile(r"""<img\b[^>]*?\bsrc\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_DATA_URI = re.compile(r"data:([\w.+-]+/[\w.+-]+)?((?:;[^,;]*)*),(.*)", re.DOTALL)
_REMOTE_PREFIXES = ("http://", "https://", "//")
_ATTACHMENT_PREFIX = "attachment:"

Ref = dict[str, Any]


@dataclass(frozen=True)
class CellImages:
    """The viewable images of one cell plus a JSON-serializable summary."""

    position: int
    cell_id: str
    cell_type: str
    images: tuple[ImageContent, ...]
    attached: tuple[Ref, ...]
    skipped: tuple[Ref, ...]

    @classmethod
    def from_cell(cls, cell: CellContent, position: int) -> "CellImages":
        """Collect a cell's images: outputs for code, embeds for markdown."""
        if cell.cell_type == "code":
            images, attached, skipped = extract_cell_images(cell.outputs)
        elif cell.cell_type == "markdown":
            images, attached, skipped = extract_markdown_images(
                cell.get_source_as_string(), cell.attachments
            )
        else:
            images, attached, skipped = [], [], []
        return cls(
            position=position,
            cell_id=cell.cell_id or "",
            cell_type=cell.cell_type,
            images=tuple(images),
            attached=tuple(attached),
            skipped=tuple(skipped),
        )

    def summary(self) -> dict[str, Any]:
        """Return the structured result a Code Mode script receives."""
        return {
            "position": self.position,
            "cell_id": self.cell_id,
            "cell_type": self.cell_type,
            "images": list(self.attached),
            "skipped": list(self.skipped),
        }


class _Collector:
    """Accumulates images under the per-call count and per-image size limits."""

    def __init__(self, max_images: int, max_bytes: int) -> None:
        self.max_images = max_images
        self.max_bytes = max_bytes
        self.images: list[ImageContent] = []
        self.attached: list[Ref] = []
        self.skipped: list[Ref] = []

    def skip(self, ref: Ref, reason: str) -> None:
        self.skipped.append({**ref, "reason": reason})

    def add(self, ref: Ref, mime_type: str, data: Any) -> None:
        """Attach base64 ``data`` as an image, or record why it was skipped."""
        ref = {**ref, "mime_type": mime_type}
        if len(self.images) >= self.max_images:
            self.skip(ref, f"limit of {self.max_images} images reached")
            return
        encoded = _join_base64(data)
        try:
            size = len(base64.b64decode(encoded, validate=True))
        except (binascii.Error, ValueError):
            self.skip(ref, "invalid base64 image data")
            return
        if size > self.max_bytes:
            self.skip(ref, f"too large ({size} > {self.max_bytes} bytes)")
            return
        self.images.append(ImageContent(type="image", data=encoded, mimeType=mime_type))
        self.attached.append({**ref, "bytes": size})

    def result(self) -> tuple[list[ImageContent], list[Ref], list[Ref]]:
        return self.images, self.attached, self.skipped


def _join_base64(value: Any) -> str:
    """Return nbformat image data as one base64 string without whitespace.

    nbformat stores it as a string (possibly wrapped with newlines) or as a
    list of string chunks.
    """
    text = "".join(value) if isinstance(value, list) else str(value)
    return "".join(text.split())


def _viewable_mime(data: dict[str, Any]) -> str | None:
    """Return the preferred viewable MIME type present in a mimebundle."""
    return next((m for m in IMAGE_CONFIG.viewable_mime_types if m in data), None)


def extract_cell_images(
    outputs: list[dict[str, Any]],
    max_images: int = IMAGE_CONFIG.max_images_per_call,
    max_bytes: int = IMAGE_CONFIG.max_image_bytes,
) -> tuple[list[ImageContent], list[Ref], list[Ref]]:
    """Collect the raster images of a code cell's outputs.

    At most one image is taken per output (the first viewable MIME type in
    ``IMAGE_CONFIG.viewable_mime_types`` order). The outputs are not mutated.

    Args:
        outputs: Unfiltered nbformat output dicts of one cell
        max_images: Maximum number of images to return
        max_bytes: Maximum decoded size of one image

    Returns:
        (images, attached, skipped): the image content blocks; one
        ``{source, output_index, mime_type, bytes}`` dict per image; and one
        ``{source, output_index, mime_type, reason}`` dict per image output
        that was not returned.
    """
    collector = _Collector(max_images, max_bytes)
    for index, output in enumerate(outputs):
        if output.get("output_type") not in _IMAGE_OUTPUT_TYPES:
            continue
        data = output.get("data") or {}
        ref = {"source": "output", "output_index": index}
        mime_type = _viewable_mime(data)
        if mime_type is not None:
            collector.add(ref, mime_type, data[mime_type])
        elif _SVG_MIME in data:
            collector.skip({**ref, "mime_type": _SVG_MIME}, _PLOT_SVG_REASON)
    return collector.result()


def _image_targets(source: str) -> list[str]:
    """Return the image targets of markdown/HTML image tags, in source order."""
    found = [
        (m.start(), m.group(1) if m.group(1) is not None else m.group(2))
        for m in _MARKDOWN_IMAGE.finditer(source)
    ]
    found += [(m.start(), m.group(1)) for m in _HTML_IMAGE.finditer(source)]
    return [target.strip() for _, target in sorted(found)]


def _add_attachment(
    collector: _Collector, ref: Ref, name: str, attachments: dict[str, Any]
) -> None:
    ref = {**ref, "name": name}
    bundle = attachments.get(name)
    if not isinstance(bundle, dict):
        collector.skip(ref, "attachment not found in the cell")
        return
    mime_type = _viewable_mime(bundle)
    if mime_type is not None:
        collector.add(ref, mime_type, bundle[mime_type])
    elif _SVG_MIME in bundle:
        collector.skip({**ref, "mime_type": _SVG_MIME}, _SVG_REASON)
    else:
        collector.skip(ref, f"unsupported attachment type: {sorted(bundle)}")


def _add_data_uri(collector: _Collector, ref: Ref, target: str) -> None:
    match = _DATA_URI.fullmatch(target)
    mime_type = (match.group(1) or "").lower() if match else ""
    params = match.group(2).lower().split(";") if match else []
    if not match or mime_type not in IMAGE_CONFIG.viewable_mime_types:
        reason = _SVG_REASON if mime_type == _SVG_MIME else "not a viewable image"
        collector.skip({**ref, "mime_type": mime_type or None}, reason)
    elif "base64" not in params:
        collector.skip({**ref, "mime_type": mime_type}, "data URI is not base64")
    else:
        collector.add(ref, mime_type, match.group(3))


def extract_markdown_images(
    source: str,
    attachments: dict[str, Any],
    max_images: int = IMAGE_CONFIG.max_images_per_call,
    max_bytes: int = IMAGE_CONFIG.max_image_bytes,
) -> tuple[list[ImageContent], list[Ref], list[Ref]]:
    """Collect the images a markdown cell renders from its own data.

    Handles ``![alt](target)`` and ``<img src="target">``. ``attachment:``
    targets are looked up in the cell's attachments (each attached once, even
    if referenced again); ``data:`` URIs are decoded inline. Remote URLs and
    local files are reported in ``skipped`` with their target.

    Args:
        source: Markdown source of the cell
        attachments: The cell's attachments, ``{name: {mime_type: base64}}``
        max_images: Maximum number of images to return
        max_bytes: Maximum decoded size of one image

    Returns:
        (images, attached, skipped) like ``extract_cell_images``; entries
        carry ``source`` ("attachment", "data_uri", "url" or "file") and
        ``ref_index``, the position of the reference among the cell's images.
    """
    collector = _Collector(max_images, max_bytes)
    seen_attachments: set[str] = set()
    for index, target in enumerate(_image_targets(source)):
        if target.lower().startswith(_ATTACHMENT_PREFIX):
            raw_name = target[len(_ATTACHMENT_PREFIX) :]
            name = raw_name if raw_name in attachments else unquote(raw_name)
            if name not in seen_attachments:
                seen_attachments.add(name)
                ref = {"source": "attachment", "ref_index": index}
                _add_attachment(collector, ref, name, attachments)
        elif target.lower().startswith("data:"):
            _add_data_uri(collector, {"source": "data_uri", "ref_index": index}, target)
        elif target.lower().startswith(_REMOTE_PREFIXES):
            collector.skip(
                {"source": "url", "ref_index": index, "target": target},
                "remote images are not fetched",
            )
        else:
            collector.skip(
                {"source": "file", "ref_index": index, "target": target},
                "local image files are not supported yet",
            )
    return collector.result()
