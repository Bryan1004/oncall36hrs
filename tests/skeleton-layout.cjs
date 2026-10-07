const {chromium}=require('playwright');
const assert=require('node:assert/strict');
// Run with Node and Playwright installed; optionally set PLAYWRIGHT_CHROMIUM_EXECUTABLE.
const root=require('path').resolve(__dirname,'../app/static')+'/';
const fixtures={"/api/state": {"mode": "paused", "delivery_enabled": false, "call_interval": 120, "push_interval": 60, "next_delivery": 0, "connections": {"telegram": {"name": "telegram", "state": "offline", "updated": 0, "changed": 0}, "whatsapp": {"name": "whatsapp", "state": "offline", "updated": 0, "changed": 0}}, "quiet_hours": {"enabled": false, "start": "18:00", "end": "09:00", "days": [0, 1, 2, 3, 4, 5, 6], "timezone": "Asia/Kuala_Lumpur"}, "quiet_active": false, "configured": {"bark": false, "bark_home": false, "bark_away": false, "telegram": false}, "groups": [], "alerts": [], "disconnected": {"telegram": false, "whatsapp": false}, "group_sync_notices": [], "group_sync_seen": 0, "group_settings": {"whatsapp": {"revision": 0, "keywords": []}, "telegram": {"revision": 0, "keywords": []}}, "group_hidden_keywords": [], "deliveries": []}, "/api/learning": {"rules": "", "versions": [], "active": null, "reviewed": 0, "evaluation_count": 0}, "/api/ai": {"base_url": "https://api.openai.com/v1", "model": "", "configured": false, "runs": []}, "/api/learning/samples?platform=whatsapp&offset=0": {"items": [], "total": 0, "counts": {"whatsapp": 0, "telegram": 0}}, "/api/telegram/login": {"step": "phone", "configured": false, "phone_hint": "", "retry_after": 0}};
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{})});
 for(const width of [390,1280])for(const panel of ['inbox','groups','samples','setup'])for(const populated of [false,true]){
  const page=await browser.newPage({viewport:{width,height:900}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  let release;const gate=new Promise(r=>release=r);
  await page.route('http://oncall.test/**',async route=>{
   const u=new URL(route.request().url());
   if(u.pathname.startsWith('/api/')){
    await gate;
    let data=structuredClone(fixtures[u.pathname+u.search]||fixtures[u.pathname]||{});
    if(u.pathname==='/api/state'&&populated){
     data.delivery_enabled=true;data.groups=Array.from({length:20},(_,i)=>({id:String(i),title:'非常长的生产告警群名称'.repeat(5),platform:'whatsapp',enabled:true}));
     data.alerts=Array.from({length:5},(_,i)=>({id:i,title:'生产告警',text:'长消息'.repeat(180),sender:'服务',created:1,platform:'whatsapp',status:'pending'}));
     data.deliveries=Array.from({length:10},()=>({created:1,channel:'home',status:'accepted'}));
    }
    if(u.pathname==='/api/learning'&&populated)data.versions=Array.from({length:8},(_,i)=>({id:i+1,count:20,evaluation_count:4,note:'版本说明'.repeat(20)}));
    if(u.pathname==='/api/learning/samples'&&populated)data={total:5,counts:{whatsapp:5},items:Array.from({length:5},(_,i)=>({id:i,title:'工作群',text:'消息正文'.repeat(60),sender:'同事',created:1}))};
    await route.fulfill({json:data});return;
   }
   const file=u.pathname==='/'?'index.html':u.pathname.replace('/assets/','');
   try{await route.fulfill({path:root+file});}catch{await route.fulfill({status:404,body:''});}
  });
  await page.goto('http://oncall.test/#'+panel,{waitUntil:'domcontentloaded'});
  if(panel==='setup'&&width<761)await page.locator('.setup-card summary').first().click();
  const geometry=()=>page.evaluate(()=>Object.fromEntries([...document.querySelectorAll('.stats,#alerts,#deliveries,.group-data-region,#samples,.learning-grid>.card,.setup-card,footer')].filter(n=>n.getBoundingClientRect().height&&n.closest('[data-panel]')&&!n.closest('[data-panel]').hidden).map((n,i)=>[n.id||n.className+i,{height:n.getBoundingClientRect().height,top:n.getBoundingClientRect().top}])));
  await page.evaluate(()=>window.scrollTo(0,0));const before=await geometry();release();
  await page.waitForFunction(()=>[...document.querySelectorAll('[data-panel]:not([hidden]) [data-loading]')].every(n=>n.dataset.loading==='false'));
  await page.waitForTimeout(150);
  const after=await geometry();assert.deepEqual(after,before,`${width} ${panel} populated=${populated}`);assert.deepEqual(errors,[]);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  console.log('PASS',width,panel,populated?'long data':'empty');await page.close();
 }
 // Failed initial loads retain their viewport and can be retried without a reload.
 for(const panel of ['inbox','samples']){
  const page=await browser.newPage({viewport:{width:390,height:900}});
  let fail=true;
  await page.route('http://oncall.test/**',async route=>{
   const u=new URL(route.request().url());
   if(u.pathname.startsWith('/api/')){
    if(fail&&((panel==='inbox'&&u.pathname==='/api/state')||(panel==='samples'&&u.pathname==='/api/learning/samples'))){await route.fulfill({status:503,json:{detail:'test unavailable'}});return;}
    await route.fulfill({json:fixtures[u.pathname+u.search]||fixtures[u.pathname]||{}});return;
   }
   try{await route.fulfill({path:root+(u.pathname==='/'?'index.html':u.pathname.replace('/assets/',''))});}catch{await route.fulfill({status:404,body:''});}
  });
  await page.goto('http://oncall.test/#'+panel);
  const target=page.locator(panel==='inbox'?'#alerts':'#samples');
  await target.locator('.load-error').waitFor();const before=await target.evaluate(n=>n.offsetHeight);
  assert.equal(await target.getAttribute('aria-busy'),'false');
  fail=false;await target.getByRole('button',{name:'重新加载'}).click();
  await target.locator('.load-error').waitFor({state:'detached'});
  await target.locator('.empty').waitFor();
  assert.equal(await target.evaluate(n=>n.offsetHeight),before);
  console.log('PASS failure/retry',panel);await page.close();
 }
 // Navigation commits immediately; destination data may remain pending behind its skeleton.
 {
  const page=await browser.newPage({viewport:{width:390,height:900}});let holdSamples=false,releaseSamples;
  const samplesGate=new Promise(resolve=>{releaseSamples=resolve;});
  await page.route('http://oncall.test/**',async route=>{
   const u=new URL(route.request().url());
   if(u.pathname.startsWith('/api/')){
    if(holdSamples&&(u.pathname==='/api/learning'||u.pathname==='/api/learning/samples'||u.pathname==='/api/ai'))await samplesGate;
    await route.fulfill({json:fixtures[u.pathname+u.search]||fixtures[u.pathname]||{}});return;
   }
   try{await route.fulfill({path:root+(u.pathname==='/'?'index.html':u.pathname.replace('/assets/',''))});}catch{await route.fulfill({status:404,body:''});}
  });
  await page.goto('http://oncall.test/#inbox');await page.locator('#alerts .empty').waitFor();holdSamples=true;
  await page.locator('[data-page="samples"]').click();
  assert.equal(await page.locator('[data-panel="samples"]').isVisible(),true);
  assert.equal(await page.locator('#samples .skeleton-sample-row').count()>0,true);
  assert.equal(await page.locator('[data-panel="inbox"]').isVisible(),false);
  releaseSamples();await page.waitForFunction(()=>document.querySelector('#samples')?.dataset.loading==='false');
  console.log('PASS immediate tab navigation');await page.close();
 }
 // Review drawers animate both directions and remain out of the focus order while closed.
 {
  const page=await browser.newPage({viewport:{width:1280,height:900}});
  await page.route('http://oncall.test/**',async route=>{
   const u=new URL(route.request().url());
   if(u.pathname.startsWith('/api/')){
    const data=u.pathname==='/api/learning/samples'?{total:1,counts:{whatsapp:1,telegram:0},items:[{id:1,title:'工作群',text:'需要审核的消息正文',sender:'同事',created:1}]}:fixtures[u.pathname+u.search]||fixtures[u.pathname]||{};
    await route.fulfill({json:data});return;
   }
   try{await route.fulfill({path:root+(u.pathname==='/'?'index.html':u.pathname.replace('/assets/',''))});}catch{await route.fulfill({status:404,body:''});}
  });
  await page.goto('http://oncall.test/#samples');
  await page.locator('.sample-detail-row').waitFor();
  const detail=page.locator('.sample-detail-row'),motion=page.locator('.sample-detail-motion'),toggle=page.locator('.sample-action-col button');
  assert.equal(await detail.getAttribute('aria-hidden'),'true');assert.equal(await detail.getAttribute('inert'),'');
  const closed=await motion.evaluate(n=>n.getBoundingClientRect().height);await toggle.click();await page.waitForTimeout(80);
  const opening=await motion.evaluate(n=>n.getBoundingClientRect().height);await page.waitForTimeout(350);const open=await motion.evaluate(n=>n.getBoundingClientRect().height);
  assert.equal(closed,0);assert(opening>closed&&opening<open);assert.equal(await toggle.getAttribute('aria-expanded'),'true');assert.equal(await detail.getAttribute('inert'),null);
  await toggle.click();await page.waitForTimeout(80);const closing=await motion.evaluate(n=>n.getBoundingClientRect().height);await page.waitForTimeout(350);
  assert(closing>0&&closing<open);assert.equal(await motion.evaluate(n=>n.getBoundingClientRect().height),0);assert.equal(await detail.getAttribute('aria-hidden'),'true');assert.equal(await detail.getAttribute('inert'),'');
  console.log('PASS review drawer animation');await page.close();
 }
 // Confirmation text uses skeletons inside the pre-existing fixed card.
 for(const width of [390,1280]){
  const page=await browser.newPage({viewport:{width,height:900}});
  await page.route('http://oncall.test/**',async route=>{
   const path=new URL(route.request().url()).pathname;
   const file=path==='/confirm'?'confirm.html':path==='/confirm/style.css'?'confirm.css':path==='/confirm/client.js'?'confirmation.js':null;
   if(file)await route.fulfill({path:root+file});else await route.fulfill({status:404,body:''});
  });
  await page.goto('http://oncall.test/confirm?preview=1');
  const heights=[];
  for(const state of ['loading','success','error','notoken']){
   await page.evaluate(state=>setViewState(state),state);
   heights.push(await page.locator('.card').evaluate(n=>n.offsetHeight));
  }
  assert.equal(new Set(heights).size,1);console.log('PASS confirmation',width);await page.close();
 }
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
