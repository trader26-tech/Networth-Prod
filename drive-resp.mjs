import { chromium } from 'playwright';
const DID='ca72598e121e406b9e122f579b52e43f',RAW='_HzQoITndxLh6HLxmlnDga9CMkHYNY3UWjCNQGdPRKQ';
const TOK='eyJkaWQiOiJjYTcyNTk4ZTEyMWU0MDZiOWUxMjJmNTc5YjUyZTQzZiIsImV4cCI6MTc4NDUzNzY5NH0.4rZErSPx3w0EJ6oFJjJgzJiEXPFOL-KrQWJ6Z_M3GHc',EXP=1784537694;
const b=await chromium.launch();const c=await b.newContext({viewport:{width:1280,height:900}});
await c.addCookies([{name:'nw_device',value:`${DID}.${RAW}`,domain:'localhost',path:'/',sameSite:'Lax'}]);
await c.addInitScript(([t,e])=>{localStorage.setItem('nw_at',t);localStorage.setItem('nw_at_exp',String(e));},[TOK,EXP]);
const p=await c.newPage();
p.on('response',async r=>{if(r.url().includes('/land/plan')){try{const j=await r.json();console.log('RESP saved=',j.totals.saved,'tax_naive=',j.totals.tax_naive,'tax_opt=',j.totals.tax_opt,'house=',j.totals.house,'viaGift=',j.parcels.filter(x=>x.via_gift).length+'/'+j.parcels.length);}catch(e){console.log('resperr',e+'')}}});
p.on('request',r=>{if(r.url().includes('/land/plan')){try{const d=JSON.parse(r.postData());console.log('REQ transfer_ok=',d.prefs.transfer_ok,'house_amount=',d.prefs.house_amount,'nSel=',d.selections.length);}catch(e){}}});
await p.goto('http://localhost:4200/land',{waitUntil:'networkidle'});
await p.waitForTimeout(1500);
await p.locator('.bhoomi-fab').click();await p.waitForTimeout(1500);
const opt=(re)=>p.locator('.lt-options .opt',{hasText:re}).first();
await opt(/Sell a property/i).click();await p.waitForTimeout(1400);
const unsel=p.locator('.pick:not(.on) .pick-tog');const m=await unsel.count();
for(let i=0;i<m;i++){await unsel.nth(0).click({force:true});await p.waitForTimeout(300);}
await opt(/Show the best plan/i).click();await p.waitForTimeout(2500);
await b.close();
