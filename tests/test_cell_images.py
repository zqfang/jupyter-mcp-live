"""Tests for cell image extraction and the view_cell_image tool."""

import base64

import nbformat
import pytest
from mcp.types import ImageContent, TextContent
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook, new_output

from jupyter_mcp_live.cell_images import (
    extract_cell_images,
    extract_markdown_images,
)
from jupyter_mcp_live.execution_tools import view_cell_image
from jupyter_mcp_live.notebook_config import IMAGE_CONFIG

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
PNG_B64 = base64.b64encode(PNG_BYTES).decode()
JPEG_B64 = base64.b64encode(b"\xff\xd8\xff" + b"\x01" * 16).decode()


def _display(data: dict) -> dict:
    return {"output_type": "display_data", "data": data, "metadata": {}}


class TestExtractCellImages:
    def test_extracts_png_and_reports_metadata(self):
        outputs = [
            {"output_type": "stream", "name": "stdout", "text": "hi\n"},
            _display({"image/png": PNG_B64, "text/plain": "<Figure>"}),
        ]

        images, attached, skipped = extract_cell_images(outputs)

        assert len(images) == 1
        assert images[0].type == "image"
        assert images[0].mime_type == "image/png"
        assert images[0].data == PNG_B64
        assert attached == [
            {
                "source": "output",
                "output_index": 1,
                "mime_type": "image/png",
                "bytes": len(PNG_BYTES),
            }
        ]
        assert skipped == []

    def test_execute_result_and_jpeg_are_supported(self):
        outputs = [
            {
                "output_type": "execute_result",
                "data": {"image/jpeg": JPEG_B64},
                "metadata": {},
                "execution_count": 3,
            }
        ]

        images, attached, _ = extract_cell_images(outputs)

        assert [i.mime_type for i in images] == ["image/jpeg"]
        assert attached[0]["output_index"] == 0

    def test_prefers_png_when_output_has_several_formats(self):
        outputs = [_display({"image/jpeg": JPEG_B64, "image/png": PNG_B64})]

        images, attached, _ = extract_cell_images(outputs)

        assert len(images) == 1
        assert attached[0]["mime_type"] == "image/png"

    def test_joins_multiline_and_list_base64(self):
        wrapped = "\n".join(PNG_B64[i : i + 10] for i in range(0, len(PNG_B64), 10))
        outputs = [
            _display({"image/png": wrapped}),
            _display({"image/png": [PNG_B64[:8], PNG_B64[8:]]}),
        ]

        images, _, skipped = extract_cell_images(outputs)

        assert [i.data for i in images] == [PNG_B64, PNG_B64]
        assert skipped == []

    def test_svg_is_skipped_with_hint(self):
        outputs = [_display({"image/svg+xml": "<svg></svg>"})]

        images, attached, skipped = extract_cell_images(outputs)

        assert images == []
        assert attached == []
        assert skipped[0]["output_index"] == 0
        assert skipped[0]["mime_type"] == "image/svg+xml"
        assert "PNG" in skipped[0]["reason"]

    def test_invalid_base64_is_skipped(self):
        outputs = [_display({"image/png": "not*base64!"})]

        images, _, skipped = extract_cell_images(outputs)

        assert images == []
        assert "base64" in skipped[0]["reason"]

    def test_oversized_image_is_skipped(self):
        outputs = [_display({"image/png": PNG_B64})]

        images, _, skipped = extract_cell_images(outputs, max_bytes=8)

        assert images == []
        assert "too large" in skipped[0]["reason"]

    def test_limit_skips_extra_images(self):
        outputs = [_display({"image/png": PNG_B64}) for _ in range(3)]

        images, attached, skipped = extract_cell_images(outputs, max_images=2)

        assert len(images) == 2
        assert [a["output_index"] for a in attached] == [0, 1]
        assert skipped[0]["output_index"] == 2
        assert "limit" in skipped[0]["reason"]

    def test_non_image_outputs_are_ignored(self):
        outputs = [
            _display({"text/html": "<b>x</b>"}),
            {"output_type": "error", "ename": "E", "evalue": "", "traceback": []},
        ]

        assert extract_cell_images(outputs) == ([], [], [])

    def test_does_not_mutate_outputs(self):
        output = _display({"image/png": [PNG_B64[:8], PNG_B64[8:]]})
        before = {"data": {"image/png": list(output["data"]["image/png"])}}

        extract_cell_images([output])

        assert output["data"]["image/png"] == before["data"]["image/png"]


@pytest.fixture
def image_notebook(tmp_path):
    """Notebook: [0] code with a PNG plot, [1] markdown, [2] code without images."""
    plot = new_code_cell("plt.plot([1, 2])", execution_count=7)
    plot.outputs = [
        new_output("display_data", data={"image/png": PNG_B64, "text/plain": "<F>"})
    ]
    text = new_code_cell("print(1)", execution_count=8)
    text.outputs = [new_output("stream", name="stdout", text="1\n")]
    nb = new_notebook(cells=[plot, new_markdown_cell("# Title"), text])
    path = tmp_path / "plots.ipynb"
    nbformat.write(nb, str(path))
    return str(path), plot.id


class TestViewCellImageTool:
    @pytest.mark.asyncio
    async def test_returns_summary_and_image_content(self, image_notebook):
        path, cell_id = image_notebook

        result = await view_cell_image(path, position_index=0)

        summary = result.structured_content
        assert summary["position"] == 0
        assert summary["cell_id"] == cell_id
        assert summary["cell_type"] == "code"
        assert summary["images"] == [
            {
                "source": "output",
                "output_index": 0,
                "mime_type": "image/png",
                "bytes": len(PNG_BYTES),
            }
        ]
        assert summary["skipped"] == []

        text, image = result.content
        assert isinstance(text, TextContent)
        assert isinstance(image, ImageContent)
        assert image.data == PNG_B64

    @pytest.mark.asyncio
    async def test_identifies_cell_by_execution_count_and_id(self, image_notebook):
        path, cell_id = image_notebook

        by_count = await view_cell_image(path, execution_count=7)
        by_id = await view_cell_image(path, cell_id=cell_id)

        assert by_count.structured_content["position"] == 0
        assert by_id.structured_content["position"] == 0

    @pytest.mark.asyncio
    async def test_cell_without_images_explains_in_text(self, image_notebook):
        path, _ = image_notebook

        result = await view_cell_image(path, position_index=2)

        assert result.structured_content["images"] == []
        assert len(result.content) == 1
        assert "no viewable images" in result.content[0].text

    @pytest.mark.asyncio
    async def test_markdown_cell_without_images(self, image_notebook):
        path, _ = image_notebook

        result = await view_cell_image(path, position_index=1)

        assert result.structured_content["cell_type"] == "markdown"
        assert result.structured_content["images"] == []

    @pytest.mark.asyncio
    async def test_requires_exactly_one_identifier(self, image_notebook):
        path, _ = image_notebook

        with pytest.raises(ValueError, match="exactly ONE"):
            await view_cell_image(path)

    @pytest.mark.asyncio
    async def test_unknown_execution_count_raises(self, image_notebook):
        path, _ = image_notebook

        with pytest.raises(ValueError, match="execution_count=99"):
            await view_cell_image(path, execution_count=99)

    @pytest.mark.asyncio
    async def test_respects_configured_image_limit(self, tmp_path):
        cell = new_code_cell("many plots", execution_count=1)
        cell.outputs = [
            new_output("display_data", data={"image/png": PNG_B64})
            for _ in range(IMAGE_CONFIG.max_images_per_call + 1)
        ]
        path = tmp_path / "many.ipynb"
        nbformat.write(new_notebook(cells=[cell]), str(path))

        result = await view_cell_image(str(path), position_index=0)

        images = [c for c in result.content if isinstance(c, ImageContent)]
        assert len(images) == IMAGE_CONFIG.max_images_per_call
        assert len(result.structured_content["skipped"]) == 1


class TestExtractMarkdownImages:
    def test_attachment_reference(self):
        source = "Intro\n\n![chart](attachment:chart.png)"
        attachments = {"chart.png": {"image/png": PNG_B64}}

        images, attached, skipped = extract_markdown_images(source, attachments)

        assert [i.data for i in images] == [PNG_B64]
        assert attached == [
            {
                "source": "attachment",
                "ref_index": 0,
                "name": "chart.png",
                "mime_type": "image/png",
                "bytes": len(PNG_BYTES),
            }
        ]
        assert skipped == []

    def test_html_img_with_data_uri(self):
        source = f'<img src="data:image/jpeg;base64,{JPEG_B64}" width="200">'

        images, attached, _ = extract_markdown_images(source, {})

        assert [i.mime_type for i in images] == ["image/jpeg"]
        assert attached[0]["source"] == "data_uri"
        assert attached[0]["ref_index"] == 0

    def test_markdown_data_uri(self):
        source = f"![x](data:image/png;base64,{PNG_B64})"

        images, attached, _ = extract_markdown_images(source, {})

        assert [i.data for i in images] == [PNG_B64]
        assert attached[0]["source"] == "data_uri"

    def test_mixed_references_keep_source_order(self):
        source = (
            "![a](attachment:a.png)\n"
            "![remote](https://example.com/r.png)\n"
            f'<img src="data:image/png;base64,{PNG_B64}">\n'
            "![local](figs/plot.png)"
        )
        attachments = {"a.png": {"image/png": PNG_B64}}

        _, attached, skipped = extract_markdown_images(source, attachments)

        assert [(a["source"], a["ref_index"]) for a in attached] == [
            ("attachment", 0),
            ("data_uri", 2),
        ]
        assert [(s["source"], s["ref_index"], s["target"]) for s in skipped] == [
            ("url", 1, "https://example.com/r.png"),
            ("file", 3, "figs/plot.png"),
        ]
        assert "not fetched" in skipped[0]["reason"]
        assert "not supported" in skipped[1]["reason"]

    def test_missing_attachment_is_skipped(self):
        images, _, skipped = extract_markdown_images("![x](attachment:gone.png)", {})

        assert images == []
        assert skipped[0]["source"] == "attachment"
        assert skipped[0]["name"] == "gone.png"
        assert "not found" in skipped[0]["reason"]

    def test_svg_attachment_is_skipped(self):
        attachments = {"d.svg": {"image/svg+xml": "<svg></svg>"}}

        images, _, skipped = extract_markdown_images(
            "![d](attachment:d.svg)", attachments
        )

        assert images == []
        assert skipped[0]["mime_type"] == "image/svg+xml"
        assert "SVG" in skipped[0]["reason"]

    def test_non_viewable_data_uri_is_skipped(self):
        source = "![s](data:image/svg+xml;utf8,<svg/>)"

        images, _, skipped = extract_markdown_images(source, {})

        assert images == []
        assert skipped[0]["source"] == "data_uri"

    def test_angle_brackets_title_and_encoded_name(self):
        source = (
            '![a](<attachment:my chart.png> "Title")\n![b](attachment:my%20chart.png)'
        )
        attachments = {"my chart.png": {"image/png": PNG_B64}}

        images, attached, skipped = extract_markdown_images(source, attachments)

        # Both references name the same attachment: it is attached once.
        assert len(images) == 1
        assert attached[0]["name"] == "my chart.png"
        assert skipped == []

    def test_limit_applies(self):
        source = "\n".join(f"![{i}](data:image/png;base64,{PNG_B64})" for i in range(3))

        images, _, skipped = extract_markdown_images(source, {}, max_images=2)

        assert len(images) == 2
        assert "limit" in skipped[0]["reason"]

    def test_plain_markdown_has_no_images(self):
        assert extract_markdown_images("# Title\n[link](https://x.org)", {}) == (
            [],
            [],
            [],
        )


class TestViewMarkdownCellImages:
    @pytest.mark.asyncio
    async def test_markdown_attachment_is_returned(self, tmp_path):
        cell = new_markdown_cell("Pasted:\n\n![image.png](attachment:image.png)")
        cell["attachments"] = {"image.png": {"image/png": PNG_B64}}
        path = tmp_path / "md.ipynb"
        nbformat.write(new_notebook(cells=[cell]), str(path))

        result = await view_cell_image(str(path), position_index=0)

        summary = result.structured_content
        assert summary["cell_type"] == "markdown"
        assert summary["images"][0]["source"] == "attachment"
        images = [c for c in result.content if isinstance(c, ImageContent)]
        assert [i.data for i in images] == [PNG_B64]
