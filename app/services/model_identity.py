"""模型标识归一化：把模型名 / 模型自述文本映射到「模型族系」。

体检需要回答「配置的模型」「服务端回传的模型」「模型自己说它是谁」是否一致。
三者的字符串几乎不可能完全相同（例如 `claude-sonnet-4-5-20250929` 与
「我是 Claude」），所以统一降维到族系（family）再比较。
"""

from __future__ import annotations

import re

# 族系 -> 该族系的识别关键字（全部小写）。顺序影响优先级：先匹配更具体的族系。
FAMILY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "claude": ("claude", "anthropic", "sonnet", "opus", "haiku"),
    "gpt": ("gpt-", "gpt3", "gpt4", "gpt5", "chatgpt", "o1-", "o3-", "o4-", "openai", "davinci"),
    "gemini": ("gemini", "bison", "palm"),
    "deepseek": ("deepseek",),
    "qwen": ("qwen", "通义", "千问", "tongyi"),
    "glm": ("glm", "chatglm", "智谱", "zhipu"),
    "kimi": ("kimi", "moonshot"),
    "doubao": ("doubao", "豆包", "seed-"),
    "hunyuan": ("hunyuan", "混元"),
    "ernie": ("ernie", "文心", "wenxin"),
    "spark": ("spark", "星火"),
    "minimax": ("minimax", "abab"),
    "step": ("step-", "阶跃"),
    "yi": ("yi-", "零一万物"),
    "baichuan": ("baichuan", "百川"),
    "llama": ("llama", "codellama"),
    "mistral": ("mistral", "mixtral", "codestral", "magistral"),
    "grok": ("grok", "xai"),
    "command": ("command-", "cohere"),
    "nova": ("nova-", "amazon.nova"),
    "titan": ("titan",),
    "phi": ("phi-",),
    "gemma": ("gemma",),
    "sensechat": ("sensechat", "sensenova", "商汤"),
}

# 模型自述里出现这些词时，说明模型在拒答或答非所问，不能当作身份证据。
_EVASIVE_MARKERS = (
    "无法",
    "不能透露",
    "不便告知",
    "as an ai language model",
    "i cannot",
    "i can't share",
)


def model_family(text: str) -> str:
    """把模型名或自述文本映射为族系，识别不出时返回空串。"""

    lowered = (text or "").strip().lower()
    if not lowered:
        return ""
    for family, keywords in FAMILY_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return family
    return ""


def self_reported_family(text: str) -> str:
    """从模型自述文本里提取族系，明显在回避时返回空串。"""

    lowered = (text or "").strip().lower()
    if not lowered:
        return ""
    family = model_family(lowered)
    if family:
        return family
    if any(marker in lowered for marker in _EVASIVE_MARKERS):
        return ""
    return ""


def normalize_model_name(model: str) -> str:
    """去掉版本日期、路径前缀、区域前缀等噪声，便于宽松比较。"""

    name = (model or "").strip().lower()
    if not name:
        return ""
    name = name.removeprefix("models/")
    # Bedrock 形如 us.anthropic.claude-sonnet-4-20250514-v1:0
    name = re.sub(r"^(us|eu|apac|ap|global)\.", "", name)
    name = re.sub(r"^(anthropic|amazon|meta|mistral|cohere|ai21|deepseek)\.", "", name)
    name = re.sub(r"[:@]\d+$", "", name)
    name = re.sub(r"-v\d+(?::\d+)?$", "", name)
    name = re.sub(r"-(?:20\d{6}|\d{6})$", "", name)
    name = re.sub(r"-(?:latest|preview|stable)$", "", name)
    return name.strip("-")


def models_match(configured: str, reported: str) -> bool:
    """判断服务端回传模型是否与配置模型指向同一个模型。"""

    left = normalize_model_name(configured)
    right = normalize_model_name(reported)
    if not left or not right:
        return False
    if left == right:
        return True
    return left.startswith(right) or right.startswith(left)
