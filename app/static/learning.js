let sampleOffset=0,learningState,learningLoaded=false,sampleRequest=0;
const sampleDrafts={};
const labels={action:'需要我处理',inform:'只需知会',irrelevant:'与我无关',unclear:'上下文不足'};
function field(label,input){const wrap=el('label',label);wrap.append(input);return wrap;}
function inputText(value,placeholder,max=200){const input=el('input');input.value=value||'';input.placeholder=placeholder;input.maxLength=max;return input;}
function selectOptions(options,value){const s=el('select');for(const [v,t]of options){const o=el('option',t);o.value=v;s.append(o);}s.value=value;return s;}
async function loadLearning(){
 learningState=await api('/api/learning');
 if(!learningLoaded){$('#responsibility-rules').value=learningState.rules;const cfg=await api('/api/ai');$('#ai-base').value=cfg.base_url;$('#ai-model').value=cfg.model;$('#ai-config-status').textContent=cfg.configured?'密钥已配置':'尚未配置';learningLoaded=true;}
 $('#learning-count').textContent=`已审核 ${learningState.reviewed} 条 · 验收 ${learningState.evaluation_count} 条`;
 const list=$('#dataset-versions');list.replaceChildren();for(const v of learningState.versions){const row=el('div',undefined,'version-row');
 let versionLabel=`V${v.id}${learningState.active===v.id?' · 默认试判版':''}`;
 if(v.superseded_by)versionLabel+=` · 历史版本，已被 V${v.superseded_by} 替代`;
 if(v.retired)versionLabel+=' · 清理后无可用样本';
 row.append(el('strong',versionLabel),el('p',`${v.count} 条（验收 ${v.evaluation_count}） · 新增 ${v.added} / 修改 ${v.changed} / 移除 ${v.removed}`,'subtle'));if(v.note)row.append(el('p',v.note,'subtle'));const show=el('button','查看快照','secondary'),activate=el('button','默认试判','secondary');
 if(v.superseded_by||v.retired)activate.disabled=true;
 show.onclick=()=>action(async()=>{const data=await api('/api/learning/versions/'+v.id);const detail=$('#version-detail');detail.hidden=false;detail.replaceChildren(el('h2','V'+v.id+' · 冻结快照'),el('p',data.rules,'message'));for(const s of data.samples){const box=el('details');box.append(el('summary',`${s.title} · ${labels[s.label]} · ${s.split==='evaluation'?'验收':'参考'}`),el('p',s.text,'message'),el('p',s.rationale||'未填写判断理由','subtle'),el('pre',s.context.map(c=>c.sender+'：'+c.text).join('\n'),'context'));detail.append(box);}});activate.onclick=()=>action(async()=>{await api('/api/learning/versions/'+v.id+'/activate',{});await loadLearning();$('#ai-candidate').value=String(v.id);toast('默认试判版本已切换；不启用响铃');});row.append(show,activate);list.append(row);}
 for(const id of ['ai-candidate','ai-baseline']){const select=$('#'+id),old=select.value;select.replaceChildren();if(id==='ai-baseline'){const option=el('option','不对比');option.value='';select.append(option);}for(const v of learningState.versions){if(v.superseded_by||v.retired)continue;const o=el('option','V'+v.id);o.value=v.id;select.append(o);}if([...select.options].some(o=>o.value===old))select.value=old;else if(id==='ai-candidate'&&learningState.active)select.value=String(learningState.active);}
 await loadRuns();
}
let expandedSample=null;
async function renderSamples(){
 const request=++sampleRequest,platform=samplePlatform,offset=sampleOffset;
 await loadLearning();if(request!==sampleRequest)return;
 const result=await api('/api/learning/samples?platform='+platform+'&offset='+offset);
 if(request!==sampleRequest||platform!==samplePlatform||offset!==sampleOffset)return;
 if(offset>0&&offset>=result.total){sampleOffset=Math.max(0,Math.floor((result.total-1)/50)*50);return renderSamples();}
 const target=$('#samples');target.replaceChildren();
 for(const [p,id]of [['whatsapp','ws'],['telegram','tg']])$('#'+id+'-sample-count').textContent=result.counts?.[p]||0;
 document.querySelectorAll('[data-sample-platform]').forEach(b=>{const active=b.dataset.samplePlatform===platform;b.setAttribute('aria-selected',String(active));b.tabIndex=active?0:-1;});
 $('#samples-page').textContent=`${result.total?offset+1:0}–${Math.min(offset+50,result.total)} / ${result.total}`;
 $('#samples-prev').disabled=offset===0;$('#samples-next').disabled=offset+50>=result.total;
 if(!result.items.length){empty(target,'暂无需要审核的消息','只收集未直接 @ 你的职责候选消息。自己的消息仅保留为前文，不参与职责审核。');return;}
 const wrap=el('div',undefined,'sample-table-wrap'),table=el('table',undefined,'sample-table'),head=el('thead'),heading=el('tr'),body=el('tbody');
 const caption=el('caption','职责审核消息');caption.className='visually-hidden';table.append(caption);
 for(const [text,cls]of [['消息 / 来源','sample-message-col'],['审核结果','sample-status-col'],['样本用途','sample-split-col'],['操作','sample-action-col']]){const th=el('th',text,cls);th.scope='col';heading.append(th);}
 head.append(heading);table.append(head,body);wrap.append(table);target.append(wrap);
 for(const saved of result.items){
  const s={...saved,...sampleDrafts[saved.id]},row=el('tr'),message=el('td',undefined,'sample-message-col');
  message.append(el('div',s.title,'sample-source'),el('div',`${s.sender} · ${date(s.created)}`,'meta'),el('div',s.text,'sample-preview'));
  const status=el('td',undefined,'sample-status-col');status.append(el('span',labels[s.label]||'待审核',s.label?'badge':'subtle'));
  if(s.urgent)status.append(el('small','紧急','sample-urgent'));
  if(sampleDrafts[s.id])status.append(el('small','未保存','subtle'));
  const splitCell=el('td',s.label?(s.split==='evaluation'?'验收':'参考'):'—','sample-split-col');
  const defaultText=saved.label?'编辑':'审核';
  const actions=el('td',undefined,'sample-action-col'),toggle=el('button',expandedSample===s.id?'收回':defaultText,'secondary');
  toggle.dataset.defaultText=defaultText;
  actions.append(toggle);row.append(message,status,splitCell,actions);
  const detail=el('tr',undefined,'sample-detail-row'),cell=el('td');cell.colSpan=4;detail.append(cell);detail.id='sample-detail-'+s.id;
  detail.hidden=expandedSample!==s.id;toggle.setAttribute('aria-controls',detail.id);toggle.setAttribute('aria-expanded',String(!detail.hidden));
  toggle.onclick=()=>{const opening=detail.hidden;body.querySelectorAll('.sample-detail-row').forEach(r=>r.hidden=true);body.querySelectorAll('.sample-action-col button').forEach(b=>{b.setAttribute('aria-expanded','false');if(b.dataset.defaultText)b.textContent=b.dataset.defaultText;});detail.hidden=!opening;expandedSample=opening?s.id:null;toggle.setAttribute('aria-expanded',String(opening));toggle.textContent=opening?'收回':defaultText;};
  cell.append(el('h3',s.title+' · '+s.sender),el('div',s.text,'message'));
  const form=el('form',undefined,'review-form'),classification=selectOptions([['','选择分类…'],...Object.entries(labels)],s.label||''),service=inputText(s.service,'例如：生产数据库'),reason=inputText(s.rationale,'为什么需要你处理／为什么无关',2000),split=selectOptions([['reference','参考样本'],['evaluation','验收样本']],s.split||'reference'),urgent=el('input');urgent.type='checkbox';urgent.checked=!!s.urgent;classification.required=true;
  const draft=()=>({label:classification.value,service:service.value,rationale:reason.value,split:split.value,urgent:urgent.checked});
  form.oninput=()=>{sampleDrafts[s.id]=draft();status.replaceChildren(el('span',labels[classification.value]||'待审核'),el('small','未保存','subtle'));};form.onchange=form.oninput;
  const urgentLabel=el('label',undefined,'urgent-choice');urgentLabel.append(urgent,document.createTextNode('紧急'));
  const buttons=el('div',undefined,'actions'),save=el('button','保存审核','primary'),context=el('button','查看前文','secondary');context.type='button';
  context.onclick=()=>action(async()=>{const rows=await api('/api/samples/'+s.id+'/context');let content=cell.querySelector('.context');if(!content){content=el('pre',undefined,'context');cell.append(content);}content.textContent=rows.map(r=>r.sender+'：'+r.text).join('\n');});buttons.append(save,context);
  if(saved.label){const remove=el('button','撤回审核','secondary');remove.type='button';remove.onclick=()=>action(async()=>{await api('/api/learning/samples/'+s.id+'/unreview',{});delete sampleDrafts[s.id];await renderSamples();toast('已从草稿撤回，历史版本保留');});buttons.append(remove);}
  if(saved.from_me==null){const own=el('button','这是我发的','secondary');own.type='button';own.title='旧记录未保存发送身份，标记后移出审核，仍保留为对话前文';own.onclick=()=>action(async()=>{own.disabled=true;try{await api('/api/learning/samples/'+s.id+'/own',{});delete sampleDrafts[s.id];await renderSamples();toast('已移出职责审核，仍保留为前文');}finally{own.disabled=false;}});buttons.append(own);}
  form.append(field('分类',classification),field('服务／职责',service),field('判断理由',reason),field('样本用途',split),urgentLabel,buttons);
  form.onsubmit=e=>{e.preventDefault();action(async()=>{save.disabled=true;try{const payload=draft();await api('/api/learning/samples/'+s.id,payload);if(JSON.stringify(sampleDrafts[s.id])===JSON.stringify(payload))delete sampleDrafts[s.id];await renderSamples();toast('审核已保存，已发布版本不受影响');}catch(err){if(err.message&&(err.message.includes('404')||err.message.includes('不再纳入'))){delete sampleDrafts[s.id];toast('该消息已不再纳入职责样本');await renderSamples();}else throw err;}finally{save.disabled=false;}});};
  cell.append(form);body.append(row,detail);
 }
}
$('#samples-prev').onclick=()=>action(async()=>{sampleOffset=Math.max(0,sampleOffset-50);await renderSamples();});$('#samples-next').onclick=()=>action(async()=>{sampleOffset+=50;await renderSamples();});
$('#rules-form').onsubmit=e=>{e.preventDefault();action(async()=>{const rules=$('#responsibility-rules').value;await api('/api/learning/rules',{rules});learningState.rules=rules;toast('职责草稿已保存');});};
$('#publish-form').onsubmit=e=>{e.preventDefault();action(async()=>{if(Object.keys(sampleDrafts).length||$('#responsibility-rules').value!==learningState.rules)throw Error('还有未保存的职责或审核草稿，请先保存再发布版本。');const b=$('#publish-form button');b.disabled=true;try{const r=await api('/api/learning/publish',{note:$('#version-note').value});$('#version-note').value='';await loadLearning();toast('已发布 V'+r.id+'，这是案例版本，不是模型微调');}finally{b.disabled=false;}});};
$('#ai-config-form').onsubmit=e=>{e.preventDefault();action(async()=>{const key=$('#ai-key').value;$('#ai-key').value='';await api('/api/ai/config',{base_url:$('#ai-base').value,model:$('#ai-model').value,api_key:key});$('#ai-config-status').textContent='配置已保存';});};
$('#ai-eval-form').onsubmit=e=>{e.preventDefault();action(async()=>{const b=$('#ai-start');b.disabled=true;try{await api('/api/ai/evaluate',{version_id:Number($('#ai-candidate').value),compare_id:$('#ai-baseline').value?Number($('#ai-baseline').value):null,limit:Number($('#ai-limit').value),sampling:$('#ai-sampling').value});await loadRuns();toast('试判任务已提交，不会触发 Bark');}finally{await loadRuns();}});};
let selectedRun=null;
const runNames={queued:'排队中',running:'试判中',complete:'已完成',failed:'失败',interrupted:'已中断',cancel_requested:'等待当前请求结束后取消',cancelled:'已取消'};
async function showRun(id){
 const run=await api('/api/ai/runs/'+id);if(selectedRun!==id)return;
 const panel=$('#ai-result');panel.replaceChildren(el('h3','试判 #'+id+' · '+(runNames[run.status]||run.status)));
 for(const m of run.metrics)panel.append(el('p',`V${m.version_id}：已判 ${m.evaluated} · 分类正确 ${m.correct} · 误报 ${m.false_positive} · 漏报 ${m.missed} · 紧急程度正确 ${m.urgency_correct}`));
 if(run.result.usage)panel.append(el('p',`输入 token ${run.result.usage.prompt_tokens} / 输出 token ${run.result.usage.completion_tokens}（供应商若未返回则为 0，费用请查服务商账单）`,'subtle'));
 for(const item of run.result.items||[]){const box=el('article',undefined,'sample');box.append(el('strong',`V${item.version_id} · 人工：${labels[item.expected.label]} / AI：${labels[item.prediction.label]}`),el('p',item.text,'message'),el('p',item.prediction.reason,'subtle'),el('p','使用参考案例：'+((item.references||[]).map(r=>'#'+r.id+' '+r.title).join('、')||'无'),'subtle'));panel.append(box);}
}
async function loadRuns(){
 const data=await api('/api/ai'),target=$('#ai-runs');target.replaceChildren();
 $('#ai-start').disabled=data.runs.some(r=>['queued','running','cancel_requested'].includes(r.status));
 for(const r of data.runs){const cfg=JSON.parse(r.config),row=el('div',undefined,'version-row'),button=el('button','查看结果','secondary');
 row.append(el('span',`#${r.id} · ${runNames[r.status]||r.status} · ${r.completed||0}/${(cfg.test_ids?.length||0)*cfg.version_ids.length} · ${cfg.model} · ${cfg.version_ids.map(i=>'V'+i).join(' / ')}`));
 if(r.error)row.append(el('p',r.error,'subtle'));button.onclick=()=>action(async()=>{selectedRun=r.id;await showRun(r.id);});row.append(button);
 if(['queued','running'].includes(r.status)){const cancel=el('button','取消剩余调用','secondary');cancel.onclick=()=>action(async()=>{cancel.disabled=true;await api('/api/ai/runs/'+r.id+'/cancel',{});await loadRuns();toast('已请求取消，已发出的云端请求可能仍会计费');});row.append(cancel);}
 target.append(row);}
 if(selectedRun!==null)await showRun(selectedRun);
}
setInterval(()=>{if(page==='samples'&&learningLoaded)loadRuns().catch(showError);},5000);
if(typeof page!=='undefined'&&page==='samples')renderSamples().catch(showError);
