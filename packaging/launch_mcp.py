import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from keytool.__main__ import main
    if "--mcp" not in sys.argv and "--mcp-http" not in sys.argv:
        sys.argv.append("--mcp")
    sys.exit(main())
