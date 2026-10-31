"""PyInstaller entry point."""
import multiprocessing

from shardloom.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
