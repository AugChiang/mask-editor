"""Tests for the standalone editor's document and page contract."""

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from mask_editor import MaskDocument, MaskEditor, Rectangle, editor_pages
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


if __name__ == "__main__":
    unittest.main()
