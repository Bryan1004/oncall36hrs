const $ = selector => document.querySelector(selector);
const el = (tag, text, cls) => {const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls; return n;};
let groupPlatform=(()=>{try{const v=localStorage.getItem('group-platform');if(v==='whatsapp'||v==='telegram')return v;}catch{}return 'whatsapp';})(),groupSaving=false,samplePlatform=(()=>{try{const v=localStorage.getItem('sample-platform');if(v==='whatsapp'||v==='telegram')return v;}catch{}return 'whatsapp';})(),cachedSamples=[],connPlatform=(()=>{try{const v=localStorage.getItem('conn-platform');if(v==='whatsapp'||v==='telegram')return v;}catch{}return 'whatsapp';})(),lastTgLogin=null;
const groupDrafts={};
function groupDraft(platform){const current=state.group_settings?.[platform]||{};return groupDrafts[platform]||(groupDrafts[platform]={keywords:[...(current.keywords||[])],baseKeywords:[...(current.keywords||[])],revision:current.revision||0,selections:{}});}
function groupDirty(platform){const d=groupDrafts[platform];return !!d&&(Object.keys(d.selections).length>0||JSON.stringify(d.keywords)!==JSON.stringify(d.baseKeywords));}
let intervalDirty=false,quietDirty=false;
let state, page='inbox', toastTimer, loading=false;
const names={home:'在家长响铃',away:'外出通知',paused:'暂停提醒'};
const statusNames={connected:'已连接',connecting:'连接中',reconnecting:'连接中',needs_scan:'等待扫码',logged_out:'已退出，请重新关联',not_configured:'未配置',needs_login:'等待登录',waiting:'等待连接',offline:'连接中断',disconnected:'连接中断',error:'连接异常',message_error:'消息处理异常'};
const date=t=>new Date(t*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
async function api(route,data){const r=await fetch(route,{method:data===undefined?'GET':'POST',headers:{'Content-Type':'application/json','X-Requested-With':'oncall'},...(data!==undefined?{body:JSON.stringify(data)}:{})});let result;try{result=await r.json();}catch{throw Error('服务器响应异常');}if(!r.ok){const error=Error(result.retry_after?`请等待 ${result.retry_after} 秒后重试`:typeof result.detail==='string'?result.detail:`请求失败 (${r.status})`);error.login=result.login;throw error;}return result;}
function toast(text){$('#toast').textContent=text;$('#toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').hidden=true,3500);}
function showError(error){$('#error').textContent=error.message;$('#error').hidden=false;}
async function action(fn){try{$('#error').hidden=true;await fn();await refresh();}catch(error){showError(error);}}
function empty(target,title,desc){target.replaceChildren();const box=el('div',undefined,'empty');box.append(el('div','◌','symbol'),el('h3',title),el('p',desc));target.append(box);}
function alertCard(a,pending){const card=el('article',undefined,'alert-card'), icon=el('div',a.platform==='whatsapp'?'WA':a.platform==='telegram'?'TG':'◎','platform-icon'), content=el('div',undefined,'alert-content'),meta=el('div',undefined,'meta');meta.append(el('strong',a.title),el('span',a.reason==='reply'?'回复了你':'提到了你'),el('span',date(a.created)));content.append(meta,el('div',a.text,'message'),el('div',a.sender,'subtle'));card.append(icon,content);if(pending){const b=el('button','✓ 已收到','secondary');b.onclick=()=>action(async()=>{b.disabled=true;await api('/api/ack',{ids:[a.id]});toast('已确认，停止这条提醒');});card.append(b);}return card;}
function render(){
 const pending=state.alerts.filter(a=>a.status==='pending');
 renderBadge();
 document.querySelectorAll('[data-mode]').forEach(b=>{b.classList.toggle('selected',b.dataset.mode===state.mode);b.setAttribute('aria-pressed',String(b.dataset.mode===state.mode));});
 $('#mode-note').textContent=state.mode==='paused'?'已暂停发送，消息仍会进入收件箱。恢复后继续提醒待确认消息。':state.mode==='home'?`每 ${state.call_interval/60} 分钟合并发送一次 Bark 长响铃通知，直到确认或暂停。`:`每 ${state.push_interval/60} 分钟通知一次；手机锁屏时由手表接收，使用中由手机提醒。`;
 $('#pending-count').textContent=pending.length;$('#pending-chip').textContent=pending.length;for(const [platform,id] of [['whatsapp','ws'],['telegram','tg']])$('#'+id+'-group-count').textContent=state.groups.filter(g=>g.enabled&&g.platform===platform).length;
 const online=Object.values(state.connections).filter(c=>c.state==='connected').length;$('#connection-count').textContent=online+' / 2';$('#connection-note').textContent=online===2?'两个平台均在线':'请查看连接与设置';
 $('#demo').hidden=state.delivery_enabled;$('#ack-all').disabled=!pending.length;
 const inbox=$('#alerts');inbox.replaceChildren();if(!pending.length)empty(inbox,'现在，没有需要处理的提醒','连接账号并选择群组，或添加一条演练消息。');else pending.forEach(a=>inbox.append(alertCard(a,true)));
 const history=$('#history');history.replaceChildren();state.alerts.filter(a=>a.status!=='pending').slice(0,20).forEach(a=>history.append(alertCard(a,false)));
 const logs=$('#deliveries');logs.replaceChildren();if(!state.deliveries.length){logs.append(el('div','还没有发送记录。','log'));}for(const d of state.deliveries){const row=el('div',undefined,'log');row.append(el('span',date(d.created)+' · '+names[d.channel]),el('div',(d.status==='simulated'?'演练成功':d.status==='accepted'?'接口已接受':'发送失败')+(d.detail?' · '+d.detail:''),d.status==='failed'?'failed':''));logs.append(row);}
 renderGroups();renderConnections();
 renderQuietHours();
 if(!intervalDirty)$('#call-interval').value=state.call_interval;
 if(!intervalDirty)$('#push-interval').value=state.push_interval;
}
function updateGroupActions(){
 const dirty=groupDirty(groupPlatform);
 $('#group-draft-note').textContent=groupSaving?'正在保存…':dirty?'● 有未保存修改':'';
 $('#group-draft-note').title=dirty?'列表与数量为预览，需保存后生效；保存取消勾选会停止该群的待处理提醒。':'';
 $('#group-save').disabled=groupSaving||!dirty;$('#group-reset').disabled=groupSaving||!dirty;
}
function renderGroups(){
 if(!groupDirty(groupPlatform)&&!groupSaving)delete groupDrafts[groupPlatform];
 const draft=groupDraft(groupPlatform),query=$('#group-search').value.trim().toLowerCase();
 const isHidden=(g,keywords=draft.keywords)=>keywords.some(k=>g.title.toLowerCase().includes(k.toLowerCase()));
 for(const [platform,id]of [['whatsapp','ws'],['telegram','tg']]){
  if(platform!==groupPlatform&&!groupDirty(platform)&&!groupSaving)delete groupDrafts[platform];
  const keywords=groupDraft(platform).keywords;
  $('#'+id+'-tab-count').textContent=state.groups.filter(g=>g.platform===platform&&!isHidden(g,keywords)).length;
 }
 document.querySelectorAll('[data-group-platform]').forEach(b=>{const active=b.dataset.groupPlatform===groupPlatform;b.setAttribute('aria-selected',String(active));b.tabIndex=active?0:-1;});
 $('#group-tab-panel').setAttribute('aria-labelledby','tab-'+groupPlatform);
 const all=state.groups.filter(g=>g.platform===groupPlatform),hidden=all.filter(g=>isHidden(g)),visible=all.filter(g=>!isHidden(g)&&g.title.toLowerCase().includes(query));
 $('#group-results').textContent=`未隐藏 ${all.length-hidden.length} 个群 · 当前列表 ${visible.length} 个 · 已生效监控 ${all.filter(g=>g.enabled).length} 个`;
 updateGroupActions();
 for(const id of ['group-select-all','group-clear-all'])$('#'+id).disabled=groupSaving||!visible.length;
 $('#group-keyword').disabled=groupSaving;$('#group-filter-form button').disabled=groupSaving;
 const chips=$('#group-keywords');chips.replaceChildren();for(const keyword of draft.keywords){const button=el('button',keyword+' ×','keyword-chip');button.type='button';button.disabled=groupSaving;button.setAttribute('aria-label','移除隐藏关键词：'+keyword);button.onclick=()=>{groupDraft(groupPlatform).keywords=groupDraft(groupPlatform).keywords.filter(k=>k!==keyword);renderGroups();};chips.append(button);}
 const makeRow=g=>{const row=el('label',undefined,'group-card'),check=el('input');check.type='checkbox';check.checked=Object.hasOwn(draft.selections,g.id)?draft.selections[g.id]:!!g.enabled;check.disabled=groupSaving;check.onchange=()=>{const d=groupDraft(groupPlatform);if(check.checked===!!g.enabled)delete d.selections[g.id];else d.selections[g.id]=check.checked;updateGroupActions();};const text=el('span',g.title);text.append(el('small',(g.platform==='whatsapp'?'WHATSAPP BUSINESS':'TELEGRAM')+(isHidden(g)?' · 已隐藏':'')));row.append(check,text);return row;};
 const target=$('#groups');const prevHeight=target.offsetHeight;if(prevHeight>0)target.style.minHeight=prevHeight+'px';target.replaceChildren();if(!visible.length)empty(target,'没有符合条件的群组','可调整搜索或隐藏关键词；隐藏群组在下方展开查看。');else visible.forEach(g=>target.append(makeRow(g)));requestAnimationFrame(()=>{target.style.minHeight='';});
 $('#hidden-groups-section').hidden=!hidden.length;$('#hidden-groups-summary').textContent=`隐藏的群组（${hidden.length}） · 其中 ${hidden.filter(g=>g.enabled).length} 个仍在监控`;
 const hiddenTarget=$('#hidden-groups');hiddenTarget.replaceChildren();hidden.forEach(g=>hiddenTarget.append(makeRow(g)));
}

function connectionTone(status){return status==='connected'?'online':['connecting','reconnecting','waiting','needs_scan','needs_login'].includes(status)?'pending':'offline';}

function renderConnections(){
 const target=$('#connections');target.replaceChildren();
 const sidebar=$('#sidebar-connections');sidebar.replaceChildren();
 for(const [platform,label]of [['whatsapp','WhatsApp'],['telegram','Telegram']]){const status=state.connections[platform]?.state||'waiting',row=el('div',undefined,'sidebar-connection'),dot=el('i',undefined,'status-dot '+connectionTone(status));dot.setAttribute('aria-hidden','true');row.append(dot,el('strong',label),el('span',statusNames[status]||status));sidebar.append(row);}
 document.querySelectorAll('[data-conn-platform]').forEach(b=>{const active=b.dataset.connPlatform===connPlatform;b.setAttribute('aria-selected',String(active));b.tabIndex=active?0:-1;});
 const name=connPlatform;
 const c=state.connections[name]||{state:'waiting'},online=c.state==='connected',row=el('div',undefined,'status-row'),status=el('span',undefined,'connection-status'),dot=el('i',undefined,'status-dot '+connectionTone(c.state));
 dot.setAttribute('aria-hidden','true');status.append(dot,document.createTextNode(statusNames[c.state]||c.state));
 const btnText=state.disconnected?.[name]?'重新连接':'断开连接';
 const controls=el('div',undefined,'connection-controls'),button=el('button',btnText,'secondary');
 button.type='button';button.setAttribute('aria-label',btnText+' '+(name==='whatsapp'?'WhatsApp':'Telegram'));
 button.onclick=()=>{
  const disconnect=!state.disconnected?.[name],platform=name==='whatsapp'?'WhatsApp':'Telegram';
  if(disconnect){
   const confirmMsg=`确认退出 ${platform}？\n这会清除服务器上的登录会话，停止接收新消息。下次需要重新${name==='whatsapp'?'扫码':'输入手机号、验证码及两步验证密码（若有）'}。\n已选群组和历史消息保留，已有待处理提醒仍需确认或暂停。`;
   if(!window.confirm(confirmMsg))return;
  }
  action(async()=>{
   button.disabled=true;
   const result=await api('/api/connections/'+name+'/'+(disconnect?'disconnect':'reconnect'),{confirmed:disconnect});
   await refresh();
   if(name==='telegram')await refreshLogin();
   else if(!disconnect){await showQr();}
   toast(disconnect?(online?'已断开连接，下次需要重新登录':'已取消连接'):'请重新'+(name==='whatsapp'?'扫码':'登录'));
   if(result.remote_logout===false)showError(Error('本地登录会话已清除；远端退出未确认，可在 '+platform+' 的设备设置中移除此设备。'));
  });
 };
 controls.append(status,button);row.append(el('strong',name==='whatsapp'?'WhatsApp Business':'Telegram'),controls);target.append(row);
 const waConnected=state.connections.whatsapp?.state==='connected';
 const waWaiting=!waConnected&&!state.disconnected?.whatsapp;
 $('#wa-login').hidden=connPlatform!=='whatsapp'||!waWaiting;
 if(state.disconnected?.whatsapp)$('#wa-status').textContent='';
 else if(waWaiting&&!$('#qr-image').getAttribute('src'))$('#wa-status').textContent='正在连接 WhatsApp…';
 $('#wa-status').hidden=!$('#wa-status').textContent;
 if(!waWaiting){$('#qr-box').hidden=true;$('#qr-image').hidden=true;$('#qr-image').removeAttribute('src');}
 else if(connPlatform==='whatsapp'&&waWaiting){$('#qr-box').hidden=false;showQr();}
 if(lastTgLogin)renderLogin(lastTgLogin);
 else $('.telegram-login').hidden=connPlatform!=='telegram'||state.connections.telegram?.state==='connected'||!!state.disconnected?.telegram;
 const providers=$('#providers');providers.replaceChildren();for(const [name,key]of [['在家 / Bark 长响铃','bark_home'],['外出 / Bark 普通通知','bark_away']]){const row=el('div',undefined,'status-row');row.append(el('strong',name),el('span',state.configured[key]?'凭据已配置 · 待实测':'尚未配置'));providers.append(row);}
 if($('#bark-home-key')&&state.configured.bark_home)$('#bark-home-key').placeholder='已配置时无需重填，输入可更换 Key';
 if($('#bark-key')&&state.configured.bark_away)$('#bark-key').placeholder='已配置时无需重填，输入可更换 Key';
}

async function refresh(){if(loading)return;loading=true;try{state=await api('/api/state');render();showNewGroups();if(page==='setup'){await refreshLogin();if(connPlatform==='whatsapp'&&state?.connections.whatsapp?.state!=='connected'&&!state?.disconnected?.whatsapp)await showQr();}}catch(e){showError(e);}finally{loading=false;}}

const pages={inbox:['提醒收件箱','需要你的时候，及时找到你。'],groups:['监控群组','把注意力留给真正重要的对话。'],samples:['职责样本','从你的判断开始，理解你的工作。'],setup:['连接与设置','一次连接，持续关注。']};

function getInitialPage(){
 const hash=location.hash.replace(/^#/,'');
 if(pages[hash])return hash;
 try{
  const saved=localStorage.getItem('active-page');
  if(pages[saved])return saved;
 }catch{}
 return 'inbox';
}

async function setPage(targetPage,syncUrl=true){
 if(!pages[targetPage])targetPage='inbox';
 page=targetPage;
 try{localStorage.setItem('active-page',page);}catch{}
 if(syncUrl){
  history.replaceState(null,'','#'+page);
 }
 document.querySelectorAll('[data-panel]').forEach(p=>p.hidden=p.dataset.panel!==page);
 document.querySelectorAll('[data-page]').forEach(n=>n.classList.toggle('active',n.dataset.page===page));
 $('#page-title').textContent=pages[page][0];
 $('#page-subtitle').textContent=pages[page][1];
 document.body.dataset.phonePage=page;
 if(page==='samples'){
  if(typeof renderSamples==='function')await renderSamples();
 }
 if(page==='setup'){
  await refreshLogin();
  if(connPlatform==='whatsapp'&&state?.connections.whatsapp?.state!=='connected'&&!state?.disconnected?.whatsapp)await showQr();
 }
}

document.querySelectorAll('[data-page]').forEach(b=>b.onclick=()=>action(async()=>{
 await setPage(b.dataset.page,true);
}));

window.addEventListener('hashchange',()=>{
 const hash=location.hash.replace(/^#/,'');
 if(pages[hash]&&hash!==page)action(async()=>setPage(hash,false));
});

const brandLink=$('.brand');
if(brandLink){
 brandLink.onclick=e=>{e.preventDefault();action(async()=>setPage('inbox',true));};
}
document.querySelectorAll('[data-mode]').forEach(b=>b.onclick=()=>action(async()=>{await api('/api/mode',{mode:b.dataset.mode});toast('已切换为'+names[b.dataset.mode]);}));
document.querySelectorAll('[data-test]').forEach(b=>b.onclick=()=>action(async()=>{b.disabled=true;$('#test-note').textContent='正在测试，请稍候…';try{const r=await api('/api/test/'+b.dataset.test,{});$('#test-note').textContent=r.status==='simulated'?'演练测试通过。真实提醒尚未开启，没有向设备发送通知。':r.status==='accepted'?'接口已接受测试请求。请检查设备是否真的响铃或震动；这次测试不会自动重复。':'测试失败：'+r.detail;}catch(error){$('#test-note').textContent=error.message;throw error;}finally{b.disabled=false;}}));
$('#demo').onclick=()=>action(async()=>{await api('/api/demo',{});toast('已添加演练消息；切换在家或外出模式可测试调度');});
$('#ack-all').onclick=()=>action(async()=>{await api('/api/ack-all',{});toast('当前显示的提醒已全部确认');});
$('#group-search').oninput=()=>{if(state)renderGroups();};
$('#interval-form').oninput=()=>{intervalDirty=true;};
$('#interval-form').onsubmit=e=>{e.preventDefault();action(async()=>{const values={call_interval:Number($('#call-interval').value),push_interval:Number($('#push-interval').value)};await api('/api/intervals',values);intervalDirty=Number($('#call-interval').value)!==values.call_interval||Number($('#push-interval').value)!==values.push_interval;toast('间隔已保存');});};
let qrLoading=false;
async function showQr(){
 if(qrLoading||state?.connections.whatsapp?.state==='connected'||state?.disconnected?.whatsapp)return;
 qrLoading=true;
 try{
  const status=await api('/api/whatsapp/qr');
  if(state?.connections.whatsapp?.state==='connected'||state?.disconnected?.whatsapp)return;
  if(status.state==='disconnected'){
   state.disconnected.whatsapp=true;state.connections.whatsapp={...state.connections.whatsapp,state:'disconnected'};renderConnections();return;
  }
  $('#qr-box').hidden=!status.image;$('#qr-image').hidden=!status.image;
  if(status.image){$('#qr-image').src=status.image;$('#wa-status').textContent='打开 WhatsApp → 已关联设备 → 关联设备，扫描二维码。';}
  else{$('#qr-image').removeAttribute('src');$('#wa-status').textContent=status.state==='logged_out'?'登录已失效，请断开连接后重新登录。':status.state==='connected'?'登录已恢复，正在更新连接状态…':'正在连接 WhatsApp…';}
 }catch(e){$('#qr-box').hidden=true;$('#qr-image').removeAttribute('src');$('#wa-status').textContent='暂时无法读取二维码：'+e.message;showError(e);}
 finally{qrLoading=false;}
}

setInterval(()=>{refresh();if(page==='setup'&&!tgBusy)refreshLogin();if(page==='setup'&&connPlatform==='whatsapp'&&state?.connections.whatsapp?.state!=='connected'&&!state?.disconnected?.whatsapp)showQr();},10000);
setInterval(()=>$('#clock').textContent=new Date().toLocaleString('zh-CN',{hour12:false}),1000);
setPage(getInitialPage(),true);
refresh();
const setupMedia=window.matchMedia('(min-width: 761px)');
function syncSetupCards(e){if(e.matches)document.querySelectorAll('details.setup-card').forEach(d=>d.open=true);}
setupMedia.addEventListener('change',syncSetupCards);
if(setupMedia.matches)syncSetupCards(setupMedia);

let tgBusy=false,tgStep='',tgRetry=0,tgConfigured=false;
function renderLogin(s){
 lastTgLogin=s;
 $('.telegram-login').hidden=connPlatform!=='telegram'||s.step==='disconnected'||(s.step==='connected'&&state?.connections.telegram?.state==='connected')||!!state?.disconnected?.telegram;
 const changed=tgStep!==s.step;tgStep=s.step;tgConfigured=s.configured;tgRetry=Date.now()+s.retry_after*1000;
 for(const step of ['phone','code','password'])$('#tg-'+step+'-form').hidden=!s.configured||s.step!==step;
 document.querySelectorAll('.tg-cancel-btn').forEach(b=>{b.hidden=!s.configured||!['code','password'].includes(s.step);});
 $('#tg-groups').hidden=s.step!=='connected';
 $('#tg-status').textContent=!s.configured?'请先在服务器 .env 配置 TELEGRAM_API_ID / TELEGRAM_API_HASH，再重新创建 app 容器。':s.step==='connected'?'已登录。到「监控群组」勾选需要关注的群。':s.step==='code'?`验证码已发送至 ${s.phone_hint}，请查看 Telegram 应用或短信。`:s.step==='password'?'你的账号开启了两步验证，请输入密码。':'';
 $('#tg-status').hidden=!$('#tg-status').textContent;
 if(changed){$('#tg-code').value='';$('#tg-password').value='';}
 loginButtons();
}
function loginButtons(){document.querySelectorAll('.telegram-login button').forEach(b=>b.disabled=tgBusy);const seconds=Math.max(0,Math.ceil((tgRetry-Date.now())/1000)),b=$('#tg-phone-form button');b.disabled=tgBusy||!tgConfigured||seconds>0;b.textContent=seconds?`等待 ${seconds} 秒后获取`:'获取验证码';}
async function refreshLogin(){try{const s=await api('/api/telegram/login');if(!tgBusy)renderLogin(s);}catch(e){$('#tg-error').textContent=e.message;$('#tg-error').hidden=false;}}
async function loginAction(route,data){if(tgBusy)return;tgBusy=true;loginButtons();$('#tg-error').hidden=true;try{renderLogin(await api('/api/telegram/login/'+route,data));if(tgStep==='connected')await refresh();}catch(e){if(e.login)renderLogin(e.login);$('#tg-error').textContent=e.message;$('#tg-error').hidden=false;}finally{tgBusy=false;loginButtons();}}
$('#tg-phone-form').onsubmit=e=>{e.preventDefault();loginAction('send-code',{phone:$('#tg-phone').value});};
$('#tg-code-form').onsubmit=e=>{e.preventDefault();const code=$('#tg-code').value;$('#tg-code').value='';loginAction('code',{code});};
$('#tg-password-form').onsubmit=e=>{e.preventDefault();const password=$('#tg-password').value;$('#tg-password').value='';loginAction('password',{password});};
document.querySelectorAll('.tg-cancel-btn').forEach(b=>{b.onclick=()=>loginAction('cancel',{});});
$('#tg-groups').onclick=()=>document.querySelector('[data-page="groups"]').click();
setInterval(loginButtons,1000);

document.querySelectorAll('[data-group-platform]').forEach(b=>{
 b.onclick=()=>{groupPlatform=b.dataset.groupPlatform;try{localStorage.setItem('group-platform',groupPlatform);}catch{}$('#hidden-groups-section').open=false;$('#group-keyword').value='';if(state)renderGroups();};
 b.onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const platform=e.key==='Home'?'whatsapp':e.key==='End'?'telegram':groupPlatform==='whatsapp'?'telegram':'whatsapp';const next=document.querySelector('[data-group-platform="'+platform+'"]');next.click();next.focus();}};
});
document.querySelectorAll('[data-sample-platform]').forEach(b=>{
 b.onclick=()=>{samplePlatform=b.dataset.samplePlatform;try{localStorage.setItem('sample-platform',samplePlatform);}catch{}sampleOffset=0;action(renderSamples);};
 b.onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const platform=e.key==='Home'?'whatsapp':e.key==='End'?'telegram':samplePlatform==='whatsapp'?'telegram':'whatsapp';const next=document.querySelector('[data-sample-platform="'+platform+'"]');next.click();next.focus();}};
});
document.querySelectorAll('[data-conn-platform]').forEach(b=>{
 b.onclick=()=>{
  connPlatform=b.dataset.connPlatform;
  try{localStorage.setItem('conn-platform',connPlatform);}catch{}
  renderConnections();
  if(connPlatform==='telegram'&&!tgBusy)refreshLogin();
  if(connPlatform==='whatsapp'&&state?.connections.whatsapp?.state!=='connected'&&!state?.disconnected?.whatsapp)showQr();
 };
 b.onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const platform=e.key==='Home'?'whatsapp':e.key==='End'?'telegram':connPlatform==='whatsapp'?'telegram':'whatsapp';const next=document.querySelector('[data-conn-platform="'+platform+'"]');next.click();next.focus();}};
});
$('#group-filter-form').onsubmit=e=>{e.preventDefault();if(groupSaving)return;const input=$('#group-keyword'),keyword=input.value.trim(),draft=groupDraft(groupPlatform);if(!keyword)return;if(draft.keywords.length>=50){toast('最多添加 50 个关键词');return;}if(!draft.keywords.some(k=>k.toLowerCase()===keyword.toLowerCase()))draft.keywords.push(keyword);input.value='';renderGroups();};
function bulkSelect(enabled){const draft=groupDraft(groupPlatform),query=$('#group-search').value.trim().toLowerCase();for(const g of state.groups.filter(g=>g.platform===groupPlatform&&g.title.toLowerCase().includes(query)&&!draft.keywords.some(k=>g.title.toLowerCase().includes(k.toLowerCase())))){if(enabled===!!g.enabled)delete draft.selections[g.id];else draft.selections[g.id]=enabled;}document.querySelectorAll('#groups .group-card input[type="checkbox"]').forEach(c=>{c.checked=enabled;});updateGroupActions();}
$('#group-select-all').onclick=()=>bulkSelect(true);$('#group-clear-all').onclick=()=>bulkSelect(false);
$('#group-reset').onclick=()=>{delete groupDrafts[groupPlatform];renderGroups();};
$('#group-save').onclick=()=>action(async()=>{if(groupSaving)return;const platform=groupPlatform,payload=JSON.parse(JSON.stringify(groupDraft(platform)));groupSaving=true;renderGroups();try{const result=await api('/api/group-settings/'+platform,payload);state.group_settings||={};state.group_settings[platform]={keywords:result.keywords,revision:result.revision};for(const g of state.groups.filter(g=>g.platform===platform))if(Object.hasOwn(payload.selections,g.id))g.enabled=payload.selections[g.id];delete groupDrafts[platform];toast('当前平台设置已保存');}finally{groupSaving=false;renderGroups();}});
window.addEventListener('beforeunload',e=>{if(intervalDirty||quietDirty||Object.keys(sampleDrafts).length||(learningLoaded&&$('#responsibility-rules').value!==learningState.rules)||(state&&['whatsapp','telegram'].some(groupDirty))){e.preventDefault();e.returnValue='';}});

for(const [id,mode,label]of [['bark-home-form','home','在家'],['bark-form','away','外出']]){
 const form=$('#'+id);form.onsubmit=e=>{e.preventDefault();action(async()=>{const input=form.querySelector('input'),device_key=input.value;input.value='';const button=form.querySelector('button[type="submit"]');button.disabled=true;try{await api('/api/bark',{mode,device_key});toast(label+' Bark 配置已保存');}finally{button.disabled=false;}});};
}
function switchBarkTab(tab){
 const isHome=tab==='home';
 const th=$('#bark-tab-home'),ta=$('#bark-tab-away'),fh=$('#bark-home-form'),fa=$('#bark-form');
 if(th){th.setAttribute('aria-selected',String(isHome));th.tabIndex=isHome?0:-1;}
 if(ta){ta.setAttribute('aria-selected',String(!isHome));ta.tabIndex=isHome?-1:0;}
 if(fh)fh.hidden=!isHome;
 if(fa)fa.hidden=isHome;
}
if($('#bark-tab-home'))$('#bark-tab-home').onclick=()=>switchBarkTab('home');
if($('#bark-tab-away'))$('#bark-tab-away').onclick=()=>switchBarkTab('away');


const themeAudio=$('#theme-audio'),audioToggle=$('#audio-toggle');
if(themeAudio&&audioToggle){
 let fadeTimer=null;
 function fadeVolume(toVol,ms,done){
  if(fadeTimer)clearInterval(fadeTimer);
  const steps=Math.max(1,Math.round(ms/40)),diff=(toVol-themeAudio.volume)/steps;
  let count=0;
  fadeTimer=setInterval(()=>{
   count++;
   const next=themeAudio.volume+diff;
   if(count>=steps||(diff>0&&next>=toVol)||(diff<0&&next<=toVol)){
    themeAudio.volume=Math.max(0,Math.min(1,toVol));
    clearInterval(fadeTimer);fadeTimer=null;
    if(done)done();
   }else{themeAudio.volume=Math.max(0,Math.min(1,next));}
  },40);
 }
 function setAudioPlaying(playing){
  audioToggle.classList.toggle('playing',playing);
  audioToggle.setAttribute('aria-pressed',String(playing));
  const label=audioToggle.querySelector('.audio-label');
  if(label)label.textContent=playing?'播放中 · 续集':'主题曲 · 续集';
  audioToggle.title=playing?'点击暂停播放':'播放主题曲《续集》';
 }
 audioToggle.onclick=()=>{
  if(themeAudio.paused){
   themeAudio.volume=0;
   themeAudio.play().then(()=>{
    setAudioPlaying(true);
    fadeVolume(1,600);
   }).catch(e=>toast('无法自动播放音频：'+e.message));
  }else{
   fadeVolume(0,500,()=>{
    themeAudio.pause();
    themeAudio.currentTime=0;
    setAudioPlaying(false);
   });
  }
 };
 themeAudio.onended=()=>{
  themeAudio.currentTime=0;
  setAudioPlaying(false);
 };
}


// Phone navigation starts each page at the top; desktop scroll behavior is unchanged.
document.querySelectorAll('nav [data-page]').forEach(button=>button.addEventListener('click',()=>{document.body.dataset.phonePage=button.dataset.page;if(matchMedia('(max-width:760px)').matches)window.scrollTo({top:0,behavior:'instant'});}));

document.body.dataset.phonePage=page;

let groupNoticeDialog=null,groupNoticeSeen=0;
function showNewGroups(){
 const notices=state.group_sync_notices||[];
 const seen=Math.max(groupNoticeSeen,state.group_sync_seen||0);
 if(groupNoticeDialog?.open&&Number(groupNoticeDialog.dataset.lastId)<=seen){groupNoticeDialog.dataset.remoteClose='true';groupNoticeDialog.close();}
 const fresh=notices.filter(n=>n.id>seen);if(!fresh.length||groupNoticeDialog?.open)return;
 const dialog=el('dialog',undefined,'group-notice');
 dialog.tabIndex=-1;
 const connectionNotice=fresh.some(n=>n.kind==='connection');
 const title=el('h2',connectionNotice?'连接成功 · 群组同步完成':'同步发现新群组');
 const latest=Math.max(...fresh.map(n=>n.id));
 const groupsByPlatform={whatsapp:[],telegram:[]};
 const seenKeys=new Set();
 let total=0,monitored=0;
 for(const n of fresh){
  const p=n.platform==='whatsapp'?'whatsapp':'telegram';
  for(const g of n.groups){
   const key=p+':'+(g.id||g.title);
   if(seenKeys.has(key))continue;
   seenKeys.add(key);
   total++;if(g.enabled)monitored++;
   groupsByPlatform[p].push(g);
  }
 }
 title.id='group-notice-title';dialog.setAttribute('aria-labelledby',title.id);
 const content=el('div',undefined,'group-notice-content');
 for(const [platform,label,cls]of[['whatsapp','WhatsApp','whatsapp'],['telegram','Telegram','telegram']]){
  const list=groupsByPlatform[platform];
  if(!list.length)continue;
  const section=el('div',undefined,'group-notice-section');
  const h3=el('h3',undefined,'group-notice-platform '+cls);
  h3.append(label,el('span',String(list.length),'group-notice-count'));
  const pills=el('div',undefined,'group-notice-pills');
  for(const g of list){
   pills.append(el('span',g.title+(g.enabled?' · 已监控':''),'group-notice-pill '+cls));
  }
  section.append(h3,pills);
  content.append(section);
 }
 const close=el('button','知道了','secondary'),view=el('button','去选择监控群组','primary'),actions=el('div',undefined,'actions group-notice-actions');
 close.onclick=()=>dialog.close();view.onclick=()=>{dialog.close();document.querySelector('[data-page="groups"]').click();};
 dialog.dataset.lastId=String(latest);
 dialog.addEventListener('close',()=>{
  dialog.remove();groupNoticeDialog=null;
  if(dialog.dataset.remoteClose==='true')return;
  groupNoticeSeen=Math.max(groupNoticeSeen,latest);
  api('/api/group-notices/read',{last_id:latest}).then(result=>{state.group_sync_seen=Math.max(state.group_sync_seen||0,result.seen);}).catch(error=>{groupNoticeSeen=0;showError(Error('新群提示已读状态未保存，请稍后重试：'+error.message));});
 });
 actions.append(close,view);
 dialog.append(title,el('p',`${connectionNotice?'本次同步':'发现'} ${total} 个${connectionNotice?'群组':'新群组'}：${monitored} 个已监控，${total-monitored} 个未监控。`,'subtle'),content,actions);
 document.body.append(dialog);
 groupNoticeDialog=dialog;
 dialog.showModal();
 dialog.focus({preventScroll:true});
 if(document.activeElement&&document.activeElement!==dialog){
  document.activeElement.blur();
 }
}

document.addEventListener('click', (e) => {
 const btn = e.target.closest('.info-btn');
 if (btn) {
  e.preventDefault();
  e.stopPropagation();
  const tip = btn.closest('.info-tip');
  const wasOpen = tip?.classList.contains('open');
  document.querySelectorAll('.info-tip.open').forEach(t => {
   t.classList.remove('open');
   const b = t.querySelector('.info-bubble');
   if (b) {
    b.style.removeProperty('--bubble-left');
    b.style.removeProperty('--arrow-left');
   }
  });
  if (!wasOpen && tip) {
   const bubble = tip.querySelector('.info-bubble');
   if (bubble) {
    bubble.style.transition = 'none';
    bubble.style.removeProperty('--bubble-left');
    bubble.style.removeProperty('--arrow-left');
    tip.classList.add('open');
    const rect = bubble.getBoundingClientRect();
    const btnRect = btn.getBoundingClientRect();
    const pad = 12;
    const viewportWidth = window.innerWidth || document.documentElement.clientWidth;
    let shift = 0;
    if (rect.right > viewportWidth - pad) {
     shift = rect.right - (viewportWidth - pad);
    } else if (rect.left < pad) {
     shift = rect.left - pad;
    }
    if (shift !== 0) {
     bubble.style.setProperty('--bubble-left', `${-shift}px`);
     const arrowX = (btnRect.left + btnRect.width / 2) - (rect.left - shift);
     const clampedArrowX = Math.max(14, Math.min(rect.width - 14, arrowX));
     bubble.style.setProperty('--arrow-left', `${clampedArrowX - 5}px`);
    }
    void bubble.offsetWidth;
    bubble.style.transition = '';
   } else {
    tip.classList.add('open');
   }
  }
  return;
 }
 if (e.target.closest('.info-bubble')) {
  e.stopPropagation();
  return;
 }
 document.querySelectorAll('.info-tip.open').forEach(t => {
  t.classList.remove('open');
  const b = t.querySelector('.info-bubble');
  if (b) {
   b.style.removeProperty('--bubble-left');
   b.style.removeProperty('--arrow-left');
  }
 });
});

document.addEventListener('keydown', (e) => {
 if (e.key === 'Escape') {
  document.querySelectorAll('.info-tip.open').forEach(t => {
   t.classList.remove('open');
   const b = t.querySelector('.info-bubble');
   if (b) {
    b.style.removeProperty('--bubble-left');
    b.style.removeProperty('--arrow-left');
   }
  });
 }
});

window.addEventListener('resize', () => {
 document.querySelectorAll('.info-tip.open').forEach(t => {
  t.classList.remove('open');
  const b = t.querySelector('.info-bubble');
  if (b) {
   b.style.removeProperty('--bubble-left');
   b.style.removeProperty('--arrow-left');
  }
 });
});



// Refresh in place so group selections and sample review drafts survive the gesture.
const pullHint=document.createElement('div');
pullHint.className='pull-refresh';
pullHint.setAttribute('role','status');
pullHint.innerHTML='<span class="pull-icon"><svg class="pull-arrow" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="4" x2="12" y2="19"></line><polyline points="19 12 12 19 5 12"></polyline></svg><svg class="pull-spinner" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-opacity="0.25"></circle><path d="M12 3a9 9 0 0 1 9 9" stroke-linecap="round"></path></svg></span><span class="pull-text">下拉刷新</span>';
document.body.append(pullHint);
const pullText=pullHint.querySelector('.pull-text');
let pullStart=null,pullDistance=0,pullBusy=false;
function resetPull(){
 pullStart=null;pullDistance=0;
 if(!pullBusy){
  pullHint.style.transition='transform .25s ease, opacity .2s ease';
  pullHint.style.transform='translate3d(-50%, -65px, 0)';
  pullHint.style.opacity='0';
  setTimeout(()=>{if(!pullBusy)pullHint.classList.remove('visible','ready','refreshing');},250);
 }
}
async function pullRefresh(){
 if(pullBusy)return;
 pullBusy=true;
 pullHint.classList.add('visible','refreshing');
 pullHint.classList.remove('ready');
 pullHint.style.transition='transform .25s cubic-bezier(0.2, 0.8, 0.3, 1), opacity .2s ease';
 pullHint.style.transform='translate3d(-50%, 18px, 0)';
 pullHint.style.opacity='1';
 pullText.textContent='正在刷新…';
 try{
  const current=await api('/api/state');
  const results=await Promise.allSettled(['whatsapp','telegram'].filter(p=>current.connections[p]?.state==='connected'&&!current.disconnected?.[p]).map(p=>api('/api/refresh/'+p,{})));
  await refresh();if(page==='samples')await renderSamples();
  const failed=results.find(r=>r.status==='rejected');if(failed)throw failed.reason;
  toast('已刷新');
 }catch(error){showError(error);}
 finally{
  pullBusy=false;
  pullHint.style.transition='transform .3s ease, opacity .25s ease';
  pullHint.style.transform='translate3d(-50%, -65px, 0)';
  pullHint.style.opacity='0';
  setTimeout(()=>{
   pullHint.classList.remove('visible','ready','refreshing');
   pullStart=null;pullDistance=0;
  },300);
 }
}
document.addEventListener('touchstart',e=>{
 if(pullBusy||!matchMedia('(max-width:760px)').matches||e.touches.length!==1||window.scrollY>0||document.querySelector('dialog[open]'))return;
 if(e.target.closest('input,textarea,select,button,a,summary,nav'))return;
 for(let n=e.target;n&&n!==document.body;n=n.parentElement){if(n.scrollHeight>n.clientHeight&&/auto|scroll/.test(getComputedStyle(n).overflowY))return;}
 pullStart={x:e.touches[0].clientX,y:e.touches[0].clientY};pullDistance=0;
},{passive:true});
document.addEventListener('touchmove',e=>{
 if(!pullStart)return;if(e.touches.length!==1){resetPull();return;}
 if(window.scrollY>0){resetPull();return;}
 const dx=e.touches[0].clientX-pullStart.x,dy=e.touches[0].clientY-pullStart.y;
 if(dy<0||Math.abs(dx)>Math.abs(dy)){resetPull();return;}
 pullDistance=dy;
 if(dy<=10){
  pullHint.classList.remove('ready');
  pullHint.style.transition='transform .2s ease, opacity .15s ease';
  pullHint.style.transform='translate3d(-50%, -65px, 0)';
  pullHint.style.opacity='0';
 }else{
  if(e.cancelable)e.preventDefault();
  pullHint.classList.add('visible');
  pullHint.style.transition='none';
  const translateY=Math.min(Math.round((dy-10)*0.45),46);
  pullHint.style.transform=`translate3d(-50%, ${translateY}px, 0)`;
  pullHint.style.opacity=String(Math.min(1,(dy-10)/25));
  if(dy>=75){
   pullHint.classList.add('ready');
   pullText.textContent='松开刷新';
  }else{
   pullHint.classList.remove('ready');
   pullText.textContent='下拉刷新';
  }
 }
},{passive:false});
document.addEventListener('touchend',()=>{
 const ready=pullDistance>=75;
 if(ready)pullRefresh();
 else resetPull();
},{passive:true});
document.addEventListener('touchcancel',resetPull,{passive:true});

function renderBadge(){
 const b=$('#delivery-badge');
 if(!b||!state)return;
 let text='在家',cls='badge-home';
 if(state.quiet_active){
  text='免打扰';cls='badge-quiet';
 }else if(state.mode==='away'){
  text='在外';cls='badge-away';
 }else if(state.mode==='paused'){
  text='暂停';cls='badge-paused';
 }
 if(!state.delivery_enabled)text+=' · 演练';
 b.textContent=text;
 b.className='badge '+cls;
}

function quietValues(){return {enabled:$('#quiet-enabled').checked,start:$('#quiet-start').value,end:$('#quiet-end').value,timezone:$('#quiet-timezone').value,days:[...document.querySelectorAll('[name="quiet-day"]:checked')].map(e=>Number(e.value))};}
function renderQuietHours(){
 const q=state.quiet_hours||{enabled:false,start:'18:00',end:'09:00',days:[0,1,2,3,4,5,6],timezone:'Asia/Kuala_Lumpur'};
 if(!quietDirty){$('#quiet-enabled').checked=q.enabled;$('#quiet-start').value=q.start;$('#quiet-end').value=q.end;$('#quiet-timezone').value=q.timezone;document.querySelectorAll('[name="quiet-day"]').forEach(e=>e.checked=q.days.includes(Number(e.value)));}
 $('#quiet-status').textContent=quietDirty?'有未保存修改':state.quiet_active?'当前处于免打扰时段':q.enabled?'已启用，当前不在免打扰时段':'免打扰未启用';
 renderBadge();
 if(state.quiet_active){$('#mode-note').textContent='免打扰中，自动提醒已暂停，消息仍会进入收件箱。时段结束后继续提醒未确认消息。';}
}
$('#quiet-form').oninput=()=>{quietDirty=true;renderQuietHours();};
$('#quiet-form').onsubmit=e=>{e.preventDefault();action(async()=>{const q=quietValues();if(q.enabled&&(!q.days.length||q.start===q.end))throw Error('请选择星期，并设置不同的开始和结束时间');await api('/api/quiet-hours',q);quietDirty=JSON.stringify(q)!==JSON.stringify(quietValues());toast('免打扰设置已保存');});};
