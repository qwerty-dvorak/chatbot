import * as vscode from 'vscode';

let serverUrl = 'http://localhost:8080';
let statusBar: vscode.StatusBarItem;
let log: vscode.OutputChannel;

export function activate(context: vscode.ExtensionContext) {
  serverUrl = context.globalState.get('serverUrl', 'http://localhost:8080')!;
  log = vscode.window.createOutputChannel('Barc Chat');

  log.appendLine('activate: serverUrl=' + serverUrl);

  statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBar.command = 'barc.setUrl';
  context.subscriptions.push(statusBar);

  context.subscriptions.push(
    vscode.commands.registerCommand('barc.setUrl', async () => {
      const url = await vscode.window.showInputBox({ prompt: 'Barc server URL', value: serverUrl });
      if (url) {
        serverUrl = url.replace(/\/+$/, '');
        await context.globalState.update('serverUrl', serverUrl);
        log.appendLine('setUrl: ' + serverUrl);
        checkHealth();
      }
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('barc.openChat', () => {
      log.appendLine('openChat');
      ChatPanel.createOrShow();
    })
  );

  context.subscriptions.push(log);

  checkHealth();
  setInterval(checkHealth, 30000);
}

function checkHealth() {
  fetch(serverUrl + '/api/health/')
    .then(r => r.json())
    .then(() => {
      statusBar.text = '$(check) Barc';
      statusBar.tooltip = 'Connected: ' + serverUrl;
      statusBar.backgroundColor = undefined;
      statusBar.show();
    })
    .catch(() => {
      statusBar.text = '$(stop) Barc';
      statusBar.tooltip = 'Disconnected: ' + serverUrl;
      statusBar.backgroundColor = new vscode.ThemeColor('statusBarItem.errorBackground');
      statusBar.show();
    });
}

function api(path: string, opts?: RequestInit) {
  return fetch(serverUrl + path, { credentials: 'include', ...opts });
}

class ChatPanel {
  static pan: ChatPanel | undefined;
  private p: vscode.WebviewPanel;
  private d: vscode.Disposable[] = [];
  private ac: AbortController | undefined;
  private chatId = '';

  static createOrShow() {
    if (ChatPanel.pan) { ChatPanel.pan.p.reveal(vscode.ViewColumn.Beside); return; }
    ChatPanel.pan = new ChatPanel();
  }

  private constructor() {
    this.p = vscode.window.createWebviewPanel('barcChat', 'Barc Chat', vscode.ViewColumn.Beside, {
      enableScripts: true,
      retainContextWhenHidden: true,
    });
    const nonce = getNonce();
    this.p.webview.html = getHtml(nonce);
    this.p.onDidDispose(() => this.dispose(), null, this.d);
    this.p.webview.onDidReceiveMessage(m => {
      log.appendLine('msg from webview: ' + JSON.stringify(m));
      this.msg(m);
    }, null, this.d);
  }

  private async msg(m: any) {
    try {
      switch (m.type) {
        case 'chats':
          this.post({ type: 'chats', chats: await this.fetchChats() });
          break;
        case 'open':
          this.chatId = m.id;
          this.post({ type: 'chat', messages: await this.fetchChat(m.id) });
          break;
        case 'create':
          this.chatId = await this.createChat(m.title);
          if (this.chatId) {
            this.post({ type: 'opened', chatId: this.chatId });
          } else {
            this.post({ type: 'error', message: 'Failed to create chat' });
          }
          break;
        case 'send':
          await this.send(m.content);
          break;
        case 'cancel':
          this.ac?.abort();
          api('/chats/' + this.chatId + '/cancel/', { method: 'POST' }).catch(() => {});
          break;
        case 'log':
          log.appendLine('[webview] ' + m.message);
          break;
        default:
          log.appendLine('unknown msg type: ' + m.type);
      }
    } catch (e: any) {
      log.appendLine('msg error: ' + e.message);
      this.post({ type: 'error', message: e.message });
    }
  }

  private post(m: any) {
    log.appendLine('post to webview: ' + JSON.stringify(m).slice(0, 200));
    this.p.webview.postMessage(m);
  }

  private async fetchChats(): Promise<{ id: string; title: string }[]> {
    const text = await (await api('/chats/')).text();
    const chats: { id: string; title: string }[] = [];
    const re = /href="\/chats\/([^"/]+)\/"[^>]*>[\s\S]*?doc-title[^>]*>([^<]+)</g;
    let m;
    while ((m = re.exec(text)) !== null) {
      chats.push({ id: m[1], title: m[2].trim() });
    }
    log.appendLine('fetchChats: ' + chats.length + ' chats');
    return chats;
  }

  private async fetchChat(id: string): Promise<{ role: string; content: string }[]> {
    const text = await (await api('/chats/' + id + '/')).text();
    const msgs: { role: string; content: string }[] = [];
    const re = /data-message-role="(user|assistant)"[^>]*>[\s\S]*?(?:data-message-content|class="msg-content(?: msg-streaming)?")[^>]*>([\s\S]*?)<\/div>/g;
    let m;
    while ((m = re.exec(text)) !== null) {
      const content = m[2].replace(/<[^>]*>/g, '').trim();
      if (content) msgs.push({ role: m[1], content: content });
    }
    log.appendLine('fetchChat: ' + msgs.length + ' msgs for ' + id);
    return msgs;
  }

  private async createChat(title: string): Promise<string> {
    const fd = new URLSearchParams();
    fd.append('title', title);
    const res = await api('/chats/new/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: fd,
      redirect: 'manual',
    });
    const loc = res.headers.get('location') || '';
    const match = loc.match(/\/chats\/([^/]+)\//);
    log.appendLine('createChat: ' + (match ? match[1] : 'failed'));
    return match ? match[1] : '';
  }

  private async send(content: string) {
    if (!this.chatId) {
      const id = await this.createChat('VS Code Chat');
      if (!id) { this.post({ type: 'error', message: 'Failed to create chat' }); return; }
      this.chatId = id;
    }
    const fd = new URLSearchParams();
    fd.append('content', content);
    await api('/chats/' + this.chatId + '/', {
      method: 'POST',
      body: fd,
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      redirect: 'manual',
    });
    this.post({ type: 'clearInput' });
    await this.stream();
  }

  private async stream() {
    this.ac = new AbortController();
    this.post({ type: 'streamStart' });
    try {
      const res = await api('/chats/' + this.chatId + '/stream/', { signal: this.ac.signal });
      const reader = res.body!.getReader();
      const dec = new TextDecoder();
      let buf = '';
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop() || '';
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const data = JSON.parse(line.slice(6));
            this.post({ type: 'streamData', data: data });
            if (data.type === 'done' || data.type === 'cancelled' || data.type === 'error') break;
          } catch (_) {}
        }
      }
    } catch (e: any) {
      if (e.name !== 'AbortError') {
        log.appendLine('stream err: ' + e.message);
        this.post({ type: 'error', message: e.message });
      }
    }
    this.post({ type: 'streamEnd' });
    this.ac = undefined;
  }

  private dispose() {
    if (this.ac) this.ac.abort();
    ChatPanel.pan = undefined;
    this.p.dispose();
    while (this.d.length) { this.d.pop()!.dispose(); }
  }
}

function getNonce(): string {
  let text = '';
  const possible = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  for (let i = 0; i < 64; i++) text += possible.charAt(Math.floor(Math.random() * possible.length));
  return text;
}

function escHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function getHtml(nonce: string): string {
  return '<!DOCTYPE html>\n<html><head>\n<meta charset="utf-8">\n<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'nonce-' + nonce + '\'; style-src \'unsafe-inline\';">\n<style>\n'
+ '*{margin:0;padding:0;box-sizing:border-box}\n'
+ ':root{--bg:#1e1e2e;--surf:#2a2a3a;--text:#cdd6f4;--text2:#6c7086;--accent:#0071e3;--user:#313244;--border:#3b3b4d}\n'
+ 'body{font:13px/1.5 -apple-system,sans-serif;background:var(--bg);color:var(--text);height:100vh;display:flex;flex-direction:column}\n'
+ '#layout{display:flex;flex:1;min-height:0}\n'
+ '#sidebar{width:200px;background:var(--surf);border-right:1px solid var(--border);display:flex;flex-direction:column;flex-shrink:0}\n'
+ '#sidebar h3{padding:10px 12px;font-size:11px;text-transform:uppercase;color:var(--text2);letter-spacing:1px}\n'
+ '#newchat{display:flex;padding:6px 8px;gap:4px;border-bottom:1px solid var(--border);align-items:center}\n'
+ '#newchat input{flex:1;min-width:0;background:var(--bg);border:1px solid var(--border);border-radius:4px;padding:5px 8px;color:var(--text);font:12px inherit;outline:none}\n'
+ '#newchat input:focus{border-color:var(--accent)}\n'
+ '#createbtn{width:26px;height:26px;flex-shrink:0;background:var(--accent);color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:16px;line-height:1;display:flex;align-items:center;justify-content:center}\n'
+ '#createbtn:hover{opacity:.85}\n'
+ '#chatlist{flex:1;overflow-y:auto}\n'
+ '.chat-item{padding:8px 12px;cursor:pointer;border-bottom:1px solid var(--border);display:flex;align-items:center}\n'
+ '.chat-item:hover{background:#333}\n'
+ '.chat-item.active{background:var(--accent);color:#fff}\n'
+ '.chat-item .title{font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}\n'
+ '#main{flex:1;display:flex;flex-direction:column;min-width:0}\n'
+ '#toolbar{display:flex;gap:4px;padding:6px 12px;background:var(--surf);border-bottom:1px solid var(--border)}\n'
+ '#toolbar button{background:none;border:1px solid var(--border);color:var(--text);padding:3px 10px;border-radius:4px;cursor:pointer;font-size:11px}\n'
+ '#toolbar button:hover{background:#333}\n'
+ '#msgs{flex:1;overflow-y:auto;padding:12px 16px}\n'
+ '.msg{margin-bottom:14px;max-width:85%}\n'
+ '.msg-user{margin-left:auto}\n'
+ '.msg-user .bubble{background:var(--accent);color:#fff;padding:8px 12px;border-radius:10px 10px 3px 10px}\n'
+ '.msg-assistant .bubble{background:var(--user);padding:8px 12px;border-radius:10px 10px 10px 3px;white-space:pre-wrap;word-break:break-word}\n'
+ '.label{font-size:10px;color:var(--text2);margin-bottom:3px;text-transform:uppercase;letter-spacing:.5px}\n'
+ '.msg-user .label{text-align:right}\n'
+ '#empty{display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;color:var(--text2);gap:6px;font-size:12px}\n'
+ '#streaming{display:none;padding:0 16px 12px;border-top:1px solid var(--border);background:var(--bg)}\n'
+ '#streaming .label{font-size:10px;color:var(--text2);margin-bottom:3px;margin-top:8px;text-transform:uppercase;letter-spacing:.5px}\n'
+ '#streaming .bubble{background:var(--user);padding:8px 12px;border-radius:10px;white-space:pre-wrap;word-break:break-word;font-size:13px;min-height:20px}\n'
+ '#streaming .cursor{display:inline-block;width:2px;height:14px;background:var(--accent);animation:blink .8s infinite;margin-left:1px;vertical-align:text-bottom}\n'
+ '@keyframes blink{50%{opacity:0}}\n'
+ '#bottom{padding:8px 12px;background:var(--surf);border-top:1px solid var(--border);display:flex;gap:6px;align-items:flex-end}\n'
+ '#input{flex:1;background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:7px 10px;color:var(--text);font:13px/1.4 inherit;resize:none;outline:none;max-height:120px}\n'
+ '#input:focus{border-color:var(--accent)}\n'
+ '#send{background:var(--accent);color:#fff;border:none;border-radius:6px;padding:7px 14px;cursor:pointer;font-size:12px;font-weight:600;white-space:nowrap}\n'
+ '#send:disabled{opacity:.4;cursor:default}\n'
+ '#send:hover:not(:disabled){opacity:.85}\n'
+ '</style></head><body>\n'
+ '<div id="layout">\n'
+ '<div id="sidebar">\n<h3>Chats</h3>\n'
+ '<div id="newchat">\n<input id="newtitle" placeholder="Title" value="New Chat">\n<button id="createbtn">+</button>\n</div>\n'
+ '<div id="chatlist"></div>\n</div>\n'
+ '<div id="main">\n'
+ '<div id="toolbar"><button id="refreshbtn">Refresh</button><button id="stopbtn" style="display:none">Stop</button></div>\n'
+ '<div id="msgs"><div id="empty">Select a chat or create one</div></div>\n'
+ '<div id="streaming"><div class="label">Assistant</div><div class="bubble" id="streamContent"><span class="cursor"></span></div></div>\n'
+ '<div id="bottom"><textarea id="input" rows="1" placeholder="Message..."></textarea><button id="send">Send</button></div>\n'
+ '</div></div>\n'
+ '<script nonce="' + nonce + '">\n'
+ '(function(){\n'
+ 'try{\n'
+ 'var vsc=acquireVsCodeApi();\n'
+ 'var msgs=[];\n'
+ 'function $(id){return document.getElementById(id)}\n'
+ 'function log(m){vsc.postMessage({type:"log",message:m})}\n'
+ 'log("script started");\n'
+ '$("createbtn").addEventListener("click",function(){log("create click");vsc.postMessage({type:"create",title:$("newtitle").value||"New Chat"})});\n'
+ '$("refreshbtn").addEventListener("click",function(){log("refresh click");vsc.postMessage({type:"chats"})});\n'
+ '$("send").addEventListener("click",doSend);\n'
+ '$("stopbtn").addEventListener("click",function(){log("stop click");vsc.postMessage({type:"cancel"})});\n'
+ '$("input").addEventListener("keydown",function(e){if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();doSend()}});\n'
+ 'function doSend(){var v=$("input").value.trim();if(!v)return;log("send click");vsc.postMessage({type:"send",content:v})}\n'
+ 'window.addEventListener("message",function(e){var m=e.data;try{switch(m.type){\n'
+ 'case"chats":renderChats(m.chats);break;\n'
+ 'case"chat":msgs=m.messages;renderMsgs();break;\n'
+ 'case"opened":$("chatlist").querySelectorAll(".chat-item").forEach(function(x){x.classList.remove("active")});break;\n'
+ 'case"clearInput":$("input").value="";break;\n'
+ 'case"streamStart":$("streaming").style.display="block";$("streamContent").textContent="";$("streamContent").appendChild(document.createElement("span")).className="cursor";$("send").disabled=true;$("stopbtn").style.display="";break;\n'
+ 'case"streamData":var d=m.data;if(d.type==="token"){var sc=$("streamContent");if(sc.lastElementChild&&sc.lastElementChild.className==="cursor"){sc.lastChild.textContent+=d.content}else{sc.textContent+=d.content}}else if(d.type==="done"||d.type==="cancelled"){var t=$("streamContent").textContent;if(t)msgs.push({role:"assistant",content:t});$("streaming").style.display="none";$("streamContent").textContent="";renderMsgs()}else if(d.type==="error"){$("streamContent").textContent="Error: "+d.content};break;\n'
+ 'case"streamEnd":$("send").disabled=false;$("stopbtn").style.display="none";break;\n'
+ 'case"error":log("ext err: "+m.message);break\n'
+ '}}catch(e){log("msg err: "+e.message)}});\n'
+ 'function renderChats(chats){var el=$("chatlist");el.innerHTML="";chats.forEach(function(c){var d=document.createElement("div");d.className="chat-item";d.dataset.id=c.id;var t=document.createElement("div");t.className="title";t.textContent=c.title;d.appendChild(t);d.addEventListener("click",function(){el.querySelectorAll(".chat-item").forEach(function(x){x.classList.remove("active")});this.classList.add("active");vsc.postMessage({type:"open",id:this.dataset.id})});el.appendChild(d)});log("renderChats: "+chats.length)}\n'
+ 'function renderMsgs(){var el=$("msgs");el.innerHTML="";msgs.forEach(function(m){var d=document.createElement("div");d.className="msg msg-"+m.role;var l=document.createElement("div");l.className="label";l.textContent=m.role==="user"?"You":"Assistant";d.appendChild(l);var b=document.createElement("div");b.className="bubble";b.textContent=m.content;d.appendChild(b);el.appendChild(d)});el.scrollTop=el.scrollHeight;log("renderMsgs: "+msgs.length)}\n'
+ 'log("sending chats request");\n'
+ 'vsc.postMessage({type:"chats"});\n'
+ '}catch(e){var vsc2=acquireVsCodeApi();vsc2.postMessage({type:"log",message:"init err: "+e.message})}\n'
+ '})();\n'
+ '</script>\n</body></html>';
}
