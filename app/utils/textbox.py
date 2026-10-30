"""CTkTextbox 辅助：隐藏内置滚动条、按内容自适应高度。

外层主内容区是 CTkScrollableFrame，由它统一负责滚动；
但页签里的日志窗与体检窗是固定高度、自己滚动的读数窗，
它们的内置滚动条不能一藏了之——藏掉之后，溢出这件事就没有任何线索了。
"""

from __future__ import annotations

import customtkinter as ctk


def hide_scrollbar(box: ctk.CTkTextbox) -> None:
    """隐藏 CTkTextbox 自带的垂直滚动条 UI。

    只用于会按内容自动长高的窗（cURL 预览）：它不滚动，所以不需要滚动条。
    """

    scrollbar = getattr(box, "_scrollbar", None)
    if scrollbar is not None:
        try:
            scrollbar.pack_forget()
        except Exception:
            pass


def tint_scrollbar(
    box: ctk.CTkTextbox,
    *,
    track: str,
    thumb: str,
    thumb_hover: str,
    width: int = 8,
) -> None:
    """把内置滚动条压成读数窗里的一道细轨。

    ``CTkTextbox._check_if_scrollbars_needed`` 会在内容溢出时重新 ``pack``
    这条滚动条，所以 ``pack_forget`` 拦不住——默认亮灰轨道会直接浮在黑底上。
    这里改色不改显隐：轨道与窗同色（只剩细条本身），
    滑块用发丝灰，溢出这件事仍然看得见，但不抢正文。
    """

    scrollbar = getattr(box, "_scrollbar", None)
    if scrollbar is None:
        return
    try:
        scrollbar.configure(
            fg_color=track,
            button_color=thumb,
            hover_color=thumb_hover,
            width=width,
        )
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
