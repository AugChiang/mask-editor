"""Desktop editor for full-resolution binary PNG masks."""

from collections import deque
import io
import math
from pathlib import Path
import uuid

from PIL import Image, ImageDraw, ImageOps


OVERLAY_COLOR = (255, 64, 160)
OVERLAY_ALPHA = 100


def editor_pages(image_dir, mask_dir):
    images, masks = Path(image_dir).resolve(), Path(mask_dir).resolve()
    if images == masks or images in masks.parents or masks in images.parents:
        raise ValueError("Image and mask directories must not overlap")
    if not images.is_dir():
        raise ValueError(f"Image directory does not exist: {images}")
    pages = [(path, masks / path.relative_to(images))
             for path in sorted(images.rglob("*")) if path.is_file() and path.suffix.lower() == ".png"]
    if not pages:
        raise ValueError(f"No PNG images found in {images}")
    return pages


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
        if self.mask_path.exists():
            with Image.open(self.mask_path) as mask:
                if mask.size != self.image.size:
                    raise ValueError(f"Mask size {mask.size} must match image size {self.image.size}: {self.mask_path}")
                self.mask = mask.convert("L").point(lambda value: 255 if value >= 128 else 0)
            self._saved = self._snapshot()
        else:
            self.mask = Image.new("L", self.image.size, 0)
            self._saved = None
        self.dirty = self._saved is None
        self.undo_history, self.redo_history = deque(), deque()
        self._stroke_before = None

    def _snapshot(self):
        buffer = io.BytesIO()
        self.mask.save(buffer, format="PNG", compress_level=1)
        return buffer.getvalue()

    def _restore(self, snapshot):
        with Image.open(io.BytesIO(snapshot)) as mask:
            self.mask = mask.copy()
        self.dirty = snapshot != self._saved

    def _remember(self, history, snapshot):
        history.append(snapshot)
        while len(history) > self.HISTORY_LIMIT or sum(map(len, history)) > self.HISTORY_BYTES:
            history.popleft()

    def begin_stroke(self):
        if self._stroke_before is None:
            self._stroke_before = self._snapshot()

    def brush(self, start, end, diameter, erase=False):
        diameter = max(1, int(diameter))
        self.begin_stroke()
        value = 0 if erase else 255
        draw = ImageDraw.Draw(self.mask)
        points = [(math.floor(x), math.floor(y)) for x, y in (start, end)]
        draw.line(points, fill=value, width=diameter)
        for x, y in points:
            if diameter == 1:
                draw.point((x, y), fill=value)
            else:
                left = x - diameter // 2
                top = y - diameter // 2
                draw.ellipse((left, top, left + diameter - 1, top + diameter - 1), fill=value)
        self.dirty = True

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
        self.mask.paste(0, (0, 0, *self.mask.size))
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
        ttk.Radiobutton(tools, text="Paint", variable=self.tool, value="paint").pack(side="left")
        ttk.Radiobutton(tools, text="Erase", variable=self.tool, value="erase").pack(side="left", padx=4)
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
        ttk.Label(view_tools, text="Magenta = inpaint | right-drag = erase | middle-drag = pan | Ctrl+S = save").pack(side="left")

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

    def point(self, event):
        x, y = self.canvas.canvasx(event.x) / self.scale, self.canvas.canvasy(event.y) / self.scale
        if 0 <= x < self.document.image.width and 0 <= y < self.document.image.height:
            return x, y
        return None

    def move_cursor(self, event):
        self._cursor_position = event.x, event.y
        self.update_cursor()

    def hide_cursor(self, event=None):
        self._cursor_position = None
        self.update_cursor()

    def update_cursor(self):
        if self._cursor_position is None:
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
        self._last_point = self.point(event)
        self._erase_stroke = erase or self.tool.get() == "erase"
        if self._last_point is not None:
            self.document.brush(self._last_point, self._last_point, self.brush_size.get(), self._erase_stroke)
            self.update_status()
            self.schedule_render()
        self.move_cursor(event)

    def drag(self, event):
        point = self.point(event)
        if point is not None:
            self.document.brush(self._last_point or point, point, self.brush_size.get(), self._erase_stroke)
            self.schedule_render()
        self._last_point = point
        self.move_cursor(event)

    def release(self, event=None):
        if event is not None and self._last_point is not None:
            self.drag(event)
        self.document.end_stroke()
        self._last_point = None
        self.update_status()

    def undo(self):
        self.release()
        self.document.undo()
        self.update_status()
        self.schedule_render()

    def redo(self):
        self.release()
        self.document.redo()
        self.update_status()
        self.schedule_render()

    def clear(self):
        self.release()
        self.document.clear()
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
            return
        preview = self.document.preview((left, top, right, bottom))
        size = (max(1, round((right-left)*self.scale)), max(1, round((bottom-top)*self.scale)))
        preview = preview.resize(size, Image.Resampling.BILINEAR)
        self._photo = ImageTk.PhotoImage(preview, master=self.root)
        self.canvas.coords(self._image_item, left*self.scale, top*self.scale)
        self.canvas.itemconfigure(self._image_item, image=self._photo)
        self.canvas.tag_raise(self._cursor_halo)
        self.canvas.tag_raise(self._cursor_item)


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
