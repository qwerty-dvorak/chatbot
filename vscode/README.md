# Barc Chat — VS Code Extension

Minimal chat client for the Barc Django LLM backend.

## Quick Start

1. Open VS Code, go to Extensions (Ctrl+Shift+X)
2. Click "..." → Install from VSIX → select `barc-chat-0.0.1.vsix`
3. Press `Ctrl+Shift+P` → `Barc: Set Server URL` → enter `http://localhost:8080`
4. Press `Ctrl+Shift+P` → `Barc: Open Chat`
5. Select a chat from the sidebar or create a new one

## Requirements

- Barc Django server running on the configured URL
- Server must be accessible from VS Code (same machine or network)

## Commands

| Command | Description |
|---------|-------------|
| `Barc: Set Server URL` | Configure the Django server URL |
| `Barc: Open Chat` | Open the chat panel |

## Notes

- The status bar shows `(✓) Barc` when connected, `(✗) Barc` when disconnected
- Streaming responses appear in real-time
- No authentication handling yet — server must allow requests or have session cookies
