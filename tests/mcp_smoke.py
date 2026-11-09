"""Smoke test: start the MCP server over stdio and call some tools."""
import asyncio, json, os, sys
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters


async def main(music_dir):
    params = StdioServerParameters(command=sys.executable, args=["-m", "keytool", "--mcp"], env=dict(os.environ))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print("tools:", sorted(t.name for t in tools.tools))

            async def call(name, **args):
                res = await s.call_tool(name, args)
                txt = res.content[0].text if res.content else ""
                if res.isError if hasattr(res, "isError") else getattr(res, "is_error", False):
                    print("ERROR", name, txt); return None
                return json.loads(txt) if txt.strip()[:1] in "[{" else txt

            print("analyze:", await call("analyze_paths", paths=[music_dir], wait_seconds=60))
            res = await call("search_tracks", keys=["Am", "Em"], notation="sharps")
            print("search Am/Em:", [(t["title"], t["key"], t["bpm"]) for t in res["tracks"]])
            res = await call("search_tracks", bpm_min=170, artist="vektor")
            print("search dnb:", [t["title"] for t in res["tracks"]])
            tid = res["tracks"][0]["id"]
            print("add kw:", await call("add_keywords", track_ids=[tid], keywords=["Liquid"]))
            print("kw search:", [t["title"] for t in (await call("search_tracks", keywords=["liquid"]))["tracks"]])
            print("compatible:", [t["title"] for t in (await call("compatible_tracks", track_id=tid, bpm_tolerance_percent=5))["tracks"]])
            print("convert:", await call("convert_key", key="F#m"))
            print("stats:", await call("library_stats"))
            print("get:", (await call("get_track", track_id=tid))["key_all_notations"])
            await call("remove_keywords", track_ids=[tid], keywords=["Liquid"])

asyncio.run(main(sys.argv[1]))
