from __future__ import annotations

from pathlib import Path
import os
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser

from PIL import Image, ImageTk

from theme_studio_core import ThemeStudio, StudioError


BG = "#f4efe6"
CARD = "#fffaf0"
ACCENT = "#204f4a"
ACCENT_LIGHT = "#d9ebe4"
TEXT = "#1d2b28"
MUTED = "#61706d"
PREVIEW_CANVAS_WIDTH = 360
PREVIEW_CANVAS_HEIGHT = 320


class ThemeStudioApp:
    def __init__(self) -> None:
        self.studio = ThemeStudio()
        self.root = tk.Tk()
        self.root.title("iPod Theme Studio")
        self.root.geometry("1380x860")
        self.root.minsize(1180, 760)
        self.root.configure(bg=BG)

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
        self.capacity_var = tk.StringVar(value="容量提醒：导入固件后会在这里提示 1888 素材和打包体积变化。")
        self.group_map: dict[str, str] = {}

        self._configure_style()
        self._build_layout()
        self._load_existing_session()
        self._refresh_assets()
        self.root.after(120, self._drain_log_queue)

    def _configure_style(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Segoe UI Semibold", 20))
        style.configure("Body.TLabel", background=CARD, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=("Segoe UI", 10))
        style.configure("Header.TLabel", background=CARD, foreground=TEXT, font=("Segoe UI Semibold", 12))
        style.configure("Accent.TButton", font=("Segoe UI Semibold", 10))
        style.configure("Treeview", font=("Consolas", 10), rowheight=26)
        style.configure("Treeview.Heading", font=("Segoe UI Semibold", 10))

    def _build_layout(self) -> None:
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)

        top = ttk.Frame(outer)
        top.pack(fill="x")

        title = ttk.Label(top, text="iPod Theme Studio", style="Title.TLabel")
        title.pack(side="left")

        subtitle = tk.Label(
            top,
            text="把官方或社区 IPSW 变成适合小白使用的素材替换工作台",
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 11),
        )
        subtitle.pack(side="left", padx=(14, 0), pady=(8, 0))

        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True, pady=(16, 0))
        body.columnconfigure(0, weight=0)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        self._build_sidebar(body)
        self._build_workspace(body)

    def _build_sidebar(self, parent: ttk.Frame) -> None:
        sidebar = ttk.Frame(parent, style="Card.TFrame", padding=16)
        sidebar.grid(row=0, column=0, sticky="nsw")

        ttk.Label(sidebar, text="工作流", style="Header.TLabel").pack(anchor="w")

        ttk.Label(sidebar, text="设备型号", style="Body.TLabel").pack(anchor="w", pady=(14, 4))
        device_box = ttk.Combobox(
            sidebar,
            textvariable=self.device_var,
            state="readonly",
            values=["nano6", "nano7-2012", "nano7-2015"],
            width=22,
        )
        device_box.pack(anchor="w", fill="x")
        device_box.bind("<<ComboboxSelected>>", self._on_device_changed)

        tips = tk.Label(
            sidebar,
            text="推荐先做官方固件流程，再决定是否导入社区 IPSW。",
            bg=CARD,
            fg=MUTED,
            justify="left",
            wraplength=250,
            font=("Segoe UI", 10),
        )
        tips.pack(anchor="w", pady=(12, 18))

        self._add_sidebar_button(sidebar, "下载官方 IPSW 备份", self._download_official_backup)
        self._add_sidebar_button(sidebar, "加载官方固件并解包", self._import_official)
        self._add_sidebar_button(sidebar, "导入社区 IPSW", self._import_community_ipsw)
        self._add_sidebar_button(sidebar, "打开 body 目录", self._open_body_dir)
        self._add_sidebar_button(sidebar, "重新扫描素材列表", self._refresh_assets)
        self._add_sidebar_button(sidebar, "生成修改后的 IPSW", self._build_ipsw)
        self._add_sidebar_button(sidebar, "关于与版权", self._show_about)

        status_card = tk.Frame(sidebar, bg=ACCENT, padx=14, pady=14)
        status_card.pack(fill="x", pady=(18, 0))

        tk.Label(
            status_card,
            text="当前状态",
            bg=ACCENT,
            fg="#d9f7ef",
            font=("Segoe UI Semibold", 12),
        ).pack(anchor="w")
        tk.Label(
            status_card,
            textvariable=self.status_var,
            bg=ACCENT,
            fg="white",
            justify="left",
            wraplength=240,
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(8, 0))

        capacity_card = tk.Frame(sidebar, bg="#efe6d5", padx=12, pady=12)
        capacity_card.pack(fill="x", pady=(12, 0))

        tk.Label(
            capacity_card,
            text="容量提醒",
            bg="#efe6d5",
            fg=TEXT,
            font=("Segoe UI Semibold", 11),
        ).pack(anchor="w")
        tk.Label(
            capacity_card,
            textvariable=self.capacity_var,
            bg="#efe6d5",
            fg=TEXT,
            justify="left",
            wraplength=244,
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(8, 0))

    def _build_workspace(self, parent: ttk.Frame) -> None:
        workspace = ttk.Frame(parent)
        workspace.grid(row=0, column=1, sticky="nsew", padx=(16, 0))
        workspace.rowconfigure(1, weight=1)
        workspace.rowconfigure(2, weight=0)
        workspace.columnconfigure(0, weight=1)
        workspace.columnconfigure(1, weight=0)

        header = ttk.Frame(workspace, style="Card.TFrame", padding=16)
        header.grid(row=0, column=0, columnspan=2, sticky="ew")

        tk.Label(
            header,
            textvariable=self.source_var,
            bg=CARD,
            fg=TEXT,
            font=("Segoe UI Semibold", 12),
        ).pack(anchor="w")
        tk.Label(
            header,
            text="左侧选择要替换的系统素材，右侧可以预览并替换。打包时会自动按原文件名和格式写回。",
            bg=CARD,
            fg=MUTED,
            justify="left",
            wraplength=950,
            font=("Segoe UI", 10),
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
            font=("Segoe UI Semibold", 10),
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
        self.asset_tree.column("id", width=110, anchor="w")
        self.asset_tree.column("format", width=90, anchor="center")
        self.asset_tree.column("size", width=110, anchor="center")
        self.asset_tree.column("group", width=170, anchor="w")
        self.asset_tree.column("name", width=250, anchor="w")
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
            bg=ACCENT_LIGHT,
            highlightthickness=0,
            relief="flat",
        )
        self.preview_canvas.grid(row=1, column=0, sticky="nsew", pady=(10, 10))
        self.preview_placeholder_id = self.preview_canvas.create_text(
            PREVIEW_CANVAS_WIDTH // 2,
            PREVIEW_CANVAS_HEIGHT // 2,
            text="暂无预览",
            fill=MUTED,
            font=("Segoe UI", 11),
        )

        self.meta_label = tk.Label(
            self.preview_panel,
            bg=CARD,
            fg=TEXT,
            justify="left",
            anchor="w",
            wraplength=330,
            font=("Consolas", 10),
        )
        self.meta_label.grid(row=2, column=0, sticky="ew")

        tk.Label(
            self.preview_panel,
            textvariable=self.notes_var,
            bg=CARD,
            fg=MUTED,
            justify="left",
            wraplength=330,
            font=("Segoe UI", 10),
        ).grid(row=3, column=0, sticky="ew", pady=(10, 12))

        replace_btn = tk.Button(
            self.preview_panel,
            text="替换当前素材",
            command=self._replace_current_asset,
            bg=ACCENT,
            fg="white",
            activebackground="#173b37",
            activeforeground="white",
            relief="flat",
            padx=16,
            pady=10,
            font=("Segoe UI Semibold", 10),
        )
        replace_btn.grid(row=4, column=0, sticky="ew")

        log_card = ttk.Frame(workspace, style="Card.TFrame", padding=14)
        log_card.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        log_card.columnconfigure(0, weight=1)

        ttk.Label(log_card, text="日志", style="Header.TLabel").grid(row=0, column=0, sticky="w")
        self.log_text = tk.Text(
            log_card,
            height=10,
            bg="#112724",
            fg="#dcf5ef",
            insertbackground="#dcf5ef",
            relief="flat",
            font=("Consolas", 10),
            padx=10,
            pady=10,
        )
        self.log_text.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self.log_text.configure(state="disabled")

    def _add_sidebar_button(self, parent: ttk.Frame, text: str, command) -> None:
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg="#ebe1cf",
            fg=TEXT,
            activebackground="#dfd0b5",
            activeforeground=TEXT,
            relief="flat",
            anchor="w",
            padx=12,
            pady=10,
            font=("Segoe UI Semibold", 10),
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
        self.capacity_var.set(self.studio.capacity_summary())

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
            self.studio.download_official_backup(device_key, self._log_from_worker)
            self.log_queue.put(("status", "官方 IPSW 备份已下载。"))

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

        try:
            os.startfile(str(body_dir))  # type: ignore[attr-defined]
        except AttributeError:
            subprocess.run(["explorer", str(body_dir)], check=False)

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


def main() -> None:
    app = ThemeStudioApp()
    app.run()


if __name__ == "__main__":
    main()
