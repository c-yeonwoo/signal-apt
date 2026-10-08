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
    let generalRefreshCooldown=false, buildingUnavailable=false, costsVerified=false;
    let serverClientVersion='synthetic-v1';
    let regionReportFailures=0, seriesFailures=0;
    page.on('pageerror', e=>errors.push(e.message));
    await page.route('**/*', async route=>{
      const url=new URL(route.request().url());
      if(url.hostname!=='buyer.test') {
        if(url.pathname.includes('echarts')) return route.fulfill({contentType:'application/javascript',body:'window.echarts={init:()=>({resize(){},on(){},setOption(){},clear(){},getOption(){return {}}})};'});
        return route.fulfill({body:'',contentType:url.pathname.endsWith('.js')?'application/javascript':'text/css'});
      }
      if(url.pathname==='/') return route.fulfill({body:html.replace('__SIGNAL_APT_CLIENT_VERSION__','synthetic-v1'),contentType:'text/html'});
      if(url.pathname==='/assets/signal-v2.js') return route.fulfill({body:signalV2,contentType:'application/javascript'});
      if(url.pathname==='/api/client-version') return route.fulfill({json:{client_version:serverClientVersion}});
      calls.push(url.pathname);
      let data={ready:false,items:[],listings:[],regions:[],actions:[],message:'합성 테스트 데이터'};
      if(url.pathname==='/api/auth/me') return route.fulfill({status:401,json:{}});
      if(url.pathname==='/api/events'){
        eventPayloads.push(route.request().postDataJSON());
        return route.fulfill({json:{ok:true}});
      }
      if(url.pathname==='/api/addr-search') return route.fulfill({json:{results:[
        {name:'중구청',address:'서울특별시 중구 다동',sigungu:'중구',sido:'서울',region_id:'kb:1114000000'},
        {name:'미확인 주소',address:'시군구 미확인',sigungu:'',sido:'',region_id:null}
      ]}});
      const decoded=decodeURIComponent(url.pathname);
      if(decoded.startsWith('/api/region-trade-prices/')){
        const region=decoded.endsWith('1115000000')?'옆구':'테스트구';
        const area=Number(url.searchParams.get('area'));
        return route.fulfill({json:area===59
          ? {status:'insufficient_sample',region,area,count:2,median_manwon:null,observed_months:['2026-08','2026-09'],computed_at:'2026-10-05'}
          : {status:'ready',region,area,count:8,median_manwon:region==='옆구'?70000:80000,
            observed_months:['2026-04','2026-09'],computed_at:'2026-10-05'}});
      }
      if(decoded==='/api/v2/regions/kb:1114000000/report' && regionReportFailures>0){
        regionReportFailures--;
        return route.fulfill({status:503,json:{detail:'synthetic source failure'}});
      }
      if(decoded==='/api/series/테스트구' && seriesFailures>0){
        seriesFailures--;
        return route.fulfill({status:503,json:{detail:'synthetic source failure'}});
      }
      if(['/api/v2/regions/테스트구/report','/api/v2/regions/kb:1114000000/report'].includes(decoded)) data={type:'region',report_id:'b'.repeat(64),
        subject:{region:'테스트구',region_id:'kb:1114000000'},asof:'2026-09-28',
        assessment:{display_grade:'매수',assessment_status:'ready',scope_note:'테스트 권역 자료',
          summary:'지역 신호만 보여 줍니다. 개별 매물의 가격 판단은 별도입니다.',raw_grade:'BUY',
          reasons:[{reason_id:'jeonse_pressure',label:'전세수급 압력',value:180,threshold:170,unit:'지수',role:'driver',passing:true}],
          change:{type:'first_observation',changed_reasons:[]}},
        positive:[{reason_id:'jeonse_pressure',label:'전세수급 압력',value:180,previous_value:180,threshold:170,unit:'지수',role:'driver',passing:true}],
        cautions:[{reason_id:'buyer_interest',label:'매수심리 관찰선 미충족',value:68,previous_value:66,threshold:70,unit:'지수',role:'driver',passing:false}],
        unknowns:[]};
      if(['/api/v2/regions/테스트구/report','/api/v2/regions/kb:1114000000/report'].includes(decoded)) regionReport=data;
      if(decoded==='/api/series/테스트구') data={metrics:{},volume:null};
      if(decoded==='/api/complex/테스트구/테스트단지') data={단지명:'테스트단지',identity_status:'single_observed',
        평형별:[{평형:26,'전용㎡':84.9,최근매매:50000,평단가:2000,매매건수:4,
          비교거래:{상태:'관측',건수:4,중앙값:50000,최저:45000,최고:55000,거래월범위:'2026-08~2026-09'}}],
        매매추이:[{ym:'2026-08',평단가:2000,건수:4}],최근평단가:2000,총거래:4,기간:'2026-08',추세pct:0};
      if(decoded==='/api/complex/테스트구/테스트단지/building') data=buildingUnavailable
        ? {ok:false} : {ok:true,building:{세대수:123,건축년도:2005}};
      if(decoded==='/api/complex/테스트구/테스트단지/quote-check') {
        quotePayloads.push(route.request().postDataJSON());
        data={상태:'관측비교',입력호가:48000,입력층:11,
          중앙값:50000,표본수:4,호가차액:-2000,호가차이율:-4,거래월범위:'2026-08~2026-09'};
      }
      if(url.pathname==='/api/listing-costs') data={매수가:50000,'총매입가':51600,
        부대비용:{취득세:500,중개비:100,법무비:50,이사비:200,인테리어:750,합계:1600},
        가정:{주택수:0,규제지역:false},assumption_checks:{home_count_entered:costsVerified,policy_status:costsVerified?'verified':'unverified'},
        notes:['생애최초 취득세 감면(한도) 반영','규제 기준일 2026-07-01','가정 기반 추정 · 최종 비용 확인 필요']};
      if(url.pathname==='/api/action-plan') data={actions:[{key:'confirm_power',title:'예산 가정을 확인하세요',cta:'예산 설정',tab:'mypage'}]};
      if(url.pathname==='/api/complex-watch') data={ready:true,total:6,moved_total:1,quiet:false,
        items:[
          ...[1,2,3,4,5].map(i=>({key:`테스트구|조용한단지${i}`,'단지명':`조용한단지${i}`,'지역':'테스트구',changes:[]})),
          {key:'테스트구|호가단지','단지명':'호가단지','지역':'테스트구','평형':25,'거래가':48000,data_days:1,
            changes:[{kind:'new_trade','말':'새 실거래'}]}
        ],unavailable:[]};
      if(url.pathname==='/api/listings/all') data={listings:[],asof:'2026-09-21',meta:{private_access:false,data_age_days:7}};
      if(url.pathname==='/api/general-listings/refresh' && route.request().method()==='POST')
        return route.fulfill({json:generalRefreshCooldown?{ok:false,reason:'cooldown'}:{ok:true,count:1,regions:1}});
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
          name:next?'두번째 후보':'첫번째 후보',region:'테스트구',kind:'일반매물',asking_manwon:50000,
          exclusive_m2:59,rooms:3},
          eligibility:finance?'verify':'matched',recommendation_reason:'호가 조건 부합',
          tradeoff:'자료 확인',verify_next:'판매 여부 확인',
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
        if(spec.max_price_manwon===61000){
          const stale={...candidate,listing:{...candidate.listing,key:'일반매물:synthetic-stale',
            name:'지난 호가 후보',asking_manwon:120000,stale:true},eligibility:'verify',
            recommendation_reason:'지난 수집 호가는 설정한 상한보다 높았습니다. 현재 가격은 확인되지 않아 예산 적합성을 보류했습니다.',
            tradeoff:'지난 호가 기준으로는 예산을 넘었습니다.',verify_next:'최신 호가를 확인하세요.'};
          data.counts={matched:0,verify:1,explore:0,exceeded:0};
          data.groups={matched:[],verify:[stale],explore:[],exceeded:[]};
          data.next_cursor=null;
        }
        if(spec.max_price_manwon===62000){
          const conflict={...candidate,listing:{...candidate.listing,key:'찐매물:synthetic-conflict',
            name:'원천 충돌 후보',asking_manwon:120000,source_conflict:true},eligibility:'verify',
            recommendation_reason:'같은 원천 ID의 매물 정보가 서로 달라 조건 판정을 보류했습니다.',
            tradeoff:'현재 알려진 조건에서는 양보할 점을 확인하지 못했습니다.',
            verify_next:'원천에서 단지·전용면적·층, 판매 여부와 실제 호가를 먼저 확인하세요.'};
          data.counts={matched:0,verify:1,explore:0,exceeded:0};
          data.groups={matched:[],verify:[conflict],explore:[],exceeded:[]};
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
      const naverMatched=url.pathname==='/api/v2/listings/report' && url.searchParams.get('key')==='급매:naver-match';
      const auctionReport=url.pathname==='/api/v2/listings/report' && url.searchParams.get('key')==='경매:auction-1';
      if(url.pathname==='/api/v2/listings/report') data={type:'listing',report_id:'synthetic-report',
        subject:{key:url.searchParams.get('key'),name:'한방테스트단지',region:'테스트구',
          kind:auctionReport?'경매':'일반매물',asking_manwon:auctionReport?42000:50000,exclusive_m2:59,region_identity_status:'matched',collected_at:'2026-09-29',
          coordinate:url.searchParams.get('key')==='일반매물:location-auto'?[37.65,127.07]:null,
          naver_complex_no:naverMatched?'12345':null,
          asking_comparison:naverMatched?{상태:'관측비교',표본수:4,중앙값:53000,호가차이율:-5.7}:null,
          price_reduction:naverMatched?{상태:'수집호가인하관측',차이율:-2}:null},
        price:naverMatched?{'상태':'관측비교','표본수':3,'호가차이율':-5.2,'비교기준일':'2026-09-29',
          '중앙값':52000,'범위':[49000,55000],'거래월범위':'2026-07~2026-09',
          '비교기준':'같은 단지·전용면적·입력 층 ±2층'}:
          {'상태':'관측비교','표본수':3},
        complex:{identity_status:'single_observed','시그널':'BUY',
          '단지시그널':{'등급':'HELD','판정상태':'held','주의':'지역 판정 보류'}},
        buyer_fit:naverMatched?{status:'within',gap_manwon:3000}:{status:'unknown'},
        positive:[{text:'동일 조건 실거래가 있습니다.'}],
        cautions:[{text:'현재 판매 여부는 확인되지 않았습니다.'}],
        next_actions:[auctionReport?'중개사에게 실제 호가 확인':'실제 호가 확인'],evidence:[{id:'trades',label:'국토부 실거래',asof:'2026-09-29',status:'관측'}]};
      if(url.pathname==='/api/v2/listings/report' && url.searchParams.get('key')==='일반매물:profile-fail')
        data.partial_failures=['buyer_profile_unavailable'];
      if(url.pathname==='/api/v2/listings/report' && url.searchParams.get('key')==='일반매물:cost-held'){
        data.subject.source_conflict=true;
        data.price={상태:'보류',이유:'원천 정보 충돌'};
      }
      if(url.pathname==='/api/v2/listings/report' && url.searchParams.get('key')==='일반매물:trade-missing' && !tradeEnriched)
        data.partial_failures=['trade_cache_unavailable'];
      if(url.pathname==='/api/v2/listings/report') reportsByKey.set(url.searchParams.get('key'),data);
      if(/^\/api\/v2\/reports\/[^/]+\/explanations$/.test(url.pathname)) {
        const payload=route.request().postDataJSON(); explanationPayloads.push(payload);
        const region=payload.type==='region';
        const fallback=region&&payload.mode==='counterevidence';
        return route.fulfill({status:200,json:{job_id:'test-job',status:fallback?'failed':'succeeded',result:{
          report_id:decodeURIComponent(url.pathname.split('/')[4]),mode:payload.mode,
          source:fallback?'deterministic_fallback':'model_validated',
          summary:fallback?'현재 판정에 반대되는 근거와 한계를 먼저 확인하세요.':'현재 리포트의 가격 근거를 확인하세요.',
          claims:fallback?[]:[region?{text:'전세수급 자료와 반대 근거를 함께 확인해야 합니다.',evidence_ids:['jeonse_pressure']}:
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
    assert.match(await page.locator('#dashCxWrap').textContent(),/테스트구/);
    assert.match(await page.locator('#dashCxWrap').textContent(),/새 실거래/);
    assert.equal(await page.locator('#dashCxWrap [data-complex-index]').first().textContent(),'호가단지',
      'changed favorite complexes are prioritized ahead of unchanged ones');
    assert.match(await page.locator('#dashCxWrap').textContent(),/관심단지 전체 6곳 보기/);
    assert.equal(await page.locator('#dashCxWrap [data-complex-index]').count(),6,
      'the full list is available inside progressive disclosure');
    assert.equal(await page.locator('#dashCxWrap').evaluate(el=>!!el.closest('details')),false);
    assert.equal(calls.includes('/api/complex-watch'),true,'favorite complexes are loaded for the home');
    assert.equal(calls.includes('/api/asks'),false,'budget candidates are not loaded on the home');
    for(const url of ['/api/regime','/api/news','/api/shortlist']) assert(!calls.includes(url),`eager hidden source ${url}`);
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
    assert.equal(calls.filter(x=>x==='/api/shortlist').length,0);
    await page.getByRole('button',{name:'내 지도',exact:true}).click();
    assert.equal(new URL(page.url()).hash,'#mymap');
    assert.match(await page.locator('#myMapGuide').textContent(),/비교를 시작/);
    assert.match(await page.locator('#mmTray').textContent(),/비교에 넣은 단지/);
    await page.getByRole('button',{name:'내 조건',exact:true}).click();
    assert.equal(new URL(page.url()).hash,'#mypage');
    assert.equal(await page.locator('#mp_capital').isVisible(),true);
    assert.equal(await page.locator('#mp_income').isVisible(),true);
    assert.equal(await page.locator('#mpExtra').getAttribute('open'),null);
    assert.equal(await page.locator('#mp_work').isVisible(),false);
    await page.locator('#mpExtra > summary').focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('#mp_work').isVisible(),true);
    assert.equal(await page.locator('#mp_chungyak').isVisible(),true);
    for(const width of [180,360,390]){
      await page.setViewportSize({width,height:800});
      const profileWidth=await page.evaluate(()=>document.documentElement.scrollWidth);
      assert(profileWidth<=width,`profile page overflows at ${width}px: ${profileWidth}`);
    }
    await page.setViewportSize({width:360,height:800});
    await page.goBack();
    await page.goBack();
    await page.waitForFunction(()=>document.body.className==='tab-dashboard');
    await page.getByRole('button',{name:'시그널',exact:true}).click();
    assert.equal(new URL(page.url()).hash,'#signal');
    assert.equal(await page.getByRole('button',{name:'시그널',exact:true}).getAttribute('aria-current'),'page');
    assert.equal(await page.locator('#groupSubnav').isVisible(),true);
    assert.equal(await page.getByRole('button',{name:/가격·수급/}).count(),1);
    await page.evaluate(()=>{allSignals=[
      {region:'테스트구',region_id:'kb:1114000000',group:'서울'},
      {region:'옆구',region_id:'kb:1115000000',group:'서울'},
      {region:'인천 중구',region_id:'kb:2811000000',group:'인천'}]; selected='테스트구';});
    await page.getByRole('button',{name:/지역 가격 비교/}).click();
    assert.equal(new URL(page.url()).hash,'#undervalued');
    assert.equal(await page.getByRole('button',{name:/지역 가격 비교/}).getAttribute('class').then(c=>c.includes('on')),true);
    assert.equal(await page.locator('#view-undervalued #sigMap').count(),1);
    assert.equal(await page.locator('#view-signal #sigMap').count(),0);
    assert.match(await page.locator('#sigMapLegend').textContent(),/평단가 급지/);
    assert.match(await page.locator('#sigMapLegend').textContent(),/매수 추천이 아닙니다/);
    await page.locator('#rpcCards').getByText('8.0억').waitFor();
    assert.match(await page.locator('#rpcCards').textContent(),/서울 테스트구[\s\S]*8건.*2026-04~2026-09/);
    assert.equal(await page.locator('#rpcRegionA option[value="kb:2811000000"]').textContent(),'인천 중구');
    await page.locator('#rpcRegionB').selectOption('kb:1115000000');
    await page.locator('#rpcCards').getByText('7.0억').waitFor();
    assert.match(await page.locator('#rpcCompare').textContent(),/중앙값 차이 1.0억.*저평가됐다는 뜻은 아닙니다/);
    await page.locator('#rpcArea').selectOption('59');
    await page.locator('#rpcCards').getByText('중앙값 보류').first().waitFor();
    assert.equal(await page.locator('#rpcCards .rpc-median').count(),2);
    assert.match(await page.locator('#rpcCompare').textContent(),/표본 부족/);
    for(const width of [180,360,390,1280]){
      await page.setViewportSize({width,height:800});
      const layout=await page.evaluate(()=>({document:document.documentElement.scrollWidth,
        viewport:innerWidth,cards:document.getElementById('rpcCards').scrollWidth,
        available:document.getElementById('rpcCards').clientWidth}));
      assert(layout.document<=layout.viewport && layout.cards<=layout.available,
        `region price comparison overflow ${width}: ${JSON.stringify(layout)}`);
    }
    await page.setViewportSize({width:360,height:800});
    await page.getByRole('button',{name:/가격·수급/}).click();
    assert.equal(new URL(page.url()).hash,'#signal');
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
    await page.evaluate(()=>_laApplyResponse('일반매물',{
      listings:[],asof:'2026-09-28',meta:{private_access:true,general_refresh_failed:true}}));
    assert.equal(await page.locator('#laSourceWarning').isVisible(),true);
    assert.match(await page.locator('#laSourceWarning').textContent(),/현재 목록은 전체가 아니며/);
    await page.setViewportSize({width:180,height:800});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),
      'source recovery notice must fit the narrow viewport');
    await page.setViewportSize({width:360,height:800});
    generalRefreshCooldown=true;
    await page.locator('#laGeneralRetry').click();
    await page.getByText(/10분이 지나야 다시 수집/).waitFor();
    assert.equal(await page.locator('#laSourceWarning').isVisible(),true);
    generalRefreshCooldown=false;
    await page.locator('#laGeneralRetry').click();
    await page.waitForFunction(()=>document.getElementById('laSourceWarning').style.display==='none');
    assert(calls.includes('/api/general-listings/refresh'));
    await page.evaluate(()=>_laApplyResponse('일반매물',{
      listings:[],asof:'2026-09-28',meta:{private_access:false,general_refresh_failed:true}}));
    assert.equal(await page.locator('#laSourceWarning').isVisible(),false,'guests must not see private refresh status');
    await page.evaluate(()=>_laApplyResponse('일반매물',{
      listings:[],asof:'2026-09-28',meta:{private_access:true,
        general_scope:{failed_regions:1,page_limited_regions:2}}}));
    assert.equal(await page.locator('#laSourceWarning').isVisible(),false);
    assert.match(await page.locator('#laSourceScope').textContent(),/1곳 조회 실패 · 2곳 페이지 제한/);
    assert.equal(await page.locator('#laSourceScope').isVisible(),true);
    assert(await page.locator('#laSourceScope').evaluate(el=>parseFloat(getComputedStyle(el).fontSize)>=13),
      'partial collection notice must not be tiny body text');
    await page.setViewportSize({width:180,height:800});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),
      'partial collection notice must fit the narrow viewport');
    await page.setViewportSize({width:360,height:800});
    await page.evaluate(()=>_laApplyResponse('일반매물',{
      listings:[],asof:'2026-09-28',meta:{private_access:false,
        general_scope:{failed_regions:1,page_limited_regions:2}}}));
    assert.equal(await page.locator('#laSourceScope').isVisible(),false,'guests must not see private coverage');
    const budgetUi=await page.evaluate(()=>{
      mapSplit=(_list,_map,items,opt)=>{window.__budgetItems=items;window.__budgetOpt=opt;};
      const row={key:'일반매물:budget',유형:'일반매물',단지명:'예산테스트단지',지역:'테스트구',
        시도:'서울',총액:50000,기회도:50,지표라벨:'등록일',지표값:'2026-10-03',
        lines:{cash:'계산상 됩니다',price:'호가',unknown:'확인 필요',next:'확인'}};
      _laApplyResponse('일반매물',{listings:[{...row,budget_fit:{status:'unknown',reason:'가정 변경'}}],
        asof:'2026-09-28',meta:{confirmed_budget:false,private_access:true}});
      const held={note:document.getElementById('laBudgetNote').textContent,
        sort:document.querySelector('#laSort option[value="budget"]').textContent,
        label:window.__budgetOpt.detail(window.__budgetItems[0]),
        cash:_buyerFour(window.__budgetItems[0],_eok).cash};
      _laApplyResponse('일반매물',{listings:[
        {...row,key:'above',총액:60000,budget_fit:{status:'above'}},
        {...row,key:'within',총액:40000,budget_fit:{status:'within'}},
        {...row,key:'unknown',총액:30000,budget_fit:{status:'unknown',reason:'지역 확인 필요'}}],
        asof:'2026-09-28',meta:{confirmed_budget:true,private_access:true}});
      return {held,order:window.__budgetItems.map(x=>x.key),
        within:window.__budgetOpt.detail(window.__budgetItems[0]),
        above:window.__budgetOpt.detail(window.__budgetItems[2]),
        cash:_buyerFour(window.__budgetItems[2],_eok).cash};
    });
    assert.match(budgetUi.held.note,/매수력 확정 가정·지역이 없거나 바뀌었습니다/);
    assert.match(budgetUi.held.sort,/예산 미확정/);
    assert.match(budgetUi.held.label,/매물 리포트 보기/);
    assert.doesNotMatch(budgetUi.held.cash,/계산상 됩니다/);
    assert.deepEqual(budgetUi.order,['within','unknown','above']);
    assert.doesNotMatch(budgetUi.within,/확정 매수력 상한 이내/);
    assert.doesNotMatch(budgetUi.above,/확정 매수력 상한 초과/);
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
    const priceEvidenceUi=await page.evaluate(()=>{
      const base={유형:'일반매물',지역:'테스트구',시도:'서울',총액:50000,기회도:10,지역식별상태:'matched',
        ref:{전용면적:59,hanbang_id:'sample',hanbang_complex_id:'complex'},source:'hanbang'};
      _laApplyResponse('일반매물',{listings:[
        {...base,key:'single',단지명:'단일근거',price_comparison:{상태:'관측비교',호가차이율:-12,표본수:4}},
        {...base,key:'overlap',단지명:'근거중첩',price_comparison:{상태:'관측비교',호가차이율:-5,표본수:4},
          asking_comparison:{상태:'관측비교',호가차이율:-7,표본수:3,공급사:'hanbang'},
          price_reduction:{상태:'수집호가인하관측',차이율:-2,관측횟수:2,확인시각:100}},
        {...base,key:'positive',단지명:'양의비교',price_comparison:{상태:'관측비교',호가차이율:3,표본수:5}}
      ],asof:'2026-09-28',meta:{confirmed_budget:false,private_access:true}});
      document.getElementById('laSort').value='evidence_overlap'; onLaSort();
      return {order:window.__budgetItems.map(x=>x.key),detail:window.__budgetOpt.detail(window.__budgetItems[0])};
    });
    assert.equal(priceEvidenceUi.order[0],'overlap');
    assert.doesNotMatch(priceEvidenceUi.detail,/여러 가격 근거 관측:/);
    assert.match(priceEvidenceUi.detail,/선택한 매물 확인/);
    const ambiguousFavorite=await page.evaluate(async()=>{
      const originalFetch=window.fetch, oldFavorites=_favs, oldIdentity=_favComplexIdentity;
      _favs=new Set(['complex:중구|옛 관심단지']);
      _favComplexIdentity=new Map([['중구|옛 관심단지',{status:'needs_reselection',
        message:'이름만 저장된 중구는 서울·개편 전 인천을 구별할 수 없습니다.'}]]);
      window.fetch=(input,...args)=>String(input)==='/api/complex-watch'
        ? Promise.resolve({ok:true,json:async()=>({ready:true,total:1,moved_total:0,quiet:true,items:[],
            unavailable:[{key:'중구|옛 관심단지','단지명':'옛 관심단지','지역':'중구',
              reason:'이름만 저장된 중구는 서울·개편 전 인천을 구별할 수 없습니다. 지역 재선택 전 변화 판정을 보류합니다.'}]})})
        : originalFetch(input,...args);
      try {
        await renderFavList(); await _loadComplexWatch();
        return {favorite:document.getElementById('favListBody').innerHTML,
          complexWatch:document.getElementById('dashCxWrap').innerHTML};
      }
      finally { window.fetch=originalFetch; _favs=oldFavorites; _favComplexIdentity=oldIdentity; }
    });
    assert.match(ambiguousFavorite.favorite,/서울·개편 전 인천을 구별할 수 없습니다/);
    assert.match(ambiguousFavorite.favorite,/지역 재선택/);
    assert.doesNotMatch(ambiguousFavorite.favorite,/＋비교|>상세</);
    assert.match(ambiguousFavorite.complexWatch,/서울·개편 전 인천을 구별할 수 없습니다/);
    assert.match(ambiguousFavorite.complexWatch,/중구/);
    assert.doesNotMatch(ambiguousFavorite.complexWatch,/openComplex/);
    const manyUnavailable=await page.evaluate(async()=>{
      const originalFetch=window.fetch;
      window.fetch=(input,...args)=>String(input)==='/api/complex-watch'
        ? Promise.resolve({ok:true,json:async()=>({ready:true,total:5,moved_total:0,quiet:true,items:[],
            unavailable:[1,2,3,4,5].map(i=>({key:`지역|${i}단지`,'단지명':`${i}단지`,'지역':'지역',
              reason:'지역 재선택 전 변화 판정을 보류합니다.'}))})})
        : originalFetch(input,...args);
      try {
        await _loadComplexWatch();
        const wrap=document.getElementById('dashCxWrap');
        const details=wrap.querySelector('.dash-unavailable details');
        return {html:wrap.innerHTML,topRows:wrap.querySelectorAll('.dash-unavailable > div:nth-child(n+2)').length,
          foldedRows:details?.querySelectorAll('.dash-more-body > div').length||0,folded:details?.open===false};
      } finally { window.fetch=originalFetch; }
    });
    assert.match(manyUnavailable.html,/확인이 필요한 나머지 2곳 보기/);
    assert.equal(manyUnavailable.topRows,3,'only the first three unavailable favorites are initially visible');
    assert.equal(manyUnavailable.foldedRows,2,'the remaining unavailable favorites are retained');
    assert.equal(manyUnavailable.folded,true,'remaining unavailable favorites are folded but retained');
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
    assert.equal(await page.locator('.watch-card-head .watch-btn').count(),0,
      'primary favorite card offers only the candidate action');
    assert.equal(await page.locator('.watch-card-more > summary').textContent(),'알림·찜 관리');
    assert.equal(await page.locator('.watch-records').getAttribute('open'),null);
    assert.equal(await page.locator('.watch-card-more').getAttribute('open'),null);
    assert.equal(await page.locator('.watch-card-prior').count(),0);
    assert.equal(await page.locator('.watch-card-detail-line').isVisible(),false);
    assert.equal(await page.locator('.watch-alternatives').isVisible(),false);
    assert.equal(await page.locator('#view-watch .watch-btn').isVisible(),false);
    await page.locator('.watch-card-more > summary').click();
    assert.match(await page.locator('.watch-card-detail-line').textContent(),/찜 당시 호가/);
    assert.match(await page.locator('.watch-alternatives').textContent(),/같은 단지의 다른 매물/);
    assert.match(await page.locator('#view-watch .watch-btn').textContent(),/찜 해제/);
    for(const width of [180,360,390,1280]){
      await page.setViewportSize({width,height:800});
      const watchWidth=await page.evaluate(()=>{const card=document.querySelector('.watch-card'),edge=card.getBoundingClientRect().right;
        return {page:document.documentElement.scrollWidth,card:card.scrollWidth,available:card.clientWidth,
          overflow:[...card.querySelectorAll('*')].filter(el=>el.getBoundingClientRect().right>edge+1)
            .slice(0,5).map(el=>`${el.tagName}.${el.className}: ${el.textContent.trim().slice(0,25)}`)};});
      assert(watchWidth.page<=width && watchWidth.card<=watchWidth.available,
        `watch page overflows at ${width}px: ${JSON.stringify(watchWidth)}`);
    }
    await page.setViewportSize({width:360,height:800});
    assert.equal(await page.locator('#view-watch .watch-btn').getAttribute('aria-pressed'),'true');
    await page.locator('#view-watch').getByRole('button',{name:'매물 확인'}).click();
    await page.locator('#v2ReportDlg').getByText('한방테스트단지',{exact:false}).waitFor();
    assert.equal(await page.locator('#v2BackToDiscovery').isVisible(),false);
    await page.locator('#v2ReportDlg').getByRole('button',{name:'리포트 닫기'}).click();
    await page.evaluate(()=>window.SignalV2.openListing('경매:auction-1'));
    await page.locator('#v2ReportBody').getByText('경매 물건, 먼저 확인할 두 가지',{exact:true}).waitFor();
    const auctionReportText=await page.locator('#v2ReportBody').textContent();
    assert.match(auctionReportText,/최저매각가/);
    assert.match(auctionReportText,/권리 안전성·필요 현금·입찰가/);
    assert.match(auctionReportText,/매각물건명세서·현황조사서·감정평가서/);
    assert.doesNotMatch(auctionReportText,/가격은 괜찮나요|내 예산에 맞나요|중개사에게 실제 호가 확인/);
    assert.match(auctionReportText,/사건·권리 정보 아님/);
    assert.match(auctionReportText,/경매 최저가·낙찰가와는 다른 자료/);
    assert.doesNotMatch(auctionReportText,/수집 호가와 가격 변화/);
    await page.locator('#v2ReportDlg').getByRole('button',{name:'리포트 닫기'}).click();
    await page.evaluate(()=>switchTab('watch'));
    assert.equal(await page.locator('.watch-card-more').getAttribute('open'),'');
    await page.locator('#view-watch input[type="number"]').fill('47000');
    await Promise.all([page.waitForResponse(r=>r.url().includes('/api/listing-watch/price-target') && r.request().method()==='PUT'),
      page.locator('#view-watch').getByRole('button',{name:'목표 저장·해제'}).click()]);
    assert.deepEqual(targetPayloads,[{key:'급매:synthetic-1',target_manwon:47000}]);
    assert.equal(await page.locator('.watch-card-more').getAttribute('open'),'');
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
      loadComplexAgents=()=>{}; loadCxTxCosts=()=>{};
      await openComplex('테스트단지','테스트구');
    });
    const buildingPath='/api/complex/테스트구/테스트단지/building';
    assert.equal(calls.filter(path=>decodeURIComponent(path)===buildingPath).length,0,'closed building details do not fetch');
    assert.equal(calls.includes('/api/loan-scenarios'),false,'complex view no longer loads legacy LTV scenarios');
    assert.equal(await page.locator('#cxLoanScen').count(),0);
    await page.locator('#cxMoreDetails > summary').click();
    await page.getByText('세대 123',{exact:true}).waitFor();
    assert.equal(calls.filter(path=>decodeURIComponent(path)===buildingPath).length,1);
    await page.locator('#cxMoreDetails > summary').click();
    await page.locator('#cxMoreDetails > summary').click();
    assert.equal(calls.filter(path=>decodeURIComponent(path)===buildingPath).length,1,'reopening does not refetch the same details');
    await page.getByText('내가 확인한 호가와 비교하기',{exact:true}).click();
    await page.locator('#cxQuoteFloor').fill('11');
    await page.locator('#cxQuoteAmount').fill('48000');
    await page.locator('#cxQuoteBtn').click();
    await page.getByText('산술 차이',{exact:false}).waitFor();
    assert.match(await page.locator('#cxQuoteResult').textContent(),/2,000만.*낮음/);
    assert.match(await page.locator('#cxQuoteResult').textContent(),/11층 ±2층/);
    await page.keyboard.press('Escape');
    buildingUnavailable=true;
    await page.evaluate(()=>openComplex('테스트단지','테스트구'));
    await page.locator('#cxMoreDetails > summary').click();
    await page.getByText('이 단지의 건축물대장을 연결하지 못했습니다.',{exact:false}).waitFor();
    assert.doesNotMatch(await page.locator('#cxBuilding').textContent(),/키 미설정|지번 매칭 실패/);
    assert.equal(await page.locator('#cxBuilding a').getAttribute('href'),
      'https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=15000000098');
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
    await page.waitForFunction(()=>document.body.classList.contains('v2-report-map'));
    const behindMap=await page.locator('#hbMap').evaluate(el=>({top:el.getBoundingClientRect().top,
      width:el.getBoundingClientRect().width}));
    assert.equal(behindMap.top,0);
    assert.equal(behindMap.width,360);
    assert.match(await page.locator('#v2ReportBody .v2-report-section').first().textContent(),/가격은 괜찮나요[\s\S]*비교 가능한 거래가 부족해 가격 판단을 보류해요[\s\S]*내 예산에 맞나요/);
    assert.equal(await page.locator('#v2ReportBody .v2-report-more[open]').count(),0);
    assert.doesNotMatch(await page.locator('#v2ReportBody .v2-report-section').allTextContents().then(rows=>rows.join(' ')),/출입구가 검증되지|통학구역 자료를 연결하지/);
    const reportLayout=await page.evaluate(()=>({modal:document.getElementById('v2ReportDlg').matches(':modal'),
      width:document.getElementById('v2ReportDlg').getBoundingClientRect().width,
      top:document.getElementById('v2ReportDlg').getBoundingClientRect().top,viewport:innerHeight}));
    assert.equal(reportLayout.modal,false);
    assert(reportLayout.top>0 && reportLayout.width<=360 && reportLayout.top<reportLayout.viewport/2);
    await page.locator('#v2MapSpace').click();
    assert.equal(await page.locator('#v2MapSpace').getAttribute('aria-pressed'),'true');
    assert((await page.locator('#v2ReportDlg').evaluate(el=>el.getBoundingClientRect().height))<reportLayout.viewport/2);
    await page.locator('#v2MapSpace').click();
    const fieldLinks=await page.locator('#v2ReportBody .v2-field-link').evaluateAll(links=>links.map(link=>({
      text:link.textContent,href:link.href,target:link.target,rel:link.rel,referrer:link.referrerPolicy})));
    assert.equal(fieldLinks.length,4);
    assert(fieldLinks.every(link=>link.target==='_blank' && link.rel.includes('noopener') && link.referrer==='no-referrer'));
    assert(fieldLinks[0].href.startsWith('https://search.naver.com/search.naver?query='));
    assert(fieldLinks[1].href.startsWith('https://map.naver.com/p/search/'));
    assert(fieldLinks[2].href.startsWith('https://hogangnono.com/search?q='));
    assert.equal(fieldLinks[3].href,'https://www.asil.kr/asil/index.jsp');
    // Unified sale controls and real list DOM; map tiles/network remain stubbed.
    await page.evaluate(async()=>{
      mapSplit=(listId,mapId,items,opt)=>{
        _ms[mapId]={items,opt,listId,listLimit:60,openGroupKeys:new Set(),markers:{},coords:{},map:{invalidateSize(){}}};
        renderMsList(mapId,items.map((_,i)=>i));
      };
      openTypedList('매매'); await loadAllListings();
      _watchKeys=new Set(['찐매물:merged-1']);
      _mtBuyOnly=false; _focusRegion=null; _mtGradeSet=new Set(['A','B','C','D','E']);
      const base={지역:'테스트구',시도:'서울',지역코드:'11140',지역식별상태:'matched',시그널:'HELD',총액:50000,기회도:0,
        price_comparison:{상태:'관측비교',호가차이율:0,표본수:3,입력층:8},
        ref:{전용면적:59,층:8},price_kind:'asking',budget_fit:{status:'unknown',reason:'가정 확인'}};
      _laApplyResponse(_SALE_TYPES.join(','),{asof:'2026-09-28',meta:{private_access:true,duplicate_records_collapsed:1},listings:[
        {...base,key:'급매:merged-1',유형:'급매',단지명:'통합인증단지',source:'baroezip',
          listing_aliases:['급매:merged-1','찐매물:merged-1'],supplier_flags:['urgent','certified']},
        {...base,key:'일반매물:normal-1',유형:'일반매물',단지명:'통합일반단지',동:'상계동',source:'hanbang',supplier_flags:[]},
      ]});
    });
    assert.equal(await page.locator('#laList .ms-row').count(),2);
    assert.equal(await page.locator('#laList .ms-row .badge').first().textContent(),'지역 신호 · 판단 보류');
    assert.match(await page.locator('#laList .ms-row .badge').first().getAttribute('title'),/매물의 호가 평가나 매수 권고가 아닙니다/);
    const scopedSignals=await page.evaluate(()=>{
      const original=mapSplit, originalSignal=safeMarketSignal, oldPresale=_presaleList;
      const oldBuyOnly=_mtBuyOnly, oldGrades=_mtGradeSet;
      const seen={};
      try{
        mapSplit=(listId,_mapId,rows,opt)=>{seen[listId]={count:rows.length,html:rows[0]?opt.summary(rows[0]).nm:''};};
        safeMarketSignal=()=> 'STRONG_BUY'; // 동명 지역 조회는 매수여도, 서버가 검증한 보류를 표시해야 한다.
        _presaleList=[{단지명:'동명이인 청약',지역:'강서구',시그널:'HELD',상태:'접수예정'}];
        renderPresale();
        renderAuction([{단지명:'동명이인 경매',region:'강서구',지역시그널:'HELD',입찰상태:'no_bid'}]);
        seen.default={presale:seen.psList,auction:seen.auctionList};
        _mtBuyOnly=true;
        renderPresale();
        renderAuction([{단지명:'동명이인 경매',region:'강서구',지역시그널:'HELD',입찰상태:'no_bid'}]);
        seen.buyOnly={presale:seen.psList.count,auction:seen.auctionList.count};
        _mtBuyOnly=false;_mtGradeSet=new Set(['A']);
        renderPresale();
        renderAuction([{단지명:'동명이인 경매',region:'강서구',지역시그널:'HELD',입찰상태:'no_bid'}]);
        seen.gradeOnly={presale:seen.psList.count,auction:seen.auctionList.count};
        return seen;
      }finally{mapSplit=original;safeMarketSignal=originalSignal;_presaleList=oldPresale;
        _mtBuyOnly=oldBuyOnly;_mtGradeSet=oldGrades;}
    });
    assert.match(scopedSignals.default.presale.html,/지역 신호 · 판단 보류/);
    assert.match(scopedSignals.default.auction.html,/지역 신호 · 판단 보류/);
    assert.deepEqual(scopedSignals.buyOnly,{presale:0,auction:0});
    assert.deepEqual(scopedSignals.gradeOnly,{presale:0,auction:0});
    assert.equal(await page.locator('#psShowPast').isChecked(),false);
    const presaleLifecycle=await page.evaluate(()=>{
      const original=mapSplit, oldList=_presaleList, oldBuyOnly=_mtBuyOnly, oldGrades=_mtGradeSet, oldFocus=_focusRegion;
      const toggle=document.getElementById('psShowPast'), seen=[];
      try{
        mapSplit=(listId,_mapId,rows)=>{if(listId==='psList') seen.push(rows.map(row=>row['상태']));};
        _presaleList=['완료','접수예정','계약중','접수중','발표대기','공고'].map(status=>({
          단지명:status,지역:'테스트구',상태:status,시그널:'HELD'}));
        _mtBuyOnly=false; _mtGradeSet=new Set(['A','B','C','D','E']); _focusRegion=null;
        renderPresale();
        toggle.checked=true; toggle.dispatchEvent(new Event('change'));
        return seen;
      }finally{
        mapSplit=original; _presaleList=oldList; _mtBuyOnly=oldBuyOnly;
        _mtGradeSet=oldGrades; _focusRegion=oldFocus; toggle.checked=false;
      }
    });
    assert.deepEqual(presaleLifecycle,[
      ['접수중','접수예정'],
      ['접수중','접수예정','발표대기','계약중','완료','공고']
    ]);
    assert.match(await page.locator('#laList').textContent(),/전용 59㎡/);
    assert.match(await page.locator('#laList').textContent(),/호가 5.0억.*전용 59㎡/);
    assert.match(await page.locator('#laList').textContent(),/서울 테스트구 상계동/);
    assert.doesNotMatch(await page.locator('#laList').textContent(),/바로이집|공급사 인증 표시/);
    assert.match(await page.locator('#laList').textContent(),/같은 전용면적·인근 층 실거래 중앙값 대비 차이 없음/);
    assert.equal(await page.locator('#laTypeChips').getByRole('button',{name:'매매',exact:true}).getAttribute('aria-pressed'),'true');
    assert.equal(await page.locator('#laTypeChips').getByRole('button',{name:'찐매물',exact:true}).count(),0);
    await page.locator('#laMoreBtn').click();
    await page.locator('#laSaleFilter').selectOption('certified');
    assert.equal(await page.locator('#laList .ms-row').count(),1);
    assert.match(await page.locator('#laList').textContent(),/통합인증단지/);
    await page.locator('#laSaleFilter').selectOption('all');
    assert.equal(await page.locator('#laList .ms-row').count(),2);
    await page.evaluate(()=>{
      window.__uiWatchClicks=0;
      window.__watchToggleOriginal=toggleListingWatch;
      window.__focusPinOriginal=_focusPinOnly;
      toggleListingWatch=()=>{window.__uiWatchClicks++};
      _focusPinOnly=async()=>{};
    });
    const firstSale=page.locator('#laList .ms-row').first();
    await firstSale.locator('.watch-btn').click();
    assert.equal(await page.evaluate(()=>window.__uiWatchClicks),1);
    assert.equal(await firstSale.getAttribute('aria-expanded'),'false');
    await firstSale.locator('.nm').click();
    assert.equal(await firstSale.getAttribute('aria-expanded'),'true');
    assert.equal(await page.locator('#laList .ms-detail .la-detail-title').textContent(),'선택한 매물 확인');
    assert.equal(await page.locator('#laList .ms-detail .la-detail-actions button').count(),1);
    assert.equal(await page.locator('#laList .ms-detail .tx-costs').count(),0);
    assert.equal(calls.filter(x=>x==='/api/listing-costs').length,0);
    const watchLayout=await firstSale.evaluate(el=>({row:el.getBoundingClientRect().width,
      watch:el.querySelector('.la-watch').getBoundingClientRect().width,
      right:el.querySelector('.la-watch').getBoundingClientRect().right,
      rowRight:el.getBoundingClientRect().right}));
    assert(watchLayout.watch<watchLayout.row/2 && watchLayout.right<=watchLayout.rowRight,
      `watch action must not fill the list width: ${JSON.stringify(watchLayout)}`);
    assert.equal(await page.locator('#laList .la-address-edit').count(),0);
    assert.equal(calls.filter(x=>x==='/api/listing-locality').length,0);
    await page.locator('#laList .ms-detail .la-detail-actions button').click();
    await page.locator('#v2ReportBody').getByText('살 때 드는 돈은?',{exact:true}).waitFor();
    assert.equal(await page.locator('#v2ReportBody .tx-costs').evaluate(el=>el.open),false);
    assert.equal(calls.filter(x=>x==='/api/listing-costs').length,0);
    await page.locator('#v2ReportBody .tx-costs summary').click();
    await page.locator('#v2ReportBody .tx-costs summary').getByText(/취득세·총매입 보류/).waitFor();
    assert.equal(await page.locator('#v2ReportBody .tx-costs .tx-row').count(),4);
    assert.doesNotMatch(await page.locator('#v2ReportBody .tx-costs .tx-costs-body').textContent(),/취득세\s*500만|총매입가\s*51,600만|부대 합계\s*1,600만/);
    assert.match(await page.locator('#v2ReportBody .tx-costs .tx-costs-body').textContent(),/현재 세율·규제 적용 또는 주택수를 검증하지 못해/);
    assert.match(await page.locator('#v2ReportBody .tx-costs .tx-note').last().textContent(),/세율·규제 최신성 미검증.*주택수 미입력/);
    assert.doesNotMatch(await page.locator('#v2ReportBody .tx-costs .tx-note').last().textContent(),/감면 반영|규제 기준일|생애최초/);
    assert.equal(calls.filter(x=>x==='/api/listing-costs').length,1);
    await page.locator('#v2ReportDlg').getByRole('button',{name:'리포트 닫기'}).click();
    costsVerified=true;
    await page.evaluate(()=>SignalV2.openListing('일반매물:synthetic-hb-1'));
    await page.locator('#v2ReportBody .tx-costs summary').click();
    await page.locator('#v2ReportBody .tx-costs .tx-costs-body').getByText('총매입가', {exact:false}).waitFor();
    assert.match(await page.locator('#v2ReportBody .tx-costs summary').textContent(),/총매입/);
    assert.match(await page.locator('#v2ReportBody .tx-costs .tx-costs-body').textContent(),/취득세\s*500만/);
    assert.match(await page.locator('#v2ReportBody .tx-costs .tx-note').textContent(),/생애최초 취득세 감면/);
    await page.locator('#v2ReportDlg').getByRole('button',{name:'리포트 닫기'}).click();
    await page.evaluate(()=>SignalV2.openListing('일반매물:cost-held'));
    await page.locator('#v2ReportBody').getByText(/비용 추정을 보류합니다/).waitFor();
    assert.equal(await page.locator('#v2ReportBody .tx-costs').count(),0);
    await page.locator('#v2ReportDlg').getByRole('button',{name:'리포트 닫기'}).click();
    await firstSale.locator('.nm').click();
    assert.equal(await firstSale.getAttribute('aria-expanded'),'false');
    await page.evaluate(()=>{ toggleListingWatch=window.__watchToggleOriginal; _focusPinOnly=window.__focusPinOriginal; });
    for(const width of [180,360,390,1280]){
      await page.setViewportSize({width,height:800});
      const layout=await page.evaluate(()=>({viewport:innerWidth,document:document.documentElement.scrollWidth,
        list:document.getElementById('laList').scrollWidth,available:document.getElementById('laList').clientWidth}));
      assert(layout.document<=layout.viewport && layout.list<=layout.available,
        `unified sale list overflow ${width}: ${JSON.stringify(layout)}`);
    }
    await page.setViewportSize({width:360,height:800});
    await page.evaluate(()=>{
      window.__savedLaData=_laData;
      const first=_laData.find(x=>x.key==='급매:merged-1');
      const second={...first,key:'급매:merged-2',총액:80000,평형:34,
        ref:{...first.ref,전용면적:84,complex_no:'same-complex'}};
      _laData=[{...first,ref:{...first.ref,complex_no:'same-complex'}},second,
        _laData.find(x=>x.key==='일반매물:normal-1')];
      renderAllListings();
    });
    const groupedSale=page.locator('#laList .ms-group-head');
    const singleSale=page.locator('#laList > .ms-row');
    assert.equal(await groupedSale.count(),1);
    assert.equal(await singleSale.count(),1);
    assert.match(await groupedSale.textContent(),/통합인증단지.*2건.*호가 5\.0억~8\.0억.*전용 59~84㎡.*서울 테스트구/);
    assert.match(await singleSale.textContent(),/통합일반단지.*호가 5\.0억.*전용 59㎡.*서울 테스트구 상계동/);
    assert.equal(await groupedSale.getAttribute('aria-expanded'),'false');
    await groupedSale.click();
    assert.equal(await groupedSale.getAttribute('aria-expanded'),'true');
    assert.equal(await page.locator('#laList .ms-group-body .ms-row:visible').count(),2);
    await groupedSale.click();
    assert.equal(await page.locator('#laList .ms-group-body .ms-row:visible').count(),0);
    const groupedLayout=await groupedSale.evaluate(el=>({groupWidth:el.getBoundingClientRect().width,
      parentWidth:el.parentElement.parentElement.getBoundingClientRect().width,
      scrollWidth:el.scrollWidth,clientWidth:el.clientWidth}));
    assert(groupedLayout.groupWidth<=groupedLayout.parentWidth && groupedLayout.scrollWidth<=groupedLayout.clientWidth,
      `group summary must fit the list: ${JSON.stringify(groupedLayout)}`);
    await page.evaluate(()=>{_laData=window.__savedLaData;renderAllListings();});
    assert.equal(nickPayloads.length,0);
    assert.equal(explanationPayloads.length,0);
    await page.evaluate(()=>SignalV2.openListing('일반매물:synthetic-hb-1'));
    await page.locator('#v2ReportBody').getByText('핵심 판단 빠르게 확인',{exact:true}).click();
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
    await page.locator('#v2ReportBody').getByRole('button',{name:'입지 다시 확인'}).click();
    await page.locator('#v2LocationResult').getByText(/테스트역 · 도보 약 8분/).waitFor();
    assert.match(await page.locator('#v2LocationResult').textContent(),/통학구역 후보 · 테스트초/);
    assert.match(await page.locator('#v2LocationResult').textContent(),/보행 경로 620m/);
    assert.match(await page.locator('#v2LocationResult').textContent(),/저장된 직장 위치가 없어 계산하지 않았어요/);
    assert.equal(await page.locator('#v2WorkSetup').isVisible(),true);
    assert.equal(calls.filter(path=>path==='/api/listing-location').length,locationCalls+1);
    assert.equal(nickPayloads.length,0);
    await page.locator('#v2ReportBody').getByText('관심 기록·비교·리포트 저장',{exact:true}).click();
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
    await page.locator('#v2ReportBody').getByText('관심 기록·비교·리포트 저장',{exact:true}).click();
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
    await page.locator('#v2ReportBody').getByRole('button',{name:'입지 다시 확인'}).click();
    await page.locator('#v2LocationResult').getByText(/자료를 불러오지 못했습니다/).waitFor();
    assert.match(await page.locator('#v2ReportBody').textContent(),/국토부 동일 조건 거래/);
    await page.evaluate(()=>SignalV2.openListing('일반매물:profile-fail'));
    await page.locator('#v2ReportBody').getByText(/내 예산 정보를 지금 불러오지 못했어요/).waitFor();
    assert.match(await page.locator('#v2ReportBody').textContent(),/국토부 동일 조건 거래/);
    await page.evaluate(()=>SignalV2.openListing('일반매물:trade-missing',true));
    await page.waitForFunction(()=>!document.querySelector('#v2RefreshTrades'));
    assert.equal(calls.filter(path=>path==='/api/v2/listings/report-enrich').length,1);
    assert.equal(await page.locator('#v2BackToDiscovery').isVisible(),true);
    await page.evaluate(()=>SignalV2.openListing('일반매물:location-slow'));
    await page.locator('#v2ReportBody').getByRole('button',{name:'입지 다시 확인'}).click();
    await page.evaluate(()=>SignalV2.openListing('일반매물:synthetic-hb-2'));
    await page.locator('#v2ReportBody').getByRole('button',{name:'입지 다시 확인'}).waitFor();
    await page.waitForTimeout(220);
    assert.match(await page.locator('#v2LocationResult').textContent(),/표시 좌표가 없어/);
    await page.locator('#v2ReportDlg').getByRole('button',{name:'리포트 닫기'}).click();
    const beforeAutoLocation=calls.filter(path=>path==='/api/listing-location').length;
    await page.evaluate(()=>SignalV2.openListing('일반매물:location-auto'));
    await page.locator('#v2LocationResult').getByText(/테스트역 · 도보 약 8분/).waitFor();
    assert.equal(calls.filter(path=>path==='/api/listing-location').length,beforeAutoLocation+1);
    await page.locator('#v2ReportDlg').getByRole('button',{name:'리포트 닫기'}).click();
    await page.evaluate(()=>SignalV2.openListing('급매:naver-match'));
    await page.locator('#v2ReportBody .v2-field-link').first().waitFor();
    assert.equal(await page.locator('#v2ReportBody .v2-field-link').first().getAttribute('href'),
      'https://new.land.naver.com/complexes/12345');
    assert.match(await page.locator('#v2ReportBody .v2-report-section').first().textContent(),/5.2% 낮아요/);
    assert.match(await page.locator('#v2ReportBody .v2-report-section').first().textContent(),/3,000만 원 남습니다/);
    await page.locator('#v2ReportBody .v2-report-evidence > summary').click();
    const unifiedEvidence=await page.locator('#v2ReportBody .v2-report-more').first().textContent();
    assert.match(unifiedEvidence,/국토부 동일 조건 거래/);
    assert.match(unifiedEvidence,/중앙값5\.20억/);
    assert.match(unifiedEvidence,/같은 공급사의 현재 수집 호가 4건/);
    assert.match(unifiedEvidence,/같은 원천 매물의 수집 호가 변화/);
    assert.match(unifiedEvidence,/단지 참고 신호 · 판단 보류/);
    assert.match(unifiedEvidence,/지역 신호 매수와 별개/);
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#v2ReportDlg').evaluate(el=>el.open),false);
    const nickCallsBeforeLegacyReport=nickPayloads.length;
    await page.evaluate(()=>openListingReport('일반매물:synthetic-hb-1'));
    await page.locator('#advReport').getByText('한방테스트단지',{exact:true}).waitFor();
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
      _ms.hbMap={map:{once(_event,fn){window.__pickEntrance=fn;},off(){},setView(){},invalidateSize(){}}};
    });
    await page.getByRole('button',{name:'지도에서 출입구 후보 지정'}).click();
    assert.equal(await page.locator('#advPanel').evaluate(el=>el.style.display),'none');
    assert.equal(await page.locator('#analysisPickCancel').evaluate(el=>el.style.display),'block');
    page.once('dialog',dlg=>dlg.accept());
    await page.evaluate(()=>window.__pickEntrance({latlng:{lat:37.6505,lng:127.0705}}));
    await page.getByText('내가 지도에서 지정한 출입구 후보 기준입니다.',{exact:false}).waitFor();
    assert.equal(await page.locator('#advPanel').evaluate(el=>el.style.display),'flex');
    assert.equal(await page.locator('#analysisPickCancel').evaluate(el=>el.style.display),'none');
    await page.getByRole('button',{name:'내 지정 지우기'}).click();
    await page.getByRole('button',{name:'지도에서 출입구 후보 지정',exact:true}).waitFor();
    assert.equal(await page.locator('#advPanel').evaluate(el=>el.classList.contains('adv-report-mode')),true);
    assert.equal(nickPayloads.length,nickCallsBeforeLegacyReport);
    assert.equal(await page.locator('#advFab,#advChatTab,#advInput,#advSendBtn').count(),0);
    await page.getByRole('button',{name:'＋ 비교함 담기'}).click();
    await page.evaluate(()=>openListingReport('일반매물:synthetic-hb-2'));
    await page.getByText('두번째테스트단지',{exact:true}).last().waitFor();
    assert.equal(nickPayloads.length,nickCallsBeforeLegacyReport);
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
    assert.equal(await page.locator('#listingCompareDlg').getByRole('button',{name:/닉에게|Nick/}).count(),0);
    assert.equal(nickPayloads.length,0);
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
      document.getElementById('search').value='';
      document.getElementById('groupFilter').value='';
      _filtersOpen=false; _otherSignalRegionsExpanded=false;
      _favs=new Set(['region:노원구']);
      _favRegionKeyByName=new Map([['노원구','kb:1135000000']]);
      allSignals=[
        {region:'노원구',group:'서울',region_id:'kb:1135000000',display_signal:'BUY',assessment_status:'ready'},
        {region:'강북구',group:'서울',region_id:'kb:1130500000',display_signal:'STRONG_BUY',assessment_status:'ready'},
      ];
      renderList();
    });
    assert.equal(await page.locator('#list details.signal-others').count(),1);
    assert.equal(await page.locator('#list details.signal-others').getAttribute('open'),null);
    assert.match(await page.locator('#list details.signal-others summary').textContent(),/그 밖의 지역 1곳/);
    await page.evaluate(()=>document.querySelector('#list details.signal-others summary').click());
    await page.waitForFunction(()=>_otherSignalRegionsExpanded);
    await page.evaluate(()=>renderList());
    assert.equal(await page.locator('#list details.signal-others').getAttribute('open'),'');
    await page.evaluate(()=>{ document.getElementById('search').value='없는지역'; renderList(); });
    assert.match(await page.locator('#list').textContent(),/조건에 맞는 지역이 없습니다/);
    await page.evaluate(()=>{
      document.getElementById('search').value='';
      document.getElementById('advPanel').style.display='none';
      document.querySelectorAll('dialog[open]').forEach(dialog=>dialog.close());
      meta={last_date:'2026-09-28',zones:{jeonse_supply:[],buyer_idx_strong:70,buyer_demand_buy:20}};
      allSignals=[{region:'테스트구',region_id:'kb:1114000000',group:'서울',signal:'BUY',display_signal:'BUY',assessment_status:'ready',급지:'B',전세수급:180,매수우위지수:68}];
      switchTab('signal'); renderList(); selectRegion('테스트구');
    });
    await page.getByText('지역 신호만 보여 줍니다.',{exact:false}).waitFor();
    assert.equal(await page.locator('.signal-evidence-details').getAttribute('open'),null);
    assert.equal(await page.locator('.signal-evidence-details .v2-row').first().isVisible(),false);
    assert.equal(await page.locator('.signal-assessment-highlight').count(),2);
    assert.match(await page.locator('.signal-assessment-highlights').textContent(),/전세수급 압력/);
    assert.match(await page.locator('.signal-assessment-highlights').textContent(),/매수심리 관찰선 미충족/);
    assert.match(await page.locator('.signal-assessment-highlights').textContent(),/이전 66/);
    const mobileCard=await page.locator('.signal-assessment').boundingBox();
    const mobilePanel=await page.locator('#haesolPanel').evaluate(el=>({width:el.clientWidth,scrollWidth:el.scrollWidth}));
    assert(mobileCard.width<=mobilePanel.width);
    assert(mobilePanel.scrollWidth<=mobilePanel.width);
    await page.setViewportSize({width:1280,height:900});
    const desktopCard=await page.locator('.signal-assessment').boundingBox();
    const desktopColumns=await page.locator('.signal-assessment-highlights').evaluate(el=>getComputedStyle(el).gridTemplateColumns.split(' ').length);
    assert(desktopCard.width>800);
    assert.equal(desktopColumns,2);
    await page.setViewportSize({width:360,height:800});
    await page.setViewportSize({width:180,height:800});
    const zoomCard=await page.locator('.signal-assessment').evaluate(el=>({width:el.clientWidth,scrollWidth:el.scrollWidth}));
    const zoomPanel=await page.locator('#haesolPanel').evaluate(el=>({width:el.clientWidth,scrollWidth:el.scrollWidth}));
    assert(zoomCard.scrollWidth<=zoomCard.width,`200% reflow summary card overflows: ${JSON.stringify(zoomCard)}`);
    assert(zoomPanel.scrollWidth<=zoomPanel.width,`200% reflow signal summary panel overflows: ${JSON.stringify(zoomPanel)}`);
    await page.setViewportSize({width:360,height:800});
    await page.locator('.signal-evidence-details > summary').click();
    assert.match(await page.locator('#haesolPanel').textContent(),/이전 발행 66/);
    const explanationCallsBeforeRegion=explanationPayloads.length;
    await page.locator('#v2RegionCounter').click();
    await page.locator('#v2RegionExplanation').getByText(/현재 판정에 반대되는 근거/).waitFor();
    assert.match(await page.locator('#v2RegionExplanation').textContent(),/검증된 추가 설명 대신 현재 리포트의 근거만 정리/);
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
    regionReportFailures=1;
    await page.evaluate(()=>SignalV2.paintRegion('테스트구','kb:1114000000'));
    await page.locator('#v2RegionRetry').waitFor();
    assert.match(await page.locator('#haesolPanel').textContent(),/등급만으로 매수 판단하지 마세요/);
    assert.doesNotMatch(await page.locator('#haesolPanel').textContent(),/판정 근거를 확인하고 있습니다/);
    await page.locator('#v2RegionRetry').focus();
    await page.keyboard.press('Enter');
    await page.locator('.signal-assessment').waitFor();
    await page.evaluate(async()=>{
      const original=window.fetch;
      const held={type:'region',asof:'2026-09-28',subject:{region:'테스트구',region_id:'kb:1114000000'},
        assessment:{assessment_status:'held',raw_grade:'BUY',
          summary:'행정구역 개편 전 자료라 현재 판정을 보류합니다.',scope_note:'인천 권역 자료',
          change:{type:'method_change',changed_reasons:[]},reasons:[]},
        positive:[{reason_id:'jeonse_pressure',label:'전세수급 압력',value:183.9,
          threshold:170,unit:'지수',role:'driver',passing:true}],cautions:[],
        unknowns:['sale_weeks_incomplete','region_boundary_obsolete','__proto__']};
      window.fetch=(input,...args)=>String(input).startsWith('/api/v2/regions/')
        ? Promise.resolve(new Response(JSON.stringify(held),{status:200,headers:{'Content-Type':'application/json'}}))
        : original(input,...args);
      try{await SignalV2.paintRegion('테스트구','kb:1114000000');}
      finally{window.fetch=original;}
    });
    const heldHero=await page.locator('.signal-assessment-highlights').textContent();
    assert.match(heldHero,/판단 보류 이유/);
    assert.match(heldHero,/최근 주간 가격 4주가 이어지지 않습니다/);
    assert.match(heldHero,/행정구역 개편 전 자료라 현재 지역의 매수·매도 판정에 사용할 수 없습니다/);
    assert.match(heldHero,/그 밖의 사유 1건/);
    assert.doesNotMatch(heldHero,/전세수급 압력|미확인 자료/);
    for(const width of [180,360]){
      await page.setViewportSize({width,height:800});
      const heldWidth=await page.locator('.signal-assessment').evaluate(el=>({scroll:el.scrollWidth,client:el.clientWidth}));
      assert(heldWidth.scroll<=heldWidth.client,`held summary overflows at ${width}px: ${JSON.stringify(heldWidth)}`);
    }
    await page.locator('.signal-evidence-details > summary').click();
    const heldReason=await page.locator('.signal-evidence-details').textContent();
    assert.match(heldReason,/행정구역 개편 전 자료라 현재 지역의 매수·매도 판정에 사용할 수 없습니다/);
    assert.match(heldReason,/기본 규칙의 충족 조건 · 현재 판정 아님/);
    assert.match(heldReason,/전세수급 압력/);
    assert.match(heldReason,/확인되지 않은 보류 사유가 있습니다/);
    assert.doesNotMatch(heldReason,/region_boundary_obsolete|__proto__|\[object Object\]/);
    await page.locator('.signal-evidence-details').getByText('기존 규칙 산출값').click();
    assert.match(await page.locator('.signal-evidence-details').textContent(),/매수 · 검증되지 않아 현재 판정으로 쓰지 않습니다/);
    assert.doesNotMatch(await page.locator('.signal-evidence-details').textContent(),/\bBUY\b/);
    await page.evaluate(()=>SignalV2.paintRegion('테스트구','kb:1114000000'));
    await page.locator('.signal-assessment').waitFor();
    seriesFailures=1;
    await page.evaluate(()=>{_signalTrendRegion=null;_lastM=null;loadSignalTrend('테스트구');});
    await page.getByRole('button',{name:'추세 다시 확인'}).waitFor();
    assert.match(await page.locator('#signalTrendStatus').textContent(),/등급만으로 판단하지 말고/);
    await page.getByRole('button',{name:'추세 다시 확인'}).focus();
    await page.keyboard.press('Enter');
    await page.waitForFunction(()=>document.getElementById('signalTrendStatus').textContent==='');
    const priorPageErrors=errors.length;
    await page.evaluate(()=>{loadSignalTrend('테스트구');_lastM=null;++_signalTrendGen;});
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    assert.equal(errors.length,priorPageErrors,'cancelled cached redraw must not read cleared metrics');
    await page.evaluate(()=>loadSignalTrend('테스트구'));
    await page.waitForFunction(()=>document.getElementById('signalTrendStatus').textContent==='');
    assert(calls.some(x=>decodeURIComponent(x)==='/api/v2/regions/kb:1114000000/report'));
    assert.equal(await page.locator('#signalPanelTrend').isVisible(),true);
    assert.equal(await page.locator('#signalPanelMap').count(),0);
    assert.equal(await page.getByRole('tab',{name:'급지 지도'}).count(),0);
    assert.equal(await page.locator('.signal-assessment').count(),1);
    assert.equal(await page.locator('.signal-evidence-details').getAttribute('open'),null);
    assert.equal(await page.locator('#haesolPanel').evaluate(el=>getComputedStyle(el).maxHeight),'none');
    assert.equal(calls.some(x=>decodeURIComponent(x)==='/api/series/테스트구'),true);
    assert.equal(await page.locator('#signalSide').evaluate(el=>el.inert),true);
    assert.equal(await page.locator('#sideOpenToggle').isVisible(),true);
    assert.equal(await page.locator('#sideOpenToggle').getAttribute('aria-expanded'),'false');
    await page.locator('#sideOpenToggle').click();
    assert.equal(await page.locator('#signalSide').evaluate(el=>el.inert),false);
    assert.equal(await page.locator('#sideToggle').getAttribute('aria-expanded'),'true');
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#signalSide').evaluate(el=>el.inert),true);
    await page.locator('#sideOpenToggle').click();
    await page.locator('#signalSide #list .row').first().click();
    assert.equal(await page.locator('#signalSide').evaluate(el=>el.inert),true);
    assert.equal(await page.locator('#sigbadge').evaluate(el=>el===document.activeElement),true);
    const seriesCountBeforeTab=calls.filter(x=>decodeURIComponent(x)==='/api/series/테스트구').length;
    await page.evaluate(()=>setSignalView('trend'));
    await page.waitForFunction(()=>document.getElementById('signalTrendStatus').textContent==='');
    assert.equal(calls.filter(x=>decodeURIComponent(x)==='/api/series/테스트구').length,seriesCountBeforeTab);
    await page.locator('.signal-evidence-details > summary').click();
    assert.match(await page.locator('#haesolPanel').textContent(),/이전 발행 66/);
    await page.setViewportSize({width:1280,height:800});
    assert.equal(await page.locator('#signalSide').isVisible(),true);
    await page.waitForFunction(()=>!document.getElementById('signalSide').inert);
    const regionAction=page.locator('#signalSide #list .row-select').first();
    assert.equal(await regionAction.getAttribute('type'),'button');
    assert.equal(await regionAction.locator('.fav').count(),0,'favorite action stays separate from region selection');
    await page.evaluate(()=>{_filtersOpen=false;document.getElementById('filterPanel').style.display='none';updateFilterToggleLabel();});
    await page.locator('#filterToggle').focus();
    await page.keyboard.press('Tab');
    assert.equal(await regionAction.evaluate(el=>el===document.activeElement),true,
      'Tab from the filter reaches region selection before the favorite button');
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('#sigbadge').evaluate(el=>el===document.activeElement),true,
      'keyboard region selection moves focus to the new report heading');
    await page.evaluate(()=>renderFilters());
    await page.locator('#filterToggle').click();
    assert.equal(await page.locator('#filterToggle').getAttribute('aria-expanded'),'true');
    const buyChip=page.locator('#filters button[data-signal="BUY"]');
    const beforeChip=await buyChip.getAttribute('aria-pressed');
    await buyChip.focus();
    await page.keyboard.press('Space');
    assert.notEqual(await buyChip.getAttribute('aria-pressed'),beforeChip);
    assert.equal(await buyChip.evaluate(el=>el===document.activeElement),true,
      'keyboard focus remains on the recreated filter button');
    await page.keyboard.press('Space');
    assert.equal(await buyChip.getAttribute('aria-pressed'),beforeChip);
    await page.locator('#filterToggle').click();
    assert.equal(await page.locator('#filterToggle').getAttribute('aria-expanded'),'false');
    await page.evaluate(()=>SignalV2.openListing('일반매물:synthetic-hb-1'));
    await page.locator('#v2ReportBody .v2-field-link').first().waitFor();
    const desktopReport=await page.locator('#v2ReportDlg').evaluate(el=>({left:el.getBoundingClientRect().left,
      right:el.getBoundingClientRect().right,modal:el.matches(':modal')}));
    assert.equal(desktopReport.modal,false);
    assert.equal(desktopReport.left,0);
    assert(desktopReport.right<640,`map side should remain visible: ${JSON.stringify(desktopReport)}`);
    await page.locator('#v2ReportDlg').getByRole('button',{name:'리포트 닫기'}).click();
    await page.setViewportSize({width:390,height:800});
    await page.waitForFunction(()=>document.getElementById('signalSide').inert);
    assert((await page.evaluate(()=>document.documentElement.scrollWidth))<=390);
    assert((await page.locator('#signalPanelTrend').evaluate(el=>el.getBoundingClientRect().bottom))<=801);
    await page.setViewportSize({width:360,height:800});
    await page.waitForFunction(()=>document.getElementById('signalSide').inert);
    assert.equal(await page.locator('#signalSide').evaluate(el=>el.inert),true);
    assert((await page.locator('#signalPanelTrend').evaluate(el=>el.getBoundingClientRect().bottom))<=801);
    const reportWidth=await page.evaluate(()=>document.documentElement.scrollWidth);
    assert(reportWidth<=360,`analysis panel overflows mobile viewport: ${reportWidth}`);
    await page.evaluate(()=>{_listingCompareSave([]);SignalV2.openDiscovery('테스트구');});
    assert.equal(await page.locator('#v2DiscoverCompare').isVisible(),false);
    assert.equal(await page.locator('#v2DiscoverExtra').evaluate(el=>el.open),false);
    assert.equal(await page.locator('#v2DiscoverForm [name=min_area_m2]').isVisible(),false);
    await page.waitForFunction(()=>document.querySelector('#v2DiscoverForm [name=region_code]')?.value==='11140');
    const maxPriceInput=page.locator('#v2DiscoverForm [name=max_price_manwon]');
    await maxPriceInput.fill('60000');
    assert.equal(await maxPriceInput.evaluate(el=>el.checkValidity()),true,'the example price must satisfy native validation');
    await page.locator('#v2DiscoverForm').getByRole('button',{name:'후보 찾기'}).focus();
    await page.keyboard.press('Enter');
    await page.waitForFunction(()=>document.getElementById('v2DiscoverResults').textContent.includes('첫번째 후보'));
    assert.equal(discoveryPayloads.at(-1).max_price_manwon,60000);
    await maxPriceInput.fill('');
    await page.locator('#v2DiscoverForm').getByRole('button',{name:'후보 찾기'}).click();
    assert.equal(discoveryPayloads.at(-1).prefer_region_code,'11140');
    await page.getByText('첫번째 후보').waitFor();
    assert.match(await page.locator('#v2DiscoverResults').textContent(),/일부 원천이 실패·제한/);
    assert.equal(await page.locator('#v2DiscoverResults [data-v2-card]').count(),1);
    assert.match(await page.locator('#v2DiscoverResults [data-v2-card]').first().textContent(),/전용 59㎡ · 방 3개/);
    assert.match(await page.locator('#v2DiscoverResults [data-v2-card]').first().textContent(),/조건 부합 근거/);
    assert.equal(await page.locator('#v2DiscoverResults [data-v2-listing]').first().evaluate(el=>el.classList.contains('primary')),true);
    const firstWatch=page.locator('#v2DiscoverResults [data-watch-key="일반매물:synthetic-1"]');
    await firstWatch.click();
    await page.waitForFunction(()=>document.querySelector('#v2DiscoverResults [data-watch-key="일반매물:synthetic-1"]')?.getAttribute('aria-pressed')==='true');
    assert(watched.has('일반매물:synthetic-1'));
    await page.locator('#v2DiscoverResults [data-v2-next]').click();
    await page.getByText('두번째 후보').waitFor();
    assert.equal(await page.locator('#v2DiscoverResults [data-v2-card]').count(),2);
    assert.equal(await page.locator('#v2DiscoverResults [data-v2-next]').count(),0);
    await page.locator('#v2DiscoverResults [data-v2-compare="일반매물:synthetic-1"]').click();
    assert.equal(await page.locator('#v2DiscoverCompare').isVisible(),false);
    await page.locator('#v2DiscoverResults [data-v2-compare="일반매물:synthetic-2"]').click();
    const discoverCompare=page.getByRole('button',{name:'선택 매물 2개 비교하기'});
    await discoverCompare.waitFor();
    await discoverCompare.click();
    await page.getByText('같은 면적 국토부 실거래',{exact:false}).waitFor();
    assert.equal(await page.locator('#listingCompareBody thead th').count(),3);
    assert.equal(await page.locator('#v2DiscoverDlg').evaluate(el=>el.open),true);
    assert.equal(await page.locator('#v2DiscoverResults [data-v2-card]').count(),2);
    await page.locator('#listingCompareDlg').getByRole('button',{name:'비교 닫기'}).click();
    assert.equal(await page.locator('#v2DiscoverDlg').evaluate(el=>el.open),true);
    assert.equal(await page.locator('#v2DiscoverResults [data-v2-card]').count(),2);
    assert.equal(await page.locator('#v2DiscoverCompare').isVisible(),true);
    await discoverCompare.click();
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#listingCompareDlg').evaluate(el=>el.open),false);
    assert.equal(await page.locator('#v2DiscoverDlg').evaluate(el=>el.open),true);
    assert.equal(await page.locator('#v2DiscoverResults [data-v2-card]').count(),2);
    await page.locator('#v2DiscoverForm').getByRole('button',{name:'후보 찾기'}).click();
    await page.getByText('첫번째 후보').waitFor();
    await page.locator('#v2DiscoverResults [data-v2-listing="일반매물:synthetic-1"]').click();
    await page.locator('#v2ReportBody').getByText('관심 기록·비교·리포트 저장',{exact:true}).click();
    const reportWatch=page.locator('#v2ReportBody [data-watch-key="일반매물:synthetic-1"]');
    await reportWatch.waitFor();
    assert.equal(await reportWatch.getAttribute('aria-pressed'),'true');
    await reportWatch.click();
    await page.waitForFunction(()=>document.querySelector('#v2ReportBody [data-watch-key="일반매물:synthetic-1"]')?.getAttribute('aria-pressed')==='false');
    assert.equal(watched.has('일반매물:synthetic-1'),false);
    await page.setViewportSize({width:180,height:800});
    const reportTopWidth=await page.locator('#v2ReportDlg .v2-top').evaluate(el=>({scroll:el.scrollWidth,client:el.clientWidth}));
    assert(reportTopWidth.scroll<=reportTopWidth.client,`report return header overflows: ${JSON.stringify(reportTopWidth)}`);
    await page.setViewportSize({width:360,height:800});
    await page.locator('#v2BackToDiscovery').click();
    assert.equal(await page.locator('#v2ReportDlg').evaluate(el=>el.open),false);
    assert.equal(await page.locator('#v2DiscoverDlg').evaluate(el=>el.open),true);
    assert.equal(await page.locator('#v2DiscoverResults [data-v2-card]').count(),1);
    assert.equal(await page.locator('#v2DiscoverResults [data-v2-listing="일반매물:synthetic-1"]')
      .evaluate(el=>el===document.activeElement),true);
    assert.equal(await page.locator('#v2DiscoverResults [data-watch-key="일반매물:synthetic-1"]').getAttribute('aria-pressed'),'false');
    await page.locator('#v2DiscoverResults [data-v2-next]').click();
    await page.getByText('두번째 후보').waitFor();
    await page.locator('#v2DiscoverResults [data-v2-listing="일반매물:synthetic-2"]').click();
    await page.locator('#v2BackToDiscovery').click();
    assert.equal(await page.locator('#v2DiscoverResults [data-v2-card]').count(),2);
    await maxPriceInput.fill('61000');
    await page.locator('#v2DiscoverForm').getByRole('button',{name:'후보 찾기'}).click();
    const staleCard=page.locator('#v2DiscoverResults [data-v2-group="verify"] [data-v2-card]');
    await staleCard.waitFor();
    assert.match(await staleCard.textContent(),/지난 수집 호가 12\.00억 · 현재 호가 미확인/);
    assert.match(await staleCard.textContent(),/판정 보류 이유: 지난 수집 호가는 설정한 상한보다 높았습니다/);
    assert.match(await page.locator('#v2DiscoverResults').textContent(),/지난 호가가 예산을 넘는 후보는 뒤로/);
    await maxPriceInput.fill('62000');
    await page.locator('#v2DiscoverForm').getByRole('button',{name:'후보 찾기'}).click();
    const conflictCard=page.locator('#v2DiscoverResults [data-v2-group="verify"] [data-v2-card]');
    await conflictCard.waitFor();
    assert.match(await conflictCard.textContent(),/원천 정보 충돌 · 표시 호가 12\.00억 · 현재 호가 미확인/);
    assert.match(await conflictCard.textContent(),/판정 보류 이유: 같은 원천 ID/);
    assert.doesNotMatch(await conflictCard.textContent(),/양보할 점: 현재 알려진 조건/);
    assert.match(await conflictCard.textContent(),/판매 여부와 실제 호가/);
    await maxPriceInput.fill('');
    await page.locator('#v2DiscoverForm').getByRole('button',{name:'후보 찾기'}).click();
    await page.getByText('첫번째 후보').waitFor();
    await page.locator('#v2DiscoverExtra > summary').click();
    await page.locator('#v2DiscoverForm [name=min_rooms]').fill('16');
    await page.locator('#v2DiscoverExtra > summary').click();
    const beforeInvalid=discoveryPayloads.length;
    await page.locator('#v2DiscoverForm').getByRole('button',{name:'후보 찾기'}).click();
    assert.equal(discoveryPayloads.length,beforeInvalid,'hidden invalid criteria must not submit');
    assert.equal(await page.locator('#v2DiscoverExtra').evaluate(el=>el.open),true,'reveal the invalid extra criterion');
    await page.locator('#v2DiscoverForm [name=min_rooms]').fill('3');
    assert.match(await page.locator('#v2DiscoverExtraSummary').textContent(),/추가 조건 1개 적용/);
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
    await page.locator('#v2DiscoverForm .v2-preferences summary').first().click();
    await page.locator('#v2DiscoverForm [name=prefer_max_price_manwon]').fill('50000');
    await page.locator('#v2DiscoverForm [name=prefer_min_area_m2]').fill('84');
    await page.locator('#v2DiscoverForm').getByRole('button',{name:'후보 찾기'}).click();
    const preferenceDetail=page.locator('#v2DiscoverResults .v2-discovery-more').first();
    await preferenceDetail.getByText(/선호 기준 2\/3개 · 자료 2\/3개 확인/).waitFor();
    assert.equal(await preferenceDetail.getAttribute('open'),null);
    await preferenceDetail.locator('summary').click();
    assert.match(await preferenceDetail.textContent(),/부합: 선호 지역·선호 호가/);
    await page.locator('#v2DiscoverForm [name=priority]').selectOption('price');
    await page.locator('#v2DiscoverForm').getByRole('button',{name:'후보 찾기'}).click();
    const priorityDetail=page.locator('#v2DiscoverResults .v2-discovery-more').first();
    await priorityDetail.locator('summary').click();
    await page.getByText(/호가 우선\(2배\) · 적합도 75\/100 · 확인도 75\/100/).waitFor();
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
    await page.evaluate(()=>{
      window.__validationFetch=window.fetch;
      window.fetch=(input,...args)=>{
        const url=new URL(String(input),location.href);
        if(url.pathname==='/api/v2/decision-notes' && args[0]?.method==='POST')
          return Promise.resolve(new Response(JSON.stringify({detail:'관심 이유를 1~500자로 입력해 주세요.'}),
            {status:422,headers:{'Content-Type':'application/json'}}));
        if(url.pathname==='/api/v2/discovery' && args[0]?.method==='POST')
          return Promise.resolve(new Response(JSON.stringify({detail:'호가·면적·방 개수·통근시간 등 숫자 조건을 다시 확인해 주세요.'}),
            {status:422,headers:{'Content-Type':'application/json'}}));
        return window.__validationFetch(input,...args);
      };
      SignalV2.openNote('region','테스트구');
    });
    await page.locator('#v2NoteForm [name="thesis"]').fill('관심 이유');
    await page.locator('#v2NoteForm [name="counter_condition"]').fill('가격 하락');
    await page.locator('#v2NoteForm button[type="submit"]').click();
    await page.getByText('관심 이유를 1~500자로 입력해 주세요.',{exact:true}).waitFor();
    await page.evaluate(()=>{document.getElementById('v2NoteDlg').close();SignalV2.openDiscovery();});
    await page.locator('#v2DiscoverForm').getByRole('button',{name:'후보 찾기'}).click();
    await page.getByText('호가·면적·방 개수·통근시간 등 숫자 조건을 다시 확인해 주세요.',{exact:true}).waitFor();
    await page.evaluate(()=>{document.getElementById('v2DiscoverDlg').close();window.fetch=window.__validationFetch;
      delete window.__validationFetch;});
    for (const width of [390, 620, 1280]) {
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
    // 360 CSS px 화면을 200% 확대했을 때의 리플로우 폭을 근사한다.
    // OS/브라우저 실제 확대·스크린리더 실사용 검증을 대신하지는 않는다.
    await page.setViewportSize({width:180,height:400});
    await page.evaluate(()=>{document.getElementById('view-all').style.display='block';});
    const zoomListingMap=await page.evaluate(()=>({viewport:innerWidth,
      page:document.documentElement.scrollWidth,
      map:document.getElementById('laMap').getBoundingClientRect().width,
      available:document.getElementById('laMap').parentElement.getBoundingClientRect().width}));
    assert(zoomListingMap.page<=zoomListingMap.viewport,`200% reflow listing page overflows: ${JSON.stringify(zoomListingMap)}`);
    assert(zoomListingMap.map<=zoomListingMap.available,`200% reflow listing map overflows: ${JSON.stringify(zoomListingMap)}`);
    await page.evaluate(()=>{document.getElementById('view-all').style.display='none';});
    await page.evaluate(()=>SignalV2.openDiscovery());
    await page.locator('#v2DiscoverForm').getByRole('button',{name:'후보 찾기'}).click();
    await page.locator('#v2DiscoverResults [data-v2-card]').first().waitFor();
    const zoomLayout=await page.evaluate(()=>({viewport:innerWidth,
      page:document.documentElement.scrollWidth,
      dialog:document.getElementById('v2DiscoverDlg').getBoundingClientRect().width,
      form:document.getElementById('v2DiscoverForm').scrollWidth,
      formAvailable:document.getElementById('v2DiscoverForm').clientWidth,
      results:document.getElementById('v2DiscoverResults').scrollWidth,
      available:document.getElementById('v2DiscoverResults').clientWidth,
      overflowing:[...document.querySelectorAll('#v2DiscoverResults *')]
        .filter(el=>el.getBoundingClientRect().right>document.getElementById('v2DiscoverResults').getBoundingClientRect().right+1)
        .slice(0,5).map(el=>({tag:el.tagName,className:el.className,text:el.textContent.slice(0,35)}))}));
    assert(zoomLayout.page<=zoomLayout.viewport,`200% reflow page overflows: ${JSON.stringify(zoomLayout)}`);
    assert(zoomLayout.dialog<=zoomLayout.viewport,`200% reflow dialog overflows: ${JSON.stringify(zoomLayout)}`);
    assert(zoomLayout.form<=zoomLayout.formAvailable,`200% reflow form overflows: ${JSON.stringify(zoomLayout)}`);
    assert(zoomLayout.results<=zoomLayout.available,`200% reflow results overflow: ${JSON.stringify(zoomLayout)}`);
    await page.evaluate(()=>document.getElementById('v2DiscoverDlg').close());
    await page.evaluate(()=>SignalV2.openListing('일반매물:synthetic-hb-1'));
    await page.locator('#v2ReportBody .v2-report-evidence > summary').waitFor();
    const zoomReport=await page.evaluate(()=>({viewport:innerWidth,
      dialog:document.getElementById('v2ReportDlg').getBoundingClientRect().width,
      body:document.getElementById('v2ReportBody').scrollWidth,
      available:document.getElementById('v2ReportBody').clientWidth,
      overflowing:[...document.querySelectorAll('#v2ReportBody *')]
        .filter(el=>el.getBoundingClientRect().right>document.getElementById('v2ReportBody').getBoundingClientRect().right-18)
        .slice(0,5).map(el=>({tag:el.tagName,className:el.className,text:el.textContent.slice(0,35)}))}));
    assert(zoomReport.dialog<=zoomReport.viewport,`200% reflow report dialog overflows: ${JSON.stringify(zoomReport)}`);
    assert(zoomReport.body<=zoomReport.available,`200% reflow report body overflows: ${JSON.stringify(zoomReport)}`);
    await page.evaluate(()=>document.getElementById('v2ReportDlg').close());
    await page.setViewportSize({width:360,height:800});
    const rollout = await page.evaluate(async()=>{
      const original=window.openListingReport;
      window._featureFlags={report_v2_enabled:true,discovery_v2_enabled:true,
        contextual_explanations_enabled:false};
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
        nickAbsent:document.getElementById('advFab')===null};
      window.openListingReport=original;
      window._featureFlags={};
      delete window.__fallbackKey;
      return result;
    });
    assert.deepEqual(rollout,{aiHidden:true,fallbackKey:'일반매물:synthetic-hb-1',
      discoverClosed:true,reportPaused:true,nickAbsent:true});
    const homeSelection=await page.evaluate(async()=>{
      document.getElementById('mp_region').value='중구';
      await _mpRegionDo();
      const choices=document.querySelectorAll('#mpRegionRes [data-region-index]').length;
      document.querySelector('#mpRegionRes [data-region-index]').click();
      const selected={choices,name:document.getElementById('mp_region').value,code:_mpRegionCode,
        status:document.getElementById('mpRegionStatus').textContent};
      document.getElementById('mp_region').value='다른 지역';
      mpRegionSearch();
      selected.cleared=_mpRegionCode===null;
      clearTimeout(_mpRegionT);
      return selected;
    });
    assert.deepEqual(homeSelection,{choices:1,name:'중구',code:'kb:1114000000',
      status:'서울 · 중구 확인됨',cleared:true});
    const homeMap=await page.evaluate(async()=>{
      const originalFetch=window.fetch;
      window.fetch=(url,...args)=>String(url).startsWith('/api/region-centroids')
        ? Promise.resolve(new Response(JSON.stringify({
            centroids:{'kb:1114000000':[37.56,126.99],중구:[37.56,126.99]},
            identities:{'kb:1114000000':{name:'중구',sido:'서울',region_id:'kb:1114000000'}}
          }),{headers:{'Content-Type':'application/json'}})) : originalFetch(url,...args);
      window.L={divIcon:o=>o,layerGroup:()=>({addTo(){return this;},remove(){}}),
        marker:()=>({bindTooltip(){return this;},addTo(){return this;},on(){},setIcon(){}}),
        featureGroup:()=>({getBounds:()=>({pad(){return this;}})})};
      myMap={invalidateSize(){},fitBounds(){}};
      _favs=new Set(); _mySearchList=[];
      _profile={거주지:'중구',거주지코드:'kb:1114000000'};
      await drawMyMap();
      const verified=_myPts.filter(p=>p.kind==='home').length;
      _profile={거주지:'부산 중구',거주지코드:'kb:1114000000'};
      await drawMyMap();
      const mismatch=_myPts.filter(p=>p.kind==='home').length;
      _profile={거주지:'중구'};
      await drawMyMap();
      const legacy=_myPts.filter(p=>p.kind==='home').length;
      const hint=document.getElementById('myMapEmpty').textContent;
      _profile={}; _favs=new Set(['region:중구']);
      _favRegionKeyByName=new Map([['중구','kb:1114000000']]);
      await drawMyMap();
      const favorite=_myPts.filter(p=>p.kind==='region').length;
      window.fetch=originalFetch;
      return {verified,mismatch,legacy,favorite,hint};
    });
    assert.equal(homeMap.verified,1);
    assert.equal(homeMap.mismatch,0);
    assert.equal(homeMap.legacy,0);
    assert.equal(homeMap.favorite,1);
    assert.match(homeMap.hint,/주소 검색 결과를 다시 선택/);
    await page.evaluate(()=>{document.getElementById('view-mymap').style.display='block';});
    for(const width of [360,180]){
      await page.setViewportSize({width,height:800});
      const layout=await page.evaluate(()=>{
        const view=document.getElementById('view-mymap');
        const map=document.getElementById('myMap'), aside=map.nextElementSibling;
        const search=document.getElementById('mmSearch');
        return {viewport:innerWidth,page:document.documentElement.scrollWidth,
          available:view.getBoundingClientRect().right,
          map:map.getBoundingClientRect().right,aside:aside.getBoundingClientRect().right,
          search:search.getBoundingClientRect().right};
      });
      assert(layout.page<=layout.viewport,`my map page overflows at ${width}px: ${JSON.stringify(layout)}`);
      assert(layout.map<=layout.available && layout.aside<=layout.available && layout.search<=layout.available,
        `my map panel overflows at ${width}px: ${JSON.stringify(layout)}`);
    }
    await page.evaluate(()=>{document.getElementById('view-mymap').style.display='none';});
    const locationCopy=await page.evaluate(()=>{
      _imj={날짜:'2026-10-10',출발:'10:00',종료:'12:00',총소요:120,이동합:20,체류:50,
        집기준:true,stops:[],준비물:[]};
      _renderImjang();
      const course=document.getElementById('imjBody').textContent;
      const legend=document.querySelector('#view-mymap aside').textContent;
      return {course,legend};
    });
    assert.match(locationCopy.course,/거주지 시군구 중심 출발/);
    assert.match(locationCopy.course,/실제 집 위치.*달라질 수 있습니다/);
    assert.match(locationCopy.legend,/거주지 시군구 중심/);
    const stationOverlay=await page.evaluate(()=>{
      const oldL=window.L, oldMap=_ms.laMap;
      const drawn=[];
      window.L={
        polyline:(points,style)=>({points,style,getBounds:()=>({pad(){return this;}})}),
        circleMarker:point=>({point,bindTooltip(label){this.label=label;return this;}}),
        layerGroup:items=>({items,addTo(){drawn.push(this);return this;},remove(){this.removed=true;}}),
      };
      _ms.laMap={map:{addLayer(){},fitBounds(){this.focused=true;}}};
      const data={listing:{kind:'찐매물',coordinate:[37.65,127.07]},mobility:{
        station_point:{name:'테스트역',coordinate:[37.66,127.08],straight_distance_m:550},
        station_walk:{status:'observed',minutes:8,distance_m:620,
          path:[[37.65,127.07],[37.655,127.075],[37.66,127.08]]}}};
      const routed=showV2StationRoute(data,true);
      const route={points:drawn.at(-1).items[0].points.length,
        label:drawn.at(-1).items[1].label,focused:_ms.laMap.map.focused};
      data.mobility.station_walk={status:'unavailable'};
      const straight=showV2StationRoute(data);
      const fallback={dash:drawn.at(-1).items[0].style.dashArray,
        label:drawn.at(-1).items[1].label,previousRemoved:drawn[0].removed};
      data.mobility.station_point.name='<img src=x onerror=alert(1)>';
      showV2StationRoute(data);
      const escaped=drawn.at(-1).items[1].label;
      clearV2StationRoute();
      window.L=oldL; _ms.laMap=oldMap;
      return {routed,route,straight,fallback,escaped};
    });
    assert.deepEqual(stationOverlay,{routed:true,route:{points:3,label:'테스트역 · 도보 약 8분 · 경로 620m',focused:true},
      straight:true,fallback:{dash:'7 7',label:'테스트역 · 직선 연결 · 도보시간 미확인',previousRemoved:true},
      escaped:'&lt;img src=x onerror=alert(1)&gt; · 직선 연결 · 도보시간 미확인'});
    serverClientVersion='synthetic-v2';
    await page.evaluate(()=>{window._signalAppReady=true; return checkClientVersion(true);});
    assert.equal(await page.locator('#clientUpdateBanner').isVisible(),true);
    assert.equal(await page.getByRole('button',{name:'새 화면으로 갱신'}).isVisible(),true);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,
      'the deployment warning must not overflow a 180px reflow');
    assert.equal(nickPayloads.length,0);
    assert.deepEqual(errors,[]);
    console.log('PASS: Chromium 360/390/620/1280px and 180px reflow, candidate clarity, lazy fetch, history, loan rendering, keyboard toggle, A→B stale race, paged decision notes, quicksale evidence, listing reports and no Nick entry');
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
