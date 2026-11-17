#!/usr/bin/env python3
"""Render accented Latin glyphs in the folder layout expected by Catbox's
``repack/fnt/generate.rb``.

The stock Umineko FNT maps every accented Latin codepoint to the same
placeholder glyph (verified: raw header entries for U+00E1, U+00E9, U+00EA,
U+00E7, U+00ED and U+00E3 are all 262160).  This script produces PNGs that
``generate.rb`` can splice into a copy of the FNT so Shin can render pt-BR.

No game asset is read or written here; only open-source font rasterisation.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

CODEPOINTS = [
    0x00C0, 0x00C1, 0x00C2, 0x00C3, 0x00C4, 0x00C5,  # À Á Â Ã Ä Å
    0x00C7, 0x00C8, 0x00C9, 0x00CA, 0x00CB, 0x00CD,  # Ç È É Ê Ë Í
    0x00CE, 0x00CF, 0x00D2, 0x00D3, 0x00D4, 0x00D5,  # Î Ï Ò Ó Ô Õ
    0x00D9, 0x00DA, 0x00DB, 0x00DC, 0x00DD, 0x00DF,  # Ù Ú Û Ü Ý ß
    0x00E0, 0x00E1, 0x00E2, 0x00E3, 0x00E4, 0x00E5,  # à á â ã ä å
    0x00E7, 0x00E8, 0x00E9, 0x00EA, 0x00EB, 0x00EC,  # ç è é ê ë ì
    0x00ED, 0x00EE, 0x00EF, 0x00F2, 0x00F3, 0x00F4,  # í î ï ò ó ô
    0x00F5, 0x00F6, 0x00F9, 0x00FA, 0x00FB, 0x00FC,  # õ ö ù ú û ü
    0x00FD, 0x00FF, 0x00B4, 0x0060, 0x005E, 0x007E,  # ý ÿ ´ ` ^ ~
]

FORMATS = [("1x", 112), ("0.5x", 56), ("0.25x", 28), ("0.125x", 14)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ttf", default="/usr/share/fonts/TTF/DejaVuSans.ttf")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--em", type=int, default=1000)
    args = parser.parse_args()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Advance width in font units, from the largest raster.
    measure_font = ImageFont.truetype(args.ttf, args.em)
    for cp in CODEPOINTS:
        char = chr(cp)
        advance = 530  # tuned so frame_width = 530*128/1000 ~= 68, matching base Latin glyphs
        folder = out / f"glyph_{cp}_{advance}_{args.em}_0_0_0"
        folder.mkdir(parents=True, exist_ok=True)
        for name, height in FORMATS:
            size = height
            font = ImageFont.truetype(args.ttf, size)
            bbox = font.getbbox(char)
            width = max(1, bbox[2] - bbox[0])
            image = Image.new("L", (width + 8, height), 255)
            draw = ImageDraw.Draw(image)
            draw.text((4 - bbox[0], -bbox[1]), char, font=font, fill=0)
            image.save(folder / f"{name}.png")
    print(f"generated {len(CODEPOINTS)} glyph folders in {out}")


if __name__ == "__main__":
    main()
