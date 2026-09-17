from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import sys
import tempfile
import threading
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageFilter, ImageOps, ImageTk


APP_TITLE = "Labelyou"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
CATEGORY_SPECS = (
    ("normal", "正常", "1_正常_数据集", "#2E7D32"),
    ("crack", "裂纹", "1_crack_数据集", "#C62828"),
    ("residue", "残料", "1_residue_数据集", "#EF6C00"),
    ("undemolded", "未脱模", "1_undemolded_数据集", "#1565C0"),
)
CATEGORY_THEMES = {
    "normal": ("#252525", "#303030", "#D8D8D8", "#4A4A4A"),
    "crack": ("#252525", "#303030", "#D8D8D8", "#4A4A4A"),
    "residue": ("#252525", "#303030", "#D8D8D8", "#4A4A4A"),
    "undemolded": ("#252525", "#303030", "#D8D8D8", "#4A4A4A"),
}
UI_BG = "#1E1E1E"
UI_SURFACE = "#232323"
UI_SURFACE_ALT = "#292929"
UI_BORDER = "#3D3D3D"
UI_TEXT = "#D8D8D8"
UI_MUTED = "#9A9A9A"
UI_ACCENT = "#2495D0"
UI_CANVAS = "#202020"
UI_MENUBAR = "#121416"
UI_FONT = "Microsoft YaHei UI"


def enable_windows_high_dpi() -> None:
    """让 Windows 直接按显示器 DPI 绘制，避免系统把整个窗口位图放大后发虚。"""
    if sys.platform != "win32":
        return

    # PER_MONITOR_AWARE_V2 在多显示器和缩放比例变化时效果最好。
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass

    try:
        # Windows 8.1 及更高版本的兼容回退。
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) in (0, -2147024891):
            return
    except (AttributeError, OSError):
        pass

    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def configure_tk_rendering(root: tk.Tk) -> float:
    """按当前显示器 DPI 配置 Tk 字体，使文字由 ClearType 以原生分辨率绘制。"""
    dpi = 96
    if sys.platform == "win32":
        try:
            root.update_idletasks()
            dpi = int(ctypes.windll.user32.GetDpiForWindow(root.winfo_id())) or 96
        except (AttributeError, OSError, ValueError):
            dpi = 96

    scale = max(1.0, dpi / 96.0)
    try:
        root.tk.call("tk", "scaling", dpi / 72.0)
    except tk.TclError:
        pass

    # ttk 与原生 Tk 控件共享这些命名字体，统一后不会出现局部模糊或字号跳变。
    named_fonts = {
        "TkDefaultFont": 9,
        "TkTextFont": 9,
        "TkMenuFont": 9,
        "TkHeadingFont": 9,
        "TkCaptionFont": 9,
        "TkSmallCaptionFont": 8,
        "TkIconFont": 9,
        "TkTooltipFont": 8,
    }
    for name, size in named_fonts.items():
        try:
            tkfont.nametofont(name).configure(family=UI_FONT, size=size)
        except tk.TclError:
            pass
    root.option_add("*Font", (UI_FONT, 9))
    return scale


@dataclass
class Category:
    key: str
    title: str
    folder_name: str
    color: str
    path: Path


def atomic_write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def unique_destination(folder: Path, filename: str) -> Path:
    """返回不覆盖已有文件的目标路径。"""
    candidate = folder / filename
    if not candidate.exists():
        return candidate
    source = Path(filename)
    index = 2
    while True:
        candidate = folder / f"{source.stem}__{index}{source.suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def normalized(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path))).casefold()


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


def scan_images(source: Path, excluded_roots: list[Path], completed: set[str]) -> list[Path]:
    images: list[Path] = []
    if not source.is_dir():
        return images

    # os.walk 配合目录剪枝比逐文件 resolve 快很多，9000 张图片也能快速完成。
    excluded = {normalized(path) for path in excluded_roots}
    for directory, dirnames, filenames in os.walk(source):
        directory_path = Path(directory)
        dirnames[:] = [
            name
            for name in dirnames
            if normalized(directory_path / name) not in excluded
        ]
        for filename in filenames:
            path = directory_path / filename
            if path.suffix.lower() in IMAGE_EXTENSIONS and normalized(path) not in completed:
                images.append(path)
    return sorted(images, key=lambda p: str(p).casefold())


class ImageClassifierApp:
    def __init__(self, root: tk.Tk, data_root: Path | None = None) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1480x900")
        self.root.minsize(1120, 700)
        self.root.configure(bg=UI_BG)
        self.root.after(0, self._enable_dark_titlebar)

        self.app_dir = Path(__file__).resolve().parent
        self.settings_path = self.app_dir / "settings.json"
        self.progress_path = self.app_dir / "progress.json"
        self.log_dir = self.app_dir / "logs"
        self.settings = self._load_json(self.settings_path, {})
        default_data_root = self.app_dir.parent / "数据集"
        self.data_root = Path(data_root or self.settings.get("data_root", default_data_root))
        self.source = Path(self.settings.get("source", self.data_root / "手模数据集"))
        saved_targets = self.settings.get("targets", {})
        saved_titles = self.settings.get("category_titles", {})
        self.categories = [
            Category(
                key,
                str(saved_titles.get(key, title)).strip() or title,
                folder,
                color,
                Path(saved_targets.get(key, self.data_root / folder)),
            )
            for key, title, folder, color in CATEGORY_SPECS
        ]

        self.images: list[Path] = []
        self.index = 0
        self.history: list[dict[str, str]] = []
        self.progress: dict[str, dict[str, str]] = self._load_json(self.progress_path, {})
        self.scan_version = 0
        self.scan_in_progress = False
        self.mode_var = tk.StringVar(value=self.settings.get("mode", "move"))
        self.status_var = tk.StringVar(value="正在准备……")
        self.file_var = tk.StringVar(value="")
        self.detail_var = tk.StringVar(value="")
        self.count_var = tk.StringVar(value="0 / 0")
        self.search_var = tk.StringVar(value="")
        self.search_result_var = tk.StringVar(value="0 张")
        self.classified_total_var = tk.StringVar(value="0")
        self.skipped_total_var = tk.StringVar(value="0")
        self.remaining_total_var = tk.StringVar(value="0")
        self.dataset_total_var = tk.StringVar(value="0")
        self.category_count_vars = {category.key: tk.StringVar(value="0") for category in self.categories}
        self.visible_image_indices: list[int] = []

        self.original_image: Image.Image | None = None
        self.photo: ImageTk.PhotoImage | None = None
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.drag_anchor: tuple[int, int] | None = None

        self._build_style()
        self._build_menu()
        self._build_ui()
        self._bind_keys()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(80, self.reload_images)

    @staticmethod
    def _load_json(path: Path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return default

    def _enable_dark_titlebar(self) -> None:
        """在 Windows 10/11 上尽量启用与 Labelme 接近的深色标题栏。"""
        if sys.platform != "win32":
            return
        try:
            self.root.update_idletasks()
            get_parent = ctypes.windll.user32.GetParent
            get_parent.restype = ctypes.c_void_p
            hwnd = get_parent(self.root.winfo_id()) or self.root.winfo_id()
            enabled = ctypes.c_int(1)
            for attribute in (20, 19):
                result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd,
                    attribute,
                    ctypes.byref(enabled),
                    ctypes.sizeof(enabled),
                )
                if result == 0:
                    break
        except (AttributeError, OSError):
            pass

    def _build_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=UI_BG)
        style.configure(
            "TLabel",
            background=UI_BG,
            foreground=UI_TEXT,
            font=(UI_FONT, 9),
        )
        style.configure("Toolbar.TFrame", background=UI_SURFACE)
        style.configure("Folderbar.TFrame", background=UI_SURFACE_ALT)
        style.configure("Side.TFrame", background=UI_SURFACE)
        style.configure("Status.TFrame", background=UI_SURFACE)
        style.configure("TPanedwindow", background=UI_BG)
        style.configure("Brand.TLabel", background=UI_SURFACE, foreground="#F2F2F2", font=(UI_FONT, 13, "bold"))
        style.configure("Side.TLabel", background=UI_SURFACE, foreground=UI_TEXT, font=(UI_FONT, 9))
        style.configure("Section.TLabel", background=UI_SURFACE, foreground="#E8E8E8", font=(UI_FONT, 10, "bold"))
        style.configure("Filename.TLabel", background=UI_SURFACE, foreground=UI_TEXT, font=(UI_FONT, 9))
        style.configure("Info.TLabel", foreground=UI_MUTED, font=(UI_FONT, 8))
        style.configure("SideInfo.TLabel", background=UI_SURFACE, foreground="#A8A8A8", font=(UI_FONT, 8))
        style.configure("Folderbar.TLabel", background=UI_SURFACE_ALT, foreground="#A8A8A8", font=(UI_FONT, 8))
        style.configure("Count.TLabel", background=UI_SURFACE, foreground=UI_TEXT, font=(UI_FONT, 10, "bold"))
        style.configure("Card.TLabel", background=UI_SURFACE_ALT, foreground="#E3E3E3", font=(UI_FONT, 9))
        style.configure("CardInfo.TLabel", background=UI_SURFACE_ALT, foreground="#A8A8A8", font=(UI_FONT, 8))
        style.configure("MetricValue.TLabel", background=UI_SURFACE_ALT, foreground="#F0F0F0", font=(UI_FONT, 14, "bold"))
        style.configure("MetricName.TLabel", background=UI_SURFACE_ALT, foreground="#A8A8A8", font=(UI_FONT, 8))
        style.configure(
            "TButton",
            background=UI_SURFACE,
            foreground=UI_TEXT,
            bordercolor=UI_BORDER,
            lightcolor=UI_SURFACE,
            darkcolor=UI_SURFACE,
            font=(UI_FONT, 9),
            padding=(8, 5),
        )
        style.map(
            "TButton",
            background=[("active", "#343434"), ("pressed", "#3B3B3B")],
            foreground=[("disabled", "#777777")],
        )
        style.configure("Toolbar.TButton", padding=(9, 4))
        style.configure(
            "Folder.TButton",
            background="#2B2B2B",
            foreground="#D2D2D2",
            bordercolor="#484848",
            lightcolor="#2B2B2B",
            darkcolor="#2B2B2B",
            font=(UI_FONT, 8),
            padding=(8, 3),
        )
        style.map("Folder.TButton", background=[("active", "#353535"), ("pressed", "#3D3D3D")])
        style.configure("Nav.TButton", padding=(7, 7))
        style.configure(
            "Search.TEntry",
            fieldbackground="#1F1F1F",
            foreground="#E6E6E6",
            bordercolor="#505050",
            lightcolor="#1F1F1F",
            darkcolor="#1F1F1F",
            insertcolor="#FFFFFF",
            padding=(7, 4),
            font=(UI_FONT, 8),
        )
        style.map(
            "Search.TEntry",
            bordercolor=[("focus", UI_ACCENT)],
            fieldbackground=[("focus", "#222222")],
        )
        style.configure("SearchClear.TButton", padding=(5, 3), font=(UI_FONT, 8))
        style.configure(
            "Danger.TButton",
            background="#382626",
            foreground="#E6BABA",
            bordercolor="#704242",
            lightcolor="#382626",
            darkcolor="#382626",
            padding=(7, 7),
        )
        style.map("Danger.TButton", background=[("active", "#543030"), ("pressed", "#672F2F")])
        style.configure(
            "TCombobox",
            padding=4,
            fieldbackground="#303030",
            background="#303030",
            foreground=UI_TEXT,
            arrowcolor=UI_TEXT,
            bordercolor=UI_BORDER,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", "#303030")],
            foreground=[("readonly", UI_TEXT)],
            selectbackground=[("readonly", "#3A3A3A")],
            selectforeground=[("readonly", "#FFFFFF")],
        )
        style.configure(
            "Vertical.TScrollbar",
            background="#343434",
            troughcolor="#242424",
            bordercolor="#242424",
            arrowcolor="#BDBDBD",
        )
        style.configure(
            "Horizontal.TProgressbar",
            troughcolor="#353535",
            background=UI_ACCENT,
            bordercolor="#353535",
            lightcolor=UI_ACCENT,
            darkcolor=UI_ACCENT,
            thickness=5,
        )

    def _new_menu(self, parent: tk.Misc) -> tk.Menu:
        return tk.Menu(
            parent,
            tearoff=False,
            bg=UI_SURFACE_ALT,
            fg=UI_TEXT,
            activebackground="#3A3A3A",
            activeforeground="#FFFFFF",
            disabledforeground="#777777",
            borderwidth=0,
            relief="flat",
            font=(UI_FONT, 9),
        )

    def _build_menu(self) -> None:
        self.menu_bar = tk.Frame(
            self.root,
            bg=UI_MENUBAR,
            height=31,
            borderwidth=0,
        )
        self.menu_bar.pack(fill="x")

        self.menu_buttons: list[tuple[tk.Menubutton, tk.Menu]] = []

        def add_menu_button(label: str) -> tk.Menu:
            button = tk.Menubutton(
                self.menu_bar,
                text=label,
                bg=UI_MENUBAR,
                fg="#E8E8E8",
                activebackground="#2B2E31",
                activeforeground="#FFFFFF",
                borderwidth=0,
                relief="flat",
                highlightthickness=0,
                indicatoron=False,
                padx=11,
                pady=5,
                font=(UI_FONT, 9),
                cursor="hand2",
            )
            # Windows 下 Menu 必须以对应 Menubutton 为父控件，点击才能稳定弹出。
            menu = self._new_menu(button)
            button.configure(menu=menu)
            button.pack(side="left")
            self.menu_buttons.append((button, menu))
            return menu

        file_menu = add_menu_button("文件")
        file_menu.add_command(label="打开图片目录", accelerator="Ctrl+O", command=self.choose_source)
        file_menu.add_command(label="重新扫描", accelerator="F5", command=self.reload_images)
        file_menu.add_command(label="打开当前图片所在目录", command=self.open_current_folder)
        file_menu.add_separator()
        file_menu.add_command(label="退出", accelerator="Ctrl+Q", command=self.close)

        edit_menu = add_menu_button("编辑")
        edit_menu.add_command(label="撤销", accelerator="Ctrl+Z", command=self.undo)
        edit_menu.add_command(label="搜索文件名", accelerator="Ctrl+F", command=self.focus_search)
        edit_menu.add_command(label="清空搜索", accelerator="Esc", command=self.clear_search)
        edit_menu.add_separator()
        edit_menu.add_command(label="删除当前图片", accelerator="Delete", command=self.delete_current_image)

        self.category_menu = add_menu_button("分类")
        for number, category in enumerate(self.categories, start=1):
            self.category_menu.add_command(
                label=f"分类为“{category.title}”",
                accelerator=str(number),
                command=lambda c=category: self.classify(c),
            )
            category.menu_index = number - 1  # type: ignore[attr-defined]
        self.category_menu.add_separator()
        self.category_menu.add_command(label="标记为不分类", accelerator="Space", command=self.skip_image)

        settings_menu = add_menu_button("设置")
        settings_menu.add_command(label="设置目标文件夹…", command=self.open_target_settings)
        settings_menu.add_command(label="编辑分类名称…", command=self.open_category_name_settings)
        settings_menu.add_separator()
        mode_menu = self._new_menu(settings_menu)
        mode_menu.add_radiobutton(label="移动图片", value="move", variable=self.mode_var, command=self.save_settings)
        mode_menu.add_radiobutton(label="复制图片", value="copy", variable=self.mode_var, command=self.save_settings)
        settings_menu.add_cascade(label="处理模式", menu=mode_menu)
        tk.Frame(self.root, bg="#2F3336", height=1, borderwidth=0).pack(fill="x")

    def _build_ui(self) -> None:
        # 常用操作和四个目标目录放在同一行，减少顶部占用空间。
        top = ttk.Frame(self.root, style="Toolbar.TFrame", padding=(10, 5))
        top.pack(fill="x")
        ttk.Button(top, text="打开目录", style="Toolbar.TButton", command=self.choose_source).grid(row=0, column=0, padx=(0, 4))
        ttk.Button(top, text="重新扫描", style="Toolbar.TButton", command=self.reload_images).grid(row=0, column=1, padx=4)
        ttk.Label(top, text="处理模式", style="Side.TLabel").grid(row=0, column=2, padx=(13, 6))
        mode = ttk.Combobox(top, textvariable=self.mode_var, values=("move", "copy"), state="readonly", width=6)
        mode.grid(row=0, column=3)
        mode.bind("<<ComboboxSelected>>", lambda _event: self.save_settings())
        tk.Frame(top, bg=UI_BORDER, width=1, height=24).grid(row=0, column=4, padx=12, sticky="ns")
        ttk.Label(top, text="目标文件夹", style="Side.TLabel").grid(row=0, column=5, padx=(0, 6))
        for column, category in enumerate(self.categories, start=6):
            button = ttk.Button(
                top,
                text=f"{category.title}  ·  {category.path.name}",
                style="Folder.TButton",
                command=lambda c=category: self.choose_category_folder(c),
            )
            button.grid(row=0, column=column, padx=3)
            category.folder_button = button  # type: ignore[attr-defined]
        spacer_column = 6 + len(self.categories)
        top.columnconfigure(spacer_column, weight=1)
        ttk.Button(top, text="撤销", style="Toolbar.TButton", command=self.undo).grid(row=0, column=spacer_column + 1, padx=(8, 4), sticky="e")
        ttk.Button(top, text="全部设置", style="Folder.TButton", command=self.open_target_settings).grid(row=0, column=spacer_column + 2, padx=(4, 0), sticky="e")

        tk.Frame(self.root, bg=UI_BORDER, height=1).pack(fill="x")

        main = ttk.Panedwindow(self.root, orient="horizontal")
        main.pack(fill="both", expand=True, padx=6, pady=6)
        preview_frame = tk.Frame(main, bg=UI_CANVAS, highlightbackground=UI_BORDER, highlightthickness=1)
        side_shell = tk.Frame(main, bg=UI_SURFACE, width=370, highlightbackground=UI_BORDER, highlightthickness=1)
        side = ttk.Frame(side_shell, style="Side.TFrame", padding=(12, 11))
        side.pack(fill="both", expand=True)
        side.columnconfigure(0, weight=1)
        side.rowconfigure(6, weight=1)
        main.add(preview_frame, weight=5)
        main.add(side_shell, weight=1)

        self.canvas = tk.Canvas(preview_frame, bg=UI_CANVAS, highlightthickness=0, cursor="fleur")
        self.canvas.pack(fill="both", expand=True, padx=1, pady=1)
        self.canvas.bind("<Configure>", lambda _event: self.render_image())
        self.canvas.bind("<MouseWheel>", self.on_mouse_wheel)
        self.canvas.bind("<ButtonPress-1>", self.on_drag_start)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<Double-Button-1>", lambda _event: self.reset_view())

        current_card = ttk.Frame(side, style="Folderbar.TFrame", padding=(11, 8))
        current_card.grid(row=0, column=0, sticky="ew")
        current_header = ttk.Frame(current_card, style="Folderbar.TFrame")
        current_header.pack(fill="x")
        ttk.Label(current_header, text="当前图片", style="Card.TLabel", font=(UI_FONT, 10, "bold")).pack(side="left")
        ttk.Label(current_header, textvariable=self.count_var, style="CardInfo.TLabel").pack(side="right")
        ttk.Label(current_card, textvariable=self.file_var, style="Card.TLabel", wraplength=320).pack(anchor="w", pady=(5, 2))
        ttk.Label(current_card, textvariable=self.detail_var, style="CardInfo.TLabel").pack(anchor="w")
        self.progress_bar = ttk.Progressbar(current_card, orient="horizontal", mode="determinate", maximum=100)
        self.progress_bar.pack(fill="x", pady=(7, 0))

        stats_card = tk.Frame(side, bg=UI_SURFACE_ALT, highlightbackground=UI_BORDER, highlightthickness=1)
        stats_card.grid(row=1, column=0, sticky="ew", pady=(7, 9))
        metrics = (
            ("已分类", self.classified_total_var),
            ("不分类", self.skipped_total_var),
            ("待分类", self.remaining_total_var),
            ("总计", self.dataset_total_var),
        )
        for metric_index, (title, variable) in enumerate(metrics):
            metric = ttk.Frame(stats_card, style="Folderbar.TFrame", padding=(6, 4))
            metric.pack(side="left", fill="x", expand=True)
            ttk.Label(metric, textvariable=variable, style="MetricValue.TLabel").pack()
            ttk.Label(metric, text=title, style="MetricName.TLabel").pack()
            if metric_index < len(metrics) - 1:
                tk.Frame(stats_card, bg=UI_BORDER, width=1).pack(side="left", fill="y", pady=7)

        heading = ttk.Frame(side, style="Side.TFrame")
        heading.grid(row=2, column=0, sticky="ew", pady=(0, 5))
        ttk.Label(heading, text="分类", style="Section.TLabel").pack(side="left")
        ttk.Button(
            heading,
            text="编辑名称",
            style="SearchClear.TButton",
            command=self.open_category_name_settings,
        ).pack(side="right")

        category_panel = ttk.Frame(side, style="Side.TFrame")
        category_panel.grid(row=3, column=0, sticky="ew")
        category_panel.columnconfigure(0, weight=1)
        for number, category in enumerate(self.categories, start=1):
            soft, hover, foreground, accent = CATEGORY_THEMES[category.key]
            card = tk.Frame(category_panel, bg=soft, highlightbackground=accent, highlightthickness=1, cursor="hand2")
            card.grid(row=number - 1, column=0, sticky="ew", pady=2)
            card.columnconfigure(0, weight=1)
            title_label = tk.Label(card, text=category.title, anchor="w", bg=soft, fg=foreground, font=(UI_FONT, 9, "bold"), cursor="hand2")
            count_label = tk.Label(card, text="0 张", width=8, anchor="e", bg=soft, fg=foreground, font=(UI_FONT, 9), cursor="hand2")
            title_label.grid(row=0, column=0, sticky="w", padx=(12, 4), pady=7)
            count_label.grid(row=0, column=1, padx=(4, 10), pady=7)
            widgets = (card, title_label, count_label)

            def classify_card(_event=None, c=category):
                self.classify(c)

            def paint_card(color, items=widgets):
                for item in items:
                    item.configure(bg=color)

            for widget in widgets:
                widget.bind("<Button-1>", classify_card)
                widget.bind("<Enter>", lambda _event, color=hover, items=widgets: paint_card(color, items))
                widget.bind("<Leave>", lambda _event, color=soft, items=widgets: paint_card(color, items))
            category.count_label = count_label  # type: ignore[attr-defined]
            category.title_label = title_label  # type: ignore[attr-defined]

        nav = ttk.Frame(side, style="Side.TFrame")
        nav.grid(row=4, column=0, sticky="ew", pady=(7, 7))
        ttk.Button(nav, text="上一张", style="Nav.TButton", command=self.previous_image).pack(side="left", expand=True, fill="x", padx=(0, 3))
        ttk.Button(nav, text="跳过", style="Nav.TButton", command=self.skip_image).pack(side="left", expand=True, fill="x", padx=3)
        ttk.Button(nav, text="下一张", style="Nav.TButton", command=self.next_image).pack(side="left", expand=True, fill="x", padx=3)
        ttk.Button(nav, text="删除", style="Danger.TButton", command=self.delete_current_image).pack(side="left", expand=True, fill="x", padx=(3, 0))

        file_heading = ttk.Frame(side, style="Side.TFrame")
        file_heading.grid(row=5, column=0, sticky="ew", pady=(0, 5))
        file_heading.columnconfigure(2, weight=1)
        ttk.Label(file_heading, text="待分类文件", style="Section.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Label(file_heading, text="搜索", style="SideInfo.TLabel").grid(row=0, column=1, sticky="w", padx=(0, 5))
        self.search_entry = ttk.Entry(
            file_heading,
            textvariable=self.search_var,
            style="Search.TEntry",
        )
        self.search_entry.grid(row=0, column=2, sticky="ew")
        self.search_entry.bind("<Return>", self.jump_to_first_search_result)
        self.search_entry.bind("<Escape>", self.clear_search)
        ttk.Button(
            file_heading,
            text="清空",
            width=4,
            style="SearchClear.TButton",
            command=self.clear_search,
        ).grid(row=0, column=3, padx=(5, 6))
        ttk.Label(file_heading, textvariable=self.search_result_var, style="SideInfo.TLabel").grid(row=0, column=4, sticky="e")
        self.search_var.trace_add("write", self.on_search_changed)
        list_frame = tk.Frame(side, bg="#202020", highlightbackground=UI_BORDER, highlightthickness=1)
        list_frame.grid(row=6, column=0, sticky="nsew")
        self.file_list = tk.Listbox(
            list_frame,
            bg="#202020",
            fg="#C8C8C8",
            selectbackground="#365A72",
            selectforeground="#FFFFFF",
            borderwidth=0,
            highlightthickness=0,
            activestyle="none",
            font=(UI_FONT, 8),
            selectborderwidth=0,
        )
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.file_list.yview)
        self.file_list.configure(yscrollcommand=scrollbar.set)
        self.file_list.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.file_list.bind("<<ListboxSelect>>", self.on_list_select)

        tk.Frame(self.root, bg=UI_BORDER, height=1).pack(fill="x")
        status = ttk.Frame(self.root, style="Status.TFrame", padding=(13, 7))
        status.pack(fill="x")
        ttk.Label(status, textvariable=self.status_var, style="SideInfo.TLabel").pack(side="left")

    def _bind_keys(self) -> None:
        self.root.bind("<KeyPress-1>", lambda e: self._shortcut_classify(e, 0))
        self.root.bind("<KeyPress-2>", lambda e: self._shortcut_classify(e, 1))
        self.root.bind("<KeyPress-3>", lambda e: self._shortcut_classify(e, 2))
        self.root.bind("<KeyPress-4>", lambda e: self._shortcut_classify(e, 3))
        self.root.bind("<KeyPress-a>", lambda e: self._shortcut_navigation(e, -1))
        self.root.bind("<KeyPress-A>", lambda e: self._shortcut_navigation(e, -1))
        self.root.bind("<KeyPress-d>", lambda e: self._shortcut_navigation(e, 1))
        self.root.bind("<KeyPress-D>", lambda e: self._shortcut_navigation(e, 1))
        self.root.bind("<Left>", lambda e: self._shortcut_navigation(e, -1))
        self.root.bind("<Right>", lambda e: self._shortcut_navigation(e, 1))
        self.root.bind("<space>", self._shortcut_skip)
        self.root.bind("<Delete>", self._shortcut_delete)
        self.root.bind("<Control-z>", self._shortcut_undo)
        self.root.bind("<Control-Z>", self._shortcut_undo)
        self.root.bind("<Control-f>", self.focus_search)
        self.root.bind("<Control-F>", self.focus_search)
        self.root.bind("<Control-o>", lambda _event: self.choose_source())
        self.root.bind("<Control-O>", lambda _event: self.choose_source())
        self.root.bind("<F5>", lambda _event: self.reload_images())
        self.root.bind("<Control-q>", lambda _event: self.close())
        self.root.bind("<Control-Q>", lambda _event: self.close())
        self.root.bind("<Escape>", self.clear_search)

    def _shortcut_classify(self, event: tk.Event, category_index: int) -> None:
        if event.widget.winfo_class() in {"Entry", "TEntry", "TCombobox"}:
            return
        self.classify(self.categories[category_index])

    def _shortcut_navigation(self, event: tk.Event, direction: int) -> None:
        if event.widget.winfo_class() in {"Entry", "TEntry", "TCombobox"}:
            return
        if direction < 0:
            self.previous_image()
        else:
            self.next_image()

    def _shortcut_delete(self, event: tk.Event) -> None:
        if event.widget.winfo_class() in {"Entry", "TEntry", "TCombobox"}:
            return
        self.delete_current_image()

    def _shortcut_skip(self, event: tk.Event) -> None:
        if event.widget.winfo_class() in {"Entry", "TEntry", "TCombobox"}:
            return
        self.skip_image()

    def _shortcut_undo(self, event: tk.Event) -> None:
        if event.widget.winfo_class() in {"Entry", "TEntry", "TCombobox"}:
            return
        self.undo()

    def save_settings(self) -> None:
        self.settings = {
            "data_root": str(self.data_root),
            "source": str(self.source),
            "targets": {category.key: str(category.path) for category in self.categories},
            "category_titles": {category.key: category.title for category in self.categories},
            "mode": self.mode_var.get(),
        }
        atomic_write_json(self.settings_path, self.settings)

    def choose_source(self) -> None:
        chosen = filedialog.askdirectory(title="选择待分类图片目录", initialdir=str(self.source if self.source.exists() else self.data_root))
        if not chosen:
            return
        self.source = Path(chosen)
        self.data_root = self.source.parent
        self.save_settings()
        self.reload_images()

    def choose_category_folder(self, category: Category) -> None:
        chosen = filedialog.askdirectory(
            title=f"选择【{category.title}】对应文件夹",
            initialdir=str(category.path if category.path.exists() else self.data_root),
        )
        if not chosen:
            return
        selected = Path(chosen)
        if normalized(selected) == normalized(self.source):
            messagebox.showerror("目录冲突", "待分类图片目录不能作为分类目标目录。")
            return
        for other in self.categories:
            if other.key != category.key and normalized(selected) == normalized(other.path):
                messagebox.showerror("目录重复", f"这个文件夹已经用于【{other.title}】分类。")
                return
        category.path = selected
        self.update_folder_buttons()
        self.save_settings()
        self.reload_images()
        self.status_var.set(f"【{category.title}】目标目录已更新：{selected}")

    def update_folder_buttons(self) -> None:
        for category in self.categories:
            button = getattr(category, "folder_button", None)
            if button is not None:
                button.configure(text=f"{category.title}  ·  {category.path.name}")
            title_label = getattr(category, "title_label", None)
            if title_label is not None:
                title_label.configure(text=category.title)
            menu_index = getattr(category, "menu_index", None)
            if menu_index is not None:
                self.category_menu.entryconfigure(menu_index, label=f"分类为“{category.title}”")

    def open_category_name_settings(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("编辑分类名称")
        dialog.geometry("500x340")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text="修改界面显示名称，不会改变目标文件夹和已有分类记录。",
            style="Info.TLabel",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 14))

        variables: dict[str, tk.StringVar] = {}
        for row, category in enumerate(self.categories, start=1):
            ttk.Label(frame, text=f"第 {row} 类", width=10).grid(row=row, column=0, sticky="w", pady=7)
            variable = tk.StringVar(value=category.title)
            variables[category.key] = variable
            entry = ttk.Entry(frame, textvariable=variable)
            entry.grid(row=row, column=1, sticky="ew", pady=7)
            if row == 1:
                entry.focus_set()
                entry.selection_range(0, "end")

        frame.columnconfigure(1, weight=1)

        def apply_names() -> None:
            names = [variables[category.key].get().strip() for category in self.categories]
            if any(not name for name in names):
                messagebox.showerror("名称无效", "四个分类名称都不能为空。", parent=dialog)
                return
            if len({name.casefold() for name in names}) != len(names):
                messagebox.showerror("名称重复", "四个分类名称不能重复。", parent=dialog)
                return
            for category, name in zip(self.categories, names):
                category.title = name
            self.update_folder_buttons()
            self.save_settings()
            dialog.destroy()
            self.status_var.set("分类名称已更新")

        buttons = ttk.Frame(frame)
        buttons.grid(row=6, column=0, columnspan=2, sticky="e", pady=(18, 0))
        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side="left", padx=4)
        ttk.Button(buttons, text="保存", command=apply_names).pack(side="left", padx=4)
        dialog.bind("<Return>", lambda _event: apply_names())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())

    def open_target_settings(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("设置四类目标目录")
        dialog.geometry("820x310")
        dialog.transient(self.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="每一类都可以选择任意文件夹。保存后立即生效。", style="Info.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 12)
        )
        variables: dict[str, tk.StringVar] = {}
        for row, category in enumerate(self.categories, start=1):
            ttk.Label(frame, text=category.title, width=9).grid(row=row, column=0, sticky="w", pady=6)
            variable = tk.StringVar(value=str(category.path))
            variables[category.key] = variable
            ttk.Entry(frame, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=8)

            def browse(v=variable, c=category):
                selected = filedialog.askdirectory(title=f"选择{c.title}目录", initialdir=v.get() or str(self.data_root))
                if selected:
                    v.set(selected)

            ttk.Button(frame, text="浏览", command=browse).grid(row=row, column=2)

        frame.columnconfigure(1, weight=1)

        def apply_settings() -> None:
            paths = [Path(variables[c.key].get().strip()) for c in self.categories]
            if any(not str(path).strip() for path in paths):
                messagebox.showerror("目录无效", "四个目标目录都必须填写。", parent=dialog)
                return
            if len({normalized(path) for path in paths}) != 4:
                messagebox.showerror("目录重复", "四个类别必须使用四个不同目录。", parent=dialog)
                return
            if normalized(self.source) in {normalized(path) for path in paths}:
                messagebox.showerror("目录冲突", "待分类目录不能同时作为目标目录。", parent=dialog)
                return
            for category, path in zip(self.categories, paths):
                category.path = path
                path.mkdir(parents=True, exist_ok=True)
            self.update_folder_buttons()
            self.save_settings()
            dialog.destroy()
            self.reload_images()

        buttons = ttk.Frame(frame)
        buttons.grid(row=6, column=0, columnspan=3, sticky="e", pady=(18, 0))
        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side="left", padx=4)
        ttk.Button(buttons, text="保存", command=apply_settings).pack(side="left", padx=4)

    def completed_paths(self) -> set[str]:
        # 复制模式中，已复制且目标仍存在的图片不会重复出现；“不分类”图片保留在源目录，
        # 通过进度记录排除。移动模式下源文件本身已离开队列。
        valid: set[str] = set()
        stale: list[str] = []
        for source_key, record in self.progress.items():
            mode = record.get("mode")
            source = Path(record.get("source", ""))
            destination_text = record.get("destination", "")
            destination = Path(destination_text) if destination_text else None
            if mode == "skip" and source.exists():
                valid.add(source_key)
            elif mode == "skip":
                stale.append(source_key)
            elif mode == "copy" and destination is not None and destination.exists():
                valid.add(source_key)
            elif mode == "copy":
                stale.append(source_key)
        for key in stale:
            self.progress.pop(key, None)
        if stale:
            atomic_write_json(self.progress_path, self.progress)
        return valid

    def reload_images(self) -> None:
        if not self.source.is_dir():
            self.images = []
            self.refresh_list()
            self.clear_preview("请选择待分类图片目录")
            self.status_var.set(f"未找到待分类目录：{self.source}")
            return
        self.scan_version += 1
        version = self.scan_version
        excluded = [category.path for category in self.categories]
        completed = self.completed_paths()
        source = self.source
        self.scan_in_progress = True
        self.clear_preview("正在扫描图片，请稍候……")
        self.count_var.set("正在扫描")
        self.progress_bar.configure(mode="indeterminate")
        self.progress_bar.start(12)
        self.status_var.set(f"正在后台扫描：{source}")

        def worker() -> None:
            try:
                found = scan_images(source, excluded, completed)
                error = None
            except OSError as exc:
                found = []
                error = str(exc)
            self.root.after(0, lambda: self.finish_reload(version, found, error))

        threading.Thread(target=worker, name="image-scanner", daemon=True).start()

    def finish_reload(self, version: int, images: list[Path], error: str | None) -> None:
        if version != self.scan_version:
            return
        self.scan_in_progress = False
        self.progress_bar.stop()
        self.progress_bar.configure(mode="determinate", value=0)
        if error:
            self.images = []
            self.refresh_list()
            self.clear_preview("扫描失败")
            messagebox.showerror("扫描失败", error)
            self.status_var.set("扫描失败")
            return
        self.images = images
        self.index = min(self.index, max(0, len(self.images) - 1))
        self.refresh_list()
        self.update_category_counts()
        self.show_current()
        self.status_var.set(f"扫描完成：剩余 {len(self.images)} 张图片")

    def refresh_list(self) -> None:
        self.file_list.delete(0, "end")
        query = self.search_var.get().strip().casefold()
        keywords = query.split()
        labels: list[str] = []
        self.visible_image_indices = []
        for image_index, path in enumerate(self.images):
            try:
                label = str(path.relative_to(self.source))
            except ValueError:
                label = path.name
            searchable = label.casefold()
            if keywords and not all(keyword in searchable for keyword in keywords):
                continue
            self.visible_image_indices.append(image_index)
            labels.append(label)
        if labels:
            self.file_list.insert("end", *labels)
        if query:
            self.search_result_var.set(f"{len(labels)} / {len(self.images)}")
        else:
            self.search_result_var.set(f"{len(self.images)} 张")
        self.select_current_in_list()

    def select_current_in_list(self) -> None:
        self.file_list.selection_clear(0, "end")
        if self.images and self.index in self.visible_image_indices:
            visible_index = self.visible_image_indices.index(self.index)
            self.file_list.selection_set(visible_index)
            self.file_list.activate(visible_index)
            self.file_list.see(visible_index)

    def on_list_select(self, _event=None) -> None:
        selection = self.file_list.curselection()
        if selection and selection[0] < len(self.visible_image_indices):
            self.index = self.visible_image_indices[selection[0]]
            self.show_current()

    def on_search_changed(self, *_args) -> None:
        self.refresh_list()

    def jump_to_first_search_result(self, _event=None) -> str:
        if self.visible_image_indices:
            self.index = self.visible_image_indices[0]
            self.show_current()
            self.status_var.set(f"已跳转到第一个搜索结果：{self.images[self.index].name}")
        else:
            self.status_var.set(f"没有找到包含“{self.search_var.get().strip()}”的图片")
        return "break"

    def clear_search(self, _event=None) -> str:
        self.search_var.set("")
        self.search_entry.focus_set()
        self.status_var.set("已清空文件名搜索")
        return "break"

    def focus_search(self, _event=None) -> str:
        self.search_entry.focus_set()
        self.search_entry.selection_range(0, "end")
        return "break"

    def align_index_to_search_results(self) -> None:
        """分类后优先显示下一张符合当前搜索条件的图片。"""
        if not self.search_var.get().strip() or not self.visible_image_indices:
            return
        if self.index in self.visible_image_indices:
            return
        self.index = next(
            (image_index for image_index in self.visible_image_indices if image_index >= self.index),
            self.visible_image_indices[0],
        )

    def show_current(self) -> None:
        if not self.images:
            self.clear_preview("当前目录已没有待分类图片")
            self.count_var.set("0 / 0")
            self.progress_bar["value"] = 100
            return
        self.index = max(0, min(self.index, len(self.images) - 1))
        path = self.images[self.index]
        try:
            with Image.open(path) as source_image:
                self.original_image = ImageOps.exif_transpose(source_image).convert("RGB")
            width, height = self.original_image.size
            size_mb = path.stat().st_size / (1024 * 1024)
            self.file_var.set(path.name)
            self.detail_var.set(f"{width} × {height} px  ·  {size_mb:.2f} MB")
            self.count_var.set(f"第 {self.index + 1} 张 / 剩余 {len(self.images)} 张")
            self.reset_view(render=False)
            self.render_image()
            self.select_current_in_list()
        except (OSError, ValueError) as exc:
            self.original_image = None
            self.clear_preview(f"图片读取失败\n{path.name}\n{exc}")
            self.status_var.set(f"无法读取：{path}")

    def clear_preview(self, text: str) -> None:
        self.original_image = None
        self.photo = None
        self.canvas.delete("all")
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        self.canvas.create_text(
            width / 2,
            height / 2,
            text=text,
            fill="#90a4ae",
            font=(UI_FONT, 18),
            justify="center",
        )
        self.file_var.set("")
        self.detail_var.set("")

    def reset_view(self, render: bool = True) -> None:
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        if render:
            self.render_image()

    def render_image(self) -> None:
        if self.original_image is None:
            return
        canvas_width = max(100, self.canvas.winfo_width())
        canvas_height = max(100, self.canvas.winfo_height())
        image_width, image_height = self.original_image.size
        fit = min(canvas_width / image_width, canvas_height / image_height)
        scale = max(0.03, min(12.0, fit * self.zoom))
        display_width = max(1, int(image_width * scale))
        display_height = max(1, int(image_height * scale))
        if scale < 1.0:
            # reducing_gap 先做整数级缩小，再用 LANCZOS 精细采样；大图缩小时边缘更干净。
            resized = self.original_image.resize(
                (display_width, display_height),
                Image.Resampling.LANCZOS,
                reducing_gap=3.0,
            )
            # 只补偿缩小造成的轻微软化，不改变源图片，也不过度强化噪点。
            if 0.3 <= scale <= 0.9:
                resized = resized.filter(ImageFilter.UnsharpMask(radius=0.55, percent=45, threshold=3))
        else:
            resized = self.original_image.resize(
                (display_width, display_height),
                Image.Resampling.BICUBIC,
            )
        self.photo = ImageTk.PhotoImage(resized, master=self.root)
        self.canvas.delete("all")
        self.canvas.create_image(
            round(canvas_width / 2 + self.pan_x),
            round(canvas_height / 2 + self.pan_y),
            image=self.photo,
            anchor="center",
        )

    def on_mouse_wheel(self, event: tk.Event) -> None:
        if self.original_image is None:
            return
        self.zoom *= 1.15 if event.delta > 0 else 1 / 1.15
        self.zoom = max(0.2, min(8.0, self.zoom))
        self.render_image()

    def on_drag_start(self, event: tk.Event) -> None:
        self.drag_anchor = (event.x, event.y)

    def on_drag(self, event: tk.Event) -> None:
        if self.drag_anchor is None:
            return
        old_x, old_y = self.drag_anchor
        self.pan_x += event.x - old_x
        self.pan_y += event.y - old_y
        self.drag_anchor = (event.x, event.y)
        self.render_image()

    def current_path(self) -> Path | None:
        if not self.images:
            return None
        return self.images[self.index]

    def classify(self, category: Category) -> None:
        if self.scan_in_progress:
            self.status_var.set("图片仍在扫描，请稍候")
            return
        source = self.current_path()
        if source is None:
            return
        if not source.exists():
            messagebox.showwarning("文件不存在", f"图片已经不存在：\n{source}")
            self.reload_images()
            return
        try:
            category.path.mkdir(parents=True, exist_ok=True)
            destination = unique_destination(category.path, source.name)
            mode = self.mode_var.get()
            if mode == "copy":
                shutil.copy2(source, destination)
            else:
                shutil.move(str(source), str(destination))
            record = {
                "source": str(source),
                "destination": str(destination),
                "category": category.key,
                "category_title": category.title,
                "mode": mode,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "index": str(self.index),
            }
            self.history.append(record)
            self.progress[normalized(source)] = record
            atomic_write_json(self.progress_path, self.progress)
            self.append_log({"action": "classify", **record})
            removed_index = self.index
            self.images.pop(removed_index)
            if self.index >= len(self.images):
                self.index = max(0, len(self.images) - 1)
            self.refresh_list()
            self.align_index_to_search_results()
            self.update_category_counts()
            self.show_current()
            verb = "复制" if mode == "copy" else "移动"
            self.status_var.set(f"已{verb}到【{category.title}】：{destination.name}")
        except OSError as exc:
            messagebox.showerror("分类失败", f"无法处理图片：\n{source}\n\n{exc}")
            self.status_var.set("分类失败，原图片未被覆盖")

    def append_log(self, record: dict[str, str]) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.log_dir / f"分类记录_{datetime.now():%Y%m%d}.jsonl"
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def undo(self) -> None:
        if not self.history:
            self.status_var.set("本次运行还没有可撤销的分类操作")
            return
        record = self.history.pop()
        source = Path(record["source"])
        mode = record["mode"]
        destination_text = record.get("destination", "")
        destination = Path(destination_text) if destination_text else None
        try:
            if mode == "skip":
                if not source.exists():
                    raise FileNotFoundError(f"原图片已不存在：{source}")
            elif mode == "copy":
                if destination is not None and destination.exists():
                    destination.unlink()
            else:
                if destination is None or not destination.exists():
                    raise FileNotFoundError(f"目标文件已不存在：{destination}")
                source.parent.mkdir(parents=True, exist_ok=True)
                restored = source if not source.exists() else unique_destination(source.parent, source.name)
                shutil.move(str(destination), str(restored))
                source = restored
            self.progress.pop(normalized(Path(record["source"])), None)
            atomic_write_json(self.progress_path, self.progress)
            undo_record = {"action": "undo", **record, "timestamp": datetime.now().isoformat(timespec="seconds")}
            self.append_log(undo_record)
            insert_at = min(int(record.get("index", "0")), len(self.images))
            self.images.insert(insert_at, source)
            self.index = insert_at
            self.refresh_list()
            self.update_category_counts()
            self.show_current()
            self.status_var.set(f"已撤销：{source.name}")
        except OSError as exc:
            self.history.append(record)
            messagebox.showerror("撤销失败", str(exc))

    def update_category_counts(self) -> None:
        counts = {category.key: 0 for category in self.categories}
        skipped = 0
        for record in self.progress.values():
            key = record.get("category")
            source = Path(record.get("source", ""))
            destination_text = record.get("destination", "")
            destination = Path(destination_text) if destination_text else None
            if (
                key in counts
                and destination is not None
                and destination.exists()
                and is_relative_to(source, self.source)
            ):
                counts[key] += 1
            elif key == "unclassified" and source.exists() and is_relative_to(source, self.source):
                skipped += 1
        classified = sum(counts.values())
        remaining = len(self.images)
        total = classified + skipped + remaining
        self.classified_total_var.set(str(classified))
        self.skipped_total_var.set(str(skipped))
        self.remaining_total_var.set(str(remaining))
        self.dataset_total_var.set(str(total))
        processed = classified + skipped
        self.progress_bar.configure(value=(processed / total * 100) if total else 100)
        for category in self.categories:
            value = counts[category.key]
            self.category_count_vars[category.key].set(str(value))
            count_label = getattr(category, "count_label", None)
            if count_label is not None:
                count_label.configure(text=f"{value} 张")

    def previous_image(self) -> None:
        if not self.images:
            return
        self.index = (self.index - 1) % len(self.images)
        self.show_current()

    def next_image(self) -> None:
        if not self.images:
            return
        self.index = (self.index + 1) % len(self.images)
        self.show_current()

    def skip_image(self) -> None:
        if self.scan_in_progress:
            self.status_var.set("图片仍在扫描，请稍候")
            return
        source = self.current_path()
        if source is None:
            return
        record = {
            "source": str(source),
            "destination": "",
            "category": "unclassified",
            "category_title": "不分类",
            "mode": "skip",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "index": str(self.index),
        }
        self.history.append(record)
        self.progress[normalized(source)] = record
        atomic_write_json(self.progress_path, self.progress)
        self.append_log({"action": "skip", **record})
        removed_index = self.index
        self.images.pop(removed_index)
        if self.index >= len(self.images):
            self.index = max(0, len(self.images) - 1)
        self.refresh_list()
        self.align_index_to_search_results()
        self.update_category_counts()
        self.show_current()
        self.status_var.set(f"已标记为不分类：{source.name}")

    def delete_current_image(self) -> None:
        if self.scan_in_progress:
            self.status_var.set("图片仍在扫描，请稍候")
            return
        source = self.current_path()
        if source is None:
            return
        confirmed = messagebox.askyesno(
            "删除图片",
            f"确定要永久删除当前图片吗？\n\n{source.name}\n\n删除后无法通过 Ctrl+Z 撤销。",
            icon="warning",
        )
        if not confirmed:
            self.status_var.set("已取消删除")
            return
        try:
            source.unlink()
            record = {
                "action": "delete",
                "source": str(source),
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }
            self.append_log(record)
            removed_index = self.index
            self.images.pop(removed_index)
            if self.index >= len(self.images):
                self.index = max(0, len(self.images) - 1)
            self.refresh_list()
            self.align_index_to_search_results()
            self.update_category_counts()
            self.show_current()
            self.status_var.set(f"已永久删除：{source.name}")
        except OSError as exc:
            messagebox.showerror("删除失败", f"无法删除图片：\n{source}\n\n{exc}")
            self.status_var.set("删除失败")

    def open_current_folder(self) -> None:
        path = self.current_path()
        folder = path.parent if path else self.source
        if folder.exists():
            os.startfile(folder)  # type: ignore[attr-defined]

    def close(self) -> None:
        self.save_settings()
        self.root.destroy()


def run_self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="labelyou_self_test_") as temp_name:
        root = Path(temp_name)
        source = root / "source"
        target = root / "target"
        nested_target = source / "category"
        source.mkdir()
        target.mkdir()
        nested_target.mkdir()
        (source / "a.jpg").write_bytes(b"a")
        (source / "b.PNG").write_bytes(b"b")
        (source / "note.txt").write_text("ignore", encoding="utf-8")
        (nested_target / "excluded.jpg").write_bytes(b"x")
        found = scan_images(source, [nested_target], set())
        assert [p.name for p in found] == ["a.jpg", "b.PNG"], found
        completed = {normalized(source / "a.jpg")}
        found = scan_images(source, [nested_target], completed)
        assert [p.name for p in found] == ["b.PNG"], found
        (target / "a.jpg").write_bytes(b"old")
        assert unique_destination(target, "a.jpg").name == "a__2.jpg"
        atomic_write_json(root / "state.json", {"中文": "正常"})
        assert json.loads((root / "state.json").read_text(encoding="utf-8"))["中文"] == "正常"
    print("SELF-TEST OK")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--data-root", type=Path, help="数据集根目录；默认使用程序同级上方的“数据集”目录")
    parser.add_argument("--self-test", action="store_true", help="运行不打开界面的自检")
    parser.add_argument("--scan-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        return run_self_test()
    if args.scan_test:
        data_root = args.data_root or Path(__file__).resolve().parent.parent / "数据集"
        source = data_root / "手模数据集"
        targets = [data_root / folder for _key, _title, folder, _color in CATEGORY_SPECS]
        images = scan_images(source, targets, set())
        if not images:
            raise RuntimeError(f"没有扫描到待分类图片：{source}")
        with Image.open(images[0]) as first:
            first.verify()
        print(f"SCAN-TEST OK: {len(images)} images, first={images[0].name}")
        return 0
    enable_windows_high_dpi()
    root = tk.Tk()
    configure_tk_rendering(root)
    app = ImageClassifierApp(root, args.data_root)
    if args.smoke_test:
        root.withdraw()
        root.update_idletasks()
        if not app.canvas.winfo_exists() or not app.file_list.winfo_exists():
            raise RuntimeError("界面控件创建失败")
        menu_labels = [
            child.cget("text")
            for child in app.menu_bar.winfo_children()
            if child.winfo_class() == "Menubutton"
        ]
        if menu_labels != ["文件", "编辑", "分类", "设置"]:
            raise RuntimeError("顶部菜单栏创建失败")
        if len(app.menu_buttons) != 4 or any(menu.master is not button for button, menu in app.menu_buttons):
            raise RuntimeError("下拉菜单未正确挂载到菜单按钮")
        app.images = [
            app.source / "station_1_crack.jpg",
            app.source / "station_2_residue.jpg",
            app.source / "station_2_undemolded.jpg",
        ]
        app.search_var.set("station_2 residue")
        if app.visible_image_indices != [1] or app.file_list.size() != 1:
            raise RuntimeError("文件名关键词搜索测试失败")
        app.search_var.set("")
        if app.visible_image_indices != [0, 1, 2] or app.file_list.size() != 3:
            raise RuntimeError("清空文件名搜索测试失败")
        original_title = app.categories[0].title
        app.categories[0].title = "名称测试"
        app.update_folder_buttons()
        if app.categories[0].title_label.cget("text") != "名称测试":  # type: ignore[attr-defined]
            raise RuntimeError("分类名称更新测试失败")
        if "名称测试" not in app.category_menu.entrycget(0, "label"):
            raise RuntimeError("分类菜单名称同步测试失败")
        app.categories[0].title = original_title
        app.update_folder_buttons()
        print("UI-SMOKE-TEST OK")
        root.destroy()
        return 0
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
