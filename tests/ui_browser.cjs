// Real Chromium DOM/keyboard/history tests with synthetic API data only.
// No production app lifespan, user DB, credentials, LLM or source calls.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../src/realty_signal/web/index.html'), 'utf8');
const signalV2 = fs.readFileSync(path.join(__dirname, '../src/realty_signal/web/signal-v2.js'), 'utf8');

(async()=>{
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage({viewport:{width:360,height:800}});
    const errors=[], calls=[], quotePayloads=[], nickPayloads=[], eventPayloads=[], discoveryPayloads=[];
    const watched=new Set(['급매:synthetic-1']);
    let entranceChosen=false;
    page.on('pageerror', e=>errors.push(e.message));
    await page.route('**/*', async route=>{
      const url=new URL(route.request().url());
      if(url.hostname!=='buyer.test') {
        if(url.pathname.includes('echarts')) return route.fulfill({contentType:'application/javascript',body:'window.echarts={init:()=>({resize(){},on(){},setOption(){},clear(){},getOption(){return {}}})};'});
        return route.fulfill({body:'',contentType:url.pathname.endsWith('.js')?'application/javascript':'text/css'});
      }
      if(url.pathname==='/') return route.fulfill({body:html,contentType:'text/html'});
      if(url.pathname==='/assets/signal-v2.js') return route.fulfill({body:signalV2,contentType:'application/javascript'});
      calls.push(url.pathname);
      let data={ready:false,items:[],listings:[],regions:[],actions:[],message:'합성 테스트 데이터'};
      if(url.pathname==='/api/auth/me') return route.fulfill({status:401,json:{}});
      if(url.pathname==='/api/events'){
        eventPayloads.push(route.request().postDataJSON());
        return route.fulfill({json:{ok:true}});
      }
      const decoded=decodeURIComponent(url.pathname);
      if(['/api/v2/regions/테스트구/report','/api/v2/regions/kb:1114000000/report'].includes(decoded)) data={type:'region',asof:'2026-09-28',
        assessment:{display_grade:'매수',assessment_status:'ready',scope_note:'테스트 권역 자료',
          summary:'지역 신호만 보여 줍니다. 개별 매물의 가격 판단은 별도입니다.',raw_grade:'BUY',
          reasons:[{reason_id:'jeonse_pressure',label:'전세수급 압력',value:180,threshold:170,unit:'지수',role:'driver',passing:true}],
          change:{type:'first_observation',changed_reasons:[]}},
        positive:[{reason_id:'jeonse_pressure',label:'전세수급 압력',value:180,threshold:170,unit:'지수',role:'driver',passing:true}],
        cautions:[{reason_id:'buyer_interest',label:'매수심리 관찰선 미충족',value:68,threshold:70,unit:'지수',role:'driver',passing:false}],
        unknowns:[]};
      if(decoded==='/api/series/테스트구') data={metrics:{},volume:null};
      if(decoded==='/api/complex/테스트구/테스트단지') data={단지명:'테스트단지',identity_status:'single_observed',
        평형별:[{평형:26,'전용㎡':84.9,최근매매:50000,평단가:2000,매매건수:4,
          비교거래:{상태:'관측',건수:4,중앙값:50000,최저:45000,최고:55000,거래월범위:'2026-08~2026-09'}}],
        매매추이:[{ym:'2026-08',평단가:2000,건수:4}],최근평단가:2000,총거래:4,기간:'2026-08',추세pct:0};
      if(decoded==='/api/complex/테스트구/테스트단지/quote-check') {
        quotePayloads.push(route.request().postDataJSON());
        data={상태:'관측비교',입력호가:48000,입력층:11,
          중앙값:50000,표본수:4,호가차액:-2000,호가차이율:-4,거래월범위:'2026-08~2026-09'};
      }
      if(url.pathname==='/api/action-plan') data={actions:[{key:'confirm_power',title:'예산 가정을 확인하세요',cta:'예산 설정',tab:'mypage'}]};
      if(url.pathname==='/api/listings/all') data={listings:[],asof:'2026-09-21',meta:{private_access:false,data_age_days:7}};
      if(url.pathname==='/api/v2/discovery') {
        const spec=route.request().postDataJSON()||{};
        discoveryPayloads.push(spec);
        const next=!!spec.cursor, finance=!!spec.max_monthly_manwon;
        const noMatch=spec.region==='조건없음';
        const noProfile=finance&&spec.prefer_region==='프로필없음';
        const preferred=!!spec.prefer_max_price_manwon||!!spec.prefer_min_area_m2;
        const candidate={listing:{key:next?'일반매물:synthetic-2':'일반매물:synthetic-1',
          name:next?'두번째 후보':'첫번째 후보',region:'테스트구',kind:'일반매물',asking_manwon:50000},
          recommendation_reason:'호가 조건 부합',tradeoff:'자료 확인',verify_next:'판매 여부 확인',
          preference:preferred?{region:'테스트구',matched:true,score:spec.priority==='price'?75:67,coverage:spec.priority==='price'?75:67,
            priority:spec.priority||'balanced',
            satisfied:2,known:2,total:3,details:[
              {field:'region',label:'선호 지역',status:'matched'},
              {field:'price',label:'선호 호가',status:'matched'},
              {field:'area',label:'선호 면적',status:'unknown'}]}:
            {region:'테스트구',matched:true,coverage:100},
          finance:finance?noProfile?{status:'no_confirmed_profile',reason:'매수력 확정 필요'}:
            {status:'policy_unverified',monthly_manwon:120,cash_manwon:20000,
              reason:'대출 규제·세율 최신성 미검증'}:null};
        const exceededCandidate={...candidate,listing:{...candidate.listing,key:'일반매물:synthetic-exceeded',
          name:'조건초과 후보',asking_manwon:70000},eligibility:'exceeded',
          tradeoff:noMatch?'선택한 필수 지역 밖의 매물입니다.':'호가가 설정한 상한보다 높습니다.'};
        data={private_access:true,source_state:'partial',coverage:{regions:['테스트구']},
          sources:[{kind:'일반매물',state:'partial'},{kind:'급매',state:'failed'}],
          counts:finance?{matched:0,verify:1,explore:0,exceeded:1}:{matched:noMatch?0:2,verify:0,explore:0,exceeded:1},
          next_cursor:finance||next?null:'synthetic-next',
          finance_context:finance?{status:noProfile?'no_confirmed_profile':'ready',policy_status:'unverified'}:null,
          groups:{matched:finance||noMatch?[]:[candidate],verify:finance?[candidate]:[],explore:[],
            exceeded:spec.include_exceeded?[exceededCandidate]:[]}};
      }
      if(url.pathname==='/api/v2/listings/report' && url.searchParams.get('key')==='일반매물:forbidden')
        return route.fulfill({status:403,json:{detail:'forbidden'}});
      if(url.pathname==='/api/v2/listings/report') data={type:'listing',report_id:'synthetic-report',
        subject:{key:url.searchParams.get('key'),name:'한방테스트단지',region:'테스트구',
          kind:'일반매물',asking_manwon:50000,collected_at:'2026-09-29'},
        price:{'상태':'관측비교','표본수':3},buyer_fit:{status:'unknown'},
        positive:[{text:'동일 조건 실거래가 있습니다.'}],
        cautions:[{text:'현재 판매 여부는 확인되지 않았습니다.'}],
        next_actions:['실제 호가 확인'],evidence:[{label:'국토부 실거래',asof:'2026-09-29',status:'관측'}]};
      if(url.pathname==='/api/general-listings') data={ready:true,state:'partial',regions:['테스트구'],last_success_at:1780000000,
        refresh:{limited_regions:['테스트구'],failed_requests:0},listings:[{hanbang_id:'synthetic-hb-1',단지명:'한방테스트단지',지역:'테스트구',호가:50000,전용면적:84.5,층:12,등록일:'2026-09-29'}]};
      if(url.pathname==='/api/listing-analysis'){
        const second=url.searchParams.get('key')==='일반매물:synthetic-hb-2';
        const listing={key:second?'일반매물:synthetic-hb-2':'일반매물:synthetic-hb-1',kind:'일반매물',
          name:second?'두번째테스트단지':'한방테스트단지',region:'테스트구',
          asking_manwon:second?47000:50000,exclusive_m2:84.5,floor:12,source:'hanbang',collected_at:'2026-09-29',coordinate:[37.65,127.07]};
        const buyer_fit={status:second?'within':'above',gap_manwon:second?1000:-2000,
          note:'호가와 확정 매수력만 비교합니다. 취득세·대출 승인·수리비는 별도 확인해야 합니다.'};
        data=url.searchParams.get('stage')==='base'?{status:'base',listing,buyer_fit}:{status:'ready',listing,buyer_fit,
          price:{상태:'관측비교',중앙값:49000,표본수:3,호가차이율:2,비교기준일:'2026-09-29',비교기준:'동일 단지·면적'},
          trades:[{month:'2026-09',price_manwon:48000,floor:10},{month:'2026-09',price_manwon:49000,floor:12},
            {month:'2026-09',price_manwon:50000,floor:13}],complex:{총거래:3},building:{},
          pros:[],cautions:[{text:'호가는 비교거래보다 높습니다.',evidence:'trades'}],
          mobility:{reason:'출입구 미확인'},school:{reason:'통학구역 미확인'},
          amenities:{reason:'시설 미확인'},development:{reason:'사업자료 미확인'},
          questions:['현재 판매 가능 여부는?'],evidence:[{label:'국토부 실거래',asof:'2026-09-29',status:'관측'}]};
      }
      if(url.pathname==='/api/listing-compare'){
        const keys=route.request().postDataJSON().keys;
        data={items:keys.map((key,i)=>({listing:{key,kind:'일반매물',name:i?'두번째테스트단지':'한방테스트단지',
          region:'테스트구',asking_manwon:i?47000:50000,exclusive_m2:84.5,floor:12,collected_at:'2026-09-29'},
          asking_per_m2_manwon:i?556.2:591.7,budget_fit:'unknown',building:{},
          price:{상태:'관측비교',호가차이율:i?-4:2,표본수:3,중앙값:49000,비교기준일:'2026-09-29'}})),
          warnings:['확정 매수력이 없습니다.'],basis:'같은 면적 국토부 실거래',not_compared:['수리 상태']};
      }
      if(url.pathname==='/api/listing-discovery') data={
        alternatives:[{listing:{key:'일반매물:synthetic-hb-2',kind:'일반매물',name:'두번째테스트단지',
          asking_manwon:47000,exclusive_m2:84.5,collected_at:'2026-09-29'},reason:'같은 지역·비슷한 전용면적·호가대'}],
        headlines:[{title:'테스트구 주택 소식',url:'https://news.example/story',published_at:'2026-09-28',
          match:'지역명 문자열 일치(단지 관련 미확인)'}],
        source_note:'현재 수집분 기준',news_note:'사업 단계 미확인'};
      if(url.pathname==='/api/listing-discovery/commute') data={status:'partial',
        selected:{key:'일반매물:synthetic-hb-1',status:'observed',minutes:39},
        alternatives:[{key:'일반매물:synthetic-hb-2',status:'observed',minutes:31}],
        note:'실제 출퇴근 시간대를 보증하지 않습니다.'};
      if(url.pathname==='/api/listing-kapt') data={status:'observed',source:'K-APT',
        source_url:'https://www.data.go.kr/data/15058453/openapi.do',name:'한방테스트단지',
        households:220,builder:'시험건설',parking_spaces:150,parking_per_household:0.68,
        retrieved_at:'2026-09-29',address:'테스트구 합성 주소',note:'주차 가능 여부를 뜻하지 않습니다.'};
      if(url.pathname==='/api/listing-entrance'){
        entranceChosen=route.request().method()==='PUT';
        data={ok:true};
      }
      if(url.pathname==='/api/listing-location') data={
        listing:{name:'한방테스트단지'},
        school:{status:'candidate',reason:'매물 표시 좌표 기준 후보',boundary_near:false,zones:[
          {name:'테스트 통학구역',schools:[{name:'테스트초'}]}]},
        mobility:{status:'partial',origin_source:entranceChosen?'user_marked_candidate':'listing_point',
          reason:entranceChosen?'내가 지도에서 지정한 출입구 후보 기준입니다.':'출입구 미확인',
          station_walk:{status:'observed',destination:'테스트역',minutes:8,distance_m:620,path:[]}},
        amenities:{status:'partial',by_category:{SW8:{label:'지하철역',places:[{name:'테스트역',distance_m:550}]}}},
        evidence:[{label:'학구도안내서비스',status:'표시 좌표 기준 후보',asof:'2026-03-20'}]};
      if(url.pathname==='/api/advisor/stream'){
        nickPayloads.push(route.request().postDataJSON());
        return route.fulfill({contentType:'text/event-stream',body:'data: {"type":"delta","text":"선택 매물의 가격을 확인하세요."}\n\ndata: {"type":"done","used":["get_selected_listing_report"]}\n\n'});
      }
      if(url.pathname==='/api/listing-watch') {
        if(route.request().method()==='POST') {
          watched.add(route.request().postDataJSON().key);
          return route.fulfill({json:{ok:true}});
        }
        if(route.request().method()==='DELETE') {
          watched.delete(url.searchParams.get('key'));
          return route.fulfill({json:{ok:true}});
        }
        data={items:[...watched].map(key=>({key,kind:key.split(':')[0],name:'테스트단지',region:'테스트구',saved_price:60000,
          price_change:-10000,current:{key,kind:key.split(':')[0],name:'테스트단지',region:'테스트구',price:50000},
          alternatives:key==='급매:synthetic-1'?[{key:'급매:synthetic-2',kind:'급매',name:'테스트단지',region:'테스트구',price:52000,reason:'같은 단지의 다른 매물'}]:[]}))};
      }
      if(url.pathname==='/api/asks') data={ready:true,budget:61000,asks:[
        {'단지명':'호가단지','지역':'테스트구','유형':'급매','총액':48000,
          lines:{cash:'필요현금',price:'원천 호가',unknown:'은행 심사',next:'호가 확인'}}]};
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
    await page.getByText('호가단지',{exact:true}).waitFor();
    assert.equal(await page.locator('#dashShortlistWrap').evaluate(el=>!!el.closest('details')),false);
    for(const url of ['/api/regime','/api/complex-watch','/api/news','/api/shortlist']) assert(!calls.includes(url),`eager hidden source ${url}`);
    const width=await page.evaluate(()=>({viewport:innerWidth,body:document.body.scrollWidth,root:document.documentElement.scrollWidth}));
    assert(width.body<=width.viewport && width.root<=width.viewport,JSON.stringify(width));
    await page.locator('#dashMarketExtra > summary').click();
    await page.waitForFunction(()=>document.getElementById('dashRegimeWrap').textContent.length>0);
    assert.equal(calls.filter(x=>x==='/api/regime').length,1);
    await page.locator('#dashMarketExtra > summary').click();
    await page.locator('#dashMarketExtra > summary').click();
    assert.equal(calls.filter(x=>x==='/api/regime').length,1);
    await page.locator('#dashExtra > summary').click();
    await page.getByText('가격대별 동네 참고',{exact:true}).waitFor();
    assert.equal(calls.filter(x=>x==='/api/shortlist').length,0);
    await page.getByRole('button',{name:'내 조건',exact:true}).click();
    assert.equal(new URL(page.url()).hash,'#mypage');
    await page.goBack();
    await page.waitForFunction(()=>document.body.className==='tab-dashboard');
    await page.evaluate(()=>{document.getElementById('groupSubnav').style.display='flex'; renderGroupSubnav('browse','all');});
    assert.equal(await page.locator('#groupSubTabs').getByRole('button',{name:/매물/}).count(),1);
    assert.equal(await page.locator('#groupSubTabs').getByRole('button',{name:/시그널/}).count(),1);
    assert.equal(await page.locator('#groupSubTabs').getByRole('button',{name:/분석·전략/}).count(),0);
    assert.equal(await page.locator('#groupSubTabs').getByRole('button',{name:/경매·재건축/}).count(),0);
    assert.equal(await page.locator('#groupSubTabs [data-sub="auction"]').count(),0);
    assert.equal(await page.locator('#groupSubTabs [data-sub="report"]').count(),0);
    const navWidth=await page.evaluate(()=>document.documentElement.scrollWidth);
    assert(navWidth<=360,`browse navigation overflows mobile viewport: ${navWidth}`);
    await page.evaluate(()=>{mapSplit=()=>{}; switchTab('all');});
    await page.getByText('일반·급매 매물은 지정된 개인 계정에서만 보입니다.',{exact:false}).waitFor();
    assert.match(await page.locator('#laStatus').textContent(),/지정된 개인 계정만/);
    await page.evaluate(()=>{window.__generalRows=[]; mapSplit=(listId,mapId,items,opt)=>{
      window.__generalRows=items; document.getElementById(listId).innerHTML=opt.summary(items[0]).nm+opt.detail(items[0]);
    }; switchTab('general');});
    await page.getByText('한방테스트단지').waitFor();
    assert(calls.includes('/api/general-listings'));
    assert.match(await page.locator('#hbStatus').textContent(),/전체 매물 아님/);
    assert.equal(await page.locator('#view-general .watch-btn').count(),1);
    await page.evaluate(()=>switchTab('watch'));
    await page.getByText('찜 당시보다 하락 1.0억').waitFor();
    assert.match(await page.locator('#watchList').textContent(),/같은 단지의 다른 매물/);
    assert.equal(await page.locator('#view-watch .watch-btn').getAttribute('aria-pressed'),'true');
    const generalCalls=calls.filter(x=>x==='/api/general-listings').length;
    await page.evaluate(()=>{_ms.hbMap={map:{invalidateSize(){}}}; switchTab('general');});
    assert.equal(calls.filter(x=>x==='/api/general-listings').length,generalCalls);
    await page.evaluate(()=>switchTab('dashboard'));
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
    await page.evaluate(()=>{
      _ms.test.items=Array.from({length:130},(_,i)=>({id:String(i)}));
      renderMsList('test',Array.from({length:130},(_,i)=>i));
    });
    assert.equal(await page.locator('#testList .ms-row').count(),60);
    await page.locator('#testList').getByRole('button',{name:/더 보기/}).click();
    assert.equal(await page.locator('#testList .ms-row').count(),120);
    await page.evaluate(async()=>{
      loadComplexAgents=()=>{}; loadCxBuilding=()=>{}; loadCxLoanScen=()=>{}; loadCxTxCosts=()=>{};
      await openComplex('테스트단지','테스트구');
    });
    await page.getByText('내가 확인한 호가와 비교하기',{exact:true}).click();
    await page.locator('#cxQuoteFloor').fill('11');
    await page.locator('#cxQuoteAmount').fill('48000');
    await page.locator('#cxQuoteBtn').click();
    await page.getByText('산술 차이',{exact:false}).waitFor();
    assert.match(await page.locator('#cxQuoteResult').textContent(),/2,000만.*낮음/);
    assert.match(await page.locator('#cxQuoteResult').textContent(),/11층 ±2층/);
    await page.keyboard.press('Escape');
    await page.evaluate(()=>{
      document.body.insertAdjacentHTML('beforeend', '<div id="qsEvidenceFixture">'
        +qsEvidenceBtn({단지명:'테스트단지',지역:'테스트구',호가:48000,전용면적:84.9,층:11})+'</div>');
    });
    await page.locator('#qsEvidenceFixture .qs-evidence').click();
    await page.getByText('산술 차이',{exact:false}).waitFor();
    assert.equal(await page.locator('#cxQuoteDetails').getAttribute('open'),'');
    assert.equal(await page.locator('#cxQuoteArea').inputValue(),'84.9');
    assert.equal(await page.locator('#cxQuoteFloor').inputValue(),'11');
    assert.deepEqual(quotePayloads.at(-1),{asking:48000,exclusive_m2:84.9,floor:11});
    await page.keyboard.press('Escape');
    await page.evaluate(()=>{
      document.getElementById('qsEvidenceFixture').innerHTML=qsEvidenceBtn(
        {단지명:'테스트단지',지역:'테스트구',호가:48000,전용면적:59.9,층:11});
    });
    const callsBefore=quotePayloads.length;
    await page.locator('#qsEvidenceFixture .qs-evidence').click();
    await page.getByText('다른 면적의 거래로 대신 비교하지 않습니다.',{exact:false}).waitFor();
    assert.equal(quotePayloads.length,callsBefore);
    await page.keyboard.press('Escape');
    await page.evaluate(()=>document.body.insertAdjacentHTML('beforeend',
      '<div id="analysisFixture">'+reportBtn('일반매물:synthetic-hb-1')+'</div>'));
    await page.locator('#analysisFixture button').click();
    await page.locator('#v2ReportBody').getByText('한방테스트단지',{exact:false}).waitFor();
    assert.equal(nickPayloads.length,0);
    await page.evaluate(()=>{
      Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async value=>{window.__copiedReportLink=value;}}});
    });
    await page.locator('#v2ReportBody').getByRole('button',{name:'내 계정에서 열기 링크 복사'}).click();
    await page.getByText('링크를 복사했습니다.',{exact:false}).waitFor();
    const link=await page.evaluate(()=>window.__copiedReportLink);
    assert.equal(new URL(link).searchParams.get('listing'),'일반매물:synthetic-hb-1');
    assert.equal(new URL(link).hash,'#all');
    assert.equal(new URL(link).searchParams.has('invite'),false);
    await page.locator('#v2ReportDlg').getByRole('button',{name:'리포트 닫기'}).click();
    await page.evaluate(value=>{
      history.replaceState(null,'',value);
      SignalV2.openInitialLink();
    },link);
    await page.locator('#v2ReportBody').getByText('한방테스트단지',{exact:false}).waitFor();
    assert.equal(await page.locator('#v2ReportDlg').evaluate(el=>el.open),true);
    await page.locator('#v2ReportDlg').getByRole('button',{name:'리포트 닫기'}).click();
    await page.waitForFunction(()=>!new URLSearchParams(location.search).has('listing'));
    await page.evaluate(()=>{
      history.replaceState(null,'','/?listing='+encodeURIComponent('일반매물:forbidden')+'#all');
      SignalV2.openInitialLink();
    });
    await page.locator('#v2ReportBody').getByText('이 매물에 접근할 수 없습니다.').waitFor();
    await page.locator('#v2ReportDlg').getByRole('button',{name:'리포트 닫기'}).click();
    await page.waitForFunction(()=>!new URLSearchParams(location.search).has('listing'));
    await page.evaluate(()=>openListingReport('일반매물:synthetic-hb-1'));
    await page.getByText('한방테스트단지',{exact:true}).last().waitFor();
    await page.getByText('동일 면적 거래 3건',{exact:false}).waitFor();
    await page.getByText('살 수 있는 가격보다 비쌈',{exact:false}).waitFor();
    assert.equal(calls.includes('/api/listing-kapt'),false);
    await page.locator('#advReport details[data-analysis="complex"] > summary').click();
    await page.getByRole('button',{name:'K-APT 세대수·시공사·주차 확인'}).click();
    await page.getByText('등록 주차면 150면',{exact:false}).waitFor();
    assert.equal(calls.filter(x=>x==='/api/listing-kapt').length,1);
    assert.equal(await page.locator('#advReport details[data-analysis="amenities"]').getAttribute('open'),null);
    await page.locator('#advReport details[data-analysis="discovery"] > summary').click();
    await page.getByRole('button',{name:'비슷한 매물·관련 뉴스 보기'}).click();
    await page.getByText('사업 단계 미확인',{exact:false}).waitFor();
    assert.equal(calls.includes('/api/listing-discovery/commute'),false);
    await page.getByRole('button',{name:'내 직장까지 통근 비교'}).click();
    await page.getByText('선택 매물보다 8분 짧음',{exact:false}).waitFor();
    await page.getByText('두번째테스트단지',{exact:false}).first().waitFor();
    await page.getByText('테스트초',{exact:false}).waitFor();
    await page.getByText('테스트역 ·',{exact:false}).first().waitFor();
    await page.evaluate(()=>{
      _ms.hbMap={map:{once(_event,fn){window.__pickEntrance=fn;},off(){},setView(){}}};
    });
    await page.getByRole('button',{name:'지도에서 출입구 후보 지정'}).click();
    assert.equal(await page.locator('#advPanel').evaluate(el=>el.style.display),'none');
    page.once('dialog',dlg=>dlg.accept());
    await page.evaluate(()=>window.__pickEntrance({latlng:{lat:37.6505,lng:127.0705}}));
    await page.getByText('내가 지도에서 지정한 출입구 후보 기준입니다.',{exact:false}).waitFor();
    assert.equal(await page.locator('#advPanel').evaluate(el=>el.style.display),'flex');
    await page.getByRole('button',{name:'내 지정 지우기'}).click();
    await page.getByRole('button',{name:'지도에서 출입구 후보 지정',exact:true}).waitFor();
    assert.equal(await page.locator('#advPanel').evaluate(el=>el.classList.contains('adv-report-mode')),true);
    await page.getByRole('button',{name:'닉과 대화'}).click();
    await page.getByText('선택 매물의 가격을 확인하세요.',{exact:false}).waitFor();
    assert.equal(nickPayloads.at(-1).listing_key,'일반매물:synthetic-hb-1');
    await page.getByRole('button',{name:'매물 리포트'}).click();
    await page.getByRole('button',{name:'＋ 비교함 담기'}).click();
    await page.evaluate(()=>openListingReport('일반매물:synthetic-hb-2'));
    await page.getByText('두번째테스트단지',{exact:true}).last().waitFor();
    await page.getByRole('button',{name:'＋ 비교함 담기'}).click();
    await page.getByRole('button',{name:'선택 매물 비교 2/3'}).click();
    await page.getByText('같은 면적 국토부 실거래',{exact:false}).waitFor();
    assert.equal(await page.locator('#listingCompareBody thead th').count(),3);
    await page.locator('#listingCompareDlg').getByRole('button',{name:'교통·학교 차이 확인'}).click();
    await page.locator('#listingCompareLocation').getByText('통학구역 후보 테스트초',{exact:false}).first().waitFor();
    const compareRequest=page.waitForRequest(req=>req.url().includes('/api/advisor/stream')&&req.postDataJSON()?.comparison_keys?.length===2);
    await page.locator('#listingCompareDlg').getByRole('button',{name:'닉에게 차이 묻기'}).click();
    assert.deepEqual((await compareRequest).postDataJSON().comparison_keys,['일반매물:synthetic-hb-1','일반매물:synthetic-hb-2']);
    assert(eventPayloads.some(x=>x.name==='listing_analysis_ready'));
    assert(eventPayloads.some(x=>x.name==='listing_evidence_open'&&x.props.section==='complex'));
    assert(eventPayloads.some(x=>x.name==='listing_commute_compare'));
    assert(eventPayloads.some(x=>x.name==='listing_compare_open'));
    assert(!JSON.stringify(eventPayloads).includes('synthetic-hb-1'));
    const held=await page.evaluate(()=>{
      const row={region:'테스트구',signal:'BUY',display_signal:'HELD',assessment_status:'held',group:'서울'};
      const label=displaySignal(row);
      return {label,badge:badge(label),style:_choStyle('signal','테스트구',{},null,{테스트구:row}),
        tip:_choTip('signal','테스트구',{},null,{테스트구:row})};
    });
    assert.equal(held.label,'HELD');
    assert.match(held.badge,/판단 보류/);
    assert.equal(held.style.fillColor,'#5f6875');
    assert.match(held.tip,/판단 보류/);
    const complexHeld=await page.evaluate(()=>_cxSigBanner({등급:'HELD',점수:null,판정상태:'held',주의:'지역 판정 보류'}));
    assert.match(complexHeld,/판단 보류/);
    assert.match(complexHeld,/지역 판정 보류/);
    assert.doesNotMatch(complexHeld,/null|undefined|지역 <b/);
    const guarded=await page.evaluate(()=>{
      allSignals=[{region:'테스트구',signal:'BUY',display_signal:'BUY',assessment_status:'held'}];
      _mtBuyOnly=true;
      const result={badge:safeMarketSignal('테스트구'),buyOnlyPass:_mtPass('테스트구'),
        unknown:displaySignal({region:'미확인구',signal:'STRONG_BUY'})};
      _mtBuyOnly=false;
      return result;
    });
    assert.deepEqual(guarded,{badge:'HELD',buyOnlyPass:false,unknown:'HELD'});
    await page.evaluate(()=>{
      document.getElementById('advPanel').style.display='none';
      document.querySelectorAll('dialog[open]').forEach(dialog=>dialog.close());
      meta={last_date:'2026-09-28',zones:{jeonse_supply:[],buyer_idx_strong:70,buyer_demand_buy:20}};
      allSignals=[{region:'테스트구',region_id:'kb:1114000000',group:'서울',signal:'BUY',display_signal:'BUY',assessment_status:'ready',급지:'B',전세수급:180,매수우위지수:68}];
      switchTab('signal'); renderList(); selectRegion('테스트구');
    });
    await page.getByText('지역 신호만 보여 줍니다.',{exact:false}).waitFor();
    assert(calls.some(x=>decodeURIComponent(x)==='/api/v2/regions/kb:1114000000/report'));
    assert.equal(await page.locator('#signalPanelReasons').isVisible(),true);
    assert.equal(await page.locator('#signalPanelTrend').isVisible(),false);
    assert.equal(await page.locator('#signalPanelMap').isVisible(),false);
    assert.equal(await page.locator('#haesolPanel').evaluate(el=>getComputedStyle(el).maxHeight),'none');
    assert.equal(await page.locator('#signalPanelReasons').evaluate(el=>getComputedStyle(el).overflowY),'auto');
    assert.equal(calls.some(x=>decodeURIComponent(x)==='/api/series/테스트구'),false);
    assert.equal(await page.locator('#signalSide').evaluate(el=>el.inert),true);
    await page.locator('#sideToggle').click();
    assert.equal(await page.locator('#signalSide').evaluate(el=>el.inert),false);
    assert.equal(await page.locator('#sideToggle').getAttribute('aria-expanded'),'true');
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#signalSide').evaluate(el=>el.inert),true);
    await page.locator('#sideToggle').click();
    await page.locator('#signalSide #list .row').first().click();
    assert.equal(await page.locator('#signalSide').evaluate(el=>el.inert),true);
    assert.equal(await page.locator('#sigbadge').evaluate(el=>el===document.activeElement),true);
    await page.getByRole('tab',{name:'가격·수급 추세'}).click();
    await page.waitForFunction(()=>document.getElementById('signalTrendStatus').textContent==='');
    assert.equal(calls.filter(x=>decodeURIComponent(x)==='/api/series/테스트구').length,1);
    assert.equal(await page.locator('#signalPanelReasons').isVisible(),false);
    await page.getByRole('tab',{name:'지역 지도'}).click();
    assert.equal(await page.locator('#signalPanelMap').isVisible(),true);
    assert.equal(await page.locator('#signalPanelTrend').isVisible(),false);
    await page.getByRole('tab',{name:'판정 근거'}).click();
    assert.equal(await page.locator('#signalPanelReasons').isVisible(),true);
    await page.getByRole('tab',{name:'판정 근거'}).focus();
    await page.keyboard.press('ArrowLeft');
    assert.equal(await page.getByRole('tab',{name:'지역 지도'}).getAttribute('aria-selected'),'true');
    await page.getByRole('tab',{name:'판정 근거'}).click();
    await page.setViewportSize({width:1280,height:800});
    assert.equal(await page.locator('#signalSide').isVisible(),true);
    assert.equal(await page.locator('#signalPanelMap').isVisible(),false);
    await page.setViewportSize({width:390,height:800});
    await page.waitForFunction(()=>document.getElementById('signalSide').inert);
    assert((await page.evaluate(()=>document.documentElement.scrollWidth))<=390);
    assert((await page.locator('#signalPanelReasons').evaluate(el=>el.getBoundingClientRect().bottom))<=801);
    await page.setViewportSize({width:360,height:800});
    await page.waitForFunction(()=>document.getElementById('signalSide').inert);
    assert.equal(await page.locator('#signalSide').evaluate(el=>el.inert),true);
    assert((await page.locator('#signalPanelReasons').evaluate(el=>el.getBoundingClientRect().bottom))<=801);
    const reportWidth=await page.evaluate(()=>document.documentElement.scrollWidth);
    assert(reportWidth<=360,`analysis panel overflows mobile viewport: ${reportWidth}`);
    await page.evaluate(()=>SignalV2.openDiscovery('테스트구'));
    await page.locator('#v2DiscoverForm').getByRole('button',{name:'후보 찾기'}).click();
    await page.getByText('첫번째 후보').waitFor();
    assert.match(await page.locator('#v2DiscoverResults').textContent(),/일부 원천이 실패·제한/);
    assert.equal(await page.locator('#v2DiscoverResults [data-v2-card]').count(),1);
    const firstWatch=page.locator('#v2DiscoverResults [data-watch-key="일반매물:synthetic-1"]');
    await firstWatch.click();
    await page.waitForFunction(()=>document.querySelector('#v2DiscoverResults [data-watch-key="일반매물:synthetic-1"]')?.getAttribute('aria-pressed')==='true');
    assert(watched.has('일반매물:synthetic-1'));
    await page.locator('#v2DiscoverResults [data-v2-next]').click();
    await page.getByText('두번째 후보').waitFor();
    assert.equal(await page.locator('#v2DiscoverResults [data-v2-card]').count(),2);
    assert.equal(await page.locator('#v2DiscoverResults [data-v2-next]').count(),0);
    await page.locator('#v2DiscoverResults [data-v2-listing="일반매물:synthetic-1"]').click();
    const reportWatch=page.locator('#v2ReportBody [data-watch-key="일반매물:synthetic-1"]');
    await reportWatch.waitFor();
    assert.equal(await reportWatch.getAttribute('aria-pressed'),'true');
    await reportWatch.click();
    await page.waitForFunction(()=>document.querySelector('#v2ReportBody [data-watch-key="일반매물:synthetic-1"]')?.getAttribute('aria-pressed')==='false');
    assert.equal(watched.has('일반매물:synthetic-1'),false);
    await page.locator('#v2ReportDlg').getByRole('button',{name:'리포트 닫기'}).click();
    await page.evaluate(()=>SignalV2.openDiscovery());
    await page.locator('#v2DiscoverForm').getByRole('button',{name:'후보 찾기'}).click();
    await page.getByText('첫번째 후보').waitFor();
    assert.equal(await page.locator('#v2DiscoverResults [data-watch-key="일반매물:synthetic-1"]').getAttribute('aria-pressed'),'false');
    await page.locator('#v2DiscoverForm [name=max_monthly_manwon]').fill('200');
    await page.locator('#v2DiscoverForm').getByRole('button',{name:'후보 찾기'}).click();
    await page.getByText(/총 월 상환 약 120만원/).waitFor();
    assert.match(await page.locator('#v2DiscoverResults').textContent(),/후보는 확인 필요로 분류/);
    assert.match(await page.locator('#v2DiscoverResults h3').textContent(),/확인 필요/);
    await page.locator('#v2DiscoverForm [name=max_monthly_manwon]').fill('');
    await page.locator('#v2DiscoverForm .v2-preferences summary').click();
    await page.locator('#v2DiscoverForm [name=prefer_max_price_manwon]').fill('50000');
    await page.locator('#v2DiscoverForm [name=prefer_min_area_m2]').fill('84');
    await page.locator('#v2DiscoverForm').getByRole('button',{name:'후보 찾기'}).click();
    await page.getByText(/선호 2\/3개 충족 · 2\/3개 자료 확인/).waitFor();
    await page.locator('#v2DiscoverForm [name=priority]').selectOption('price');
    await page.locator('#v2DiscoverForm').getByRole('button',{name:'후보 찾기'}).click();
    await page.getByText(/호가 우선\(2배\): 적합도 75\/100 · 확인도 75\/100/).waitFor();
    assert.equal(discoveryPayloads.at(-1).priority,'price');
    assert.equal(await page.getByText('조건초과 후보').count(),0);
    await page.locator('#v2DiscoverForm [name=include_exceeded]').check();
    await page.locator('#v2DiscoverForm').getByRole('button',{name:'후보 찾기'}).click();
    await page.getByText('조건초과 후보').waitFor();
    assert.match(await page.locator('#v2DiscoverResults [data-v2-group="exceeded"]').textContent(),/구매 가능 추천이 아닙니다/);
    assert.equal(discoveryPayloads.at(-1).include_exceeded,true);
    await page.locator('#v2DiscoverForm [name=include_exceeded]').uncheck();
    await page.locator('#v2DiscoverForm [name=region_mode]').selectOption('must');
    await page.locator('#v2DiscoverForm [name=region]').fill('조건없음');
    await page.locator('#v2DiscoverForm').getByRole('button',{name:'후보 찾기'}).click();
    await page.getByText(/조건 초과 1건을 비교용으로 보려면/).waitFor();
    assert.equal(await page.locator('#v2DiscoverResults [data-v2-card]').count(),0);
    await page.locator('#v2DiscoverForm [name=region_mode]').selectOption('prefer');
    await page.locator('#v2DiscoverForm [name=region]').fill('테스트구');
    await page.locator('#v2DiscoverForm [name=region]').fill('프로필없음');
    await page.locator('#v2DiscoverForm [name=max_monthly_manwon]').fill('200');
    await page.locator('#v2DiscoverForm').getByRole('button',{name:'후보 찾기'}).click();
    await page.locator('[data-v2-finance-setup]').click();
    assert.equal(await page.locator('#v2DiscoverDlg').evaluate(el=>el.open),false);
    assert.equal(await page.locator('#view-mypage').isVisible(),true);
    assert.deepEqual(errors,[]);
    console.log('PASS: Chromium 360px, candidate clarity, lazy fetch, history, loan rendering, keyboard toggle, A→B stale race, quicksale evidence, listing report and Nick context');
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
