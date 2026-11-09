import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from keytool.__main__ import main
    if len(sys.argv) == 1:          # no arguments: MCP server over stdio (as started by Claude Desktop);
        sys.argv.append("--mcp")    # with arguments the console exe also runs --analyze / --compare-key etc.
    sys.exit(main())
