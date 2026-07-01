"""Rotation correction: pick the upright orientation before cross-check.

The decision is pure (highest mean OCR confidence wins, ties prefer 0°). The
caller supplies confidences obtained by quick OCR passes at each candidate angle;
both cross-check engines then run on the chosen, upright page.
"""

from __future__ import annotations

_ANGLES = (0, 90, 180, 270)


def best_rotation_angle(confidence_by_angle: dict[int, float]) -> int:
    """Return the angle in {0,90,180,270} with highest confidence; ties -> 0."""
    if not confidence_by_angle:
        return 0
    return max(_ANGLES, key=lambda a: (confidence_by_angle.get(a, -1.0), -a))
