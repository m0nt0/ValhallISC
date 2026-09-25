"""PyInstaller entry point: the same executable is the tray app (no args), the CLI and the mount worker."""

from irisfs.__main__ import main

if __name__ == "__main__":
    main()
