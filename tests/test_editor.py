"""Tests for the standalone editor's document and page contract."""

from pathlib import Path
import tempfile
import unittest

from PIL import Image

from mask_editor import MaskDocument, editor_pages
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


if __name__ == "__main__":
    unittest.main()
