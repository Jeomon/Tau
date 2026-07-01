"""Tests for tau/utils/image_processing.py — ProcessedImage and helpers."""

from __future__ import annotations

import io

import pytest

from tau.utils.image_processing import (
    ProcessedImage,
    _resize_to_fit,
    process_image,
)

try:
    from PIL import Image

    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

pytestmark = pytest.mark.skipif(not PIL_AVAILABLE, reason="Pillow not installed")


def _make_png(width: int = 100, height: int = 100, color=(255, 0, 0)) -> bytes:
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestProcessedImage:
    def test_was_resized_false_when_same_dimensions(self):
        p = ProcessedImage(
            data=b"",
            mime_type="image/png",
            original_width=100,
            original_height=100,
            display_width=100,
            display_height=100,
        )
        assert p.was_resized is False

    def test_was_resized_true_when_different(self):
        p = ProcessedImage(
            data=b"",
            mime_type="image/png",
            original_width=4000,
            original_height=3000,
            display_width=2000,
            display_height=1500,
        )
        assert p.was_resized is True

    def test_dimension_note_none_when_not_resized(self):
        p = ProcessedImage(
            data=b"",
            mime_type="image/png",
            original_width=100,
            original_height=100,
            display_width=100,
            display_height=100,
        )
        assert p.dimension_note() is None

    def test_dimension_note_contains_scale(self):
        p = ProcessedImage(
            data=b"",
            mime_type="image/png",
            original_width=4000,
            original_height=4000,
            display_width=2000,
            display_height=2000,
        )
        note = p.dimension_note()
        assert note is not None
        assert "2.0" in note
        assert "4000x4000" in note


class TestResizeToFit:
    def test_small_image_not_resized(self):
        img = Image.new("RGB", (100, 100))
        result = _resize_to_fit(img, 2000, 2000)
        assert result.size == (100, 100)

    def test_wide_image_resized(self):
        img = Image.new("RGB", (4000, 100))
        result = _resize_to_fit(img, 2000, 2000)
        assert result.size[0] <= 2000

    def test_tall_image_resized(self):
        img = Image.new("RGB", (100, 4000))
        result = _resize_to_fit(img, 2000, 2000)
        assert result.size[1] <= 2000

    def test_aspect_ratio_preserved(self):
        img = Image.new("RGB", (4000, 2000))
        result = _resize_to_fit(img, 2000, 2000)
        w, h = result.size
        assert abs(w / h - 2.0) < 0.05


class TestProcessImage:
    def test_returns_processed_image(self):
        data = _make_png(100, 100)
        result = process_image(data)
        assert isinstance(result, ProcessedImage)
        assert len(result.data) > 0
        assert result.mime_type in ("image/png", "image/jpeg")

    def test_large_image_resized(self):
        data = _make_png(3000, 3000)
        result = process_image(data, max_width=500, max_height=500)
        assert result.display_width <= 500
        assert result.display_height <= 500
        assert result.was_resized is True

    def test_small_image_not_resized(self):
        data = _make_png(50, 50)
        result = process_image(data, max_width=2000, max_height=2000)
        assert result.display_width == 50
        assert result.display_height == 50
        assert result.was_resized is False

    def test_auto_resize_false_preserves_size(self):
        data = _make_png(3000, 3000)
        result = process_image(data, max_width=100, max_height=100, auto_resize=False)
        assert result.display_width == 3000
        assert result.display_height == 3000

    def test_force_png_output(self):
        data = _make_png(50, 50)
        result = process_image(data, to_png=True)
        assert result.mime_type == "image/png"

    def test_original_dimensions_preserved_after_resize(self):
        data = _make_png(3000, 2000)
        result = process_image(data, max_width=500, max_height=500)
        assert result.original_width == 3000
        assert result.original_height == 2000


class TestConvertToPng:
    def test_png_input_returns_png(self):
        from tau.utils.image_processing import convert_to_png

        data = _make_png(10, 10)
        result = convert_to_png(data)
        assert result[:4] == b"\x89PNG"

    def test_output_is_bytes(self):
        from tau.utils.image_processing import convert_to_png

        data = _make_png(5, 5)
        assert isinstance(convert_to_png(data), bytes)

    def test_nonempty_output(self):
        from tau.utils.image_processing import convert_to_png

        data = _make_png(8, 8)
        assert len(convert_to_png(data)) > 0


class TestEncodingHelpers:
    def test_encode_png_returns_png_bytes(self):
        from PIL import Image

        from tau.utils.image_processing import _encode_png

        img = Image.new("RGB", (4, 4), color=(255, 0, 0))
        result = _encode_png(img)
        assert result[:4] == b"\x89PNG"

    def test_encode_png_rgba_mode(self):
        from PIL import Image

        from tau.utils.image_processing import _encode_png

        img = Image.new("RGBA", (4, 4), color=(0, 255, 0, 128))
        result = _encode_png(img)
        assert result[:4] == b"\x89PNG"

    def test_encode_jpeg_returns_jpeg_bytes(self):
        from PIL import Image

        from tau.utils.image_processing import _encode_jpeg

        img = Image.new("RGB", (4, 4), color=(0, 0, 255))
        result = _encode_jpeg(img, quality=80)
        assert result[:2] == b"\xff\xd8"

    def test_as_rgb_converts_palette_mode(self):
        from PIL import Image

        from tau.utils.image_processing import _as_rgb

        img = Image.new("P", (4, 4))
        result = _as_rgb(img)
        assert result.mode == "RGB"

    def test_as_rgb_leaves_rgb_unchanged(self):
        from PIL import Image

        from tau.utils.image_processing import _as_rgb

        img = Image.new("RGB", (4, 4))
        assert _as_rgb(img) is img

    def test_encode_best_returns_tuple(self):
        from PIL import Image

        from tau.utils.image_processing import _encode_best

        img = Image.new("RGB", (4, 4))
        data, mime = _encode_best(img, max_bytes=10_000_000, jpeg_quality=80, force_png=False)
        assert isinstance(data, bytes)
        assert mime in ("image/png", "image/jpeg")

    def test_encode_best_force_png(self):
        from PIL import Image

        from tau.utils.image_processing import _encode_best

        img = Image.new("RGB", (4, 4))
        _, mime = _encode_best(img, max_bytes=10_000_000, jpeg_quality=80, force_png=True)
        assert mime == "image/png"
