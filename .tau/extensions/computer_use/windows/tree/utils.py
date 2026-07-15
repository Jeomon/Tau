"""Small geometry helpers shared by the tree traversal engine."""

from __future__ import annotations

import random
from typing import Any


def random_point_within_bounding_box(node: Any, scale_factor: float = 1.0) -> tuple[int, int]:
    """Generate a random point within a scaled-down bounding box of node."""
    box = node.BoundingRectangle
    scaled_width = int(box.width() * scale_factor)
    scaled_height = int(box.height() * scale_factor)
    scaled_left = box.left + (box.width() - scaled_width) // 2
    scaled_top = box.top + (box.height() - scaled_height) // 2
    x = random.randint(scaled_left, scaled_left + scaled_width)
    y = random.randint(scaled_top, scaled_top + scaled_height)
    return (x, y)
