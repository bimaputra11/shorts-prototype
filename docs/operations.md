# Local Operations, Tunnel and Security

## Tunnel lifecycle
The tunnel is intentionally not configured to start automatically with Windows. Start it only when ChatGPT needs access to the local Shorts project.

Use Shorts Tunnel.cmd or the desktop shortcut to open the controller menu. The script is scripts/shorts-tunnel.ps1.

Direct PowerShell commands from the project root:
    .\scripts\shorts-tunnel.ps1 -Action start
    .\scripts\shorts-tunnel.ps1 -Action stop
    .\scripts\shorts-tunnel.ps1 -Action restart
    .\scripts\shorts-tunnel.ps1 -Action status

The controller reads the runtime key from %LOCALAPPDATA%\shorts-prototype\secrets\runtime-key.txt. The key is outside the repository. Never copy it into source files, documentation, Git history, screenshots or chat.

The tunnel profile is stored under %APPDATA%\tunnel-client\shorts-prototype.yaml. Logs and runtime state are stored under %LOCALAPPDATA%\shorts-prototype\logs and %LOCALAPPDATA%\shorts-prototype\runtime.

## Health checks
With the tunnel running:
    Invoke-WebRequest http://127.0.0.1:8765/healthz
    Invoke-WebRequest http://127.0.0.1:8765/readyz
    Invoke-WebRequest http://127.0.0.1:8765/health/mcp

Expected basic responses are live from /healthz and ready from /readyz. The MCP status endpoint reports whether initialization and tool-list handshake have been observed. It may report not_observed until discovery has occurred.

Stopping the tunnel disconnects ChatGPT from the local MCP server. Start it again in a later session before using the connector.

For detailed setup, see secure-mcp-connection-setup.md.

## Security boundaries
- Read-only filesystem MCP tools can browse/read inside the project root. Their paths are relative to workspace/; .. can reach the project root. Absolute paths and paths outside the project root are rejected.
- Filesystem write tools are restricted to workspace/.
- PowerShell runs as the signed-in Windows user and is not sandboxed to workspace/. Commands can access other resources available to that account.
- Keep the tunnel private and connect it only to a trusted ChatGPT account/workspace.
- The server removes tunnel/API credential variables from its own and PowerShell child-process environments.
- Protect the Windows account and runtime-key file. Never disclose credentials.
- Do not put credentials or private reaction footage in public repositories.
