# Barc Chat VS Code Extension — Usage

## Install

1. Open VS Code
2. `Ctrl+Shift+X` to open Extensions
3. Click `...` (Views and More Actions) → `Install from VSIX...`
4. Select `askbarc-0.0.1.vsix`

## Setup

1. `Ctrl+Shift+P` → `Barc: Set Server URL`
2. Enter your Django server URL (default: `http://localhost:8080`)
3. Status bar shows `✓ Barc` when connected, `✗ Barc` when disconnected

## Chat

1. `Ctrl+Shift+P` → `Barc: Open Chat`
2. Chat panel opens on the side
3. Left sidebar shows existing chats (click to open)
4. Type title → `+` to create a new chat
5. Type message → Enter or click Send
6. Streaming response appears in real-time
7. Click Stop button (during streaming) to cancel

## Notes

- The extension proxies all requests through VS Code (no CORS issues)
- Authentication: the Django server must allow requests (no auth or session cookie)
- Server must be accessible from VS Code — localhost works when server runs locally

## Files

| File | Purpose |
|------|---------|
| `askbarc-0.0.1.vsix` | Packaged extension (install in VS Code) |
| `src/extension.ts` | Extension source code |
| `dist/extension.js` | Compiled bundle |
| `README.md` | Extension marketplace README |

## Build from source

```bash
cd vscode
pnpm install
pnpm run package
npx @vscode/vsce package
```
