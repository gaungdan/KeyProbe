"""Application entrypoint."""

from app.ui.app import KeyProbeApp


def main() -> None:
    """启动 KeyProbe 桌面应用。"""

    app = KeyProbeApp()
    app.mainloop()


if __name__ == "__main__":
    main()
