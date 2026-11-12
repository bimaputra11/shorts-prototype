# Secure MCP Connection Setup — Windows to ChatGPT

This guide documents the verified setup for connecting the local Shorts Prototype MCP server on Windows to ChatGPT Web through OpenAI Secure MCP Tunnel. It covers the process from creating the Platform tunnel through adding the custom MCP connection in ChatGPT, plus the local start/stop controller.

The configuration was tested on November 9, 2026. OpenAI's interface and release versions can change; verify the current release and UI labels when repeating the process.

## 1. Connection overview

The local MCP server exposes selected tools to ChatGPT. The tunnel client makes an outbound connection to OpenAI, so the PC does not need an inbound public port.

Request path: ChatGPT -> OpenAI tunnel -> local tunnel-client -> local Python MCP server.

The tunnel-client must be running whenever ChatGPT needs the local tools. It is intentionally not configured to start automatically with Windows.

## 2. Current configuration

| Setting | Current value |
| --- | --- |
| Windows | Windows 10 Pro, 64-bit, AMD64 |
| Project root | $env:USERPROFILE\Projects\shorts-prototype |
| Python | 3.12.10 |
| Python environment | Project-root .venv |
| Node.js runtime | 24.22.1 (used by yt-dlp for YouTube player challenges) |
| YouTube downloader | yt-dlp 2026.10.26 + yt-dlp-ejs 0.8.0 |
| MCP Python package | mcp 2.3.0 |
| MCP server | server.py |
| MCP transport | stdio |
| Tunnel client | 0.0.19 |
| Tunnel profile | shorts-prototype |
| Tunnel name | shorts-prototype |
| Tunnel ID | YOUR_TUNNEL_ID |
| Local health address | http://127.0.0.1:8765 |
| Filesystem workspace | $env:USERPROFILE\Projects\shorts-prototype\workspace |
| Runtime key file | %LOCALAPPDATA%\shorts-prototype\secrets\runtime-key.txt |
| Profile file | %APPDATA%\tunnel-client\shorts-prototype.yaml |

The tunnel ID is not the runtime API key. Never put the runtime API key in this document.

## 3. Prerequisites and permissions

You need a ChatGPT account where custom MCP connections are available, access to an OpenAI Platform organization, Windows 10/11 64-bit, PowerShell, Python 3.12, Node.js, the local server source, and the official OpenAI tunnel-client Windows AMD64 release. Install the pinned Python dependencies with `python -m pip install -r requirements.txt`; this includes yt-dlp and its EJS challenge-solver package. The existing `imageio-ffmpeg` dependency supplies FFmpeg for merging separate video and audio streams.

The runtime key principal needs Tunnels Read + Use for this tunnel. Tunnel administration is separate and requires Tunnels Read + Manage and the applicable admin-key permission. Normal tunnel operation does not need an admin key.

## 4. Create the tunnel in OpenAI Platform

1. Open https://platform.openai.com/settings/organization/tunnels.
2. Select the correct Platform organization.
3. Create a tunnel.
4. Name it, for example, shorts-prototype.
5. Add a description, for example, automate my YouTube Shorts content workflow.
6. Associate it with the intended organization and ChatGPT workspace where those options are provided.
7. Save the tunnel and copy its tunnel ID.

The tunnel created for this project is named shorts-prototype. Its ID is:

~~~text
YOUR_TUNNEL_ID
~~~

Reuse this tunnel ID when reinstalling the client. Do not create another tunnel unless a new one is intentionally required.

## 5. Create the restricted runtime key

1. Open https://platform.openai.com/settings/organization/api-keys.
2. Create a runtime API key named, for example, shorts-prototype-runtime.
3. Choose restricted permissions.
4. Grant Tunnels Read and Tunnels Use only.
5. Create the key and copy it immediately; the full secret may not be shown again.

Do not use an admin API key as the long-lived runtime key.

### Save the key locally

This project deliberately stores the key as plaintext, as chosen by the user. Keep it outside the project and never commit, upload, or share it.

~~~powershell
$secretDir = "$env:LOCALAPPDATA\shorts-prototype\secrets"
New-Item -ItemType Directory -Force -Path $secretDir | Out-Null
$keyFile = Join-Path $secretDir 'runtime-key.txt'
notepad $keyFile
~~~

Paste only the key into Notepad, save, and close it. The controller reads this file when starting the tunnel. Do not paste the key into ChatGPT, source files, profile YAML, command-line arguments, or logs.

Because the key is plaintext, anyone or any process running under the signed-in Windows account may be able to read it. Protect the account and credential file.

## 6. Install tunnel-client

Use the official OpenAI tunnel-client release distribution. Do not substitute ngrok or another public tunnel.

The original client folder was $env:USERPROFILE\tunnel-client. The original binary was v0.0.15. For this project, v0.0.19 AMD64 was installed separately, preserving the original files.

Verified executable:

~~~text
$env:USERPROFILE\tunnel-client\updates\v0.0.19\extracted\tunnel-client.exe
~~~

The v0.0.19 archive used was tunnel-client-v0.0.19-windows-amd64.zip from the official v0.0.19 release. The SHA256SUMS.txt file from the same release was downloaded and the archive checksum was verified before extraction.

Checksum observed for the archive:

~~~text
1d0ecc3a54f2a51e27a635ac155f40fbb95df58c52cdeddf6c75532cc0bbeb25
~~~

To verify the installed executable:

~~~powershell
$client = '$env:USERPROFILE\tunnel-client\updates\v0.0.19\extracted\tunnel-client.exe'
& $client --version
~~~

Observed version:

~~~text
0.0.19+01d67bb4da0d8359b808b4761f8db171ab682cd7
~~~

Use the absolute executable path; it does not need to be on PATH.

## 7. Prepare the local Python MCP server

Create a project virtual environment and install the MCP SDK:

~~~powershell
$project = '$env:USERPROFILE\Projects\shorts-prototype'
py -3.12 -m venv "$project\.venv"
& "$project\.venv\Scripts\python.exe" -m pip install --upgrade pip
& "$project\.venv\Scripts\python.exe" -m pip install "mcp[cli]"
~~~

The tested environment used Python 3.12.10 and MCP Python package 2.3.0.

Server source:

~~~text
$env:USERPROFILE\Projects\shorts-prototype\server.py
~~~

The server uses local stdio transport. MCP protocol output must stay on stdout; diagnostic logs belong on stderr.

The server exposes ten tools:

| Tool | Purpose |
| --- | --- |
| server_info | Show server identity and workspace |
| list_files | List files and folders anywhere inside the project root (read-only) |
| read_text_file | Read a UTF-8 text file anywhere inside the project root (read-only) |
| write_text_file | Create or update a UTF-8 text file inside workspace only |
| create_directory | Create a directory inside workspace only |
| get_file_info | Inspect metadata anywhere inside the project root (read-only) |
| run_powershell | Run PowerShell from the workspace |
| build_configured_short | Render any prepared project from a JSON profile and an explicitly selected hook |
| analyze_inspiration_video | Detect scenes, extract representative frames, and read timed on-screen captions from an inbox video |
| import_inspiration_source | Download a submitted YouTube or direct video URL into the inbox with yt-dlp |

Read-only filesystem tools (list_files, read_text_file, and get_file_info) can browse and read anywhere inside the project root. Their paths are relative to workspace/; use .. to reach the project root (for example, ../README.md). Absolute paths and paths resolving outside the project root are rejected. Write tools (write_text_file and create_directory) remain restricted to workspace/. PowerShell is not sandboxed to the workspace: it runs with the signed-in Windows user's permissions and can access other locations.

Validate the server:

~~~powershell
$project = '$env:USERPROFILE\Projects\shorts-prototype'
$python = Join-Path $project '.venv\Scripts\python.exe'
& $python -m py_compile (Join-Path $project 'server.py')
if ($LASTEXITCODE -ne 0) { throw 'Python syntax validation failed.' }
& $python -c "from mcp.server import MCPServer; print('MCP SERVER IMPORT OK')"
~~~

## 8. Generate the tunnel-client profile

Use the built-in local stdio sample:

~~~powershell
$project = '$env:USERPROFILE\Projects\shorts-prototype'
$client = '$env:USERPROFILE\tunnel-client\updates\v0.0.19\extracted\tunnel-client.exe'
$python = (Join-Path $project '.venv\Scripts\python.exe').Replace('\', '/')
$server = (Join-Path $project 'server.py').Replace('\', '/')
$mcpCommand = "$python $server"

$initArgs = @(
    '--sample', 'sample_mcp_stdio_local',
    '--profile', 'shorts-prototype',
    '--tunnel-id', 'YOUR_TUNNEL_ID',
    '--mcp-command', $mcpCommand,
    '--health-listen-addr', '127.0.0.1:8765'
)
& $client init @initArgs
if ($LASTEXITCODE -ne 0) { throw 'Profile generation failed.' }
~~~

**Windows path detail:** use forward slashes in the executable and server paths passed to --mcp-command. Backslashes were parsed incorrectly by the client build used here; forward slashes worked.

Profile location:

~~~text
%APPDATA%\tunnel-client\shorts-prototype.yaml
~~~

The profile must reference the key using an environment variable, not a literal secret. The relevant profile settings are:

~~~yaml
control_plane:
  base_url: "https://api.openai.com"
  tunnel_id: "YOUR_TUNNEL_ID"
  api_key: "env:CONTROL_PLANE_API_KEY"
health:
  listen_addr: "127.0.0.1:8765"
mcp:
  commands:
    - channel: main
      command: "C:/Users/KAYRA/Projects/shorts-prototype/.venv/Scripts/python.exe C:/Users/KAYRA/Projects/shorts-prototype/server.py"
~~~

Never put the API key directly into the YAML file.

## 9. Validate the profile

Load the key into the current PowerShell process without printing it:

~~~powershell
$keyFile = "$env:LOCALAPPDATA\shorts-prototype\secrets\runtime-key.txt"
$env:CONTROL_PLANE_API_KEY = (Get-Content -Raw $keyFile).Trim()
if (-not $env:CONTROL_PLANE_API_KEY) { throw 'Runtime API key is empty.' }
~~~

Run the doctor check:

~~~powershell
$client = '$env:USERPROFILE\tunnel-client\updates\v0.0.19\extracted\tunnel-client.exe'
& $client doctor --profile shorts-prototype --explain
~~~

Expected result: RESULT ok. For stdio servers, network reachability and OAuth metadata checks are intentionally skipped.

After manual validation, remove the key from the current PowerShell process:

~~~powershell
Remove-Item Env:\CONTROL_PLANE_API_KEY -ErrorAction SilentlyContinue
~~~

## 10. First manual tunnel test

Load the runtime key and start the foreground daemon:

~~~powershell
$keyFile = "$env:LOCALAPPDATA\shorts-prototype\secrets\runtime-key.txt"
$env:CONTROL_PLANE_API_KEY = (Get-Content -Raw $keyFile).Trim()
$client = '$env:USERPROFILE\tunnel-client\updates\v0.0.19\extracted\tunnel-client.exe'
& $client run --profile shorts-prototype
~~~

For a separate PowerShell window, check:

~~~powershell
Invoke-WebRequest http://127.0.0.1:8765/healthz -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:8765/readyz -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:8765/health/mcp -UseBasicParsing
~~~

Expected basic health results are HTTP 200, with live from /healthz and ready from /readyz. The MCP endpoint is diagnostic telemetry and may say not_observed before discovery is recorded. A successful ChatGPT tool call is the stronger connection test.

## 11. Add the custom MCP connection in ChatGPT Web

Keep tunnel-client running while adding/discovering the connector and while ChatGPT calls MCP tools.

1. Open ChatGPT Web.
2. Open Settings → Connectors, or https://chatgpt.com/#settings/Connectors.
3. Choose the option to add a custom MCP server/connector.
4. Choose Tunnel as the connection method.
5. Select or provide tunnel ID YOUR_TUNNEL_ID.
6. Name the connector, for example, Shorts Prototype.
7. If the UI asks for upstream authentication, use the no-upstream-auth option. This local stdio server does not implement its own OAuth flow; the tunnel runtime authenticates to OpenAI using the runtime API key.
8. Review the permissions/security information.
9. Create/install the connector. The UI used “Create as a plugin” in this setup.
10. Confirm that all ten tools are discovered.

Exact UI wording can change. Choose the Tunnel connection and existing tunnel, not a public localhost URL.

## 12. Use the local start/stop controller

The tunnel should not run constantly. A local controller starts it only when needed.

Files:

~~~text
$env:USERPROFILE\Projects\shorts-prototype\Shorts Tunnel.cmd
$env:USERPROFILE\Projects\shorts-prototype\scripts\shorts-tunnel.ps1
$env:USERPROFILE\Desktop\Shorts Prototype Tunnel.lnk
~~~

Double-click the desktop shortcut to open the menu:

| Menu choice | Action |
| --- | --- |
| 1 | Start tunnel in background and wait for /readyz |
| 2 | Stop tunnel and its local child-process tree |
| 3 | Restart tunnel |
| 4 | Check status |
| Q | Close the menu; do not stop the tunnel |

Direct PowerShell commands:

~~~powershell
$controller = '$env:USERPROFILE\Projects\shorts-prototype\scripts\shorts-tunnel.ps1'
& $controller -Action start
& $controller -Action status
& $controller -Action restart
& $controller -Action stop
~~~

The controller reads the plaintext runtime key, places it in the tunnel-client process environment, then removes it from the controller's own environment. The key is not placed in command-line arguments, profile YAML, or logs.

Runtime state is stored in %LOCALAPPDATA%\shorts-prototype\runtime. Logs are stored in %LOCALAPPDATA%\shorts-prototype\logs.

Closing the menu does not stop the tunnel. Choose option 2 to stop it.

Do not use MCP's run_powershell tool to stop/restart the tunnel that carries that same MCP connection. The request can terminate its own connection. Use the desktop shortcut/controller instead.

## 13. Verify after each start or restart

1. Confirm the controller reports Tunnel is READY.
2. Confirm status shows RUNNING, Health: LIVE, and Ready: READY.
3. Make a fresh MCP call in ChatGPT, such as server_info or list_files.
4. If a tool call succeeds, the ChatGPT-to-local-MCP path is working.

The background restart test showed that /health/mcp can report unknown / not_observed and zero tools even though fresh ChatGPT calls to server_info and list_files succeed. Do not use that telemetry field alone as proof of failure.

## 14. Troubleshooting

### tunnel-client is not recognized

Use its full path:

~~~powershell
& '$env:USERPROFILE\tunnel-client\updates\v0.0.19\extracted\tunnel-client.exe' --version
~~~

### MCP executable not found during profile creation

Use forward-slash paths in the --mcp-command value:

~~~powershell
$python = 'C:/Users/KAYRA/Projects/shorts-prototype/.venv/Scripts/python.exe'
$server = 'C:/Users/KAYRA/Projects/shorts-prototype/server.py'
$mcpCommand = "$python $server"
~~~

### Runtime API key rejected

- Check that the key file exists at %LOCALAPPDATA%\shorts-prototype\secrets\runtime-key.txt.
- Check that it contains the complete key and no labels.
- Confirm its principal has Tunnels Read + Use for this tunnel.
- Do not use an admin key as the runtime key.
- The desktop controller loads the key automatically.

Never print or paste the key into a chat or terminal transcript.

### Port 8765 already in use

Use the controller's Status action. Avoid starting a second instance.

### Tunnel ready but ChatGPT tools fail

Check the tunnel process, /healthz, /readyz, correct tunnel selection in ChatGPT, connector enabled state, and local logs. Make a fresh ChatGPT tool call after a restart.

### MCP telemetry says not_observed

Make a fresh MCP tool call from ChatGPT. In the tested background restart, server_info and list_files worked even while the telemetry endpoint remained not_observed.

### Restarting through MCP disconnects ChatGPT

This is expected when stopping the tunnel that carries the MCP request. Use the desktop shortcut or local controller.

## 15. Security and operational notes

- The runtime key is stored as plaintext in the local Windows profile by user choice. Protect the Windows account and credential file.
- Never include the runtime key in documentation, project source, command-line arguments, Git history, screenshots, or chat.
- Keep the tunnel stopped when it is not needed.
- Read-only filesystem MCP tools can browse/read inside the project root; filesystem write/create tools remain restricted to workspace/. These are application-level path restrictions, not a separate Windows security boundary.
- The run_powershell tool runs with the signed-in Windows user's permissions and can access locations beyond the workspace. Use this private server only with trusted instructions and calls.
- Stopping the tunnel disconnects ChatGPT until the tunnel is started again.
- The controller terminates the tunnel-client process tree, including local MCP server child processes.

## 16. Setup completion checklist

- [x] Tunnel created in OpenAI Platform.
- [x] Restricted runtime key created with Tunnels Read + Use.
- [x] Runtime key saved locally outside the project.
- [x] tunnel-client v0.0.19 Windows AMD64 installed and checksum verified.
- [x] Python 3.12 virtual environment and MCP SDK installed.
- [x] Local stdio MCP server created.
- [x] tunnel-client profile generated.
- [x] tunnel-client doctor returned RESULT ok.
- [x] Local health endpoints returned HTTP 200.
- [x] Custom MCP connector added in ChatGPT Web.
- [x] Seven original MCP tools were tested; video analysis, adapted-video rendering, and source-link intake were added afterward.
- [x] Desktop start/stop/restart/status controller and shortcut created.
- [x] Background restart tested; fresh ChatGPT MCP calls succeeded afterwards.

**Current outcome:** The local Shorts Prototype MCP server is connected to ChatGPT Web through OpenAI Secure MCP Tunnel. Start it only when needed using the Shorts Prototype Tunnel desktop shortcut.
