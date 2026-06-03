# Demo assets

The README hero GIF and the source files used to render it.

| File | Purpose |
|---|---|
| `demo.gif` | The recording embedded in the project README. |
| `demo.tape` | [`vhs`](https://github.com/charmbracelet/vhs) script that drives the recording. |
| `mcp-cli-mock.sh` | Shell stub that prints the two canned MCP-tool responses (Alice vs. Bob) the recording shows. Not a real client — for demo rendering only. |

## Re-rendering the GIF

```bash
cd .github/assets
cp mcp-cli-mock.sh mcp-cli                # vhs invokes ./mcp-cli
chmod +x mcp-cli
PATH="$PWD:$PATH" vhs demo.tape           # writes demo.gif into the current dir
rm mcp-cli                                # keep the canonical stub committed
```

Both `vhs` and its `ttyd` dependency are available on macOS via `brew install vhs`.
On Linux see the [vhs README](https://github.com/charmbracelet/vhs#installation).

Edit `demo.tape` to change theme, font size, typing speed or the
commands; edit `mcp-cli-mock.sh` to change the canned outputs.
