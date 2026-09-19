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
