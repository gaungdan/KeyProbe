"""KeyProbe 主窗口：CustomTkinter 实现，复用原有 async 后端逻辑。"""

import asyncio
import json
import threading
from pathlib import Path

import customtkinter as ctk
from PIL import Image

from app.checker.client import LLMChecker
from app.models.check import CheckStatus
from app.models.config import AppConfig, UrlMode
from app.services.cc_switch import CCSwitchImportResult, CCSwitchProvider, load_cc_switch_providers
from app.services.config_store import load_config, save_config
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
from app.services.url_builder import (
    anthropic_test_urls,
    bedrock_test_urls,
    gemini_test_urls,
    openai_test_urls,
    responses_test_urls,
)
from app.ui.log_panel import LogPanel
from app.ui.result_panel import ResultPanel
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
        self.is_processing = False
        self._models: list[str] = []
        self._cc_switch_providers: dict[str, CCSwitchProvider] = {}
        self._selected_cc_switch_provider: CCSwitchProvider | None = None
        self._provider_source = "auto"
        self._suppress_provider_change = False

        self._setup_window()
        self._create_widgets()
        self._apply_config()
        self.logger.set_callback(
            lambda line: self.after(0, self.log_panel.log, line)
        )

    _ASSETS = Path(__file__).resolve().parent / "assets"

    @staticmethod
    def _load_icon(name: str, size: int = 20) -> ctk.CTkImage:
        base = KeyProbeApp._ASSETS
        light = Image.open(base / f"{name}-light.png").resize((size, size), Image.LANCZOS)
        dark = Image.open(base / f"{name}.png").resize((size, size), Image.LANCZOS)
        return ctk.CTkImage(light_image=light, dark_image=dark, size=(size, size))

    # ---------- 窗口与组件 ----------
    def _setup_window(self) -> None:
        self.title("KeyProbe - LLM Key Checker")
        self.geometry("1000x880")
        self.minsize(900, 760)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

    def _create_widgets(self) -> None:
        # 标题栏
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(15, 5))
        ctk.CTkLabel(
            header,
            text="KeyProbe - LLM Key Checker",
            font=("Helvetica", 24, "bold"),
        ).pack()

        # 配置表单
        form = ctk.CTkFrame(self)
        form.pack(fill="x", padx=20, pady=10)
        form.grid_columnconfigure(1, weight=1)

        self.base_url_entry = self._form_entry(form, 0, "接口地址", "https://api.openai.com/v1")
        self.base_url_entry.bind("<FocusOut>", self._on_base_url_confirmed)
        self.base_url_entry.bind("<Return>", self._on_base_url_confirmed)
        self.base_url_entry.bind("<KeyRelease>", self._on_base_url_typing)

        # URL 模式开关
        ctk.CTkLabel(form, text="URL 模式", font=("Helvetica", 14)).grid(
            row=1, column=0, sticky="w", padx=15, pady=8
        )
        switch_box = ctk.CTkFrame(form, fg_color="transparent")
        switch_box.grid(row=1, column=1, sticky="w", padx=15, pady=8)
        self.url_mode_switch = ctk.CTkSwitch(
            switch_box, text="", command=self._update_url_mode_label, width=46
        )
        self.url_mode_switch.pack(side="left")
        self.url_mode_label = ctk.CTkLabel(switch_box, text="", font=("Helvetica", 13))
        self.url_mode_label.pack(side="left", padx=10)

        ctk.CTkLabel(form, text="密钥", font=("Helvetica", 14)).grid(
            row=2, column=0, sticky="w", padx=15, pady=8
        )
        key_frame = ctk.CTkFrame(form, fg_color="transparent")
        key_frame.grid(row=2, column=1, sticky="ew", padx=15, pady=8)
        key_frame.grid_columnconfigure(0, weight=1)
        self.api_key_entry = ctk.CTkEntry(key_frame, placeholder_text="sk-...", height=38, show="*")
        self.api_key_entry.grid(row=0, column=0, sticky="ew")
        self._api_key_visible = False
        self._eye_icon = self._load_icon("eye")
        self._eye_off_icon = self._load_icon("eye-off")
        self.eye_btn = ctk.CTkButton(
            key_frame, image=self._eye_icon, text="", width=38, height=38,
            command=self._toggle_key_visible,
        )
        self.eye_btn.grid(row=0, column=1, padx=(6, 0))

        # CC Switch 导入
        ctk.CTkLabel(form, text="CC Switch 配置", font=("Helvetica", 14)).grid(
            row=3, column=0, sticky="w", padx=15, pady=8
        )
        self.cc_switch_menu = ctk.CTkComboBox(
            form,
            values=[CC_SWITCH_PLACEHOLDER],
            state="disabled",
            command=self._on_cc_switch_provider_selected,
        )
        self.cc_switch_menu.grid(row=3, column=1, sticky="ew", padx=15, pady=8)
        self.cc_switch_menu.set(CC_SWITCH_PLACEHOLDER)

        # 服务商/协议
        ctk.CTkLabel(form, text="服务商/协议", font=("Helvetica", 14)).grid(
            row=4, column=0, sticky="w", padx=15, pady=8
        )
        self.provider_menu = ctk.CTkOptionMenu(
            form, values=PROVIDER_OPTIONS, command=self._on_provider_change
        )
        self.provider_menu.grid(row=4, column=1, sticky="ew", padx=15, pady=8)

        self.timeout_entry = self._form_entry(form, 5, "超时时间（秒）", "30")

        # 模型
        ctk.CTkLabel(form, text="模型", font=("Helvetica", 14)).grid(
            row=6, column=0, sticky="w", padx=15, pady=8
        )
        self.model_menu = ctk.CTkComboBox(form, values=[MODEL_PLACEHOLDER])
        self.model_menu.grid(row=6, column=1, sticky="ew", padx=15, pady=8)

        # 操作按钮
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=20, pady=(0, 6))
        self.import_btn = ctk.CTkButton(
            actions, text="导入 CC Switch 配置", height=40, command=self._on_import_cc_switch
        )
        self.import_btn.pack(side="left", padx=5)
        self.fetch_btn = ctk.CTkButton(
            actions, text="获取模型", height=40, command=self._on_fetch_models
        )
        self.fetch_btn.pack(side="left", padx=5)
        self.test_btn = ctk.CTkButton(
            actions,
            text="测试接口",
            height=40,
            fg_color="#ff6b00",
            hover_color="#e56000",
            command=self._on_test,
        )
        self.test_btn.pack(side="left", padx=5)
        self.save_btn = ctk.CTkButton(
            actions,
            text="保存配置",
            height=40,
            fg_color="#374151",
            hover_color="#1f2937",
            command=self._on_save,
        )
        self.save_btn.pack(side="left", padx=5)

        # 结果区与日志区
        self.result_panel = ResultPanel(self)
        self.result_panel.pack(fill="x", padx=20, pady=6)

        self.log_panel = LogPanel(
            self, on_clear=self.logger.clear
        )
        self.log_panel.pack(fill="both", expand=True, padx=20, pady=(6, 15))

    def _form_entry(self, master, row: int, label: str, placeholder: str, show: str | None = None):
        ctk.CTkLabel(master, text=label, font=("Helvetica", 14)).grid(
            row=row, column=0, sticky="w", padx=15, pady=8
        )
        kwargs = {"placeholder_text": placeholder, "height": 38}
        if show:
            kwargs["show"] = show
        entry = ctk.CTkEntry(master, **kwargs)
        entry.grid(row=row, column=1, sticky="ew", padx=15, pady=8)
        return entry

    # ---------- 配置回填 ----------
    def _apply_config(self) -> None:
        cfg = self.config_data
        self._set_entry(self.base_url_entry, cfg.base_url)
        self._set_entry(self.api_key_entry, cfg.api_key)
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
        )

    def _on_provider_change(self, choice: str) -> None:
        if self._suppress_provider_change:
            return
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
        self._suppress_provider_change = True
        try:
            self.provider_menu.set(provider)
        finally:
            self._suppress_provider_change = False
        self._provider_source = source

    def _update_url_mode_label(self) -> None:
        if self.url_mode_switch.get():
            self.url_mode_label.configure(text="使用完整地址（不拼接）")
        else:
            self.url_mode_label.configure(text="自动拼接接口后缀")
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
        self.cc_switch_menu.configure(
            state="disabled" if locked or not self._cc_switch_providers else "normal"
        )

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
        except Exception as exc:  # noqa: BLE001
            self.logger.error(f"导入 CC Switch 配置失败：{exc}")
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
        self.result_panel.set_model_count(1)
        self._update_url_mode_label()
        self._update_curl_preview()
        self.logger.info(
            f"已加载 CC Switch 配置：{provider.display_name}，默认模型：{provider.model}"
        )
        if auto_fetch:
            self._queue_fetch_models()

    def _queue_fetch_models(self) -> None:
        self.after(100, self._fetch_models_when_idle)

    def _fetch_models_when_idle(self) -> None:
        if self.is_processing:
            self._queue_fetch_models()
            return
        self._on_fetch_models()

    def _on_fetch_models(self) -> None:
        if self.is_processing:
            self.logger.warning("正在处理中，请稍候...")
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
        except Exception as exc:  # noqa: BLE001
            self.logger.error(f"异常: {exc}")
        finally:
            self.after(0, lambda: self._lock(False))

    def _apply_models_result(self, result) -> None:
        self.result_panel.set_provider(result.provider)
        if result.models:
            self._models = list(result.models)
            self.model_menu.configure(values=result.models)
            self.model_menu.set(result.models[0])
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
        except Exception as exc:  # noqa: BLE001
            self.logger.error(f"异常: {exc}")
        finally:
            self.after(0, lambda: self._lock(False))

    def _apply_test_result(self, result) -> None:
        self.result_panel.set_status(result.status)
        self.result_panel.set_latency(result.latency_ms)
        self.result_panel.set_provider(result.provider)
        if result.error_message:
            self.logger.error(result.error_message)

    def _current_models(self) -> list[str]:
        return self._models

    def _on_save(self) -> None:
        self._select_provider_from_base_url()
        cfg = self._collect_config()
        try:
            save_config(cfg)
            self.config_data = cfg
            self.logger.success("配置已保存到 config.json")
        except Exception as exc:  # noqa: BLE001
            self.logger.error(f"保存失败: {exc}")

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

        if self._url_mode() == "full":
            url = base_url
        elif is_anthropic_provider(provider):
            url = anthropic_test_urls(base_url)[0]
        elif provider == AMAZON_BEDROCK:
            url = bedrock_test_urls(base_url, model)[0]
        elif provider == GOOGLE_GEMINI:
            url = gemini_test_urls(base_url, model)[0]
        elif provider == OPENAI_RESPONSES:
            url = responses_test_urls(base_url)[0]
        else:
            url = openai_test_urls(base_url)[0]

        if is_anthropic_provider(provider):
            auth = f'-H "x-api-key: {api_key}" -H "anthropic-version: 2023-06-01"'
            body = {"model": model, "max_tokens": 16, "messages": [{"role": "user", "content": "hello"}]}
        elif provider == GOOGLE_GEMINI:
            auth = f'-H "x-goog-api-key: {api_key}"'
            body = {"contents": [{"role": "user", "parts": [{"text": "hello"}]}], "generationConfig": {"maxOutputTokens": 16, "temperature": 0}}
        elif provider == AMAZON_BEDROCK:
            auth = f'-H "Authorization: Bearer {api_key}"'
            body = {"messages": [{"role": "user", "content": [{"text": "hello"}]}], "inferenceConfig": {"maxTokens": 16, "temperature": 0}}
        elif provider == OPENAI_RESPONSES:
            auth = f'-H "Authorization: Bearer {api_key}"'
            body = {"model": model, "input": "hello", "max_output_tokens": 16, "temperature": 0}
        else:
            auth = f'-H "Authorization: Bearer {api_key}"'
            body = {"model": model, "messages": [{"role": "user", "content": "hello"}], "max_tokens": 16}

        curl = (
            f"curl -X POST {url} \\\n"
            f'  {auth} \\\n'
            f'  -H "Content-Type: application/json" \\\n'
            f"  -d '{json.dumps(body, ensure_ascii=False)}'"
        )
        self.result_panel.set_curl(curl)
