// Real Chromium DOM/keyboard/history tests with synthetic API data only.
// No production app lifespan, user DB, credentials, LLM or source calls.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../src/realty_signal/web/index.html'), 'utf8');

(async()=>{
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage({viewport:{width:360,height:800}});
    const errors=[], calls=[];
    page.on('pageerror', e=>errors.push(e.message));
    await page.route('**/*', async route=>{
      const url=new URL(route.request().url());
      if(url.hostname!=='buyer.test') {
        if(url.pathname.includes('echarts')) return route.fulfill({contentType:'application/javascript',body:'window.echarts={init:()=>({resize(){},on(){},setOption(){},clear(){},getOption(){return {}}})};'});
        return route.fulfill({body:'',contentType:url.pathname.endsWith('.js')?'application/javascript':'text/css'});
      }
      if(url.pathname==='/') return route.fulfill({body:html,contentType:'text/html'});
      calls.push(url.pathname);
      let data={ready:false,items:[],listings:[],regions:[],actions:[],message:'합성 테스트 데이터'};
      if(url.pathname==='/api/auth/me') return route.fulfill({status:401,json:{}});
      if(url.pathname==='/api/action-plan') data={actions:[{key:'confirm_power',title:'예산 가정을 확인하세요',cta:'예산 설정',tab:'mypage'}]};
      if(url.pathname==='/api/shortlist') data={ready:true,budget:60000,pyeong:25,candidates:[
        {'단지':'테스트 단지 A',region:'테스트구','예상가':50000,'자금':{'필요현금':30000,'총월상환':105},'근거':'현금 여유 우선'}]};
      if(url.pathname==='/api/geocode') {
        await new Promise(r=>setTimeout(r,180));
        data={coords:{A:[37,127],B:[37.1,127.1]}};
      }
      if(url.pathname==='/api/buying-power/scenario') data={ready:true,'상태':'확인 필요','필요대출':20000,'대출가능상한':30000,'필요현금':30000,'부족':0,'월상환':105,'총월상환':105,'잔여현금':5000,'비상자금':3000};
      return route.fulfill({json:data});
    });
    await page.goto('http://buyer.test/');
    await page.waitForSelector('body[data-auth="1"]');
    await page.evaluate(()=>{
      hideAuthGate(); _profile={'가용자본':30000,'연소득':8000};
      _userEmail='synthetic@example.test'; switchTab('dashboard',true);
      window.addEventListener('hashchange',routeFromHash);
    });
    await page.getByText('단지·실거래 확인',{exact:true}).waitFor();
    assert.equal(await page.locator('#dashShortlistWrap').evaluate(el=>!!el.closest('details')),false);
    for(const url of ['/api/regime','/api/complex-watch','/api/news']) assert(!calls.includes(url),`eager hidden source ${url}`);
    const width=await page.evaluate(()=>({viewport:innerWidth,body:document.body.scrollWidth,root:document.documentElement.scrollWidth}));
    assert(width.body<=width.viewport && width.root<=width.viewport,JSON.stringify(width));
    await page.locator('#dashMarketExtra > summary').click();
    await page.waitForFunction(()=>document.getElementById('dashRegimeWrap').textContent.length>0);
    assert.equal(calls.filter(x=>x==='/api/regime').length,1);
    await page.locator('#dashMarketExtra > summary').click();
    await page.locator('#dashMarketExtra > summary').click();
    assert.equal(calls.filter(x=>x==='/api/regime').length,1);
    await page.getByRole('button',{name:'내 조건',exact:true}).click();
    assert.equal(new URL(page.url()).hash,'#mypage');
    await page.goBack();
    await page.waitForFunction(()=>document.body.className==='tab-dashboard');
    await page.evaluate(()=>openLoanCalc(50000,'합성 후보'));
    await page.getByText('확인 필요 · 가정 기반 추정, 은행 승인 전',{exact:true}).waitFor();
    await page.keyboard.press('Escape');

    // Execute real row rendering and selection against the browser DOM.
    await page.evaluate(()=>{
      document.querySelectorAll('dialog[open]').forEach(d=>d.close());
      document.getElementById('dashBody').innerHTML='<div id="testList"></div>';
      window.__popup=[];
      _ms.test={listId:'testList',items:[{id:'A'},{id:'B'}],coords:{},markers:{},sel:null,
        map:{setView(){}},opt:{summary:x=>({nm:x.id}),detail:x=>`<button>확인 ${x.id}</button>`,geoq:x=>x.id}};
      // Map adapter only: chart vendors/network are outside this UI contract test.
      plotPins=()=>{ for(let i=0;i<2;i++) _ms.test.markers[i]={getElement:()=>null,getLatLng:()=>[37,127],openPopup:()=>window.__popup.push(i),closePopup(){}}; };
      renderMsList('test',[0,1]);
    });
    const a=page.locator('#testList .ms-row').nth(0),b=page.locator('#testList .ms-row').nth(1);
    await a.focus(); await page.keyboard.press('Enter');
    assert.equal(await a.getAttribute('aria-expanded'),'true');
    await page.keyboard.press('Space');
    assert.equal(await a.getAttribute('aria-expanded'),'false');
    await page.waitForTimeout(240);
    assert.deepEqual(await page.evaluate(()=>window.__popup),[]);
    await a.click(); await b.click();
    await page.waitForTimeout(240);
    assert.equal(await a.getAttribute('aria-expanded'),'false');
    assert.equal(await b.getAttribute('aria-expanded'),'true');
    assert.deepEqual(await page.evaluate(()=>window.__popup),[1]);
    await page.getByRole('button',{name:'확인 B',exact:true}).focus();
    await page.keyboard.press('Escape');
    assert.equal(await b.getAttribute('aria-expanded'),'false');
    assert.equal(await b.evaluate(el=>el===document.activeElement),true);
    assert.deepEqual(errors,[]);
    console.log('PASS: Chromium 360px, candidate clarity, lazy fetch, history, loan rendering, keyboard toggle, A→B stale race, Escape focus');
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
