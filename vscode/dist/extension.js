"use strict";var x=Object.create;var g=Object.defineProperty;var y=Object.getOwnPropertyDescriptor;var w=Object.getOwnPropertyNames;var C=Object.getPrototypeOf,k=Object.prototype.hasOwnProperty;var S=(t,e)=>{for(var n in e)g(t,n,{get:e[n],enumerable:!0})},f=(t,e,n,r)=>{if(e&&typeof e=="object"||typeof e=="function")for(let a of w(e))!k.call(t,a)&&a!==n&&g(t,a,{get:()=>e[a],enumerable:!(r=y(e,a))||r.enumerable});return t};var E=(t,e,n)=>(n=t!=null?x(C(t)):{},f(e||!t||!t.__esModule?g(n,"default",{value:t,enumerable:!0}):n,t)),L=t=>f(g({},"__esModule",{value:!0}),t);var B={};S(B,{activate:()=>$});module.exports=L(B);var s=E(require("vscode")),d="http://localhost:8080",o,i;function $(t){d=t.globalState.get("serverUrl","http://localhost:8080"),i=s.window.createOutputChannel("Barc Chat"),i.appendLine("activate: serverUrl="+d),o=s.window.createStatusBarItem(s.StatusBarAlignment.Right,100),o.command="barc.setUrl",t.subscriptions.push(o),t.subscriptions.push(s.commands.registerCommand("barc.setUrl",async()=>{let e=await s.window.showInputBox({prompt:"Barc server URL",value:d});e&&(d=e.replace(/\/+$/,""),await t.globalState.update("serverUrl",d),i.appendLine("setUrl: "+d),u())})),t.subscriptions.push(s.commands.registerCommand("barc.openChat",()=>{i.appendLine("openChat"),m.createOrShow()})),t.subscriptions.push(i),u(),setInterval(u,3e4)}function u(){fetch(d+"/api/health/").then(t=>t.json()).then(()=>{o.text="$(check) Barc",o.tooltip="Connected: "+d,o.backgroundColor=void 0,o.show()}).catch(()=>{o.text="$(stop) Barc",o.tooltip="Disconnected: "+d,o.backgroundColor=new s.ThemeColor("statusBarItem.errorBackground"),o.show()})}function l(t,e){return fetch(d+t,{credentials:"include",...e})}var m=class t{static pan;p;d=[];ac;chatId="";static createOrShow(){if(t.pan){t.pan.p.reveal(s.ViewColumn.Beside);return}t.pan=new t}constructor(){this.p=s.window.createWebviewPanel("barcChat","Barc Chat",s.ViewColumn.Beside,{enableScripts:!0,retainContextWhenHidden:!0});let e=I();this.p.webview.html=M(e),this.p.onDidDispose(()=>this.dispose(),null,this.d),this.p.webview.onDidReceiveMessage(n=>{i.appendLine("msg from webview: "+JSON.stringify(n)),this.msg(n)},null,this.d)}async msg(e){try{switch(e.type){case"chats":this.post({type:"chats",chats:await this.fetchChats()});break;case"open":this.chatId=e.id,this.post({type:"chat",messages:await this.fetchChat(e.id)});break;case"create":this.chatId=await this.createChat(e.title),this.chatId?this.post({type:"opened",chatId:this.chatId}):this.post({type:"error",message:"Failed to create chat"});break;case"send":await this.send(e.content);break;case"cancel":this.ac?.abort(),l("/chats/"+this.chatId+"/cancel/",{method:"POST"}).catch(()=>{});break;case"log":i.appendLine("[webview] "+e.message);break;default:i.appendLine("unknown msg type: "+e.type)}}catch(n){i.appendLine("msg error: "+n.message),this.post({type:"error",message:n.message})}}post(e){i.appendLine("post to webview: "+JSON.stringify(e).slice(0,200)),this.p.webview.postMessage(e)}async fetchChats(){let e=await(await l("/chats/")).text(),n=[],r=/href="\/chats\/([^"/]+)\/"[^>]*>[\s\S]*?doc-title[^>]*>([^<]+)</g,a;for(;(a=r.exec(e))!==null;)n.push({id:a[1],title:a[2].trim()});return i.appendLine("fetchChats: "+n.length+" chats"),n}async fetchChat(e){let n=await(await l("/chats/"+e+"/")).text(),r=[],a=/data-message-role="(user|assistant)"[^>]*>[\s\S]*?(?:data-message-content|class="msg-content(?: msg-streaming)?")[^>]*>([\s\S]*?)<\/div>/g,c;for(;(c=a.exec(n))!==null;){let h=c[2].replace(/<[^>]*>/g,"").trim();h&&r.push({role:c[1],content:h})}return i.appendLine("fetchChat: "+r.length+" msgs for "+e),r}async createChat(e){let n=new URLSearchParams;n.append("title",e);let c=((await l("/chats/new/",{method:"POST",headers:{"Content-Type":"application/x-www-form-urlencoded"},body:n,redirect:"manual"})).headers.get("location")||"").match(/\/chats\/([^/]+)\//);return i.appendLine("createChat: "+(c?c[1]:"failed")),c?c[1]:""}async send(e){if(!this.chatId){let r=await this.createChat("VS Code Chat");if(!r){this.post({type:"error",message:"Failed to create chat"});return}this.chatId=r}let n=new URLSearchParams;n.append("content",e),await l("/chats/"+this.chatId+"/",{method:"POST",body:n,headers:{"Content-Type":"application/x-www-form-urlencoded"},redirect:"manual"}),this.post({type:"clearInput"}),await this.stream()}async stream(){this.ac=new AbortController,this.post({type:"streamStart"});try{let n=(await l("/chats/"+this.chatId+"/stream/",{signal:this.ac.signal})).body.getReader(),r=new TextDecoder,a="";for(;;){let{done:c,value:h}=await n.read();if(c)break;a+=r.decode(h,{stream:!0});let b=a.split(`
`);a=b.pop()||"";for(let v of b)if(v.startsWith("data: "))try{let p=JSON.parse(v.slice(6));if(this.post({type:"streamData",data:p}),p.type==="done"||p.type==="cancelled"||p.type==="error")break}catch{}}}catch(e){e.name!=="AbortError"&&(i.appendLine("stream err: "+e.message),this.post({type:"error",message:e.message}))}this.post({type:"streamEnd"}),this.ac=void 0}dispose(){for(this.ac&&this.ac.abort(),t.pan=void 0,this.p.dispose();this.d.length;)this.d.pop().dispose()}};function I(){let t="",e="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";for(let n=0;n<64;n++)t+=e.charAt(Math.floor(Math.random()*e.length));return t}function M(t){return`<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-`+t+`'; style-src 'unsafe-inline';">
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#1e1e2e;--surf:#2a2a3a;--text:#cdd6f4;--text2:#6c7086;--accent:#0071e3;--user:#313244;--border:#3b3b4d}
body{font:13px/1.5 -apple-system,sans-serif;background:var(--bg);color:var(--text);height:100vh;display:flex;flex-direction:column}
#layout{display:flex;flex:1;min-height:0}
#sidebar{width:200px;background:var(--surf);border-right:1px solid var(--border);display:flex;flex-direction:column;flex-shrink:0}
#sidebar h3{padding:10px 12px;font-size:11px;text-transform:uppercase;color:var(--text2);letter-spacing:1px}
#newchat{display:flex;padding:6px 8px;gap:4px;border-bottom:1px solid var(--border);align-items:center}
#newchat input{flex:1;min-width:0;background:var(--bg);border:1px solid var(--border);border-radius:4px;padding:5px 8px;color:var(--text);font:12px inherit;outline:none}
#newchat input:focus{border-color:var(--accent)}
#createbtn{width:26px;height:26px;flex-shrink:0;background:var(--accent);color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:16px;line-height:1;display:flex;align-items:center;justify-content:center}
#createbtn:hover{opacity:.85}
#chatlist{flex:1;overflow-y:auto}
.chat-item{padding:8px 12px;cursor:pointer;border-bottom:1px solid var(--border);display:flex;align-items:center}
.chat-item:hover{background:#333}
.chat-item.active{background:var(--accent);color:#fff}
.chat-item .title{font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#main{flex:1;display:flex;flex-direction:column;min-width:0}
#toolbar{display:flex;gap:4px;padding:6px 12px;background:var(--surf);border-bottom:1px solid var(--border)}
#toolbar button{background:none;border:1px solid var(--border);color:var(--text);padding:3px 10px;border-radius:4px;cursor:pointer;font-size:11px}
#toolbar button:hover{background:#333}
#msgs{flex:1;overflow-y:auto;padding:12px 16px}
.msg{margin-bottom:14px;max-width:85%}
.msg-user{margin-left:auto}
.msg-user .bubble{background:var(--accent);color:#fff;padding:8px 12px;border-radius:10px 10px 3px 10px}
.msg-assistant .bubble{background:var(--user);padding:8px 12px;border-radius:10px 10px 10px 3px;white-space:pre-wrap;word-break:break-word}
.label{font-size:10px;color:var(--text2);margin-bottom:3px;text-transform:uppercase;letter-spacing:.5px}
.msg-user .label{text-align:right}
#empty{display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;color:var(--text2);gap:6px;font-size:12px}
#streaming{display:none;padding:0 16px 12px;border-top:1px solid var(--border);background:var(--bg)}
#streaming .label{font-size:10px;color:var(--text2);margin-bottom:3px;margin-top:8px;text-transform:uppercase;letter-spacing:.5px}
#streaming .bubble{background:var(--user);padding:8px 12px;border-radius:10px;white-space:pre-wrap;word-break:break-word;font-size:13px;min-height:20px}
#streaming .cursor{display:inline-block;width:2px;height:14px;background:var(--accent);animation:blink .8s infinite;margin-left:1px;vertical-align:text-bottom}
@keyframes blink{50%{opacity:0}}
#bottom{padding:8px 12px;background:var(--surf);border-top:1px solid var(--border);display:flex;gap:6px;align-items:flex-end}
#input{flex:1;background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:7px 10px;color:var(--text);font:13px/1.4 inherit;resize:none;outline:none;max-height:120px}
#input:focus{border-color:var(--accent)}
#send{background:var(--accent);color:#fff;border:none;border-radius:6px;padding:7px 14px;cursor:pointer;font-size:12px;font-weight:600;white-space:nowrap}
#send:disabled{opacity:.4;cursor:default}
#send:hover:not(:disabled){opacity:.85}
</style></head><body>
<div id="layout">
<div id="sidebar">
<h3>Chats</h3>
<div id="newchat">
<input id="newtitle" placeholder="Title" value="New Chat">
<button id="createbtn">+</button>
</div>
<div id="chatlist"></div>
</div>
<div id="main">
<div id="toolbar"><button id="refreshbtn">Refresh</button><button id="stopbtn" style="display:none">Stop</button></div>
<div id="msgs"><div id="empty">Select a chat or create one</div></div>
<div id="streaming"><div class="label">Assistant</div><div class="bubble" id="streamContent"><span class="cursor"></span></div></div>
<div id="bottom"><textarea id="input" rows="1" placeholder="Message..."></textarea><button id="send">Send</button></div>
</div></div>
<script nonce="`+t+`">
(function(){
try{
var vsc=acquireVsCodeApi();
var msgs=[];
function $(id){return document.getElementById(id)}
function log(m){vsc.postMessage({type:"log",message:m})}
log("script started");
$("createbtn").addEventListener("click",function(){log("create click");vsc.postMessage({type:"create",title:$("newtitle").value||"New Chat"})});
$("refreshbtn").addEventListener("click",function(){log("refresh click");vsc.postMessage({type:"chats"})});
$("send").addEventListener("click",doSend);
$("stopbtn").addEventListener("click",function(){log("stop click");vsc.postMessage({type:"cancel"})});
$("input").addEventListener("keydown",function(e){if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();doSend()}});
function doSend(){var v=$("input").value.trim();if(!v)return;log("send click");vsc.postMessage({type:"send",content:v})}
window.addEventListener("message",function(e){var m=e.data;try{switch(m.type){
case"chats":renderChats(m.chats);break;
case"chat":msgs=m.messages;renderMsgs();break;
case"opened":$("chatlist").querySelectorAll(".chat-item").forEach(function(x){x.classList.remove("active")});break;
case"clearInput":$("input").value="";break;
case"streamStart":$("streaming").style.display="block";$("streamContent").textContent="";$("streamContent").appendChild(document.createElement("span")).className="cursor";$("send").disabled=true;$("stopbtn").style.display="";break;
case"streamData":var d=m.data;if(d.type==="token"){var sc=$("streamContent");if(sc.lastElementChild&&sc.lastElementChild.className==="cursor"){sc.lastChild.textContent+=d.content}else{sc.textContent+=d.content}}else if(d.type==="done"||d.type==="cancelled"){var t=$("streamContent").textContent;if(t)msgs.push({role:"assistant",content:t});$("streaming").style.display="none";$("streamContent").textContent="";renderMsgs()}else if(d.type==="error"){$("streamContent").textContent="Error: "+d.content};break;
case"streamEnd":$("send").disabled=false;$("stopbtn").style.display="none";break;
case"error":log("ext err: "+m.message);break
}}catch(e){log("msg err: "+e.message)}});
function renderChats(chats){var el=$("chatlist");el.innerHTML="";chats.forEach(function(c){var d=document.createElement("div");d.className="chat-item";d.dataset.id=c.id;var t=document.createElement("div");t.className="title";t.textContent=c.title;d.appendChild(t);d.addEventListener("click",function(){el.querySelectorAll(".chat-item").forEach(function(x){x.classList.remove("active")});this.classList.add("active");vsc.postMessage({type:"open",id:this.dataset.id})});el.appendChild(d)});log("renderChats: "+chats.length)}
function renderMsgs(){var el=$("msgs");el.innerHTML="";msgs.forEach(function(m){var d=document.createElement("div");d.className="msg msg-"+m.role;var l=document.createElement("div");l.className="label";l.textContent=m.role==="user"?"You":"Assistant";d.appendChild(l);var b=document.createElement("div");b.className="bubble";b.textContent=m.content;d.appendChild(b);el.appendChild(d)});el.scrollTop=el.scrollHeight;log("renderMsgs: "+msgs.length)}
log("sending chats request");
vsc.postMessage({type:"chats"});
}catch(e){var vsc2=acquireVsCodeApi();vsc2.postMessage({type:"log",message:"init err: "+e.message})}
})();
</script>
</body></html>`}0&&(module.exports={activate});
