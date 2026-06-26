# LLM Key Checker

Python 桌面版 LLM Key Checker，用于检测 OpenAI Compatible、Anthropic/Claude 等接口的可用性。

## 功能

- `baseURL` 输入，开关打开时由工具自动拼接接口后缀。
- 开关关闭时不拼接路径，直接请求用户输入的完整接口地址。
- Provider 下拉列表支持 `OpenAl Responses`、`OpenAl Compatible`、`Anthropic`、`Amazon Bedrock`、`Google (Gemini)`。
- 获取模型列表、测试模型调用、查看延迟和模型数量。
- cURL 请求预览，API Key 始终脱敏。
- 实时日志，支持清空、复制、保存。
- 保存配置到 `config.json`，会保存 API Key 并在启动时自动回填。

## 安装

建议使用 Python 3.12。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

## 启动

```powershell
python main.py
```

## URL 模式

开关打开是「自动拼接接口后缀」：

- OpenAI Compatible 获取模型：`{baseURL}/models`
- OpenAI Compatible 测试接口：`{baseURL}/chat/completions`
- Anthropic 获取模型：`{baseURL}/v1/models`
- Anthropic 测试接口：`{baseURL}/v1/messages`

OpenAI Compatible 格式通常需要用户输入带 `/v1` 的地址，例如：

```text
https://api.openai.com/v1
```

工具会拼接出：

```text
https://api.openai.com/v1/chat/completions
```

开关关闭时，工具不拼接路径，直接请求 `baseURL` 输入框里的完整地址。

## 打包 exe

```powershell
pip install pyinstaller
pyinstaller --noconfirm --onefile --windowed --name LLM-Key-Checker main.py
```

生成文件位于 `dist\LLM-Key-Checker.exe`。

## 安全说明

- 日志和 cURL 预览仍会脱敏显示 API Key。
- `config.json` 会保存 `base_url`、`api_key`、`url_mode`、`provider`、`model`、`timeout` 和主题设置。
