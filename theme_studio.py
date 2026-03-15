from __future__ import annotations

from pathlib import Path
import math
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
import webbrowser

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover - optional runtime acceleration
    cv2 = None
    np = None

from PIL import Image, ImageDraw, ImageFilter, ImageTk

from theme_studio_core import APP_ROOT, ThemeStudio, StudioError, WORK_INPUTS, _detect_saved_artwork_format


BG = "#edf2f7"
CARD = "#ffffff"
ACCENT = "#0f6fff"
ACCENT_LIGHT = "#eaf2ff"
TEXT = "#162031"
MUTED = "#5f6b7a"
SOFT_BG = "#f5f8fc"
BORDER = "#d7e0eb"
PRIMARY_ACTIVE = "#0b5de3"
SECONDARY_BG = "#f4f7fb"
SECONDARY_ACTIVE = "#e4ebf4"
SECONDARY_TEXT = "#204a8f"
LOG_BG = "#0f1726"
LOG_TEXT = "#e7eef9"
STATUS_BG = "#163e76"
STATUS_TEXT = "#d9e8ff"
PREVIEW_CANVAS_WIDTH = 360
PREVIEW_CANVAS_HEIGHT = 320
CROP_CANVAS_WIDTH = 520
CROP_CANVAS_HEIGHT = 420
APP_ICON_PNG = APP_ROOT / "studio_icon.png"
APP_ICON_ICO = APP_ROOT / "studio_icon.ico"
IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"
UI_FONT_FAMILY = "Helvetica Neue" if IS_MACOS else "Segoe UI"
MONO_FONT_FAMILY = "Menlo" if IS_MACOS else "Consolas"
IMAGE_FILE_TYPES = [
    ("Image files", ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.bmp")),
    ("PNG image", "*.png"),
    ("JPEG image", ("*.jpg", "*.jpeg")),
    ("All files", "*"),
]


def ui_font(size: int, bold: bool = False) -> tuple[str, int, str]:
    return (UI_FONT_FAMILY, size, "bold" if bold else "normal")


def mono_font(size: int) -> tuple[str, int]:
    return (MONO_FONT_FAMILY, size)


def image_color_count(image: Image.Image) -> int:
    return len(set(image.convert("RGBA").getdata()))


def rgb565_like_image(image: Image.Image) -> Image.Image:
    rgb = image.convert("RGB")
    if np is not None:
        data = np.array(rgb, dtype=np.uint8)
        data[..., 0] = (data[..., 0] >> 3) << 3
        data[..., 1] = (data[..., 1] >> 2) << 2
        data[..., 2] = (data[..., 2] >> 3) << 3
        return Image.fromarray(data, "RGB").convert("RGBA")

    red, green, blue = rgb.split()
    red = red.point(lambda value: (value >> 3) << 3)
    green = green.point(lambda value: (value >> 2) << 2)
    blue = blue.point(lambda value: (value >> 3) << 3)
    return Image.merge("RGB", (red, green, blue)).convert("RGBA")


def manual_format_conversion_actions() -> list[tuple[str, str, bool]]:
    return [
        ("0565", "转到 0565（RGB565）", True),
        ("0065", "转到 0065（<=65535 色）", False),
        ("0064", "转到 0064（<=255 色）", False),
        ("0008", "转到 0008（8 位灰度）", False),
        ("0004", "转到 0004（4 位灰度）", False),
        ("cancel", "取消", False),
    ]


def manual_format_conversion_actions_for(current_format: str) -> list[tuple[str, str, bool]]:
    mapping = {
        "1888": ["0565", "0065", "0064", "0008", "0004"],
        "0565": ["0065", "0064", "0008", "0004"],
        "0065": ["0064", "0008", "0004"],
        "0064": ["0008", "0004"],
        "0008": ["0004"],
    }
    labels = {
        "0565": "转到 0565（RGB565）",
        "0065": "转到 0065（<=65535 色）",
        "0064": "转到 0064（<=255 色）",
        "0008": "转到 0008（8 位灰度）",
        "0004": "转到 0004（4 位灰度）",
    }
    targets = mapping.get(current_format, [])
    if not targets:
        return [("cancel", "取消", False)]
    return [(targets[0], labels[targets[0]], True)] + [
        (target, labels[target], False) for target in targets[1:]
    ] + [("cancel", "取消", False)]


LOW_FORMAT_ORDER = ["0004", "0008", "0064", "0065", "0565"]
FORMAT_RANK = {fmt: index for index, fmt in enumerate(LOW_FORMAT_ORDER + ["1888"])}


def full_conversion_actions(target_format: str) -> list[tuple[str, str, bool]]:
    mapping = {
        "0004": [("0004", "转到 0004", False), ("0008", "转到 0008", False), ("0064", "转到 0064", False), ("0065", "转到 0065", True), ("1888", "保持 1888", False)],
        "0008": [("0008", "转到 0008", False), ("0064", "转到 0064", False), ("0065", "转到 0065", True), ("1888", "保持 1888", False)],
        "0064": [("0064", "转到 0064", False), ("0065", "转到 0065", True), ("1888", "保持 1888", False)],
        "0065": [("0065", "转到 0065", True), ("1888", "保持 1888", False)],
        "0565": [("0565", "转到 0565", True), ("0065", "转到 0065", False), ("0064", "转到 0064", False), ("0008", "转到 0008", False), ("0004", "转到 0004", False), ("1888", "保持 1888", False)],
    }
    return mapping.get(target_format, [("1888", "保持 1888", True)]) + [("cancel", "取消", False)]


def lower_format_actions(direct_format: str) -> list[tuple[str, str, bool]]:
    if direct_format not in LOW_FORMAT_ORDER:
        return []
    direct_index = LOW_FORMAT_ORDER.index(direct_format)
    return [
        (fmt, f"尝试降到 {fmt}", False)
        for fmt in reversed(LOW_FORMAT_ORDER[:direct_index])
    ]


def natural_low_color_hint(image_format: str) -> str:
    if image_format in {"0064", "0065"}:
        return (
            f"\n\n这通常表示缩放后的颜色数自然落入 {image_format} 范围，"
            "并不是额外做了强制降色；视觉上一般不会比 1888 少，但体积通常更省。"
        )
    if image_format == "0565":
        return "\n\n这通常表示当前图片已经接近 RGB565 色阶，继续写回该格式会更省空间。"
    return ""


def button_style(variant: str = "secondary") -> dict[str, object]:
    if variant == "primary":
        if IS_MACOS:
            return {
                "bg": "#dce9ff",
                "fg": SECONDARY_TEXT,
                "activebackground": "#cfe0ff",
                "activeforeground": SECONDARY_TEXT,
            }
        return {
            "bg": ACCENT,
            "fg": "white",
            "activebackground": PRIMARY_ACTIVE,
            "activeforeground": "white",
        }
    return {
        "bg": SECONDARY_BG,
        "fg": SECONDARY_TEXT,
        "activebackground": SECONDARY_ACTIVE,
        "activeforeground": SECONDARY_TEXT,
    }


def open_in_file_manager(path: Path, parent: tk.Misc | None = None) -> None:
    try:
        if hasattr(os, "startfile"):
            os.startfile(str(path))  # type: ignore[attr-defined]
            return

        if IS_MACOS:
            subprocess.run(["open", str(path)], check=True)
            return

        opener = shutil.which("xdg-open")
        if opener:
            subprocess.run([opener, str(path)], check=True)
            return

        raise OSError("当前系统没有可用的文件管理器启动命令。")
    except Exception as exc:
        messagebox.showerror("打开目录失败", f"无法打开目录：\n{path}\n\n{exc}", parent=parent)


class CropResizeDialog:
    def __init__(self, parent: tk.Tk, source_path: Path, target_name: str, target_size: tuple[int, int]) -> None:
        self.parent = parent
        self.source_path = source_path
        self.target_name = target_name
        self.target_size = target_size
        with Image.open(source_path) as image:
            self.source_image = image.convert("RGBA")

        self.dialog = tk.Toplevel(parent)
        self.dialog.title("裁剪并缩放")
        self.dialog.geometry("760x720")
        self.dialog.minsize(680, 640)
        self.dialog.configure(bg=BG)
        self.dialog.transient(parent)
        self.dialog.grab_set()

        self.preview_image: ImageTk.PhotoImage | None = None
        self.result_path: Path | None = None
        self.drag_start: tuple[int, int] | None = None
        self.base_scale = 1.0
        self.offset_x = 0.0
        self.offset_y = 0.0
        self.crop_frame = (0.0, 0.0, 0.0, 0.0)

        self.zoom_var = tk.DoubleVar(value=1.0)
        self.zoom_label_var = tk.StringVar(value="缩放 100%")

        self._build_layout()
        self._update_crop_frame()
        self._render()

    def _build_layout(self) -> None:
        container = tk.Frame(self.dialog, bg=CARD, padx=18, pady=18)
        container.pack(fill="both", expand=True, padx=16, pady=16)
        container.grid_rowconfigure(3, weight=1)
        container.grid_columnconfigure(0, weight=1)
        container.rowconfigure(1, weight=1)
        container.columnconfigure(0, weight=1)

        tk.Label(
            container,
            text="裁剪并缩放到目标分辨率",
            bg=CARD,
            fg=TEXT,
            font=("Segoe UI Semibold", 17),
        ).grid(row=0, column=0, sticky="w")

        self.preview_canvas = tk.Canvas(
            container,
            width=CROP_CANVAS_WIDTH,
            height=CROP_CANVAS_HEIGHT,
            bg="#10211f",
            highlightthickness=0,
            relief="flat",
        )
        self.preview_canvas.grid(row=1, column=0, sticky="nsew", pady=(14, 0))
        self.preview_canvas.bind("<ButtonPress-1>", self._on_drag_start)
        self.preview_canvas.bind("<B1-Motion>", self._on_drag)
        self.preview_canvas.bind("<ButtonRelease-1>", self._on_drag_end)
        self.preview_canvas.bind("<MouseWheel>", self._on_mouse_wheel)
        self.preview_canvas.bind("<Button-4>", self._on_mouse_wheel)
        self.preview_canvas.bind("<Button-5>", self._on_mouse_wheel)
        self.preview_canvas.bind("<Configure>", self._on_canvas_resize)

        tk.Label(
            container,
            text=(
                f"目标素材：{self.target_name}\n"
                f"目标分辨率：{self.target_size[0]}x{self.target_size[1]}\n"
                "拖动图片调整取景，滚轮或滑块控制缩放。半透明框内的内容会被输出成可直接替换的 PNG。"
            ),
            bg=CARD,
            fg=TEXT,
            justify="left",
            wraplength=700,
            font=("Segoe UI", 10),
        ).grid(row=2, column=0, sticky="ew", pady=(14, 0))

        controls = tk.Frame(container, bg=CARD)
        controls.grid(row=3, column=0, sticky="ew", pady=(16, 0))
        controls.columnconfigure(1, weight=1)

        tk.Label(
            controls,
            textvariable=self.zoom_label_var,
            bg=CARD,
            fg=TEXT,
            font=("Segoe UI Semibold", 10),
        ).grid(row=0, column=0, sticky="w", padx=(0, 12))

        zoom_scale = tk.Scale(
            controls,
            from_=1.0,
            to=6.0,
            resolution=0.05,
            orient="horizontal",
            variable=self.zoom_var,
            command=self._on_zoom_changed,
            bg=CARD,
            fg=TEXT,
            highlightthickness=0,
            troughcolor="#d7e7df",
            activebackground=ACCENT,
        )
        zoom_scale.grid(row=0, column=1, sticky="ew")

        buttons = tk.Frame(container, bg=CARD)
        buttons.grid(row=4, column=0, sticky="ew", pady=(18, 0))

        tk.Button(
            buttons,
            text="重置取景",
            command=self._reset_view,
            **button_style("secondary"),
            relief="flat",
            bd=0,
            padx=14,
            pady=9,
            font=("Segoe UI Semibold", 10),
        ).pack(side="left")

        tk.Button(
            buttons,
            text="取消",
            command=self._cancel,
            **button_style("secondary"),
            relief="flat",
            bd=0,
            padx=14,
            pady=9,
            font=("Segoe UI Semibold", 10),
        ).pack(side="right")

        tk.Button(
            buttons,
            text="生成替换图",
            command=self._confirm,
            **button_style("primary"),
            relief="flat",
            bd=0,
            padx=16,
            pady=9,
            font=("Segoe UI Semibold", 10),
        ).pack(side="right", padx=(0, 10))

    def show(self) -> Path | None:
        self.dialog.wait_window()
        return self.result_path

    def _display_scale(self) -> float:
        return self.base_scale * self.zoom_var.get()

    def _update_crop_frame(self) -> None:
        canvas_width = max(self.preview_canvas.winfo_width(), CROP_CANVAS_WIDTH)
        canvas_height = max(self.preview_canvas.winfo_height(), CROP_CANVAS_HEIGHT)
        target_width, target_height = self.target_size

        max_width = canvas_width - 48
        max_height = canvas_height - 48
        fit = min(max_width / target_width, max_height / target_height)
        crop_width = max(120.0, target_width * fit)
        crop_height = max(120.0, target_height * fit)

        left = (canvas_width - crop_width) / 2
        top = (canvas_height - crop_height) / 2
        self.crop_frame = (left, top, left + crop_width, top + crop_height)
        self.base_scale = max(crop_width / self.source_image.width, crop_height / self.source_image.height)
        self._clamp_offsets()

    def _render(self) -> None:
        canvas_width = max(self.preview_canvas.winfo_width(), CROP_CANVAS_WIDTH)
        canvas_height = max(self.preview_canvas.winfo_height(), CROP_CANVAS_HEIGHT)
        left, top, right, bottom = self.crop_frame
        crop_width = right - left
        crop_height = bottom - top
        scale = self._display_scale()

        render_width = max(1, int(round(self.source_image.width * scale)))
        render_height = max(1, int(round(self.source_image.height * scale)))
        image_center_x = canvas_width / 2 + self.offset_x
        image_center_y = canvas_height / 2 + self.offset_y
        image_left = int(round(image_center_x - render_width / 2))
        image_top = int(round(image_center_y - render_height / 2))

        composed = Image.new("RGBA", (canvas_width, canvas_height), "#10211f")
        preview = self.source_image.resize((render_width, render_height), Image.Resampling.LANCZOS)
        composed.alpha_composite(preview, (image_left, image_top))

        overlay = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        shade = (0, 0, 0, 155)
        draw.rectangle((0, 0, canvas_width, top), fill=shade)
        draw.rectangle((0, bottom, canvas_width, canvas_height), fill=shade)
        draw.rectangle((0, top, left, bottom), fill=shade)
        draw.rectangle((right, top, canvas_width, bottom), fill=shade)
        draw.rectangle((left, top, right, bottom), outline=(255, 255, 255, 235), width=3)
        draw.text((left + 12, top + 12), f"{self.target_size[0]}x{self.target_size[1]}", fill=(255, 255, 255, 220))
        composed = Image.alpha_composite(composed, overlay)

        self.preview_image = ImageTk.PhotoImage(composed)
        self.preview_canvas.delete("all")
        self.preview_canvas.create_image(0, 0, image=self.preview_image, anchor="nw")
        self.preview_canvas.create_text(
            canvas_width / 2,
            canvas_height - 14,
            text="拖动图片调整位置，滚轮可缩放",
            fill="#d7ebe4",
            font=("Segoe UI", 10),
        )

        zoom_percent = int(round(self.zoom_var.get() * 100))
        self.zoom_label_var.set(f"缩放 {zoom_percent}%")

    def _clamp_offsets(self) -> None:
        left, top, right, bottom = self.crop_frame
        crop_width = right - left
        crop_height = bottom - top
        render_width = self.source_image.width * self._display_scale()
        render_height = self.source_image.height * self._display_scale()
        max_offset_x = max(0.0, (render_width - crop_width) / 2)
        max_offset_y = max(0.0, (render_height - crop_height) / 2)
        self.offset_x = min(max(self.offset_x, -max_offset_x), max_offset_x)
        self.offset_y = min(max(self.offset_y, -max_offset_y), max_offset_y)

    def _reset_view(self) -> None:
        self.zoom_var.set(1.0)
        self.offset_x = 0.0
        self.offset_y = 0.0
        self._update_crop_frame()
        self._render()

    def _on_zoom_changed(self, _value=None) -> None:
        self._clamp_offsets()
        self._render()

    def _on_mouse_wheel(self, event) -> None:
        if getattr(event, "num", None) == 4:
            delta = 0.1
        elif getattr(event, "num", None) == 5:
            delta = -0.1
        elif getattr(event, "delta", 0) == 0:
            return
        else:
            delta = 0.1 if event.delta > 0 else -0.1
        next_zoom = min(6.0, max(1.0, self.zoom_var.get() + delta))
        self.zoom_var.set(next_zoom)
        self._on_zoom_changed()

    def _on_drag_start(self, event) -> None:
        self.drag_start = (event.x, event.y)

    def _on_drag(self, event) -> None:
        if self.drag_start is None:
            self.drag_start = (event.x, event.y)
            return

        last_x, last_y = self.drag_start
        self.offset_x += event.x - last_x
        self.offset_y += event.y - last_y
        self.drag_start = (event.x, event.y)
        self._clamp_offsets()
        self._render()

    def _on_drag_end(self, _event=None) -> None:
        self.drag_start = None

    def _on_canvas_resize(self, _event=None) -> None:
        self._update_crop_frame()
        self._render()

    def _confirm(self) -> None:
        try:
            self.result_path = self._export_image()
        except Exception as exc:  # pragma: no cover - modal fallback
            messagebox.showerror("裁剪失败", str(exc), parent=self.dialog)
            return
        self.dialog.destroy()

    def _cancel(self) -> None:
        self.result_path = None
        self.dialog.destroy()

    def _high_quality_resize(self, image: Image.Image, target_size: tuple[int, int]) -> Image.Image:
        if cv2 is not None and np is not None:
            rgba = np.array(image.convert("RGBA"))
            bgra = cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA)
            interpolation = cv2.INTER_AREA
            if image.width < target_size[0] or image.height < target_size[1]:
                interpolation = cv2.INTER_LANCZOS4
            resized = cv2.resize(bgra, target_size, interpolation=interpolation)
            rgba_resized = cv2.cvtColor(resized, cv2.COLOR_BGRA2RGBA)
            result = Image.fromarray(rgba_resized, "RGBA")
            if image.width / target_size[0] >= 2 or image.height / target_size[1] >= 2:
                result = result.filter(ImageFilter.UnsharpMask(radius=0.4, percent=85, threshold=2))
            return result

        resized = image
        target_width, target_height = target_size

        # Downsample in stages to preserve more detail than a single large shrink.
        while resized.width // 2 >= target_width and resized.height // 2 >= target_height:
            next_width = max(target_width, resized.width // 2)
            next_height = max(target_height, resized.height // 2)
            resized = resized.resize((next_width, next_height), Image.Resampling.BOX)

        resized = resized.resize(target_size, Image.Resampling.LANCZOS)

        if image.width / target_width >= 2 or image.height / target_height >= 2:
            resized = resized.filter(ImageFilter.UnsharpMask(radius=0.6, percent=115, threshold=2))
        return resized

    def _export_image(self) -> Path:
        left, top, right, bottom = self.crop_frame
        crop_width = right - left
        crop_height = bottom - top
        scale = self._display_scale()

        source_crop_width = crop_width / scale
        source_crop_height = crop_height / scale
        source_center_x = self.source_image.width / 2 - self.offset_x / scale
        source_center_y = self.source_image.height / 2 - self.offset_y / scale
        source_left = source_center_x - source_crop_width / 2
        source_top = source_center_y - source_crop_height / 2
        source_right = source_left + source_crop_width
        source_bottom = source_top + source_crop_height

        working_size = (
            max(self.target_size[0], int(round(source_crop_width))),
            max(self.target_size[1], int(round(source_crop_height))),
        )
        cropped = self.source_image.transform(
            working_size,
            Image.Transform.EXTENT,
            (source_left, source_top, source_right, source_bottom),
            resample=Image.Resampling.BICUBIC,
        )
        rendered = self._high_quality_resize(cropped, self.target_size)

        WORK_INPUTS.mkdir(parents=True, exist_ok=True)
        target_stem = Path(self.target_name).stem
        timestamp = time.strftime("%Y%m%d%H%M%S")
        output_path = WORK_INPUTS / f"{target_stem}_crop_{timestamp}.png"
        rendered.save(output_path, "PNG")
        return output_path


class SizeInputDialog:
    def __init__(self, parent: tk.Tk, initial_size: tuple[int, int] | None = None) -> None:
        self.result: tuple[int, int] | None = None

        self.dialog = tk.Toplevel(parent)
        self.dialog.title("目标分辨率")
        self.dialog.geometry("380x236")
        self.dialog.minsize(340, 220)
        self.dialog.configure(bg=BG)
        self.dialog.transient(parent)
        self.dialog.grab_set()

        width_value = str(initial_size[0]) if initial_size else ""
        height_value = str(initial_size[1]) if initial_size else ""
        self.width_var = tk.StringVar(value=width_value)
        self.height_var = tk.StringVar(value=height_value)

        container = tk.Frame(self.dialog, bg=CARD, padx=18, pady=18)
        container.pack(fill="both", expand=True, padx=16, pady=16)
        container.columnconfigure(1, weight=1)

        tk.Label(
            container,
            text="输入目标分辨率",
            bg=CARD,
            fg=TEXT,
            font=("Segoe UI Semibold", 15),
        ).grid(row=0, column=0, columnspan=2, sticky="w")

        tk.Label(
            container,
            text="宽",
            bg=CARD,
            fg=TEXT,
            font=("Segoe UI Semibold", 10),
        ).grid(row=1, column=0, sticky="w", pady=(18, 8), padx=(0, 10))
        tk.Entry(
            container,
            textvariable=self.width_var,
            relief="flat",
            bg="#f7f1e7",
            fg=TEXT,
            insertbackground=TEXT,
            font=("Segoe UI", 11),
        ).grid(row=1, column=1, sticky="ew", pady=(18, 8))

        tk.Label(
            container,
            text="高",
            bg=CARD,
            fg=TEXT,
            font=("Segoe UI Semibold", 10),
        ).grid(row=2, column=0, sticky="w", pady=(0, 8), padx=(0, 10))
        tk.Entry(
            container,
            textvariable=self.height_var,
            relief="flat",
            bg="#f7f1e7",
            fg=TEXT,
            insertbackground=TEXT,
            font=("Segoe UI", 11),
        ).grid(row=2, column=1, sticky="ew", pady=(0, 8))

        buttons = tk.Frame(container, bg=CARD)
        buttons.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(18, 0))

        tk.Button(
            buttons,
            text="取消",
            command=self.dialog.destroy,
            **button_style("secondary"),
            relief="flat",
            bd=0,
            padx=14,
            pady=8,
            font=("Segoe UI Semibold", 10),
        ).pack(side="right")

        tk.Button(
            buttons,
            text="确定",
            command=self._confirm,
            **button_style("primary"),
            relief="flat",
            bd=0,
            padx=14,
            pady=8,
            font=("Segoe UI Semibold", 10),
        ).pack(side="right", padx=(0, 10))

    def show(self) -> tuple[int, int] | None:
        self.dialog.wait_window()
        return self.result

    def _confirm(self) -> None:
        width_text = self.width_var.get().strip()
        height_text = self.height_var.get().strip()
        if not width_text.isdigit() or not height_text.isdigit():
            messagebox.showerror("格式不正确", "宽和高都必须是正整数。", parent=self.dialog)
            return

        width = int(width_text)
        height = int(height_text)
        if width <= 0 or height <= 0:
            messagebox.showerror("格式不正确", "宽和高都必须大于 0。", parent=self.dialog)
            return

        self.result = (width, height)
        self.dialog.destroy()


class ActionChoiceDialog:
    def __init__(
        self,
        parent: tk.Misc,
        title: str,
        message: str,
        actions: list[tuple[str, str, bool]],
    ) -> None:
        self.result: str | None = None
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(title)
        message_lines = message.count("\n") + 1
        longest_line = max((len(line) for line in message.splitlines()), default=0)
        longest_label = max((len(label) for _, label, _ in actions), default=0)
        button_count = max(len(actions), 1)
        button_rows = max(1, math.ceil(button_count / 3))

        dialog_width = max(680, min(980, 280 + longest_line * 7 + min(button_count, 3) * 170 + longest_label * 9))
        dialog_height = max(320, min(640, 210 + message_lines * 24 + button_rows * 56))

        self.dialog.geometry(f"{dialog_width}x{dialog_height}")
        self.dialog.minsize(660, 300)
        self.dialog.configure(bg=BG)
        self.dialog.transient(parent)
        self.dialog.grab_set()

        container = tk.Frame(self.dialog, bg=CARD, padx=18, pady=18)
        container.pack(fill="both", expand=True, padx=16, pady=16)
        container.columnconfigure(0, weight=1)

        tk.Label(
            container,
            text=title,
            bg=CARD,
            fg=TEXT,
            font=("Segoe UI Semibold", 15),
        ).grid(row=0, column=0, sticky="w")

        tk.Label(
            container,
            text=message,
            bg=CARD,
            fg=TEXT,
            justify="left",
            wraplength=max(420, dialog_width - 96),
            font=("Segoe UI", 10),
        ).grid(row=1, column=0, sticky="w", pady=(12, 0))

        button_row = tk.Frame(container, bg=CARD)
        button_row.grid(row=2, column=0, sticky="ew", pady=(18, 0))
        buttons_per_row = 3 if len(actions) > 3 else max(len(actions), 1)
        for column in range(buttons_per_row):
            button_row.columnconfigure(column, weight=1)

        for index, (value, label, primary) in enumerate(actions):
            tk.Button(
                button_row,
                text=label,
                command=lambda selected=value: self._choose(selected),
                **button_style("primary" if primary else "secondary"),
                relief="flat",
                bd=0,
                padx=14,
                pady=9,
                font=("Segoe UI Semibold", 10),
            ).grid(
                row=index // buttons_per_row,
                column=index % buttons_per_row,
                sticky="ew",
                padx=(0, 10) if index % buttons_per_row != buttons_per_row - 1 else (0, 0),
                pady=(0, 10),
            )

    def show(self) -> str | None:
        self.dialog.wait_window()
        return self.result

    def _choose(self, value: str | None) -> None:
        self.result = value
        self.dialog.destroy()


class ReductionPreviewDialog:
    def __init__(
        self,
        parent: tk.Tk,
        source_path: Path,
        target_format: str,
        render_fn,
        allow_keep_original: bool,
        title: str,
    ) -> None:
        self.target_format = target_format
        self.render_fn = render_fn
        self.allow_keep_original = allow_keep_original
        self.action = "cancel"
        self.result_path: Path | None = None
        self.strategy_var = tk.StringVar(value="平滑")
        self.original_preview: ImageTk.PhotoImage | None = None
        self.reduced_preview: ImageTk.PhotoImage | None = None
        self.reduced_image: Image.Image | None = None

        with Image.open(source_path) as image:
            self.source_image = image.convert("RGBA")

        self.dialog = tk.Toplevel(parent)
        self.dialog.title(title)
        self.dialog.geometry("1020x840")
        self.dialog.minsize(960, 780)
        self.dialog.configure(bg=BG)
        self.dialog.transient(parent)
        self.dialog.grab_set()

        self._build_layout()
        self._render_preview()

    def _build_layout(self) -> None:
        container = tk.Frame(self.dialog, bg=CARD, padx=18, pady=18)
        container.pack(fill="both", expand=True, padx=16, pady=16)
        container.grid_rowconfigure(3, weight=1)
        container.grid_columnconfigure(0, weight=1)

        tk.Label(
            container,
            text=f"降色预览：目标格式 {self.target_format}",
            bg=CARD,
            fg=TEXT,
            font=("Segoe UI Semibold", 17),
        ).grid(row=0, column=0, sticky="w")

        description = (
            "推荐先保持 1888，只在你确认需要控体积或保留原低色格式时再降色。"
            if self.allow_keep_original
            else "当前素材已经是 1888，这里可以手动尝试转换到 0565、0065、0064、0008 或 0004。"
        )
        tk.Label(
            container,
            text=description,
            bg=CARD,
            fg=MUTED,
            justify="left",
            wraplength=820,
            font=("Segoe UI", 10),
        ).grid(row=1, column=0, sticky="w", pady=(8, 0))

        strategy_row = tk.Frame(container, bg=CARD)
        strategy_row.grid(row=2, column=0, sticky="ew", pady=(16, 0))
        tk.Label(
            strategy_row,
            text="降色策略",
            bg=CARD,
            fg=TEXT,
            font=("Segoe UI Semibold", 10),
        ).pack(side="left")
        strategy_box = ttk.Combobox(
            strategy_row,
            textvariable=self.strategy_var,
            state="readonly",
            values=["保守", "平滑", "锐利"],
            width=12,
        )
        strategy_box.pack(side="left", padx=(10, 0))
        strategy_box.bind("<<ComboboxSelected>>", self._render_preview)

        tk.Label(
            strategy_row,
            text="保守：更少处理  平滑：默认推荐  锐利：边缘更硬",
            bg=CARD,
            fg=MUTED,
            font=("Segoe UI", 9),
        ).pack(side="left", padx=(12, 0))

        preview_row = tk.Frame(container, bg=CARD)
        preview_row.grid(row=3, column=0, sticky="nsew", pady=(18, 0))
        preview_row.grid_columnconfigure(0, weight=1)
        preview_row.grid_columnconfigure(1, weight=1)
        preview_row.grid_rowconfigure(0, weight=1)

        original_card = tk.Frame(preview_row, bg="#f7f1e7", padx=12, pady=12)
        original_card.grid(row=0, column=0, sticky="nsew")
        tk.Label(original_card, text="原图预览", bg="#f7f1e7", fg=TEXT, font=("Segoe UI Semibold", 10)).pack(anchor="w")
        self.original_canvas = tk.Canvas(original_card, width=360, height=360, bg=ACCENT_LIGHT, highlightthickness=0)
        self.original_canvas.pack(fill="both", expand=True, pady=(10, 0))

        reduced_card = tk.Frame(preview_row, bg="#f7f1e7", padx=12, pady=12)
        reduced_card.grid(row=0, column=1, sticky="nsew", padx=(16, 0))
        tk.Label(
            reduced_card,
            text="转换预览",
            bg="#f7f1e7",
            fg=TEXT,
            font=("Segoe UI Semibold", 10),
        ).pack(anchor="w")
        self.reduced_canvas = tk.Canvas(reduced_card, width=360, height=360, bg=ACCENT_LIGHT, highlightthickness=0)
        self.reduced_canvas.pack(fill="both", expand=True, pady=(10, 0))

        self.summary_label = tk.Label(
            container,
            text="",
            bg=CARD,
            fg=TEXT,
            justify="left",
            wraplength=820,
            font=("Segoe UI", 10),
        )
        self.summary_label.grid(row=4, column=0, sticky="w", pady=(14, 0))

        buttons = tk.Frame(container, bg=CARD)
        buttons.grid(row=5, column=0, sticky="ew", pady=(18, 0))

        tk.Button(
            buttons,
            text="取消",
            command=self.dialog.destroy,
            **button_style("secondary"),
            relief="flat",
            bd=0,
            padx=14,
            pady=9,
            font=("Segoe UI Semibold", 10),
        ).pack(side="right")

        tk.Button(
            buttons,
            text="应用当前转换",
            command=self._apply_reduced,
            **button_style("primary"),
            relief="flat",
            bd=0,
            padx=16,
            pady=9,
            font=("Segoe UI Semibold", 10),
        ).pack(side="right", padx=(0, 10))

        if self.allow_keep_original:
            tk.Button(
                buttons,
                text="保持 1888（推荐）",
                command=self._keep_original,
                **button_style("secondary"),
                relief="flat",
                bd=0,
                padx=14,
                pady=9,
                font=("Segoe UI Semibold", 10),
            ).pack(side="left")

    def show(self) -> tuple[str, str]:
        self.dialog.wait_window()
        return self.action, self.strategy_var.get()

    def _render_preview(self, _event=None) -> None:
        strategy = self.strategy_var.get()
        self.reduced_image = self.render_fn(self.source_image.copy(), self.target_format, strategy)

        self.original_preview = ImageTk.PhotoImage(self._thumbnail(self.source_image))
        self.reduced_preview = ImageTk.PhotoImage(self._thumbnail(self.reduced_image))

        self.original_canvas.delete("all")
        self.original_canvas.create_image(180, 180, image=self.original_preview, anchor="center")
        self.reduced_canvas.delete("all")
        self.reduced_canvas.create_image(180, 180, image=self.reduced_preview, anchor="center")

        reduced_colors = len(set(self.reduced_image.convert("RGBA").getdata()))
        self.summary_label.configure(
            text=f"当前策略：{strategy}。预览图颜色数约为 {reduced_colors}，可先看效果再决定是否应用。"
        )

    def _thumbnail(self, image: Image.Image) -> Image.Image:
        preview = image.copy()
        preview.thumbnail((320, 320), Image.Resampling.LANCZOS)
        return preview

    def _apply_reduced(self) -> None:
        self.action = "reduced"
        self.dialog.destroy()

    def _keep_original(self) -> None:
        self.action = "keep"
        self.dialog.destroy()


class SavedAssetBrowserDialog:
    def __init__(self, parent: tk.Tk, studio: ThemeStudio, pick_mode: bool = False) -> None:
        self.parent = parent
        self.studio = studio
        self.pick_mode = pick_mode
        self.result_path: Path | None = None
        self.preview_image: ImageTk.PhotoImage | None = None

        self.dialog = tk.Toplevel(parent)
        self.dialog.title("已保存素材")
        self.dialog.geometry("920x620")
        self.dialog.minsize(820, 520)
        self.dialog.configure(bg=BG)
        self.dialog.transient(parent)
        self.dialog.grab_set()

        self._build_layout()
        self._normalize_saved_button_labels()
        self._load_items()

    def _build_layout(self) -> None:
        container = tk.Frame(self.dialog, bg=CARD, padx=18, pady=18)
        container.pack(fill="both", expand=True, padx=16, pady=16)
        container.rowconfigure(1, weight=1)
        container.columnconfigure(0, weight=1)
        container.columnconfigure(1, weight=0)

        title = "从收藏素材中选择" if self.pick_mode else "已保存素材库"
        tk.Label(
            container,
            text=title,
            bg=CARD,
            fg=TEXT,
            font=("Segoe UI Semibold", 17),
        ).grid(row=0, column=0, columnspan=2, sticky="w")

        list_card = tk.Frame(container, bg=CARD)
        list_card.grid(row=1, column=0, sticky="nsew", pady=(14, 0))
        list_card.rowconfigure(0, weight=1)
        list_card.columnconfigure(0, weight=1)

        columns = ("id", "format", "size", "saved_at", "name")
        self.saved_tree = ttk.Treeview(list_card, columns=columns, show="headings", selectmode="extended")
        self.saved_tree.heading("id", text="ID")
        self.saved_tree.heading("format", text="格式")
        self.saved_tree.heading("size", text="尺寸")
        self.saved_tree.heading("saved_at", text="保存时间")
        self.saved_tree.heading("name", text="文件名")
        self.saved_tree.column("id", width=100, anchor="w")
        self.saved_tree.column("format", width=90, anchor="center")
        self.saved_tree.column("size", width=90, anchor="center")
        self.saved_tree.column("saved_at", width=130, anchor="center")
        self.saved_tree.column("name", width=260, anchor="w")
        self.saved_tree.grid(row=0, column=0, sticky="nsew")
        self.saved_tree.bind("<<TreeviewSelect>>", self._on_item_selected)
        self.saved_tree.bind("<Double-1>", self._on_double_click)

        scrollbar = ttk.Scrollbar(list_card, orient="vertical", command=self.saved_tree.yview)
        self.saved_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=0, column=1, sticky="ns")

        preview_card = tk.Frame(container, bg="#f7f1e7", padx=14, pady=14)
        preview_card.grid(row=1, column=1, sticky="ns", padx=(16, 0), pady=(14, 0))

        self.saved_preview_canvas = tk.Canvas(
            preview_card,
            width=300,
            height=300,
            bg=ACCENT_LIGHT,
            highlightthickness=0,
            relief="flat",
        )
        self.saved_preview_canvas.pack()
        self.saved_preview_placeholder = self.saved_preview_canvas.create_text(
            150,
            150,
            text="暂无预览",
            fill=MUTED,
            font=("Segoe UI", 11),
        )

        self.saved_meta_label = tk.Label(
            preview_card,
            text="",
            bg="#f7f1e7",
            fg=TEXT,
            justify="left",
            anchor="w",
            wraplength=300,
            font=("Consolas", 10),
        )
        self.saved_meta_label.pack(fill="x", pady=(12, 0))

        self.saved_hint_label = tk.Label(
            preview_card,
            text="先在左侧选一张收藏素材。",
            bg="#f7f1e7",
            fg=MUTED,
            justify="left",
            wraplength=300,
            font=("Segoe UI", 10),
        )
        self.saved_hint_label.pack(fill="x", pady=(10, 0))

        buttons = tk.Frame(container, bg=CARD)
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(18, 0))

        tk.Button(
            buttons,
            text="打开收藏目录",
            command=self._open_saved_dir,
            **button_style("secondary"),
            relief="flat",
            bd=0,
            padx=14,
            pady=9,
            font=("Segoe UI Semibold", 10),
        ).pack(side="left")

        tk.Button(
            buttons,
            text="刷新列表",
            command=self._load_items,
            **button_style("secondary"),
            relief="flat",
            bd=0,
            padx=14,
            pady=9,
            font=("Segoe UI Semibold", 10),
        ).pack(side="left", padx=(10, 0))

        tk.Button(
            buttons,
            text="关闭",
            command=self.dialog.destroy,
            **button_style("secondary"),
            relief="flat",
            bd=0,
            padx=14,
            pady=9,
            font=("Segoe UI Semibold", 10),
        ).pack(side="right")

        if self.pick_mode:
            tk.Button(
                buttons,
                text="使用这张素材",
                command=self._confirm_pick,
                **button_style("primary"),
                relief="flat",
                bd=0,
                padx=16,
                pady=9,
                font=("Segoe UI Semibold", 10),
            ).pack(side="right", padx=(0, 10))

    def show(self) -> Path | None:
        self.dialog.wait_window()
        return self.result_path

    def _load_items(self) -> None:
        self.saved_tree.delete(*self.saved_tree.get_children())
        self.items = self.studio.list_saved_artwork()
        for item in self.items:
            self.saved_tree.insert(
                "",
                "end",
                iid=item["path"],
                values=(item["id"], item["format"], item["size"], item["saved_at"], item["name"]),
            )

        if self.items:
            first = self.items[0]["path"]
            self.saved_tree.selection_set(first)
            self.saved_tree.focus(first)
            self.saved_tree.see(first)
            self._on_item_selected()
        else:
            self._clear_preview("当前还没有保存过素材。")

    def _on_item_selected(self, _event=None) -> None:
        selection = self.saved_tree.selection()
        if not selection:
            return

        path = Path(selection[0])
        try:
            with Image.open(path) as image:
                preview = image.convert("RGBA")
                preview.thumbnail((280, 280), Image.Resampling.LANCZOS)
                self.preview_image = ImageTk.PhotoImage(preview)
                size = f"{image.size[0]}x{image.size[1]}"
        except OSError:
            self._clear_preview("无法读取这张素材。")
            return

        self.saved_preview_canvas.delete("all")
        self.saved_preview_canvas.create_image(150, 150, image=self.preview_image, anchor="center")

        stem_parts = path.stem.split("_", 1)
        image_id = stem_parts[0]
        image_format = stem_parts[1] if len(stem_parts) == 2 else ""
        self.saved_meta_label.configure(
            text="\n".join(
                [
                    f"文件名: {path.name}",
                    f"素材 ID: {image_id}",
                    f"格式码: {image_format}",
                    f"尺寸:   {size}",
                    f"路径:   {path}",
                ]
            )
        )
        hint = "双击或点右下角按钮即可拿来替换当前素材。" if self.pick_mode else "这些收藏素材会跨不同固件会话保留。"
        self.saved_hint_label.configure(text=hint)

    def _clear_preview(self, message: str) -> None:
        self.preview_image = None
        self.saved_preview_canvas.delete("all")
        self.saved_preview_canvas.create_text(150, 150, text="暂无预览", fill=MUTED, font=("Segoe UI", 11))
        self.saved_meta_label.configure(text="")
        self.saved_hint_label.configure(text=message)

    def _open_saved_dir(self) -> None:
        saved_dir = self.studio.saved_assets_dir()
        open_in_file_manager(saved_dir, parent=self.dialog)

    def _confirm_pick(self) -> None:
        selection = self.saved_tree.selection()
        if not selection:
            messagebox.showinfo("先选素材", "请先在左侧列表里选中一张收藏素材。", parent=self.dialog)
            return
        self.result_path = Path(selection[0])
        self.dialog.destroy()

    def _on_double_click(self, _event=None) -> None:
        if self.pick_mode:
            self._confirm_pick()


class SavedAssetBrowserDialog:
    def __init__(
        self,
        parent: tk.Tk,
        studio: ThemeStudio,
        pick_mode: bool = False,
        import_callback=None,
        reduce_callback=None,
        resize_callback=None,
    ) -> None:
        self.parent = parent
        self.studio = studio
        self.pick_mode = pick_mode
        self.import_callback = import_callback
        self.reduce_callback = reduce_callback
        self.resize_callback = resize_callback
        self.result_path: Path | None = None
        self.preview_image: ImageTk.PhotoImage | None = None
        self.items_by_path: dict[str, dict[str, str]] = {}
        self.search_var = tk.StringVar()

        self.dialog = tk.Toplevel(parent)
        self.dialog.title("已保存素材")
        self.dialog.geometry("1180x700")
        self.dialog.minsize(1020, 600)
        self.dialog.configure(bg=BG)
        self.dialog.transient(parent)
        self.dialog.grab_set()

        self._build_layout()
        self._load_items()

    def _build_layout(self) -> None:
        container = tk.Frame(self.dialog, bg=CARD, padx=18, pady=18)
        container.pack(fill="both", expand=True, padx=16, pady=16)
        container.rowconfigure(2, weight=1)
        container.columnconfigure(0, weight=1)
        container.columnconfigure(1, weight=0)

        title = "从收藏素材中选择" if self.pick_mode else "已保存素材库"
        tk.Label(
            container,
            text=title,
            bg=CARD,
            fg=TEXT,
            font=("Segoe UI Semibold", 17),
        ).grid(row=0, column=0, columnspan=2, sticky="w")

        search_row = tk.Frame(container, bg=CARD)
        search_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        search_row.columnconfigure(1, weight=1)

        tk.Label(search_row, text="搜索", bg=CARD, fg=TEXT, font=("Segoe UI Semibold", 10)).grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        search_entry = tk.Entry(
            search_row,
            textvariable=self.search_var,
            relief="flat",
            bg="#f7f1e7",
            fg=TEXT,
            insertbackground=TEXT,
            font=("Segoe UI", 10),
        )
        search_entry.grid(row=0, column=1, sticky="ew")
        search_entry.bind("<KeyRelease>", self._load_items)
        tk.Label(search_row, text="可搜索文件名和备注", bg=CARD, fg=MUTED, font=("Segoe UI", 9)).grid(
            row=0, column=2, sticky="w", padx=(10, 0)
        )

        list_card = tk.Frame(container, bg=CARD)
        list_card.grid(row=2, column=0, sticky="nsew", pady=(14, 0))
        list_card.rowconfigure(0, weight=1)
        list_card.columnconfigure(0, weight=1)

        columns = ("id", "format", "size", "saved_at", "note", "name")
        self.saved_tree = ttk.Treeview(list_card, columns=columns, show="headings", selectmode="browse")
        self.saved_tree.heading("id", text="ID")
        self.saved_tree.heading("format", text="格式")
        self.saved_tree.heading("size", text="尺寸")
        self.saved_tree.heading("saved_at", text="保存时间")
        self.saved_tree.heading("note", text="备注")
        self.saved_tree.heading("name", text="文件名")
        self.saved_tree.column("id", width=90, anchor="w")
        self.saved_tree.column("format", width=90, anchor="center")
        self.saved_tree.column("size", width=90, anchor="center")
        self.saved_tree.column("saved_at", width=130, anchor="center")
        self.saved_tree.column("note", width=180, anchor="w")
        self.saved_tree.column("name", width=220, anchor="w")
        self.saved_tree.grid(row=0, column=0, sticky="nsew")
        self.saved_tree.bind("<<TreeviewSelect>>", self._on_item_selected)
        self.saved_tree.bind("<Double-1>", self._on_double_click)

        scrollbar = ttk.Scrollbar(list_card, orient="vertical", command=self.saved_tree.yview)
        self.saved_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=0, column=1, sticky="ns")

        preview_card = tk.Frame(container, bg="#f7f1e7", padx=14, pady=14)
        preview_card.grid(row=2, column=1, sticky="ns", padx=(16, 0), pady=(14, 0))

        self.saved_preview_canvas = tk.Canvas(
            preview_card,
            width=300,
            height=300,
            bg=ACCENT_LIGHT,
            highlightthickness=0,
            relief="flat",
        )
        self.saved_preview_canvas.pack()

        self.saved_meta_label = tk.Label(
            preview_card,
            text="",
            bg="#f7f1e7",
            fg=TEXT,
            justify="left",
            anchor="w",
            wraplength=300,
            font=("Consolas", 10),
        )
        self.saved_meta_label.pack(fill="x", pady=(12, 0))

        self.saved_hint_label = tk.Label(
            preview_card,
            text="先在左侧选一张收藏素材。",
            bg="#f7f1e7",
            fg=MUTED,
            justify="left",
            wraplength=300,
            font=("Segoe UI", 10),
        )
        self.saved_hint_label.pack(fill="x", pady=(10, 0))

        buttons = tk.Frame(container, bg=CARD)
        buttons.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(18, 0))
        self.button_bar = buttons

        tk.Button(
            buttons,
            text="从电脑导入",
            command=self._import_from_computer,
            **button_style("secondary"),
            relief="flat",
            bd=0,
            padx=14,
            pady=9,
            font=("Segoe UI Semibold", 10),
        ).pack(side="left")
        tk.Button(
            buttons,
            text="编辑备注",
            command=self._edit_note,
            **button_style("secondary"),
            relief="flat",
            bd=0,
            padx=14,
            pady=9,
            font=("Segoe UI Semibold", 10),
        ).pack(side="left", padx=(10, 0))
        tk.Button(
            buttons,
            text="手动改色彩",
            command=self._resize_selected_saved_asset,
            **button_style("secondary"),
            relief="flat",
            bd=0,
            padx=14,
            pady=9,
            font=("Segoe UI Semibold", 10),
        ).pack(side="left", padx=(10, 0))
        tk.Button(
            buttons,
            text="1888 闄嶈壊",
            command=self._reduce_selected_saved_asset,
            **button_style("secondary"),
            relief="flat",
            bd=0,
            padx=14,
            pady=9,
            font=("Segoe UI Semibold", 10),
        ).pack(side="left", padx=(10, 0))
        tk.Button(
            buttons,
            text="删除收藏",
            command=self._delete_selected,
            **button_style("secondary"),
            relief="flat",
            bd=0,
            padx=14,
            pady=9,
            font=("Segoe UI Semibold", 10),
        ).pack(side="left", padx=(10, 0))
        tk.Button(
            buttons,
            text="打开收藏目录",
            command=self._open_saved_dir,
            **button_style("secondary"),
            relief="flat",
            bd=0,
            padx=14,
            pady=9,
            font=("Segoe UI Semibold", 10),
        ).pack(side="left", padx=(10, 0))
        tk.Button(
            buttons,
            text="刷新列表",
            command=self._load_items,
            **button_style("secondary"),
            relief="flat",
            bd=0,
            padx=14,
            pady=9,
            font=("Segoe UI Semibold", 10),
        ).pack(side="left", padx=(10, 0))
        tk.Button(
            buttons,
            text="关闭",
            command=self.dialog.destroy,
            **button_style("secondary"),
            relief="flat",
            bd=0,
            padx=14,
            pady=9,
            font=("Segoe UI Semibold", 10),
        ).pack(side="right")

        if self.pick_mode:
            tk.Button(
                buttons,
                text="使用这张素材",
                command=self._confirm_pick,
                **button_style("primary"),
                relief="flat",
                bd=0,
                padx=16,
                pady=9,
                font=("Segoe UI Semibold", 10),
            ).pack(side="right", padx=(0, 10))

    def show(self) -> Path | None:
        self.dialog.wait_window()
        return self.result_path

    def _normalize_saved_button_labels(self) -> None:
        buttons = [widget for widget in self.button_bar.winfo_children() if isinstance(widget, tk.Button)]
        labels = ["从电脑导入", "编辑备注", "调整尺寸", "手动改色彩", "删除收藏", "打开收藏目录", "刷新列表"]
        for widget, label in zip(buttons, labels):
            widget.configure(text=label)

    def _selected_path(self) -> Path | None:
        selection = self.saved_tree.selection()
        if not selection:
            return None
        return Path(selection[0])

    def _selected_paths(self) -> list[Path]:
        return [Path(item) for item in self.saved_tree.selection()]

    def _load_items(self, _event=None, keep_path: str | None = None) -> None:
        selected = keep_path
        if selected is None:
            current = self._selected_path()
            selected = str(current) if current else None

        self.saved_tree.delete(*self.saved_tree.get_children())
        items = self.studio.list_saved_artwork(self.search_var.get())
        self.items_by_path = {item["path"]: item for item in items}

        for item in items:
            self.saved_tree.insert(
                "",
                "end",
                iid=item["path"],
                values=(item["id"], item["format"], item["size"], item["saved_at"], item.get("note", ""), item["name"]),
            )

        if not items:
            self._clear_preview("当前还没有符合搜索条件的收藏素材。")
            return

        chosen = selected if selected in self.items_by_path else items[0]["path"]
        self.saved_tree.selection_set(chosen)
        self.saved_tree.focus(chosen)
        self.saved_tree.see(chosen)
        self._on_item_selected()

    def _on_item_selected(self, _event=None) -> None:
        path = self._selected_path()
        if not paths:
            return

        item = self.items_by_path.get(str(path), {})
        try:
            with Image.open(path) as image:
                preview = image.convert("RGBA")
                preview.thumbnail((280, 280), Image.Resampling.LANCZOS)
                self.preview_image = ImageTk.PhotoImage(preview)
                size = f"{image.size[0]}x{image.size[1]}"
        except OSError:
            self._clear_preview("无法读取这张素材。")
            return

        self.saved_preview_canvas.delete("all")
        self.saved_preview_canvas.create_image(150, 150, image=self.preview_image, anchor="center")

        image_id = item.get("id", "")
        image_format = item.get("format", "")
        note = item.get("note", "")
        self.saved_meta_label.configure(
            text="\n".join(
                [
                    f"文件名: {path.name}",
                    f"素材 ID: {image_id or '-'}",
                    f"格式码: {image_format or '-'}",
                    f"尺寸:   {size}",
                    f"备注:   {note or '-'}",
                    f"路径:   {path}",
                ]
            )
        )
        hint = "双击或点右下角按钮即可拿来替换当前素材。" if self.pick_mode else "收藏素材会跨不同固件会话保留。"
        self.saved_hint_label.configure(text=hint)

    def _clear_preview(self, message: str) -> None:
        self.preview_image = None
        self.saved_preview_canvas.delete("all")
        self.saved_preview_canvas.create_text(150, 150, text="暂无预览", fill=MUTED, font=("Segoe UI", 11))
        self.saved_meta_label.configure(text="")
        self.saved_hint_label.configure(text=message)

    def _parse_saved_item_name(self, path: Path) -> tuple[str, str]:
        parts = path.stem.split("_")
        if len(parts) >= 2 and parts[0].isdigit() and len(parts[1]) == 4 and parts[1].isdigit():
            return parts[0], parts[1]
        return "", ""

    def _reduce_selected_saved_asset(self) -> None:
        path = self._selected_path()
        if path is None:
            messagebox.showinfo("先选素材", "请先在左侧列表里选中一张收藏素材。", parent=self.dialog)
            return

        if self.reduce_callback is None:
            messagebox.showinfo("当前不可用", "这个窗口当前没有绑定降色动作。", parent=self.dialog)
            return

        item = self.items_by_path.get(str(path), {})
        updated = self.reduce_callback(path, item)
        if updated is not None:
            self._load_items(keep_path=str(updated))

    def _resize_selected_saved_asset(self) -> None:
        path = self._selected_path()
        if path is None:
            messagebox.showinfo("先选素材", "请先在左侧列表里选中一张收藏素材。", parent=self.dialog)
            return

        if self.resize_callback is None:
            messagebox.showinfo("当前不可用", "这个窗口当前没有绑定尺寸调整动作。", parent=self.dialog)
            return

        item = self.items_by_path.get(str(path), {})
        updated = self.resize_callback(path, item)
        if updated is not None:
            self._load_items(keep_path=str(updated))

    def _import_from_computer(self) -> None:
        if self.import_callback is None:
            messagebox.showinfo("当前不可用", "这个窗口当前没有绑定导入动作。", parent=self.dialog)
            return

        imported = self.import_callback()
        if imported is not None:
            self._load_items(keep_path=str(imported))

    def _edit_note(self) -> None:
        path = self._selected_path()
        if path is None:
            messagebox.showinfo("先选素材", "请先在左侧列表里选中一张收藏素材。", parent=self.dialog)
            return

        current_note = self.items_by_path.get(str(path), {}).get("note", "")
        new_note = simpledialog.askstring("编辑备注", "输入新的备注：", initialvalue=current_note, parent=self.dialog)
        if new_note is None:
            return

        self.studio.update_saved_artwork_note(path, new_note)
        self._load_items(keep_path=str(path))

    def _delete_selected(self) -> None:
        paths = self._selected_paths()
        if path is None:
            messagebox.showinfo("先选素材", "请先在左侧列表里选中一张收藏素材。", parent=self.dialog)
            return

        confirmed = messagebox.askyesno("删除收藏", f"确定要删除这张收藏素材吗？\n\n{path.name}", parent=self.dialog)
        if not confirmed:
            return

        for path in paths:
            self.studio.delete_saved_artwork(path)
        self._load_items()

    def _open_saved_dir(self) -> None:
        saved_dir = self.studio.saved_assets_dir()
        open_in_file_manager(saved_dir, parent=self.dialog)

    def _confirm_pick(self) -> None:
        path = self._selected_path()
        if path is None:
            messagebox.showinfo("先选素材", "请先在左侧列表里选中一张收藏素材。", parent=self.dialog)
            return
        self.result_path = path
        self.dialog.destroy()

    def _on_double_click(self, _event=None) -> None:
        if self.pick_mode:
            self._confirm_pick()


class SavedAssetBrowserDialog:
    def __init__(
        self,
        parent: tk.Tk,
        studio: ThemeStudio,
        pick_mode: bool = False,
        import_callback=None,
        reduce_callback=None,
        resize_callback=None,
    ) -> None:
        self.parent = parent
        self.studio = studio
        self.pick_mode = pick_mode
        self.import_callback = import_callback
        self.reduce_callback = reduce_callback
        self.resize_callback = resize_callback
        self.result_path: Path | None = None
        self.preview_image: ImageTk.PhotoImage | None = None
        self.items_by_path: dict[str, dict[str, str]] = {}
        self.search_var = tk.StringVar()

        self.dialog = tk.Toplevel(parent)
        self.dialog.title("已保存素材")
        self.dialog.geometry("1180x700")
        self.dialog.minsize(1020, 600)
        self.dialog.configure(bg=BG)
        self.dialog.transient(parent)
        self.dialog.grab_set()

        self._build_layout()
        self._load_items()

    def _build_layout(self) -> None:
        container = tk.Frame(self.dialog, bg=CARD, padx=18, pady=18)
        container.pack(fill="both", expand=True, padx=16, pady=16)
        container.rowconfigure(2, weight=1)
        container.columnconfigure(0, weight=1)
        container.columnconfigure(1, weight=0)

        title = "从收藏素材中选择" if self.pick_mode else "已保存素材库"
        tk.Label(
            container,
            text=title,
            bg=CARD,
            fg=TEXT,
            font=("Segoe UI Semibold", 17),
        ).grid(row=0, column=0, columnspan=2, sticky="w")

        search_row = tk.Frame(container, bg=CARD)
        search_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        search_row.columnconfigure(1, weight=1)

        tk.Label(search_row, text="搜索", bg=CARD, fg=TEXT, font=("Segoe UI Semibold", 10)).grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        search_entry = tk.Entry(
            search_row,
            textvariable=self.search_var,
            relief="flat",
            bg="#f7f1e7",
            fg=TEXT,
            insertbackground=TEXT,
            font=("Segoe UI", 10),
        )
        search_entry.grid(row=0, column=1, sticky="ew")
        search_entry.bind("<KeyRelease>", self._load_items)
        tk.Label(
            search_row,
            text="可搜索文件名和备注",
            bg=CARD,
            fg=MUTED,
            font=("Segoe UI", 9),
        ).grid(row=0, column=2, sticky="w", padx=(10, 0))

        list_card = tk.Frame(container, bg=CARD)
        list_card.grid(row=2, column=0, sticky="nsew", pady=(14, 0))
        list_card.rowconfigure(0, weight=1)
        list_card.columnconfigure(0, weight=1)

        columns = ("id", "format", "size", "saved_at", "note", "name")
        self.saved_tree = ttk.Treeview(list_card, columns=columns, show="headings", selectmode="extended")
        self.saved_tree.heading("id", text="ID")
        self.saved_tree.heading("format", text="格式")
        self.saved_tree.heading("size", text="尺寸")
        self.saved_tree.heading("saved_at", text="保存时间")
        self.saved_tree.heading("note", text="备注")
        self.saved_tree.heading("name", text="文件名")
        self.saved_tree.column("id", width=90, anchor="w")
        self.saved_tree.column("format", width=90, anchor="center")
        self.saved_tree.column("size", width=90, anchor="center")
        self.saved_tree.column("saved_at", width=130, anchor="center")
        self.saved_tree.column("note", width=180, anchor="w")
        self.saved_tree.column("name", width=220, anchor="w")
        self.saved_tree.grid(row=0, column=0, sticky="nsew")
        self.saved_tree.bind("<<TreeviewSelect>>", self._on_item_selected)
        self.saved_tree.bind("<Double-1>", self._on_double_click)

        scrollbar = ttk.Scrollbar(list_card, orient="vertical", command=self.saved_tree.yview)
        self.saved_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=0, column=1, sticky="ns")

        preview_card = tk.Frame(container, bg="#f7f1e7", padx=14, pady=14)
        preview_card.grid(row=2, column=1, sticky="ns", padx=(16, 0), pady=(14, 0))

        self.saved_preview_canvas = tk.Canvas(
            preview_card,
            width=300,
            height=300,
            bg=ACCENT_LIGHT,
            highlightthickness=0,
            relief="flat",
        )
        self.saved_preview_canvas.pack()

        self.saved_meta_label = tk.Label(
            preview_card,
            text="",
            bg="#f7f1e7",
            fg=TEXT,
            justify="left",
            anchor="w",
            wraplength=300,
            font=("Consolas", 10),
        )
        self.saved_meta_label.pack(fill="x", pady=(12, 0))

        self.saved_hint_label = tk.Label(
            preview_card,
            text="先在左侧选一张收藏素材。",
            bg="#f7f1e7",
            fg=MUTED,
            justify="left",
            wraplength=300,
            font=("Segoe UI", 10),
        )
        self.saved_hint_label.pack(fill="x", pady=(10, 0))

        buttons = tk.Frame(container, bg=CARD)
        buttons.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(18, 0))

        left_actions = tk.Frame(buttons, bg=CARD)
        left_actions.pack(side="left")
        right_actions = tk.Frame(buttons, bg=CARD)
        right_actions.pack(side="right")

        action_specs = [
            ("从电脑导入", self._import_from_computer),
            ("编辑备注", self._edit_note),
            ("调整尺寸", self._resize_selected_saved_asset),
            ("手动改色彩", self._reduce_selected_saved_asset),
            ("删除收藏", self._delete_selected),
            ("打开收藏目录", self._open_saved_dir),
            ("刷新列表", self._load_items),
        ]
        for index, (label, command) in enumerate(action_specs):
            tk.Button(
                left_actions,
                text=label,
                command=command,
                **button_style("secondary"),
                relief="flat",
                bd=0,
                padx=14,
                pady=9,
                font=("Segoe UI Semibold", 10),
            ).pack(side="left", padx=(0 if index == 0 else 10, 0))

        tk.Button(
            right_actions,
            text="关闭",
            command=self.dialog.destroy,
            **button_style("secondary"),
            relief="flat",
            bd=0,
            padx=14,
            pady=9,
            font=("Segoe UI Semibold", 10),
        ).pack(side="right")

        if self.pick_mode:
            tk.Button(
                right_actions,
                text="使用这张素材",
                command=self._confirm_pick,
                **button_style("primary"),
                relief="flat",
                bd=0,
                padx=16,
                pady=9,
                font=("Segoe UI Semibold", 10),
            ).pack(side="right", padx=(0, 10))

    def show(self) -> Path | None:
        self.dialog.wait_window()
        return self.result_path

    def _selected_path(self) -> Path | None:
        selection = self.saved_tree.selection()
        if not selection:
            return None
        return Path(selection[0])

    def _selected_paths(self) -> list[Path]:
        return [Path(item) for item in self.saved_tree.selection()]

    def _load_items(self, _event=None, keep_path: str | None = None) -> None:
        selected = keep_path
        if selected is None:
            current = self._selected_path()
            selected = str(current) if current else None

        self.saved_tree.delete(*self.saved_tree.get_children())
        items = self.studio.list_saved_artwork(self.search_var.get())
        self.items_by_path = {item["path"]: item for item in items}

        for item in items:
            self.saved_tree.insert(
                "",
                "end",
                iid=item["path"],
                values=(item["id"], item["format"], item["size"], item["saved_at"], item.get("note", ""), item["name"]),
            )

        if not items:
            self._clear_preview("当前还没有符合搜索条件的收藏素材。")
            return

        chosen = selected if selected in self.items_by_path else items[0]["path"]
        self.saved_tree.selection_set(chosen)
        self.saved_tree.focus(chosen)
        self.saved_tree.see(chosen)
        self._on_item_selected()

    def _on_item_selected(self, _event=None) -> None:
        path = self._selected_path()
        if path is None:
            return

        item = self.items_by_path.get(str(path), {})
        try:
            with Image.open(path) as image:
                preview = image.convert("RGBA")
                preview.thumbnail((280, 280), Image.Resampling.LANCZOS)
                self.preview_image = ImageTk.PhotoImage(preview)
                size = f"{image.size[0]}x{image.size[1]}"
        except OSError:
            self._clear_preview("无法读取这张素材。")
            return

        self.saved_preview_canvas.delete("all")
        self.saved_preview_canvas.create_image(150, 150, image=self.preview_image, anchor="center")

        image_id = item.get("id", "")
        image_format = item.get("format", "")
        note = item.get("note", "")
        self.saved_meta_label.configure(
            text="\n".join(
                [
                    f"文件名: {path.name}",
                    f"素材 ID: {image_id or '-'}",
                    f"格式码: {image_format or '-'}",
                    f"尺寸:   {size}",
                    f"备注:   {note or '-'}",
                    f"路径:   {path}",
                ]
            )
        )
        hint = "双击或点右下角按钮即可拿来替换当前素材。" if self.pick_mode else "收藏素材会跨不同固件会话保留。"
        self.saved_hint_label.configure(text=hint)

    def _clear_preview(self, message: str) -> None:
        self.preview_image = None
        self.saved_preview_canvas.delete("all")
        self.saved_preview_canvas.create_text(150, 150, text="暂无预览", fill=MUTED, font=("Segoe UI", 11))
        self.saved_meta_label.configure(text="")
        self.saved_hint_label.configure(text=message)

    def _import_from_computer(self) -> None:
        if self.import_callback is None:
            messagebox.showinfo("当前不可用", "这个窗口当前没有绑定导入动作。", parent=self.dialog)
            return

        imported = self.import_callback()
        if imported is not None:
            self._load_items(keep_path=str(imported))

    def _edit_note(self) -> None:
        path = self._selected_path()
        if path is None:
            messagebox.showinfo("先选素材", "请先在左侧列表里选中一张收藏素材。", parent=self.dialog)
            return

        current_note = self.items_by_path.get(str(path), {}).get("note", "")
        new_note = simpledialog.askstring("编辑备注", "输入新的备注：", initialvalue=current_note, parent=self.dialog)
        if new_note is None:
            return

        self.studio.update_saved_artwork_note(path, new_note)
        self._load_items(keep_path=str(path))

    def _resize_selected_saved_asset(self) -> None:
        path = self._selected_path()
        if path is None:
            messagebox.showinfo("先选素材", "请先在左侧列表里选中一张收藏素材。", parent=self.dialog)
            return

        if self.resize_callback is None:
            messagebox.showinfo("当前不可用", "这个窗口当前没有绑定尺寸调整动作。", parent=self.dialog)
            return

        item = self.items_by_path.get(str(path), {})
        updated = self.resize_callback(path, item)
        if updated is not None:
            self._load_items(keep_path=str(updated))

    def _reduce_selected_saved_asset(self) -> None:
        path = self._selected_path()
        if path is None:
            messagebox.showinfo("先选素材", "请先在左侧列表里选中一张收藏素材。", parent=self.dialog)
            return

        if self.reduce_callback is None:
            messagebox.showinfo("当前不可用", "这个窗口当前没有绑定降色动作。", parent=self.dialog)
            return

        item = self.items_by_path.get(str(path), {})
        updated = self.reduce_callback(path, item)
        if updated is not None:
            self._load_items(keep_path=str(updated))

    def _delete_selected(self) -> None:
        paths = self._selected_paths()
        if not paths:
            messagebox.showinfo("先选素材", "请先在左侧列表里选中至少一张收藏素材。", parent=self.dialog)
            return

        sample_names = "\n".join(path.name for path in paths[:5])
        if len(paths) > 5:
            sample_names += f"\n... 共 {len(paths)} 张"

        confirmed = messagebox.askyesno(
            "删除收藏",
            f"确定要删除这 {len(paths)} 张收藏素材吗？\n\n{sample_names}",
            parent=self.dialog,
        )
        if not confirmed:
            return

        for path in paths:
            self.studio.delete_saved_artwork(path)
        self._load_items()

    def _open_saved_dir(self) -> None:
        saved_dir = self.studio.saved_assets_dir()
        open_in_file_manager(saved_dir, parent=self.dialog)

    def _confirm_pick(self) -> None:
        path = self._selected_path()
        if path is None:
            messagebox.showinfo("先选素材", "请先在左侧列表里选中一张收藏素材。", parent=self.dialog)
            return
        self.result_path = path
        self.dialog.destroy()

    def _on_double_click(self, _event=None) -> None:
        if self.pick_mode:
            self._confirm_pick()


class FontSlotBrowserDialog:
    def __init__(self, parent: tk.Tk, studio: ThemeStudio, log_callback=None) -> None:
        self.parent = parent
        self.studio = studio
        self.log_callback = log_callback
        self.items_by_name: dict[str, dict[str, object]] = {}
        self.summary_var = tk.StringVar(value="")
        self.detail_var = tk.StringVar(value="")
        self.warning_var = tk.StringVar(
            value="字体替换只会在最终打包时写入。不是所有字体都兼容 iPod nano，替换后若设备无法启动，请清除替换并重新打包。"
        )

        self.dialog = tk.Toplevel(parent)
        self.dialog.title("字体槽位")
        self.dialog.geometry("1100x680")
        self.dialog.minsize(980, 560)
        self.dialog.configure(bg=BG)
        self.dialog.transient(parent)
        self.dialog.grab_set()

        self._build_layout()
        self._load_items()

    def _build_layout(self) -> None:
        container = tk.Frame(self.dialog, bg=CARD, padx=18, pady=18)
        container.pack(fill="both", expand=True, padx=16, pady=16)
        container.rowconfigure(2, weight=1)
        container.columnconfigure(0, weight=1)
        container.columnconfigure(1, weight=0)

        tk.Label(
            container,
            text="字体槽位",
            bg=CARD,
            fg=TEXT,
            font=("Segoe UI Semibold", 17),
        ).grid(row=0, column=0, columnspan=2, sticky="w")

        tk.Label(
            container,
            textvariable=self.summary_var,
            bg=CARD,
            fg=MUTED,
            justify="left",
            wraplength=920,
            font=("Segoe UI", 10),
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 0))

        list_card = tk.Frame(container, bg=CARD)
        list_card.grid(row=2, column=0, sticky="nsew", pady=(14, 0))
        list_card.rowconfigure(0, weight=1)
        list_card.columnconfigure(0, weight=1)

        columns = ("name", "extension", "status")
        self.font_tree = ttk.Treeview(list_card, columns=columns, show="headings")
        self.font_tree.heading("name", text="文件名")
        self.font_tree.heading("extension", text="扩展名")
        self.font_tree.heading("status", text="状态")
        self.font_tree.column("name", width=320, anchor="w")
        self.font_tree.column("extension", width=90, anchor="center")
        self.font_tree.column("status", width=120, anchor="center")
        self.font_tree.grid(row=0, column=0, sticky="nsew")
        self.font_tree.bind("<<TreeviewSelect>>", self._on_item_selected)

        scrollbar = ttk.Scrollbar(list_card, orient="vertical", command=self.font_tree.yview)
        self.font_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=0, column=1, sticky="ns")

        detail_card = tk.Frame(container, bg="#f7f1e7", padx=14, pady=14)
        detail_card.grid(row=2, column=1, sticky="ns", padx=(16, 0), pady=(14, 0))

        tk.Label(
            detail_card,
            text="当前槽位详情",
            bg="#f7f1e7",
            fg=TEXT,
            font=("Segoe UI Semibold", 12),
        ).pack(anchor="w")

        self.detail_label = tk.Label(
            detail_card,
            textvariable=self.detail_var,
            bg="#f7f1e7",
            fg=TEXT,
            justify="left",
            anchor="w",
            wraplength=320,
            font=("Consolas", 10),
        )
        self.detail_label.pack(fill="x", pady=(12, 0))

        self.warning_label = tk.Label(
            detail_card,
            textvariable=self.warning_var,
            bg="#f7f1e7",
            fg=MUTED,
            justify="left",
            wraplength=320,
            font=("Segoe UI", 10),
        )
        self.warning_label.pack(fill="x", pady=(12, 0))

        buttons = tk.Frame(container, bg=CARD)
        buttons.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(18, 0))

        self.replace_button = tk.Button(
            buttons,
            text="选择替换字体",
            command=self._choose_replacement,
            **button_style("primary"),
            relief="flat",
            bd=0,
            padx=14,
            pady=9,
            font=("Segoe UI Semibold", 10),
        )
        self.replace_button.pack(side="left")

        self.clear_button = tk.Button(
            buttons,
            text="清除替换",
            command=self._clear_replacement,
            **button_style("secondary"),
            relief="flat",
            bd=0,
            padx=14,
            pady=9,
            font=("Segoe UI Semibold", 10),
        )
        self.clear_button.pack(side="left", padx=(10, 0))

        self.export_button = tk.Button(
            buttons,
            text="导出当前字体",
            command=self._export_current_font,
            **button_style("secondary"),
            relief="flat",
            bd=0,
            padx=14,
            pady=9,
            font=("Segoe UI Semibold", 10),
        )
        self.export_button.pack(side="left", padx=(10, 0))

        tk.Button(
            buttons,
            text="打开字体暂存目录",
            command=self._open_staging_dir,
            **button_style("secondary"),
            relief="flat",
            bd=0,
            padx=14,
            pady=9,
            font=("Segoe UI Semibold", 10),
        ).pack(side="left", padx=(10, 0))

        tk.Button(
            buttons,
            text="刷新列表",
            command=self._load_items,
            **button_style("secondary"),
            relief="flat",
            bd=0,
            padx=14,
            pady=9,
            font=("Segoe UI Semibold", 10),
        ).pack(side="left", padx=(10, 0))

        tk.Button(
            buttons,
            text="关闭",
            command=self.dialog.destroy,
            **button_style("secondary"),
            relief="flat",
            bd=0,
            padx=14,
            pady=9,
            font=("Segoe UI Semibold", 10),
        ).pack(side="right")

    def show(self) -> None:
        self.dialog.wait_window()

    def _selected_name(self) -> str | None:
        selection = self.font_tree.selection()
        if not selection:
            return None
        return str(selection[0])

    def _load_items(self, keep_name: str | None = None) -> None:
        selected = keep_name if keep_name is not None else self._selected_name()
        self.font_tree.delete(*self.font_tree.get_children())
        items = self.studio.list_fonts()
        self.items_by_name = {str(item["name"]): item for item in items}

        replace_count = sum(1 for item in items if item["status"] == "已指定替换")
        self.summary_var.set(
            f"当前固件中检测到 {len(items)} 个字体槽位，其中已指定替换 {replace_count} 个。"
            " v1 只支持 .ttf 替换；.ttc / .otf 仅展示，不提供写回。"
        )

        for item in items:
            self.font_tree.insert(
                "",
                "end",
                iid=str(item["name"]),
                values=(item.get("display_name", item["name"]), item["extension"], item["status"]),
            )

        if not items:
            self.detail_var.set("当前还没有可用的字体工作区。\n请先导入官方固件或社区 IPSW。")
            self.warning_var.set("字体替换只会在最终打包时写入。")
            self._update_button_state(None)
            return

        chosen = selected if selected in self.items_by_name else str(items[0]["name"])
        self.font_tree.selection_set(chosen)
        self.font_tree.focus(chosen)
        self.font_tree.see(chosen)
        self._on_item_selected()

    def _update_button_state(self, item: dict[str, object] | None) -> None:
        if not item:
            self.replace_button.configure(state="disabled")
            self.clear_button.configure(state="disabled")
            self.export_button.configure(state="disabled")
            return

        supported = bool(item.get("supported"))
        has_replacement = bool(item.get("replacement_path"))
        self.replace_button.configure(state="normal" if supported else "disabled")
        self.clear_button.configure(state="normal" if supported and has_replacement else "disabled")
        self.export_button.configure(state="normal")

    def _on_item_selected(self, _event=None) -> None:
        slot_name = self._selected_name()
        item = self.items_by_name.get(slot_name or "")
        if not item:
            self.detail_var.set("请先从左侧列表选择一个字体槽位。")
            self.warning_var.set("字体替换只会在最终打包时写入。")
            self._update_button_state(None)
            return

        slot_name = str(item["name"])
        display_name = str(item.get("display_name", slot_name))
        extension = str(item["extension"])
        status = str(item["status"])
        replacement_path = str(item.get("replacement_path", "")) or "-"
        supported = bool(item["supported"])
        hint = str(item.get("hint", ""))
        kind = str(item.get("kind", "file"))

        support_text = "可替换的 TrueType 槽位（.ttf）" if supported else "v1 暂不支持此格式替换"
        lines = [
            f"文件名:   {slot_name}",
            f"扩展名:   {extension}",
            f"状态:     {status}",
            f"支持情况: {support_text}",
            f"替换文件: {replacement_path}",
        ]
        if hint:
            lines.append(f"提示:     {hint}")
        self.detail_var.set("\n".join(lines))

        warnings = [
            "字体替换只会在最终打包时写入，不会立刻改写当前 rsrc 基底文件。",
            "如果目标是简体中文，请优先选择包含完整简体中文字形的 .ttf；v1 不做字形覆盖率分析。",
            "不是所有字体都兼容 iPod nano，替换后若设备无法启动，请清除替换并重新打包。",
        ]
        if not supported:
            warnings.insert(1, "当前这个槽位是 .ttc / .otf，只做只读展示，暂不支持替换。")
        self.warning_var.set(" ".join(warnings))
        self._update_button_state(item)

    def _choose_replacement(self) -> None:
        slot_name = self._selected_name()
        item = self.items_by_name.get(slot_name or "")
        if not item or not bool(item.get("supported")):
            messagebox.showinfo("当前槽位不可替换", "v1 只支持 .ttf 槽位替换。", parent=self.dialog)
            return

        source = filedialog.askopenfilename(
            title=f"为 {slot_name} 选择替换字体",
            filetypes=[("TrueType font", "*.ttf"), ("All files", "*.*")],
        )
        if not source:
            return

        try:
            staged_path = self.studio.stage_font_replacement(slot_name, Path(source))
        except StudioError as exc:
            messagebox.showerror("暂存字体失败", str(exc), parent=self.dialog)
            return

        if self.log_callback is not None:
            self.log_callback(f"已为字体槽位暂存替换：{slot_name} <- {Path(source).name}")
            self.log_callback(f"字体暂存位置：{staged_path}")
        self._load_items(keep_name=slot_name)

    def _clear_replacement(self) -> None:
        slot_name = self._selected_name()
        if not slot_name:
            return
        try:
            self.studio.clear_font_replacement(slot_name)
        except StudioError as exc:
            messagebox.showerror("清除替换失败", str(exc), parent=self.dialog)
            return

        if self.log_callback is not None:
            self.log_callback(f"已清除字体槽位替换：{slot_name}")
        self._load_items(keep_name=slot_name)

    def _export_current_font(self) -> None:
        slot_name = self._selected_name()
        if not slot_name:
            return

        item = self.items_by_name.get(slot_name, {})
        if item.get("kind") == "ttc-member":
            initial_name = f"{str(item.get('member_name') or 'exported_font')}.ttf"
            file_pattern = "*.ttf"
        else:
            initial_name = str(item.get("display_name", slot_name))
            file_pattern = f"*{Path(initial_name).suffix}"
        target = filedialog.asksaveasfilename(
            title="导出当前字体",
            defaultextension=Path(initial_name).suffix,
            initialfile=initial_name,
            filetypes=[("Font file", file_pattern), ("All files", "*.*")],
        )
        if not target:
            return

        try:
            output_path = self.studio.export_current_font(slot_name, Path(target))
        except StudioError as exc:
            messagebox.showerror("导出字体失败", str(exc), parent=self.dialog)
            return

        if self.log_callback is not None:
            self.log_callback(f"已导出当前字体：{slot_name} -> {output_path}")

    def _open_staging_dir(self) -> None:
        open_in_file_manager(self.studio.fonts_dir(), parent=self.dialog)


class ThemeStudioApp:
    def __init__(self) -> None:
        self.studio = ThemeStudio()
        self.root = tk.Tk()
        if IS_MACOS:
            self.root.tk.call("tk", "scaling", 1.12)
        self.root.option_add("*tearOff", False)
        self.root.title("iPod Theme Studio")
        self.root.geometry("1500x900")
        self.root.minsize(1260, 760)
        self.root.configure(bg=BG)
        self.app_icon_image: ImageTk.PhotoImage | None = None
        self.header_icon_image: ImageTk.PhotoImage | None = None
        self._load_app_icon()

        self.log_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.preview_image: ImageTk.PhotoImage | None = None
        self.preview_canvas_image_id: int | None = None
        self.current_selection: str | None = None
        self.busy = False

        self.device_var = tk.StringVar(value="nano7-2012")
        self.group_var = tk.StringVar(value="全部素材")
        self.status_var = tk.StringVar(value="还没有导入固件。")
        self.source_var = tk.StringVar(value="当前来源：未加载")
        self.notes_var = tk.StringVar(value="请选择左侧素材。")
        self.capacity_var = tk.StringVar(value="导入固件后会在这里提示 1888 素材和打包体积变化。")
        self._capacity_refresh_token = 0
        self.group_map: dict[str, str] = {}

        self._configure_style()
        self._build_layout()
        self._apply_branding()
        self._load_existing_session()
        self._refresh_assets()
        self.root.after(120, self._drain_log_queue)

    def _configure_style(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=ui_font(24, bold=True))
        style.configure("Body.TLabel", background=CARD, foreground=TEXT, font=ui_font(12))
        style.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=ui_font(11))
        style.configure("Header.TLabel", background=CARD, foreground=TEXT, font=ui_font(14, bold=True))
        style.configure("Treeview", font=mono_font(11), rowheight=30, fieldbackground=CARD, background=CARD, foreground=TEXT)
        style.configure("Treeview.Heading", font=ui_font(11, bold=True), background=SOFT_BG, foreground=TEXT)
        style.map("Treeview", background=[("selected", ACCENT_LIGHT)], foreground=[("selected", TEXT)])

    def _load_app_icon(self) -> None:
        if APP_ICON_PNG.exists():
            try:
                with Image.open(APP_ICON_PNG) as image:
                    header_icon = image.copy()
                    header_icon.thumbnail((40, 40), Image.Resampling.LANCZOS)
                    self.header_icon_image = ImageTk.PhotoImage(header_icon)

                self.app_icon_image = tk.PhotoImage(file=str(APP_ICON_PNG))
                self.root.iconphoto(True, self.app_icon_image)
            except Exception:
                self.app_icon_image = None
                self.header_icon_image = None

        if IS_WINDOWS and APP_ICON_ICO.exists():
            try:
                self.root.iconbitmap(str(APP_ICON_ICO))
            except Exception:
                pass

    def _apply_branding(self) -> None:
        outer = self.root.winfo_children()[0] if self.root.winfo_children() else None
        if outer is None:
            return

        top = outer.winfo_children()[0] if outer.winfo_children() else None
        if top is None:
            return

        title_label = None
        subtitle_label = None
        for child in top.winfo_children():
            if isinstance(child, ttk.Label) and child.cget("text") == "iPod Theme Studio":
                title_label = child
            elif isinstance(child, tk.Label):
                subtitle_label = child

        if subtitle_label is not None:
            subtitle_label.configure(text="定制属于你自己的 iPod nano 6/7")

        if title_label is not None and self.header_icon_image is not None:
            existing_icon = getattr(self, "_header_icon_label", None)
            if existing_icon is None or not existing_icon.winfo_exists():
                self._header_icon_label = tk.Label(top, image=self.header_icon_image, bg=BG)
                self._header_icon_label.pack(side="left", padx=(0, 10), before=title_label)

    def _build_layout(self) -> None:
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)

        top = ttk.Frame(outer)
        top.pack(fill="x")

        title_row = ttk.Frame(top)
        title_row.pack(side="left")

        if self.header_icon_image is not None:
            tk.Label(title_row, image=self.header_icon_image, bg=BG).pack(side="left", padx=(0, 10))

        title = ttk.Label(title_row, text="iPod Theme Studio", style="Title.TLabel")
        title.pack(side="left")

        subtitle = tk.Label(
            top,
            text="把官方或社区 IPSW 变成适合小白使用的素材替换工作台",
            bg=BG,
            fg=MUTED,
            font=ui_font(12),
        )
        subtitle.pack(side="left", padx=(14, 0), pady=(8, 0))

        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True, pady=(16, 0))
        body.columnconfigure(0, weight=0)
        body.columnconfigure(0, minsize=320 if not IS_MACOS else 296)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        self._build_sidebar(body)
        self._build_workspace(body)

    def _build_sidebar(self, parent: ttk.Frame) -> None:
        sidebar_card = ttk.Frame(parent, style="Card.TFrame", padding=0)
        sidebar_card.grid(row=0, column=0, sticky="nsew")
        sidebar_card.columnconfigure(0, weight=1)
        sidebar_card.rowconfigure(0, weight=1)

        sidebar_scroll = ttk.Scrollbar(sidebar_card, orient="vertical")
        sidebar_scroll.grid(row=0, column=1, sticky="ns")

        self.sidebar_canvas = tk.Canvas(
            sidebar_card,
            bg=CARD,
            highlightthickness=0,
            yscrollcommand=sidebar_scroll.set,
        )
        self.sidebar_canvas.grid(row=0, column=0, sticky="nsew")
        sidebar_scroll.configure(command=self.sidebar_canvas.yview)

        sidebar = ttk.Frame(self.sidebar_canvas, style="Card.TFrame", padding=16)
        self.sidebar_canvas_window = self.sidebar_canvas.create_window((0, 0), window=sidebar, anchor="nw")
        sidebar.bind("<Configure>", self._sync_sidebar_scroll_region)
        self.sidebar_canvas.bind("<Configure>", self._resize_sidebar_panel)

        ttk.Label(sidebar, text="工作流", style="Header.TLabel").pack(anchor="w")

        ttk.Label(sidebar, text="设备型号", style="Body.TLabel").pack(anchor="w", pady=(14, 4))
        device_box = ttk.Combobox(
            sidebar,
            textvariable=self.device_var,
            state="readonly",
            values=["nano6", "nano7-2012", "nano7-2015"],
            width=24,
        )
        device_box.pack(anchor="w", fill="x")
        device_box.bind("<<ComboboxSelected>>", self._on_device_changed)

        self.sidebar_tips_label = tk.Label(
            sidebar,
            text="推荐先做官方固件流程，再决定是否导入社区 IPSW。",
            bg=CARD,
            fg=MUTED,
            justify="left",
            wraplength=286,
            font=ui_font(11),
        )
        self.sidebar_tips_label.pack(anchor="w", pady=(12, 18))

        self._add_sidebar_button(sidebar, "下载官方 IPSW 备份", self._download_official_backup)
        self._add_sidebar_button(sidebar, "加载官方固件并解包", self._import_official, variant="primary")
        self._add_sidebar_button(sidebar, "导入社区 IPSW", self._import_community_ipsw)
        self._add_sidebar_button(sidebar, "打开 body 目录", self._open_body_dir)
        self._add_sidebar_button(sidebar, "查看已保存素材", self._show_saved_assets)
        self._add_sidebar_button(sidebar, "重新扫描素材列表", self._refresh_assets)
        self._add_sidebar_button(sidebar, "生成修改后的 IPSW", self._build_ipsw, variant="primary")
        self._add_sidebar_button(sidebar, "关于与版权", self._show_about)

        self._add_sidebar_button(sidebar, "查看字体槽位", self._show_font_slots)

        status_card = tk.Frame(sidebar, bg=STATUS_BG, padx=16, pady=16, highlightthickness=1, highlightbackground="#244d86")
        status_card.pack(fill="x", pady=(18, 0))

        tk.Label(
            status_card,
            text="当前状态",
            bg=STATUS_BG,
            fg=STATUS_TEXT,
            font=ui_font(13, bold=True),
        ).pack(anchor="w")
        self.sidebar_status_label = tk.Label(
            status_card,
            textvariable=self.status_var,
            bg=STATUS_BG,
            fg="white",
            justify="left",
            wraplength=286,
            font=ui_font(11),
        )
        self.sidebar_status_label.pack(anchor="w", pady=(8, 0))

        capacity_card = tk.Frame(sidebar, bg=SOFT_BG, padx=14, pady=14, highlightthickness=1, highlightbackground=BORDER)
        capacity_card.pack(fill="x", pady=(12, 0))

        tk.Label(
            capacity_card,
            text="容量提醒",
            bg=SOFT_BG,
            fg=TEXT,
            font=ui_font(12, bold=True),
        ).pack(anchor="w")
        self.capacity_label = tk.Label(
            capacity_card,
            textvariable=self.capacity_var,
            bg=SOFT_BG,
            fg=TEXT,
            justify="left",
            wraplength=286,
            font=ui_font(10),
        )
        self.capacity_label.pack(anchor="w", pady=(8, 0))

    def _build_workspace(self, parent: ttk.Frame) -> None:
        workspace = ttk.Frame(parent)
        workspace.grid(row=0, column=1, sticky="nsew", padx=(16, 0))
        workspace.rowconfigure(1, weight=1)
        workspace.rowconfigure(2, weight=0)
        workspace.columnconfigure(0, weight=1)
        workspace.columnconfigure(1, weight=4 if IS_MACOS else 0)

        header = ttk.Frame(workspace, style="Card.TFrame", padding=16)
        header.grid(row=0, column=0, columnspan=2, sticky="ew")

        tk.Label(
            header,
            textvariable=self.source_var,
            bg=CARD,
            fg=TEXT,
            font=ui_font(13, bold=True),
        ).pack(anchor="w")
        tk.Label(
            header,
            text="左侧选择要替换的系统素材，右侧可以预览并替换。打包时会自动按原文件名和格式写回。",
            bg=CARD,
            fg=MUTED,
            justify="left",
            wraplength=950,
            font=ui_font(11),
        ).pack(anchor="w", pady=(8, 0))

        list_card = ttk.Frame(workspace, style="Card.TFrame", padding=12)
        list_card.grid(row=1, column=0, sticky="nsew", pady=(16, 0))
        list_card.rowconfigure(2, weight=1)
        list_card.columnconfigure(0, weight=1)

        ttk.Label(list_card, text="美术资源列表", style="Header.TLabel").grid(row=0, column=0, sticky="w", padx=4, pady=(0, 8))

        filter_row = tk.Frame(list_card, bg=CARD)
        filter_row.grid(row=1, column=0, columnspan=2, sticky="ew", padx=4, pady=(0, 10))
        filter_row.columnconfigure(1, weight=1)

        tk.Label(
            filter_row,
            text="快捷分组",
            bg=CARD,
            fg=TEXT,
            font=ui_font(11, bold=True),
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))

        self.group_box = ttk.Combobox(
            filter_row,
            textvariable=self.group_var,
            state="readonly",
            values=["全部素材"],
        )
        self.group_box.grid(row=0, column=1, sticky="ew")
        self.group_box.bind("<<ComboboxSelected>>", self._jump_to_selected_group)

        columns = ("id", "format", "size", "group", "name")
        self.asset_tree = ttk.Treeview(list_card, columns=columns, show="headings", selectmode="browse")
        self.asset_tree.heading("id", text="ID")
        self.asset_tree.heading("format", text="格式")
        self.asset_tree.heading("size", text="尺寸")
        self.asset_tree.heading("group", text="分组")
        self.asset_tree.heading("name", text="文件名")
        self.asset_tree.column("id", width=92, minwidth=82, stretch=False, anchor="w")
        self.asset_tree.column("format", width=72, minwidth=64, stretch=False, anchor="center")
        self.asset_tree.column("size", width=88, minwidth=78, stretch=False, anchor="center")
        self.asset_tree.column("group", width=132, minwidth=112, stretch=False, anchor="w")
        self.asset_tree.column("name", width=180, minwidth=140, stretch=True, anchor="w")
        self.asset_tree.grid(row=2, column=0, sticky="nsew")
        self.asset_tree.bind("<<TreeviewSelect>>", self._on_asset_selected)

        scrollbar = ttk.Scrollbar(list_card, orient="vertical", command=self.asset_tree.yview)
        self.asset_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=2, column=1, sticky="ns")

        preview_card = ttk.Frame(workspace, style="Card.TFrame", padding=14)
        preview_card.grid(row=1, column=1, sticky="nsew", padx=(16, 0), pady=(16, 0))
        preview_card.columnconfigure(0, weight=1)
        preview_card.rowconfigure(0, weight=1)

        preview_scroll = ttk.Scrollbar(preview_card, orient="vertical")
        preview_scroll.grid(row=0, column=1, sticky="ns")

        self.preview_panel_canvas = tk.Canvas(
            preview_card,
            bg=CARD,
            highlightthickness=0,
            yscrollcommand=preview_scroll.set,
        )
        self.preview_panel_canvas.grid(row=0, column=0, sticky="nsew")
        preview_scroll.configure(command=self.preview_panel_canvas.yview)

        self.preview_panel = tk.Frame(self.preview_panel_canvas, bg=CARD)
        self.preview_panel_canvas_window = self.preview_panel_canvas.create_window(
            (0, 0),
            window=self.preview_panel,
            anchor="nw",
        )
        self.preview_panel.bind("<Configure>", self._sync_preview_scroll_region)
        self.preview_panel_canvas.bind("<Configure>", self._resize_preview_panel)

        ttk.Label(self.preview_panel, text="预览与替换", style="Header.TLabel").grid(row=0, column=0, sticky="w")

        self.preview_canvas = tk.Canvas(
            self.preview_panel,
            width=PREVIEW_CANVAS_WIDTH,
            height=PREVIEW_CANVAS_HEIGHT,
            bg=SOFT_BG,
            highlightthickness=0,
            relief="flat",
        )
        self.preview_canvas.grid(row=1, column=0, sticky="nsew", pady=(10, 10))
        self.preview_placeholder_id = self.preview_canvas.create_text(
            PREVIEW_CANVAS_WIDTH // 2,
            PREVIEW_CANVAS_HEIGHT // 2,
            text="暂无预览",
            fill=MUTED,
            font=ui_font(12),
        )

        self.meta_label = tk.Label(
            self.preview_panel,
            bg=CARD,
            fg=TEXT,
            justify="left",
            anchor="w",
            wraplength=330,
            font=mono_font(11),
        )
        self.meta_label.grid(row=2, column=0, sticky="ew")

        tk.Label(
            self.preview_panel,
            textvariable=self.notes_var,
            bg=CARD,
            fg=MUTED,
            justify="left",
            wraplength=330,
            font=ui_font(11),
        ).grid(row=3, column=0, sticky="ew", pady=(10, 12))

        action_row = tk.Frame(self.preview_panel, bg=CARD)
        action_row.grid(row=4, column=0, sticky="ew")
        action_row.columnconfigure(0, weight=1)
        action_row.columnconfigure(1, weight=1)

        replace_btn = tk.Button(
            action_row,
            text="替换当前素材",
            command=self._replace_current_asset,
            **button_style("primary"),
            relief="flat",
            bd=0,
            padx=16,
            pady=12,
            font=ui_font(11, bold=True),
        )
        replace_btn.grid(row=0, column=0, sticky="ew")

        save_btn = tk.Button(
            action_row,
            text="保存当前素材",
            command=self._save_current_asset,
            **button_style("secondary"),
            relief="flat",
            bd=0,
            padx=16,
            pady=12,
            font=ui_font(11, bold=True),
        )
        save_btn.grid(row=0, column=1, sticky="ew", padx=(10, 0))

        reduce_btn = tk.Button(
            action_row,
            text="手动改色彩",
            command=self._reduce_color_and_replace,
            **button_style("secondary"),
            relief="flat",
            bd=0,
            padx=16,
            pady=12,
            font=ui_font(11, bold=True),
        )
        reduce_btn.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        log_card = ttk.Frame(workspace, style="Card.TFrame", padding=14)
        log_card.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        log_card.columnconfigure(0, weight=1)

        ttk.Label(log_card, text="日志", style="Header.TLabel").grid(row=0, column=0, sticky="w")
        self.log_text = tk.Text(
            log_card,
            height=10,
            bg=LOG_BG,
            fg=LOG_TEXT,
            insertbackground=LOG_TEXT,
            relief="flat",
            font=mono_font(11),
            padx=10,
            pady=10,
        )
        self.log_text.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self.log_text.configure(state="disabled")

    def _add_sidebar_button(self, parent: ttk.Frame, text: str, command, variant: str = "secondary") -> None:
        button = tk.Button(
            parent,
            text=text,
            command=command,
            **button_style(variant),
            relief="flat",
            anchor="w",
            bd=0,
            padx=14,
            pady=12 if IS_MACOS else 10,
            font=ui_font(11 if IS_MACOS else 10, bold=True),
        )
        button.pack(fill="x", pady=4)

    def _load_existing_session(self) -> None:
        session = self.studio.load_session()
        if session.device_key:
            self.device_var.set(session.device_key)
        self._refresh_group_options()
        if session.source_label:
            self.source_var.set(f"当前来源：{session.source_label}")
            self.status_var.set("已从上次工作区恢复素材列表。")
        self._update_capacity_hint()

    def _drain_log_queue(self) -> None:
        while True:
            try:
                level, message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self._append_log(level, message)
            if level == "done":
                self.busy = False
            elif level == "refresh":
                self._refresh_assets()
            elif level == "source":
                self.source_var.set(message)
            elif level == "status":
                self.status_var.set(message)
            elif level == "notes":
                self.notes_var.set(message)

        self.root.after(120, self._drain_log_queue)

    def _append_log(self, level: str, message: str) -> None:
        if level not in {"log", "error"}:
            return
        prefix = "[ERROR] " if level == "error" else ""
        self.log_text.configure(state="normal")
        self.log_text.insert("end", prefix + message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _run_task(self, title: str, fn) -> None:
        if self.busy:
            messagebox.showinfo("请稍等", "当前还有任务在执行。")
            return

        self.busy = True
        self.status_var.set(title)
        self.log_queue.put(("log", f"== {title} =="))

        def worker() -> None:
            try:
                fn()
            except StudioError as exc:
                self.log_queue.put(("error", str(exc)))
                self.log_queue.put(("status", "操作失败，请查看日志。"))
            except Exception as exc:  # pragma: no cover - UI fallback
                self.log_queue.put(("error", f"未处理异常: {exc}"))
                self.log_queue.put(("status", "出现未处理异常，请查看日志。"))
            finally:
                self.log_queue.put(("done", ""))

        threading.Thread(target=worker, daemon=True).start()

    def _current_device(self) -> str:
        return self.device_var.get().strip()

    def _selected_group_key(self) -> str:
        return self.group_map.get(self.group_var.get(), "all")

    def _refresh_group_options(self) -> None:
        groups = self.studio.get_artwork_groups(self._current_device())
        self.group_map = {group["label"]: group["key"] for group in groups}
        labels = list(self.group_map.keys()) or ["全部素材"]
        current = self.group_var.get()
        if current not in labels:
            current = labels[0]
        self.group_box.configure(values=labels)
        self.group_var.set(current)

    def _update_capacity_hint(self) -> None:
        self._capacity_refresh_token += 1
        token = self._capacity_refresh_token
        self.capacity_var.set("正在估算当前素材集的打包体积和 rsrc 分区余量……")

        def worker() -> None:
            try:
                packed_size = self.studio.estimate_packed_silverdb_size()
                summary = self.studio.capacity_summary(packed_size)
            except StudioError:
                summary = self.studio.capacity_summary()
            except Exception as exc:  # pragma: no cover - UI fallback
                summary = f"容量提醒：暂时无法估算当前打包体积，原因：{exc}"

            def apply_result() -> None:
                if token != self._capacity_refresh_token:
                    return
                self.capacity_var.set(summary)

            self.root.after(0, apply_result)

        threading.Thread(target=worker, daemon=True).start()

    def _on_device_changed(self, _event=None) -> None:
        self._refresh_group_options()
        self._refresh_assets()

    def _jump_to_selected_group(self, _event=None) -> None:
        self._refresh_assets()
        children = self.asset_tree.get_children()
        if children:
            first = children[0]
            self.asset_tree.selection_set(first)
            self.asset_tree.focus(first)
            self.asset_tree.see(first)
            self._on_asset_selected()

    def _download_official_backup(self) -> None:
        device_key = self._current_device()

        def task() -> None:
            backup_path = self.studio.download_official_backup(device_key, self._log_from_worker)
            self.log_queue.put(("status", f"官方 IPSW 备份已就绪：{backup_path}"))
            self.log_queue.put(("notes", f"官方 IPSW 备份位置：{backup_path}"))

        self._run_task("下载官方 IPSW 备份", task)

    def _import_official(self) -> None:
        device_key = self._current_device()

        def task() -> None:
            session = self.studio.import_official_firmware(device_key, self._log_from_worker)
            self.log_queue.put(("source", f"当前来源：{session.source_label}"))
            self.log_queue.put(("status", "官方固件已解包，可以浏览素材。"))
            self.log_queue.put(("notes", "选择任意素材后，可以替换成自己的 PNG。"))
            self.log_queue.put(("refresh", ""))

        self._run_task("加载官方固件并解包", task)

    def _import_community_ipsw(self) -> None:
        path = filedialog.askopenfilename(
            title="选择社区 IPSW",
            filetypes=[("IPSW firmware", "*.ipsw"), ("Zip archive", "*.zip"), ("All files", "*.*")],
        )
        if not path:
            return

        device_key = self._current_device()
        ipsw_path = Path(path)

        def task() -> None:
            session = self.studio.import_community_ipsw(device_key, ipsw_path, self._log_from_worker)
            self.log_queue.put(("source", f"当前来源：{session.source_label}"))
            self.log_queue.put(("status", "社区 IPSW 已解包，可以浏览素材。"))
            self.log_queue.put(("notes", "替换后的打包结果会保留社区 IPSW 的其余内容。"))
            self.log_queue.put(("refresh", ""))

        self._run_task("导入社区 IPSW", task)

    def _refresh_assets(self) -> None:
        self.asset_tree.delete(*self.asset_tree.get_children())
        self.current_selection = None
        items = self.studio.list_artwork(self._selected_group_key())
        for item in items:
            self.asset_tree.insert(
                "",
                "end",
                iid=item["name"],
                values=(item["id"], item["format"], item["size"], item.get("group", "未分组"), item["name"]),
            )

        if items:
            self.status_var.set(f"已载入 {len(items)} 张素材。")
        else:
            self.status_var.set("当前还没有可浏览的素材。")

        self._clear_preview()
        self.meta_label.configure(text="")
        self._update_capacity_hint()

    def _on_asset_selected(self, _event=None) -> None:
        selection = self.asset_tree.selection()
        if not selection:
            return

        name = selection[0]
        path = self.studio.body_dir() / name
        self.current_selection = name

        with Image.open(path) as image:
            preview = image.convert("RGBA")
            preview.thumbnail((PREVIEW_CANVAS_WIDTH - 20, PREVIEW_CANVAS_HEIGHT - 20))
            self.preview_image = ImageTk.PhotoImage(preview)
            size = f"{image.size[0]}x{image.size[1]}"

        self._draw_preview()

        image_id, image_format = path.stem.split("_")
        self.meta_label.configure(
            text="\n".join(
                [
                    f"文件名: {path.name}",
                    f"素材 ID: {image_id}",
                    f"格式码: {image_format}",
                    f"分组:   {self.studio.describe_artwork_group({'id': image_id, 'size': size}, self._current_device())}",
                    f"尺寸:   {size}",
                    f"路径:   {path}",
                ]
            )
        )

        if image_format in {"0064", "0065"}:
            self.notes_var.set("调色板素材：程序会先尝试保留原格式；如果颜色超限，会自动改成同 ID 的 _1888.png。")
        elif image_format in {"0004", "0008"}:
            self.notes_var.set("灰度素材：替换图只要尺寸一致即可，打包时会自动转灰度。")
        elif image_format == "0565":
            self.notes_var.set("RGB565 素材：替换图尺寸一致即可，打包时会自动转换为 16 位色。")
        else:
            self.notes_var.set("RGBA 素材：可以直接用任意同尺寸 PNG 替换。")

    def _replace_current_asset(self) -> None:
        if not self.current_selection:
            messagebox.showinfo("先选素材", "请先在左侧列表中选中一个系统素材。")
            return

        path = filedialog.askopenfilename(
            title="选择要替换进去的图片",
            filetypes=[("PNG image", "*.png"), ("All files", "*.*")],
        )
        if not path:
            return

        candidate = Path(path)
        try:
            new_name, notes = self.studio.replace_artwork(self.current_selection, candidate)
        except StudioError as exc:
            messagebox.showerror("替换失败", str(exc))
            return

        old_name = self.current_selection
        self._append_log("log", f"已替换 {old_name} <- {candidate.name}")
        self.notes_var.set("；".join(notes))
        self._refresh_assets()
        if self.asset_tree.exists(new_name):
            self.asset_tree.selection_set(new_name)
            self.asset_tree.focus(new_name)
            self.asset_tree.see(new_name)
            self.current_selection = new_name
            self._on_asset_selected()

    def _build_ipsw(self) -> None:
        default_path = self.studio.default_output_path()
        save_path = filedialog.asksaveasfilename(
            title="保存修改后的 IPSW",
            defaultextension=".ipsw",
            initialfile=default_path.name,
            initialdir=str(default_path.parent),
            filetypes=[("IPSW firmware", "*.ipsw")],
        )
        if not save_path:
            return

        def task() -> None:
            output = self.studio.build_ipsw(Path(save_path), self._log_from_worker)
            self.log_queue.put(("status", f"打包完成：{output.name}"))
            self.log_queue.put(("notes", "生成好的 IPSW 仍然建议通过 iTunes/Apple Devices 刷入。"))

        self._run_task("生成修改后的 IPSW", task)

    def _open_body_dir(self) -> None:
        body_dir = self.studio.body_dir()
        if not body_dir.exists():
            messagebox.showinfo("还没有素材目录", "请先导入官方固件或社区 IPSW。")
            return

        open_in_file_manager(body_dir, parent=self.root)

    def _clear_preview(self) -> None:
        self.preview_image = None
        if self.preview_canvas_image_id is not None:
            self.preview_canvas.delete(self.preview_canvas_image_id)
            self.preview_canvas_image_id = None
        self.preview_canvas.itemconfigure(self.preview_placeholder_id, state="normal")

    def _sync_preview_scroll_region(self, _event=None) -> None:
        self.preview_panel_canvas.configure(scrollregion=self.preview_panel_canvas.bbox("all"))

    def _resize_preview_panel(self, event) -> None:
        self.preview_panel_canvas.itemconfigure(self.preview_panel_canvas_window, width=event.width)

    def _sync_sidebar_scroll_region(self, _event=None) -> None:
        self.sidebar_canvas.configure(scrollregion=self.sidebar_canvas.bbox("all"))

    def _resize_sidebar_panel(self, event) -> None:
        panel_width = max(int(event.width), 1)
        self.sidebar_canvas.itemconfigure(self.sidebar_canvas_window, width=panel_width)

        outer_wrap = max(180, panel_width - 42)
        card_wrap = max(160, panel_width - 78)

        self.sidebar_tips_label.configure(wraplength=outer_wrap)
        self.sidebar_status_label.configure(wraplength=card_wrap)
        self.capacity_label.configure(wraplength=card_wrap)

    def _draw_preview(self) -> None:
        if self.preview_image is None:
            self._clear_preview()
            return

        if self.preview_canvas_image_id is not None:
            self.preview_canvas.delete(self.preview_canvas_image_id)

        self.preview_canvas_image_id = self.preview_canvas.create_image(
            PREVIEW_CANVAS_WIDTH // 2,
            PREVIEW_CANVAS_HEIGHT // 2,
            image=self.preview_image,
            anchor="center",
        )
        self.preview_canvas.itemconfigure(self.preview_placeholder_id, state="hidden")

    def _replace_current_asset(self) -> None:
        if not self.current_selection:
            messagebox.showinfo("先选素材", "请先在左侧列表中选中一个系统素材。")
            return

        path = filedialog.askopenfilename(
            title="选择要替换进去的图片",
            filetypes=IMAGE_FILE_TYPES,
        )
        if not path:
            return

        candidate = Path(path)
        try:
            prepared_candidate = self._prepare_replacement_candidate(self.current_selection, candidate)
        except StudioError as exc:
            messagebox.showerror("处理图片失败", str(exc))
            return

        if prepared_candidate is None:
            return

        try:
            new_name, notes = self.studio.replace_artwork(self.current_selection, prepared_candidate)
        except StudioError as exc:
            messagebox.showerror("替换失败", str(exc))
            return

        old_name = self.current_selection
        self._append_log("log", f"已替换 {old_name} <- {candidate.name}")
        if prepared_candidate != candidate:
            self._append_log("log", "已先生成适配后的中间 PNG，并按目标素材规则完成替换。")
        self.notes_var.set("；".join(notes))
        self._refresh_assets()
        if self.asset_tree.exists(new_name):
            self.asset_tree.selection_set(new_name)
            self.asset_tree.focus(new_name)
            self.asset_tree.see(new_name)
            self.current_selection = new_name
            self._on_asset_selected()

    def _prepare_replacement_candidate(self, target_name: str, candidate: Path) -> Path | None:
        target_path = self.studio.body_dir() / target_name
        if not target_path.exists():
            raise StudioError(f"没有找到目标素材：{target_name}")

        with Image.open(target_path) as target_image:
            target_size = target_image.size

        with Image.open(candidate) as candidate_image:
            candidate_size = candidate_image.size

        if candidate_size != target_size:
            messagebox.showinfo(
                "需要先裁剪",
                (
                    f"新图尺寸是 {candidate_size[0]}x{candidate_size[1]}，但目标素材需要 "
                    f"{target_size[0]}x{target_size[1]}。\n\n"
                    "接下来会打开内置裁剪窗口，你可以像手机相册那样拖动和缩放来取景。"
                ),
            )
            return self._open_crop_dialog(candidate, target_name, target_size)

        wants_crop = messagebox.askyesnocancel(
            "如何处理这张图？",
            (
                "这张图片已经是目标尺寸。\n\n"
                "是：打开内置裁剪/缩放窗口，再微调取景\n"
                "否：直接转成 PNG 并替换\n"
                "取消：放弃本次替换"
            ),
        )
        if wants_crop is None:
            return None
        if wants_crop:
            return self._open_crop_dialog(candidate, target_name, target_size)
        return self._stage_candidate_png(candidate, target_name)

    def _open_crop_dialog(self, candidate: Path, target_name: str, target_size: tuple[int, int]) -> Path | None:
        dialog = CropResizeDialog(self.root, candidate, target_name, target_size)
        result = dialog.show()
        if result is None:
            self._append_log("log", "已取消本次内置裁剪操作。")
        return result

    def _stage_candidate_png(self, candidate: Path, target_name: str) -> Path:
        WORK_INPUTS.mkdir(parents=True, exist_ok=True)
        target_stem = Path(target_name).stem
        timestamp = time.strftime("%Y%m%d%H%M%S")
        output_path = WORK_INPUTS / f"{target_stem}_prepared_{timestamp}.png"
        with Image.open(candidate) as image:
            image.convert("RGBA").save(output_path, "PNG")
        return output_path

    def _replace_current_asset(self) -> None:
        if not self.current_selection:
            messagebox.showinfo("先选素材", "请先在左侧列表中选中一个系统素材。")
            return

        candidate = self._choose_replacement_candidate()
        if candidate is None:
            return

        try:
            prepared_candidate = self._prepare_replacement_candidate(self.current_selection, candidate)
        except StudioError as exc:
            messagebox.showerror("处理图片失败", str(exc))
            return

        if prepared_candidate is None:
            return

        try:
            new_name, notes = self.studio.replace_artwork(self.current_selection, prepared_candidate)
        except StudioError as exc:
            messagebox.showerror("替换失败", str(exc))
            return

        old_name = self.current_selection
        self._append_log("log", f"已替换 {old_name} <- {candidate.name}")
        if prepared_candidate != candidate:
            self._append_log("log", f"已先生成适配后的 PNG：{prepared_candidate.name}")
        self.notes_var.set("；".join(notes))
        self._refresh_assets()
        if self.asset_tree.exists(new_name):
            self.asset_tree.selection_set(new_name)
            self.asset_tree.focus(new_name)
            self.asset_tree.see(new_name)
            self.current_selection = new_name
            self._on_asset_selected()

    def _choose_replacement_candidate(self) -> Path | None:
        source_choice = messagebox.askyesnocancel(
            "替换来源",
            "是：从电脑选择新图片\n否：从收藏素材中选择\n取消：放弃本次替换",
        )
        if source_choice is None:
            return None
        if source_choice:
            path = filedialog.askopenfilename(
                title="选择要替换进去的图片",
                filetypes=IMAGE_FILE_TYPES,
            )
            return Path(path) if path else None
        return self._pick_saved_asset()

    def _pick_saved_asset(self) -> Path | None:
        if not self.studio.list_saved_artwork():
            messagebox.showinfo("还没有收藏素材", "当前收藏库还是空的，先用“保存当前素材”收集几张喜欢的图吧。")
            return None
        dialog = SavedAssetBrowserDialog(self.root, self.studio, pick_mode=True)
        return dialog.show()

    def _show_saved_assets(self) -> None:
        SavedAssetBrowserDialog(self.root, self.studio, pick_mode=False).show()

    def _save_current_asset(self) -> None:
        if not self.current_selection:
            messagebox.showinfo("先选素材", "请先在左侧列表中选中一个系统素材。")
            return

        try:
            saved_path = self.studio.save_artwork_copy(self.current_selection)
        except StudioError as exc:
            messagebox.showerror("保存失败", str(exc))
            return

        self._append_log("log", f"已收藏素材：{saved_path.name}")
        messagebox.showinfo("保存完成", f"已保存到收藏素材库：\n{saved_path}")

    def _pick_saved_asset(self) -> Path | None:
        if not self.studio.list_saved_artwork():
            messagebox.showinfo("还没有收藏素材", "当前收藏库还是空的，先用“保存当前素材”收集几张喜欢的图吧。")
            return None
        dialog = SavedAssetBrowserDialog(
            self.root,
            self.studio,
            pick_mode=True,
            import_callback=self._import_file_to_saved_assets,
            reduce_callback=self._reduce_saved_library_asset,
        )
        return dialog.show()

    def _show_saved_assets(self) -> None:
        SavedAssetBrowserDialog(
            self.root,
            self.studio,
            pick_mode=False,
            import_callback=self._import_file_to_saved_assets,
            reduce_callback=self._reduce_saved_library_asset,
        ).show()

    def _save_current_asset(self) -> None:
        if not self.current_selection:
            messagebox.showinfo("先选素材", "请先在左侧列表中选中一个系统素材。")
            return

        note = self._ask_saved_asset_note("")
        if note is None:
            return

        try:
            saved_path = self.studio.save_artwork_copy(self.current_selection, note=note)
        except StudioError as exc:
            messagebox.showerror("保存失败", str(exc))
            return

        self._append_log("log", f"已收藏素材：{saved_path.name}")
        messagebox.showinfo("保存完成", f"已保存到收藏素材库：\n{saved_path}")

    def _import_file_to_saved_assets(self) -> Path | None:
        path = filedialog.askopenfilename(
            title="选择要导入到素材库的图片",
            filetypes=IMAGE_FILE_TYPES,
        )
        if not path:
            return None

        source = Path(path)
        note = self._ask_saved_asset_note(source.stem)
        if note is None:
            return None

        resize_now = messagebox.askyesnocancel(
            "导入方式",
            "是：现在就输入目标分辨率并进入裁剪/缩放\n否：原图直接保存到素材库\n取消：放弃本次导入",
        )
        if resize_now is None:
            return None

        candidate = source
        if resize_now:
            target_size = self._ask_target_size()
            if target_size is None:
                return None
            cropped = self._open_crop_dialog(source, source.name, target_size)
            if cropped is None:
                return None
            candidate = cropped

        try:
            saved_path = self.studio.import_saved_asset(candidate, note=note, preferred_name=source.name)
        except StudioError as exc:
            messagebox.showerror("导入失败", str(exc))
            return None

        self._append_log("log", f"已导入收藏素材：{saved_path.name}")
        return saved_path

    def _ask_saved_asset_note(self, initial_value: str) -> str | None:
        return simpledialog.askstring(
            "素材备注",
            "给这张收藏素材写个备注吧，可留空：",
            initialvalue=initial_value,
            parent=self.root,
        )

    def _ask_target_size(self) -> tuple[int, int] | None:
        value = simpledialog.askstring(
            "目标分辨率",
            "输入目标分辨率，格式例如 240x432：",
            parent=self.root,
        )
        if value is None:
            return None

        text = value.lower().replace(" ", "")
        if "x" not in text:
            messagebox.showerror("格式不正确", "请输入类似 240x432 的分辨率格式。")
            return None

        width_text, height_text = text.split("x", 1)
        if not width_text.isdigit() or not height_text.isdigit():
            messagebox.showerror("格式不正确", "宽和高都必须是正整数。")
            return None

        width = int(width_text)
        height = int(height_text)
        if width <= 0 or height <= 0:
            messagebox.showerror("格式不正确", "宽和高都必须大于 0。")
            return None
        return width, height

    def _reduce_saved_library_asset(self, asset_path: Path, item: dict[str, str]) -> Path | None:
        current_format = item.get("format", "")
        if current_format != "1888":
            messagebox.showinfo("当前素材不适用", "素材库里的这个条目当前不是 1888，无需使用这个降色按钮。", parent=self.root)
            return None

        target_choice = messagebox.askyesnocancel(
            "目标降色格式",
            "是：降到 0064\n否：降到 0065\n取消：放弃本次降色",
            parent=self.root,
        )
        if target_choice is None:
            return None
        target_format = "0064" if target_choice else "0065"

        action, strategy = self._open_reduction_decision(asset_path, target_format, allow_keep_original=False)
        if action != "reduced":
            return None

        try:
            reduced_candidate, reduction_notes = self._create_reduced_candidate(
                asset_path.name,
                asset_path,
                target_format,
                strategy,
            )
            image_id = item.get("id", "")
            output_name = f"{image_id}_{target_format}.png" if image_id else asset_path.name
            updated_path = self.studio.replace_saved_artwork(asset_path, reduced_candidate, output_name=output_name)
        except StudioError as exc:
            messagebox.showerror("降色失败", str(exc), parent=self.root)
            return None

        self._append_log("log", f"已将收藏素材降色为 {target_format}：{updated_path.name}")
        self._append_replacement_notes_to_log(reduction_notes)
        return updated_path

    def _resize_saved_library_asset(self, asset_path: Path, item: dict[str, str]) -> Path | None:
        target_size = self._ask_target_size()
        if target_size is None:
            return None

        cropped = self._open_crop_dialog(asset_path, asset_path.name, target_size)
        if cropped is None:
            return None

        try:
            updated_path = self.studio.replace_saved_artwork(asset_path, cropped, output_name=asset_path.name)
        except StudioError as exc:
            messagebox.showerror("调整尺寸失败", str(exc), parent=self.root)
            return None

        self._append_log("log", f"已调整收藏素材尺寸：{updated_path.name} -> {target_size[0]}x{target_size[1]}")
        return updated_path

    def _pick_saved_asset(self) -> Path | None:
        if not self.studio.list_saved_artwork():
            messagebox.showinfo("还没有收藏素材", "当前收藏库还是空的，先用“保存当前素材”收集几张喜欢的图吧。")
            return None
        dialog = SavedAssetBrowserDialog(
            self.root,
            self.studio,
            pick_mode=True,
            import_callback=self._import_file_to_saved_assets,
            reduce_callback=self._reduce_saved_library_asset,
            resize_callback=self._resize_saved_library_asset,
        )
        return dialog.show()

    def _show_saved_assets(self) -> None:
        SavedAssetBrowserDialog(
            self.root,
            self.studio,
            pick_mode=False,
            import_callback=self._import_file_to_saved_assets,
            reduce_callback=self._reduce_saved_library_asset,
            resize_callback=self._resize_saved_library_asset,
        ).show()

    def _show_font_slots(self) -> None:
        session = self.studio.load_session()
        if not session.device_key or not session.source_kind:
            messagebox.showinfo("还没有固件工作区", "请先导入官方固件或社区 IPSW，再查看当前固件中的字体槽位。")
            return

        if not self.studio.list_fonts():
            messagebox.showinfo("没有检测到字体槽位", "当前工作区里没有找到 /Resources/Fonts，请先重新导入固件或 IPSW。")
            return

        FontSlotBrowserDialog(
            self.root,
            self.studio,
            log_callback=lambda message: self._append_log("log", message),
        ).show()

    def _ask_target_size(self) -> tuple[int, int] | None:
        dialog = SizeInputDialog(self.root)
        return dialog.show()

    def _replace_current_asset(self) -> None:
        if not self.current_selection:
            messagebox.showinfo("先选素材", "请先在左侧列表中选中一个系统素材。")
            return

        candidate = self._choose_replacement_candidate()
        if candidate is None:
            return

        try:
            prepared_candidate = self._prepare_replacement_candidate(self.current_selection, candidate)
        except StudioError as exc:
            messagebox.showerror("处理图片失败", str(exc))
            return

        if prepared_candidate is None:
            return

        try:
            new_name, notes = self.studio.replace_artwork(self.current_selection, prepared_candidate)
        except StudioError as exc:
            messagebox.showerror("替换失败", str(exc))
            return

        old_name = self.current_selection
        self._append_log("log", f"已替换 {old_name} <- {candidate.name}")
        if prepared_candidate != candidate:
            self._append_log("log", "已先生成适配后的中间 PNG，并按目标素材规则完成替换。")
        self._append_replacement_notes_to_log(notes)
        self.notes_var.set("；".join(notes))
        self._refresh_assets()
        if self.asset_tree.exists(new_name):
            self.asset_tree.selection_set(new_name)
            self.asset_tree.focus(new_name)
            self.asset_tree.see(new_name)
            self.current_selection = new_name
            self._on_asset_selected()

    def _reduce_color_and_replace(self) -> None:
        if not self.current_selection:
            messagebox.showinfo("先选素材", "请先在左侧列表中选中一个系统素材。")
            return

        target_format = self.current_selection.split("_", 1)[1].split(".", 1)[0]
        if target_format not in {"0064", "0065"}:
            messagebox.showinfo("当前格式不适用", "降色后替换目前只针对 0064 / 0065 目标素材。")
            return

        candidate = self._choose_replacement_candidate()
        if candidate is None:
            return

        try:
            prepared_candidate = self._prepare_replacement_candidate(self.current_selection, candidate)
            if prepared_candidate is None:
                return
            reduced_candidate, reduction_notes = self._reduce_candidate_for_target_format(
                self.current_selection,
                prepared_candidate,
            )
            new_name, notes = self.studio.replace_artwork(self.current_selection, reduced_candidate)
        except StudioError as exc:
            messagebox.showerror("降色替换失败", str(exc))
            return

        all_notes = reduction_notes + notes
        self._append_log("log", f"已降色并替换 {self.current_selection} <- {candidate.name}")
        self._append_replacement_notes_to_log(all_notes)
        self.notes_var.set("；".join(all_notes))
        self._refresh_assets()
        if self.asset_tree.exists(new_name):
            self.asset_tree.selection_set(new_name)
            self.asset_tree.focus(new_name)
            self.asset_tree.see(new_name)
            self.current_selection = new_name
            self._on_asset_selected()

    def _reduce_candidate_for_target_format(self, target_name: str, candidate_path: Path) -> tuple[Path, list[str]]:
        target_format = target_name.split("_", 1)[1].split(".", 1)[0]
        target_stem = Path(target_name).stem
        timestamp = time.strftime("%Y%m%d%H%M%S")
        output_path = WORK_INPUTS / f"{target_stem}_reduced_{timestamp}.png"
        WORK_INPUTS.mkdir(parents=True, exist_ok=True)

        if target_format == "0064":
            with Image.open(candidate_path) as image:
                reduced = image.convert("RGBA").quantize(colors=255, method=Image.Quantize.FASTOCTREE)
                reduced.save(output_path, "PNG")
            return output_path, ["已手动降色到 0064 目标范围（<=255 色）。"]

        if target_format == "0065":
            with Image.open(candidate_path) as image:
                rgb = image.convert("RGB")
                if np is not None:
                    data = np.array(rgb, dtype=np.uint8)
                    data[..., 0] = (data[..., 0] >> 3) << 3
                    data[..., 1] = (data[..., 1] >> 2) << 2
                    data[..., 2] = (data[..., 2] >> 3) << 3
                    reduced = Image.fromarray(data, "RGB")
                else:
                    red, green, blue = rgb.split()
                    red = red.point(lambda value: (value >> 3) << 3)
                    green = green.point(lambda value: (value >> 2) << 2)
                    blue = blue.point(lambda value: (value >> 3) << 3)
                    reduced = Image.merge("RGB", (red, green, blue))
                reduced.save(output_path, "PNG")
            return output_path, ["已手动降色到接近 RGB565 / 0065 的颜色范围。"]

        raise StudioError(f"当前暂不支持把图片手动降到 {target_format}。")

    def _append_replacement_notes_to_log(self, notes: list[str]) -> None:
        for note in notes:
            if "1888" in note or "降色" in note:
                self._append_log("log", note)

    def _replace_current_asset(self) -> None:
        if not self.current_selection:
            messagebox.showinfo("先选素材", "请先在左侧列表中选中一个系统素材。")
            return

        candidate = self._choose_replacement_candidate()
        if candidate is None:
            return

        try:
            prepared_candidate = self._prepare_replacement_candidate(self.current_selection, candidate)
        except StudioError as exc:
            messagebox.showerror("处理图片失败", str(exc))
            return

        if prepared_candidate is None:
            return

        target_format = self.current_selection.split("_", 1)[1].split(".", 1)[0]
        replacement_candidate = prepared_candidate
        pre_notes: list[str] = []

        try:
            predicted_name, _predicted_notes = self.studio.validate_replacement(self.current_selection, prepared_candidate)
        except StudioError as exc:
            messagebox.showerror("替换失败", str(exc))
            return

        if target_format in {"0064", "0065"} and predicted_name.endswith("_1888.png"):
            action, strategy = self._open_reduction_decision(prepared_candidate, target_format)
            if action == "cancel":
                return
            if action == "reduced":
                replacement_candidate, pre_notes = self._create_reduced_candidate(
                    self.current_selection,
                    prepared_candidate,
                    target_format,
                    strategy,
                )

        try:
            new_name, notes = self.studio.replace_artwork(self.current_selection, replacement_candidate)
        except StudioError as exc:
            messagebox.showerror("替换失败", str(exc))
            return

        all_notes = pre_notes + notes
        self._append_log("log", f"已替换 {self.current_selection} <- {candidate.name}")
        if prepared_candidate != candidate:
            self._append_log("log", "已先生成适配后的中间 PNG，并按目标素材规则完成替换。")
        self._append_replacement_notes_to_log(all_notes)
        self.notes_var.set("；".join(all_notes))
        self._refresh_assets()
        if self.asset_tree.exists(new_name):
            self.asset_tree.selection_set(new_name)
            self.asset_tree.focus(new_name)
            self.asset_tree.see(new_name)
            self.current_selection = new_name
            self._on_asset_selected()

    def _reduce_color_and_replace(self) -> None:
        if not self.current_selection:
            messagebox.showinfo("先选素材", "请先在左侧列表中选中一个系统素材。")
            return

        current_format = self.current_selection.split("_", 1)[1].split(".", 1)[0]
        if current_format != "1888":
            messagebox.showinfo("当前素材不适用", "这个按钮现在只用于把当前已经是 1888 的素材手动降色。")
            return

        target_choice = messagebox.askyesnocancel(
            "目标降色格式",
            "是：降到 0064\n否：降到 0065\n取消：放弃本次降色",
        )
        if target_choice is None:
            return
        target_format = "0064" if target_choice else "0065"

        source_path = self.studio.body_dir() / self.current_selection
        action, strategy = self._open_reduction_decision(source_path, target_format, allow_keep_original=False)
        if action != "reduced":
            return

        try:
            reduced_candidate, reduction_notes = self._create_reduced_candidate(
                self.current_selection,
                source_path,
                target_format,
                strategy,
            )
            new_name, notes = self.studio.replace_artwork_with_format(
                self.current_selection,
                reduced_candidate,
                target_format,
            )
        except StudioError as exc:
            messagebox.showerror("降色失败", str(exc))
            return

        all_notes = reduction_notes + notes
        self._append_log("log", f"已把当前 1888 素材手动降到 {target_format}。")
        self._append_replacement_notes_to_log(all_notes)
        self.notes_var.set("；".join(all_notes))
        self._refresh_assets()
        if self.asset_tree.exists(new_name):
            self.asset_tree.selection_set(new_name)
            self.asset_tree.focus(new_name)
            self.asset_tree.see(new_name)
            self.current_selection = new_name
            self._on_asset_selected()

    def _open_reduction_decision(
        self,
        source_path: Path,
        target_format: str,
        allow_keep_original: bool = True,
    ) -> tuple[str, str]:
        title = "是否尝试转换到原格式" if allow_keep_original else "对当前 1888 素材转换格式"
        dialog = ReductionPreviewDialog(
            self.root,
            source_path,
            target_format,
            self._render_reduced_image_for_strategy,
            allow_keep_original,
            title,
        )
        return dialog.show()

    def _create_reduced_candidate(
        self,
        target_name: str,
        source_path: Path,
        target_format: str,
        strategy: str,
    ) -> tuple[Path, list[str]]:
        with Image.open(source_path) as image:
            reduced = self._render_reduced_image_for_strategy(image, target_format, strategy)
        target_stem = Path(target_name).stem
        timestamp = time.strftime("%Y%m%d%H%M%S")
        output_path = WORK_INPUTS / f"{target_stem}_reduced_{target_format}_{timestamp}.png"
        WORK_INPUTS.mkdir(parents=True, exist_ok=True)
        reduced.save(output_path, "PNG")
        return output_path, [f"已按“{strategy}”策略手动转换到 {target_format}。"]

    def _render_reduced_image_for_strategy(
        self,
        source_image: Image.Image,
        target_format: str,
        strategy: str,
    ) -> Image.Image:
        if target_format in {"0004", "0008"}:
            working = source_image.convert("L")
            if strategy == "平滑":
                working = working.filter(ImageFilter.GaussianBlur(radius=0.35))
                dither = Image.Dither.NONE
            elif strategy == "锐利":
                working = working.filter(ImageFilter.UnsharpMask(radius=0.6, percent=120, threshold=2))
                dither = Image.Dither.FLOYDSTEINBERG
            else:
                dither = Image.Dither.NONE

            colors = 16 if target_format == "0004" else 256
            reduced = working.quantize(colors=colors, method=Image.Quantize.FASTOCTREE, dither=dither)
            return reduced.convert("RGBA")

        if target_format == "0064":
            working = source_image.convert("RGBA")
            if strategy == "平滑":
                working = working.filter(ImageFilter.GaussianBlur(radius=0.35))
                dither = Image.Dither.NONE
            elif strategy == "锐利":
                working = working.filter(ImageFilter.UnsharpMask(radius=0.6, percent=120, threshold=2))
                dither = Image.Dither.FLOYDSTEINBERG
            else:
                dither = Image.Dither.NONE
            reduced = working.quantize(colors=255, method=Image.Quantize.FASTOCTREE, dither=dither)
            return reduced.convert("RGBA")

        if target_format == "0065":
            working = source_image.convert("RGBA")
            if strategy == "平滑":
                working = working.filter(ImageFilter.GaussianBlur(radius=0.35))
            elif strategy == "锐利":
                working = working.filter(ImageFilter.UnsharpMask(radius=0.6, percent=120, threshold=2))

            if image_color_count(working) <= 65535:
                return working.convert("RGBA")

            return rgb565_like_image(working)

        if target_format == "0565":
            working = source_image.convert("RGBA")
            if strategy == "平滑":
                working = working.filter(ImageFilter.GaussianBlur(radius=0.35))
            elif strategy == "锐利":
                working = working.filter(ImageFilter.UnsharpMask(radius=0.6, percent=120, threshold=2))

            return rgb565_like_image(working)

        raise StudioError(f"当前暂不支持预览 {target_format} 的格式转换效果。")

    def _append_replacement_notes_to_log(self, notes: list[str]) -> None:
        for note in notes:
            if "1888" in note or "降到" in note or "转换到" in note or "手动按" in note:
                self._append_log("log", note)

    def _replacement_source_format(self) -> str:
        item = getattr(self, "_last_replacement_item", None)
        if isinstance(item, dict):
            path = item.get("path", "")
            if path:
                detected = _detect_saved_artwork_format(Path(path))
                if detected:
                    return detected
            return item.get("format", "")
        return ""

    def _candidate_meets_output_format(self, candidate_path: Path, output_format: str) -> bool:
        with Image.open(candidate_path) as image:
            rgba = image.convert("RGBA")
            pixels = set(rgba.getdata())
            color_count = len(pixels)
            opaque_only = all(alpha == 255 for _red, _green, _blue, alpha in pixels)

            if output_format == "1888":
                return True
            if output_format == "0004":
                rgb_colors = {(r, g, b) for r, g, b, _a in rgba.getdata()}
                return opaque_only and all(r == g == b for r, g, b in rgb_colors) and len(rgb_colors) <= 16
            if output_format == "0008":
                rgb_colors = {(r, g, b) for r, g, b, _a in rgba.getdata()}
                return opaque_only and all(r == g == b for r, g, b in rgb_colors) and len(rgb_colors) <= 256
            if output_format == "0064":
                return color_count <= 255
            if output_format == "0065":
                return color_count <= 65535
            if output_format == "0565":
                rgb_colors = {(r, g, b) for r, g, b, _a in rgba.getdata()}
                return opaque_only and all(r % 8 == 0 and g % 4 == 0 and b % 8 == 0 for r, g, b in rgb_colors)
        return False

    def _choose_low_color_output_format(
        self,
        target_format: str,
        prepared_format: str,
        prepared_candidate: Path,
        show_direct_notice: bool,
    ) -> tuple[str, bool] | None:
        if target_format == "1888":
            direct_format = prepared_format or "1888"
            if show_direct_notice:
                messagebox.showinfo(
                    "已满足原格式",
                    f"已满足原图格式或更低格式，接下来会直接写回。\n\n原格式：1888\n新图：{direct_format}",
                    parent=self.root,
                )
            return direct_format, False

        if target_format not in {"0004", "0008", "0064", "0065", "0565"}:
            return None

        if target_format == "0565":
            direct_candidates = [
                fmt for fmt in LOW_FORMAT_ORDER if self._candidate_meets_output_format(prepared_candidate, fmt)
            ]
        else:
            direct_candidates = [
                fmt
                for fmt in ("0004", "0008", "0064", "0065")
                if self._candidate_meets_output_format(prepared_candidate, fmt)
            ]

        if prepared_format in direct_candidates:
            direct_format = prepared_format
        else:
            direct_format = direct_candidates[0] if direct_candidates else "1888"

        if direct_format != "1888" and FORMAT_RANK[direct_format] <= FORMAT_RANK[target_format]:
            if show_direct_notice:
                messagebox.showinfo(
                    "已满足原格式",
                    f"已满足原图格式或更低格式，接下来会直接写回。\n\n原格式：{target_format}\n新图：{direct_format}",
                    parent=self.root,
                )
            return direct_format, False

        if direct_format != "1888":
            actions: list[tuple[str, str, bool]] = [
                (direct_format, f"直接写回 {direct_format}（推荐）", True),
                *lower_format_actions(direct_format),
                ("cancel", "取消", False),
            ]
            choice = ActionChoiceDialog(
                self.root,
                "当前图片已经满足较高的低色格式",
                (
                    f"调整尺寸后的新图已经满足 {direct_format}，但原素材是 {target_format}。\n\n"
                    "你可以直接写回当前格式，或者继续往更低的格式转换。"
                ),
                actions,
            ).show()
            if choice in {None, "cancel"}:
                return None
            return choice, choice != direct_format

        choice = ActionChoiceDialog(
            self.root,
            "替换时使用什么格式",
            (
                f"当前目标素材原本是 {target_format}，调整尺寸后的新图属于 1888。\n\n"
                "请选择这次最终要写回成什么格式。只要继续往低色格式转换，就会进入预览。"
            ),
            full_conversion_actions(target_format),
        ).show()
        if choice in {None, "cancel"}:
            return None
        return choice, choice != "1888"

    def _prepare_candidate_for_saved_format(
        self,
        candidate_path: Path,
        target_name: str,
        preferred_format: str,
    ) -> tuple[Path, list[str]]:
        if preferred_format not in {"0004", "0008", "0064", "0065", "0565"}:
            return candidate_path, []

        WORK_INPUTS.mkdir(parents=True, exist_ok=True)
        target_stem = Path(target_name).stem
        timestamp = time.strftime("%Y%m%d%H%M%S")
        output_path = WORK_INPUTS / f"{target_stem}_preserve_{preferred_format}_{timestamp}.png"

        with Image.open(candidate_path) as image:
            prepared = self._render_reduced_image_for_strategy(image, preferred_format, "保守")

        prepared.save(output_path, "PNG")
        return output_path, [f"已按收藏素材的 {preferred_format} 格式特性处理 resize 后的中间图。"]

    def _prepare_replacement_candidate(self, target_name: str, candidate: Path) -> Path | None:
        self._last_prepare_notes = []

        target_path = self.studio.body_dir() / target_name
        if not target_path.exists():
            raise StudioError(f"没有找到目标素材：{target_name}")

        with Image.open(target_path) as target_image:
            target_size = target_image.size

        with Image.open(candidate) as candidate_image:
            candidate_size = candidate_image.size

        if candidate_size != target_size:
            messagebox.showinfo(
                "需要先裁剪",
                (
                    f"新图尺寸是 {candidate_size[0]}x{candidate_size[1]}，但目标素材需要 "
                    f"{target_size[0]}x{target_size[1]}。\n\n"
                    "接下来会打开内置裁剪窗口，你可以像手机相册那样拖动和缩放来取景。"
                ),
            )
            prepared = self._open_crop_dialog(candidate, target_name, target_size)
        else:
            wants_crop = messagebox.askyesnocancel(
                "如何处理这张图？",
                (
                    "这张图片已经是目标尺寸。\n\n"
                    "是：打开内置裁剪/缩放窗口，再微调取景\n"
                    "否：直接转成 PNG 并替换\n"
                    "取消：放弃本次替换"
                ),
            )
            if wants_crop is None:
                return None
            prepared = self._open_crop_dialog(candidate, target_name, target_size) if wants_crop else self._stage_candidate_png(candidate, target_name)

        if prepared is None:
            return None

        preferred_format = self._replacement_source_format()
        if preferred_format and preferred_format != "1888":
            prepared, format_notes = self._prepare_candidate_for_saved_format(prepared, target_name, preferred_format)
            self._last_prepare_notes.extend(format_notes)

        return prepared

    def _choose_replacement_candidate(self) -> Path | None:
        self._last_replacement_item = None
        self._last_prepare_notes = []

        source_choice = messagebox.askyesnocancel(
            "替换来源",
            "是：从电脑选择新图片\n否：从收藏素材中选择\n取消：放弃本次替换",
        )
        if source_choice is None:
            return None
        if source_choice:
            path = filedialog.askopenfilename(
                title="选择要替换进去的图片",
                filetypes=IMAGE_FILE_TYPES,
            )
            return Path(path) if path else None

        picked = self._pick_saved_asset()
        if picked is None:
            return None

        self._last_replacement_item = next(
            (item for item in self.studio.list_saved_artwork() if item["path"] == str(picked)),
            None,
        )
        return picked

    def _replace_current_asset(self) -> None:
        if not self.current_selection:
            messagebox.showinfo("先选素材", "请先在左侧列表中选中一个系统素材。")
            return

        candidate = self._choose_replacement_candidate()
        if candidate is None:
            return

        try:
            prepared_candidate = self._prepare_replacement_candidate(self.current_selection, candidate)
        except StudioError as exc:
            messagebox.showerror("处理图片失败", str(exc))
            return

        if prepared_candidate is None:
            return

        target_format = self.current_selection.split("_", 1)[1].split(".", 1)[0]
        source_format = self._replacement_source_format() or _detect_saved_artwork_format(candidate)
        prepared_format = _detect_saved_artwork_format(prepared_candidate)
        show_direct_notice = prepared_candidate != candidate
        replacement_candidate = prepared_candidate
        pre_notes: list[str] = list(getattr(self, "_last_prepare_notes", []))

        try:
            predicted_name, _predicted_notes = self.studio.validate_replacement(self.current_selection, prepared_candidate)
        except StudioError as exc:
            messagebox.showerror("替换失败", str(exc))
            return

        selected_output = self._choose_low_color_output_format(
            target_format,
            prepared_format,
            prepared_candidate,
            show_direct_notice,
        )
        if selected_output is None and target_format in {"0004", "0008", "0064", "0065", "0565", "1888"}:
            return

        if selected_output is not None:
            selected_output_format, needs_preview = selected_output
            if selected_output_format == "1888":
                try:
                    new_name, notes = self.studio.replace_artwork_with_format(
                        self.current_selection,
                        replacement_candidate,
                        "1888",
                    )
                except StudioError as exc:
                    messagebox.showerror("替换失败", str(exc))
                    return
                pre_notes.append("已按你的选择保持 1888。")
            else:
                if needs_preview:
                    action, strategy = self._open_reduction_decision(
                        prepared_candidate,
                        selected_output_format,
                        allow_keep_original=False,
                    )
                    if action != "reduced":
                        return
                    reduction_candidate, reduction_notes = self._create_reduced_candidate(
                        self.current_selection,
                        prepared_candidate,
                        selected_output_format,
                        strategy,
                    )
                    replacement_candidate = reduction_candidate
                    pre_notes = list(getattr(self, "_last_prepare_notes", [])) + reduction_notes
                else:
                    replacement_candidate = prepared_candidate
                    pre_notes.append(f"当前图片已经满足 {selected_output_format}，已直接按该格式写回。")

                try:
                    new_name, notes = self.studio.replace_artwork_with_format(
                        self.current_selection,
                        replacement_candidate,
                        selected_output_format,
                    )
                except StudioError as exc:
                    messagebox.showerror("替换失败", str(exc))
                    return
        else:
            try:
                if target_format == "1888" and source_format in {"0004", "0008", "0064", "0065", "0565"}:
                    new_name, notes = self.studio.replace_artwork_with_format(
                        self.current_selection,
                        replacement_candidate,
                        source_format,
                    )
                    notes = [f"已按收藏素材的 {source_format} 格式写回当前素材。"] + notes
                else:
                    new_name, notes = self.studio.replace_artwork(self.current_selection, replacement_candidate)
            except StudioError as exc:
                messagebox.showerror("替换失败", str(exc))
                return

        all_notes = pre_notes + notes
        self._append_log("log", f"已替换 {self.current_selection} <- {candidate.name}")
        if prepared_candidate != candidate:
            self._append_log("log", "已先生成适配后的中间 PNG，并按目标素材规则完成替换。")
        self._append_replacement_notes_to_log(all_notes)
        self.notes_var.set("；".join(all_notes))
        self._refresh_assets()
        if self.asset_tree.exists(new_name):
            self.asset_tree.selection_set(new_name)
            self.asset_tree.focus(new_name)
            self.asset_tree.see(new_name)
            self.current_selection = new_name
            self._on_asset_selected()

    def _show_about(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("关于与版权")
        dialog.geometry("640x430")
        dialog.configure(bg=BG)
        dialog.transient(self.root)
        dialog.grab_set()

        container = tk.Frame(dialog, bg=CARD, padx=22, pady=22)
        container.pack(fill="both", expand=True, padx=16, pady=16)

        tk.Label(
            container,
            text="关于 iPod Theme Studio",
            bg=CARD,
            fg=TEXT,
            font=("Segoe UI Semibold", 18),
        ).pack(anchor="w")

        body_text = (
            "这个界面工具基于原项目 ipod_theme 的工作流程和相关代码封装而成，"
            "用于更方便地浏览、替换和重新打包 iPod nano 固件中的美术资源。\n\n"
            "原项目地址：\n"
            "https://github.com/nfzerox/ipod_theme\n\n"
            "原项目作者：nfzerox 以及 README 中列出的相关贡献者与上游项目作者。\n\n"
            "许可证：GPL-3.0\n"
            "原项目采用 GPL-3.0 协议发布，因此基于该项目修改和再分发时，也需要遵守 GPL-3.0 的相关要求。"
        )

        tk.Label(
            container,
            text=body_text,
            bg=CARD,
            fg=TEXT,
            justify="left",
            wraplength=580,
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(14, 18))

        button_row = tk.Frame(container, bg=CARD)
        button_row.pack(fill="x")

        tk.Button(
            button_row,
            text="打开原项目主页",
            command=lambda: webbrowser.open("https://github.com/nfzerox/ipod_theme"),
            bg=ACCENT,
            fg="white",
            activebackground="#173b37",
            activeforeground="white",
            relief="flat",
            padx=14,
            pady=8,
            font=("Segoe UI Semibold", 10),
        ).pack(side="left")

        tk.Button(
            button_row,
            text="关闭",
            command=dialog.destroy,
            bg="#ebe1cf",
            fg=TEXT,
            activebackground="#dfd0b5",
            activeforeground=TEXT,
            relief="flat",
            padx=14,
            pady=8,
            font=("Segoe UI Semibold", 10),
        ).pack(side="right")

    def _log_from_worker(self, message: str) -> None:
        self.log_queue.put(("log", message))

    def run(self) -> None:
        self.root.mainloop()


def _saved_asset_browser_delete_selected(self) -> None:
    paths = self._selected_paths()
    if not paths:
        messagebox.showinfo("先选素材", "请先在左侧列表里选中至少一张收藏素材。", parent=self.dialog)
        return

    sample_names = "\n".join(path.name for path in paths[:5])
    if len(paths) > 5:
        sample_names += f"\n... 共 {len(paths)} 张"

    choice = ActionChoiceDialog(
        self.dialog,
        "删除收藏",
        f"确定要删除这 {len(paths)} 张收藏素材吗？\n\n{sample_names}",
        [
            ("delete", "删除这些素材", True),
            ("cancel", "取消", False),
        ],
    ).show()
    if choice != "delete":
        return

    for path in paths:
        self.studio.delete_saved_artwork(path)
    self._load_items()


def _theme_studio_prepare_replacement_candidate(self, target_name: str, candidate: Path) -> Path | None:
    self._last_prepare_notes = []

    target_path = self.studio.body_dir() / target_name
    if not target_path.exists():
        raise StudioError(f"没有找到目标素材：{target_name}")

    with Image.open(target_path) as target_image:
        target_size = target_image.size

    with Image.open(candidate) as candidate_image:
        candidate_size = candidate_image.size

    if candidate_size != target_size:
        messagebox.showinfo(
            "需要先裁剪",
            (
                f"新图尺寸是 {candidate_size[0]}x{candidate_size[1]}，但目标素材需要 "
                f"{target_size[0]}x{target_size[1]}。\n\n"
                "接下来会打开内置裁剪窗口，你可以像手机相册那样拖动和缩放来取景。"
            ),
        )
        prepared = self._open_crop_dialog(candidate, target_name, target_size)
    else:
        choice = ActionChoiceDialog(
            self.root,
            "如何处理这张图？",
            "这张图片已经是目标尺寸，请直接选择下一步操作。",
            [
                ("crop", "继续裁剪/缩放", True),
                ("direct", "直接替换", False),
                ("cancel", "取消", False),
            ],
        ).show()
        if choice in {None, "cancel"}:
            return None
        prepared = self._open_crop_dialog(candidate, target_name, target_size) if choice == "crop" else self._stage_candidate_png(candidate, target_name)

    if prepared is None:
        return None

    preferred_format = self._replacement_source_format()
    if preferred_format and preferred_format != "1888":
        prepared, format_notes = self._prepare_candidate_for_saved_format(prepared, target_name, preferred_format)
        self._last_prepare_notes.extend(format_notes)

    return prepared


def _theme_studio_choose_replacement_candidate(self) -> Path | None:
    self._last_replacement_item = None
    self._last_prepare_notes = []

    choice = ActionChoiceDialog(
        self.root,
        "替换来源",
        "请选择这次替换要使用的图片来源。",
        [
            ("computer", "从电脑选图", True),
            ("saved", "从收藏素材中选", False),
            ("cancel", "取消", False),
        ],
    ).show()
    if choice in {None, "cancel"}:
        return None

    if choice == "computer":
        path = filedialog.askopenfilename(
            title="选择要替换进去的图片",
            filetypes=IMAGE_FILE_TYPES,
        )
        return Path(path) if path else None

    picked = self._pick_saved_asset()
    if picked is None:
        return None

    self._last_replacement_item = next(
        (item for item in self.studio.list_saved_artwork() if item["path"] == str(picked)),
        None,
    )
    return picked


def _theme_studio_import_multiple_saved_assets(self, sources: list[Path]) -> Path | None:
    choice = ActionChoiceDialog(
        self.root,
        "批量导入方式",
        f"已选择 {len(sources)} 张图片。\n\n请选择这次批量导入的处理方式。",
        [
            ("resize", "统一尺寸，逐张裁剪后导入", True),
            ("direct", "全部原图直接保存", False),
            ("cancel", "取消", False),
        ],
    ).show()
    if choice in {None, "cancel"}:
        return None

    target_size: tuple[int, int] | None = None
    if choice == "resize":
        target_size = self._ask_target_size()
        if target_size is None:
            return None

    imported_paths: list[Path] = []
    skipped_names: list[str] = []
    stopped_early = False

    for index, source in enumerate(sources, start=1):
        candidate = source

        if choice == "resize":
            cropped = self._open_crop_dialog(
                source,
                f"批量导入 {index}/{len(sources)} - {source.name}",
                target_size,
            )
            if cropped is None:
                next_step = ActionChoiceDialog(
                    self.root,
                    "当前图片未导入",
                    f"第 {index}/{len(sources)} 张没有完成裁剪：\n\n{source.name}\n\n接下来怎么处理？",
                    [
                        ("skip", "跳过这一张", True),
                        ("stop", "停止剩余导入", False),
                    ],
                ).show()
                if next_step == "skip":
                    skipped_names.append(source.name)
                    continue
                stopped_early = True
                break
            candidate = cropped

        try:
            saved_path = self.studio.import_saved_asset(candidate, note="", preferred_name=source.name)
        except StudioError as exc:
            next_step = ActionChoiceDialog(
                self.root,
                "导入失败",
                f"这张图片导入失败：\n\n{source.name}\n\n{exc}\n\n接下来怎么处理？",
                [
                    ("skip", "跳过这一张", True),
                    ("stop", "停止剩余导入", False),
                ],
            ).show()
            if next_step == "skip":
                skipped_names.append(source.name)
                continue
            stopped_early = True
            break

        imported_paths.append(saved_path)
        self._append_log("log", f"已导入收藏素材：{saved_path.name}")

    if imported_paths:
        summary_lines = [f"本次已导入 {len(imported_paths)} 张素材。"]
        if skipped_names:
            summary_lines.append(f"跳过 {len(skipped_names)} 张。")
        if stopped_early:
            summary_lines.append("已提前停止剩余导入。")
        messagebox.showinfo("批量导入完成", "\n".join(summary_lines), parent=self.root)
        return imported_paths[-1]

    if skipped_names or stopped_early:
        messagebox.showinfo("没有导入新素材", "这次批量导入没有新增素材。", parent=self.root)
    return None


def _theme_studio_import_file_to_saved_assets(self) -> Path | None:
    paths = filedialog.askopenfilenames(
        title="选择要导入到素材库的图片",
        filetypes=IMAGE_FILE_TYPES,
    )
    if not paths:
        return None

    sources = [Path(path) for path in paths]
    if len(sources) > 1:
        return _theme_studio_import_multiple_saved_assets(self, sources)

    source = sources[0]
    note = self._ask_saved_asset_note(source.stem)
    if note is None:
        return None

    choice = ActionChoiceDialog(
        self.root,
        "导入方式",
        "请选择导入到素材库时要不要先调整尺寸。",
        [
            ("resize", "先裁剪/缩放再保存", True),
            ("direct", "直接保存原图", False),
            ("cancel", "取消", False),
        ],
    ).show()
    if choice in {None, "cancel"}:
        return None

    candidate = source
    if choice == "resize":
        target_size = self._ask_target_size()
        if target_size is None:
            return None
        cropped = self._open_crop_dialog(source, source.name, target_size)
        if cropped is None:
            return None
        candidate = cropped

    try:
        saved_path = self.studio.import_saved_asset(candidate, note=note, preferred_name=source.name)
    except StudioError as exc:
        messagebox.showerror("导入失败", str(exc))
        return None

    self._append_log("log", f"已导入收藏素材：{saved_path.name}")
    return saved_path


def _theme_studio_reduce_saved_library_asset(self, asset_path: Path, item: dict[str, str]) -> Path | None:
    current_format = item.get("format", "")
    if current_format == "0004":
        messagebox.showinfo("当前素材不适用", "这张收藏素材已经是 0004 了，没有更低的格式可再转换。", parent=self.root)
        return None

    choice = ActionChoiceDialog(
        self.root,
        "目标转换格式",
        f"请选择要把这张 {current_format} 收藏素材转换到哪个更低的格式。",
        manual_format_conversion_actions_for(current_format),
    ).show()
    if choice in {None, "cancel"}:
        return None
    target_format = choice

    action, strategy = self._open_reduction_decision(asset_path, target_format, allow_keep_original=False)
    if action != "reduced":
        return None

    try:
        reduced_candidate, reduction_notes = self._create_reduced_candidate(
            asset_path.name,
            asset_path,
            target_format,
            strategy,
        )
        image_id = item.get("id", "")
        output_name = f"{image_id}_{target_format}.png" if image_id else asset_path.name
        updated_path = self.studio.replace_saved_artwork(asset_path, reduced_candidate, output_name=output_name)
    except StudioError as exc:
        messagebox.showerror("降色失败", str(exc), parent=self.root)
        return None

    self._append_log("log", f"已将收藏素材转换为 {target_format}：{updated_path.name}")
    self._append_replacement_notes_to_log(reduction_notes)
    return updated_path


def _theme_studio_reduce_color_and_replace(self) -> None:
    if not self.current_selection:
        messagebox.showinfo("先选素材", "请先在左侧列表中选中一个系统素材。")
        return

    current_format = self.current_selection.split("_", 1)[1].split(".", 1)[0]
    if current_format == "0004":
        messagebox.showinfo("当前素材不适用", "这个素材已经是 0004 了，没有更低的格式可再转换。")
        return

    choice = ActionChoiceDialog(
        self.root,
        "目标转换格式",
        f"请选择要把当前 {current_format} 素材转换到哪个更低的格式。",
        manual_format_conversion_actions_for(current_format),
    ).show()
    if choice in {None, "cancel"}:
        return
    target_format = choice

    source_path = self.studio.body_dir() / self.current_selection
    action, strategy = self._open_reduction_decision(source_path, target_format, allow_keep_original=False)
    if action != "reduced":
        return

    try:
        reduced_candidate, reduction_notes = self._create_reduced_candidate(
            self.current_selection,
            source_path,
            target_format,
            strategy,
        )
        new_name, notes = self.studio.replace_artwork_with_format(
            self.current_selection,
            reduced_candidate,
            target_format,
        )
    except StudioError as exc:
        messagebox.showerror("降色失败", str(exc))
        return

    all_notes = reduction_notes + notes
    self._append_log("log", f"已把当前 1888 素材手动转换到 {target_format}。")
    self._append_replacement_notes_to_log(all_notes)
    self.notes_var.set("；".join(all_notes))
    self._refresh_assets()
    if self.asset_tree.exists(new_name):
        self.asset_tree.selection_set(new_name)
        self.asset_tree.focus(new_name)
        self.asset_tree.see(new_name)
        self.current_selection = new_name
        self._on_asset_selected()


def _theme_studio_show_about(self) -> None:
    dialog = tk.Toplevel(self.root)
    dialog.title("关于与版权")
    dialog.geometry("700x500")
    dialog.configure(bg=BG)
    dialog.transient(self.root)
    dialog.grab_set()

    container = tk.Frame(dialog, bg=CARD, padx=22, pady=22)
    container.pack(fill="both", expand=True, padx=16, pady=16)

    tk.Label(
        container,
        text="关于 iPod Theme Studio",
        bg=CARD,
        fg=TEXT,
        font=ui_font(20, bold=True),
    ).pack(anchor="w")

    body_text = (
        "iPod Theme Studio 是基于上游项目 ipod_theme 构建的图形界面版本，"
        "用于更方便地浏览、替换和重新打包 iPod nano 固件中的美术资源。\n\n"
        "当前项目地址：\n"
        "https://github.com/wxhwxhwxh2002/ipod_theme_studio\n\n"
        "上游项目地址：\n"
        "https://github.com/nfzerox/ipod_theme\n\n"
        "上游作者：nfzerox，以及 README 中列出的相关贡献者与上游项目作者。\n\n"
        "许可证：GPL-3.0\n"
        "本项目基于 GPL-3.0 项目继续修改和分发，因此同样遵守 GPL-3.0。"
    )

    tk.Label(
        container,
        text=body_text,
        bg=CARD,
        fg=TEXT,
        justify="left",
        wraplength=640,
        font=ui_font(11),
    ).pack(anchor="w", pady=(14, 18))

    button_row = tk.Frame(container, bg=CARD)
    button_row.pack(fill="x")

    tk.Button(
        button_row,
        text="打开本项目主页",
        command=lambda: webbrowser.open("https://github.com/wxhwxhwxh2002/ipod_theme_studio"),
        **button_style("primary"),
        relief="flat",
        bd=0,
        padx=14,
        pady=10,
        font=ui_font(11, bold=True),
    ).pack(side="left")

    tk.Button(
        button_row,
        text="打开上游项目",
        command=lambda: webbrowser.open("https://github.com/nfzerox/ipod_theme"),
        **button_style("secondary"),
        relief="flat",
        bd=0,
        padx=14,
        pady=10,
        font=ui_font(11, bold=True),
    ).pack(side="left", padx=(10, 0))

    tk.Button(
        button_row,
        text="关闭",
        command=dialog.destroy,
        **button_style("secondary"),
        relief="flat",
        bd=0,
        padx=14,
        pady=10,
        font=ui_font(11, bold=True),
    ).pack(side="right")


SavedAssetBrowserDialog._delete_selected = _saved_asset_browser_delete_selected
ThemeStudioApp._prepare_replacement_candidate = _theme_studio_prepare_replacement_candidate
ThemeStudioApp._choose_replacement_candidate = _theme_studio_choose_replacement_candidate
ThemeStudioApp._import_file_to_saved_assets = _theme_studio_import_file_to_saved_assets
ThemeStudioApp._reduce_saved_library_asset = _theme_studio_reduce_saved_library_asset
ThemeStudioApp._reduce_color_and_replace = _theme_studio_reduce_color_and_replace
ThemeStudioApp._show_about = _theme_studio_show_about


def main() -> None:
    app = ThemeStudioApp()
    app.run()


if __name__ == "__main__":
    main()
