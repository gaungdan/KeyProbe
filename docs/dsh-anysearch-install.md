# DSH 安装 AnySearch 插件

把 [AnySearch](https://anysearch.com) 接入 DeepSeek Harness，为 Agent 提供实时联网搜索。

## 环境要求

- Node.js 22.19+ 或 24+
- `pnpm` 必须在 `PATH` 上（`dsh plugin` 命令用它管理 profile 依赖）
- API Key 可选：不配走匿名配额，配了每天 1000 次免费调用

## 一、安装插件

```sh
npx -y @deepseek-ai/dsh plugin --profile web add @anysearch/anysearch-dsh
```

装完 profile 的 `package.json` 会多出 bundle 注册，无需手动编辑：

```json
{
  "dependencies": { "@anysearch/anysearch-dsh": "^0.1.2" },
  "dsh": { "profile": { "bundles": [
    "@deepseek-ai/dsh-base",
    "@deepseek-ai/dsh-web-app",
    "@anysearch/anysearch-dsh"
  ] } }
}
```

## 二、配置 API Key

在 [anysearch.com/console/api-keys](https://anysearch.com/console/api-keys) 免费创建，格式为 `as_sk_...`。

写入 `$DSH_HOME/.credentials.yaml`（默认 `~/.dsh/.credentials.yaml`）的 `refs:` 下：

```yaml
version: 1
refs:
  ANYSEARCH_API_KEY: as_sk_你的密钥
```

优先级：启动时的 `ANYSEARCH_API_KEY` 环境变量 > 凭据文件 > 匿名。插件每次调用都重新解析凭据，轮换密钥无需重启。

## 三、重启并验证

重启 DSH 后，确认组合配置：

```sh
npx -y @deepseek-ai/dsh --profile web --dump-config
```

出现以下三处即为成功（只显示引用名，不泄露密钥值）：

```yaml
- id: web                       # patched by @anysearch/anysearch-dsh
  config:
    searchProvider: anysearch
    fetchProvider: anysearch
- id: web-search-anysearch      # == @anysearch/anysearch-dsh
  config:
    apiKeyEnv: ANYSEARCH_API_KEY
```

让 Agent 做一次需要联网的任务即可实测。

## 四、可用工具

| 用途 | 工具 |
|---|---|
| 普通网页搜索 | `web_search` |
| 抓取并清洗指定 URL | `web_fetch` |
| 发现可用领域与垂类标签 | `anysearch_capabilities` |
| 垂类或带参数搜索 | `anysearch_search` |
| 1–5 个查询并发 | `anysearch_batch_search` |

插件自动接管 Harness 原生的 `web_search` / `web_fetch`，不新增重复的 extract 工具。

## 五、自定义配置（可选）

默认无需改动。要调整就在 profile 的 `cordis.patch.yml` 里覆盖同一个 id——`id` 保持 `web-search-anysearch`，并**整块重写 `config`**（patch 是替换而非合并），且不要再加第二个不同 id 的 AnySearch provider：

```yaml
- id: web-search-anysearch
  config:
    apiKeyEnv: ANYSEARCH_API_KEY
    baseURL: https://api.anysearch.com
    maxRenderedContentChars: 12000
```

| 字段 | 默认值 | 说明 |
|---|---|---|
| `apiKeyEnv` | `ANYSEARCH_API_KEY` | 凭据引用名；缺失则匿名访问 |
| `baseURL` | `https://api.anysearch.com` | API 基址 |
| `maxRenderedContentChars` | `12000` | 每次高级工具调用渲染给模型的正文字符上限 |

## 六、管理

```sh
# 更新
npx -y @deepseek-ai/dsh plugin --profile web update @anysearch/anysearch-dsh

# 卸载
npx -y @deepseek-ai/dsh plugin --profile web remove @anysearch/anysearch-dsh
```

## 常见问题

**`web_fetch` 不在工具列表里。** 面向模型的工具由 agent preset 决定，DSH 自带的 `standard` / `code` / `cordis` 三个 preset 都设了 `tool-web` 的 `fetch: false`。需要的话自建 preset：把 shipped preset 的 `agent.cordis.yml` 复制到 `$DSH_HOME/.agent-presets/<新名字>/`，改为 `fetch: true`，再配 `preset.yml`，然后在 `settings.yaml` 指定默认值：

```yaml
agent-presets:
  default: <新名字>
```

preset 在会话创建时绑定，改默认值只对**新建**会话生效。preset 信任级别等同 shell 访问权，勿使用来源不明的 preset。

**`web_fetch` 报 `data.normalized_url must be a string`。** 0.1.2 的已知缺陷：`parseExtractData` 校验了 7 个服务端当前不返回的字段（`normalized_url`、`effective_url`、`content_type`、`source_http_status`、`truncated`、`returned_characters`、`content_trust`）。接口本身正常，是客户端校验过严。搜索类工具不受影响。临时替代——用 Skill 包的 CLI 抓取：

```sh
node <skill_dir>/scripts/anysearch_cli.js extract "https://example.com"
```

**`dsh plugin add` 找不到 pnpm。** 先确保 `node`、`npx`、`pnpm` 都能从 `PATH` 直接调用。

**GitHub 域名不可达。** 若 hosts 文件把 `github.com`、`raw.githubusercontent.com` 指向 `127.0.0.1`，改用 `codeload.github.com` 或 `cdn.jsdelivr.net` 拉取，或清理 hosts 记录。

## 其他接入方式

| 方式 | 形态 | 适用 |
|---|---|---|
| DSH 插件 | 原生 provider + 工具 | DSH 用户首选 |
| Skill 包 | 技能目录 + 多语言 CLI | 任意 Agent 平台通用 |
| MCP | `https://api.anysearch.com/mcp` | 支持 MCP 的客户端 |
| HTTP API | `POST /v1/search`、`/v1/extract` | 自建集成 |

Skill 包安装：从 [releases](https://github.com/anysearch-ai/anysearch-skill/releases) 下载解压，重命名为 `anysearch` 放入技能目录（`~/.claude/skills/`、`~/.codex/skills/`、`~/.agents/skills/` 等），在其 `.env` 中设置 `ANYSEARCH_API_KEY`。
