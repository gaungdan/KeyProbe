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
- **Anthropic 回退**：当 Anthropic 鉴权失败时，自动尝试 OpenAI 兼容鉴权；模型列表仍获取失败时回退到内置默认模型。
- **cURL 请求预览**：实时生成等效 cURL 命令，API Key 始终脱敏。
- **实时日志**：带时间戳与级别，支持清空。
- **配置持久化**：保存到 `config.json`，启动时自动回填 `base_url`、`api_key`、`url_mode`、`provider`、`model`、`timeout`、`theme_mode`。

## 项目结构

```text
main.py                    # 入口
app/
  checker/client.py        # 异步检测核心逻辑（httpx）
  models/                  # pydantic 配置与结果模型
  services/
    providers.py           # Provider 识别与归一化
    url_builder.py         # URL 拼接与备用地址生成
    config_store.py        # config.json 读写
    logger_service.py      # 日志服务
  ui/                      # CustomTkinter 界面（主窗口、结果面板、日志面板）
  utils/security.py        # API Key 脱敏
tests/test_core.py         # 核心逻辑单测
build.bat                  # Windows 打包脚本
KeyProbe.html              # 纯前端单文件版本
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

## 安全说明

- 日志和 cURL 预览对 API Key 脱敏显示（保留首尾若干字符）。
- `config.json` 会明文保存 `base_url`、`api_key`、`url_mode`、`provider`、`model`、`timeout`、`theme_mode`，请勿提交到公共仓库。
