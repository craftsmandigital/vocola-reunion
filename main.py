"""Thin launcher so ``uv run main.py`` and the existing Windows shortcut keep working."""

from speech_to_text.app import main


if __name__ == "__main__":
    main()