const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../src/realty_signal/web/index.html'), 'utf8');
const extract = (start, end) => html.slice(html.indexOf(start), html.indexOf(end));

test('inline app scripts parse and browse navigation keeps one list and the signal map', () => {
  const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map(m => m[1]);
  scripts.filter(Boolean).forEach(script => assert.doesNotThrow(() => new vm.Script(script)));
  const targets = {groupSubLabel: {}, groupSubTabs: {}, listingFocus: {style: {}}};
  const ctx = vm.createContext({document: {getElementById: id => targets[id]}, location: {hash: '#all'}});
  vm.runInContext(extract('const _GROUPS={', 'const _LOAD={'), ctx);
  ctx.renderGroupSubnav('browse', 'all');
  assert.match(targets.groupSubTabs.innerHTML, /data-sub="all"/);
  assert.match(targets.groupSubTabs.innerHTML, /data-sub="signal"/);
  assert.ok(targets.groupSubTabs.innerHTML.indexOf('data-sub="signal"') < targets.groupSubTabs.innerHTML.indexOf('data-sub="all"'));
  assert.doesNotMatch(targets.groupSubTabs.innerHTML, /분석·전략|경매·재건축|data-sub="auction"|data-sub="report"/);
  ctx.renderGroupSubnav('browse', 'auction');
  assert.match(targets.groupSubTabs.innerHTML, /data-sub="all"/);
  assert.doesNotMatch(targets.groupSubTabs.innerHTML, /data-sub="auction"|data-sub="report"/);
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
  const row={style:{},querySelector:()=>({set onclick(fn){this.handler=fn;row.favoriteClick=fn}})};
  const ctx=vm.createContext({
    document:{createElement:()=>row}, selected:null, _SIGC:{HELD:'#aaa'},
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
    meta:null, allSignals:[], selected:null, active:new Set(['BUY']), window:{},
    displaySignal:r=>r?.display_signal||r?.signal||'HELD',
    localStorage:{getItem:k=>saved.get(k)||null,setItem:(k,v)=>saved.set(k,v)},
    document:{getElementById:id=>{
      if(!elements.has(id)) elements.set(id,{style:{},textContent:'',innerHTML:''});
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

test('stale weekly observations do not appear as this week signal changes', async () => {
  const wrap = {innerHTML:''}, events = [];
  let payload = {ready:true, as_of:'2026-09-21', stale_days:12,
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
  vm.runInContext(extract('function safeRadarSignal(listing){', 'function _mtPass(region){'), ctx);
  assert.equal(ctx.safeRadarSignal({지역: '중구', 시도: '인천', 시그널: ''}), 'HELD');
  assert.equal(ctx.safeRadarSignal({지역: '중구', 시도: '서울', 시그널: 'BUY'}), 'STRONG_BUY');
});

test('quicksale empty view names filter and source states separately', () => {
  const empty = {textContent: ''}, gap = {value: '0'};
  const ctx = vm.createContext({
    document: {getElementById: id => id === 'qsGap' ? gap : empty},
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
  gap.value = '-5';
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
  assert.match(html, /시그널<\/b> — 이번 주 동네 색깔이 지도에 있고, 매물 목록은 그 옆입니다/);
  assert.match(html, /id="sigMap"/);
  assert.match(html, /onclick="switchTab\('signal'\)"[^>]*>시그널</);
});
