"""ComboBox 占位提示的取值契约与读数窗的只读/复制契约。无显示环境时整体跳过。"""

from collections.abc import Iterator

import pytest

ctk = pytest.importorskip("customtkinter")

from app.services.url_builder import curl_command, curl_preview  # noqa: E402
from app.ui import theme  # noqa: E402 - 必须在 importorskip 之后
from app.ui.result_panel import ResultPanel  # noqa: E402
from app.ui.widgets import ComboBox  # noqa: E402

PLACEHOLDER = "获取或直接填写模型 ID"


@pytest.fixture(scope="module")
def root() -> Iterator[object]:
    """整个模块共用一个根窗口：反复新建/销毁 Tk 根窗口会让 Tk 初始化失败。"""

    try:
        window = ctk.CTk()
    except Exception as exc:  # pragma: no cover - 取决于运行环境
        pytest.skip(f"无可用显示环境：{exc}")
    window.withdraw()
    yield window
    window.destroy()


def test_placeholder_is_not_a_value_and_manual_input_wins(root) -> None:
    box = ComboBox(root, values=[], placeholder_text=PLACEHOLDER)
    root.update_idletasks()

    # 占位提示只是提示：界面上可见，取值为空
    assert box._entry.get() == PLACEHOLDER
    assert box.get() == ""

    # 手动输入：模拟点进输入框再敲字
    box._clear_placeholder()
    box._entry.insert(0, "my-relay-model")
    assert box.get() == "my-relay-model"

    # 清空并失焦后占位提示回来，取值重新变空
    box._entry.delete(0, "end")
    box._restore_placeholder()
    assert box.get() == ""
    assert box._entry.get() == PLACEHOLDER


def test_set_and_dropdown_selection_override_placeholder(root) -> None:
    box = ComboBox(root, values=["a-1", "b-2"], placeholder_text=PLACEHOLDER)
    root.update_idletasks()
    # 带占位提示时不预选首项
    assert box.get() == ""

    box.set("a-1")
    assert box.get() == "a-1"

    box._dropdown_callback("b-2")
    assert box.get() == "b-2"

    # set("") 回到占位提示状态，重绘后灰字不会被父类改回正文色
    box.set("")
    assert box.get() == ""
    box.configure(require_redraw=True)
    assert box.get() == ""
    assert box._entry.cget("fg") == theme.TEXT_FAINT


def test_readonly_combobox_keeps_preselecting_first_value(root) -> None:
    box = ComboBox(root, values=["only"], readonly=True)
    root.update_idletasks()

    assert box.get() == "only"
    # 解除锁定时不应退回可编辑状态
    box.configure(state="normal")
    assert box._state == "readonly"


def test_set_works_on_disabled_combobox(root) -> None:
    """禁用中的下拉框也要能改写文字，否则「先禁用再 set」会得到空白框。"""

    box = ComboBox(root, values=["a", "b"], readonly=True)
    root.update_idletasks()
    box.configure(state="disabled")

    box.set("b")
    assert box.get() == "b"


def test_dropdown_arrow_is_repainted_uniformly(root) -> None:
    """内置箭头（粗折线或字形）必须换成自绘折线，两个控件才是一套。"""

    from app.ui.widgets import OptionMenu, _ARROW_TAG

    combo = ComboBox(root, values=["a", "b"], readonly=True, height=theme.CONTROL_HEIGHT)
    menu = OptionMenu(root, values=["c", "d"], height=theme.CONTROL_HEIGHT)
    root.update_idletasks()

    shapes = []
    for widget in (combo, menu):
        canvas = widget._canvas
        assert canvas.find_withtag(_ARROW_TAG), "自绘箭头缺失"
        assert not canvas.find_withtag("dropdown_arrow"), "原生箭头没有清掉"
        coords = [float(v) for v in canvas.tk.splitlist(
            canvas.tk.call(canvas._w, "coords", _ARROW_TAG))]
        assert len(coords) == 6, "箭头应为三点折线"
        assert float(canvas.itemcget(_ARROW_TAG, "width")) == 2
        # 横向 / 纵向跨度一致：并排放着才是同一个记号
        shapes.append((round(coords[4] - coords[0], 1), round(coords[3] - coords[1], 1)))
    assert shapes[0] == shapes[1]


def test_dropdown_entries_padded_and_headers_disabled(root) -> None:
    """弹层条目统一左内缩，分组标题置灰且不可选。"""

    box = ComboBox(root, values=["── 分组（1）──", "real"], readonly=True)
    root.update_idletasks()
    box.set_header_values({"── 分组（1）──"})
    menu = box._dropdown_menu

    assert str(menu.entrycget(0, "label")).startswith("  ")
    assert menu.entrycget(0, "state") == "disabled"
    assert menu.entrycget(1, "state") == "normal"
    # 标题只是分隔，取值列表里不能真的选中它
    assert not str(menu.entrycget(1, "label")).startswith("──")


def test_curl_preview_is_readonly(root) -> None:
    """request 区是读数窗：能选中，但敲键盘、粘贴都改不动它。"""

    panel = ResultPanel(root)
    display = curl_preview(
        "https://api.example.com/v1/messages",
        [("x-api-key", "sk-abc***xyz"), ("Content-Type", "application/json")],
        {"model": "m", "max_tokens": 16},
    )
    panel.set_curl(display)
    root.update_idletasks()
    root.update()

    inner = panel.curl_preview._textbox
    assert inner.get("1.0", "end-1c") == display

    inner.focus_force()
    inner.mark_set("insert", "end")
    root.update()
    inner.event_generate("<Key>", keysym="x")
    inner.event_generate("<Control-v>")
    inner.event_generate("<<Paste>>", data="curl --evil")
    root.update()

    assert inner.get("1.0", "end-1c") == display


def test_curl_copy_button_emits_single_line(root) -> None:
    """展示用续行写法，复制出来必须是一条能直接粘进终端的命令。"""

    copied: list[str] = []
    panel = ResultPanel(root, on_copy=copied.append)
    headers = [("x-api-key", "sk-abc***xyz"), ("Content-Type", "application/json")]
    body = {"model": "m", "max_tokens": 16}
    url = "https://api.example.com/v1/messages"
    panel.set_curl(curl_preview(url, headers, body), copy_text=curl_command(url, headers, body, windows=False))

    panel.copy_btn.invoke()

    assert copied == [curl_command(url, headers, body, windows=False)]
    assert "\n" not in copied[0]
    assert "\\" not in copied[0]
