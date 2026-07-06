#!/usr/bin/env python3
"""Generate PNG icons for the Podocracy PWA from the shared palette."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1] / "apps" / "web"
PAPER = "#efe9dd"
PANEL = "#fbf8f2"
LINE = "#e2dac9"
SIGNAL = "#e2543b"
INK = "#fbf8f2"


def draw_icon(size: int) -> Image.Image:
    image = Image.new("RGBA", (size, size), PAPER)
    draw = ImageDraw.Draw(image)

    margin = round(size * 0.08)
    radius = round(size * 0.18)
    draw.rounded_rectangle(
        (margin, margin, size - margin, size - margin),
        radius=radius,
        fill=PANEL,
        outline=LINE,
        width=max(1, round(size * 0.03)),
    )

    center = size / 2
    signal_radius = size * 0.28
    draw.ellipse(
        (
            center - signal_radius,
            center - signal_radius,
            center + signal_radius,
            center + signal_radius,
        ),
        fill=SIGNAL,
    )

    stroke = max(2, round(size * 0.055))
    top_y = center - size * 0.04
    bottom_y = center + size * 0.12
    left_x = center - size * 0.16
    right_x = center + size * 0.16
    mid_x = center

    draw.arc(
        (left_x, top_y - size * 0.12, right_x, top_y + size * 0.12),
        start=200,
        end=340,
        fill=INK,
        width=stroke,
    )
    draw.arc(
        (mid_x - size * 0.12, bottom_y - size * 0.08, mid_x + size * 0.12, bottom_y + size * 0.08),
        start=20,
        end=160,
        fill=INK,
        width=stroke,
    )

    return image


def main() -> None:
    for size, name in ((192, "icon-192.png"), (512, "icon-512.png"), (180, "apple-touch-icon.png")):
        icon = draw_icon(size)
        icon.save(ROOT / name, format="PNG")
        print(f"Wrote {ROOT / name}")


if __name__ == "__main__":
    main()
