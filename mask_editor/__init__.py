"""Reusable desktop editor for binary inpainting masks."""

from .editor import MaskDocument, MaskEditor, Rectangle, editor_pages, launch_editor
from .manifest import MaskManifest
from .version import __version__

__all__ = ["MaskDocument", "MaskEditor", "MaskManifest", "Rectangle", "editor_pages",
           "launch_editor", "__version__"]
