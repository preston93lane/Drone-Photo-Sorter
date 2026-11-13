"""Entry point. With no arguments it opens the app; with folder arguments it
runs in command-line mode (see `python main.py --help`)."""

import sys

if __name__ == "__main__":
    if len(sys.argv) > 1:
        from drone_sorter.sorter import main as cli_main
        raise SystemExit(cli_main())
    from drone_sorter.app import main as gui_main
    gui_main()
