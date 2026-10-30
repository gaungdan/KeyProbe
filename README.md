# KeyProbe

LLM 接口连通性测试工具，桌面版基于 Python + CustomTkinter，用于检测 OpenAI 兼容、Anthropic/Claude 等接口的 API Key 可用性、模型列表与调用延迟。

仓库内同时提供一份纯前端单文件版本 [KeyProbe.html](KeyProbe.html)，浏览器直接打开即可使用。

## 功能

- **接口地址**输入，`URL 模式` 开关控制是否自动拼接接口后缀。
  - 开关打开（`auto`）：工具按 Provider 自动拼接 `/v1/models`、`/chat/completions`、`/v1/messages` 等路径，并对 `/v1/v1` 重复斜杠做归一化。
  - 开关关闭（`full`）：不拼接路径，直接请求输入框里的完整地址。
- **服务商/协议**下拉，支持 `OpenAI 响应接口`、`OpenAI 兼容接口`、`Anthropic Claude 协议`、`亚马逊 Bedrock`、`谷歌 Gemini`；输入 `baseURL` 后自动识别并回填。
- **获取模型**：拉取模型列表，兼容 `data` / `models` / 字符串数组等多种返回格式；模型数自动统计。
- **测试接口**：发起最小化调用，分类返回 `有效 / 密钥无效 / 请求受限 / 余额不足 / 网络错误 / 服务错误 / 未知`，并显示延迟。
- **模型体检**：在「测试接口」之上进一步核查四件事——系统配置的是什么模型、服务端实际调用的是什么模型、模型是否具备预期能力、调用参数是否被网关异常修改，结果按「模型身份 / 调用参数 / 模型能力」三个维度逐项给出「通过 / 可疑 / 异常 / 跳过」结论并可一键复制报告。
- **Anthropic 回退**：当 Anthropic 鉴权失败时，自动尝试 OpenAI 兼容鉴权；模型列表仍获取失败时回退到内置默认模型。
- **cURL 请求预览**：实时生成等效 cURL 命令，API Key 始终脱敏。
- **实时日志**：带时间戳与级别，支持清空。
- **配置读取**：启动时从 `config.json` 自动回填 `base_url`、`url_mode`、`provider`、`model`、`timeout`、`theme_mode`、`audit_level`（需手动编辑该文件；界面不提供保存功能，API Key 永不落盘）。

## 项目结构

```text
main.py                    # 入口
app/
  checker/client.py        # 异步检测核心逻辑（httpx）
  checker/auditor.py        # 模型体检：身份 / 参数篡改 / 能力探针
  models/                  # pydantic 配置与结果模型
  services/
    providers.py           # Provider 识别与归一化
    url_builder.py         # URL 拼接与备用地址生成
    config_store.py        # config.json 读取
    logger_service.py      # 日志服务
    auth_headers.py        # 鉴权头构造（checker 与 auditor 共用）
    protocol.py            # 五种协议的请求体/响应解析适配
    model_identity.py      # 模型名归一化与族系识别
  ui/                      # CustomTkinter 界面（主窗口、结果面板、体检面板、日志面板）
  utils/security.py        # API Key 脱敏
tests/test_core.py         # 核心逻辑单测
tests/test_audit.py        # 模型体检单测
build.bat                  # Windows 打包脚本
KeyProbe.html              # 纯前端单文件版本
tools/browser-mcp/         # DSH Agent 浏览器操作能力接入（开发辅助，非运行时依赖）
```

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

## 运行测试

```powershell
pytest
```

## URL 模式说明

开关打开（`auto`，自动拼接接口后缀）：

- OpenAI 兼容 获取模型：`{baseURL}/v1/models`（失败回退 `{baseURL}/models`）
- OpenAI 兼容 测试接口：`{baseURL}/chat/completions`
- Anthropic 获取模型：`{baseURL}/v1/models` → `{baseURL}/models` → 去除 `/anthropic`、`/claude` 后缀再试
- Anthropic 测试接口：`{baseURL}/v1/messages`

OpenAI 兼容格式通常需要输入带 `/v1` 的地址，例如：

```text
https://api.openai.com/v1
```

工具会拼接出：

```text
https://api.openai.com/v1/chat/completions
```

不同 Provider 会使用各自的原生协议，而不是统一伪装成 Chat Completions：

- OpenAI Responses：`POST /responses`，请求体使用 `input` 与 `max_output_tokens`。
- Gemini：`GET /v1beta/models`、`POST /v1beta/models/{model}:generateContent`，使用 `x-goog-api-key`。
- Bedrock：`GET /foundation-models`、`POST /model/{model}/invoke`，请求体使用 Bedrock Messages 格式。

Bedrock 的 API Key 输入框可填 Bedrock API Key（按 Bearer 认证）；若直接访问 AWS Runtime，也可填写
`accessKeyId|secretAccessKey|sessionToken`（session token 可省略），工具会生成 AWS SigV4 签名。

开关关闭（`full`）时，工具不拼接路径，直接请求 `baseURL` 输入框里的完整地址，适合自定义或代理接口。

## 打包 exe

方式一：使用打包脚本（推荐，会自动检查环境与依赖）。

```powershell
.\build.bat
```

方式二：手动调用 PyInstaller。

```powershell
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
pyinstaller --noconfirm --clean --onefile --windowed --name KeyProbe --distpath ./dist --workpath ./build --specpath ./build main.py
```

`--clean` 用于清除 PyInstaller 缓存并移除临时文件，避免命中旧的构建缓存导致产物未更新。`requirements.txt` 已包含 `pyinstaller`，无需单独安装。

生成文件位于 `dist\KeyProbe.exe`。`config.json` 不打包进 exe，程序启动时若不存在则使用默认配置并在运行时按需写入。

## 自动构建与发行版

由于 PyInstaller 无法在 Linux 上交叉编译 Windows exe，而 Gitee 免费版（Gitee Go）仅提供 Linux 运行机，本仓库采用 **GitHub Actions 构建 + 回传 Gitee Release** 方案：

1. 在 Gitee 上打 `v*` tag / 发布发行版。
2. 通过 Gitee「仓库镜像」把 tag 同步到 GitHub 镜像仓库。
3. GitHub Actions 在 `windows-latest` 上拉起 PyInstaller 打包（见 [.github/workflows/build-exe.yml](.github/workflows/build-exe.yml)）。
4. 工作流调 Gitee API 把 `dist/KeyProbe.exe` 上传回对应 tag 的 Gitee 发行版。

### 一次性配置

1. 在 GitHub 新建同名空仓库作为镜像。
2. Gitee 仓库 → 管理 → 仓库镜像管理，添加 push 镜像指向 GitHub 仓库，鉴权用 GitHub Personal Access Token（需 `repo` 权限）。
3. 在 GitHub 镜像仓库 Settings → Secrets and variables → Actions 添加：
   - `GITEE_TOKEN`：Gitee 私人令牌（`projects`、`releases` 权限）。
   - `GITEE_OWNER`：Gitee 用户名。
   - `GITEE_REPO`：Gitee 仓库名。

> 若同一 tag 下已有同名 `KeyProbe.exe` 附件，Gitee 会拒绝重复上传，重跑前需在 Gitee 发行版页面手动删除旧附件。

## 模型体检

「测试接口」只能告诉你这个 Key 能不能打通；「模型体检」在此基础上进一步回答四个问题，用来识别中转 / 代理网关把请求偷偷改掉的情况：

1. **系统配置的是什么模型** —— 来自 `config.json`、CC Switch 或当前表单里的模型名。
2. **实际调用的是什么模型** —— 从响应体里取出服务端回传的模型标识（OpenAI/Anthropic 的 `model`、Gemini 的 `modelVersion`），并让模型自述身份，三方比对。
3. **模型是否具备预期能力** —— 用最小化探针检测工具调用、JSON 结构化输出、图像输入等能力是否真的可用。
4. **调用参数是否被异常修改** —— 发送可验证的参数（`max_tokens=8` + 长文本、`stop` 序列、`system` 指令、`temperature=0`），再根据输出长度、结束原因、是否被截断反推参数是否被网关丢弃或覆盖。

### 深度档位

| 档位 | 调用次数 | 覆盖项 |
|---|---|---|
| 快速 | 约 2 次 | 模型身份、max_tokens |
| 标准（默认） | 约 5 次 | + system 指令、stop 序列、工具调用 |
| 深度 | 约 9 次 | + JSON 模式、图像输入、temperature=0 复现性 |

### 结论说明

每项结论按严重度从低到高标注：`跳过`（协议不支持）、`通过`、`未知`（证据不足）、`可疑`（有迹象但不能定论）、`异常`（明确不达预期）。**置信度**表示该项结论的可信程度，受限于只能通过 HTTP 请求间接观测，部分结论（如 temperature 复现性）只能给出中等或低置信度。

### 探针原理摘要

- **max_tokens 上限**：请求 `max_tokens=8` 并要求输出 1~300，检查实际输出是否被截断、`finish_reason` 是否为 `length`。回传几百 token 即说明上限被忽略。
- **stop 序列**：要求输出 `ALPHA BRAVO CHARLIE DELTA` 并设 `stop=["CHARLIE"]`，若输出里还出现 `DELTA`，说明 stop 被丢弃。
- **system 指令**：用 system 提示强制回答 `KEYPROBE-OK`，模型没按此回答即说明 system 被丢弃或被追加内容。
- **temperature=0 复现性**：同一请求发两次，输出不一致则标记可疑（部分模型在 temp=0 下本身不保证确定性，故置信度为低）。
- **服务端回传模型**：配置 `claude-*` 但响应回传 `qwen*`，直接判异常；同族但版本/档位不同判可疑。
- **模型自述**：让模型自报家门，与配置族系比对。模型可能撒谎，仅作交叉证据。
- **工具调用**：附带工具定义并 `tool_choice=required`，响应里有 `tool_calls` 即具备该能力。

> 体检会产生真实 API 调用并消耗 token，深度档位约 9 次调用；每次探针的 `max_tokens` 都很小。报告中记录了每个探针的请求体、状态码、回传模型与用量，便于复核。

## 安全说明

- 日志和 cURL 预览对 API Key 脱敏显示（保留首尾若干字符）。
- `config.json` 仅在启动时读取（`base_url`、`url_mode`、`provider`、`model`、`timeout`、`theme_mode`、`audit_level`），程序不会写入该文件；**API Key 永不落盘**：每次启动需手动输入，或通过「导入 CC Switch 配置」从本地 CC Switch 数据库读取。

## CC Switch 导入

「导入 CC Switch 配置」以只读方式打开 `~/.cc-switch/cc-switch.db`（Windows 下回退 `%HOME%\.cc-switch`），把可检测的配置填充到表单。支持 CC Switch 的 `claude`、`codex`、`gemini`、`opencode`、`grokbuild` 五类条目，只导入同时具备接口地址、密钥与默认模型的配置，其余计入「已跳过」。

兼容点：

- **Claude 档位模型**：新版 CC Switch 把模型选择存为档位（`fable` / `sonnet` / `opus` / `haiku`），实际生效模型在 `ANTHROPIC_DEFAULT_<档位>_MODEL` 中；导入时按档位取值，回退到 `ANTHROPIC_MODEL`，并优先使用不带上下文标记的 `*_MODEL_NAME`。
- **模型名后缀**：`deepseek-v4-flash[1M]` 这类 CC Switch 内部标记会被剥离，避免拿带后缀的名字去请求接口。
- **grokbuild**：解析 `[models].default` 与 `[model."<name>"]` 的 TOML 结构，按 `api_backend` 映射协议。
- **重名条目**：同名配置用 id 尾部区分（如 `My Codex（codex） · 292907`）。
- **分组展示**：下拉框按客户端分组（Claude / Codex / Gemini / OpenCode / Grok Build），组内沿用 CC Switch 的排序；`sort_index` 是各组内部序号，跨组比较没有意义，因此不再全局混排。
- 新增的 `provider_endpoints`（多端点自动选择）与 `provider_type`、`cost_multiplier` 等列不影响导入，主地址仍以 `settings_config` 为准。
