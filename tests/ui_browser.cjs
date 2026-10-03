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
    const errors=[], calls=[], quotePayloads=[], nickPayloads=[], eventPayloads=[], discoveryPayloads=[], explanationPayloads=[], targetPayloads=[];
    const watched=new Set(['급매:synthetic-1']);
    const watchTargets=new Map();
    const reportsByKey=new Map(), savedReports=new Map();
    let entranceChosen=false, tradeEnriched=false, regionReport=null, comparisonReport=null, occupancyChecked=false, commuteChecked=false;
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
      if(['/api/v2/regions/테스트구/report','/api/v2/regions/kb:1114000000/report'].includes(decoded)) data={type:'region',report_id:'b'.repeat(64),
        subject:{region:'테스트구',region_id:'kb:1114000000'},asof:'2026-09-28',
        assessment:{display_grade:'매수',assessment_status:'ready',scope_note:'테스트 권역 자료',
          summary:'지역 신호만 보여 줍니다. 개별 매물의 가격 판단은 별도입니다.',raw_grade:'BUY',
          reasons:[{reason_id:'jeonse_pressure',label:'전세수급 압력',value:180,threshold:170,unit:'지수',role:'driver',passing:true}],
          change:{type:'first_observation',changed_reasons:[]}},
        positive:[{reason_id:'jeonse_pressure',label:'전세수급 압력',value:180,threshold:170,unit:'지수',role:'driver',passing:true}],
        cautions:[{reason_id:'buyer_interest',label:'매수심리 관찰선 미충족',value:68,threshold:70,unit:'지수',role:'driver',passing:false}],
        unknowns:[]};
      if(['/api/v2/regions/테스트구/report','/api/v2/regions/kb:1114000000/report'].includes(decoded)) regionReport=data;
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
      if(url.pathname==='/api/freshness') {
        const now=Date.parse('2026-10-03T12:00:00Z')/1000;
        data={now,'기준일':'2026-09-21',kb_fetch:{observation_check:{asof:'2026-09-21',previous_asof:'2026-09-21',changed:false,checked_at:now-3600}},sources:[
          {key:'signal',label:'시장 시그널 (KB)',asof:'2026-09-21',ts:now-3600,cycle:'주 1회',note:'KB 자료'},
          {key:'trade',label:'국토부 실거래',ts:now-3600,cycle:'조회 시',note:'거래 자료'}]};
      }
      if(url.pathname==='/api/v2/discovery/regions') data={status:'ready',regions:[
        {code:'11140',name:'테스트구',label:'서울 · 테스트구'},
        {code:'11150',name:'조건없음',label:'서울 · 조건없음'},
        {code:'11160',name:'프로필없음',label:'서울 · 프로필없음'},
        {code:'11170',name:'중구',label:'서울 · 중구'},
        {code:'28125',name:'중구',label:'인천 · 중구'}]};
      if(url.pathname==='/api/v2/discovery') {
        const spec=route.request().postDataJSON()||{};
        discoveryPayloads.push(spec);
        const next=!!spec.cursor, finance=!!spec.max_monthly_manwon;
        const noMatch=spec.region_code==='11150';
        const noProfile=finance&&spec.prefer_region_code==='11160';
        const preferred=!!spec.prefer_max_price_manwon||!!spec.prefer_min_area_m2;
        const candidate={listing:{key:next?'일반매물:synthetic-2':'일반매물:synthetic-1',
          name:next?'두번째 후보':'첫번째 후보',region:'테스트구',kind:'일반매물',asking_manwon:50000,rooms:3},
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
          single_condition_relaxations:noMatch?{region_code:1}:{},
          next_cursor:finance||next?null:'synthetic-next',
          finance_context:finance?{status:noProfile?'no_confirmed_profile':'ready',policy_status:'unverified'}:null,
          groups:{matched:finance||noMatch?[]:[candidate],verify:finance?[candidate]:[],explore:[],
            exceeded:spec.include_exceeded?[exceededCandidate]:[]}};
        if(spec.move_in_by){
          const occupancyPass=occupancyChecked && spec.move_in_by>='2026-12-08';
          const occupancyFail=occupancyChecked && !occupancyPass;
          candidate.constraints=[{field:'move_in_by',status:occupancyPass?'pass':occupancyFail?'fail':'unknown'}];
          candidate.listing.move_in=occupancyChecked?{status:'dated',date:'2026-12-08'}:null;
          candidate.eligibility=occupancyPass?'matched':occupancyFail?'exceeded':'verify';
          data.counts={matched:occupancyPass?1:0,verify:occupancyChecked?0:1,explore:0,exceeded:occupancyFail?1:0};
          data.groups={matched:occupancyPass?[candidate]:[],verify:occupancyChecked?[]:[candidate],explore:[],exceeded:spec.include_exceeded&&occupancyFail?[candidate]:[]};
          data.next_cursor=null;
        }
        if(spec.max_commute_minutes){
          const commutePass=commuteChecked&&spec.max_commute_minutes>=54;
          const commuteFail=commuteChecked&&!commutePass;
          candidate.listing.coordinate=[37.65,127.06];
          candidate.listing.commute=commuteChecked?{status:'observed',minutes:54}:null;
          candidate.verify_next='저장된 직장까지의 대중교통 경로를 확인하세요.';
          candidate.constraints=[{field:'max_commute_minutes',status:commutePass?'pass':commuteFail?'fail':'unknown'}];
          candidate.eligibility=commutePass?'matched':commuteFail?'exceeded':'verify';
          data.commute_context={status:spec.prefer_region_code==='11160'?'missing_work':'ready'};
          data.counts={matched:commutePass?1:0,verify:commuteChecked?0:1,explore:0,exceeded:commuteFail?1:0};
          data.groups={matched:commutePass?[candidate]:[],verify:commuteChecked?[]:[candidate],explore:[],
            exceeded:spec.include_exceeded&&commuteFail?[candidate]:[]};
          data.next_cursor=null;
        }
      }
      if(url.pathname==='/api/v2/discovery/occupancy') {
        occupancyChecked=true;
        data={status:'dated',date:'2026-12-08',source:'hanbang_detail'};
      }
      if(url.pathname==='/api/v2/discovery/commute') {
        commuteChecked=true;
        data={status:'observed',minutes:54,source:'Kakao 대중교통 경로'};
      }
      if(url.pathname==='/api/v2/listings/report' && url.searchParams.get('key')==='일반매물:forbidden')
        return route.fulfill({status:403,json:{detail:'forbidden'}});
      if(url.pathname==='/api/v2/listings/report') data={type:'listing',report_id:'synthetic-report',
        subject:{key:url.searchParams.get('key'),name:'한방테스트단지',region:'테스트구',
          kind:'일반매물',asking_manwon:50000,collected_at:'2026-09-29'},
        price:{'상태':'관측비교','표본수':3},buyer_fit:{status:'unknown'},
        positive:[{text:'동일 조건 실거래가 있습니다.'}],
        cautions:[{text:'현재 판매 여부는 확인되지 않았습니다.'}],
        next_actions:['실제 호가 확인'],evidence:[{id:'trades',label:'국토부 실거래',asof:'2026-09-29',status:'관측'}]};
      if(url.pathname==='/api/v2/listings/report' && url.searchParams.get('key')==='일반매물:profile-fail')
        data.partial_failures=['buyer_profile_unavailable'];
      if(url.pathname==='/api/v2/listings/report' && url.searchParams.get('key')==='일반매물:trade-missing' && !tradeEnriched)
        data.partial_failures=['trade_cache_unavailable'];
      if(url.pathname==='/api/v2/listings/report') reportsByKey.set(url.searchParams.get('key'),data);
      if(/^\/api\/v2\/reports\/[^/]+\/explanations$/.test(url.pathname)) {
        const payload=route.request().postDataJSON(); explanationPayloads.push(payload);
        const region=payload.type==='region';
        return route.fulfill({status:200,json:{job_id:'test-job',status:'succeeded',result:{
          report_id:decodeURIComponent(url.pathname.split('/')[4]),mode:payload.mode,source:'model_validated',
          summary:'현재 리포트의 가격 근거를 확인하세요.',
          claims:[region?{text:'전세수급 자료와 반대 근거를 함께 확인해야 합니다.',evidence_ids:['jeonse_pressure']}:
            {text:'거래 표본의 한계를 먼저 확인해야 합니다.',evidence_ids:['trades']}],
          cautions:['현재 판매 여부는 별도 확인이 필요합니다.'],limit:'현장 확인 전에는 결론을 보류하세요.'}}});
      }
      if(url.pathname==='/api/v2/comparisons') {
        const keys=route.request().postDataJSON().keys;
        data={type:'comparison',report_id:'c'.repeat(64),basis:'같은 조건 가격 비교',items:keys.map((key,i)=>({
          listing:{key,kind:'일반매물',name:i?'두번째테스트단지':'한방테스트단지',region:'테스트구',
            asking_manwon:i?47000:50000,exclusive_m2:84.5,collected_at:'2026-09-29'},
          price:{상태:'관측비교',표본수:3,호가차이율:i?-4:2}})),cautions:[],unknowns:['수리 상태']};
        comparisonReport=data;
      }
      if(url.pathname==='/api/v2/listings/report-enrich') {
        tradeEnriched=true;
        data={ok:true};
      }
      if(url.pathname==='/api/v2/report-snapshots' && route.request().method()==='POST') {
        const request=route.request().postDataJSON(), report=request.type==='region'?regionReport:
          request.type==='comparison'?comparisonReport:reportsByKey.get(request.key);
        if(!report || request.report_id!==report.report_id) return route.fulfill({status:409,json:{}});
        savedReports.set(request.report_id,{report:structuredClone(report),saved_at:1780000000});
        return route.fulfill({status:201,json:{report_id:request.report_id,saved_at:1780000000}});
      }
      if(url.pathname==='/api/v2/report-snapshots') data={items:[...savedReports.values()]
        .filter(saved=>!url.searchParams.get('key')||(saved.report.subject?.key||saved.report.subject?.region_id)===url.searchParams.get('key'))
        .map(saved=>({report_id:saved.report.report_id,subject_key:saved.report.subject?.key||saved.report.subject?.region_id,
          kind:saved.report.type==='comparison'?'비교:개인':saved.report.subject?.kind||'지역',
          name:saved.report.type==='comparison'?'2개 매물 비교':saved.report.subject?.name||saved.report.subject?.region,asof:saved.report.asof,
          saved_at:saved.saved_at}))};
      if(url.pathname.startsWith('/api/v2/report-snapshots/')) {
        const id=decodeURIComponent(url.pathname.split('/').at(-1));
        if(route.request().method()==='DELETE') {
          const deleted=savedReports.delete(id);
          return route.fulfill(deleted?{json:{deleted:true}}:{status:404,json:{}});
        }
        const saved=savedReports.get(id);
        return route.fulfill(saved?{json:saved}:{status:404,json:{}});
      }
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
      if(url.pathname==='/api/listing-location' && url.searchParams.get('key')==='일반매물:location-fail')
        return route.fulfill({status:503,json:{}});
      if(url.pathname==='/api/listing-location' && url.searchParams.get('key')==='일반매물:location-slow')
        await new Promise(r=>setTimeout(r,180));
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
          target_price:watchTargets.get(key)??null,
          price_change:-10000,current:{key,kind:key.split(':')[0],name:'테스트단지',region:'테스트구',price:50000},
          alternatives:key==='급매:synthetic-1'?[{key:'급매:synthetic-2',kind:'급매',name:'테스트단지',region:'테스트구',price:52000,reason:'같은 단지의 다른 매물'}]:[]}))};
      }
      if(url.pathname==='/api/listing-watch/price-target') {
        if(route.request().method()==='PUT') {
          const payload=route.request().postDataJSON();
          targetPayloads.push(payload);
          watchTargets.set(payload.key,payload.target_manwon);
          return route.fulfill({json:{ok:true,target_manwon:payload.target_manwon}});
        }
        watchTargets.delete(url.searchParams.get('key'));
        return route.fulfill({json:{ok:true}});
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
    assert.match(await page.locator('#dashShortlistWrap').textContent(),/예산 범위 매물 후보/);
    assert.match(await page.locator('#dashShortlistWrap').textContent(),/대출 승인·규제·권리·판매 여부는 별도 확인/);
    assert.doesNotMatch(await page.locator('#dashShortlistWrap').textContent(),/지금 살 수 있는 집/);
    assert.equal(await page.locator('#dashShortlistWrap').evaluate(el=>!!el.closest('details')),false);
    for(const url of ['/api/regime','/api/complex-watch','/api/news','/api/shortlist']) assert(!calls.includes(url),`eager hidden source ${url}`);
    const width=await page.evaluate(()=>({viewport:innerWidth,body:document.body.scrollWidth,root:document.documentElement.scrollWidth}));
    assert(width.body<=width.viewport && width.root<=width.viewport,JSON.stringify(width));
    await page.locator('#date').click();
    await page.locator('#freshBox').getByText(/관측 12일 전/).waitFor();
    assert.match(await page.locator('#freshBox').textContent(),/관측 2026-09-21 · 최종 수집 1시간 전/);
    assert.match(await page.locator('#freshBox').textContent(),/최신 공표를 확인하기 전까지/);
    assert.match(await page.locator('#freshBox').textContent(),/최근 수집 확인에서도 새 관측일이 없었습니다/);
    assert.match(await page.locator('#sourceAsOf').textContent(),/KB 기준 2026-09-21/);
    await page.keyboard.press('Escape');
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
    await page.getByRole('button',{name:'시그널',exact:true}).click();
    assert.equal(new URL(page.url()).hash,'#signal');
    assert.equal(await page.getByRole('button',{name:'시그널',exact:true}).getAttribute('aria-current'),'page');
    assert.equal(await page.locator('#groupSubnav').isVisible(),false);
    await page.evaluate(()=>{mapSplit=()=>{};});
    await page.getByRole('button',{name:'매물 찾기',exact:true}).click();
    assert.equal(new URL(page.url()).hash,'#all');
    assert.equal(await page.getByRole('button',{name:'매물 찾기',exact:true}).getAttribute('aria-current'),'page');
    assert.equal(await page.getByRole('button',{name:'시그널',exact:true}).getAttribute('aria-current'),null);
    assert.equal(await page.locator('#groupSubnav').isVisible(),false);
    const navWidth=await page.evaluate(()=>document.documentElement.scrollWidth);
    assert(navWidth<=360,`browse navigation overflows mobile viewport: ${navWidth}`);
    await page.evaluate(()=>{_focusRegion='<img src=x onerror=alert(1)>'; renderListingFocus();});
    assert.equal(await page.locator('#listingFocus img').count(),0);
    assert.equal(await page.locator('#listingFocus').getByRole('button',{name:/전체 보기/}).count(),1);
    await page.locator('#listingFocus').getByRole('button',{name:/전체 보기/}).click();
    await page.getByText('일반·급매 매물은 지정된 개인 계정에서만 보입니다.',{exact:false}).waitFor();
    assert.match(await page.locator('#laStatus').textContent(),/지정된 개인 계정만/);
    const budgetUi=await page.evaluate(()=>{
      mapSplit=(_list,_map,items,opt)=>{window.__budgetItems=items;window.__budgetOpt=opt;};
      const row={key:'일반매물:budget',유형:'일반매물',단지명:'예산테스트단지',지역:'테스트구',
        시도:'서울',총액:50000,기회도:50,지표라벨:'등록일',지표값:'2026-10-03',
        lines:{cash:'계산상 됩니다',price:'호가',unknown:'확인 필요',next:'확인'}};
      _laApplyResponse('일반매물',{listings:[{...row,budget_fit:{status:'unknown',reason:'가정 변경'}}],
        asof:'2026-09-28',meta:{confirmed_budget:false,private_access:true}});
      const held={note:document.getElementById('laBudgetNote').textContent,
        sort:document.querySelector('#laSort option[value="budget"]').textContent,
        label:window.__budgetOpt.summary(window.__budgetItems[0]).sub,
        cash:_buyerFour(window.__budgetItems[0],_eok).cash};
      _laApplyResponse('일반매물',{listings:[
        {...row,key:'above',총액:60000,budget_fit:{status:'above'}},
        {...row,key:'within',총액:40000,budget_fit:{status:'within'}},
        {...row,key:'unknown',총액:30000,budget_fit:{status:'unknown',reason:'지역 확인 필요'}}],
        asof:'2026-09-28',meta:{confirmed_budget:true,private_access:true}});
      return {held,order:window.__budgetItems.map(x=>x.key),
        within:window.__budgetOpt.summary(window.__budgetItems[0]).sub,
        above:window.__budgetOpt.summary(window.__budgetItems[2]).sub,
        cash:_buyerFour(window.__budgetItems[2],_eok).cash};
    });
    assert.match(budgetUi.held.note,/매수력 확정 가정·지역이 없거나 바뀌었습니다/);
    assert.match(budgetUi.held.sort,/예산 미확정/);
    assert.match(budgetUi.held.label,/예산 비교 보류/);
    assert.doesNotMatch(budgetUi.held.cash,/계산상 됩니다/);
    assert.deepEqual(budgetUi.order,['within','unknown','above']);
    assert.match(budgetUi.within,/호가·확정상한 이내/);
    assert.match(budgetUi.above,/호가·확정상한 초과/);
    assert.match(budgetUi.cash,/상한보다 높습니다/);
    const priceUi=await page.evaluate(()=>{
      const base={유형:'일반매물',단지명:'가격테스트',지역:'테스트구',시도:'서울',기회도:10};
      _laApplyResponse('일반매물',{listings:[
        {...base,key:'high',총액:160000}, {...base,key:'unknown',총액:null},
        {...base,key:'low',총액:40000}],asof:'2026-09-28',
        meta:{confirmed_budget:false,private_access:true}});
      const all=window.__budgetItems.map(x=>x.key);
      document.getElementById('laPriceMax').value='150000'; onLaPrice();
      const filtered=window.__budgetItems.map(x=>x.key);
      const note=document.getElementById('laPriceNote').textContent;
      resetLaPrice();
      return {all,filtered,restored:window.__budgetItems.map(x=>x.key),note};
    });
    assert.deepEqual(priceUi.all,['low','high','unknown']);
    assert.deepEqual(priceUi.filtered,['low']);
    assert.deepEqual(priceUi.restored,priceUi.all);
    assert.match(priceUi.note,/가격 미상 1건 제외/);
    const ambiguousFavorite=await page.evaluate(async()=>{
      const originalFetch=window.fetch, oldFavorites=_favs, oldIdentity=_favComplexIdentity;
      _favs=new Set(['complex:중구|옛 관심단지']);
      _favComplexIdentity=new Map([['중구|옛 관심단지',{status:'needs_reselection',
        message:'이름만 저장된 중구는 서울·개편 전 인천을 구별할 수 없습니다.'}]]);
      window.fetch=(input,...args)=>String(input)==='/api/myfeed'
        ? Promise.resolve({json:async()=>({ok:true,items:[{type:'complex',region:'중구',name:'옛 관심단지',
            지역확인필요:true,데이터없음:true}]})}) : originalFetch(input,...args);
      try {
        await renderFavList(); await _loadDashFeed();
        return {favorite:document.getElementById('favListBody').innerHTML,
          feed:document.getElementById('dashFeedWrap').innerHTML};
      }
      finally { window.fetch=originalFetch; _favs=oldFavorites; _favComplexIdentity=oldIdentity; }
    });
    assert.match(ambiguousFavorite.favorite,/서울·개편 전 인천을 구별할 수 없습니다/);
    assert.match(ambiguousFavorite.favorite,/지역 재선택/);
    assert.doesNotMatch(ambiguousFavorite.favorite,/＋비교|>상세</);
    assert.match(ambiguousFavorite.feed,/지역 확인 전 · 변화 판정 보류/);
    assert.doesNotMatch(ambiguousFavorite.feed,/openComplex/);
    const reselected=await page.evaluate(async()=>{
      const originalFetch=window.fetch, originalToggle=toggleFav;
      let saved=null;
      window.fetch=(input,...args)=>String(input)==='/api/v2/discovery/regions'
        ? Promise.resolve({ok:true,json:async()=>({regions:[{code:'11140',name:'중구',label:'서울 · 중구'}]})})
        : originalFetch(input,...args);
      toggleFav=async(kind,key,label)=>{ saved={kind,key,label}; return true; };
      try{
        await toggleComplexFav('중구','옛 관심단지');
        const dlg=document.getElementById('complexRegionPickDlg');
        const options=document.getElementById('complexRegionPickOptions').textContent;
        dlg.querySelector('#complexRegionPickOptions button').click();
        await new Promise(resolve=>setTimeout(resolve,0));
        return {options,saved};
      }finally{
        document.getElementById('complexRegionPickDlg').close();
        window.fetch=originalFetch; toggleFav=originalToggle;
      }
    });
    assert.match(reselected.options,/서울 · 중구 · 이 단지가 맞습니다/);
    assert.deepEqual(reselected.saved,{kind:'complex',key:'kb:1114000000|옛 관심단지',label:'옛 관심단지'});
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
    await page.locator('#view-watch input[type="number"]').fill('47000');
    await Promise.all([page.waitForResponse(r=>r.url().includes('/api/listing-watch/price-target') && r.request().method()==='PUT'),
      page.locator('#view-watch').getByRole('button',{name:'목표 저장·해제'}).click()]);
    assert.deepEqual(targetPayloads,[{key:'급매:synthetic-1',target_manwon:47000}]);
    await page.locator('#view-watch input[type="number"]').fill('');
    await Promise.all([page.waitForResponse(r=>r.url().includes('/api/listing-watch/price-target') && r.request().method()==='DELETE'),
      page.locator('#view-watch').getByRole('button',{name:'목표 저장·해제'}).click()]);
    assert.equal(watchTargets.size,0);
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
    await page.evaluate(()=>{
      const common={유형:'일반매물',지역코드:'11350',단지명:'한 단지',source:'hanbang',
        ref:{hanbang_complex_id:'c1'},평형:25};
      _ms.test.items=[{...common,id:'A'},{...common,id:'B'},
        {...common,id:'C',지역코드:'28110'}];
      _ms.test.opt={summary:x=>({nm:x.id}),detail:x=>`<button>확인 ${x.id}</button>`,
        groupKey:window.__budgetOpt.groupKey,groupLabel:window.__budgetOpt.groupLabel};
      _ms.test.listLimit=60; _ms.test.openGroupKeys=new Set();
      _ms.test.viewIndices=[0,1,2];
      const marker={getElement:()=>null,getLatLng:()=>[37,127],openPopup(){},closePopup(){}};
      _ms.test.markers={0:marker,1:marker,2:marker};
      renderMsList('test',[0,1,2]);
    });
    assert.equal(await page.locator('#testList .ms-group').count(),1);
    const groupHead=page.locator('#testList .ms-group-head');
    assert.match(await groupHead.textContent(),/2개 매물/);
    assert.equal(await groupHead.getAttribute('aria-expanded'),'false');
    await groupHead.click();
    const groupedRow=page.locator('#testList .ms-group .ms-row[data-i="1"]');
    await groupedRow.click();
    assert.equal(await groupedRow.getAttribute('aria-expanded'),'true');
    await groupedRow.click();
    assert.equal(await groupedRow.getAttribute('aria-expanded'),'false');
    await groupHead.click();
    assert.equal(await groupHead.getAttribute('aria-expanded'),'false');
    await page.evaluate(()=>selectMsRow('test',1));
    assert.equal(await groupHead.getAttribute('aria-expanded'),'true');
    assert.equal(await groupedRow.getAttribute('aria-expanded'),'true');
    await groupHead.click();
    assert.equal(await page.evaluate(()=>_ms.test.sel),null);
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
    assert.equal(explanationPayloads.length,0);
    await page.locator('#v2ReportBody').getByRole('button',{name:'가격이 싼가?'}).click();
    assert.match(await page.locator('#v2QuickAnswer').textContent(),/동일 조건 실거래 3건과 비교/);
    await page.locator('#v2ReportBody').getByRole('button',{name:'내 예산에 맞나?'}).click();
    assert.match(await page.locator('#v2QuickAnswer').textContent(),/대출 승인·세금·수리비 확인 전/);
    await page.locator('#v2ReportBody').getByRole('button',{name:'뭘 조심해야 하나?'}).click();
    assert.match(await page.locator('#v2QuickAnswer').textContent(),/현재 판매 여부는 확인되지/);
    assert.equal(nickPayloads.length,0);
    assert.equal(explanationPayloads.length,0);
    await page.locator('#v2ReportBody').getByRole('button',{name:'쉽게 설명',exact:true}).click();
    await page.locator('#v2ListingExplanation').getByText(/거래 표본의 한계/).waitFor();
    assert.match(await page.locator('#v2ListingExplanation').textContent(),/근거: 국토부 실거래/);
    assert.deepEqual(explanationPayloads.at(-1),{type:'listing',key:'일반매물:synthetic-hb-1',mode:'easy'});
    assert.equal(nickPayloads.length,0);
    const locationCalls=calls.filter(path=>path==='/api/listing-location').length;
    await page.locator('#v2ReportBody').getByRole('button',{name:'입지 근거 확인'}).click();
    await page.locator('#v2LocationResult').getByText(/테스트역 · 약 8분/).waitFor();
    assert.match(await page.locator('#v2LocationResult').textContent(),/통학구역 후보 · 테스트초/);
    assert.match(await page.locator('#v2LocationResult').textContent(),/직선 550m/);
    assert.equal(calls.filter(path=>path==='/api/listing-location').length,locationCalls+1);
    assert.equal(nickPayloads.length,0);
    await page.locator('#v2ReportBody').getByRole('button',{name:'이 리포트 저장'}).click();
    await page.locator('#v2SaveReportStatus').getByText(/저장했습니다/).waitFor();
    await page.locator('#v2ReportBody').getByRole('button',{name:'네, 충분했어요'}).click();
    assert.deepEqual(eventPayloads.filter(x=>x.name==='report_task_feedback').at(-1),
      {name:'report_task_feedback',props:{type:'listing',answer:'yes'}});
    assert.equal(await page.locator('#v2ReportBody').getByRole('button',{name:'아니요, 더 필요해요'}).isDisabled(),true);
    const reportFetches=calls.filter(path=>path==='/api/v2/listings/report').length;
    await page.locator('#v2ReportBody').getByRole('button',{name:'저장본 보기'}).click();
    await page.locator('#v2ReportBody').getByRole('button',{name:'당시 리포트 보기'}).click();
    await page.locator('#v2ReportBody').getByText(/저장 당시 · 한방테스트단지/).waitFor();
    assert.match(await page.locator('#v2ReportBody').textContent(),/현재 호가·판매 여부/);
    assert.equal(calls.filter(path=>path==='/api/v2/listings/report').length,reportFetches);
    assert.equal(nickPayloads.length,0);
    await page.locator('#v2ReportBody').getByRole('button',{name:'저장본 목록으로'}).click();
    page.once('dialog', dialog=>dialog.dismiss());
    await page.locator('#v2ReportBody').getByRole('button',{name:'저장본 삭제'}).click();
    assert.equal(await page.locator('#v2ReportBody').getByRole('button',{name:'당시 리포트 보기'}).count(),1);
    page.once('dialog', dialog=>dialog.accept());
    await page.locator('#v2ReportBody').getByRole('button',{name:'저장본 삭제'}).click();
    await page.locator('#v2ReportBody').getByText('저장한 리포트가 없습니다.').waitFor();
    await page.locator('#v2ReportDlg').getByRole('button',{name:'리포트 닫기'}).click();
    await page.evaluate(()=>SignalV2.openListing('일반매물:synthetic-hb-1'));
    await page.locator('#v2ReportBody').getByRole('button',{name:'내 계정에서 열기 링크 복사'}).waitFor();
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
    await page.evaluate(()=>SignalV2.openListing('일반매물:location-fail'));
    await page.locator('#v2ReportBody').getByRole('button',{name:'입지 근거 확인'}).click();
    await page.locator('#v2LocationResult').getByText(/자료를 불러오지 못했습니다/).waitFor();
    assert.match(await page.locator('#v2ReportBody').textContent(),/가격 근거/);
    await page.evaluate(()=>SignalV2.openListing('일반매물:profile-fail'));
    await page.locator('#v2ReportBody').getByText(/내 자금 프로필을 불러오지 못해/).waitFor();
    assert.match(await page.locator('#v2ReportBody').textContent(),/가격 근거/);
    await page.evaluate(()=>SignalV2.openListing('일반매물:trade-missing'));
    await page.locator('#v2ReportBody').getByRole('button',{name:'실거래 근거 새로 확인'}).waitFor();
    assert.equal(calls.filter(path=>path==='/api/v2/listings/report-enrich').length,0);
    await page.locator('#v2ReportBody').getByRole('button',{name:'실거래 근거 새로 확인'}).click();
    await page.locator('#v2ReportBody').getByRole('button',{name:'실거래 근거 새로 확인'}).waitFor({state:'hidden'});
    assert.equal(calls.filter(path=>path==='/api/v2/listings/report-enrich').length,1);
    await page.evaluate(()=>SignalV2.openListing('일반매물:location-slow'));
    await page.locator('#v2ReportBody').getByRole('button',{name:'입지 근거 확인'}).click();
    await page.evaluate(()=>SignalV2.openListing('일반매물:synthetic-hb-2'));
    await page.locator('#v2ReportBody').getByRole('button',{name:'입지 근거 확인'}).waitFor();
    await page.waitForTimeout(220);
    assert.equal(await page.locator('#v2LocationResult').textContent(),'');
    await page.locator('#v2ReportDlg').getByRole('button',{name:'리포트 닫기'}).click();
    const nickCallsBeforeLegacyReport=nickPayloads.length;
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
    assert.equal(nickPayloads.length,nickCallsBeforeLegacyReport);
    await page.getByRole('button',{name:'닉과 대화'}).click();
    assert.equal(nickPayloads.length,nickCallsBeforeLegacyReport);
    await page.locator('#advInput').fill('이 매물의 가격 근거를 설명해줘');
    await page.locator('#advSendBtn').click();
    await page.getByText('선택 매물의 가격을 확인하세요.',{exact:false}).waitFor();
    assert.equal(nickPayloads.at(-1).listing_key,'일반매물:synthetic-hb-1');
    await page.getByRole('button',{name:'매물 리포트'}).click();
    await page.getByRole('button',{name:'＋ 비교함 담기'}).click();
    await page.evaluate(()=>openListingReport('일반매물:synthetic-hb-2'));
    await page.getByText('두번째테스트단지',{exact:true}).last().waitFor();
    assert.equal(nickPayloads.length,nickCallsBeforeLegacyReport+1);
    await page.getByRole('button',{name:'＋ 비교함 담기'}).click();
    await page.getByRole('button',{name:'선택 매물 비교 2/3'}).click();
    await page.getByText('같은 면적 국토부 실거래',{exact:false}).waitFor();
    assert.equal(await page.locator('#listingCompareBody thead th').count(),3);
    await page.locator('#listingCompareDlg').getByRole('button',{name:'교통·학교 차이 확인'}).click();
    await page.locator('#listingCompareLocation').getByText('통학구역 후보 테스트초',{exact:false}).first().waitFor();
    await page.locator('#listingCompareDlg').getByRole('button',{name:'이 비교 리포트 저장'}).click();
    await page.locator('#listingCompareSaveStatus').getByText(/저장했습니다/).waitFor();
    assert.equal(calls.filter(x=>x==='/api/v2/comparisons').length,1);
    await page.locator('#listingCompareDlg').getByRole('button',{name:'저장본 보기'}).click();
    await page.locator('#v2ReportBody').getByRole('button',{name:'당시 리포트 보기'}).click();
    await page.locator('#v2ReportBody').getByText(/저장 당시 · 2개 매물 비교/).waitFor();
    assert.match(await page.locator('#v2ReportBody').textContent(),/현재 호가·판매 여부/);
    await page.locator('#v2ReportDlg').getByRole('button',{name:'리포트 닫기'}).click();
    await page.evaluate(()=>listingCompareOpen());
    await page.locator('#listingCompareDlg').getByRole('button',{name:'닉에게 차이 묻기'}).waitFor();
    const compareRequest=page.waitForRequest(req=>req.url().includes('/api/advisor/stream')&&req.postDataJSON()?.comparison_keys?.length===2);
    await page.locator('#listingCompareDlg').getByRole('button',{name:'닉에게 차이 묻기'}).click();
    assert.deepEqual((await compareRequest).postDataJSON().comparison_keys,['일반매물:synthetic-hb-1','일반매물:synthetic-hb-2']);
    assert(eventPayloads.some(x=>x.name==='listing_analysis_ready'));
    assert(eventPayloads.some(x=>x.name==='listing_evidence_open'&&x.props.section==='complex'));
    assert(eventPayloads.some(x=>x.name==='listing_commute_compare'));
    assert(eventPayloads.some(x=>x.name==='listing_compare_open'));
    assert(!JSON.stringify(eventPayloads).includes('synthetic-hb-1'));
    await page.evaluate(()=>{
      document.querySelectorAll('dialog[open]').forEach(dialog=>dialog.close());
      renderAlerts({region_events:[{subject_type:'region',subject_key:'kb:1114000000',
        kind:'region_evidence',created_at:1780000000,seen:false,
        payload:{region:'테스트구<img src=x onerror=alert(1)>',region_id:'kb:1114000000',asof:'2026-09-28',
          old_grade:'매수',new_grade:'관망',changed_reasons:[{label:'전세수급 압력'}]}},
        {subject_type:'region',subject_key:'kb:1114000000',kind:'region_evidence',
          created_at:1780000001,seen:true,
          payload:{region:'테스트구',region_id:'kb:1114000000',asof:'2026-09-28',old_grade:'매수',
            new_grade:'관망',changed_reasons:[{label:'전세수급 압력'}]}}],
        watch_events:[{subject_type:'listing',subject_key:'청약:123',
          kind:'presale_deadline',created_at:1780000000,seen:false,
          payload:{name:'테스트 청약',region:'테스트구',date:'2026-10-04',status:'접수중'}},
        {subject_type:'listing',subject_key:'일반매물:synthetic-hb-1',
        kind:'listing_price',created_at:1780000000,seen:false,
        payload:{name:'<img src=x onerror=alert(1)>',region:'테스트구',old_price:50000,new_price:47000}}],
        timing:[],nbhd:[],changes:[],digest:[],unread:1,prefs:{listing_price:true,new_alternative:true}});
      document.getElementById('alertsDlg').showModal();
    });
    assert.match(await page.locator('#alertsBody').textContent(),/확인된 호가 5.0억 → 4.7억/);
    assert.match(await page.locator('#alertsBody').textContent(),/다음 일정이 오늘\(2026-10-04\)/);
    assert.equal(await page.locator('#alertsBody').getByRole('button',{name:'청약 일정 목록 보기'}).count(),1);
    assert.match(await page.locator('#alertsBody').textContent(),/과거 발행 기록은 현재 매수 신호가 아닙니다/);
    assert.equal(await page.locator('#alertsBody img').count(),0);
    await page.evaluate(()=>{
      window._signalAppReady=false;
      allSignals=[{region:'테스트구',region_id:'kb:1114000000',group:'서울',
        signal:'BUY',display_signal:'BUY',assessment_status:'ready',급지:'B',
        전세수급:180,매수우위지수:68}];
    });
    await page.locator('#alertsBody [data-alert-region="테스트구"]').click();
    await page.locator('#haesolPanel').getByText(/지역 신호만 보여 줍니다/).waitFor();
    await page.evaluate(()=>document.getElementById('alertsDlg').showModal());
    await page.locator('#alertsBody [data-alert-listing]').click();
    await page.locator('#v2ReportBody').getByText('한방테스트단지',{exact:false}).waitFor();
    await page.locator('#v2ReportDlg').getByRole('button',{name:'리포트 닫기'}).click();
    const held=await page.evaluate(()=>{
      const row={region:'테스트구',region_id:'kb:1234500000',signal:'BUY',display_signal:'HELD',assessment_status:'held',group:'서울'};
      const label=displaySignal(row);
      return {label,badge:badge(label),style:_choStyle('signal','테스트구',{},null,{테스트구:row},'12345'),
        tip:_choTip('signal','테스트구',{},null,{테스트구:row},'12345'),
        otherStyle:_choStyle('signal','테스트구',{},null,{테스트구:row},'28110'),
        otherTip:_choTip('signal','테스트구',{},null,{테스트구:row},'28110')};
    });
    assert.equal(held.label,'HELD');
    assert.match(held.badge,/판단 보류/);
    assert.equal(held.style.fillColor,'#5f6875');
    assert.match(held.tip,/판단 보류/);
    assert.equal(held.otherStyle.fillColor,'#e2e8f0');
    assert.match(held.otherTip,/자료 없음/);
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
    await page.evaluate(()=>renderList());
    assert.match(await page.locator('#list').textContent(),/모두 판단 보류 · 수급 참고 순/);
    await page.evaluate(()=>{
      document.getElementById('advPanel').style.display='none';
      document.querySelectorAll('dialog[open]').forEach(dialog=>dialog.close());
      meta={last_date:'2026-09-28',zones:{jeonse_supply:[],buyer_idx_strong:70,buyer_demand_buy:20}};
      allSignals=[{region:'테스트구',region_id:'kb:1114000000',group:'서울',signal:'BUY',display_signal:'BUY',assessment_status:'ready',급지:'B',전세수급:180,매수우위지수:68}];
      switchTab('signal'); renderList(); selectRegion('테스트구');
    });
    await page.getByText('지역 신호만 보여 줍니다.',{exact:false}).waitFor();
    const explanationCallsBeforeRegion=explanationPayloads.length;
    await page.locator('#v2RegionCounter').click();
    await page.locator('#v2RegionExplanation').getByText(/전세수급 자료와 반대 근거/).waitFor();
    assert.equal(explanationPayloads.length,explanationCallsBeforeRegion+1);
    assert.deepEqual(explanationPayloads.at(-1),{type:'region',key:'kb:1114000000',mode:'counterevidence'});
    await page.locator('#haesolPanel details').last().locator('summary').click();
    await page.locator('#v2RegionSave').click();
    await page.locator('#v2RegionSaveStatus').getByText(/저장했습니다/).waitFor();
    await page.locator('#v2RegionSaved').click();
    await page.locator('#v2ReportBody').getByRole('button',{name:'당시 리포트 보기'}).click();
    await page.locator('#v2ReportBody').getByText(/저장 당시 · 테스트구/).waitFor();
    assert.match(await page.locator('#v2ReportBody').textContent(),/현재 시그널·자료 신선도/);
    await page.locator('#v2ReportDlg').getByRole('button',{name:'리포트 닫기'}).click();
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
    await page.waitForFunction(()=>document.querySelector('#v2DiscoverForm [name=region_code]')?.value==='11140');
    await page.locator('#v2DiscoverForm').getByRole('button',{name:'후보 찾기'}).click();
    assert.equal(discoveryPayloads.at(-1).prefer_region_code,'11140');
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
    await page.locator('#v2DiscoverForm [name=min_rooms]').fill('3');
    await page.locator('#v2DiscoverForm').getByRole('button',{name:'후보 찾기'}).click();
    assert.equal(discoveryPayloads.at(-1).min_rooms,3);
    assert.match(await page.locator('#v2DiscoverResults [data-v2-card]').first().textContent(),/방 3개/);
    await page.locator('#v2DiscoverForm [name=min_rooms]').fill('');
    await page.locator('#v2DiscoverForm [name=move_in_by]').fill('2026-12-31');
    await page.locator('#v2DiscoverForm').getByRole('button',{name:'후보 찾기'}).click();
    assert.equal(discoveryPayloads.at(-1).move_in_by,'2026-12-31');
    await page.locator('#v2DiscoverResults [data-v2-group="verify"] [data-v2-occupancy]').click();
    await page.getByText(/원천 표시: 2026-12-08 입주 가능/).waitFor();
    assert.equal(await page.locator('#v2DiscoverResults [data-v2-group="matched"] [data-v2-card]').count(),1);
    occupancyChecked=false;
    await page.locator('#v2DiscoverForm [name=move_in_by]').fill('2026-11-30');
    await page.locator('#v2DiscoverForm').getByRole('button',{name:'후보 찾기'}).click();
    await page.locator('#v2DiscoverResults [data-v2-occupancy]').click();
    await page.getByRole('status').getByText(/요청한 2026-11-30보다 늦어 기본 후보에서 제외/).waitFor();
    assert.equal(await page.locator('#v2DiscoverResults [data-v2-card]').count(),0);
    await page.locator('#v2DiscoverForm [name=move_in_by]').fill('');
    await page.locator('#v2DiscoverForm [name=max_commute_minutes]').fill('60');
    await page.locator('#v2DiscoverForm').getByRole('button',{name:'후보 찾기'}).click();
    assert.equal(discoveryPayloads.at(-1).max_commute_minutes,60);
    await page.locator('#v2DiscoverResults [data-v2-commute]').click();
    await page.getByText(/대중교통 안내 54분/).first().waitFor();
    assert.equal(await page.locator('#v2DiscoverResults [data-v2-group="matched"] [data-v2-card]').count(),1);
    commuteChecked=false;
    await page.locator('#v2DiscoverForm [name=max_commute_minutes]').fill('45');
    await page.locator('#v2DiscoverForm').getByRole('button',{name:'후보 찾기'}).click();
    await page.locator('#v2DiscoverResults [data-v2-commute]').click();
    await page.getByRole('status').getByText(/설정한 45분을 넘어 기본 후보에서 제외/).waitFor();
    assert.equal(await page.locator('#v2DiscoverResults [data-v2-card]').count(),0);
    await page.locator('#v2DiscoverForm [name=max_commute_minutes]').fill('');
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
    await page.locator('#v2DiscoverForm [name=region_code]').selectOption('11150');
    await page.locator('#v2DiscoverForm').getByRole('button',{name:'후보 찾기'}).click();
    await page.getByText(/조건 초과 1건을 비교용으로 보려면/).waitFor();
    assert.equal(await page.locator('#v2DiscoverResults [data-v2-card]').count(),0);
    await page.locator('#v2DiscoverResults [data-v2-relax=region_code]').click();
    assert.equal(await page.locator('#v2DiscoverForm [name=region_code]').evaluate(el=>el===document.activeElement),true);
    assert.equal(await page.locator('#v2DiscoverForm [name=region_code]').inputValue(),'11150');
    await page.locator('#v2DiscoverForm [name=region_mode]').selectOption('prefer');
    await page.locator('#v2DiscoverForm [name=region_code]').selectOption('11160');
    await page.locator('#v2DiscoverForm [name=max_monthly_manwon]').fill('200');
    await page.locator('#v2DiscoverForm').getByRole('button',{name:'후보 찾기'}).click();
    await page.locator('[data-v2-finance-setup]').click();
    assert.equal(await page.locator('#v2DiscoverDlg').evaluate(el=>el.open),false);
    assert.equal(await page.locator('#view-mypage').isVisible(),true);
    await page.evaluate(()=>SignalV2.openDiscovery());
    await page.locator('#v2DiscoverForm [name=max_monthly_manwon]').fill('');
    await page.locator('#v2DiscoverForm [name=max_commute_minutes]').fill('60');
    await page.locator('#v2DiscoverForm [name=region_code]').selectOption('11160');
    await page.locator('#v2DiscoverForm').getByRole('button',{name:'후보 찾기'}).click();
    await page.getByText(/내 정보에서 직장 위치를 먼저 저장하세요/).waitFor();
    assert.equal(await page.locator('#v2DiscoverResults [data-v2-commute]').count(),0);
    await page.locator('[data-v2-work-setup]').click();
    assert.equal(await page.locator('#mp_work').evaluate(el=>el===document.activeElement),true);
    await page.evaluate(()=>SignalV2.openDiscovery('중구'));
    await page.getByText(/선택한 지역을 확인할 수 없습니다/).waitFor();
    assert.equal(await page.locator('#v2DiscoverForm [type=submit]').isDisabled(),true);
    await page.locator('#v2DiscoverForm [name=region_code]').selectOption('28125');
    await page.locator('#v2DiscoverForm').getByRole('button',{name:'후보 찾기'}).click();
    assert.equal(discoveryPayloads.at(-1).prefer_region_code,'28125');
    await page.evaluate(()=>document.getElementById('v2DiscoverDlg').close());
    await page.evaluate(()=>{
      window.__savedPagingFetch=window.fetch;
      window.fetch=(input,...args)=>{
        const url=new URL(String(input),location.href);
        if(url.pathname==='/api/v2/report-snapshots'&&(!args[0]||!args[0].method)){
          const older=!!url.searchParams.get('cursor');
          const n=older?1:2;
          return Promise.resolve({ok:true,json:async()=>({items:[{
            report_id:String(n).repeat(64),subject_key:`kb:${n}`,kind:'지역',
            name:`저장 지역 ${n}`,asof:'2026-09-21',saved_at:1780000000-n}],
            next_cursor:older?null:'older-page'})});
        }
        return window.__savedPagingFetch(input,...args);
      };
    });
    await page.evaluate(()=>SignalV2.openSavedReports());
    await page.getByText('저장 지역 2').waitFor();
    assert.match(await page.locator('#v2ReportBody').textContent(),/삭제 전까지 보관하며 공개 공유 링크는 만들지 않습니다/);
    await page.locator('#v2ReportBody').getByRole('button',{name:'이전 저장본 더 보기'}).click();
    await page.getByText('저장 지역 1').waitFor();
    assert.equal(await page.locator('#v2ReportBody [data-saved-report]').count(),2);
    await page.evaluate(()=>{window.fetch=window.__savedPagingFetch;delete window.__savedPagingFetch;});
    await page.evaluate(()=>{
      window.__notePagingFetch=window.fetch;
      window.__noteRecord=(id,key,thesis)=>({id,subject_type:'region',subject_key:key,
        thesis,counter_condition:'가격이 달라지면 재검토',horizon_weeks:12,revision:1,report_id:null});
      window.fetch=(input,...args)=>{
        const url=new URL(String(input),location.href);
        if(url.pathname==='/api/v2/decision-notes'&&(!args[0]||!args[0].method)){
          if(url.searchParams.get('subject_key')==='A')
            return new Promise(resolve=>{window.__resolveNoteA=()=>resolve({ok:true,json:async()=>({
              notes:[window.__noteRecord(1,'A','늦은 기록')],next_cursor:null})});});
          const older=url.searchParams.get('cursor')==='older-note';
          const key=url.searchParams.get('subject_key');
          const notes=key==='B'?[window.__noteRecord(2,'B','새 대상 기록')]:
            [window.__noteRecord(older?3:4,'전체',older?'과거 기록':'최신 기록')];
          return Promise.resolve({ok:true,json:async()=>({notes,
            next_cursor:key||older?null:'older-note'})});
        }
        return window.__notePagingFetch(input,...args);
      };
    });
    await page.evaluate(()=>SignalV2.openNote('region','A'));
    await page.evaluate(()=>SignalV2.openNote('region','B'));
    await page.getByText('새 대상 기록').waitFor();
    await page.evaluate(()=>window.__resolveNoteA());
    await page.waitForTimeout(20);
    assert.doesNotMatch(await page.locator('#v2NoteList').textContent(),/늦은 기록/);
    await page.evaluate(()=>SignalV2.openNotes());
    await page.getByText('최신 기록').waitFor();
    await page.locator('#v2NoteList [data-note-more]').click();
    await page.getByText('과거 기록').waitFor();
    assert.equal(await page.locator('#v2NoteList [data-note-edit]').count(),2);
    assert.equal(await page.locator('#v2NoteList [data-note-more]').count(),0);
    await page.evaluate(()=>{document.getElementById('v2NoteDlg').close();window.fetch=window.__notePagingFetch;
      delete window.__notePagingFetch;delete window.__noteRecord;delete window.__resolveNoteA;});
    for (const width of [390, 1280]) {
      await page.setViewportSize({width,height:844});
      await page.evaluate(()=>SignalV2.openDiscovery());
      await page.locator('#v2DiscoverForm').getByRole('button',{name:'후보 찾기'}).click();
      await page.locator('#v2DiscoverResults [data-v2-card]').first().waitFor();
      const layout = await page.evaluate(() => ({viewport:innerWidth,
        page:document.documentElement.scrollWidth,
        dialog:document.getElementById('v2DiscoverDlg').getBoundingClientRect().width,
        results:document.getElementById('v2DiscoverResults').scrollWidth,
        available:document.getElementById('v2DiscoverResults').clientWidth}));
      assert(layout.page<=layout.viewport,`discovery page overflows at ${width}px: ${JSON.stringify(layout)}`);
      assert(layout.dialog<=layout.viewport,`discovery dialog overflows at ${width}px: ${JSON.stringify(layout)}`);
      assert(layout.results<=layout.available,`discovery results overflow at ${width}px: ${JSON.stringify(layout)}`);
      await page.evaluate(()=>document.getElementById('v2DiscoverDlg').close());
    }
    const rollout = await page.evaluate(async()=>{
      const original=window.openListingReport;
      window._featureFlags={report_v2_enabled:true,discovery_v2_enabled:true,
        contextual_explanations_enabled:false,nick_global_entry_enabled:false};
      document.body.classList.add('nick-global-disabled');
      await SignalV2.paintRegion('테스트구','kb:1114000000');
      const aiHidden=!document.getElementById('v2RegionExplain');
      window._featureFlags.report_v2_enabled=false;
      window._featureFlags.discovery_v2_enabled=false;
      window.__fallbackKey=null;
      window.openListingReport=key=>{window.__fallbackKey=key;};
      SignalV2.openListing('일반매물:synthetic-hb-1');
      SignalV2.openDiscovery('테스트구');
      await SignalV2.paintRegion('테스트구','kb:1114000000');
      switchTab('signal');
      const result={aiHidden,fallbackKey:window.__fallbackKey,
        discoverClosed:!document.getElementById('v2DiscoverDlg').open,
        reportPaused:document.getElementById('haesolPanel').textContent.includes('일시 중지'),
        nickHidden:getComputedStyle(document.getElementById('advFab')).display==='none',
        retainedClass:document.body.classList.contains('nick-global-disabled')};
      window.openListingReport=original;
      window._featureFlags={};
      delete window.__fallbackKey;
      return result;
    });
    assert.deepEqual(rollout,{aiHidden:true,fallbackKey:'일반매물:synthetic-hb-1',
      discoverClosed:true,reportPaused:true,nickHidden:true,retainedClass:true});
    assert.deepEqual(errors,[]);
    console.log('PASS: Chromium 360/390/1280px, candidate clarity, lazy fetch, history, loan rendering, keyboard toggle, A→B stale race, paged decision notes, quicksale evidence, listing report and Nick context');
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
