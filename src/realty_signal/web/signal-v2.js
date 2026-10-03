/* Explainable market reports and user-led discovery. No automatic LLM calls. */
(() => {
  'use strict';
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const money = value => Number.isFinite(Number(value)) && Number(value) > 0
    ? `${(Number(value) / 10000).toFixed(2)}억` : '가격 미확인';
  const risks = {
    sale_weeks_incomplete:'최근 주간 가격 4주가 이어지지 않습니다',
    market_inputs_missing:'전세수급 또는 매수심리 자료가 없습니다',
    market_inputs_stale:'전세수급 또는 매수심리 기준일이 다릅니다',
    region_identity_ambiguous:'동명이 지역의 코드 매핑을 확인해야 합니다',
    source_stale:'지역 자료가 14일 넘게 갱신되지 않았습니다',
    price_direction_conflict:'매수 신호와 최근 가격 하락이 충돌합니다',
  };
  let listingGeneration = 0, regionGeneration = 0, discoveryGeneration = 0;
  let noteSubject = null, editingNote = null, notesCache = [];

  async function json(url, options) {
    const response = await fetch(url, options);
    if (!response.ok) throw new Error(response.status === 401 ? '로그인이 필요합니다.' :
      response.status === 403 ? '이 매물에 접근할 수 없습니다.' :
      response.status === 404 ? '현재 수집분에서 대상을 찾지 못했습니다.' :
      '자료를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.');
    return response.json();
  }

  function reason(reason) {
    const value = reason.value == null ? '자료 미확인' : `${esc(reason.value)}${reason.unit === '%/주' ? '%/주' : ''}`;
    const source = reason.inherited ? ` · ${esc(reason.source_region)} 권역 자료` : '';
    const threshold = reason.threshold == null ? '' : ` · 관찰선 ${esc(reason.threshold)}${reason.unit === '%/주' ? '%/주' : ''}`;
    const prior = reason.previous_value == null ? '' : ` · 이전 발행 ${esc(reason.previous_value)}${reason.unit === '%/주' ? '%/주' : ''}`;
    const verdict = reason.role === 'driver' ? (reason.passing ? ' · 조건 충족' : ' · 조건 미충족') : '';
    return `<div class="v2-row"><b>${esc(reason.label)}</b><br>${value}${threshold}${verdict}${source}${prior}</div>`;
  }

  async function paintRegion(region, regionId) {
    const generation = ++regionGeneration;
    const target = document.getElementById('haesolPanel');
    if (!target) return;
    target.dataset.reportRegion = region;
    try {
      const ref=regionId && regionId.startsWith('kb:') ? regionId : region;
      const report = await json(`/api/v2/regions/${encodeURIComponent(ref)}/report`);
      if (generation !== regionGeneration || target.dataset.reportRegion !== region) return;
      const a = report.assessment;
      const changed = a.change?.type;
      const changedLabels = (a.change?.changed_reasons || []).map(id =>
        id === 'grade' ? '등급' : id === 'safety_status' ? '자료 안전 상태' :
          a.reasons.find(r => r.reason_id === id)?.label || id);
      const change = changed === 'first_observation' ? '비교할 이전 발행 기록이 없습니다.' :
        changed === 'method_change' || changed === 'mixed_change'
          ? '계산 기준이 달라져 지난 판정과 단순 비교할 수 없습니다.' :
        changed === 'freshness_change' ? '시장 지표는 같지만 원천 갱신이 지연돼 현재 판정을 보류합니다.' :
        changed === 'market_change' || changed === 'source_revision'
          ? `이전 ${esc(a.change.previous_grade || '판정')}에서 ${changedLabels.map(esc).join(' · ')} 근거가 달라졌습니다.${changed === 'source_revision' ? ' 같은 기준일의 원천 수정입니다.' : ''}` :
          '이전 발행 판정에서 확인된 근거 변화가 없습니다.';
      target.innerHTML = `<section class="v2-panel" aria-label="지역 시그널 근거">
        <p class="v2-report-lead">${esc(a.summary)}</p>
        <p class="v2-muted">KB ${esc(report.asof)} 기준 · ${esc(a.scope_note)}</p>
        <h3>이번 판정의 근거</h3>${report.positive.length ? report.positive.slice(0,3).map(reason).join('') :
          '<p>충족된 강세 조건이 없거나 자료가 부족합니다.</p>'}
        <h3>반대 근거와 한계</h3>${report.cautions.length ? report.cautions.map(reason).join('') :
          '<p>현재 연결된 지표에서 별도 반대 근거를 확인하지 못했습니다. 위험이 없다는 뜻은 아닙니다.</p>'}
        ${report.unknowns.length ? `<p class="v2-row v2-caution">판단 보류 이유: ${report.unknowns.map(x => esc(risks[x] || x)).join(' · ')}</p>` : ''}
        <p class="v2-muted">${change}</p>
        ${a.assessment_status === 'held' ? `<details><summary>기존 규칙 산출값</summary><p>${esc(a.raw_grade)} · 검증되지 않아 현재 판정으로 쓰지 않습니다.</p></details>` : ''}
        <p class="v2-muted">매수우위지수 100은 KB의 응답 균형선입니다. 앱의 강세 조건 70은 별도 관찰 기준입니다.</p>
        <button type="button" class="btn" id="v2RegionDiscover">이 지역 매물 비교</button>
        <button type="button" class="btn" id="v2RegionNote">관심 이유 기록</button>
      </section>`;
      target.querySelector('#v2RegionDiscover').onclick = () => openDiscovery(region);
      target.querySelector('#v2RegionNote').onclick = () => openNote('region', region, report.report_id);
    } catch (_) {
      if (generation === regionGeneration && target.dataset.reportRegion === region) {
        const note = document.createElement('p');
        note.className = 'v2-muted';
        note.textContent = '구조화된 근거를 불러오지 못했습니다. 위 자료의 기준일을 확인해 주세요.';
        target.appendChild(note);
      }
    }
  }

  async function openListing(key) {
    if (!key) return;
    const generation = ++listingGeneration;
    const dialog = document.getElementById('v2ReportDlg');
    const body = document.getElementById('v2ReportBody');
    if (!dialog.open) dialog.showModal();
    body.textContent = '매물 근거를 확인하고 있습니다…';
    try {
      const report = await json(`/api/v2/listings/report?key=${encodeURIComponent(key)}`);
      if (generation !== listingGeneration || !dialog.open) return;
      const item = report.subject;
      const price = report.price || {};
      const fit = report.buyer_fit || {};
      const pros = (report.positive || []).map(x => `<div class="v2-row">${esc(x.text)}</div>`).join('');
      const cautions = (report.cautions || []).map(x => `<div class="v2-row v2-caution">${esc(x.text)}</div>`).join('');
      const evidence = (report.evidence || []).map(x =>
        `${esc(x.label)} · ${esc(x.asof || '기준일 미확인')} · ${esc(x.status)}`).join('<br>');
      body.innerHTML = `<h2>${esc(item.name || '매물')} · ${money(item.asking_manwon)}</h2>
        <p class="v2-muted">${esc(item.region)} · ${esc(item.kind)} · 수집 ${esc(item.collected_at || '시각 미확인')}${item.stale ? ' · 지난 수집 결과' : ''}</p>
        <p>${esc((report.lines || {}).cash || '자금 계산은 확인이 필요합니다.')}</p>
        <p>${esc((report.lines || {}).price || '현재 판매 여부와 실제 호가를 확인하세요.')}</p>
        <h3>가격 근거</h3><div class="v2-row">${esc(price['이유'] ||
          (price['상태'] === '관측비교' ? `동일 조건 실거래 ${price['표본수']}건과 비교했습니다.` :
            '비교 가능한 실거래가 부족합니다.'))}</div>
        <h3>장점</h3>${pros || '<p>확인된 장점이 없습니다. 비교 자료를 더 살펴보세요.</p>'}
        <h3>주의할 점</h3>${cautions || '<p>현재 확인된 자료에서 별도 주의 항목이 없습니다. 현장 상태는 확인이 필요합니다.</p>'}
        <h3>다음 확인</h3>${(report.next_actions || []).map(x => `<p>• ${esc(x)}</p>`).join('')}
        <details><summary>근거와 기준일</summary><p class="v2-muted">${evidence || '근거 기준일을 확인할 수 없습니다.'}</p></details>
        <p class="v2-muted">${fit.status === 'within' ? '호가가 저장된 가격 상한 안입니다. 대출 승인은 별도입니다.' :
          fit.status === 'above' ? '저장된 가격 상한을 넘습니다.' : '예산 적합성은 아직 확인되지 않았습니다.'}</p>
        <button type="button" class="btn" id="v2ListingNote">관심 이유 기록</button>
        <button type="button" class="btn" id="v2ListingCompare">비교함에 담기</button>
        <button type="button" class="btn" id="v2OpenCompare">비교함 열기</button>
        ${watchAction(item)}
        <button type="button" class="btn" id="v2ListingLink">내 계정에서 열기 링크 복사</button>
        <p class="v2-muted">이 링크는 현재 수집분을 다시 조회합니다. 지금 보이는 리포트의 고정 사본은 아닙니다.</p>
        <p id="v2ListingLinkStatus" class="v2-muted" role="status"></p>`;
      body.querySelector('#v2ListingNote').onclick = () => openNote('listing', key, report.report_id);
      body.querySelector('#v2ListingCompare').onclick = event => {
        event.currentTarget.textContent = addCompare(key);
      };
      body.querySelector('#v2OpenCompare').onclick = () => {
        dialog.close();
        listingCompareOpen();
      };
      body.querySelector('#v2ListingLink').onclick = () => copyListingLink(item.key);
    } catch (error) {
      if (generation === listingGeneration) body.textContent = error.message;
    }
  }

  async function copyListingLink(key) {
    const url = new URL(location.pathname, location.origin);
    url.searchParams.set('listing', key);
    url.hash = 'all';
    const status = document.getElementById('v2ListingLinkStatus');
    try {
      await navigator.clipboard.writeText(url.href);
      status.textContent = '링크를 복사했습니다. 같은 계정의 접근 권한이 있어야 열립니다.';
    } catch (_) {
      status.innerHTML = `<label>링크를 직접 복사하세요<input type="text" readonly value="${esc(url.href)}"></label>`;
      status.querySelector('input').select();
    }
  }

  function openInitialLink() {
    const key = new URLSearchParams(location.search).get('listing');
    if (!key) return;
    if (!/^(급매|찐매물|일반매물|청약|경매):.{1,180}$/.test(key)) return;
    openListing(key);
  }

  document.getElementById('v2ReportDlg')?.addEventListener('close', () => {
    const url = new URL(location.href);
    if (!url.searchParams.has('listing')) return;
    url.searchParams.delete('listing');
    history.replaceState(history.state, '', url.pathname + url.search + url.hash);
  });
  document.getElementById('onbDlg')?.addEventListener('close', openInitialLink);

  function openDiscovery(region = '') {
    discoveryGeneration++;
    const dialog = document.getElementById('v2DiscoverDlg');
    const form = document.getElementById('v2DiscoverForm');
    if (region) form.elements.region.value = region;
    if (!dialog.open) dialog.showModal();
    document.getElementById('v2DiscoverResults').textContent = '조건을 입력하면 현재 수집된 매물을 비교합니다.';
    form.elements.max_price_manwon.focus();
  }

  function addCompare(key) {
    if (typeof _listingCompareKeys !== 'function' || typeof _listingCompareSave !== 'function')
      return '비교 기능을 사용할 수 없습니다';
    const keys = _listingCompareKeys();
    if (keys.includes(key)) return '이미 비교함에 있습니다';
    if (keys.length >= 3) return '비교함은 최대 3개입니다';
    _listingCompareSave([...keys, key]);
    return `비교함에 담았습니다 · ${keys.length + 1}/3`;
  }

  function openNote(type, key, reportId) {
    noteSubject = {type, key, reportId};
    editingNote = null;
    const dialog = document.getElementById('v2NoteDlg');
    const form = document.getElementById('v2NoteForm');
    form.style.display = '';
    form.reset();
    document.getElementById('v2NoteStatus').textContent = '';
    if (!dialog.open) dialog.showModal();
    form.elements.thesis.focus();
    refreshNotes();
  }

  function openNotes() {
    noteSubject = null;
    editingNote = null;
    const dialog = document.getElementById('v2NoteDlg');
    document.getElementById('v2NoteForm').style.display = 'none';
    document.getElementById('v2NoteStatus').textContent = '';
    if (!dialog.open) dialog.showModal();
    refreshNotes();
  }

  async function refreshNotes() {
    const list = document.getElementById('v2NoteList');
    list.textContent = '저장한 기록을 불러오는 중…';
    try {
      const search = noteSubject ? '?' + new URLSearchParams({subject_type:noteSubject.type,
        subject_key:noteSubject.key}) : '';
      const data = await json('/api/v2/decision-notes' + search);
      notesCache = data.notes || [];
      list.innerHTML = `<h3>저장한 판단 · ${notesCache.length}건</h3>` +
        (notesCache.length ? notesCache.map(n => `<div class="v2-row">
          <b>${esc(n.subject_key)} · ${n.horizon_weeks}주 뒤 복기</b>
          <p>${esc(n.thesis)}</p><p class="v2-muted">다시 생각할 조건: ${esc(n.counter_condition)}</p>
          <button type="button" class="btn" data-note-edit="${n.id}">수정</button>
          <button type="button" class="btn" data-note-delete="${n.id}">삭제</button>
        </div>`).join('') : '<p class="v2-muted">아직 기록이 없습니다.</p>');
      list.querySelectorAll('[data-note-edit]').forEach(button => button.onclick = () => {
        const n = notesCache.find(x => x.id === Number(button.dataset.noteEdit));
        if (!n) return;
        editingNote = n;
        noteSubject = {type:n.subject_type,key:n.subject_key,reportId:n.report_id};
        const form = document.getElementById('v2NoteForm');
        form.style.display = '';
        form.elements.thesis.value = n.thesis;
        form.elements.counter_condition.value = n.counter_condition;
        form.elements.horizon_weeks.value = String(n.horizon_weeks);
        form.elements.thesis.focus();
      });
      list.querySelectorAll('[data-note-delete]').forEach(button => button.onclick = async () => {
        const id = Number(button.dataset.noteDelete);
        if (!confirm('이 판단 기록을 삭제할까요?')) return;
        try {
          await json(`/api/v2/decision-notes/${id}`, {method:'DELETE'});
          refreshNotes();
        } catch (error) {
          document.getElementById('v2NoteStatus').textContent = error.message;
        }
      });
    } catch (error) {
      list.textContent = error.message;
    }
  }

  async function saveNote(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const status = document.getElementById('v2NoteStatus');
    if (!noteSubject) return;
    const payload = {subject_type:noteSubject.type, subject_key:noteSubject.key,
      report_id:noteSubject.reportId, thesis:form.elements.thesis.value,
      counter_condition:form.elements.counter_condition.value,
      horizon_weeks:Number(form.elements.horizon_weeks.value)};
    status.textContent = '저장 중…';
    try {
      const url = editingNote ? `/api/v2/decision-notes/${editingNote.id}` : '/api/v2/decision-notes';
      if (editingNote) payload.revision = editingNote.revision;
      await json(url, {method:editingNote ? 'PATCH' : 'POST',
        headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
      status.textContent = '관심 이유를 저장했습니다.';
      editingNote = null;
      form.reset();
      refreshNotes();
    } catch (error) {
      status.textContent = error.message;
    }
  }

  function discoveryCard(x) {
    const finance = x.finance;
    const preference = x.preference || {};
    const preferred = (preference.details || []).filter(p => p.status === 'matched').map(p => esc(p.label));
    const unknownPreference = (preference.details || []).filter(p => p.status === 'unknown').map(p => esc(p.label));
    const monthly = finance && Number.isFinite(Number(finance.monthly_manwon))
      ? `${esc(finance.monthly_manwon)}만원` : '미계산';
    const cash = finance && Number.isFinite(Number(finance.cash_manwon))
      ? `${esc(finance.cash_manwon)}만원` : '미계산';
    return `<div class="v2-row" data-v2-card><b>${esc(x.listing.name || '이름 미확인')}</b> · ${money(x.listing.asking_manwon)}
      <p>${esc(x.listing.region)} · ${esc(x.listing.kind)}</p>
      ${finance ? `<p>자금 참고 계산: 총 월 상환 약 ${monthly} · 필요현금 약 ${cash}</p><p>${esc(finance.reason)}</p>` : ''}
      <p>${x.eligibility === 'verify' ? '확인된 점' : x.eligibility === 'explore' ? '탐색 단서' : '추천 이유'}: ${esc(x.recommendation_reason)}</p>
      ${preference.total ? `<p>선호 ${preference.satisfied}/${preference.total}개 충족 · ${preference.known}/${preference.total}개 자료 확인${preferred.length ? ` · 부합: ${preferred.join('·')}` : ''}${unknownPreference.length ? ` · 미확인: ${unknownPreference.join('·')}` : ''}</p>` : ''}
      <p>양보할 점: ${esc(x.tradeoff)}</p><p>확인할 점: ${esc(x.verify_next)}</p>
      <button type="button" class="btn" data-v2-listing="${esc(x.listing.key)}">리포트 보기</button>
      <button type="button" class="btn" data-v2-compare="${esc(x.listing.key)}">비교함 담기</button>
      ${watchAction(x.listing)}</div>`;
  }

  function watchAction(listing) {
    return listing?.key && listing.name && typeof watchBtn === 'function' ? watchBtn(listing.key) : '';
  }

  async function runDiscovery(cursor = null) {
    const generation = ++discoveryGeneration;
    const form = document.getElementById('v2DiscoverForm');
    const result = document.getElementById('v2DiscoverResults');
    const spec = {};
    for (const field of ['max_price_manwon', 'min_area_m2', 'max_monthly_manwon',
                         'prefer_max_price_manwon', 'prefer_min_area_m2', 'region']) {
      const value = form.elements[field].value.trim();
      if (value) spec[field === 'region' && form.elements.region_mode.value === 'prefer' ?
        'prefer_region' : field] = field === 'region' ? value : Number(value);
    }
    if (cursor) spec.cursor = cursor;
    if (!cursor) result.textContent = '조건에 맞는 후보를 확인하고 있습니다…';
    else result.querySelector('[data-v2-next]')?.setAttribute('disabled', '');
    try {
      const response = await fetch('/api/v2/discovery', {
        method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(spec),
      });
      if (response.status === 409) throw new Error('수집 결과가 바뀌었습니다. 다시 후보 찾기를 눌러 주세요.');
      if (response.status === 422 && cursor) throw new Error('검색 조건이 바뀌었습니다. 다시 후보 찾기를 눌러 주세요.');
      if (!response.ok) throw new Error('후보를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.');
      const data = await response.json();
      if (generation !== discoveryGeneration || !document.getElementById('v2DiscoverDlg').open) return;
      if (!data.private_access) {
        result.textContent = '일반·급매 매물은 지정된 개인 계정에서만 보입니다.';
        return;
      }
      if (!cursor) {
        const regions = data.coverage?.regions || [];
        const labels = {ready:'정상',empty:'조회 0건',partial:'일부 제한',partial_empty:'일부 실패',
          stale:'지난 수집',stale_failed:'갱신 실패',unverified:'검증 대기',failed:'조회 실패',never_scanned:'미수집'};
        const sources = (data.sources || []).map(s => `${esc(s.kind)} ${labels[s.state] || '상태 미확인'}`).join(' · ');
        const warning = data.source_state === 'unavailable' ? '아직 수집된 원천이 없습니다. 전국 매물 0건이라는 뜻은 아닙니다.' :
          data.source_state === 'partial_empty' ? '원천 일부가 실패하거나 제한돼 0건을 확정할 수 없습니다.' :
          data.source_state === 'partial' ? '일부 원천이 실패·제한됐습니다. 아래 후보는 전체 시장이 아닙니다.' :
          data.source_state === 'empty' ? '이번 수집분은 0건입니다. 전체 시장의 매물 수는 아닙니다.' : '';
        const requested = spec.region || spec.prefer_region;
        const outside = requested && regions.length && !regions.includes(requested) ?
          `<p class="v2-muted">${esc(requested)}은(는) 현재 확인된 수집 지역에 없습니다.</p>` : '';
        const financeNotice = spec.max_monthly_manwon ?
          data.finance_context?.status === 'profile_unavailable' ? '저장된 매수력을 지금 읽지 못했습니다. 월 부담은 판정하지 않습니다.' :
          data.finance_context?.status !== 'ready' ? '매수력을 설정·확정해야 월 부담을 계산할 수 있습니다.' :
          data.finance_context?.policy_status !== 'verified' ? `대출 규제·세율 최신성이 검증되지 않았습니다(${esc(data.finance_context?.policy_declared_asof || '기준일 미확인')} 기준 가정). 월 부담은 참고용이며 후보는 확인 필요로 분류됩니다.` :
          '월 부담은 입력 가정의 추정치이며 대출 승인이 아닙니다.' : '';
        const hasPreference = !!(spec.prefer_region || spec.prefer_max_price_manwon || spec.prefer_min_area_m2);
        result.innerHTML = `<p class="v2-muted">현재 수집된 ${regions.length}개 지역 · ${sources || '원천 상태 미확인'} · 전체 시장 아님</p>
          <p class="v2-muted">정렬: ${hasPreference ? '입력한 선호 충족·자료 확인도 → 수집일' : '최근 수집일'} · 호가가 저렴하다는 이유만으로 우선하지 않습니다.</p>
          ${warning ? `<p class="v2-row v2-caution">${warning}</p>` : ''}
          ${financeNotice ? `<p class="v2-row v2-caution">${financeNotice}</p>` : ''}
          ${spec.max_monthly_manwon && data.finance_context?.status !== 'ready' && data.finance_context?.status !== 'profile_unavailable' ?
            '<button type="button" class="btn" data-v2-finance-setup>매수력 설정으로 이동</button>' : ''}${outside}
          <div id="v2DiscoveryGroups"></div><div id="v2DiscoveryPaging"></div>`;
      }
      const groupHost = result.querySelector('#v2DiscoveryGroups');
      const setup = result.querySelector('[data-v2-finance-setup]');
      if (setup) setup.onclick = () => { document.getElementById('v2DiscoverDlg').close(); switchTab('mypage'); };
      const sections = [['matched','조건 부합'],['verify','확인 필요'],['explore','탐색 후보']];
      for (const [name,label] of sections) {
        const rows = data.groups[name] || [];
        if (!rows.length) continue;
        let section = groupHost.querySelector(`[data-v2-group="${name}"]`);
        if (!section) {
          section = document.createElement('section');
          section.dataset.v2Group = name;
          section.innerHTML = `<h3>${label} · ${data.counts[name]}건</h3><div data-v2-items></div>`;
          groupHost.appendChild(section);
        }
        section.querySelector('[data-v2-items]').insertAdjacentHTML('beforeend', rows.map(discoveryCard).join(''));
      }
      if (!groupHost.querySelector('[data-v2-card]') && data.source_state !== 'unavailable' && data.source_state !== 'partial_empty')
        groupHost.innerHTML = '<p>현재 조건의 후보가 없습니다. 가격·면적·지역을 하나씩 조정해 보세요.</p>';
      const total = (data.counts.matched || 0) + (data.counts.verify || 0) + (data.counts.explore || 0);
      const shown = groupHost.querySelectorAll('[data-v2-card]').length;
      const paging = result.querySelector('#v2DiscoveryPaging');
      paging.innerHTML = `<p class="v2-muted">현재 수집분 ${total}건 중 ${shown}건 표시</p>`;
      if (data.next_cursor) {
        const more = document.createElement('button');
        more.type = 'button'; more.className = 'btn'; more.dataset.v2Next = '';
        more.textContent = '다음 후보 더 보기';
        more.onclick = () => runDiscovery(data.next_cursor);
        paging.appendChild(more);
      }
      result.querySelectorAll('[data-v2-listing]').forEach(button => button.onclick = () => {
        document.getElementById('v2DiscoverDlg').close();
        openListing(button.dataset.v2Listing);
      });
      result.querySelectorAll('[data-v2-compare]').forEach(button => button.onclick = () => {
        button.textContent = addCompare(button.dataset.v2Compare);
      });
      if (_listingCompareKeys().length >= 2) {
        const button = result.querySelector('[data-v2-open-compare]') || document.createElement('button');
        button.className = 'btn primary';
        button.dataset.v2OpenCompare = '';
        button.textContent = '선택 매물 비교하기';
        button.onclick = () => { document.getElementById('v2DiscoverDlg').close(); listingCompareOpen(); };
        if (!button.isConnected) result.prepend(button);
      }
    } catch (error) {
      if (generation === discoveryGeneration) {
        if (cursor) {
          const paging = result.querySelector('#v2DiscoveryPaging');
          if (paging) paging.textContent = error.message;
        }
        else result.textContent = error.message;
      }
    }
  }

  document.getElementById('v2DiscoverForm')?.addEventListener('submit', event => {event.preventDefault();runDiscovery();});
  document.getElementById('v2NoteForm')?.addEventListener('submit', saveNote);
  window.SignalV2 = {paintRegion, openListing, openDiscovery, openNote, openNotes, openInitialLink};
  if (window._signalAppReady && !document.getElementById('onbDlg')?.open) openInitialLink();
  if (typeof selected === 'string' && selected) paintRegion(selected);
})();
