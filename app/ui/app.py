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
from app.services.cc_switch import CCSwitchImportResult, CCSwitchProvider, load_cc_switch_providers
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
    ghost_button,
    load_icon,
    primary_button,
    signal_button,
)
from app.ui import theme
from app.utils.security import mask_api_key

MODEL_PLACEHOLDER = "（先获取模型）"
CC_SWITCH_PLACEHOLDER = "（请先导入配置）"


class KeyProbeApp(ctk.CTk):
    """KeyProbe 应用主窗口。"""

    def __init__(self) -> None:
        super().__init__()

        self.config_data: AppConfig = load_config()
        self.logger = LoggerService()
        self.checker = LLMChecker(log=self._checker_log)
        self.auditor = ModelAuditor(log=self._checker_log)
        self.is_processing = False
        self._models: list[str] = []
        self._cc_switch_providers: dict[str, CCSwitchProvider] = {}
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
    WINDOW_MIN_HEIGHT = 780

    # 表单标签列固定宽度，让所有输入控件左边缘严格对齐
    LABEL_COL_WIDTH = 84
    SUB_LABEL_COL_WIDTH = 52
    PAD_X = theme.GAP_XL
    ROW_PAD_Y = 6

    def _setup_window(self) -> None:
        self.title("KeyProbe - LLM Key Checker")
        # 表单压到 5 行后首屏能同时容纳日志区，默认高度给到 880 而不是贴着最小值
        self.geometry(f"{self.WINDOW_MIN_WIDTH}x880")
        self.minsize(self.WINDOW_MIN_WIDTH, self.WINDOW_MIN_HEIGHT)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        # 窗口底色比面板更深一层，面板才会显得是「装上去的」而不是同一块平面
        self.configure(fg_color=theme.INK)

    def _create_widgets(self) -> None:
        # 可滚动主内容区：内容超出窗口高度时自动出现滚动条，底部内容可滚动查看
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
        header.pack(fill="x", padx=self.PAD_X, pady=(theme.GAP_SM, 2))
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
        form.pack(fill="x", padx=self.PAD_X, pady=(theme.GAP_LG, theme.GAP_MD))
        form.grid_columnconfigure(0, minsize=self.LABEL_COL_WIDTH)
        form.grid_columnconfigure(1, weight=1, uniform="field")
        form.grid_columnconfigure(2, minsize=self.SUB_LABEL_COL_WIDTH)
        form.grid_columnconfigure(3, weight=1, uniform="field")

        ctk.CTkLabel(
            form,
            text=theme.caps("endpoint"),
            font=theme.mono(9, "bold"),
            text_color=theme.TEXT_FAINT,
            anchor="w",
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=theme.GAP_LG,
               pady=(theme.GAP_LG, theme.GAP_SM))

        # 接口地址与拼接模式同属一个决定，放同一行；开关文字本身就是当前状态说明
        self._form_label(form, 1, "接口地址")
        url_row = ctk.CTkFrame(form, fg_color="transparent")
        url_row.grid(row=1, column=1, columnspan=3, sticky="ew",
                     padx=theme.GAP_LG, pady=self.ROW_PAD_Y)
        url_row.grid_columnconfigure(0, weight=1)
        self.base_url_entry = ctk.CTkEntry(
            url_row,
            placeholder_text="https://api.openai.com/v1",
            height=theme.CONTROL_HEIGHT,
            font=theme.mono(12),
            fg_color=theme.SURFACE_INPUT,
            border_color=theme.EDGE,
        )
        self.base_url_entry.grid(row=0, column=0, sticky="ew")
        self.url_mode_switch = ctk.CTkSwitch(
            url_row,
            text="",
            font=theme.font(12),
            command=self._update_url_mode_label,
            width=40,
            progress_color=theme.SIGNAL,
        )
        self.url_mode_switch.grid(row=0, column=1, padx=(theme.GAP_MD, 0))
        self.base_url_entry.bind("<FocusOut>", self._on_base_url_confirmed)
        self.base_url_entry.bind("<Return>", self._on_base_url_confirmed)
        self.base_url_entry.bind("<KeyRelease>", self._on_base_url_typing)

        self._form_label(form, 2, "密钥")
        key_frame = ctk.CTkFrame(form, fg_color="transparent")
        key_frame.grid(row=2, column=1, columnspan=3, sticky="ew",
                       padx=theme.GAP_LG, pady=self.ROW_PAD_Y)
        key_frame.grid_columnconfigure(0, weight=1)
        self.api_key_entry = ctk.CTkEntry(
            key_frame,
            placeholder_text="sk-...",
            height=theme.CONTROL_HEIGHT,
            font=theme.mono(12),
            fg_color=theme.SURFACE_INPUT,
            border_color=theme.EDGE,
            show="*",
        )
        self.api_key_entry.grid(row=0, column=0, sticky="ew")
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
            border_width=0,
            command=self._toggle_key_visible,
        )
        self.eye_btn.grid(row=0, column=1, padx=(theme.GAP_SM, 0))

        # 服务商与模型是一组：先定协议，再定模型，并排比上下分开更贴合心智
        self._form_label(form, 3, "服务商")
        self.provider_menu = OptionMenu(
            form,
            values=PROVIDER_OPTIONS,
            height=theme.CONTROL_HEIGHT,
            font=theme.font(13),
            command=self._on_provider_change,
        )
        self.provider_menu.grid(row=3, column=1, sticky="ew",
                                padx=(theme.GAP_LG, 0), pady=self.ROW_PAD_Y)

        self._sub_label(form, 3, "模型")
        self.model_menu = ComboBox(
            form,
            values=[MODEL_PLACEHOLDER],
            command=self._on_model_selected,
            readonly=True,
            height=theme.CONTROL_HEIGHT,
            font=theme.mono(12),
        )
        self.model_menu.grid(row=3, column=3, sticky="ew",
                             padx=(0, theme.GAP_LG), pady=self.ROW_PAD_Y)

        self._form_label(form, 4, "CC Switch")
        self.cc_switch_menu = ComboBox(
            form,
            values=[CC_SWITCH_PLACEHOLDER],
            readonly=True,
            height=theme.CONTROL_HEIGHT,
            font=theme.font(13),
            command=self._on_cc_switch_provider_selected,
        )
        self.cc_switch_menu.grid(row=4, column=1, sticky="ew",
                                 padx=(theme.GAP_LG, 0), pady=self.ROW_PAD_Y)
        # 必须先写入占位文字再禁用：disabled 的 Entry 会拒绝 insert，
        # 若先禁用再 set 会得到一个空白下拉框。
        self.cc_switch_menu.set(CC_SWITCH_PLACEHOLDER)
        self.cc_switch_menu.configure(state="disabled")

        self._sub_label(form, 4, "超时")
        timeout_box = ctk.CTkFrame(form, fg_color="transparent")
        timeout_box.grid(row=4, column=3, sticky="w",
                         padx=(0, theme.GAP_LG), pady=self.ROW_PAD_Y)
        self.timeout_entry = ctk.CTkEntry(
            timeout_box,
            placeholder_text="30",
            width=72,
            height=theme.CONTROL_HEIGHT,
            font=theme.mono(12),
            fg_color=theme.SURFACE_INPUT,
            border_color=theme.EDGE,
        )
        self.timeout_entry.pack(side="left")
        ctk.CTkLabel(
            timeout_box, text="秒", font=theme.mono(11), text_color=theme.TEXT_FAINT
        ).pack(side="left", padx=(theme.GAP_SM, 0))

        self._form_label(form, 5, "体检深度")
        audit_box = ctk.CTkFrame(form, fg_color="transparent")
        audit_box.grid(row=5, column=1, columnspan=3, sticky="ew",
                       padx=theme.GAP_LG, pady=(self.ROW_PAD_Y, theme.GAP_LG))
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
            font=theme.font(12),
            text_color=theme.TEXT_FAINT,
        ).pack(side="left", padx=theme.GAP_MD)

        # 操作按钮：主操作靠右，与阅读动线一致。两个彩色实心按钮在近黑底上最先被看到。
        actions = ctk.CTkFrame(self.content, fg_color="transparent")
        actions.pack(fill="x", padx=self.PAD_X, pady=(theme.GAP_XS, theme.GAP_MD))
        self.import_btn = ghost_button(
            actions, text="导入 CC Switch", width=140, command=self._on_import_cc_switch
        )
        self.import_btn.pack(side="left")
        self.fetch_btn = ghost_button(
            actions, text="获取模型", width=108, command=self._on_fetch_models
        )
        self.fetch_btn.pack(side="left", padx=theme.GAP_SM)
        self.test_btn = primary_button(
            actions, text="测试接口", width=136, command=self._on_test
        )
        self.test_btn.pack(side="right")
        self.audit_btn = signal_button(
            actions, text="模型体检", width=136, command=self._on_audit
        )
        self.audit_btn.pack(side="right", padx=theme.GAP_SM)

        # 忙碌指示条：常驻一条发丝线，空闲时就是分隔线，运行时才走进度。
        # 原实现靠 pack_forget 显隐，每次操作都会把下方内容顶动一次。
        self.progress_bar = ctk.CTkProgressBar(
            self.content,
            height=2,
            corner_radius=0,
            fg_color=theme.EDGE,
            progress_color=theme.SIGNAL,
            mode="determinate",
        )
        self.progress_bar.pack(fill="x", padx=self.PAD_X)
        self.progress_bar.set(0)

        # 结果区与日志区
        self.result_panel = ResultPanel(
            self.content, on_copy=lambda text: self._copy_to_clipboard(text, "cURL 命令")
        )
        self.result_panel.pack(fill="x", padx=self.PAD_X, pady=(theme.GAP_MD, 0))

        # 日志与体检报告页签；内部文本框按内容撑开，外层滚动条统一滚动，不再两层滚动条
        self.bottom_tabs = ctk.CTkTabview(
            self.content,
            fg_color=theme.PANEL,
            segmented_button_fg_color=theme.INK,
            segmented_button_selected_color=theme.NEUTRAL_FILL,
            segmented_button_selected_hover_color=theme.NEUTRAL_HOVER,
            segmented_button_unselected_color=theme.INK,
            segmented_button_unselected_hover_color=theme.CONTROL_HOVER,
            text_color=theme.TEXT_PRIMARY,
            anchor="w",
        )
        self.bottom_tabs.pack(fill="x", padx=self.PAD_X, pady=(theme.GAP_MD, theme.GAP_XL))

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
        if cfg.url_mode == "auto":
            self.url_mode_switch.deselect()
        else:
            self.url_mode_switch.select()
        self._update_url_mode_label()
        if cfg.model:
            self._models = [cfg.model]
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
        return "full" if self.url_mode_switch.get() else "auto"

    def _timeout(self) -> int:
        raw = self.timeout_entry.get().strip()
        try:
            return max(1, min(300, int(raw)))
        except ValueError:
            return 30

    def _audit_level(self) -> AuditLevel:
        return AUDIT_LEVEL_BY_LABEL.get(self.audit_level_menu.get(), "standard")

    def _collect_config(self) -> AppConfig:
        model = self.model_menu.get()
        if model == MODEL_PLACEHOLDER:
            model = ""
        return AppConfig(
            base_url=self.base_url_entry.get().strip(),
            api_key=self.api_key_entry.get().strip(),
            url_mode=self._url_mode(),
            provider=normalize_provider(self.provider_menu.get()),
            model=model,
            timeout=self._timeout(),
            theme_mode="dark",
            audit_level=self._audit_level(),
        )

    def _on_model_selected(self, choice: str) -> None:
        self._model_source = "手动选择"
        self._update_curl_preview()

    def _on_provider_change(self, choice: str) -> None:
        self._provider_source = "manual"
        self._selected_cc_switch_provider = None
        self._update_curl_preview()

    # ---------- 事件 ----------
    def _on_base_url_typing(self, event=None) -> None:
        if self._provider_source == "cc_switch":
            self._provider_source = "manual"
        self._selected_cc_switch_provider = None
        self._update_curl_preview()

    def _on_base_url_confirmed(self, event=None) -> None:
        self._select_provider_from_base_url()
        self._update_curl_preview()

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

    def _update_url_mode_label(self) -> None:
        # 开关文字就是当前状态说明，省掉一个只为解释开关而存在的独立标签
        if self.url_mode_switch.get():
            self.url_mode_switch.configure(text="完整地址，不拼接")
        else:
            self.url_mode_switch.configure(text="自动拼接接口后缀")
        self._update_curl_preview()

    def _toggle_key_visible(self) -> None:
        self._api_key_visible = not self._api_key_visible
        self.api_key_entry.configure(show="" if self._api_key_visible else "*")
        self.eye_btn.configure(image=self._eye_off_icon if self._api_key_visible else self._eye_icon)

    def _checker_log(self, message: str) -> None:
        self.logger.info(message)

    def _lock(self, locked: bool) -> None:
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
            # 常驻的发丝线切到不定长动画，布局不动，只有那条线活起来
            self.progress_bar.configure(mode="indeterminate")
            self.progress_bar.start()
        else:
            self.progress_bar.stop()
            self.progress_bar.configure(mode="determinate")
            self.progress_bar.set(0)

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
            self.cc_switch_menu.configure(values=[CC_SWITCH_PLACEHOLDER], state="disabled")
            self.cc_switch_menu.set(CC_SWITCH_PLACEHOLDER)
            self.logger.error(result.error_message)
            return

        self._cc_switch_providers = {
            provider.display_name: provider for provider in result.providers
        }
        labels = list(self._cc_switch_providers)
        self.cc_switch_menu.configure(values=labels, state="normal")
        self.cc_switch_menu.set(CC_SWITCH_PLACEHOLDER)
        self.logger.success(
            f"已从 CC Switch 导入 {len(labels)} 个可检测配置：{result.source_path}"
        )
        if result.skipped_count:
            self.logger.info(f"已跳过 {result.skipped_count} 个不完整或不支持的配置")
        if labels:
            first_label = labels[0]
            self.cc_switch_menu.set(first_label)
            self._load_cc_switch_provider(first_label, auto_fetch=True)

    def _on_cc_switch_provider_selected(self, choice: str) -> None:
        self._load_cc_switch_provider(choice, auto_fetch=True)

    def _load_cc_switch_provider(self, choice: str, *, auto_fetch: bool) -> None:
        provider = self._cc_switch_providers.get(choice)
        if provider is None:
            return

        self._selected_cc_switch_provider = provider
        self._set_entry(self.base_url_entry, provider.base_url)
        self._set_entry(self.api_key_entry, provider.api_key)
        if provider.url_mode == "auto":
            self.url_mode_switch.deselect()
        else:
            self.url_mode_switch.select()
        self._set_provider(provider.provider, source="cc_switch")
        self._models = [provider.model]
        self.model_menu.configure(values=self._models)
        self.model_menu.set(provider.model)
        self._model_source = f"CC Switch · {provider.display_name}"
        self.result_panel.set_model_count(1)
        self._update_url_mode_label()
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
            self.logger.error(err)
            return
        self._select_provider_from_base_url()
        self._lock(True)
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
            self._models = list(result.models)
            self.model_menu.configure(values=result.models)
            current = self.model_menu.get()
            if current not in result.models:
                self.model_menu.set(result.models[0])
                self._model_source = "模型列表首项"
            self.result_panel.set_model_count(len(result.models))
        else:
            self.result_panel.set_model_count(0)
            if result.error_message:
                self.logger.error(result.error_message)
        if result.latency_ms is not None:
            self.result_panel.set_latency(result.latency_ms)
        self._update_curl_preview()

    def _on_test(self) -> None:
        if self.is_processing:
            self.logger.warning("正在处理中，请稍候...")
            return
        err = self._validate_inputs()
        if err:
            self.logger.error(err)
            return
        self._select_provider_from_base_url()
        self._lock(True)
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

    # ---------- 模型体检 ----------
    def _on_audit(self) -> None:
        if self.is_processing:
            self.logger.warning("正在处理中，请稍候...")
            return
        err = self._validate_inputs()
        if err:
            self.logger.error(err)
            return
        if not self._selected_model():
            self.logger.error("请先选择或填写模型")
            return
        self._select_provider_from_base_url()
        self.bottom_tabs.set("模型体检")
        self.audit_panel.reset()
        self._lock(True)
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
        model = self.model_menu.get().strip()
        return "" if model == MODEL_PLACEHOLDER else model

    def _current_models(self) -> list[str]:
        return self._models

    def _validate_inputs(self) -> str | None:
        """Validate form inputs before making API calls. Returns error message or None."""
        base_url = self.base_url_entry.get().strip()
        if not base_url:
            return "接口地址不能为空"
        if "://" not in base_url:
            return "接口地址格式不正确，请包含协议（如 https://）"

        timeout = self.timeout_entry.get().strip()
        if timeout:
            try:
                val = int(timeout)
                if val < 1 or val > 300:
                    return "超时时间必须在 1～300 秒之间"
            except ValueError:
                return "超时时间必须为数字"

        if not self.api_key_entry.get().strip():
            return "密钥不能为空"

        return None

    # ---------- cURL 预览 ----------
    def _update_curl_preview(self) -> None:
        base_url = self.base_url_entry.get().strip()
        provider = self.provider_menu.get()
        api_key = mask_api_key(self.api_key_entry.get())
        model = self.model_menu.get()
        if model == MODEL_PLACEHOLDER:
            model = "<模型>"

        if not base_url:
            self.result_panel.set_curl("")
            return

        url = pick_test_url(provider, base_url, model, self._url_mode())
        auth_header = self._curl_auth_header(provider, api_key)
        body = build_request_body(provider, model)

        curl = (
            f"curl -X POST {url} \\\n"
            f'  {auth_header} \\\n'
            f'  -H "Content-Type: application/json" \\\n'
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