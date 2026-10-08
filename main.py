import sys
from pathlib import Path


def main() -> None:
    source = Path(__file__).resolve().parent / "src"
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
    from cli.menu import CLIApp

    CLIApp().run()


if __name__ == "__main__":
    main()
