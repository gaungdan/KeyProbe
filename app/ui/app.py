"""KeyProbe 主窗口：CustomTkinter 实现，复用原有 async 后端逻辑。"""

import asyncio
import json
import traceback
import threading

import customtkinter as ctk

from app.checker.auditor import ModelAuditor
from app.checker.client import LLMChecker
from app.models.audit import AUDIT_LEVEL_BY_LABEL, AUDIT_LEVEL_LABELS, AuditReport
from app.models.check import CheckStatus
from app.models.config import AppConfig, AuditLevel, UrlMode
from app.services.cc_switch import (
    APP_TYPE_LABELS,
    APP_TYPE_ORDER,
    CCSwitchImportResult,
    CCSwitchProvider,
    load_cc_switch_providers,
)
from app.services.config_store import load_config
from app.services.logger_service import LoggerService
from app.services.providers import (
    AMAZON_BEDROCK,
    GOOGLE_GEMINI,
    OPENAI_RESPONSES,
    PROVIDER_OPTIONS,
    detect_provider,
    is_anthropic_provider,
    normalize_provider,
)
from app.services.url_builder import build_request_body, pick_test_url
from app.ui.audit_panel import AuditPanel
from app.ui.log_panel import LogPanel
from app.ui.result_panel import ResultPanel
from app.ui.widgets import (
    ComboBox,
    OptionMenu,
    bind_focus_ring,
    hairline,
    load_icon,
    primary_button,
    secondary_button,
)
from app.ui import theme
from app.utils.security import mask_api_key

MODEL_PLACEHOLDER = "获取或直接填写模型 ID"
CC_SWITCH_PLACEHOLDER = "（请先导入配置）"

# 拼接模式：两个互斥选项直接写死在控件上。
# 早先用开关 + 一段会随状态改写的说明文字，用户无法判断那行字
# 描述的是「当前状态」还是「被关掉的能力」——关闭状态恰恰就是自动拼接。
URL_MODE_AUTO = "自动拼接后缀"
URL_MODE_FULL = "完整地址"


def _cc_switch_dropdown_values(
    providers: list[CCSwitchProvider],
) -> tuple[list[str], set[str]]:
    """把配置排成「分组标题 + 组内条目」的下拉列表，并返回标题文本集合。"""

    groups: dict[str, list[CCSwitchProvider]] = {}
    for provider in providers:
        groups.setdefault(provider.app_type.strip().lower(), []).append(provider)

    def group_rank(app_type: str) -> int:
        return (
            APP_TYPE_ORDER.index(app_type)
            if app_type in APP_TYPE_ORDER
            else len(APP_TYPE_ORDER)
        )

    values: list[str] = []
    headers: set[str] = set()
    for app_type in sorted(groups, key=group_rank):
        items = groups[app_type]
        header = f"── {APP_TYPE_LABELS.get(app_type, app_type)}（{len(items)}）──"
        headers.add(header)
        values.append(header)
        values.extend(item.display_name for item in items)
    return values, headers


class KeyProbeApp(ctk.CTk):
    """KeyProbe 应用主窗口。"""

    def __init__(self) -> None:
        super().__init__()

        self.config_data: AppConfig = load_config()
        self.logger = LoggerService()
        self.checker = LLMChecker(log=self._checker_log)
        self.auditor = ModelAuditor(log=self._checker_log)
        self.is_processing = False
        self._listed_models: list[str] = []
        self._cc_switch_providers: dict[str, CCSwitchProvider] = {}
        self._cc_switch_headers: set[str] = set()
        self._cc_switch_current = CC_SWITCH_PLACEHOLDER
        self._selected_cc_switch_provider: CCSwitchProvider | None = None
        self._provider_source = "auto"
        self._model_source = "手动输入"

        self._setup_window()
        self._create_widgets()
        self._apply_config()
        self.logger.set_callback(
            lambda line: self.after(0, self.log_panel.log, line)
        )

    # ---------- 窗口与组件 ----------
    WINDOW_MIN_WIDTH = 980
    WINDOW_MIN_HEIGHT = 720

    # 表单标签列固定宽度，让所有输入控件左边缘严格对齐
    LABEL_COL_WIDTH = 84
    SUB_LABEL_COL_WIDTH = 52
    PAD_X = theme.GAP_XL
    ROW_PAD_Y = 2

    def _setup_window(self) -> None:
        self.title("KeyProbe - LLM Key Checker")
        # 默认高度按「真实可用高度」收敛。Tk 在这里是 DPI-unaware 的：
        # 它以为屏幕是 1920×1080，实际桌面被 125% 缩放成 1536×864，
        # 于是 920 的窗口会被系统放大成 1150 物理像素，底部连同日志一起
        # 沉到任务栏下面。这里直接问系统要工作区与 DPI，换算成 Tk 的坐标系。
        self.geometry(f"{self.WINDOW_MIN_WIDTH}x{self._default_height()}{self._center_offset()}")
        self.minsize(self.WINDOW_MIN_WIDTH, self.WINDOW_MIN_HEIGHT)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        # 窗口底色比面板更深一层，面板才会显得是「装上去的」而不是同一块平面
        self.configure(fg_color=theme.INK)

    def _default_height(self) -> int:
        """按系统工作区与 DPI 缩放算出窗口可用高度。"""

        preferred = 920
        try:
            import ctypes
            import ctypes.wintypes as wt

            rect = wt.RECT()
            ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
            work = rect.bottom - rect.top
            dpi = ctypes.windll.user32.GetDpiForSystem() or 96
            scale = dpi / 96 if dpi else 1.0
            available = int(work / scale) - 150
        except Exception:
            available = self.winfo_screenheight() - 140
        return max(self.WINDOW_MIN_HEIGHT, min(preferred, available))

    def _center_offset(self) -> str:
        """窗口居中，而不是让系统把它丢在默认位置（可能一半落在屏幕外）。"""

        try:
            width = self.WINDOW_MIN_WIDTH
            height = self._default_height()
            x = max(0, (self.winfo_screenwidth() - width) // 2)
            y = max(0, (self.winfo_screenheight() - height) // 3)
            return f"+{x}+{y}"
        except Exception:
            return ""

    def _create_widgets(self) -> None:
        # 主内容区是唯一的滚动容器：表单、结果区、底部读数坞都装在里面，
        # 窗口高度不够时由最外层那条滚动条统一浏览。
        # 早先把日志坞 dock 在窗口底部常驻，是为了让「过程证据」不被推出屏幕，
        # 代价是坞反过来把表单挤到窗口外面，且两块内容各有各的滚动秩序。
        self.content = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
            scrollbar_button_color=theme.EDGE,
            scrollbar_button_hover_color=theme.SIGNAL_DIM,
        )
        self.content.pack(fill="both", expand=True)

        # 标题栏：等宽字标，Key 用正文色、Probe 用信号色，
        # 一眼就是「仪器」而不是「后台系统」。Tk 的 Label 无法双色，故拆两个。
        # 说明文字与字标同行，anchor="s" 让它压在字标基线上而不是垂直居中。
        header = ctk.CTkFrame(self.content, fg_color="transparent")
        header.pack(fill="x", padx=self.PAD_X, pady=(theme.GAP_XS, 2))
        ctk.CTkLabel(
            header, text="Key", font=theme.mono(24, "bold"), text_color=theme.TEXT_PRIMARY
        ).pack(side="left")
        ctk.CTkLabel(
            header, text="Probe", font=theme.mono(24, "bold"), text_color=theme.SIGNAL
        ).pack(side="left")
        ctk.CTkLabel(
            header,
            text="探测接口连通性，核对模型是否名实相符",
            font=theme.font(12),
            text_color=theme.TEXT_MUTED,
            anchor="w",
        ).pack(side="left", anchor="s", padx=(theme.GAP_MD, 0), pady=(0, 4))

        # 配置表单：短字段两两并排，行数从 9 压到 5，首屏不再需要滚动
        form = ctk.CTkFrame(self.content, fg_color=theme.PANEL)
        form.pack(fill="x", padx=self.PAD_X, pady=(theme.GAP_SM, theme.GAP_XS))
        form.grid_columnconfigure(0, minsize=self.LABEL_COL_WIDTH)
        form.grid_columnconfigure(1, weight=1, uniform="field")
        form.grid_columnconfigure(2, minsize=self.SUB_LABEL_COL_WIDTH)
        form.grid_columnconfigure(3, weight=1, uniform="field")

        ctk.CTkLabel(
            form,
            text=theme.caps("config"),
            font=theme.mono(9, "bold"),
            text_color=theme.TEXT_FAINT,
            anchor="w",
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=theme.GAP_LG,
               pady=(theme.GAP_SM, theme.GAP_XS))

        # 接口地址独占整行：它是最长的字段，之前被开关挤到只剩 640px，
        # 开关那行说明文字也因此顶出面板边缘被裁掉。
        self._form_label(form, 1, "接口地址")
        url_row = ctk.CTkFrame(form, fg_color="transparent")
        url_row.grid(row=1, column=1, columnspan=3, sticky="ew",
                     padx=(theme.GAP_LG, theme.GAP_LG), pady=(self.ROW_PAD_Y, 2))
        url_row.grid_columnconfigure(0, weight=1)
        self.base_url_entry = ctk.CTkEntry(
            url_row,
            placeholder_text="https://api.openai.com/v1",
            height=theme.CONTROL_HEIGHT,
            font=theme.mono(12),
            fg_color=theme.SURFACE_INPUT,
            border_color=theme.EDGE_STRONG,
            border_width=1,
        )
        self.base_url_entry.grid(row=0, column=0, sticky="ew")
        bind_focus_ring(self.base_url_entry)
        self.url_mode_seg = ctk.CTkSegmentedButton(
            url_row,
            values=[URL_MODE_AUTO, URL_MODE_FULL],
            width=212,
            height=theme.CONTROL_HEIGHT,
            font=theme.font(12),
            corner_radius=theme.BUTTON_RADIUS,
            fg_color=theme.SURFACE_INPUT,
            selected_color=theme.SELECTED_FILL,
            selected_hover_color=theme.SELECTED_HOVER,
            unselected_color=theme.SURFACE_INPUT,
            unselected_hover_color=theme.CONTROL_HOVER,
            text_color=theme.TEXT_PRIMARY,
            command=lambda _value: self._on_url_mode_change(),
        )
        self.url_mode_seg.grid(row=0, column=1, padx=(theme.GAP_SM, 0))
        self.base_url_entry.bind("<FocusOut>", self._on_base_url_confirmed)
        self.base_url_entry.bind("<Return>", self._on_base_url_confirmed)
        self.base_url_entry.bind("<KeyRelease>", self._on_base_url_typing)

        # 实际请求地址常驻一行：拼接结果以前只藏在下面的 cURL 里，
        # 而 cURL 是用户判断「这工具靠不靠谱」的唯一凭据。
        self.endpoint_hint = ctk.CTkLabel(
            form,
            text="",
            font=theme.mono(10),
            text_color=theme.TEXT_FAINT,
            anchor="w",
        )
        self.endpoint_hint.grid(row=2, column=1, columnspan=3, sticky="w",
                                padx=(theme.GAP_LG, theme.GAP_LG),
                                pady=(0, self.ROW_PAD_Y))

        self._form_label(form, 3, "密钥")
        key_frame = ctk.CTkFrame(form, fg_color="transparent")
        key_frame.grid(row=3, column=1, columnspan=3, sticky="ew",
                       padx=(theme.GAP_LG, theme.GAP_LG), pady=self.ROW_PAD_Y)
        key_frame.grid_columnconfigure(0, weight=1)
        self.api_key_entry = ctk.CTkEntry(
            key_frame,
            placeholder_text="sk-...",
            height=theme.CONTROL_HEIGHT,
            font=theme.mono(12),
            fg_color=theme.SURFACE_INPUT,
            border_color=theme.EDGE_STRONG,
            border_width=1,
            show="*",
        )
        self.api_key_entry.grid(row=0, column=0, sticky="ew")
        bind_focus_ring(self.api_key_entry)
        self.api_key_entry.bind("<KeyRelease>", self._on_field_edited)
        self._api_key_visible = False
        self._eye_icon = load_icon("eye")
        self._eye_off_icon = load_icon("eye-off")
        self.eye_btn = ctk.CTkButton(
            key_frame,
            image=self._eye_icon,
            text="",
            width=theme.CONTROL_HEIGHT,
            height=theme.CONTROL_HEIGHT,
            fg_color=theme.NEUTRAL_FILL,
            hover_color=theme.NEUTRAL_HOVER,
            border_width=1,
            border_color=theme.NEUTRAL_EDGE,
            corner_radius=theme.BUTTON_RADIUS,
            command=self._toggle_key_visible,
        )
        self.eye_btn.grid(row=0, column=1, padx=(theme.GAP_SM, 0))

        # 「连到哪儿」与「拿这把钥匙做什么」是两件事，用一道发丝线分开。
        # 面板顶部只有一个眉标，所以这条线只负责分组，不各起一个标题。
        hairline(form).grid(row=4, column=0, columnspan=4, sticky="ew",
                            padx=theme.GAP_LG, pady=(theme.GAP_SM, theme.GAP_MD))

        # 服务商与模型是一组：先定协议，再定模型，并排比上下分开更贴合心智
        self._form_label(form, 5, "服务商")
        self.provider_menu = OptionMenu(
            form,
            values=PROVIDER_OPTIONS,
            height=theme.CONTROL_HEIGHT,
            font=theme.font(13),
            command=self._on_provider_change,
        )
        self.provider_menu.grid(row=5, column=1, sticky="ew",
                                padx=(theme.GAP_LG, 0), pady=self.ROW_PAD_Y)

        self._sub_label(form, 5, "模型")
        model_row = ctk.CTkFrame(form, fg_color="transparent")
        model_row.grid(row=5, column=3, sticky="ew",
                       padx=(0, theme.GAP_LG), pady=self.ROW_PAD_Y)
        model_row.grid_columnconfigure(0, weight=1)
        # 允许手动输入：部分中转站不提供 /models，模型 ID 只能靠用户自己填
        self.model_menu = ComboBox(
            model_row,
            values=[],
            placeholder_text=MODEL_PLACEHOLDER,
            command=self._on_model_selected,
            height=theme.CONTROL_HEIGHT,
            font=theme.mono(12),
        )
        self.model_menu.grid(row=0, column=0, sticky="ew")
        self.model_menu.bind("<KeyRelease>", self._on_model_typed)
        bind_focus_ring(self.model_menu._entry, target=self.model_menu)
        # 「获取模型」的结果就写进右手边这个框，按钮跟着字段走；
        # 操作条因此只剩两个「执行」动作，不再有四颗高低不一的按钮抢视线。
        self.fetch_btn = secondary_button(
            model_row,
            text="获取模型",
            width=92,
            height=theme.CONTROL_HEIGHT,
            command=self._on_fetch_models,
        )
        self.fetch_btn.grid(row=0, column=1, padx=(theme.GAP_SM, 0))

        self._form_label(form, 6, "CC Switch")
        cc_row = ctk.CTkFrame(form, fg_color="transparent")
        cc_row.grid(row=6, column=1, sticky="ew",
                    padx=(theme.GAP_LG, 0), pady=self.ROW_PAD_Y)
        cc_row.grid_columnconfigure(0, weight=1)
        self.cc_switch_menu = ComboBox(
            cc_row,
            values=[CC_SWITCH_PLACEHOLDER],
            readonly=True,
            height=theme.CONTROL_HEIGHT,
            font=theme.font(13),
            command=self._on_cc_switch_provider_selected,
        )
        self.cc_switch_menu.grid(row=0, column=0, sticky="ew")
        self.cc_switch_menu.set(CC_SWITCH_PLACEHOLDER)
        self.cc_switch_menu.configure(state="disabled")
        # 没导入前这个框是死的：入口摆在它右手边，
        # 用户不必往两行之外找，才知道该点哪里把它激活。
        # 与「获取模型」同款实心 + 描边、同宽：两颗按钮都是「字段右边的动作」，
        # 早先一颗实心一颗只有描边，并排读起来像两种不同的东西。
        self.import_btn = secondary_button(
            cc_row,
            text="导入配置",
            width=92,
            height=theme.CONTROL_HEIGHT,
            command=self._on_import_cc_switch,
        )
        self.import_btn.grid(row=0, column=1, padx=(theme.GAP_SM, 0))

        self._sub_label(form, 6, "超时")
        timeout_box = ctk.CTkFrame(form, fg_color="transparent")
        timeout_box.grid(row=6, column=3, sticky="w",
                         padx=(0, theme.GAP_LG), pady=self.ROW_PAD_Y)
        self.timeout_entry = ctk.CTkEntry(
            timeout_box,
            placeholder_text="30",
            width=72,
            height=theme.CONTROL_HEIGHT,
            font=theme.mono(12),
            fg_color=theme.SURFACE_INPUT,
            border_color=theme.EDGE_STRONG,
            border_width=1,
        )
        self.timeout_entry.pack(side="left")
        bind_focus_ring(self.timeout_entry)
        self.timeout_entry.bind("<KeyRelease>", self._on_field_edited)
        ctk.CTkLabel(
            timeout_box, text="秒", font=theme.mono(11), text_color=theme.TEXT_FAINT
        ).pack(side="left", padx=(theme.GAP_SM, 0))

        self._form_label(form, 7, "体检深度")
        audit_box = ctk.CTkFrame(form, fg_color="transparent")
        audit_box.grid(row=7, column=1, columnspan=3, sticky="ew",
                       padx=(theme.GAP_LG, theme.GAP_LG), pady=self.ROW_PAD_Y)
        self.audit_level_menu = OptionMenu(
            audit_box,
            values=list(AUDIT_LEVEL_LABELS.values()),
            width=170,
            height=theme.CONTROL_HEIGHT,
            font=theme.font(13),
        )
        self.audit_level_menu.pack(side="left")
        ctk.CTkLabel(
            audit_box,
            text="每档都会真实调用模型，档位越深消耗的 token 越多",
            font=theme.font(11),
            text_color=theme.TEXT_FAINT,
        ).pack(side="left", padx=theme.GAP_MD)

        # 字段级校验反馈：错误必须出现在表单里，而不是首屏之外的日志页签。
        # 空文本时整行 grid_remove，不占布局高度。
        self.form_error = ctk.CTkLabel(
            form,
            text="",
            font=theme.mono(11),
            text_color=theme.ERROR,
            anchor="w",
        )
        self.form_error.grid(row=8, column=1, columnspan=3, sticky="w",
                             padx=(theme.GAP_LG, theme.GAP_LG),
                             pady=(0, theme.GAP_SM))
        self.form_error.grid_remove()
        self._error_field = None

        # 操作条只留两个「执行」动作：等高、靠右、与阅读动线一致。
        # 主操作仍是全窗唯一的实心蓝，次级用中性实心加描边。
        actions = ctk.CTkFrame(self.content, fg_color="transparent")
        actions.pack(fill="x", padx=self.PAD_X, pady=(theme.GAP_XS, theme.GAP_XS))
        self.test_btn = primary_button(
            actions, text="测试接口", width=136, command=self._on_test
        )
        self.test_btn.pack(side="right")
        self.audit_btn = secondary_button(
            actions, text="模型体检", width=124, command=self._on_audit
        )
        self.audit_btn.pack(side="right", padx=theme.GAP_SM)

        # 忙碌指示条：常驻一条发丝线，空闲时就是分隔线，运行时才走进度。
        # 原实现靠 pack_forget 显隐，每次操作都会把下方内容顶动一次。
        self.progress_bar = ctk.CTkProgressBar(
            self.content,
            height=3,
            corner_radius=0,
            fg_color=theme.EDGE,
            progress_color=theme.SIGNAL,
            mode="determinate",
        )
        self.progress_bar.pack(fill="x", padx=self.PAD_X)
        self.progress_bar.set(0)

        # 结果区
        self.result_panel = ResultPanel(
            self.content, on_copy=lambda text: self._copy_to_clipboard(text, "cURL 命令")
        )
        self.result_panel.pack(fill="x", padx=self.PAD_X, pady=theme.GAP_XS)

        # 底部读数坞：执行日志 + 模型体检，跟着主内容一起滚。
        # 上方补一道发丝线：它与 cURL 窗同为深色，中间只有一截黑，
        # 看不出「这是一个成组的读数区」而不是又一块散内容。
        hairline(self.content).pack(fill="x", padx=self.PAD_X, pady=(theme.GAP_SM, 0))

        # 页签栏底色与面板同色：原来用 INK 会在面板上压出一条突兀的黑带。
        # 选中态原来用 NEUTRAL_FILL（#1C2533）几乎等于面板色，
        # 两个页签看上去像都没选中；提到 TAB_SELECTED 才读得出「当前在这」。
        self.bottom_tabs = ctk.CTkTabview(
            self.content,
            fg_color=theme.PANEL,
            segmented_button_fg_color=theme.PANEL,
            segmented_button_selected_color=theme.TAB_SELECTED,
            segmented_button_selected_hover_color=theme.TAB_SELECTED_HOVER,
            segmented_button_unselected_color=theme.PANEL,
            segmented_button_unselected_hover_color=theme.CONTROL_HOVER,
            text_color=theme.TEXT_PRIMARY,
            anchor="w",
        )
        self.bottom_tabs.pack(fill="x", padx=self.PAD_X, pady=(theme.GAP_SM, theme.GAP_MD))

        log_tab = self.bottom_tabs.add("执行日志")
        audit_tab = self.bottom_tabs.add("模型体检")

        self.log_panel = LogPanel(log_tab, on_clear=self.logger.clear)
        self.log_panel.pack(fill="both", expand=True)

        self.audit_panel = AuditPanel(
            audit_tab, on_copy=lambda text: self._copy_to_clipboard(text, "体检报告")
        )
        self.audit_panel.pack(fill="both", expand=True)

    def _form_label(self, master, row: int, text: str) -> None:
        """表单左列标签。等宽字体让「接口地址 / 密钥 / 服务商」右边缘等宽对齐。"""

        ctk.CTkLabel(
            master, text=text, font=theme.mono(12), text_color=theme.TEXT_MUTED, anchor="w"
        ).grid(row=row, column=0, sticky="w", padx=(theme.GAP_LG, 0), pady=self.ROW_PAD_Y)

    def _sub_label(self, master, row: int, text: str) -> None:
        """并排布局中第二个字段的标签，落在第 2 列。"""

        ctk.CTkLabel(
            master, text=text, font=theme.mono(12), text_color=theme.TEXT_MUTED, anchor="w"
        ).grid(row=row, column=2, sticky="w", padx=(theme.GAP_LG, 0), pady=self.ROW_PAD_Y)

    # ---------- 配置回填 ----------
    def _apply_config(self) -> None:
        cfg = self.config_data
        self._set_entry(self.base_url_entry, cfg.base_url)
        # API Key 不持久化，启动时不回填，需手动输入
        self._set_entry(self.timeout_entry, str(cfg.timeout))
        provider = normalize_provider(cfg.provider)
        if provider in PROVIDER_OPTIONS:
            self._set_provider(provider, source="config")
        self.url_mode_seg.set(URL_MODE_AUTO if cfg.url_mode == "auto" else URL_MODE_FULL)
        self._update_endpoint_hint()
        if cfg.model:
            self.model_menu.configure(values=[cfg.model])
            self.model_menu.set(cfg.model)
            self._model_source = "config.json"
        self.audit_level_menu.set(
            AUDIT_LEVEL_LABELS.get(cfg.audit_level, AUDIT_LEVEL_LABELS["standard"])
        )
        self._update_curl_preview()

    @staticmethod
    def _set_entry(entry: ctk.CTkEntry, value: str) -> None:
        entry.delete(0, "end")
        entry.insert(0, value.strip())

    # ---------- 表单读取 ----------
    def _url_mode(self) -> UrlMode:
        return "full" if self.url_mode_seg.get() == URL_MODE_FULL else "auto"

    def _timeout(self) -> int:
        raw = self.timeout_entry.get().strip()
        try:
            return max(1, min(300, int(raw)))
        except ValueError:
            return 30

    def _audit_level(self) -> AuditLevel:
        return AUDIT_LEVEL_BY_LABEL.get(self.audit_level_menu.get(), "standard")

    def _collect_config(self) -> AppConfig:
        return AppConfig(
            base_url=self.base_url_entry.get().strip(),
            api_key=self.api_key_entry.get().strip(),
            url_mode=self._url_mode(),
            provider=normalize_provider(self.provider_menu.get()),
            model=self._selected_model(),
            timeout=self._timeout(),
            theme_mode="dark",
            audit_level=self._audit_level(),
        )

    def _on_model_selected(self, choice: str) -> None:
        self._model_source = "手动选择"
        self._update_curl_preview()

    def _on_model_typed(self, event=None) -> None:
        """手动输入模型 ID：中转站拿不到 /models 时的唯一入口。"""

        self._model_source = "手动输入"
        self._update_curl_preview()

    def _on_provider_change(self, choice: str) -> None:
        self._provider_source = "manual"
        self._selected_cc_switch_provider = None
        self._update_curl_preview()

    # ---------- 事件 ----------
    def _on_base_url_typing(self, event=None) -> None:
        """输入过程中只更新「实际请求」这一行，不重算 cURL。

        cURL 框高度随内容变化，若每敲一个字都重算，下方状态条、页签、
        日志区会跟着上下跳动。这里只改一行等宽小字，不触发任何重排。
        """

        if self._provider_source == "cc_switch":
            self._provider_source = "manual"
        self._selected_cc_switch_provider = None
        self._clear_form_error()
        self._update_endpoint_hint()

    def _on_base_url_confirmed(self, event=None) -> None:
        self._select_provider_from_base_url()
        self._update_endpoint_hint()
        self._update_curl_preview()

    def _on_url_mode_change(self) -> None:
        self._update_endpoint_hint()
        self._update_curl_preview()

    def _on_field_edited(self, event=None) -> None:
        self._clear_form_error()

    def _select_provider_from_base_url(self) -> None:
        base_url = self.base_url_entry.get().strip()
        if not base_url:
            return
        if self._provider_source in {"manual", "config", "cc_switch"}:
            return
        if (
            self._selected_cc_switch_provider is not None
            and base_url == self._selected_cc_switch_provider.base_url
        ):
            return

        provider = detect_provider(base_url)
        if provider in PROVIDER_OPTIONS and self.provider_menu.get() != provider:
            self._set_provider(provider, source="auto")

    def _set_provider(self, provider: str, *, source: str) -> None:
        # Temporarily remove command to avoid callback during programmatic set
        cmd = self.provider_menu._command
        self.provider_menu.configure(command=lambda _: None)
        self.provider_menu.set(provider)
        self.provider_menu.configure(command=cmd)
        self._provider_source = source

    def _update_endpoint_hint(self) -> None:
        """把「实际会请求哪个地址」常驻在接口地址下方。

        拼接逻辑（自动补后缀、去 /v1/v1、识别协议）以前完全隐形，
        用户只能从下面的 cURL 里反推。这里把它提前成一行实时反馈。
        """

        base_url = self.base_url_entry.get().strip()
        if not base_url:
            self.endpoint_hint.configure(text="")
            return

        provider = self.provider_menu.get()
        model = self._selected_model() or "<模型>"
        try:
            url = pick_test_url(provider, base_url, model, self._url_mode())
        except Exception:
            url = base_url

        if len(url) > 88:
            url = f"{url[:44]}…{url[-40:]}"
        self.endpoint_hint.configure(text=f"实际请求 {url}")

    def _toggle_key_visible(self) -> None:
        self._api_key_visible = not self._api_key_visible
        self.api_key_entry.configure(show="" if self._api_key_visible else "*")
        self.eye_btn.configure(image=self._eye_off_icon if self._api_key_visible else self._eye_icon)

    def _checker_log(self, message: str) -> None:
        self.logger.info(message)

    def _lock(self, locked: bool, busy: tuple | None = None) -> None:
        """进入/退出忙碌态。

        ``busy`` 传入 ``(按钮, "进行中的文案")``：被点的那个按钮原地改写文案，
        用户才能确认「我刚才点的是哪个」。只把按钮变灰的话，30 秒超时期间
        界面看起来就是没反应。
        """

        self.is_processing = locked
        state = "disabled" if locked else "normal"
        self.import_btn.configure(state=state)
        self.fetch_btn.configure(state=state)
        self.test_btn.configure(state=state)
        self.audit_btn.configure(state=state)
        self.cc_switch_menu.configure(
            state="disabled" if locked or not self._cc_switch_providers else "normal"
        )
        if locked:
            if busy is not None:
                button, label = busy
                self._busy_restore = (button, button.cget("text"))
                button.configure(text=label)
            # 常驻的发丝线切到不定长动画，布局不动，只有那条线活起来
            self.progress_bar.configure(mode="indeterminate")
            self.progress_bar.start()
        else:
            restore = getattr(self, "_busy_restore", None)
            if restore is not None:
                button, text = restore
                button.configure(text=text)
                self._busy_restore = None
            self.progress_bar.stop()
            self.progress_bar.configure(mode="determinate")
            self.progress_bar.set(0)

    # ---------- 校验反馈 ----------
    def _show_form_error(self, message: str, field=None) -> None:
        """把校验失败摆在表单里，而不是只写进首屏之外的日志页签。"""

        self.form_error.configure(text=f"!  {message}")
        self.form_error.grid()
        if field is not None:
            self._error_field = field
            # 先立标记再上色：紧接着的 focus_set 会触发 FocusIn，
            # 没有这个标记，焦点环会把错误描边盖成蓝色。
            try:
                field._error_ring = True
                field.configure(border_color=theme.ERROR, border_width=2)
            except Exception:
                pass
            field.focus_set()

    def _clear_form_error(self) -> None:
        if self._error_field is not None:
            try:
                self._error_field._error_ring = False
                self._error_field.configure(
                    border_color=theme.EDGE_STRONG, border_width=1
                )
            except Exception:
                pass
            self._error_field = None
        self.form_error.grid_remove()

    def _fail_validation(self, message: str) -> None:
        self._show_form_error(message, self._error_field)
        self.logger.error(message)

    def _on_import_cc_switch(self) -> None:
        if self.is_processing:
            self.logger.warning("正在处理中，请稍候...")
            return
        self._lock(True)
        threading.Thread(target=self._import_cc_switch_worker, daemon=True).start()

    def _import_cc_switch_worker(self) -> None:
        try:
            self.logger.info("========== 导入 CC Switch 配置 ==========")
            result = load_cc_switch_providers()
            self.after(0, lambda: self._apply_cc_switch_import(result))
        except Exception as exc:
            self.logger.error(f"导入 CC Switch 配置失败：{exc}")
            self.logger.error(traceback.format_exc())
        finally:
            self.after(0, lambda: self._lock(False))

    def _apply_cc_switch_import(self, result: CCSwitchImportResult) -> None:
        if result.error_message:
            self._cc_switch_providers = {}
            self._cc_switch_headers = set()
            self._cc_switch_current = CC_SWITCH_PLACEHOLDER
            self.cc_switch_menu.configure(values=[CC_SWITCH_PLACEHOLDER], state="disabled")
            self.cc_switch_menu.set(CC_SWITCH_PLACEHOLDER)
            self.logger.error(result.error_message)
            return

        self._cc_switch_providers = {
            provider.display_name: provider for provider in result.providers
        }
        values, headers = _cc_switch_dropdown_values(result.providers)
        self._cc_switch_headers = headers
        self._cc_switch_current = CC_SWITCH_PLACEHOLDER
        self.cc_switch_menu.configure(values=values, state="normal")
        # 分组标题在弹层里置灰、不可点：它只说明「下面这一组属于哪个 App」
        self.cc_switch_menu.set_header_values(headers)
        self.cc_switch_menu.set(CC_SWITCH_PLACEHOLDER)
        self.logger.success(
            f"已从 CC Switch 导入 {len(self._cc_switch_providers)} 个可检测配置：{result.source_path}"
        )
        if result.skipped_count:
            self.logger.info(f"已跳过 {result.skipped_count} 个不完整或不支持的配置")
        provider_labels = list(self._cc_switch_providers)
        if provider_labels:
            first_label = provider_labels[0]
            self.cc_switch_menu.set(first_label)
            self._load_cc_switch_provider(first_label, auto_fetch=True)

    def _on_cc_switch_provider_selected(self, choice: str) -> None:
        if choice in self._cc_switch_headers:
            # 分组标题只是视觉分隔，选中后回退到上一次的有效配置
            self.cc_switch_menu.set(self._cc_switch_current)
            return
        self._load_cc_switch_provider(choice, auto_fetch=True)

    def _load_cc_switch_provider(self, choice: str, *, auto_fetch: bool) -> None:
        provider = self._cc_switch_providers.get(choice)
        if provider is None:
            return

        self._cc_switch_current = choice
        self._selected_cc_switch_provider = provider
        self._set_entry(self.base_url_entry, provider.base_url)
        self._set_entry(self.api_key_entry, provider.api_key)
        self.url_mode_seg.set(
            URL_MODE_AUTO if provider.url_mode == "auto" else URL_MODE_FULL
        )
        self._set_provider(provider.provider, source="cc_switch")
        self.model_menu.configure(values=[provider.model])
        self.model_menu.set(provider.model)
        self._model_source = f"CC Switch · {provider.display_name}"
        # 换了中转站，上一家的模型列表随即作废，等下面的自动获取重新填充
        self._listed_models = []
        self.result_panel.set_model_count(0)
        self._clear_form_error()
        self._update_endpoint_hint()
        self._update_curl_preview()
        self.logger.info(
            f"已加载 CC Switch 配置：{provider.display_name}，默认模型：{provider.model}"
        )
        if auto_fetch:
            if self.is_processing:
                # 导入刚完成，不可能在处理中，但以防万一
                self.after(100, self._on_fetch_models)
            else:
                self._on_fetch_models()

    def _on_fetch_models(self) -> None:
        if self.is_processing:
            self.logger.warning("正在处理中，请稍候...")
            return
        err = self._validate_inputs()
        if err:
            self._fail_validation(err)
            return
        self._clear_form_error()
        self._select_provider_from_base_url()
        self._lock(True, busy=(self.fetch_btn, "获取中…"))
        threading.Thread(target=self._fetch_models_worker, daemon=True).start()

    def _fetch_models_worker(self) -> None:
        try:
            cfg = self._collect_config()
            self.logger.info("========== 获取模型 ==========")
            result = asyncio.run(
                self.checker.fetch_models(
                    provider=cfg.provider,
                    api_key=cfg.api_key,
                    timeout=cfg.timeout,
                    url_mode=cfg.url_mode,
                    base_url=cfg.base_url,
                )
            )
            self.after(0, lambda: self._apply_models_result(result))
        except Exception as exc:
            self.logger.error(f"获取模型异常: {exc}")
            self.logger.error(traceback.format_exc())
        finally:
            self.after(0, lambda: self._lock(False))

    def _apply_models_result(self, result) -> None:
        self.result_panel.set_provider(result.provider)
        if result.models:
            self._listed_models = list(result.models)
            self.model_menu.configure(values=result.models)
            current = self._selected_model()
            if current in result.models:
                pass
            elif self._model_source == "手动输入" and current:
                # 手动填写的模型 ID 多半正是列表里没有的那个，不能被列表首项顶掉
                self.logger.warning(
                    f"手动填写的模型 {current} 不在模型列表中，仍按填写值测试"
                )
            else:
                self.model_menu.set(result.models[0])
                self._model_source = "模型列表首项"
            self.result_panel.set_model_count(len(result.models))
        else:
            self._listed_models = []
            self.result_panel.set_model_count(0)
            if result.error_message:
                self.logger.error(result.error_message)
            if result.raw:
                raw_text = (
                    json.dumps(result.raw, ensure_ascii=False, indent=2)
                    if isinstance(result.raw, dict)
                    else str(result.raw)
                )
                self.logger.error(f"完整响应：\n{raw_text}")
            self.logger.info("未取到模型列表，可在「模型」框直接填写模型 ID 后测试")
        if result.latency_ms is not None:
            self.result_panel.set_latency(result.latency_ms)
        self._update_curl_preview()

    def _on_test(self) -> None:
        if self.is_processing:
            self.logger.warning("正在处理中，请稍候...")
            return
        err = self._validate_inputs()
        if err:
            self._fail_validation(err)
            return
        if not self._selected_model():
            self._show_form_error("请先选择或填写模型", self.model_menu)
            self.logger.error("请先选择或填写模型")
            return
        self._clear_form_error()
        self._select_provider_from_base_url()
        self._lock(True, busy=(self.test_btn, "测试中…"))
        threading.Thread(target=self._test_worker, daemon=True).start()

    def _test_worker(self) -> None:
        try:
            cfg = self._collect_config()
            model_count = len(self._current_models())
            self.logger.info("========== 测试接口 ==========")
            result = asyncio.run(
                self.checker.test_model(
                    provider=cfg.provider,
                    api_key=cfg.api_key,
                    model=cfg.model,
                    timeout=cfg.timeout,
                    url_mode=cfg.url_mode,
                    base_url=cfg.base_url,
                    model_count=model_count,
                )
            )
            self.after(0, lambda: self._apply_test_result(result))
        except Exception as exc:
            self.logger.error(f"测试接口异常: {exc}")
            self.logger.error(traceback.format_exc())
        finally:
            self.after(0, lambda: self._lock(False))

    def _apply_test_result(self, result) -> None:
        self.result_panel.set_status(result.status)
        self.result_panel.set_latency(result.latency_ms)
        self.result_panel.set_provider(result.provider)
        if result.error_message:
            self.logger.error(result.error_message)
        if result.raw:
            raw_text = (
                json.dumps(result.raw, ensure_ascii=False, indent=2)
                if isinstance(result.raw, dict)
                else str(result.raw)
            )
            self.logger.error(f"完整响应：\n{raw_text}")

    # ---------- 模型体检 ----------
    def _on_audit(self) -> None:
        if self.is_processing:
            self.logger.warning("正在处理中，请稍候...")
            return
        err = self._validate_inputs()
        if err:
            self._fail_validation(err)
            return
        if not self._selected_model():
            self._show_form_error("请先选择或填写模型", self.model_menu)
            self.logger.error("请先选择或填写模型")
            return
        self._clear_form_error()
        self._select_provider_from_base_url()
        self.bottom_tabs.set("模型体检")
        self.audit_panel.reset()
        self._lock(True, busy=(self.audit_btn, "体检中…"))
        threading.Thread(target=self._audit_worker, daemon=True).start()

    def _audit_worker(self) -> None:
        try:
            cfg = self._collect_config()
            self.logger.info("========== 模型体检 ==========")
            report = asyncio.run(
                self.auditor.audit(
                    provider=cfg.provider,
                    api_key=cfg.api_key,
                    model=cfg.model,
                    timeout=cfg.timeout,
                    url_mode=cfg.url_mode,
                    base_url=cfg.base_url,
                    level=cfg.audit_level,
                    known_models=self._current_models(),
                    configured_source=self._model_source,
                )
            )
            self.after(0, lambda: self._apply_audit_report(report))
        except Exception as exc:
            self.logger.error(f"模型体检异常: {exc}")
            self.logger.error(traceback.format_exc())
            self.after(0, lambda: self.audit_panel.reset())
        finally:
            self.after(0, lambda: self._lock(False))

    def _apply_audit_report(self, report: AuditReport) -> None:
        self.audit_panel.show_report(report)
        self.result_panel.set_provider(report.provider)
        if report.error_message:
            self.logger.error(f"体检未完成：{report.error_message}")
            for probe in report.probes:
                if probe.error_message:
                    self.logger.error(
                        f"  探针「{probe.title}」→ {probe.url} "
                        f"HTTP {probe.status_code or '—'}｜{probe.error_message}"
                    )
            return
        problems = report.problems()
        if problems:
            self.logger.warning(f"体检发现 {len(problems)} 项需要关注：")
            for finding in problems:
                self.logger.warning(f"  · {finding.title} — {finding.detail}")
        else:
            self.logger.success("体检未发现异常")

    def _copy_to_clipboard(self, text: str, what: str = "内容") -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.logger.success(f"{what}已复制到剪贴板")
        except Exception as exc:
            self.logger.error(f"复制失败：{exc}")

    def _selected_model(self) -> str:
        return self.model_menu.get().strip()

    def _current_models(self) -> list[str]:
        """服务端真实返回过的模型列表；手动填写或配置带入的模型不算在内。"""

        return self._listed_models

    def _validate_inputs(self) -> str | None:
        """Validate form inputs before making API calls. Returns error message or None.

        失败时同时把出错的字段记到 ``_error_field``，由调用方转成内联报错——
        错误只写进日志页签的话，它落在首屏之外，界面看起来就是「点了没反应」。
        """

        self._error_field = None
        base_url = self.base_url_entry.get().strip()
        if not base_url:
            self._error_field = self.base_url_entry
            return "接口地址不能为空"
        if "://" not in base_url:
            self._error_field = self.base_url_entry
            return "接口地址格式不正确，请包含协议（如 https://）"

        timeout = self.timeout_entry.get().strip()
        if timeout:
            try:
                val = int(timeout)
                if val < 1 or val > 300:
                    self._error_field = self.timeout_entry
                    return "超时时间必须在 1～300 秒之间"
            except ValueError:
                self._error_field = self.timeout_entry
                return "超时时间必须为数字"

        if not self.api_key_entry.get().strip():
            self._error_field = self.api_key_entry
            return "密钥不能为空"

        return None

    # ---------- cURL 预览 ----------
    def _update_curl_preview(self) -> None:
        self._update_endpoint_hint()
        base_url = self.base_url_entry.get().strip()
        provider = self.provider_menu.get()
        api_key = mask_api_key(self.api_key_entry.get())
        model = self._selected_model() or "<模型>"

        if not base_url:
            self.result_panel.set_curl("")
            return

        url = pick_test_url(provider, base_url, model, self._url_mode())
        auth_header = self._curl_auth_header(provider, api_key)
        body = build_request_body(provider, model)

        curl = (
            f"curl -X POST {url} \
"
            f'  {auth_header} \
'
            f'  -H "Content-Type: application/json" \
'
            f"  -d '{json.dumps(body, ensure_ascii=False)}'"
        )
        self.result_panel.set_curl(curl)

    @staticmethod
    def _curl_auth_header(provider: str, masked_key: str) -> str:
        """Return the cURL auth header for the given provider."""
        p = provider.lower()
        if "anthropic" in p or "claude" in p or "协议" in p:
            return f'-H "x-api-key: {masked_key}" -H "anthropic-version: 2023-06-01"'
        if "gemini" in p:
            return f'-H "x-goog-api-key: {masked_key}"'
        return f'-H "Authorization: Bearer {masked_key}"'