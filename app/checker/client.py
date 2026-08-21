"""Async HTTP checker for LLM provider connectivity."""

import json
from collections.abc import Callable
from time import perf_counter
from typing import Any
from urllib.parse import urlparse

import httpx

from app.models.check import CheckResult, CheckStatus, ModelFetchResult
from app.models.config import UrlMode
from app.services.providers import (
    AMAZON_BEDROCK,
    ANTHROPIC,
    ANTHROPIC_DEFAULT_MODELS,
    GOOGLE_GEMINI,
    OPENAI_COMPATIBLE,
    OPENAI_RESPONSES,
    is_anthropic_provider,
    normalize_provider,
)
from app.services.url_builder import (
    anthropic_model_urls,
    anthropic_test_urls,
    bedrock_model_urls,
    bedrock_test_urls,
    build_request_body,
    full_url_model_urls,
    gemini_model_urls,
    gemini_test_urls,
    openai_model_urls,
    openai_test_urls,
    responses_model_urls,
    responses_test_urls,
)
from app.services.bedrock_auth import parse_aws_credentials, sign_bedrock_request

LogCallback = Callable[[str], None]


class LLMChecker:
    """Check model listing and chat/message calls for providers."""

    def __init__(
        self, log: LogCallback | None = None, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._log = log or (lambda message: None)
        self._transport = transport

    async def close(self) -> None:
        """No-op, kept for API compatibility.

        The checker no longer keeps a shared AsyncClient. Every operation opens
        a fresh client inside its own event-loop run and closes it before the
        loop is torn down. Sharing one client across ``asyncio.run()`` calls
        would bind its connection pool to an already-closed loop, causing
        ``RuntimeError: Event loop is closed`` (httpcore/anyio cleanup).
        """

    async def fetch_models(
        self,
        *,
        provider: str,
        api_key: str,
        timeout: int,
        url_mode: UrlMode,
        base_url: str,
        models_url: str = "",
    ) -> ModelFetchResult:
        """Fetch models from the provider."""

        if not api_key.strip():
            return ModelFetchResult(
                provider=provider,
                error_message="API Key 不能为空",
            )

        effective_provider = self._effective_provider(provider, base_url)
        urls = [models_url.strip()] if models_url.strip() else (
            full_url_model_urls(base_url)
            if url_mode == "full"
            else self._model_urls(effective_provider, base_url)
        )
        if not urls or not urls[0]:
            return ModelFetchResult(provider=effective_provider, error_message="模型列表 URL 不能为空")

        self._log(f"模型列表候选地址（{len(urls)} 个）：{' | '.join(urls)}")
        result = await self._do_fetch_models(effective_provider, api_key, timeout, url_mode, urls)

        if not result.models and is_anthropic_provider(effective_provider):
            self._log("Anthropic 鉴权获取模型失败，尝试 OpenAI 兼容鉴权...")
            oai_urls = openai_model_urls(base_url) if url_mode == "auto" else urls
            oai_result = await self._do_fetch_models(
                OPENAI_COMPATIBLE, api_key, timeout, url_mode, oai_urls
            )
            if oai_result.models:
                oai_result.provider = effective_provider
                return oai_result
            self._log("两种鉴权均失败，使用 Anthropic 默认模型")
            return ModelFetchResult(
                provider=effective_provider,
                models=ANTHROPIC_DEFAULT_MODELS,
                request_url=urls[0],
                error_message=result.error_message or oai_result.error_message,
                raw={},
            )

        return result

    async def _do_fetch_models(
        self,
        provider: str,
        api_key: str,
        timeout: int,
        url_mode: UrlMode,
        urls: list[str],
    ) -> ModelFetchResult:
        last_error = ""
        # 每次调用使用独立的 AsyncClient，并在同一事件循环内关闭。GUI 在后台线程里用
        # asyncio.run() 调用这里，每次运行都会新建并销毁事件循环；若跨循环复用共享
        # client，httpx/httpcore 清理旧连接时会抛出 "Event loop is closed"。
        async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
            for url in urls:
                headers = self._headers(provider, api_key, "GET", url)
                self._log(f"请求 GET {url}")
                started = perf_counter()
                try:
                    response = await client.get(url, headers=headers)
                except httpx.RequestError as exc:
                    last_error = str(exc)
                    self._log(f"网络错误：{last_error}")
                    continue

                latency_ms = int((perf_counter() - started) * 1000)
                if response.status_code in {404, 405, 501} and url_mode == "auto" and len(urls) > 1:
                    last_error = f"接口不存在 ({response.status_code})，尝试备用地址"
                    self._log(last_error)
                    continue
                if response.status_code in {401, 403}:
                    last_error = self._extract_error(response)
                    self._log(f"鉴权失败：HTTP {response.status_code} {last_error}")
                    return ModelFetchResult(
                        provider=provider,
                        request_url=url,
                        latency_ms=latency_ms,
                        error_message=last_error,
                        raw=self._safe_json(response) or response.text[:2000],
                    )
                if response.status_code >= 400:
                    last_error = self._extract_error(response)
                    self._log(f"获取模型失败：HTTP {response.status_code} {last_error}，尝试备用地址")
                    continue

                models = self._parse_models(response, provider)
                self._log(f"获取模型成功，共发现 {len(models)} 个模型")
                return ModelFetchResult(
                    provider=provider,
                    models=models,
                    request_url=url,
                    latency_ms=latency_ms,
                    raw=self._safe_json(response),
                )

            return ModelFetchResult(
                provider=provider,
                request_url=urls[-1] if urls else "",
                error_message=last_error or "未获取到模型列表",
                raw={},
            )

    async def test_model(
        self,
        *,
        provider: str,
        api_key: str,
        model: str,
        timeout: int,
        url_mode: UrlMode,
        base_url: str,
        test_url: str = "",
        model_count: int = 0,
    ) -> CheckResult:
        """Call a minimal model request and classify the result."""

        if not api_key.strip():
            return CheckResult(
                status=CheckStatus.INVALID_KEY,
                provider=provider,
                selected_model=model,
                model_count=model_count,
                error_message="API Key 不能为空",
            )
        if not model.strip():
            return CheckResult(
                status=CheckStatus.UNKNOWN,
                provider=provider,
                selected_model=model,
                model_count=model_count,
                error_message="请先选择或填写模型",
            )

        effective_provider = self._effective_provider(provider, base_url)
        urls = (
            [test_url.strip() or base_url.strip()]
            if url_mode == "full"
            else self._test_urls(effective_provider, base_url, model)
        )
        if not urls or not urls[0]:
            return CheckResult(
                status=CheckStatus.UNKNOWN,
                provider=effective_provider,
                selected_model=model,
                model_count=model_count,
                error_message="测试接口 URL 不能为空",
            )

        result = await self._do_test_model(effective_provider, api_key, model, timeout, url_mode, urls, model_count)

        if result.status in {CheckStatus.INVALID_KEY, CheckStatus.NETWORK_ERROR, CheckStatus.UNKNOWN} \
                and is_anthropic_provider(effective_provider):
            if result.status == CheckStatus.INVALID_KEY:
                self._log("Anthropic 鉴权测试失败，尝试 OpenAI 兼容鉴权并保留 Anthropic 请求格式...")
                fallback_urls = urls
                fallback = await self._do_test_model(
                    effective_provider,
                    api_key,
                    model,
                    timeout,
                    url_mode,
                    fallback_urls,
                    model_count,
                    auth_provider=OPENAI_COMPATIBLE,
                )
            else:
                self._log("Anthropic 接口测试失败，尝试 OpenAI 兼容接口...")
                fallback_urls = openai_test_urls(base_url) if url_mode == "auto" else urls
                fallback = await self._do_test_model(
                    OPENAI_COMPATIBLE, api_key, model, timeout, url_mode, fallback_urls, model_count
                )
            if fallback.status != CheckStatus.INVALID_KEY or fallback.latency_ms is not None:
                fallback.provider = effective_provider
                return fallback

        return result

    async def _do_test_model(
        self,
        provider: str,
        api_key: str,
        model: str,
        timeout: int,
        url_mode: UrlMode,
        urls: list[str],
        model_count: int,
        auth_provider: str | None = None,
    ) -> CheckResult:
        body = self._body(provider, model)
        header_provider = auth_provider or provider
        last_result: CheckResult | None = None

        # 与 _do_fetch_models 同理：独立 AsyncClient，随事件循环创建/销毁，避免
        # 跨 asyncio.run() 复用连接池导致 "Event loop is closed"。
        async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
            for url in urls:
                headers = self._headers(header_provider, api_key, "POST", url, body)
                self._log(f"请求 POST {url}")
                started = perf_counter()
                try:
                    response = await client.post(url, headers=headers, json=body)
                except httpx.RequestError as exc:
                    last_result = CheckResult(
                        status=CheckStatus.NETWORK_ERROR,
                        provider=provider,
                        selected_model=model,
                        model_count=model_count,
                        request_url=url,
                        error_message=str(exc),
                    )
                    self._log(f"网络错误：{exc}")
                    continue

                latency_ms = int((perf_counter() - started) * 1000)
                if response.status_code == 404 and url_mode == "auto":
                    self._log("接口不存在，尝试备用地址")
                    last_result = CheckResult(
                        status=CheckStatus.UNKNOWN,
                        provider=provider,
                        latency_ms=latency_ms,
                        model_count=model_count,
                        selected_model=model,
                        request_url=url,
                        error_message="接口不存在",
                        raw=self._safe_json(response) or response.text[:2000],
                    )
                    continue

                if 200 <= response.status_code < 300:
                    raw = self._safe_json(response)
                    self._log(f"检测完成，延迟 {latency_ms}ms")
                    self._log(json.dumps(raw, ensure_ascii=False, indent=2))
                    return CheckResult(
                        status=CheckStatus.VALID,
                        provider=provider,
                        latency_ms=latency_ms,
                        model_count=model_count,
                        selected_model=model,
                        request_url=url,
                        raw=raw,
                    )

                status = self._classify_error(response)
                error_message = self._extract_error(response)
                raw = self._safe_json(response) or response.text[:2000]
                self._log(f"检测失败：HTTP {response.status_code} {error_message}")
                self._log(json.dumps(raw, ensure_ascii=False, indent=2) if isinstance(raw, dict) else str(raw))
                return CheckResult(
                    status=status,
                    provider=provider,
                    latency_ms=latency_ms,
                    model_count=model_count,
                    selected_model=model,
                    request_url=url,
                    error_message=error_message,
                    raw=self._safe_json(response) or response.text[:2000],
                )

            return last_result or CheckResult(
                status=CheckStatus.UNKNOWN,
                provider=provider,
                selected_model=model,
                model_count=model_count,
                error_message="检测失败",
            )

    def _model_urls(self, provider: str, base_url: str) -> list[str]:
        if is_anthropic_provider(provider):
            return anthropic_model_urls(base_url)
        if provider == AMAZON_BEDROCK:
            return bedrock_model_urls(base_url)
        if provider == GOOGLE_GEMINI:
            return gemini_model_urls(base_url)
        if provider == OPENAI_RESPONSES:
            return responses_model_urls(base_url)
        return openai_model_urls(base_url)

    def _test_urls(self, provider: str, base_url: str, model: str = "") -> list[str]:
        if is_anthropic_provider(provider):
            return anthropic_test_urls(base_url)
        if provider == AMAZON_BEDROCK:
            return bedrock_test_urls(base_url, model)
        if provider == GOOGLE_GEMINI:
            return gemini_test_urls(base_url, model)
        if provider == OPENAI_RESPONSES:
            return responses_test_urls(base_url)
        return openai_test_urls(base_url)

    def _effective_provider(self, provider: str, base_url: str) -> str:
        provider = normalize_provider(provider)
        if is_anthropic_provider(provider) or self._base_url_uses_anthropic_route(base_url):
            return ANTHROPIC
        return provider

    @staticmethod
    def _base_url_uses_anthropic_route(base_url: str) -> bool:
        path = urlparse(base_url.strip()).path.lower()
        parts = [part for part in path.split("/") if part]
        return any(part in {"anthropic", "claude"} for part in parts)

    def _headers(
        self, provider: str, api_key: str, method: str = "GET", url: str = "", body: dict[str, Any] | None = None
    ) -> dict[str, str]:
        if is_anthropic_provider(provider):
            return {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
        if provider == GOOGLE_GEMINI:
            return {"x-goog-api-key": api_key, "Content-Type": "application/json"}
        if provider == AMAZON_BEDROCK:
            credentials = parse_aws_credentials(api_key)
            if credentials:
                payload = json.dumps(body, separators=(",", ":")).encode("utf-8") if body else b""
                return {"Content-Type": "application/json", **sign_bedrock_request(
                    credentials=credentials, method=method, url=url, payload=payload
                )}
            return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _body(self, provider: str, model: str) -> dict[str, Any]:
        return build_request_body(provider, model)

    def _parse_models(self, response: httpx.Response, provider: str = "") -> list[str]:
        payload = self._safe_json(response)
        models: list[str] = []

        if isinstance(payload, dict):
            # standard OpenAI: {"data": [{"id": "m1"}, ...]}
            data = payload.get("data")
            if isinstance(data, list):
                self._extract_model_ids(data, models)
            # some proxies: {"models": [{"id": "m1"}, ...]}
            # or: {"models": ["m1", "m2"]}
            entries = payload.get("models")
            if isinstance(entries, list):
                self._extract_model_ids(entries, models)
            summaries = payload.get("modelSummaries")
            if isinstance(summaries, list):
                self._extract_model_ids(summaries, models)
        elif isinstance(payload, list):
            self._extract_model_ids(payload, models)

        if provider == GOOGLE_GEMINI:
            models = [model.removeprefix("models/") for model in models]
        return sorted(set(models))

    @staticmethod
    def _extract_model_ids(items: list[Any], out: list[str]) -> None:
        for item in items:
            if isinstance(item, dict):
                for key in ("id", "name", "model", "modelId"):
                    value = item.get(key)
                    if value and str(value).strip():
                        out.append(str(value).strip())
                        break
            elif isinstance(item, str) and item.strip():
                out.append(item.strip())

    def _classify_error(self, response: httpx.Response) -> CheckStatus:
        if response.status_code == 429:
            return CheckStatus.RATE_LIMITED
        if response.status_code in {401, 403}:
            return CheckStatus.INVALID_KEY
        if response.status_code >= 500:
            return CheckStatus.SERVER_ERROR
        if response.status_code in {402, 409} or any(
            keyword in self._extract_error(response).lower()
            for keyword in ("insufficient", "balance", "quota", "credit")
        ):
            return CheckStatus.INSUFFICIENT_BALANCE
        return CheckStatus.UNKNOWN

    def _extract_error(self, response: httpx.Response) -> str:
        payload = self._safe_json(response)
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                return str(error.get("message") or error.get("type") or response.text)
            if isinstance(error, str):
                return error
            if payload.get("message"):
                return str(payload["message"])
        return response.text[:300] or response.reason_phrase

    def _safe_json(self, response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return {}
