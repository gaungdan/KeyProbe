"""CTkTextbox 辅助：隐藏内置滚动条、按内容自适应高度。

外部主内容区已经是 CTkScrollableFrame，统一由外层滚动条负责滚动，
内部 textbox 不再各自带一条滚动条，避免出现两层滚动条并排的情况。
"""

from __future__ import annotations

import customtkinter as ctk


def hide_scrollbar(box: ctk.CTkTextbox) -> None:
    """隐藏 CTkTextbox 自带的垂直滚动条 UI。"""

    scrollbar = getattr(box, "_scrollbar", None)
    if scrollbar is not None:
        try:
            scrollbar.pack_forget()
        except Exception:
            pass


def fit_to_content(
    box: ctk.CTkTextbox,
    *,
    line_height: int = 20,
    min_height: int = 120,
    max_height: int = 800,
) -> None:
    """按内容行数设置 textbox 高度，让外层滚动条负责浏览。"""

    try:
        inner = getattr(box, "_textbox", box)
        end_index = inner.index("end -1c")
        last_line = int(str(end_index).split(".")[0])
        lines = max(1, last_line)
        height = max(min_height, min(max_height, lines * line_height + 16))
        box.configure(height=height)
    except Exception:
        pass


def fit_to_wrapped_content(
    box: ctk.CTkTextbox,
    *,
    line_height: int = 18,
    min_height: int = 64,
    max_height: int = 400,
) -> None:
    """按实际显示行数（含自动换行）设置高度，内容整体可见、不被裁切。

    与 :func:`fit_to_content` 的区别：这里统计的是换行后的可视行数，
    适用于 cURL 这类单条逻辑行很长、会被折成多行显示的内容。
    """

    try:
        inner = getattr(box, "_textbox", box)
        inner.update_idletasks()
        # Tk 的 displaylines 统计的是两个索引之间的换行次数，
        # 因此可见行数 = 换行次数 + 1（单行内容返回 None/0）。
        wraps = inner.count("1.0", "end -1c", "displaylines")
        if isinstance(wraps, tuple):
            wraps = wraps[0] if wraps else 0
        lines = int(wraps or 0) + 1
        height = max(min_height, min(max_height, lines * line_height + 16))
        box.configure(height=height)
    except Exception:
        pass
