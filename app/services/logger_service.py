"""日志服务模块：统一的日志输出接口，带时间戳、级别与回调。"""

from datetime import datetime
from enum import Enum
from typing import Callable, Optional


class LogLevel(Enum):
    """日志级别。"""

    INFO = "INFO"
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"
    WARNING = "WARNING"


class LoggerService:
    """日志服务，向注册的回调推送格式化日志。"""

    def __init__(self, callback: Optional[Callable[[str], None]] = None) -> None:
        self.callback = callback
        self.logs: list[str] = []

    def set_callback(self, callback: Callable[[str], None]) -> None:
        """设置日志回调。"""

        self.callback = callback

    def _format_log(self, level: LogLevel, message: str) -> str:
        timestamp = datetime.now().strftime("%H:%M:%S")
        return f"[{timestamp}] [{level.value}] {message}"

    def _emit(self, level: LogLevel, message: str) -> None:
        log = self._format_log(level, message)
        self.logs.append(log)
        if self.callback:
            self.callback(log)

    def info(self, message: str) -> None:
        """输出 INFO 日志。"""

        self._emit(LogLevel.INFO, message)

    def success(self, message: str) -> None:
        """输出 SUCCESS 日志。"""

        self._emit(LogLevel.SUCCESS, message)

    def error(self, message: str) -> None:
        """输出 ERROR 日志。"""

        self._emit(LogLevel.ERROR, message)

    def warning(self, message: str) -> None:
        """输出 WARNING 日志。"""

        self._emit(LogLevel.WARNING, message)

    def clear(self) -> None:
        """清空日志缓存。"""

        self.logs.clear()

    def get_text(self) -> str:
        """返回全部日志文本，用于保存到文件。"""

        return "\n".join(self.logs)
