"""Desktop editor for full-resolution binary PNG masks."""

from collections import deque
from dataclasses import asdict, dataclass, replace
import base64
import hashlib
import io
import json
import math
from pathlib import Path
import uuid

from PIL import Image, ImageDraw, ImageOps


OVERLAY_COLOR = (255, 64, 160)
OVERLAY_ALPHA = 100
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


def editor_pages(image_dir, mask_dir):
    images, masks = Path(image_dir).resolve(), Path(mask_dir).resolve()
    if images == masks or masks in images.parents:
        raise ValueError("Image and mask directories must not overlap in this direction")
    if not images.is_dir():
        raise ValueError(f"Image directory does not exist: {images}")
    pages, outputs = [], {}
    for path in sorted(images.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS or masks in path.parents:
            continue
        mask = masks / path.relative_to(images).with_suffix(".png")
        if mask in outputs:
            raise ValueError(f"Images {outputs[mask]} and {path} map to the same mask: {mask}")
        outputs[mask] = path
        pages.append((path, mask))
    if not pages:
        raise ValueError(f"No supported images found in {images}")
    return pages


@dataclass
class Rectangle:
    """A rectangle in source-image coordinates, rotated clockwise in screen space."""

    cx: float
    cy: float
    width: float
    height: float
    angle: float = 0.0

    def __post_init__(self):
        if (not all(math.isfinite(value) for value in asdict(self).values())
                or self.width <= 0 or self.height <= 0):
            raise ValueError("Rectangle dimensions must be positive and all values finite")

    def corners(self):
        cosine, sine = math.cos(math.radians(self.angle)), math.sin(math.radians(self.angle))
        return [(self.cx + x * cosine - y * sine, self.cy + x * sine + y * cosine)
                for x, y in ((-self.width / 2, -self.height / 2),
                             (self.width / 2, -self.height / 2),
                             (self.width / 2, self.height / 2),
                             (-self.width / 2, self.height / 2))]

    def contains(self, point):
        x, y = point[0] - self.cx, point[1] - self.cy
        cosine, sine = math.cos(math.radians(self.angle)), math.sin(math.radians(self.angle))
        return (abs(x * cosine + y * sine) <= self.width / 2
                and abs(-x * sine + y * cosine) <= self.height / 2)

    def rotation_handle(self, distance):
        a, b = self.corners()[:2]
        angle = math.radians(self.angle)
        return ((a[0] + b[0]) / 2 + math.sin(angle) * distance,
                (a[1] + b[1]) / 2 - math.cos(angle) * distance)

    def resize_from_corner(self, corner, point):
        """Keep the opposite corner fixed while dragging one corner."""
        anchor = self.corners()[(corner + 2) % 4]
        dx, dy = point[0] - anchor[0], point[1] - anchor[1]
        cosine, sine = math.cos(math.radians(self.angle)), math.sin(math.radians(self.angle))
        local_x, local_y = dx * cosine + dy * sine, -dx * sine + dy * cosine
        sign_x = -1 if corner in (0, 3) else 1
        sign_y = -1 if corner in (0, 1) else 1
        self.width = max(1.0, sign_x * local_x)
        self.height = max(1.0, sign_y * local_y)
        half_x, half_y = sign_x * self.width / 2, sign_y * self.height / 2
        self.cx = anchor[0] + half_x * cosine - half_y * sine
        self.cy = anchor[1] + half_x * sine + half_y * cosine


class MaskDocument:
    """Original image plus a separate full-resolution binary mask and stroke history."""

    HISTORY_LIMIT = 32
    HISTORY_BYTES = 32 * 1024 * 1024

    def __init__(self, image_path, mask_path):
        self.image_path, self.mask_path = Path(image_path), Path(mask_path)
        if self.image_path.resolve() == self.mask_path.resolve():
            raise ValueError("The mask path must not replace the original image")
        with Image.open(self.image_path) as image:
            self.image = ImageOps.exif_transpose(image).convert("RGB")
        self.rectangles = []
        if self.mask_path.exists():
            with Image.open(self.mask_path) as mask:
                if mask.size != self.image.size:
                    raise ValueError(f"Mask size {mask.size} must match image size {self.image.size}: {self.mask_path}")
                self._base_mask = mask.convert("L").point(lambda value: 255 if value >= 128 else 0)
            self._load_shapes()
            self._recompose()
            self._saved = self._snapshot()
        else:
            self._base_mask = Image.new("L", self.image.size, 0)
            self._recompose()
            self._saved = None
        self.dirty = self._saved is None
        self.undo_history, self.redo_history = deque(), deque()
        self._stroke_before = None

    def _snapshot(self):
        buffer = io.BytesIO()
        self._base_mask.save(buffer, format="PNG", compress_level=1)
        return buffer.getvalue(), tuple((r.cx, r.cy, r.width, r.height, r.angle)
                                      for r in self.rectangles)

    def _restore(self, snapshot):
        mask_png, rectangles = snapshot
        with Image.open(io.BytesIO(mask_png)) as mask:
            self._base_mask = mask.copy()
        self.rectangles = [Rectangle(*values) for values in rectangles]
        self._recompose()
        self.dirty = snapshot != self._saved

    def _remember(self, history, snapshot):
        history.append(snapshot)
        while (len(history) > self.HISTORY_LIMIT
               or sum(len(item[0]) + 40 * len(item[1]) for item in history) > self.HISTORY_BYTES):
            history.popleft()

    @property
    def metadata_path(self):
        return self.mask_path.with_name(self.mask_path.stem + ".mask-editor.json")

    def _recompose(self):
        self.mask = self._base_mask.copy()
        draw = ImageDraw.Draw(self.mask)
        for rectangle in self.rectangles:
            draw.polygon(rectangle.corners(), fill=255)

    def _load_shapes(self):
        if not self.metadata_path.is_file():
            return
        original_mask = self._base_mask.copy()
        try:
            data = json.loads(self.metadata_path.read_text(encoding="utf-8"))
            if data["version"] != 1:
                return
            if data["mask_sha256"] != hashlib.sha256(self._base_mask.tobytes()).hexdigest():
                return  # The PNG was edited outside this editor; keep that PNG authoritative.
            with Image.open(io.BytesIO(base64.b64decode(data["base_png"], validate=True))) as base:
                if base.size != self.image.size or base.mode != "L":
                    return
                rectangles = [Rectangle(**values) for values in data["rectangles"]]
                self._base_mask = base.point(lambda value: 255 if value >= 128 else 0)
                self.rectangles = rectangles
            self._recompose()
            if hashlib.sha256(self.mask.tobytes()).hexdigest() != data["mask_sha256"]:
                self._base_mask = original_mask
                self.rectangles = []
        except (OSError, ValueError, KeyError, TypeError):
            self._base_mask = original_mask
            self.rectangles = []

    def begin_stroke(self):
        if self._stroke_before is None:
            self._stroke_before = self._snapshot()

    def brush(self, start, end, diameter, erase=False):
        diameter = max(1, int(diameter))
        self.begin_stroke()
        value = 0 if erase else 255
        draw = ImageDraw.Draw(self._base_mask)
        points = [(math.floor(x), math.floor(y)) for x, y in (start, end)]
        draw.line(points, fill=value, width=diameter)
        for x, y in points:
            if diameter == 1:
                draw.point((x, y), fill=value)
            else:
                left = x - diameter // 2
                top = y - diameter // 2
                draw.ellipse((left, top, left + diameter - 1, top + diameter - 1), fill=value)
        self._recompose()
        self.dirty = True

    def add_rectangle(self, rectangle):
        self.begin_stroke()
        self.rectangles.append(rectangle)
        self._recompose()
        self.dirty = True
        return len(self.rectangles) - 1

    def update_rectangle(self, index, rectangle):
        self.begin_stroke()
        self.rectangles[index] = rectangle
        self._recompose()
        self.dirty = True

    def remove_rectangle(self, index):
        self.begin_stroke()
        del self.rectangles[index]
        self._recompose()
        self.end_stroke()

    def end_stroke(self):
        if self._stroke_before is None:
            return
        after = self._snapshot()
        if after != self._stroke_before:
            self._remember(self.undo_history, self._stroke_before)
            self.redo_history.clear()
        self._stroke_before = None
        self.dirty = after != self._saved

    def undo(self):
        self.end_stroke()
        if self.undo_history:
            self._remember(self.redo_history, self._snapshot())
            self._restore(self.undo_history.pop())

    def redo(self):
        self.end_stroke()
        if self.redo_history:
            self._remember(self.undo_history, self._snapshot())
            self._restore(self.redo_history.pop())

    def clear(self):
        self.begin_stroke()
        self._base_mask.paste(0, (0, 0, *self._base_mask.size))
        self.rectangles.clear()
        self._recompose()
        self.end_stroke()

    def save(self):
        self.end_stroke()
        self.mask_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.mask_path.with_name(f".{self.mask_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            self.mask.save(temporary, format="PNG")
            temporary.replace(self.mask_path)
        finally:
            if temporary.exists():
                temporary.unlink()
        if self.rectangles:
            buffer = io.BytesIO()
            self._base_mask.save(buffer, format="PNG")
            data = {"version": 1,
                    "mask_sha256": hashlib.sha256(self.mask.tobytes()).hexdigest(),
                    "base_png": base64.b64encode(buffer.getvalue()).decode("ascii"),
                    "rectangles": [asdict(rectangle) for rectangle in self.rectangles]}
            metadata_temp = self.metadata_path.with_name(
                f".{self.metadata_path.name}.{uuid.uuid4().hex}.tmp")
            try:
                metadata_temp.write_text(json.dumps(data), encoding="utf-8")
                metadata_temp.replace(self.metadata_path)
            finally:
                if metadata_temp.exists():
                    metadata_temp.unlink()
        elif self.metadata_path.exists():
            self.metadata_path.unlink()
        self._saved = self._snapshot()
        self.dirty = False

    def preview(self, box=None):
        """The overlay exists only in the display; never export it as the mask or image."""
        image = self.image.crop(box) if box else self.image
        mask = self.mask.crop(box) if box else self.mask
        alpha = mask.point(lambda value: OVERLAY_ALPHA if value else 0)
        color = Image.new("RGB", image.size, OVERLAY_COLOR)
        return Image.composite(color, image, alpha)


class MaskEditor:
    def __init__(self, root, image_dir, mask_dir):
        import tkinter as tk
        from tkinter import messagebox, ttk

        self.root, self.tk, self.messagebox = root, tk, messagebox
        self.images = Path(image_dir).resolve()
        self.pages = editor_pages(self.images, mask_dir)
        self.index = 0
        self.document = MaskDocument(*self.pages[0])
        self.scale = 1.0
        self._last_point = None
        self._erase_stroke = False
        self._render_job = None
        self._photo = None
        self._fitted = False
        self._cursor_position = None
        self.selected_rectangle = None
        self._shape_action = None
        self._shape_start = None
        self._shape_original = None
        self._rotation_offset = 0
        root.title("Mask editor")
        root.geometry("1100x820")
        root.minsize(720, 480)
        root.protocol("WM_DELETE_WINDOW", self.close)

        navigation = ttk.Frame(root, padding=8)
        navigation.pack(fill="x")
        self.previous_button = ttk.Button(navigation, text="Previous", command=lambda: self.navigate(-1))
        self.previous_button.pack(side="left")
        self.next_button = ttk.Button(navigation, text="Next", command=lambda: self.navigate(1))
        self.next_button.pack(side="left", padx=4)
        self.page_choice = ttk.Combobox(navigation, state="readonly",
                                       values=[path.relative_to(self.images).as_posix() for path, _ in self.pages])
        self.page_choice.pack(side="left", fill="x", expand=True, padx=8)
        self.page_choice.bind("<<ComboboxSelected>>", self.choose_page)
        self.save_button = ttk.Button(navigation, text="Save mask", command=self.save)
        self.save_button.pack(side="left", padx=4)
        ttk.Button(navigation, text="Save and next", command=self.save_next).pack(side="left")

        tools = ttk.Frame(root, padding=(8, 0, 8, 8))
        tools.pack(fill="x")
        self.tool = tk.StringVar(value="paint")
        ttk.Radiobutton(tools, text="Paint", variable=self.tool, value="paint",
                        command=self.tool_changed).pack(side="left")
        ttk.Radiobutton(tools, text="Erase", variable=self.tool, value="erase",
                        command=self.tool_changed).pack(side="left", padx=4)
        ttk.Radiobutton(tools, text="Rectangle", variable=self.tool, value="rectangle",
                        command=self.tool_changed).pack(side="left", padx=4)
        ttk.Button(tools, text="Add rectangle", command=self.add_rectangle).pack(side="left", padx=(8, 0))
        ttk.Button(tools, text="Delete rectangle", command=self.delete_rectangle).pack(side="left", padx=4)
        self.brush_size = tk.IntVar(value=24)
        self.brush_label = tk.StringVar(value="Brush: 24 px")
        ttk.Label(tools, textvariable=self.brush_label).pack(side="left", padx=(12, 4))
        self.brush_slider = ttk.Scale(tools, from_=1, to=200, length=160, command=self.set_brush_size)
        self.brush_slider.pack(side="left")
        self.brush_slider.set(24)
        ttk.Button(tools, text="Undo", command=self.undo).pack(side="left", padx=(12, 4))
        ttk.Button(tools, text="Redo", command=self.redo).pack(side="left")
        ttk.Button(tools, text="Clear mask", command=self.clear).pack(side="left", padx=4)

        view_tools = ttk.Frame(root, padding=(8, 0, 8, 8))
        view_tools.pack(fill="x")
        ttk.Button(view_tools, text="Zoom -", command=lambda: self.zoom(1 / 1.25)).pack(side="left")
        ttk.Button(view_tools, text="Zoom +", command=lambda: self.zoom(1.25)).pack(side="left", padx=4)
        ttk.Button(view_tools, text="Fit", command=self.fit).pack(side="left")
        ttk.Button(view_tools, text="100%", command=lambda: self.set_zoom(1.0)).pack(side="left", padx=4)
        self.zoom_label = tk.StringVar()
        ttk.Label(view_tools, textvariable=self.zoom_label).pack(side="left", padx=8)
        ttk.Label(view_tools, text="Rectangle: drag to draw, drag corners to resize, circle to rotate | Ctrl+S = save").pack(side="left")

        viewport = ttk.Frame(root)
        viewport.pack(fill="both", expand=True)
        viewport.rowconfigure(0, weight=1)
        viewport.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(viewport, background="#282831", highlightthickness=0, cursor="arrow")
        self.canvas.grid(row=0, column=0, sticky="nsew")
        horizontal = ttk.Scrollbar(viewport, orient="horizontal", command=self.scroll_x)
        horizontal.grid(row=1, column=0, sticky="ew")
        vertical = ttk.Scrollbar(viewport, orient="vertical", command=self.scroll_y)
        vertical.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(xscrollcommand=horizontal.set, yscrollcommand=vertical.set)
        self._image_item = self.canvas.create_image(0, 0, anchor="nw")
        # A black rim and white outline remain visible on manga and the mask overlay.
        self._cursor_halo = self.canvas.create_oval(0, 0, 0, 0, outline="black", width=3, state="hidden")
        self._cursor_item = self.canvas.create_oval(0, 0, 0, 0, outline="white", width=1, state="hidden")
        self.canvas.bind("<ButtonPress-1>", self.press)
        self.canvas.bind("<B1-Motion>", self.drag)
        self.canvas.bind("<ButtonRelease-1>", self.release)
        self.canvas.bind("<ButtonPress-3>", lambda event: self.press(event, erase=True))
        self.canvas.bind("<B3-Motion>", self.drag)
        self.canvas.bind("<ButtonRelease-3>", self.release)
        self.canvas.bind("<Motion>", self.move_cursor)
        self.canvas.bind("<Enter>", self.move_cursor)
        self.canvas.bind("<Leave>", self.hide_cursor)
        self.canvas.bind("<Configure>", self.resize)
        self.canvas.bind("<MouseWheel>", self.wheel)
        self.canvas.bind("<Button-4>", lambda event: self.zoom(1.25, event))
        self.canvas.bind("<Button-5>", lambda event: self.zoom(1 / 1.25, event))
        self.canvas.bind("<ButtonPress-2>", lambda event: self.canvas.scan_mark(event.x, event.y))
        self.canvas.bind("<B2-Motion>", self.pan)
        for key, action in (("<Control-s>", self.save), ("<Control-z>", self.undo), ("<Control-y>", self.redo)):
            root.bind(key, lambda event, action=action: self.shortcut(action))
        root.bind("<Delete>", lambda event: self.shortcut(self.delete_rectangle))
        root.bind("<bracketleft>", lambda event: self.shortcut(lambda: self.rotate_selected(-15)))
        root.bind("<bracketright>", lambda event: self.shortcut(lambda: self.rotate_selected(15)))
        self.status = tk.StringVar()
        ttk.Label(root, textvariable=self.status, padding=8).pack(fill="x")
        self.update_status()
        root.after_idle(self.fit)

    def shortcut(self, action):
        action()
        return "break"

    def update_status(self, message=None):
        relative = self.document.image_path.relative_to(self.images).as_posix()
        marker = " *" if self.document.dirty else ""
        self.root.title(f"Mask editor - {relative}{marker}")
        self.page_choice.current(self.index)
        self.previous_button.configure(state="normal" if self.index else "disabled")
        self.next_button.configure(state="normal" if self.index + 1 < len(self.pages) else "disabled")
        self.status.set(message or f"Page {self.index + 1}/{len(self.pages)} | {self.document.image.width} x {self.document.image.height} | Mask: {self.document.mask_path}{marker}")

    def set_brush_size(self, value):
        self.brush_size.set(max(1, round(float(value))))
        self.brush_label.set(f"Brush: {self.brush_size.get()} px")
        if hasattr(self, "canvas"):
            self.update_cursor()

    def tool_changed(self):
        self.release()
        self.update_cursor()
        self.schedule_render()

    def point(self, event):
        x, y = self.image_point(event)
        if 0 <= x < self.document.image.width and 0 <= y < self.document.image.height:
            return x, y
        return None

    def image_point(self, event):
        return self.canvas.canvasx(event.x) / self.scale, self.canvas.canvasy(event.y) / self.scale

    def move_cursor(self, event):
        self._cursor_position = event.x, event.y
        self.update_cursor()

    def hide_cursor(self, event=None):
        self._cursor_position = None
        self.update_cursor()

    def update_cursor(self):
        if self._cursor_position is None or self.tool.get() == "rectangle":
            self.canvas.itemconfigure(self._cursor_halo, state="hidden")
            self.canvas.itemconfigure(self._cursor_item, state="hidden")
            self.canvas.configure(cursor="arrow")
            return
        pointer_x, pointer_y = self._cursor_position
        x, y = self.canvas.canvasx(pointer_x), self.canvas.canvasy(pointer_y)
        if not (0 <= pointer_x < self.canvas.winfo_width()
                and 0 <= pointer_y < self.canvas.winfo_height()
                and 0 <= x < self.document.image.width * self.scale
                and 0 <= y < self.document.image.height * self.scale):
            self.canvas.itemconfigure(self._cursor_halo, state="hidden")
            self.canvas.itemconfigure(self._cursor_item, state="hidden")
            self.canvas.configure(cursor="arrow")
            return
        radius = self.brush_size.get() * self.scale / 2
        for item in (self._cursor_halo, self._cursor_item):
            self.canvas.coords(item, x-radius, y-radius, x+radius, y+radius)
            self.canvas.itemconfigure(item, state="normal")
            self.canvas.tag_raise(item)
        self.canvas.configure(cursor="none")

    def press(self, event, erase=False):
        self.release()
        self.canvas.focus_set()
        if self.tool.get() == "rectangle" and not erase:
            self.press_rectangle(event)
            self.move_cursor(event)
            return
        self._last_point = self.point(event)
        self._erase_stroke = erase or self.tool.get() == "erase"
        if self._last_point is not None:
            self.document.brush(self._last_point, self._last_point, self.brush_size.get(), self._erase_stroke)
            self.update_status()
            self.schedule_render()
        self.move_cursor(event)

    def drag(self, event):
        if self._shape_action is not None:
            self.drag_rectangle(event)
            self.move_cursor(event)
            return
        point = self.point(event)
        if point is not None:
            self.document.brush(self._last_point or point, point, self.brush_size.get(), self._erase_stroke)
            self.schedule_render()
        self._last_point = point
        self.move_cursor(event)

    def release(self, event=None):
        if self._shape_action is not None:
            if event is not None:
                self.drag_rectangle(event)
            self.document.end_stroke()
            self._shape_action = None
            self._shape_original = None
            self.update_status()
            self.schedule_render()
            return
        if event is not None and self._last_point is not None:
            self.drag(event)
        self.document.end_stroke()
        self._last_point = None
        self.update_status()

    def add_rectangle(self):
        self.release()
        width, height = self.document.image.size
        rectangle = Rectangle(width / 2, height / 2, max(1, width / 4), max(1, height / 4))
        self.selected_rectangle = self.document.add_rectangle(rectangle)
        self.document.end_stroke()
        self.tool.set("rectangle")
        self.tool_changed()
        self.update_status()
        self.schedule_render()

    def delete_rectangle(self):
        self.release()
        if self.tool.get() != "rectangle" or self.selected_rectangle is None:
            return
        if self.selected_rectangle < len(self.document.rectangles):
            self.document.remove_rectangle(self.selected_rectangle)
        self.selected_rectangle = None
        self.update_status()
        self.schedule_render()

    def rotate_selected(self, degrees):
        self.release()
        if self.tool.get() != "rectangle" or self.selected_rectangle is None:
            return
        if self.selected_rectangle >= len(self.document.rectangles):
            return
        rectangle = replace(self.document.rectangles[self.selected_rectangle])
        rectangle.angle = (rectangle.angle + degrees) % 360
        self.document.update_rectangle(self.selected_rectangle, rectangle)
        self.document.end_stroke()
        self.update_status()
        self.schedule_render()

    def rotation_handle(self, rectangle):
        """Choose a reachable rotation handle near the top or bottom edge."""
        distance = min(28 / self.scale, min(self.document.image.size) / 4)
        corners = rectangle.corners()
        options = ((rectangle.rotation_handle(distance),
                    ((corners[0][0] + corners[1][0]) / 2,
                     (corners[0][1] + corners[1][1]) / 2)),
                   (rectangle.rotation_handle(-rectangle.height - distance),
                    ((corners[2][0] + corners[3][0]) / 2,
                     (corners[2][1] + corners[3][1]) / 2)))
        for point, anchor in options:
            if 0 <= point[0] < self.document.image.width and 0 <= point[1] < self.document.image.height:
                return point, anchor
        return options[0]

    def press_rectangle(self, event):
        point = self.image_point(event)
        handle_radius = 9 / self.scale
        index = self.selected_rectangle
        if index is not None and index < len(self.document.rectangles):
            rectangle = self.document.rectangles[index]
            if math.dist(point, self.rotation_handle(rectangle)[0]) <= handle_radius:
                self._shape_action = "rotate"
                self._rotation_offset = (rectangle.angle - math.degrees(
                    math.atan2(point[1] - rectangle.cy, point[0] - rectangle.cx)))
            else:
                corners = rectangle.corners()
                corner = min(range(4), key=lambda i: math.dist(point, corners[i]))
                distance = math.dist(point, corners[corner])
                if (distance <= handle_radius
                        and distance <= math.dist(point, (rectangle.cx, rectangle.cy))):
                    self._shape_action = ("resize", corner)
        if self._shape_action is None:
            for index in range(len(self.document.rectangles) - 1, -1, -1):
                if self.document.rectangles[index].contains(point):
                    self.selected_rectangle = index
                    self._shape_action = "move"
                    break
        if self._shape_action is None:
            if self.point(event) is None:
                self.selected_rectangle = None
                self.schedule_render()
                return
            self.selected_rectangle = self.document.add_rectangle(Rectangle(*point, 1, 1))
            self._shape_action = "create"
        self._shape_start = point
        self._shape_original = replace(self.document.rectangles[self.selected_rectangle])
        self.document.begin_stroke()
        self.schedule_render()

    def drag_rectangle(self, event):
        point = self.image_point(event)
        original = self._shape_original
        rectangle = replace(original)
        if self._shape_action == "create":
            rectangle.cx = (self._shape_start[0] + point[0]) / 2
            rectangle.cy = (self._shape_start[1] + point[1]) / 2
            rectangle.width = max(1, abs(point[0] - self._shape_start[0]))
            rectangle.height = max(1, abs(point[1] - self._shape_start[1]))
        elif self._shape_action == "move":
            rectangle.cx += point[0] - self._shape_start[0]
            rectangle.cy += point[1] - self._shape_start[1]
        elif self._shape_action == "rotate":
            rectangle.angle = (math.degrees(math.atan2(point[1] - rectangle.cy,
                                                       point[0] - rectangle.cx))
                               + self._rotation_offset) % 360
        else:
            rectangle.resize_from_corner(self._shape_action[1], point)
        self.document.update_rectangle(self.selected_rectangle, rectangle)
        self.update_status()
        self.schedule_render()

    def undo(self):
        self.release()
        self.document.undo()
        self.selected_rectangle = None
        self.update_status()
        self.schedule_render()

    def redo(self):
        self.release()
        self.document.redo()
        self.selected_rectangle = None
        self.update_status()
        self.schedule_render()

    def clear(self):
        self.release()
        self.document.clear()
        self.selected_rectangle = None
        self.update_status()
        self.schedule_render()

    def save(self):
        self.release()
        try:
            self.document.save()
        except OSError as exc:
            self.messagebox.showerror("Could not save mask", str(exc), parent=self.root)
            return False
        self.update_status(f"Saved mask: {self.document.mask_path}")
        print(f"Saved edited mask: {self.document.mask_path}", flush=True)
        return True

    def confirm_page_change(self):
        self.release()
        if not self.document.dirty:
            return True
        choice = self.messagebox.askyesnocancel("Unsaved mask", "Save changes to this mask?", parent=self.root)
        if choice is None:
            return False
        return self.save() if choice else True

    def load_page(self, index):
        if not 0 <= index < len(self.pages) or index == self.index:
            self.page_choice.current(self.index)
            return
        if not self.confirm_page_change():
            self.page_choice.current(self.index)
            return
        try:
            document = MaskDocument(*self.pages[index])
        except (OSError, ValueError) as exc:
            self.messagebox.showerror("Could not load page", str(exc), parent=self.root)
            self.page_choice.current(self.index)
            return
        self.document, self.index = document, index
        self.selected_rectangle = None
        self.hide_cursor()
        self.fit()
        self.update_status()

    def navigate(self, step):
        self.load_page(self.index + step)

    def choose_page(self, event):
        self.load_page(self.page_choice.current())

    def save_next(self):
        if self.save():
            self.navigate(1)

    def close(self):
        if self.confirm_page_change():
            if self._render_job is not None:
                self.root.after_cancel(self._render_job)
            self.root.destroy()

    def fit(self):
        width, height = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        self.scale = min(1.0, width / self.document.image.width, height / self.document.image.height)
        self._fitted = width > 1 and height > 1
        self.configure_view()
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)
        self.schedule_render()

    def configure_view(self):
        self.canvas.configure(scrollregion=(0, 0, math.ceil(self.document.image.width * self.scale),
                                             math.ceil(self.document.image.height * self.scale)))
        self.zoom_label.set(f"{self.scale:.0%}")

    def set_zoom(self, scale, event=None):
        self.release()
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        anchor_x, anchor_y = (event.x, event.y) if event else (width / 2, height / 2)
        source_x = self.canvas.canvasx(anchor_x) / self.scale
        source_y = self.canvas.canvasy(anchor_y) / self.scale
        self.scale = max(0.02, min(8.0, scale))
        self.configure_view()
        self.canvas.xview_moveto(max(0, source_x*self.scale-anchor_x) / (self.document.image.width*self.scale))
        self.canvas.yview_moveto(max(0, source_y*self.scale-anchor_y) / (self.document.image.height*self.scale))
        self.schedule_render()

    def zoom(self, factor, event=None):
        self.set_zoom(self.scale * factor, event)

    def wheel(self, event):
        self.zoom(1.25 if event.delta > 0 else 1 / 1.25, event)
        return "break"

    def scroll_x(self, *args):
        self.canvas.xview(*args)
        self.schedule_render()

    def scroll_y(self, *args):
        self.canvas.yview(*args)
        self.schedule_render()

    def pan(self, event):
        self.canvas.scan_dragto(event.x, event.y, gain=1)
        self.move_cursor(event)
        self.schedule_render()

    def resize(self, event):
        if not self._fitted:
            self.fit()
        else:
            self.schedule_render()

    def schedule_render(self):
        self.update_cursor()
        if self._render_job is None:
            self._render_job = self.root.after(16, self.render)

    def render(self):
        from PIL import ImageTk

        self._render_job = None
        # Render only the visible source crop. Zooming a large manga page does not
        # allocate a PhotoImage for the entire enlarged page.
        left = max(0, math.floor(self.canvas.canvasx(0) / self.scale))
        top = max(0, math.floor(self.canvas.canvasy(0) / self.scale))
        right = min(self.document.image.width, math.ceil(self.canvas.canvasx(self.canvas.winfo_width()) / self.scale))
        bottom = min(self.document.image.height, math.ceil(self.canvas.canvasy(self.canvas.winfo_height()) / self.scale))
        self.update_cursor()
        if right <= left or bottom <= top:
            self.render_shapes()
            return
        preview = self.document.preview((left, top, right, bottom))
        size = (max(1, round((right-left)*self.scale)), max(1, round((bottom-top)*self.scale)))
        preview = preview.resize(size, Image.Resampling.BILINEAR)
        self._photo = ImageTk.PhotoImage(preview, master=self.root)
        self.canvas.coords(self._image_item, left*self.scale, top*self.scale)
        self.canvas.itemconfigure(self._image_item, image=self._photo)
        self.render_shapes()
        self.canvas.tag_raise(self._cursor_halo)
        self.canvas.tag_raise(self._cursor_item)

    def render_shapes(self):
        self.canvas.delete("rectangle-ui")
        if self.tool.get() != "rectangle":
            return
        for index, rectangle in enumerate(self.document.rectangles):
            corners = rectangle.corners()
            scaled = [(x * self.scale, y * self.scale) for x, y in corners]
            self.canvas.create_polygon(*scaled, fill="", outline="#39d8ed",
                                       width=2 if index == self.selected_rectangle else 1,
                                       tags="rectangle-ui")
            if index != self.selected_rectangle:
                continue
            rotation, anchor = self.rotation_handle(rectangle)
            rx, ry = rotation[0] * self.scale, rotation[1] * self.scale
            self.canvas.create_line(anchor[0] * self.scale, anchor[1] * self.scale,
                                    rx, ry, fill="#39d8ed", width=2,
                                    tags="rectangle-ui")
            self.canvas.create_oval(rx - 6, ry - 6, rx + 6, ry + 6,
                                    fill="#39d8ed", outline="black", tags="rectangle-ui")
            for x, y in scaled:
                self.canvas.create_rectangle(x - 6, y - 6, x + 6, y + 6,
                                             fill="white", outline="black", tags="rectangle-ui")


def launch_editor(image_dir, mask_dir):
    editor_pages(image_dir, mask_dir)  # Validate before creating a window.
    try:
        import tkinter as tk
    except ImportError as exc:
        raise RuntimeError("The mask editor requires Tkinter. Install Python's Tk support.") from exc
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        raise RuntimeError("Could not open the mask editor. A desktop display and Tk support are required.") from exc
    try:
        MaskEditor(root, image_dir, mask_dir)
        root.mainloop()
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass
