# qdrant-mcp-rbac — sample demo gif
# Render: vhs demo.tape (produces demo.gif)

Output demo.gif

Set FontSize 18
Set Width 1200
Set Height 720
Set Theme "Catppuccin Mocha"
Set Padding 24
Set TypingSpeed 28ms

# --- intro line -----------------------------------------------------------
Type "# qdrant-mcp-rbac — same query, different users, different results"
Sleep 600ms
Enter
Sleep 700ms

# --- alice (sales role) ---------------------------------------------------
Type "# alice has role: sales (full access to collection)"
Enter
Sleep 400ms

Type "./mcp-cli --user alice@acme.com call search_collection collection=quarterly_reports query=Q3-revenue"
Sleep 200ms
Enter
Sleep 1800ms

# blank line for breathing room
Enter
Sleep 800ms

# --- bob (external auditor with doc-policy) -------------------------------
Type "# bob is external (role: auditor, doc_policy: source must start with 'public/')"
Enter
Sleep 400ms

Type "./mcp-cli --user bob@vendor.com call search_collection collection=quarterly_reports query=Q3-revenue"
Sleep 200ms
Enter
Sleep 1800ms

# closing pause so the viewer can read the diff
Sleep 2200ms

# --- caption --------------------------------------------------------------
Type "# same MCP tool, server-side enforcement, no client coordination"
Sleep 1500ms
