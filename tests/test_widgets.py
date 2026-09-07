"""ComboBox 占位提示的取值契约。无显示环境时整体跳过。"""

from collections.abc import Iterator

import pytest

ctk = pytest.importorskip("customtkinter")

from app.ui import theme  # noqa: E402 - 必须在 importorskip 之后
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
