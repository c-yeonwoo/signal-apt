const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../src/realty_signal/web/index.html'), 'utf8');
const signalV2 = fs.readFileSync(path.join(__dirname, '../src/realty_signal/web/signal-v2.js'), 'utf8');
const extract = (start, end) => html.slice(html.indexOf(start), html.indexOf(end));

test('region and complex price tiers share one five-step wording with explicit scope', () => {
  const ctx=vm.createContext({});
  vm.runInContext(extract('const _GRADE_NAMES=', '// ===== 매물 공통 카드 요소'),ctx);
  for(const [code,rank,name] of [['A',1,'상'],['B',2,'중상'],['C',3,'중'],['D',4,'중하'],['E',5,'하']]){
    assert.match(ctx.regionGradeBadge(code),new RegExp(`지역 가격대 ${name}(?![가-힣])`));
    assert.match(ctx.gradeBadge(rank),new RegExp(`단지 가격대 ${name}(?![가-힣])`));
  }
  assert.equal(ctx.regionGradeBadge('X'),'–');
  assert.equal(ctx.gradeBadge(6),'–');
  assert.doesNotMatch(html,/id="imjangDlg"|openImjang\(/);
});

test('Nick chat entry points and free-form question composer are absent while reports remain', () => {
  assert.doesNotMatch(html, /askNick|nickCxBtn|advFab|advChatTab|advInput|advSend|닉에게|Nick에게|닉과 대화|\/api\/advisor\/stream/);
  assert.doesNotMatch(signalV2, /Nick 호출|v2ExplainQuestion|이 리포트에 질문하기/);
  assert.match(html, /id="advReport"/); // read-only rollout fallback
  assert.match(signalV2, /핵심 판단 빠르게 확인/);
  assert.match(html, /id="listingCompareDlg"/);
});

test('retrospective scorecards are not sold as published prediction performance', () => {
  assert.match(html, /과거 자료로 재구성한 연구용 비교/);
  assert.match(html, /실제 발행 성과나 미래 상승 확률/);
  assert.match(html, /시그널의 인과적 기여나 향후 수익을 증명하지 않습니다/);
  assert.match(html, /과거 등급 변경 로그의 12주 방향 비교/);
  assert.doesNotMatch(html, /검증된 시그널|정말 맞나요\?|초과가 시그널의 실제 기여분/);
});

test('same-week sell-risk explanation enrichment is not described as a source revision', () => {
  assert.match(signalV2, /changed === 'explanation_change'/);
  assert.match(signalV2, /판정은 유지되며 매도주의 보정 근거 설명이 추가됐습니다/);
});

test('market charts do not label the ungraded buyer-demand ladder as an actionable signal', () => {
  assert.match(html, /buyer_demand_buy, name:'매수세 참고선'/);
  assert.doesNotMatch(html, /buyer_demand_buy, name:'매수신호'/);
});

test('KB national affordability is not presented as the opposite HF burden index or a district metric', () => {
  assert.match(html, /전국 아파트 주택구매력지수/);
  assert.match(html, /전국 구매력지수/);
  assert.match(html, /높을수록 구매력 증가/);
  assert.match(html, /이 지역이나 개인의 구매력은 아닙니다/);
  assert.doesNotMatch(html, /한국주택금융공사 K-HAI|높을수록 비쌈/);
  assert.match(html, /\['주택담보대출금리','KB부동산 데이터허브/);
});

test('regional trend chart does not overlay one national affordability line on every district', () => {
  const chart = extract('function drawChart(', '// 거시 추세 미니차트');
  assert.doesNotMatch(chart, /mc\.구매력|주택구매력지수/);
  assert.match(html, /전국 구매력지수는 이 지역의 지표가 아니므로 차트에 겹치지 않으며/);
});

test('KB freshness legend changes to held at the same nine-calendar-day boundary as signal assessment', async () => {
  const box={innerHTML:''}, source={textContent:''};
  const now=Date.parse('2026-10-04T00:00:00Z')/1000;
  const data={now,기준일:'2026-09-25',sources:[{key:'signal',label:'KB 시장 시그널',asof:'2026-09-25',ts:now,cycle:'주간'}]};
  const ctx=vm.createContext({
    document:{getElementById:id=>id==='freshBox'?box:id==='sourceAsOf'?source:null},
    fetch:async()=>({json:async()=>data}),
  });
  vm.runInContext(extract('function _relTime(', 'async function openHistModal('),ctx);
  await ctx.loadFreshness();
  assert.match(box.innerHTML,/시그널 판단 보류/);
  assert.match(box.innerHTML,/background:#dc2626/);
  assert.doesNotMatch(box.innerHTML,/KB 관측 14일 이내/);
  data.sources[0].asof='2026-09-26';
  await ctx.loadFreshness();
  assert.doesNotMatch(box.innerHTML,/시그널 판단 보류/);
  assert.match(box.innerHTML,/background:#16a34a/);
});

test('KB freshness uses the Korean date at midnight rather than elapsed UTC hours', async () => {
  const box={innerHTML:''};
  const data={now:Date.parse('2026-10-08T14:59:59Z')/1000,기준일:'2026-09-30',
    sources:[{key:'signal',label:'KB 시장 시그널',asof:'2026-09-30',ts:0,cycle:'주간'}]};
  const ctx=vm.createContext({
    document:{getElementById:id=>id==='freshBox'?box:null},
    fetch:async()=>({json:async()=>data}),
  });
  vm.runInContext(extract('function _relTime(', 'async function openHistModal('),ctx);
  await ctx.loadFreshness();
  assert.doesNotMatch(box.innerHTML,/시그널 판단 보류/);
  assert.match(box.innerHTML,/관측 8일 전/);
  assert.match(box.innerHTML,/background:#16a34a/);
  data.now=Date.parse('2026-10-08T15:00:00Z')/1000;
  await ctx.loadFreshness();
  assert.match(box.innerHTML,/9일 전/);
  assert.match(box.innerHTML,/관측 9일 전 · 시그널 판단 보류/);
  assert.match(box.innerHTML,/시그널 판단 보류/);
  assert.match(box.innerHTML,/background:#dc2626/);
  data.sources[0].asof='2026-10-10';
  await ctx.loadFreshness();
  assert.match(box.innerHTML,/관측일 미확인 · 시그널 판단 보류/);
  assert.match(box.innerHTML,/background:#94a3b8/);
});

test('unverified regulation is not drawn or labeled as a current designation', () => {
  const map=vm.createContext({document:{querySelectorAll:()=>[]}});
  vm.runInContext(extract('let _REGULATION =', 'function initMap('),map);
  assert.equal(vm.runInContext("_regTags('노원구','11')",map),null);
  assert.equal(vm.runInContext("_choTip('reg','노원구',{},'11',null,null)",map),
    '노원구 · 현재 규제 지정 미검증');
  assert.match(html,/규제 지정 최신성 미확인 · 계약 전 공식 고시 확인/);
  const badge=vm.createContext({_eok:()=> '6억'});
  vm.runInContext(extract('function _bpRegBadge(', 'async function _loadComplexWatch('),badge);
  const rendered=badge._bpRegBadge({정책상태:'unverified',지역:'노원구',규제지역:true,
    자격설명:'무주택',절대한도:60000});
  assert.match(rendered,/규제·대출 규칙 최신성 미검증/);
  assert.match(rendered,/규제지역 가정/);
  assert.doesNotMatch(rendered,/>규제지역<|>비규제</);
});

test('grade map reports when regional boundaries or price-grade joins are unavailable', async () => {
  const ctx = vm.createContext({_uvGeo:null, fetch:async()=>({ok:false,json:async()=>({})})});
  vm.runInContext(extract("let _uvGeoError=''", 'function _geoRow('), ctx);
  assert.equal(await ctx._ensureGeo(), null);
  assert.match(vm.runInContext('_uvGeoError',ctx),/지역 경계 지도를 불러오지 못했습니다/);
  const valid = vm.createContext({_uvGeo:null,fetch:async()=>({ok:true,json:async()=>({type:'FeatureCollection',features:[]})})});
  vm.runInContext(extract("let _uvGeoError=''", 'function _geoRow('), valid);
  assert.deepEqual(JSON.parse(JSON.stringify(await valid._ensureGeo())),{type:'FeatureCollection',features:[]});
  assert.equal(vm.runInContext('_uvGeoError',valid),'');
  assert.match(html,/현재 지역 가격대 자료와 지도 지역을 연결하지 못했습니다/);
});

test('optional Telegram opt-in is compact and keeps notification details expandable', async () => {
  const el={innerHTML:''};
  const ctx=vm.createContext({document:{getElementById:id=>id==='dashNotify'?el:null},
    fetch:async()=>({ok:true,json:async()=>({blocked:[{channel:'telegram',available:true,linked:false,
      reason:'연결 전입니다.',how:'마이페이지에서 연결합니다.'}],note:'선택 알림입니다.'})}),
    esc:value=>String(value),switchTab(){},});
  vm.runInContext(extract('async function _loadNotifyStatus(){','async function _loadWeekly(){'),ctx);
  await ctx._loadNotifyStatus();
  assert.match(el.innerHTML,/dash-notify-optin/);
  assert.match(el.innerHTML,/브리핑·관심단지·찜한 매물·청약 알림을 받아보세요/);
  assert.match(el.innerHTML,/<details>/);
  assert.match(el.innerHTML,/openTelegramConnect\(\)/);
  assert.match(el.innerHTML,/텔레그램 연결/);
  assert.doesNotMatch(el.innerHTML,/dash-card/);
});

test('GTX evidence is opt-in, coexists with map overlays, and separates operating from construction', async () => {
  const data=JSON.parse(fs.readFileSync(path.join(__dirname,'../src/realty_signal/web/gtx-evidence.json'),'utf8'));
  const lines=[], pins=[], removed=[];
  const chain=()=>({addTo(){return this},bindTooltip(){return this},bindPopup(html){pins.push(html);return this}});
  const L={layerGroup:chain,polyline:(points,style)=>{lines.push({points,style});return chain()},circleMarker:()=>chain()};
  const pane={style:{}}, legend={style:{display:'none'},innerHTML:'',textContent:''};
  const map={_gtxLegend:legend,getPane:()=>null,createPane:()=>pane,removeLayer:layer=>removed.push(layer)};
  const attrs={}; const classes=new Set();
  const button={classList:{toggle:(c,on)=>on?classes.add(c):classes.delete(c),remove:c=>classes.delete(c)},
    setAttribute:(k,v)=>{attrs[k]=v}};
  let calls=0;
  const ctx=vm.createContext({L,fetch:async()=>{calls++;return {ok:true,json:async()=>data}}});
  vm.runInContext(extract('const _GTX_COLORS=', 'function attachOverlayControl('),ctx);
  assert.match(extract('function attachOverlayControl(', 'function initMap('),/button\[data-m\].*button\[data-gtx\]/s);
  await ctx._toggleGtx(map,button);
  assert.equal(calls,1);
  assert.equal(attrs['aria-pressed'],'true');
  assert.match(legend.innerHTML,/실제 철도 선형·도보거리/);
  assert.match(legend.innerHTML,/B 공사 중/);
  assert.equal(lines.filter(x=>!x.style.dashArray).length,2);
  assert.equal(lines.filter(x=>x.style.dashArray).length,3);
  assert.ok(pins.some(p=>p.includes('GTX-A')&&p.includes('GTX-B')&&p.includes('운행 중')&&p.includes('공사 중')));
  assert.ok(pins.some(p=>p.includes('미개통 · 운행 불가')&&p.includes('삼성역 일대')));
  assert.match(ctx._gtxPopup({...data,asof:'2025-01-01'},'서울',[data.sections[0]]),/기준일 당시 운행 중/);
  assert.match(legend.innerHTML,/좌표 © OpenStreetMap 기여자/);
  await ctx._toggleGtx(map,button);
  assert.equal(attrs['aria-pressed'],'false');
  assert.equal(legend.style.display,'none');
  assert.equal(removed.length,1);
  await ctx._toggleGtx(map,button);
  assert.equal(calls,1); // shared same-day in-browser evidence cache
});

test('inline app scripts parse and signal/listing navigation are separate tasks', () => {
  const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map(m => m[1]);
  scripts.filter(Boolean).forEach(script => assert.doesNotThrow(() => new vm.Script(script)));
  assert.match(html, /data-group="market" onclick="switchTab\('signal'\)"[^>]*>시그널<\/button>/);
  assert.match(html, /data-group="browse" onclick="switchTab\('all'\)"[^>]*>매물 찾기<\/button>/);
  const targets = {groupSubLabel: {}, groupSubTabs: {}, listingFocus: {style: {}}};
  const ctx = vm.createContext({document: {getElementById: id => targets[id]}, location: {hash: '#all'}});
  vm.runInContext(extract('const _GROUPS={', 'const _LOAD={'), ctx);
  assert.equal(ctx._groupOf('signal'),'market');
  assert.equal(ctx._groupOf('all'),'browse');
  ctx.renderGroupSubnav('browse', 'all');
  assert.match(targets.groupSubTabs.innerHTML, /data-sub="all"/);
  assert.doesNotMatch(targets.groupSubTabs.innerHTML, /data-sub="signal"/);
  assert.doesNotMatch(targets.groupSubTabs.innerHTML, /분석·전략|경매·재건축|data-sub="auction"|data-sub="report"/);
  ctx.renderGroupSubnav('browse', 'auction');
  assert.match(targets.groupSubTabs.innerHTML, /data-sub="all"/);
  assert.doesNotMatch(targets.groupSubTabs.innerHTML, /data-sub="auction"|data-sub="report"/);
});

test('returning to a long-lived tab checks market and visible listings at most once per 15 minutes', async () => {
  let now=1_000_000, marketChecks=0, listingChecks=0;
  const ctx=vm.createContext({
    Date:{now:()=>now}, window:{_signalAppReady:true},
    document:{visibilityState:'visible'}, location:{hash:'#all'},
    checkClientVersion(){},
    loadData:async()=>{marketChecks++;}, loadAllListings:async()=>{listingChecks++;},
  });
  vm.runInContext(extract('let _lastVisibleMarketCheck=', 'let _showHist='),ctx);
  await ctx.refreshVisibleMarket();
  await ctx.refreshVisibleMarket();
  assert.equal(marketChecks,1);
  assert.equal(listingChecks,1);
  now+=900001;
  ctx.document.visibilityState='hidden';
  await ctx.refreshVisibleMarket();
  assert.equal(marketChecks,1);
  ctx.document.visibilityState='visible';
  await ctx.refreshVisibleMarket();
  assert.equal(marketChecks,2);
  assert.equal(listingChecks,2);
  assert.match(html,/KB \$\{d\.asof\|\|'–'\}.*info\.data_age_days/);
});

test('entering signal from another view rechecks the current market revision', async () => {
  let checks=0;
  const ctx=vm.createContext({
    window:{_signalAppReady:true}, _signalMode:'reasons',
    setSignalView(){}, renderScoreboard(){}, renderAha(){},
    loadData:async()=>{checks++;},
  });
  vm.runInContext(extract('const _LOAD={', 'function switchTab('),ctx);
  vm.runInContext('_LOAD.signal()',ctx);
  await Promise.resolve();
  assert.equal(checks,1);
  ctx.window._signalAppReady=false;
  vm.runInContext('_LOAD.signal()',ctx);
  await Promise.resolve();
  assert.equal(checks,1);
});

test('same named complex pins group only when display coordinates nearly match', () => {
  const ctx=vm.createContext({});
  vm.runInContext(extract('function _pinGroups(', 'function plotPins('),ctx);
  const items=[
    {단지명:'가상단지',지역:'노원구'}, {단지명:'가상단지',지역:'노원구'},
    {단지명:'가상단지',지역:'노원구'}, {단지명:'가상단지',지역:'강남구'}];
  const coords={0:[37.65001,127.07001],1:[37.65002,127.07002],
    2:[37.6510,127.0710],3:[37.65001,127.07001]};
  const groups=Array.from(ctx._pinGroups(items,coords),g=>Array.from(g));
  assert.deepEqual(groups,[[0,1],[2],[3]]);
});

test('retired favorite region shows reselect guidance without unsafe inline region code', async () => {
  const body={innerHTML:'',querySelectorAll:()=>[]};
  const ctx=vm.createContext({
    _favs:new Set(["region:서구';alert(1)//"]),
    _favRegionIdentity:new Map([["서구';alert(1)//",{status:'needs_reselection',message:'새 구역을 직접 선택해 주세요.'}]]),
    document:{getElementById:id=>id==='favListBody'?body:null},
    fetch:async()=>({json:async()=>({items:[]})}),
    esc:s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
    badge:s=>`[${s}]`,safeMarketSignal:()=>"BUY",
  });
  vm.runInContext(extract('async function renderFavList()', 'async function favRemove('),ctx);
  await ctx.renderFavList();
  assert.match(body.innerHTML,/새 구역을 직접 선택해 주세요/);
  assert.match(body.innerHTML,/\[HELD\]/);
  assert.doesNotMatch(body.innerHTML,/onclick="[^"]*alert\(1\)/);
  assert.match(body.innerHTML,/data-fav-region-open="서구&#39;;alert\(1\)\/\/"/);
});

test('ambiguous old region stays in records but not active favorite badges', async () => {
  const ctx=vm.createContext({
    _favs:new Set(), _favRegionIdentity:new Map(),
    fetch:async()=>({json:async()=>({favorites:[
      {kind:'region',key:'중구',region_identity:{status:'needs_reselection',message:'다시 선택'}},
      {kind:'region',key:'강남구',region_identity:{status:'ready',message:''}},
    ]})}),
  });
  vm.runInContext(extract('async function loadFavs()', 'async function toggleFav('),ctx);
  await ctx.loadFavs();
  assert.equal(ctx._favs.has('region:중구'),false);
  assert.equal(ctx._favs.has('region:강남구'),true);
  assert.equal(ctx._favRegionIdentity.get('중구').status,'needs_reselection');
});

test('code-key region favorite supplies current display name while old record stays separate', async () => {
  const ctx=vm.createContext({
    _favs:new Set(), _favRegionIdentity:new Map(), _favRegionKeyByName:new Map(),
    fetch:async()=>({json:async()=>({favorites:[
      {kind:'region',key:'중구',region_identity:{status:'needs_reselection',message:'다시 선택'}},
      {kind:'region',key:'kb:1114000000',region_identity:{status:'ready',name:'중구',label:'서울 · 중구'}},
    ]})}),
  });
  vm.runInContext(extract('async function loadFavs()', 'async function toggleFav('),ctx);
  await ctx.loadFavs();
  assert.equal(ctx._favs.has('region:중구'),true);
  assert.equal(ctx._favRegionKeyByName.get('중구'),'kb:1114000000');
  assert.equal(ctx._favRegionIdentity.get('중구').status,'needs_reselection');
  assert.equal(ctx._favRegionIdentity.get('kb:1114000000').label,'서울 · 중구');
});

test('complex star targets verified code favorite before an old name key', () => {
  const ctx=vm.createContext({_favComplexIdentity:new Map([
    ['강남구|같은단지',{status:'ready',name:'강남구'}],
    ['kb:1168000000|같은단지',{status:'ready',name:'강남구'}],
  ])});
  vm.runInContext(extract('function cxFavKey(', 'const _ambiguousLegacyComplexRegions='),ctx);
  assert.equal(ctx.cxFavKey('강남구','같은단지'),'kb:1168000000|같은단지');
});

test('old ambiguous Jung-gu can be explicitly reselected with the verified Seoul code', () => {
  const calls=[];
  const row={style:{},querySelector:selector=>({set onclick(fn){
    if(selector==='.fav') row.favoriteClick=fn;
  }})};
  const ctx=vm.createContext({
    document:{createElement:()=>row}, selected:null, _SIGC:{HELD:'#aaa'}, SIG_KO:{HELD:'보류'},
    _REGION_GRADE:{}, _favs:new Set(),
    _favRegionIdentity:new Map([['중구',{status:'needs_reselection'}]]),
    _favRegionKeyByName:new Map(), displaySignal:()=> 'HELD',
    badge:()=>'',gwonLabel:()=>'',esc:x=>x,
    toggleFav:(...args)=>calls.push(args),openFavList:()=>calls.push(['records']),toast:()=>{},
  });
  vm.runInContext(extract('function rowEl(', '// 전세수급·매수우위가 KB 권역'),ctx);
  ctx.rowEl({region:'중구',region_id:'kb:1114000000',group:'서울'},0);
  assert.match(row.innerHTML,/재선택/);
  assert.match(row.innerHTML,/<button type="button" class="fav" aria-label="서울 중구를 관심지역으로 재선택"/);
  row.favoriteClick({stopPropagation(){}});
  assert.deepEqual(calls,[['region','kb:1114000000','중구']]);
});

test('buyer region keeps old ambiguous name unselected until the user types a verified region', () => {
  const elements=new Map();
  const get=id=>{
    if(!elements.has(id)) elements.set(id,{value:'',checked:false,style:{},dataset:{},children:[],showModal(){}});
    return elements.get(id);
  };
  const signals=[{region:'중구',region_id:'kb:1114000000'}];
  const ctx=vm.createContext({
    document:{getElementById:get},
    _profile:{매수력:{가정:{지역:'중구'}}}, _bp:{가정:{지역:'중구'}},
    _sizeSel:{}, allSignals:signals, window:{allSignals:signals},
    bpPreview(){},
  });
  vm.runInContext(extract('function openBuyingPower()', 'let _bpTimer'),ctx);
  ctx.openBuyingPower();
  assert.equal(get('bp_region').value,'');
  assert.match(get('bp_region_help').textContent,/시·도를 확인할 수 없습니다/);
  get('bp_region').value='중구'; // 사용자가 현행 지역을 다시 입력했다.
  assert.equal(ctx._bpParams().region_code,'kb:1114000000');
});

test('buyer preview renders a verified response and shows region validation errors', async () => {
  const preview={textContent:'',innerHTML:''};
  const callbacks=[];
  const responses=[
    {ok:true,json:async()=>({ready:true,최대매수가:80000,월상환:120,필요현금:30000,
      대출:50000,LTV상한:0.7,실효LTV:0.6,규제:{},비용:{},안내:[]})},
    {ok:false,json:async()=>({detail:'지역을 다시 선택해 주세요.'})},
  ];
  const ctx=vm.createContext({
    document:{getElementById:()=>preview},
    _bpParams:()=>({capital:50000,region:'중구',region_code:'kb:1114000000'}),
    _bpRegBadge:()=>'',_eok:n=>String(n),
    URLSearchParams,clearTimeout:()=>{},setTimeout:fn=>{callbacks.push(fn);return callbacks.length;},
    fetch:async()=>responses.shift(),
  });
  vm.runInContext(extract('let _bpTimer=null', 'async function bpConfirm()'),ctx);
  ctx.bpPreview();
  await callbacks.shift()();
  assert.match(preview.innerHTML,/80000까지/);
  ctx.bpPreview();
  await callbacks.shift()();
  assert.match(preview.textContent,/지역을 다시 선택해 주세요/);
});

test('stale budget watch shows a reconfirm action instead of disappearing', async () => {
  const wrap={innerHTML:''};
  const ctx=vm.createContext({
    document:{getElementById:id=>id==='dashBudgetNewWrap'?wrap:null},
    fetch:async()=>({ok:true,json:async()=>({ready:false,reason:'reconfirm_required',
      message:'매수력을 다시 확정해 주세요.'})}),
    _dashCard:x=>x,_dashH:x=>x,
  });
  vm.runInContext(extract('async function _loadBudgetWatch()', 'async function _loadBuyingPower()'),ctx);
  await ctx._loadBudgetWatch();
  assert.match(wrap.innerHTML,/매수력을 다시 확정해 주세요/);
  assert.match(wrap.innerHTML,/openBuyingPower\(\)/);
});

test('grade choropleth sits above tiles and uses a darker-is-higher price scale', () => {
  assert.match(html, /pane\.style\.zIndex='350'/);
  assert.match(html, /choroplethPane/);
  assert.doesNotMatch(html, /pane:mode==='signal'\?'overlayPane':'tilePane'/);
  assert.doesNotMatch(html, /id="signalModeMap"/);
  const price=html.split('id="view-undervalued"')[1].split('id="view-presale"')[0];
  const signal=html.split('id="view-signal"')[1].split('id="view-all"')[0];
  assert.match(price, /id="sigMap"/);
  assert.doesNotMatch(signal, /id="sigMap"/);
  const ctx=vm.createContext({
    document:{getElementById:()=>null},
    _geoRow:(lut,name,code)=>{
      const row=lut&&lut[name];
      return row&&code&&row.region_id===`kb:${code}00000`?row:null;
    },
  });
  vm.runInContext(extract('const _GRADE_FILL=', 'function _choTip'), ctx);
  const expensive=ctx._choStyle('grade','강남',{강남:{급지:'A',region_id:'kb:1168000000'}},'11',null,'11680');
  const cheap=ctx._choStyle('grade','중랑',{중랑:{급지:'E',region_id:'kb:1126000000'}},'11',null,'11260');
  const missing=ctx._choStyle('grade','없는구',{},'11',null,'11000');
  assert.equal(expensive.fillColor, '#1e3a8a');
  assert.equal(cheap.fillColor, '#dbeafe');
  assert.notEqual(expensive.fillColor, cheap.fillColor);
  assert.equal(missing.fillColor, '#e2e8f0');
  assert.ok(expensive.fillOpacity>=0.7);
});

test('all maps use an attributed keyless fallback instead of watermarked CARTO tiles', () => {
  const ctx=vm.createContext({L:{tileLayer:(url,options)=>({url,options})}});
  vm.runInContext(extract('// 공용 지도 타일', '// ===== 지도 오버레이'),ctx);
  const fallback=ctx._mapTile();
  assert.match(fallback.url,/^https:\/\/tile\.openstreetmap\.org\//);
  assert.match(fallback.options.attribution,/OpenStreetMap contributors/);
  vm.runInContext('_mapCfg={vworld:"synthetic-key"}',ctx);
  const configured=ctx._mapTile();
  assert.match(configured.url,/api\.vworld\.kr/);
  assert.match(configured.options.attribution,/VWorld/);
  assert.doesNotMatch(html,/basemaps\.cartocdn\.com/);
  for(const id of ['uvMap','sigMap','myMap'])
    assert.match(html,new RegExp(`${id}=L\\.map\\(el,\\{attributionControl:true`));
});

test('grouped map pins keep per-listing selection and one marker per group', () => {
  const layers=new Set(), made=[];
  const map={removeLayer:m=>layers.delete(m),hasLayer:m=>layers.has(m),invalidateSize(){}};
  const st={map,markers:{},sel:null};
  const ctx=vm.createContext({_ms:{test:st},initMap:()=>st,setTimeout(){},esc:s=>String(s),_eok:v=>String(v),
    selectMsRow(){},L:{divIcon:o=>o,DomEvent:{stopPropagation(){}},marker:(coord,opts)=>{
      const marker={coord,opts,events:{},bindPopup(html){this.popup=html;return this;},
        on(name,fn){this.events[name]=fn;return this;},addTo(){layers.add(this);return this;}};
      made.push(marker);return marker;
    }}});
  vm.runInContext(extract('function _pinGroups(', '// 핀 포커스:'),ctx);
  const items=[{단지명:'가상단지',지역:'노원구',유형:'일반매물',총액:50000},
    {단지명:'가상단지',지역:'노원구',유형:'일반매물',총액:49000},
    {단지명:'다른단지',지역:'노원구',유형:'급매',총액:51000}];
  ctx.plotPins('test',items,{0:[37.65,127.07],1:[37.65,127.07],2:[37.66,127.08]},
    {label:x=>x.단지명},{keepView:true});
  assert.equal(made.length,2);
  assert.equal(st.markers[0],st.markers[1]);
  assert.notEqual(st.markers[0],st.markers[2]);
  assert.match(st.markers[0].popup,/2개 매물/);
  assert.equal(layers.size,2);
  const general=[{단지명:'가상단지',지역:'노원구',호가:75000,평형:25.7},
    {단지명:'가상단지',지역:'노원구',호가:74000,평형:25.7}];
  ctx.plotPins('test',general,{0:[37.65,127.07],1:[37.65,127.07]},
    {label:x=>x.단지명,popupKind:'일반매물',popupPrice:x=>x.호가},{keepView:true});
  assert.match(st.markers[0].popup,/일반매물 · 75000 · 25.7평/);
  assert.doesNotMatch(st.markers[0].popup,/가격 미확인/);
});

test('dense listing maps cluster nearby pins without breaking viewport filtering or row focus', async () => {
  const layers=new Set(), clusters=[];
  const map={addLayer:m=>layers.add(m),removeLayer:m=>layers.delete(m),hasLayer:m=>layers.has(m),
    getBounds:()=>({contains:c=>c[0]<37.4}),invalidateSize(){}};
  const st={map,markers:{},sel:null};
  const ctx=vm.createContext({_ms:{test:st},initMap:()=>st,setTimeout(){},esc:s=>String(s),_eok:v=>String(v),
    _boundsKey:()=> 'test-bounds',
    renderMsList(){},hideMapCta(){},L:{divIcon:o=>o,latLng:(...c)=>c,DomEvent:{stopPropagation(){}},
      marker:(coord,opts)=>({coord,opts,events:{},bindPopup(){return this;},on(name,fn){this.events[name]=fn;return this;},
        getLatLng(){return coord;},openPopup(){this.opened=true;},getElement(){return null;}}),
      markerClusterGroup:()=>{const members=new Set();const cluster={members,
        addLayers:ms=>ms.forEach(m=>members.add(m)),addLayer:m=>members.add(m),removeLayer:m=>members.delete(m),
        hasLayer:m=>members.has(m),zoomToShowLayer:(m,cb)=>{assert(members.has(m));cb();}};
        clusters.push(cluster);return cluster;}}});
  vm.runInContext(extract('function applyViewportFilter(', 'async function selectMsRow('),ctx);
  vm.runInContext(extract('function _pinGroups(', '// 핀 포커스:'),ctx);
  vm.runInContext(extract('async function _focusPinOnly(', 'async function focusPin('),ctx);
  const items=Array.from({length:81},(_,i)=>({단지명:`단지${i}`,지역:'시험구'}));
  const coords=Object.fromEntries(items.map((_,i)=>[i,[37+i/100,127]]));
  ctx.plotPins('test',items,coords,{label:x=>x.단지명},{keepView:true});
  assert.equal(clusters.length,1);
  assert.equal(clusters[0].members.size,81);
  assert.equal(layers.size,1,'cluster layer replaces individual map markers');
  ctx.applyViewportFilter('test',{lock:true});
  assert.equal(clusters[0].members.size,40);
  st._selectionGen=1;
  await ctx._focusPinOnly('test',0,1);
  assert.equal(st.sel,0);
  assert.equal(st.markers[0].opened,true);
  assert.equal(clusters[0].members.size,40);
});

test('general listing status distinguishes personal-only, source failure, and limited coverage', () => {
  const ctx = vm.createContext({});
  vm.runInContext(extract('function generalStatusText(data){', 'async function loadGeneralListings('), ctx);
  assert.match(ctx.generalStatusText({state:'personal_only'}), /개인 계정/);
  assert.match(ctx.generalStatusText({state:'identity_unverified'}), /시·도 출처를 확인할 수 없어/);
  assert.match(ctx.generalStatusText({state:'failed',listings:[],refresh:{error:'timeout'}}), /0건이라는 뜻이 아닙니다/);
  const text = ctx.generalStatusText({state:'partial',listings:[{}],regions:['노원구'],
    last_success_at:1,refresh:{limited_regions:['노원구'],failed_requests:0}});
  assert.match(text, /전체 매물 아님/);
});

test('general listings reuse the same-day map and explicit refresh bypasses cache', async () => {
  const calls = [], nodes = new Map(), map = {invalidateSize: () => calls.push('resize')};
  const ctx = vm.createContext({
    _hbList: [], _hbLoadedAt: 0, _ms: {hbMap: {map}},
    _listingCacheFresh: at => at > 0, _reuseListingMap: () => map.invalidateSize(),
    document: {getElementById: id => {
      if (!nodes.has(id)) nodes.set(id, {textContent: '', disabled: false});
      return nodes.get(id);
    }},
    fetch: async url => {calls.push(url); return {ok:true, json:async()=>({state:'ready', listings:[{단지명:'시험단지'}], regions:['노원구']})};},
    generalStatusText: () => '1건', mapSplit: () => calls.push('render'),
  });
  vm.runInContext(extract('async function loadGeneralListings(', 'async function refreshGeneralListings('), ctx);
  await ctx.loadGeneralListings();
  await ctx.loadGeneralListings();
  await ctx.loadGeneralListings(true);
  assert.deepEqual(calls, ['/api/general-listings','render','resize','/api/general-listings','render']);
});

test('market snapshot revalidates date and guard revision without refetching stable data', async () => {
  const calls = [], saved = new Map(), elements = new Map();
  const payload = {
    '/api/meta': {last_date:'2026-09-29'},
    '/api/signals': [{region:'노원구',signal:'BUY'}],
    '/api/regime': {}, '/api/macro': {},
  };
  const ctx = vm.createContext({
    meta:null, allSignals:[], selected:null, active:new Set(['BUY']),
    activeGrade:new Set(['A','B','C','D','E']), _favRegionKeyByName:new Map(), window:{},
    displaySignal:r=>r?.display_signal||r?.signal||'HELD',
    localStorage:{getItem:k=>saved.get(k)||null,setItem:(k,v)=>saved.set(k,v)},
    document:{getElementById:id=>{
      if(!elements.has(id)) elements.set(id,{style:{},textContent:'',innerHTML:'',value:''});
      return elements.get(id);
    }},
    fetch:async url=>{calls.push(url); return {ok:true,json:async()=>payload[url]};},
    renderZoneLegend(){}, renderList(){}, selectRegion:region=>calls.push('select:'+region),
  });
  vm.runInContext(extract('const _MARKET_CACHE_KEY=', 'let _showHist='), ctx);
  await ctx.loadData();
  assert.deepEqual(calls.slice(0,4), ['/api/meta','/api/signals','/api/regime','/api/macro']);
  await ctx.loadData();
  assert.deepEqual(calls.filter(x=>x.startsWith('/api/')).slice(4), ['/api/meta']);
  payload['/api/meta']={...payload['/api/meta'],last_date:'2026-09-30'};
  await ctx.loadData();
  assert.deepEqual(calls.filter(x=>x.startsWith('/api/')).slice(5), ['/api/meta','/api/meta','/api/signals','/api/regime','/api/macro']);
  assert.equal(elements.get('dateTxt').textContent,'KB 기준 2026-09-30');
  payload['/api/meta']={...payload['/api/meta'],signal_guard_version:'v3'};
  await ctx.loadData();
  assert.equal(calls.filter(x=>x.startsWith('/api/')).length,15);
  await ctx.loadData(true);
  assert.equal(calls.filter(x=>x.startsWith('/api/')).length,19);
});

test('long-lived tabs warn about a new UI revision without auto-reloading or losing the current screen', async () => {
  assert.match(html, /name="signal-apt-client-version" content="__SIGNAL_APT_CLIENT_VERSION__"/);
  assert.match(html, /id="clientUpdateBanner" role="alert" hidden/);
  const calls=[], banner={hidden:true};
  let now=100000, serverVersion='old', reloads=0;
  const ctx=vm.createContext({
    Date:{now:()=>now}, window:{_signalAppReady:true},
    document:{visibilityState:'visible',querySelector:()=>({content:'old'}),getElementById:()=>banner},
    location:{reload:()=>reloads++},
    fetch:async url=>{calls.push(url);return {ok:true,json:async()=>({client_version:serverVersion})};},
  });
  vm.runInContext(extract('const _CLIENT_VERSION=', 'const _MARKET_CACHE_KEY='),ctx);
  await ctx.checkClientVersion();
  assert.equal(banner.hidden,true);
  assert.deepEqual(calls,['/api/client-version']);
  serverVersion='new';
  await ctx.checkClientVersion();
  assert.equal(calls.length,1); // tab switches in the same minute do not flood the endpoint
  now+=61000;
  await ctx.checkClientVersion();
  assert.equal(banner.hidden,false);
  assert.equal(reloads,0); // refresh remains a deliberate user action
  await ctx.checkClientVersion();
  assert.equal(calls.length,2);
  assert.match(extract('function switchTab(', 'const _ROUTES='), /checkClientVersion\(\)/);
  assert.match(extract('async function refreshVisibleMarket(', 'let _showHist='), /checkClientVersion\(\)/);
});

test('first signal report opens a verified visible favorite before the global top grade', () => {
  const inputs={search:{value:''},groupFilter:{value:''}};
  const ctx=vm.createContext({
    allSignals:[
      {region:'강북구',group:'서울',display_signal:'STRONG_BUY',assessment_status:'ready'},
      {region:'노원구',group:'서울',display_signal:'BUY',assessment_status:'ready'},
      {region:'중구',group:'서울',display_signal:'HELD',assessment_status:'held'},
    ],
    active:new Set(['STRONG_BUY','BUY','HELD']), activeGrade:new Set(['A','B','C','D','E']),
    _favRegionKeyByName:new Map([['노원구','kb:1135000000']]),
    document:{getElementById:id=>inputs[id]},
    displaySignal:r=>r.assessment_status==='ready'?r.display_signal:'HELD',
  });
  vm.runInContext(extract('function _defaultSignalRegion(){', 'function _applyMarketData('),ctx);
  assert.equal(ctx._defaultSignalRegion(),'노원구');
  inputs.search.value='강북';
  assert.equal(ctx._defaultSignalRegion(),'강북구');
  inputs.search.value='';
  ctx._favRegionKeyByName=new Map([['중구','kb:1114000000']]);
  assert.equal(ctx._defaultSignalRegion(),'중구');
  ctx.active.delete('HELD');
  assert.equal(ctx._defaultSignalRegion(),'강북구');
  ctx._favRegionKeyByName=new Map([['사라진 지역','kb:9999900000']]);
  assert.equal(ctx._defaultSignalRegion(),'강북구');
});

test('signal filter button says which conditions are excluded, not selected', () => {
  const button={innerHTML:'',attributes:{},setAttribute(k,v){this.attributes[k]=v;}};
  const ctx=vm.createContext({
    SIGNALS:['STRONG_BUY','BUY','WATCH','NEUTRAL','SELL_RISK','HELD'],
    active:new Set(['STRONG_BUY','BUY','WATCH','SELL_RISK','HELD']),
    activeGrade:new Set(['A','B','C','D','E']), _filtersOpen:false,
    document:{getElementById:()=>button},
  });
  vm.runInContext(extract('function updateFilterToggleLabel(){', 'function renderFilters(){'),ctx);
  ctx.updateFilterToggleLabel();
  assert.match(button.innerHTML,/제외 1개/);
  assert.doesNotMatch(button.innerHTML,/1개 골라 둠/);
  assert.equal(button.attributes['aria-expanded'],'false');
});

test('stale weekly observations do not appear as this week signal changes', async () => {
  const wrap = {innerHTML:''}, events = [];
  let payload = {ready:true, current_check_ready:true, as_of:'2026-09-21', stale_days:12,
    mine:[], rest:[], movers:[], totals:{regions:20,up:1,down:4}, holds:[]};
  const ctx = vm.createContext({
    document:{getElementById:()=>wrap},
    fetch:async()=>({ok:true,json:async()=>payload}),
    _dashCard:x=>x, _dashH:x=>x, _dashMore:(label,x)=>label+x,
    _weeklyQuiet:x=>x, _myWeeklyBlock:()=>'', _holdBlock:()=>'',
    _renderComeback:()=>events.push('comeback'),
    _ackWhenVisible:()=>events.push('seen'),
    esc:x=>String(x),
  });
  vm.runInContext(extract('function _staleBanner(d){', 'function _moverRow(m){'),ctx);
  vm.runInContext(extract('async function _loadWeekly(){', '// ===== 다음 할 일'),ctx);
  await ctx._loadWeekly();
  assert.match(wrap.innerHTML,/주간 변화 확인 보류/);
  assert.match(wrap.innerHTML,/KB 관측 기준일이 12일 전/);
  assert.doesNotMatch(wrap.innerHTML,/등급 변화 <b>5곳/);
  assert.deepEqual(events,[]);
  payload={...payload,as_of:'2026-09-28',stale_days:5};
  await ctx._loadWeekly();
  assert.match(wrap.innerHTML,/등급 변화 없음/);
  assert.deepEqual(events,['comeback','seen']);
});

test('raw weekly BUY is historical when current safety assessment is held', async () => {
  const wrap={innerHTML:''};
  const change={region:'노원구',from:'WATCH',to:'BUY',from_ko:'관망',to_ko:'매수',up:true,current_verified:false};
  const ctx=vm.createContext({
    document:{getElementById:()=>wrap},
    fetch:async()=>({ok:true,json:async()=>({ready:true,current_check_ready:true,as_of:'2026-09-28',stale_days:5,
      mine:[change],rest:[],movers:[],totals:{regions:1,up:1,down:0},holds:[]})}),
    _dashCard:x=>x,_dashH:x=>x,_dashMore:(label,x)=>label+x,
    _weeklyQuiet:x=>x,_myWeeklyBlock:()=>'',_holdBlock:()=>'',_renderComeback:()=>{},
    _ackWhenVisible:()=>{},esc:x=>String(x),
    _sigMoveRow:()=>'<div>과거 계산 비교 · 현재 판정 확인 필요</div>',
  });
  vm.runInContext(extract('function _staleBanner(d){','function _moverRow(m){'),ctx);
  vm.runInContext(extract('async function _loadWeekly(){','// ===== 다음 할 일'),ctx);
  await ctx._loadWeekly();
  assert.match(wrap.innerHTML,/현재 등급 변화 확인 필요/);
  assert.match(wrap.innerHTML,/과거 계산 비교/);
  assert.doesNotMatch(wrap.innerHTML,/현재 판정으로 확인된 등급 변화 <b>1곳/);
});

test('comeback history never turns held raw BUY into current buying advice', () => {
  const el={innerHTML:''};
  const ctx=vm.createContext({
    document:{getElementById:()=>el},
    _dashCard:x=>x,_dashMore:(label,x)=>label+x,
    _buyerChip:b=>b?`BUYER ADVICE ${b.why}`:'',
    esc:x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
  });
  vm.runInContext(extract('function _renderComeback(d){', '/** 곧 바뀔 수 있는 동네 워치'),ctx);
  const changed={region:"악성' onclick='alert(1)",from_ko:'관망',to_ko:'강력매수',up:true,
    from:'WATCH',to:'STRONG_BUY',steps:1,path:[],current_verified:false,
    buyer:{why:'선택지가 줄기 전에 확인'}};
  const comeback={ready:true,quiet:false,weeks:4,as_of:'2026-09-28',prev_as_of:'2026-08-31',
    totals:{regions:1,up:1,down:0},mine:[changed],rest:[],rest_total:0};
  ctx._renderComeback({comeback});
  assert.match(el.innerHTML,/당시 강력매수/);
  assert.match(el.innerHTML,/과거 계산 · 현재 판정 확인 필요/);
  assert.doesNotMatch(el.innerHTML,/BUYER ADVICE|선택지가 줄기 전에/);
  assert.doesNotMatch(el.innerHTML,/onclick='alert/);
  changed.current_verified=true;
  ctx._renderComeback({comeback});
  assert.match(el.innerHTML,/BUYER ADVICE/);
  assert.doesNotMatch(el.innerHTML,/당시 강력매수/);
});

test('opening quicksale reaches both APIs and renders', async () => {
  const calls = [], status = {}, region = {options: [0, 1]};
  const ctx = vm.createContext({
    document: {getElementById: id => id === 'qsRegion' ? region : status},
    fetch: async url => {calls.push(url); return {json: async () => ({ready: true, listings: []})};},
    _qsMode: '급매', _qsLoadedAt: 0, _qsData: {급매:null,찐매물:null},
    _listingCacheFresh: () => false,
    qsStatusText: () => '0건', renderQuicksale: () => calls.push('render'),
  });
  vm.runInContext(extract('async function loadQuicksale(', 'async function refreshQuicksale('), ctx);
  await ctx.loadQuicksale();
  assert.deepEqual(calls, ['/api/quicksale', '/api/certified', 'render']);
  assert.equal(status.textContent, '0건');
});

test('quicksale status separates verified empty from upstream failure', () => {
  const ctx = vm.createContext({});
  vm.runInContext(extract('function qsStatusText(data){', 'function qsMode(mode){'), ctx);
  const zero = {ready: true, state: 'empty', listings: [], regions: ['노원구'], last_success_at: 1};
  assert.match(ctx.qsStatusText(zero), /해당 매물 없음/);
  assert.match(ctx.qsStatusText({ready: false, state: 'failed', listings: [], refresh: {error: 'timeout'}}), /0건으로 판단할 수 없습니다/);
  assert.match(ctx.qsStatusText({...zero, state: 'partial_empty', refresh: {failed_requests: 1}}), /0건 확정 불가/);
  assert.match(ctx.qsStatusText({...zero, refresh: {signal_context: 'unavailable'}}), /지역 시그널 미확인/);
  assert.match(ctx.qsStatusText({...zero, state: 'identity_unverified'}), /시·도 출처를 확인할 수 없어/);
});

test('radar card never borrows a region badge after source province mismatch', () => {
  const ctx = vm.createContext({safeMarketSignal: () => 'STRONG_BUY'});
  vm.runInContext(extract('function safeRadarSignal(listing){', 'function _mtPass(signal, grade){'), ctx);
  assert.equal(ctx.safeRadarSignal({지역: '중구', 시도: '인천', 시그널: ''}), 'HELD');
  assert.equal(ctx.safeRadarSignal({지역: '중구', 시도: '서울', 시그널: 'BUY'}), 'STRONG_BUY');
});

test('quicksale empty view names filter and source states separately', () => {
  const empty = {textContent: ''};
  const ctx = vm.createContext({
    document: {getElementById: () => empty},
    renderMtFilter() {}, inFocus: () => true, _mtPass: () => true, mapSplit() {},
    _mtBuyOnly: false,
    _qsMode: '급매', _qsList: [], _qsCertList: [], _qsData: {급매: {state: 'failed'}},
    _qsViewKey: () => 'test',
  });
  vm.runInContext(extract('function renderQuicksale(){', '// ===== 통합 매물('), ctx);
  ctx.renderQuicksale();
  assert.match(empty.textContent, /수집이 실패했습니다/);
  ctx._qsData.급매 = {state: 'empty'};
  ctx.renderQuicksale();
  assert.match(empty.textContent, /조회에 성공한 지역/);
  ctx._qsList = [{지역: '노원구', 급매갭: -2}];
  ctx.inFocus = () => false;
  ctx.renderQuicksale();
  assert.match(empty.textContent, /필터에서 제외됐습니다/);
});

test('listing evidence action requires exact area and escapes source attributes', () => {
  const ctx = vm.createContext({esc: s => String(s ?? '').replace(/[&<>"']/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))});
  vm.runInContext(extract('function qsEvidenceBtn(m){', '// ===== 통합 매물('), ctx);
  const button = ctx.qsEvidenceBtn({단지명:'A" onclick="alert(1)', 지역:'테스트구',
    총액:48000, ref:{전용면적:84.9,층:11}});
  assert.match(button, /data-name="A&quot; onclick=/);
  assert.doesNotMatch(button, /data-name="A" onclick=/);
  assert.match(button, /data-area="84.9"/);
  assert.match(button, /data-floor="11"/);
  assert.match(ctx.qsEvidenceBtn({단지명:'A', 지역:'테스트구', 호가:48000}), /전용면적·호가 확인 필요/);
});

test('radar cards and map ignore legacy cross-area discounts and show exclusive area', () => {
  let rendered;
  const ctx=vm.createContext({
    document:{getElementById:()=>({textContent:''})}, renderMtFilter(){},
    inFocus:()=>true, _mtPass:()=>true, _mtBuyOnly:false,
    _qsMode:'급매', _qsList:[{단지명:'비교단지',전용면적:59,평형:'84',호가:50000,급매갭:-37.5,중위시세:80000}],
    _qsCertList:[], _qsData:{급매:{state:'ready'}}, _qsViewKey:()=>'',
    mapSplit:(_list,_map,rows,options)=>{rendered={rows,options};},
    regionSignalBadge:grade=>`지역 신호 · ${grade==='HELD'?'판단 보류':grade}`,
    safeRadarSignal:()=> 'HELD', _mtMetric:(label,value)=>label+value,
    _eok:n=>(n/10000)+'억', txCostsSlot:()=>'', watchBtn:()=>'', reportBtn:()=>'',
    loanBtn:()=>'', cxDetailBtn:()=>'', cxSigBtn:()=>'', naverBtn:()=>'', esc:String,
  });
  vm.runInContext(extract('function qsAreaLabel(m){','// ===== 통합 매물('),ctx);
  ctx.renderQuicksale();
  assert.equal(rendered.rows.length,1);
  const row=rendered.rows[0], card=rendered.options.summary(row);
  const shown=JSON.stringify(card)+rendered.options.detail(row)+rendered.options.label(row);
  assert.match(card.right,/호가.*5억/);
  assert.match(card.sub,/전용 59㎡/);
  assert.match(card.nm,/지역 신호 · 판단 보류/);
  assert.match(shown,/공급사 급매 표시/);
  assert.doesNotMatch(shown,/-37\.5|8억|84평|중위시세/);
  assert.equal(rendered.options.color(row),rendered.options.color({...row,급매갭:0}));
  assert.equal(ctx.qsAreaLabel({평형:84}),'전용면적 확인 필요');
  assert.doesNotMatch(html,/id="qsGap"|시세 대비 저가 매물/);
});

function selectionHarness() {
  const classes = (...init) => {
    const s = new Set(init);
    return {contains: x => s.has(x), add: x => s.add(x), remove: x => s.delete(x)};
  };
  const det = {classList: classes('ms-detail'), style: {display: 'none'}, innerHTML: ''};
  const row = {classList: classes('ms-row'), nextElementSibling: det, setAttribute() {}, scrollIntoView() {}};
  const list = {querySelector: () => row, querySelectorAll: q => q === '.ms-detail' ? [det] : [row]};
  let release, popupOpens = 0;
  const state = {listId: 'list', items: [{}], coords: {}, markers: {}, sel: null,
    opt: {detail: () => '', geoq: () => 'synthetic'}, map: {setView() {}}};
  const ctx = vm.createContext({
    document: {getElementById: () => list}, _ms: {test: state},
    fetch: () => new Promise(r => {release = () => r({json: async () => ({coords: {synthetic: [37, 127]}})});}),
    plotPins() {state.markers[0] = {getElement: () => null, getLatLng: () => [37, 127],
      openPopup() {popupOpens++;}, closePopup() {}};},
  });
  vm.runInContext(extract('async function selectMsRow(', 'async function mapSplit('), ctx);
  vm.runInContext(extract('async function _focusPinOnly(', 'async function focusPin('), ctx);
  return {ctx, state, det, row, release: () => release(), opened: () => popupOpens};
}

test('second click closes and delayed geocode cannot restore selection', async () => {
  const h = selectionHarness();
  const first = h.ctx.selectMsRow('test', 0);
  assert.equal(h.det.style.display, 'block');
  await h.ctx.selectMsRow('test', 0);
  assert.equal(h.det.style.display, 'none');
  h.release();
  await first;
  assert.equal(h.state.sel, null);
  assert.equal(h.opened(), 0);
  assert.equal(Object.keys(h.state.coords).length, 0);
});

test('listing cards render before centroid lookup and stale pins are removed', async () => {
  let releaseCentroids;
  const events=[];
  const oldMarker={};
  const state={markers:{0:oldMarker},map:{removeLayer:layer=>events.push(layer===oldMarker?'remove-old':'remove-other')},
    _vpLocked:false};
  const ctx=vm.createContext({
    _ms:{map:state},initMap:()=>state,bindMapViewport(){},
    renderMsList:(_map,indices)=>events.push(`render-${indices.length}`),
    plotPins:()=>events.push('pins'),applyViewportFilter:()=>events.push('viewport'),
    fetch:url=>url.startsWith('/api/region-centroids')
      ? new Promise(resolve=>{releaseCentroids=()=>resolve({json:async()=>({centroids:{서울:[37,127]}})});})
      : Promise.resolve({json:async()=>({coords:{}})}),
  });
  vm.runInContext(extract('async function mapSplit(', '// 공용 지도 타일'),ctx);
  const pending=ctx.mapSplit('list','map',[{지역:'서울'}],{
    geoq:()=> '서울 테스트단지',regionOf:()=> '서울'});
  assert.equal(events[0],'remove-old');
  assert.equal(events[1],'render-1');
  assert.equal(typeof releaseCentroids,'function');
  assert.equal(events.includes('pins'),false);
  releaseCentroids();
  await pending;
  assert.deepEqual(events,['remove-old','render-1','pins','viewport']);
});

test('data reload invalidates in-flight map selection', async () => {
  const h = selectionHarness();
  const first = h.ctx.selectMsRow('test', 0);
  h.state._selectionGen++;
  h.release();
  await first;
  assert.equal(h.opened(), 0);
});

test('buyer candidate card distinguishes inquiry from infeasibility and expands in place', () => {
  const ctx = vm.createContext({esc: s => String(s ?? '').replace(/</g, '&lt;')});
  vm.runInContext(extract('function _buyerFour(', 'async function loadConclusion(){'), ctx);
  const eok = n => n == null ? '–' : `${n}만`;
  const auction = ctx._ccListingCard({유형: '경매', 단지명: '시험단지', 지역: '노원구',
    총액: 50000, 입찰상태: 'needs_review', 예산확인필요: true,
    decision: {feasibility: 'unknown', unknowns: ['권리 확인'], next_action: '권리 확인 후 검토'}}, eok);
  assert.match(auction, /<details class="cc-listing"/);
  assert.match(auction, /아직 입찰하지 않음/);
  assert.match(auction, /최저 입찰가입니다/);
  assert.match(auction, /돈/);
  assert.match(auction, /아직/);
  assert.match(auction, /권리 확인/);
  assert.match(auction, /다음/);
  assert.doesNotMatch(auction, /예산초과·참고|<div onclick="switchTab/);

  const estimated = ctx._ccListingCard({유형: '재건축', 단지명: '<가짜>', 지역: '노원구',
    추정가: 60000, 가격출처: '지역평단추정', 예산확인필요: true,
    decision: {feasibility: 'unknown', unknowns: ['현장 확인']}}, eok);
  assert.match(estimated, /가격과 돈을 확인해야 함/);
  assert.match(estimated, /동네 평균으로 짐작한 가격입니다/);
  assert.match(estimated, /&lt;가짜>/);
  const lined = ctx._ccListingCard({유형:'급매', 단지명:'선', 지역:'노원구', 총액:10000,
    lines:{cash:'서버현금', price:'서버가격', unknown:'서버미확인', next:'서버다음'}}, eok);
  assert.match(lined, /서버현금/);
  assert.match(lined, /서버다음/);
  assert.doesNotMatch(lined, /매수 상한을 확정하면/);
});

test('listing price filter and budget sort require server-validated buyer fit', () => {
  const ctx = vm.createContext({
    _laPrice: {lo: 0, hi: 200000, min: 0, max: 150000},
  });
  vm.runInContext(extract('function _laAsk(x){', 'function renderAllListings(){'), ctx);
  assert.equal(ctx._laBudgetTier({총액: 40000,budget_fit:{status:'within'}}), 2);
  assert.equal(ctx._laBudgetTier({총액: null}), 1);
  assert.equal(ctx._laBudgetTier({총액: 80000,budget_fit:{status:'above'}}), 0);
  assert.equal(ctx._laBudgetTier({총액: 40000}), 1);
  assert.match(ctx._laBudgetLabel({총액: 40000,유형:'일반매물'}), /예산 비교 보류/);
  assert.equal(ctx._laPricePass({총액: null}), true);
  assert.equal(ctx._laPricePass({총액: 160000}), true);
  ctx._laPrice.active = true;
  assert.equal(ctx._laPricePass({총액: null}), false);
  assert.equal(ctx._laPricePass({총액: 10000}), true);
  assert.equal(ctx._laPricePass({총액: 160000}), false);
  assert.match(html, /내 예산 순/);
  assert.doesNotMatch(html, /언제·어디를 볼지/);
  assert.match(html, /시그널·지역 가격 비교<\/b> — 가격·수급 추세와 판정 근거를 먼저 보고/);
  assert.match(html, /id="sigMap"/);
  assert.match(html, /onclick="switchTab\('signal'\)"[^>]*>시그널</);
});

test('sale navigation groups sources while supplier flags filter independently', () => {
  const ctx=vm.createContext({_SALE_TYPES:['일반매물','급매','찐매물'],
    document:{getElementById:()=>({value:''})},switchTab:()=>{}});
  vm.runInContext(extract('function openTypedList(', 'let _listingSub='),ctx);
  vm.runInContext(extract('function _laSalePass(', 'function _laAsk('),ctx);
  ctx.openTypedList('일반매물');
  assert.deepEqual([...ctx._laSel],['일반매물','급매','찐매물']);
  assert.equal(ctx._laSalePass({유형:'찐매물'}),true);
  ctx.openTypedList('급매');
  assert.equal(ctx._laSalePass({유형:'찐매물',supplier_flags:['urgent','certified']}),true);
  assert.equal(ctx._laSalePass({유형:'일반매물',supplier_flags:[]}),false);
  ctx.openTypedList('찐매물');
  assert.equal(ctx._laSalePass({유형:'일반매물',supplier_flags:['certified']}),true);
  assert.match(ctx._laSourceLabel({유형:'일반매물',source:'hanbang',supplier_flags:['certified']}),/한방.*공급사 인증 표시/);
  assert.match(html,/const _LA_TYPES=\['매매','청약','경매','재건축'\]/);
});

test('listing price line shows comparable conditions but never turns missing data into a discount', () => {
  const ctx=vm.createContext({});
  vm.runInContext(extract('function listingPriceLine(', 'function _laAsk('),ctx);
  const price={상태:'관측비교',호가차이율:-10,표본수:3,입력층:8};
  assert.match(ctx.listingPriceLine(price),/같은 전용면적·인근 층.*10% 낮음.*3건.*할인율 아님/);
  assert.match(ctx.listingPriceLine({...price,호가차이율:0}),/차이 없음/);
  assert.equal(ctx.listingPriceLine({...price,상태:'보류'}),'');
  assert.equal(ctx.listingPriceLine({...price,호가차이율:null}),'');
  assert.equal(ctx.listingPriceLine(undefined),'');
  assert.match(signalV2,/listingPriceLine\(x\.listing\.price_comparison\)/);
});

test('current asking sample is labeled as a limited sample and held data is never narrated', () => {
  assert.match(html,/같은 공급사 호가 표본보다 낮은순/);
  assert.match(signalV2,/item\.asking_comparison/);
  assert.match(signalV2,/실거래 할인율이 아니며/);
});

test('trade-evidence sort puts supported lower quotes first and unverified rows last', () => {
  const ctx=vm.createContext({});
  vm.runInContext(extract('function _laTradePriceEvidence(', 'function renderAllListings(){'),ctx);
  const lower={기회도:10,price_comparison:{상태:'관측비교',호가차이율:-12,표본수:3}};
  const near={기회도:90,price_comparison:{상태:'관측비교',호가차이율:-2,표본수:8}};
  const equal={기회도:50,price_comparison:{상태:'관측비교',호가차이율:0,표본수:5}};
  const unknown={기회도:100,price_comparison:{상태:'보류',호가차이율:null,표본수:0}};
  assert.ok(ctx._laCompareTradePrice(lower,near)<0);
  assert.ok(ctx._laCompareTradePrice(near,equal)<0);
  assert.ok(ctx._laCompareTradePrice(equal,unknown)<0);
  assert.ok(ctx._laCompareTradePrice(unknown,near)>0);
  assert.equal(ctx._laTradePriceEvidence({...lower,price_comparison:{상태:'관측비교',호가차이율:null,표본수:5}}),null);
  assert.equal(ctx._laTradePriceEvidence({...lower,price_comparison:{상태:'관측비교',호가차이율:-1,표본수:2}}),null);
  assert.match(html,/같은 면적 실거래보다 낮은순/);
  assert.match(html,/if\(_laSort==='trade_low'\) return _laCompareTradePrice\(a,b\)/);
  assert.match(html,/할인율 아님/);
  const askingLow={기회도:1,asking_comparison:{상태:'관측비교',호가차이율:-10,표본수:3}};
  const askingHigh={기회도:100,asking_comparison:{상태:'관측비교',호가차이율:2,표본수:4}};
  const askingUnknown={기회도:1000,asking_comparison:{상태:'보류',호가차이율:null,표본수:0}};
  assert.ok(ctx._laCompareAsking(askingLow,askingHigh)<0);
  assert.ok(ctx._laCompareAsking(askingHigh,askingUnknown)<0);
  assert.match(html,/if\(_laSort==='asking_low'\) return _laCompareAsking\(a,b\)/);
});

test('multiple observed price evidence is a discovery aid, not a blended deal score', () => {
  const ctx=vm.createContext({});
  vm.runInContext(extract('function _laTradePriceEvidence(', 'function renderAllListings(){'),ctx);
  const overlap={기회도:1,
    price_comparison:{상태:'관측비교',호가차이율:-5,표본수:4},
    asking_comparison:{상태:'관측비교',호가차이율:-8,표본수:3},
    price_reduction:{상태:'수집호가인하관측',차이율:-2,관측횟수:2,확인시각:100}};
  const one={기회도:99,price_comparison:{상태:'관측비교',호가차이율:-10,표본수:5}};
  const none={기회도:50,price_comparison:{상태:'관측비교',호가차이율:4,표본수:5}};
  assert.deepEqual(Array.from(ctx._laPriceEvidenceTypes(overlap)),['같은 면적 실거래','현재 수집 호가 표본','같은 매물 호가 인하']);
  assert.ok(ctx._laCompareEvidenceOverlap(overlap,one)<0);
  assert.ok(ctx._laCompareEvidenceOverlap(one,none)<0);
  assert.match(html,/여러 가격 근거 확인순/);
  assert.match(signalV2,/이 매물의 가격 적정성이나 매수 권고가 아닙니다/);
  assert.match(html,/if\(_laSort==='evidence_overlap'\) return _laCompareEvidenceOverlap\(a,b\)/);
});

test('observed source-price drops sort separately and are not rendered as transaction prices', () => {
  const ctx=vm.createContext({_eok:value=>`${value}만원`});
  vm.runInContext(extract('function _laTradePriceEvidence(', 'function renderAllListings(){'),ctx);
  const older={기회도:5,price_reduction:{상태:'수집호가인하관측',차이율:-3,관측횟수:2,확인시각:100}};
  const larger={기회도:90,price_reduction:{상태:'수집호가인하관측',차이율:-8,관측횟수:2,확인시각:90}};
  const newer={기회도:5,price_reduction:{상태:'수집호가인하관측',차이율:-3,관측횟수:2,확인시각:200}};
  const unknown={기회도:100,price_reduction:null};
  assert.ok(ctx._laComparePriceReduction(larger,older)<0);
  assert.ok(ctx._laComparePriceReduction(newer,older)<0);
  assert.ok(ctx._laComparePriceReduction(older,unknown)<0);
  assert.match(html,/수집 호가 인하 확인순/);
  assert.match(signalV2,/item\.price_reduction/);
  assert.match(signalV2,/실제 거래가·현재 판매 여부·인하 이유는 확인되지 않았습니다/);
});

test('merged watch button preserves existing aliases and removes only that listing on explicit toggle', async () => {
  const saved=new Set(['찐매물:123','급매:123','일반매물:999']),calls=[];
  const button={dataset:{watchKey:'급매:123',watchAliases:'["급매:123","찐매물:123"]'},setAttribute(){}};
  const ctx=vm.createContext({_watchKeys:saved,_watchPendingKeys:new Set(),esc:String,EVENTS:{LISTING_WATCH_ADD:'watch'},track(){},toast(){},
    document:{querySelectorAll:()=>[button],getElementById:()=>({style:{display:'none'}})},
    fetch:async(url,options)=>{calls.push([url,options.method]);return {ok:true};}});
  vm.runInContext(extract('function _watchAliases(', 'async function saveWatchPriceTarget('),ctx);
  assert.match(ctx.watchBtn('급매:123',['찐매물:123']),/aria-pressed="true"/);
  await ctx.toggleListingWatch(button);
  assert.equal(calls.length,2);
  assert.ok(calls.every(([url,method])=>method==='DELETE'&&decodeURIComponent(url).endsWith(':123')));
  assert.deepEqual([...saved],['일반매물:999']);
  assert.equal(button.textContent,'☆ 찜하기');
  await ctx.toggleListingWatch(button);
  assert.equal(calls[2][1],'POST');
  assert.equal(button.textContent,'★ 찜함');
});
