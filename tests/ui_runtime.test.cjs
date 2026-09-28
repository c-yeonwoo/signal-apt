const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../src/realty_signal/web/index.html'), 'utf8');
const extract = (start, end) => html.slice(html.indexOf(start), html.indexOf(end));

test('opening quicksale reaches both APIs and renders', async () => {
  const calls = [], status = {}, region = {options: [0, 1]};
  const ctx = vm.createContext({
    document: {getElementById: id => id === 'qsRegion' ? region : status},
    fetch: async url => {calls.push(url); return {json: async () => ({ready: true, listings: []})};},
    _qsMode: '급매', qsStatusText: () => '0건', renderQuicksale: () => calls.push('render'),
  });
  vm.runInContext(extract('async function loadQuicksale(){', 'async function refreshQuicksale(){'), ctx);
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
});

test('quicksale empty view names filter and source states separately', () => {
  const empty = {textContent: ''}, gap = {value: '0'};
  const ctx = vm.createContext({
    document: {getElementById: id => id === 'qsGap' ? gap : empty},
    renderMtFilter() {}, inFocus: () => true, _mtPass: () => true, mapSplit() {},
    _qsMode: '급매', _qsList: [], _qsCertList: [], _qsData: {급매: {state: 'failed'}},
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
  vm.runInContext(extract('function _ccListingCard(', 'async function loadConclusion(){'), ctx);
  const eok = n => n == null ? '–' : `${n}만`;
  const auction = ctx._ccListingCard({유형: '경매', 단지명: '시험단지', 지역: '노원구',
    총액: 50000, 입찰상태: 'needs_review', 예산확인필요: true,
    decision: {feasibility: 'unknown', unknowns: ['권리 확인'], next_action: '권리 확인 후 검토'}}, eok);
  assert.match(auction, /<details class="cc-listing"/);
  assert.match(auction, /입찰 보류/);
  assert.match(auction, /최저매각가 · 취득비용 별도/);
  assert.doesNotMatch(auction, /예산초과·참고|<div onclick="switchTab/);

  const estimated = ctx._ccListingCard({유형: '재건축', 단지명: '<가짜>', 지역: '노원구',
    추정가: 60000, 가격출처: '지역평단추정', 예산확인필요: true,
    decision: {feasibility: 'unknown', unknowns: ['현장 확인']}}, eok);
  assert.match(estimated, /가격·자금 확인 필요/);
  assert.match(estimated, /지역평단 추정 · 매물가 아님/);
  assert.match(estimated, /&lt;가짜>/);
});
