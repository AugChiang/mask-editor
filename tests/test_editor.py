"""Tests for the standalone editor's document and page contract."""

from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from mask_editor import MaskDocument, MaskEditor, MaskManifest, Rectangle, __version__, editor_pages
from mask_editor.__main__ import main


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.images = root / "images"
        self.masks = root / "masks"
        self.image_path = self.images / "chapter" / "page.png"
        self.mask_path = self.masks / "chapter" / "page.png"
        self.image_path.parent.mkdir(parents=True)
        Image.new("RGB", (24, 20), "white").save(self.image_path)

    def test_nested_pages_and_missing_masks(self):
        self.assertEqual(editor_pages(self.images, self.masks), [(self.image_path, self.mask_path)])
        document = MaskDocument(self.image_path, self.mask_path)
        self.assertTrue(document.dirty)
        self.assertEqual(document.mask.getextrema(), (0, 0))
        document.save()
        self.assertTrue(self.mask_path.is_file())
        self.assertFalse(document.dirty)

    def test_paint_undo_redo_save_and_source_preservation(self):
        original = self.image_path.read_bytes()
        document = MaskDocument(self.image_path, self.mask_path)
        document.brush((2, 10), (21, 10), 3)
        document.end_stroke()
        self.assertEqual(document.mask.getpixel((12, 10)), 255)
        self.assertNotEqual(document.preview().getpixel((12, 10)), (255, 255, 255))
        document.undo()
        self.assertEqual(document.mask.getpixel((12, 10)), 0)
        document.redo()
        document.save()
        with Image.open(self.mask_path) as mask:
            self.assertEqual(mask.size, (24, 20))
            self.assertEqual(mask.mode, "L")
            self.assertEqual(set(mask.tobytes()), {0, 255})
        self.assertEqual(self.image_path.read_bytes(), original)

    def test_invalid_paths_and_mask_sizes(self):
        with self.assertRaisesRegex(ValueError, "overlap"):
            editor_pages(self.images, self.images)
        self.mask_path.parent.mkdir(parents=True)
        Image.new("L", (20, 24), 0).save(self.mask_path)
        with self.assertRaisesRegex(ValueError, "Mask size"):
            MaskDocument(self.image_path, self.mask_path)

    def test_cli_reports_missing_images_without_opening_window(self):
        with self.assertRaises(SystemExit) as exit_context:
            main([str(self.images / "missing"), str(self.masks)])
        self.assertEqual(exit_context.exception.code, 1)

    def test_cli_creates_manifest_before_opening_window(self):
        import tkinter

        with patch("tkinter.Tk", side_effect=tkinter.TclError("no display")):
            with self.assertRaises(SystemExit) as exit_context:
                main(["--input-dir", str(self.images), "--mask-dir", str(self.masks)])
        self.assertEqual(exit_context.exception.code, 1)
        data = json.loads((self.masks / "mask-editor.json").read_text(encoding="utf-8"))
        self.assertEqual(data["images"][0]["origin-img"], "chapter/page.png")

    def test_input_dir_uses_nested_mask_output_and_does_not_scan_it_as_input(self):
        output = self.images / "mask"
        with patch("mask_editor.__main__.launch_editor") as launch:
            self.assertEqual(main(["--input-dir", str(self.images)]), 0)
            launch.assert_called_once_with(self.images, output)
        pages = editor_pages(self.images, output)
        self.assertEqual(pages, [(self.image_path, output / "chapter/page.png")])
        document = MaskDocument(*pages[0])
        document.save()
        self.assertEqual(editor_pages(self.images, output), pages)

    def test_jpeg_input_maps_to_png_mask_and_ambiguous_names_are_rejected(self):
        source = self.images / "chapter/photo.jpg"
        Image.new("RGB", (24, 20), "white").save(source)
        pages = editor_pages(self.images, self.images / "mask")
        self.assertIn((source, self.images / "mask/chapter/photo.png"), pages)
        Image.new("RGB", (24, 20), "white").save(self.images / "chapter/page.jpg")
        with self.assertRaisesRegex(ValueError, "same mask"):
            editor_pages(self.images, self.images / "mask")

    def test_rectangles_rasterize_move_undo_and_reopen_as_shapes(self):
        original = self.image_path.read_bytes()
        document = MaskDocument(self.image_path, self.mask_path)
        document.add_rectangle(Rectangle(8, 8, 6, 4))
        document.end_stroke()
        self.assertEqual(document.mask.getpixel((8, 8)), 255)
        self.assertEqual(document.mask.getpixel((18, 8)), 0)
        document.update_rectangle(0, Rectangle(17, 8, 6, 4, 45))
        document.end_stroke()
        self.assertEqual(document.mask.getpixel((8, 8)), 0)
        self.assertEqual(document.mask.getpixel((17, 8)), 255)
        document.undo()
        self.assertEqual(document.mask.getpixel((8, 8)), 255)
        document.redo()
        document.save()
        self.assertTrue(document.metadata_path.is_file())
        self.assertEqual(set(Image.open(self.mask_path).tobytes()), {0, 255})
        self.assertEqual(self.image_path.read_bytes(), original)
        reopened = MaskDocument(self.image_path, self.mask_path)
        self.assertEqual(len(reopened.rectangles), 1)
        self.assertEqual(reopened.rectangles[0].angle, 45)
        self.assertEqual(reopened.mask.tobytes(), document.mask.tobytes())
        reopened.remove_rectangle(0)
        self.assertEqual(reopened.mask.getextrema(), (0, 0))
        reopened.save()
        self.assertFalse(reopened.metadata_path.exists())

    def test_erase_cuts_rectangle_mask_and_survives_undo_save_and_reopen(self):
        document = MaskDocument(self.image_path, self.mask_path)
        rectangle = Rectangle(12, 10, 16, 12)
        document.add_rectangle(rectangle)
        document.end_stroke()
        document.brush((12, 10), (12, 10), 3, erase=True)
        document.end_stroke()
        self.assertEqual(document.mask.getpixel((12, 10)), 0)
        self.assertEqual(document.mask.getpixel((6, 10)), 255)
        self.assertEqual(document.preview().getpixel((12, 10)), (255, 255, 255))
        document.undo()
        self.assertEqual(document.mask.getpixel((12, 10)), 255)
        document.redo()
        self.assertEqual(document.mask.getpixel((12, 10)), 0)
        document.brush((12, 10), (12, 10), 3)
        document.end_stroke()
        self.assertEqual(document.mask.getpixel((12, 10)), 255)
        document.undo()
        self.assertEqual(document.mask.getpixel((12, 10)), 0)
        document.save()

        with Image.open(self.mask_path) as saved:
            self.assertEqual(set(saved.tobytes()), {0, 255})
            self.assertEqual(saved.getpixel((12, 10)), 0)
        metadata = json.loads(document.metadata_path.read_text(encoding="utf-8"))
        self.assertEqual(metadata["version"], 2)
        reopened = MaskDocument(self.image_path, self.mask_path)
        self.assertEqual(len(reopened.rectangles), 1)
        self.assertEqual(reopened.mask.getpixel((12, 10)), 0)
        reopened.remove_rectangle(0)
        reopened.add_rectangle(rectangle)
        reopened.end_stroke()
        self.assertEqual(reopened.mask.getpixel((12, 10)), 255)

    def test_version_one_rectangle_metadata_still_loads(self):
        document = MaskDocument(self.image_path, self.mask_path)
        document.add_rectangle(Rectangle(12, 10, 8, 6, 30))
        document.save()
        metadata = json.loads(document.metadata_path.read_text(encoding="utf-8"))
        metadata["version"] = 1
        del metadata["erase_png"]
        document.metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        reopened = MaskDocument(self.image_path, self.mask_path)
        self.assertEqual(len(reopened.rectangles), 1)
        self.assertEqual(reopened.rectangles[0].angle, 30)
        self.assertEqual(reopened.mask.tobytes(), document.mask.tobytes())

    def test_one_manifest_lists_all_images_and_preserves_editable_masks(self):
        second_image = self.images / "chapter" / "second.jpg"
        Image.new("RGB", (12, 16), "white").save(second_image)
        pages = editor_pages(self.images, self.masks)
        manifest = MaskManifest(self.images, self.masks, pages)
        data = json.loads(manifest.path.read_text(encoding="utf-8"))
        self.assertEqual(data["mask_editor_version"], __version__)
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual([item["origin-img"] for item in data["images"]],
                         ["chapter/page.png", "chapter/second.jpg"])
        self.assertEqual(data["images"][1],
                         {"origin-img": "chapter/second.jpg", "mask": "chapter/second.png",
                          "width": 12, "height": 16, "saved": False})

        document = MaskDocument(self.image_path, self.mask_path, manifest=manifest)
        document.add_rectangle(Rectangle(12, 10, 14, 12, 30))
        document.end_stroke()
        document.brush((12, 10), (12, 10), 3, erase=True)
        document.end_stroke()
        document.save()
        self.assertFalse(document.metadata_path.exists())
        self.assertEqual(list(self.masks.rglob("*.json")), [manifest.path])
        data = json.loads(manifest.path.read_text(encoding="utf-8"))
        page = data["images"][0]
        self.assertTrue(page["saved"])
        self.assertEqual(page["mask"], "chapter/page.png")
        self.assertEqual(page["editor_state"]["rectangles"][0]["angle"], 30)
        reopened_manifest = MaskManifest(self.images, self.masks, pages)
        reopened = MaskDocument(self.image_path, self.mask_path, manifest=reopened_manifest)
        self.assertEqual(reopened.mask.tobytes(), document.mask.tobytes())
        self.assertEqual(len(reopened.rectangles), 1)
        self.assertEqual(reopened.mask.getpixel((12, 10)), 0)

    def test_manifest_migrates_legacy_sidecar(self):
        legacy = MaskDocument(self.image_path, self.mask_path)
        legacy.add_rectangle(Rectangle(12, 10, 8, 6, 45))
        legacy.save()
        self.assertTrue(legacy.metadata_path.exists())
        manifest = MaskManifest(self.images, self.masks, editor_pages(self.images, self.masks))
        self.assertFalse(legacy.metadata_path.exists())
        self.assertEqual(len(list(self.masks.rglob("*.json"))), 1)
        reopened = MaskDocument(self.image_path, self.mask_path, manifest=manifest)
        self.assertEqual(reopened.rectangles[0].angle, 45)
        self.assertEqual(reopened.mask.tobytes(), legacy.mask.tobytes())

    def test_erasing_empty_image_does_not_hide_a_later_rectangle(self):
        document = MaskDocument(self.image_path, self.mask_path)
        document.add_rectangle(Rectangle(12, 10, 8, 6))
        document.end_stroke()
        document.brush((2, 2), (2, 2), 3, erase=True)
        document.end_stroke()
        document.add_rectangle(Rectangle(2, 2, 4, 4))
        document.end_stroke()
        self.assertEqual(document.mask.getpixel((2, 2)), 255)

    def test_corner_resize_keeps_opposite_corner_and_rotation_changes_coverage(self):
        rectangle = Rectangle(12, 10, 8, 4, 30)
        opposite = rectangle.corners()[2]
        rectangle.resize_from_corner(0, (2, 2))
        self.assertAlmostEqual(rectangle.corners()[2][0], opposite[0])
        self.assertAlmostEqual(rectangle.corners()[2][1], opposite[1])
        self.assertGreater(rectangle.width, 8)
        document = MaskDocument(self.image_path, self.mask_path)
        document.add_rectangle(Rectangle(12, 10, 12, 2, 90))
        document.end_stroke()
        self.assertEqual(document.mask.getpixel((12, 5)), 255)
        self.assertEqual(document.mask.getpixel((7, 10)), 0)

    def test_canvas_rectangle_drag_controls_update_saved_pixels(self):
        Image.new("RGB", (100, 100), "white").save(self.image_path)

        class Canvas:
            @staticmethod
            def canvasx(x):
                return x

            @staticmethod
            def canvasy(y):
                return y

        editor = MaskEditor.__new__(MaskEditor)
        editor.document = MaskDocument(self.image_path, self.mask_path)
        editor.canvas = Canvas()
        editor.scale = 1
        editor.selected_rectangle = None
        editor._shape_action = None
        editor._shape_original = None
        editor._last_point = None
        editor.schedule_render = lambda: None
        editor.update_status = lambda: None
        event = lambda x, y: SimpleNamespace(x=x, y=y)

        editor.press_rectangle(event(20, 20))
        editor.drag_rectangle(event(50, 40))
        editor.release()
        self.assertEqual(editor.document.mask.getpixel((35, 30)), 255)
        self.assertEqual((editor.document.rectangles[0].width,
                          editor.document.rectangles[0].height), (30, 20))

        editor.press_rectangle(event(35, 30))
        editor.drag_rectangle(event(60, 50))
        editor.release()
        self.assertEqual(editor.document.mask.getpixel((35, 30)), 0)
        self.assertEqual(editor.document.mask.getpixel((60, 50)), 255)

        corner = editor.document.rectangles[0].corners()[2]
        editor.press_rectangle(event(*corner))
        editor.drag_rectangle(event(corner[0] + 10, corner[1] + 10))
        editor.release()
        self.assertGreater(editor.document.rectangles[0].width, 30)

        handle = editor.document.rectangles[0].rotation_handle(28)
        editor.press_rectangle(event(*handle))
        editor.drag_rectangle(event(editor.document.rectangles[0].cx + 10,
                                    editor.document.rectangles[0].cy))
        editor.release()
        self.assertAlmostEqual(editor.document.rectangles[0].angle, 90)

    def test_copy_paste_uses_adjusted_geometry_and_works_across_pages(self):
        Image.new("RGB", (100, 100), "white").save(self.image_path)

        class Tool:
            value = "rectangle"

            def get(self):
                return self.value

            def set(self, value):
                self.value = value

        editor = MaskEditor.__new__(MaskEditor)
        editor.document = MaskDocument(self.image_path, self.mask_path)
        editor.document.add_rectangle(Rectangle(30, 30, 20, 10, 30))
        editor.document.end_stroke()
        editor.tool = Tool()
        editor.selected_rectangle = 0
        editor._shape_action = None
        editor._last_point = None
        editor._copied_rectangle = None
        editor._copied_from_image = None
        editor._paste_count = 0
        editor.update_status = lambda message=None: None
        editor.update_cursor = lambda: None
        editor.schedule_render = lambda: None

        self.assertTrue(editor.copy_rectangle())
        editor.document.update_rectangle(0, Rectangle(30, 30, 25, 15, 45))
        editor.document.end_stroke()
        self.assertTrue(editor.paste_rectangle())
        pasted = editor.document.rectangles[1]
        self.assertEqual((pasted.cx, pasted.cy, pasted.width, pasted.height, pasted.angle),
                         (46, 46, 20, 10, 30))
        self.assertEqual(editor.selected_rectangle, 1)
        editor.document.undo()
        self.assertEqual(len(editor.document.rectangles), 1)

        other_image = self.images / "other.png"
        Image.new("RGB", (100, 100), "white").save(other_image)
        editor.document = MaskDocument(other_image, self.masks / "other.png")
        editor.selected_rectangle = None
        self.assertTrue(editor.paste_rectangle())
        other = editor.document.rectangles[0]
        self.assertEqual((other.cx, other.cy, other.width, other.height, other.angle),
                         (30, 30, 20, 10, 30))


if __name__ == "__main__":
    unittest.main()
