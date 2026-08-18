#!/usr/bin/env python3
"""
Entry point for the Redmi Note 13 Pro camera automation application.
"""

from app.gui import Application


def main() -> None:
    app = Application()
    app.run()


if __name__ == "__main__":
    main()