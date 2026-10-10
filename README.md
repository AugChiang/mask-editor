# Mask editor

A small Tkinter editor for binary PNG masks. It needs Pillow and a desktop with
Tk support; it does not depend on Camelia, a model, or a server.

## Install and run

```bash
python -m pip install -e .
mask-editor /path/to/images /path/to/masks
# or: python -m mask_editor /path/to/images /path/to/masks
```

Both directories may contain nested folders. For each PNG in `images`, the editor
uses the matching relative path under `masks`. Missing masks start blank. Source
images are never overwritten. Saved masks are grayscale PNGs at the source image's
full resolution, containing only 0 (keep) and 255 (selected). Existing masks must
have the same dimensions as their source images. The magenta overlay is for display
only.

Paint or erase with the left mouse button, or erase with the right mouse button.
Use the brush slider, Undo/Redo, Clear mask, zoom controls, scrollbars, and middle
mouse dragging to navigate. Ctrl+S saves, Ctrl+Z undoes, and Ctrl+Y redoes. The
editor asks about unsaved changes when changing pages or closing.

## Use from Python

```python
from mask_editor import launch_editor, MaskDocument, editor_pages

launch_editor("images", "masks")
```

`editor_pages(image_dir, mask_dir)` returns source/mask path pairs. `MaskDocument`
provides mask loading, painting, undo/redo, preview, and saving without opening a
window. `launch_editor` opens the Tk window and blocks until it closes.

## Use as a submodule

```bash
git submodule add https://github.com/AugChiang/mask-editor.git mask-editor
python -m pip install -e ./mask-editor
```

Initialize it after cloning a parent repository with `git submodule update --init`.
Run its tests with `python -m unittest discover -s mask-editor/tests -v` from the
parent directory, or `python -m unittest discover -s tests -v` from this repository.
