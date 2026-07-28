import { chromium } from 'playwright';
const OUT='/private/tmp/claude-501/-Users-ranjeev-Documents-projects-Networth/64346bbc-e794-4e1f-b4ec-4d46cd78b9a6/scratchpad';
const b=await chromium.launch(); const ctx=await b.newContext({viewport:{width:1400,height:1000},deviceScaleFactor:2});
await ctx.addCookies([{name:'nw_device',value:process.env.NW_DEV,domain:'localhost',path:'/',sameSite:'Lax'}]);
await ctx.addInitScript(([t,e])=>{localStorage.setItem('nw_at',t);localStorage.setItem('nw_at_exp',e);},[process.env.NW_TOK,process.env.NW_EXP]);
const p=await ctx.newPage();
await p.goto('http://localhost:4200/land',{waitUntil:'networkidle'}); await p.waitForTimeout(3000);
await p.screenshot({path:`${OUT}/bhoomi-fab.png`});
await (await p.$('.bhoomi-fab')).click(); await p.waitForTimeout(2500);
await p.screenshot({path:`${OUT}/bhoomi-open.png`});
// click a use case to see cards in the popup
for(const el of await p.$$('.bhoomi-pop .opt')){ if((await el.textContent())?.includes('sell now')){await el.click();break;} }
await p.waitForTimeout(1500);
await p.screenshot({path:`${OUT}/bhoomi-cards.png`});
console.log('fab', !!(await p.$('.bhoomi-fab')), 'pop', !!(await p.$('.bhoomi-pop')), 'w', await p.evaluate(()=>{const e=document.querySelector('.bhoomi-pop'); return e? Math.round(e.getBoundingClientRect().width):0}));
await b.close();
