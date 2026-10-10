"""Optional real-Tk smoke test for rectangle canvas interactions."""

from pathlib import Path
import sys
import tempfile
import tkinter as tk

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mask_editor import MaskDocument, MaskEditor


def main():
    with tempfile.TemporaryDirectory() as temporary:
        images = Path(temporary) / "images"
        masks = images / "mask"
        images.mkdir()
        image_path = images / "page.png"
        Image.new("RGB", (128, 128), "white").save(image_path)
        root = tk.Tk()
        root.withdraw()
        root.attributes("-alpha", 0)
        try:
            editor = MaskEditor(root, images, masks)
            root.deiconify()
            root.update()
            editor.tool.set("rectangle")
            editor.tool_changed()

            def stroke(start, end):
                def screen(point):
                    return (round(point[0] * editor.scale - editor.canvas.canvasx(0)),
                            round(point[1] * editor.scale - editor.canvas.canvasy(0)))
                editor.canvas.event_generate("<ButtonPress-1>", x=screen(start)[0], y=screen(start)[1])
                editor.canvas.event_generate("<B1-Motion>", x=screen(end)[0], y=screen(end)[1])
                editor.canvas.event_generate("<ButtonRelease-1>", x=screen(end)[0], y=screen(end)[1])
                root.update()

            stroke((20, 20), (60, 50))
            assert len(editor.document.rectangles) == 1
            assert editor.document.mask.getpixel((40, 35)) == 255
            stroke((40, 35), (70, 65))
            assert editor.document.mask.getpixel((40, 35)) == 0
            corner = editor.document.rectangles[0].corners()[2]
            stroke(corner, (corner[0] + 10, corner[1] + 10))
            assert editor.document.rectangles[0].width > 40
            handle = editor.rotation_handle(editor.document.rectangles[0])[0]
            rectangle = editor.document.rectangles[0]
            stroke(handle, (rectangle.cx + 30, rectangle.cy))
            assert abs(editor.document.rectangles[0].angle - 90) < 2
            editor.canvas.focus_set()
            editor.canvas.event_generate("<Control-c>")
            editor.canvas.event_generate("<Control-v>")
            root.update()
            assert len(editor.document.rectangles) == 2
            original, pasted = editor.document.rectangles
            assert (pasted.width, pasted.height, pasted.angle) == (
                original.width, original.height, original.angle)
            assert editor.selected_rectangle == 1
            editor.tool.set("erase")
            editor.tool_changed()
            editor.brush_slider.set(5)
            erased = (round(original.cx), round(original.cy))
            stroke(erased, erased)
            assert editor.document.mask.getpixel(erased) == 0
            assert editor.document.mask.getbbox()
            editor.render()
            editor.save()
            assert (masks / "page.png").is_file()
            reopened = MaskDocument(image_path, masks / "page.png")
            assert len(reopened.rectangles) == 2
            assert reopened.mask.getpixel(erased) == 0
        finally:
            root.destroy()
    print("Rectangle GUI smoke passed")


if __name__ == "__main__":
    main()
