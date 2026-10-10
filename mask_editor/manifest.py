"""One portable manifest for all masks in an editor session."""

import json
from pathlib import Path
import uuid

from PIL import Image, ImageOps

from .version import __version__


class MaskManifest:
    """Track source images, saved masks, and editable shape state."""

    SCHEMA_VERSION = 1

    def __init__(self, image_dir, mask_dir, pages):
        self.image_dir = Path(image_dir).resolve()
        self.mask_dir = Path(mask_dir).resolve()
        self.path = self.mask_dir / "mask-editor.json"
        previous = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if data["schema_version"] != self.SCHEMA_VERSION or not isinstance(data["images"], list):
                    raise ValueError("Unsupported mask manifest format")
                previous = {item["origin-img"]: item for item in data["images"]}
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise ValueError(f"Cannot read mask manifest {self.path}: {exc}") from exc

        self.entries = {}
        legacy_paths = []
        for image_path, mask_path in pages:
            image_path, mask_path = Path(image_path), Path(mask_path)
            source_name = image_path.relative_to(self.image_dir).as_posix()
            mask_name = mask_path.relative_to(self.mask_dir).as_posix()
            with Image.open(image_path) as image:
                width, height = ImageOps.exif_transpose(image).size
            entry = {"origin-img": source_name, "mask": mask_name,
                     "width": width, "height": height, "saved": mask_path.is_file()}
            old = previous.get(source_name)
            if old and old.get("mask") == mask_name and "editor_state" in old:
                entry["editor_state"] = old["editor_state"]
            legacy = mask_path.with_name(mask_path.stem + ".mask-editor.json")
            if legacy.is_file():
                try:
                    entry["editor_state"] = json.loads(legacy.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise ValueError(f"Cannot migrate editor state {legacy}: {exc}") from exc
                legacy_paths.append(legacy)
            self.entries[source_name] = entry
        self._write()
        for legacy in legacy_paths:
            legacy.unlink()

    def _write(self):
        self.mask_dir.mkdir(parents=True, exist_ok=True)
        data = {"schema_version": self.SCHEMA_VERSION,
                "mask_editor_version": __version__,
                "images": list(self.entries.values())}
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            temporary.replace(self.path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def state_for(self, image_path):
        return self.entries[Path(image_path).relative_to(self.image_dir).as_posix()].get("editor_state")

    def record(self, image_path, state):
        entry = self.entries[Path(image_path).relative_to(self.image_dir).as_posix()]
        entry["saved"] = True
        if state is None:
            entry.pop("editor_state", None)
        else:
            entry["editor_state"] = state
        self._write()
