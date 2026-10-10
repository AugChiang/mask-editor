"""Run the mask editor as ``python -m mask_editor``."""

import argparse
from pathlib import Path

from .editor import launch_editor


def main(argv=None):
    parser = argparse.ArgumentParser(description="Edit binary PNG masks over source images.")
    parser.add_argument("image_dir", nargs="?", help="Directory of source images")
    parser.add_argument("legacy_mask_dir", nargs="?", help="Optional mask directory")
    parser.add_argument("--input-dir", type=Path, help="Directory of clean images")
    parser.add_argument("--mask-dir", type=Path, help="Mask output directory; default: INPUT_DIR/mask")
    args = parser.parse_args(argv)
    if args.image_dir and args.input_dir:
        parser.error("Use either a positional image directory or --input-dir")
    if args.legacy_mask_dir and args.mask_dir:
        parser.error("Use either a positional mask directory or --mask-dir")
    images = args.input_dir or (Path(args.image_dir) if args.image_dir else None)
    if images is None:
        parser.error("An image directory is required (use --input-dir)")
    masks = args.mask_dir or (Path(args.legacy_mask_dir) if args.legacy_mask_dir else images / "mask")
    try:
        launch_editor(images, masks)
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"mask-editor: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
