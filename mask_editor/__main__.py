"""Run the mask editor as ``python -m mask_editor``."""

import argparse

from .editor import launch_editor


def main(argv=None):
    parser = argparse.ArgumentParser(description="Edit binary PNG masks over source images.")
    parser.add_argument("image_dir", help="Directory of source PNG images, searched recursively")
    parser.add_argument("mask_dir", help="Directory of matching binary PNG masks")
    args = parser.parse_args(argv)
    try:
        launch_editor(args.image_dir, args.mask_dir)
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"mask-editor: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
