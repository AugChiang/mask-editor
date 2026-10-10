# Mask editor

A small Tkinter editor for binary PNG masks. It needs Pillow and a desktop with
Tk support; it does not depend on Camelia, a model, or a server.

## Run from a checkout

```bash
# From the directory containing mask-editor/ (for example, Camelia):
python ./mask-editor/main.py --input-dir /path/to/clean-images
# From inside the mask-editor repository:
python main.py --input-dir /path/to/clean-images
```

This entry point works directly in an Anaconda environment without installing the
package. Pillow and Python's Tk support must be available in that environment.
Alternatively, `python -m pip install -e .` enables `mask-editor --input-dir ...`
and `python -m mask_editor --input-dir ...`.

Masks are saved to `<input-dir>/mask/`, preserving nested image paths. For example,
`<input-dir>/chapter/page.jpg` maps to `<input-dir>/mask/chapter/page.png`. PNG,
JPEG, WebP, BMP, and TIFF source images are supported. Inputs whose names differ
only by extension and would map to one mask are rejected. The
mask output folder is excluded when finding source images. Pass `--mask-dir PATH`
to use a different mask location. The older positional form
`mask-editor IMAGES MASKS` also works. Missing masks start blank. Source
images are never overwritten. Saved masks are grayscale PNGs at the source image's
full resolution, containing only 0 (keep) and 255 (selected). Existing masks must
have the same dimensions as their source images. The magenta overlay is for display
only.

Paint or erase with the left mouse button, or erase with the right mouse button.
Use the brush slider, Undo/Redo, Clear mask, zoom controls, scrollbars, and middle
mouse dragging to navigate. Ctrl+S saves, Ctrl+Z undoes, and Ctrl+Y redoes. The
editor asks about unsaved changes when changing pages or closing.
The **Erase** brush and right-button dragging also remove parts of rectangle masks.
Switch back to **Paint** to restore erased pixels. Erased areas remain editable
after saving and reopening; they are kept with the rectangle data in the manifest.

Select **Rectangle** and drag on the image to draw a masked rectangle. Drag inside
it to move it, drag a corner to change its width and height, or drag the circle
above it to rotate it. **Add rectangle** inserts one in the image center;
**Delete rectangle** or the Delete key removes the selected one. The mask PNG
contains the actual rotated rectangle pixels, with no outline or handles. A
single `<mask-dir>/mask-editor.json` manifest lists every source image and its
mask, the editor version, image dimensions, and whether its mask has been saved.
It also keeps rectangles and erasures editable after reopening. Existing
per-mask JSON sidecars are moved into this manifest when the editor starts.
Other tools can use the PNG masks alone. Brush strokes and rectangles are
combined in the saved mask. The `[` and `]` keys rotate the selected rectangle
by 15 degrees when its circle is hard to reach.

For example, an entry in the manifest looks like this (paths are relative to
their respective input and mask directories):

```json
{
  "schema_version": 1,
  "mask_editor_version": "0.1.0",
  "images": [
    {
      "origin-img": "chapter/page.jpg",
      "mask": "chapter/page.png",
      "width": 1280,
      "height": 960,
      "saved": false
    }
  ]
}
```

Saving a page changes `saved` to `true`. Pages with editable rectangles or
erasures also have an `editor_state` object in their entry.

Press **Ctrl+C** to copy the selected rectangle and **Ctrl+V** to paste an
editable duplicate with the same width, height, and rotation. Pasting on the
same image offsets the duplicate by 16 pixels so it is visible; pasting on a
different page keeps its source coordinates. You can move the pasted rectangle
before saving.

## Use from Python

```python
from mask_editor import launch_editor, MaskDocument, Rectangle, editor_pages

launch_editor("images", "images/mask")
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
An optional real-Tk check is `python tests/smoke_gui.py` from this repository.
