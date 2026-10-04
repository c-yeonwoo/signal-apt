/* Explainable market reports and user-led discovery. No automatic LLM calls. */
(() => {
  'use strict';
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const money = value => Number.isFinite(Number(value)) && Number(value) > 0
    ? `${(Number(value) / 10000).toFixed(2)}억` : '가격 미확인';
  const gapMoney = value => Number(value) === 0 ? '0원' :
    Number(value) < 10000 ? `${Math.round(Number(value)).toLocaleString('ko-KR')}만 원` : money(value);
  const enabled = name => window._featureFlags?.[name] !== false;
  const risks = {
    sale_weeks_incomplete:'최근 주간 가격 4주가 이어지지 않습니다',
    market_inputs_missing:'전세수급 또는 매수심리 자료가 없습니다',
    market_inputs_stale:'전세수급 또는 매수심리 기준일이 다릅니다',
    region_identity_ambiguous:'동명이 지역의 코드 매핑을 확인해야 합니다',
    region_boundary_obsolete:'행정구역 개편 전 자료라 현재 지역의 매수·매도 판정에 사용할 수 없습니다',
    source_stale:'KB 관측 기준일이 8일을 넘어서 현재 판정을 보류합니다',
    price_direction_conflict:'매수 신호와 최근 가격 하락이 충돌합니다',
  };
  const historicalGrades = {
    STRONG_BUY:'강력매수', BUY:'매수', WATCH:'관망', NEUTRAL:'중립',
    SELL_RISK:'매도주의', SELL:'매도',
  };
  const riskLabel = code => Object.hasOwn(risks, code) ? risks[code] : '확인되지 않은 보류 사유가 있습니다';
  const historicalGradeLabel = code => Object.hasOwn(historicalGrades, code) ? historicalGrades[code] : '표시 불가';
  let listingGeneration = 0, regionGeneration = 0, discoveryGeneration = 0, discoveryRegionGeneration = 0;
  let discoveryReturn = null;
  let noteSubject = null, editingNote = null, notesCache = [];
  let noteList = {generation:0, next_cursor:null, loading:false};
  let savedReportList = {key:null, items:[], next_cursor:null, policy:null};

  async function json(url, options) {
    const response = await fetch(url, options);
    if (!response.ok) {
      const inputError = response.status === 422 && url.startsWith('/api/v2/decision-notes')
        ? (await response.json().catch(() => ({}))).detail : null;
      throw new Error(typeof inputError === 'string' && inputError.trim() ? inputError : response.status === 401 ? '로그인이 필요합니다.' :
      response.status === 403 ? '이 매물에 접근할 수 없습니다.' :
      response.status === 404 ? '현재 수집분에서 대상을 찾지 못했습니다.' :
      response.status === 409 ? '자료나 내 조건이 바뀌었습니다. 리포트를 다시 열어 주세요.' :
      response.status === 429 ? '오늘 AI 설명 요청 한도에 도달했습니다. 기본 근거는 계속 볼 수 있습니다.' :
      '자료를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.');
    }
    return response.json();
  }

  function reason(reason) {
    const value = reason.value == null ? '자료 미확인' : `${esc(reason.value)}${reason.unit === '%/주' ? '%/주' : ''}`;
    const source = reason.inherited ? ` · ${esc(reason.source_region)} 권역 자료` : '';
    const threshold = reason.threshold == null ? '' : ` · 관찰선 ${esc(reason.threshold)}${reason.unit === '%/주' ? '%/주' : ''}`;
    const prior = reason.previous_value == null || String(reason.previous_value) === String(reason.value)
      ? '' : ` · 이전 발행 ${esc(reason.previous_value)}${reason.unit === '%/주' ? '%/주' : ''}`;
    const verdict = reason.role === 'driver' ? (reason.passing ? ' · 조건 충족' : ' · 조건 미충족') : '';
    return `<div class="v2-row"><b>${esc(reason.label)}</b><br>${value}${threshold}${verdict}${source}${prior}</div>`;
  }

  function briefReason(item, includeComparison = false) {
    if (!item) return '';
    const unit = item.unit === '%/주' ? '%/주' : item.unit ? ` ${esc(item.unit)}` : '';
    const value = item.value == null ? '자료 미확인' : `${esc(item.value)}${unit}`;
    const threshold = item.threshold == null ? '' : ` · 기준 ${esc(item.threshold)}${unit}`;
    const prior = !includeComparison || item.previous_value == null || String(item.previous_value) === String(item.value)
      ? '' : ` · 이전 ${esc(item.previous_value)}${unit}`;
    const status = item.role === 'driver' ? (item.passing ? '충족' : '미충족') : '';
    return `<div class="signal-assessment-fact"><b>${esc(item.label)}</b><span>${value}${threshold}${prior}${status ? ` · ${status}` : ''}</span></div>`;
  }

  function listingQuickAnswer(report, question) {
    const lines = report.lines || {};
    if (question === 'price') return {
      title:'가격이 싼가?',
      answer: report.price?.['이유'] || lines.price ||
        (report.price?.['상태'] === '관측비교' && Number(report.price?.['표본수']) > 0
          ? `동일 조건 실거래 ${report.price['표본수']}건과 비교했습니다. 이 숫자만으로 싸다고 확정할 수 없습니다.`
          : '비교 가능한 동일 조건 실거래가 부족해 싸다고 판단할 수 없습니다.'),
      limit:'실거래 비교는 실제 계약 가격이나 현재 판매 가능 여부를 보증하지 않습니다.',
    };
    if (question === 'budget') return {
      title:'내 예산에 맞나?',
      answer:(report.partial_failures || []).includes('buyer_profile_unavailable')
        ? '내 자금 프로필을 읽지 못해 예산 적합성을 보류했습니다.'
        : lines.cash || report.buyer_fit?.reason || '저장된 자금 조건이 없어 비교할 수 없습니다.',
      limit:'호가와 저장한 가정의 비교이며 대출 승인·세금·수리비 확인 전입니다.',
    };
    if (question === 'risk') return {
      title:'뭘 조심해야 하나?',
      answer:report.cautions?.[0]?.text || report.unknowns?.[0] ||
        '이 자료에서 별도 주의 항목을 확인하지 못했습니다. 위험이 없다는 뜻은 아닙니다.',
      limit:'아래 주의할 점과 다음 확인 항목을 함께 살펴보세요.',
    };
    return null;
  }

  function listingFieldLinks(item) {
    const query = [item.region, item.name].filter(Boolean).join(' ').trim();
    const search = terms => `https://search.naver.com/search.naver?query=${encodeURIComponent(terms)}`;
    const naverNo = /^[1-9][0-9]{0,11}$/.test(String(item.naver_complex_no || ''))
      ? String(item.naver_complex_no) : null;
    const naver = naverNo ? `https://new.land.naver.com/complexes/${naverNo}` :
      search(`${query} 아파트 매매 네이버 부동산`);
    const link = (href, label, note) => `<a class="v2-field-link" href="${esc(href)}" target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer"><b>${esc(label)} ↗</b><small>${esc(note)}</small></a>`;
    return `<section class="v2-field-check" aria-label="사진 거리뷰 후기 현장 확인">
      <h3>실제 모습과 거주 경험 보기</h3>
      <div class="v2-field-grid">
        ${link(naver, naverNo ? '네이버 부동산 · 매물·사진' : '네이버에서 매물·사진 찾기', naverNo ? '단지 페이지 · 같은 매물인지 다시 확인' : '단지명 검색 · 정확한 매물 연결은 미확인')}
        ${query ? link(`https://map.naver.com/p/search/${encodeURIComponent(query)}`, '네이버 지도 · 거리뷰', '검색 결과에서 거리뷰를 선택 · 출입구 확인') : ''}
        ${query ? link(search(`site:hogangnono.com ${query} 후기`), '호갱노노 · 거주 후기', '후기 검색 · 작성 시점과 단지를 확인') : ''}
        ${query ? link(search(`site:asil.kr ${query} 단지톡`), '아실 · 단지톡', '후기 검색 · 작성 시점과 단지를 확인') : ''}
      </div>
      <p class="v2-muted">외부 사이트에서 단지·동·호수가 같은지 확인하세요. 후기와 사진은 분석 점수에 넣지 않습니다.</p>
    </section>`;
  }

  function listingPriceSummary(price, failures, stale) {
    if (stale) return '<b>현재 호가인지 먼저 확인하세요</b><p class="v2-muted">지난 수집분의 비교 결과는 아래 상세 근거에 남겨두었어요.</p>';
    const diff = Number(price?.['호가차이율']);
    const count = Number(price?.['표본수']);
    if (price?.['상태'] === '관측비교' && price['호가차이율'] != null && Number.isFinite(diff) && Number.isFinite(count) && count > 0) {
      const verdict = diff < 0 ? `비슷한 조건의 최근 거래보다 ${Math.abs(diff).toLocaleString('ko-KR',{maximumFractionDigits:1})}% 낮아요` :
        diff > 0 ? `비슷한 조건의 최근 거래보다 ${diff.toLocaleString('ko-KR',{maximumFractionDigits:1})}% 높아요` : '비슷한 조건의 최근 거래와 비슷해요';
      return `<b>${esc(verdict)}</b><p class="v2-muted">같은 단지·전용면적 거래 ${esc(count)}건 기준${price['비교기준일'] ? ` · ${esc(price['비교기준일'])}` : ''}. 층 조건과 실제 계약가는 상세 근거에서 확인하세요.</p>`;
    }
    return `<b>아직 싼 매물인지 판단할 수 없어요</b><p class="v2-muted">이 매물에 맞는 가격 비교 결과가 없습니다. 이유는 아래 상세 근거에서 볼 수 있어요.</p>
      ${(failures || []).includes('trade_cache_unavailable') ? '<button type="button" class="btn" id="v2RefreshTrades">실거래 다시 확인</button><p id="v2RefreshTradesStatus" class="v2-muted" role="status"></p>' : ''}`;
  }

  function listingBudgetSummary(fit, failures) {
    if ((failures || []).includes('buyer_profile_unavailable'))
      return '<b>내 예산 정보를 지금 불러오지 못했어요</b><p class="v2-muted">잠시 후 다시 열어 주세요. 가격 비교는 위에서 따로 확인할 수 있어요.</p>';
    const gap = Number(fit?.gap_manwon);
    if (fit?.status === 'within' && fit.gap_manwon != null && Number.isFinite(gap) && gap >= 0)
      return `<b>저장한 매수력 상한 안이에요</b><p class="v2-muted">상한까지 ${esc(gapMoney(gap))} 남습니다. 취득세·수리비·대출 승인은 별도 확인이 필요해요.</p>`;
    if (fit?.status === 'above' && fit.gap_manwon != null && Number.isFinite(gap) && gap < 0)
      return `<b>저장한 매수력 상한을 넘어요</b><p class="v2-muted">상한보다 ${esc(gapMoney(Math.abs(gap)))} 높습니다. 자금 계획을 다시 확인하세요.</p>`;
    return '<b>내 예산과는 아직 비교하지 않았어요</b><p class="v2-muted">자금 조건을 확정하면 호가와 비교할 수 있습니다.</p><button type="button" class="btn" id="v2BudgetSetup">내 자금 확인</button>';
  }

  function showReportDrawer() {
    const legacyPanel = document.getElementById('advPanel');
    if (legacyPanel?.style.display === 'flex') {
      if (typeof window.clearListingReportContext === 'function') window.clearListingReportContext();
      legacyPanel.style.display = 'none';
    }
    const dialog = document.getElementById('v2ReportDlg');
    if (!dialog.open) dialog.show();
    return dialog;
  }

  function revealCurrentListingMap(item) {
    let tab = document.body.className.match(/(?:^|\s)tab-(all|general|quicksale|auction)(?:\s|$)/)?.[1];
    if (!tab && item && typeof window.switchTab === 'function') {
      tab = {일반매물:'general',급매:'quicksale',찐매물:'quicksale',경매:'auction'}[item.kind] || 'all';
      window.switchTab(tab);
    }
    if (!window.matchMedia('(max-width:600px)').matches) return;
    const mapId = {all:'laMap',general:'hbMap',quicksale:'qsMap',auction:'auctionMap'}[tab];
    const map = mapId && document.getElementById(mapId);
    if (map?.getClientRects().length) {
      document.body.classList.add('v2-report-map');
      requestAnimationFrame(() => window.dispatchEvent(new Event('resize')));
    }
  }

  function explanationHtml(result, report) {
    const labels = new Map(report.type === 'region'
      ? (report.assessment?.reasons || []).map(item => [item.reason_id, item.label || item.reason_id])
      : report.type === 'listing'
        ? (report.evidence || []).map(item => [item.id, item.label || item.id])
        : (report.items || []).map((item, index) => [`item_${index}`, item.listing?.name || '비교 매물']));
    return `<div class="v2-row"><b>${result.source === 'model_validated' ? '근거를 쉽게 풀어봤어요' : '기본 근거 설명'}</b>
      ${result.source === 'model_validated' ? '' : '<p class="v2-muted">검증된 추가 설명 대신 현재 리포트의 근거만 정리했습니다.</p>'}
      <p>${esc(result.summary || '')}</p>
      ${(result.claims || []).map(claim => `<p>${esc(claim.text)} <small class="v2-muted">근거: ${claim.evidence_ids.map(id => esc(labels.get(id) || id)).join(' · ')}</small></p>`).join('')}
      ${(result.cautions || []).map(item => `<p class="v2-caution">확인: ${esc(item)}</p>`).join('')}
      <p class="v2-muted">${esc(result.limit || '')}</p></div>`;
  }

  async function askExplanation(report, payload, target, isCurrent) {
    if (!target || !isCurrent()) return;
    if (!enabled('contextual_explanations_enabled')) {
      target.textContent = '추가 AI 설명은 잠시 중지됐습니다. 기본 근거 리포트는 계속 볼 수 있습니다.';
      return;
    }
    target.textContent = '선택한 리포트의 근거를 확인해 설명을 준비하고 있습니다…';
    const options = {method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({...payload, mode:payload.mode || 'easy'})};
    try {
      let job = await json(`/api/v2/reports/${encodeURIComponent(report.report_id)}/explanations`, options);
      for (const delay of [2000,5000,10000,10000,10000,10000]) {
        if (!isCurrent()) return;
        if (document.hidden) {
          target.textContent = '화면을 다시 볼 때 설명 버튼을 누르면 작업 결과를 이어서 확인합니다.';
          return;
        }
        if (job.status === 'succeeded' || job.status === 'failed') break;
        await new Promise(resolve => setTimeout(resolve, delay));
        if (!isCurrent()) return;
        if (document.hidden) {
          target.textContent = '화면을 다시 볼 때 설명 버튼을 누르면 작업 결과를 이어서 확인합니다.';
          return;
        }
        job = await json(`/api/v2/report-jobs/${encodeURIComponent(job.job_id)}`);
      }
      if (!isCurrent()) return;
      target.innerHTML = job.result ? explanationHtml(job.result, report) :
        '<p class="v2-muted">설명을 계속 준비 중입니다. 잠시 뒤 다시 눌러 상태를 확인해 주세요. 기본 리포트는 그대로 사용할 수 있습니다.</p>';
    } catch (error) {
      if (isCurrent()) target.textContent = `${error.message} 기본 근거 리포트는 계속 볼 수 있습니다.`;
    }
  }

  async function paintRegion(region, regionId) {
    const generation = ++regionGeneration;
    const target = document.getElementById('haesolPanel');
    if (!target) return;
    target.dataset.reportRegion = region;
    if (!enabled('report_v2_enabled')) {
      target.innerHTML = '<p class="v2-row v2-caution">독립 근거 리포트가 일시 중지됐습니다. 위 지역 배지와 출처 기준일만 참고하고, 재개 후 근거·반대 근거를 확인해 주세요.</p>';
      return;
    }
    try {
      const ref=regionId && regionId.startsWith('kb:') ? regionId : region;
      const report = await json(`/api/v2/regions/${encodeURIComponent(ref)}/report`);
      if (generation !== regionGeneration || target.dataset.reportRegion !== region) return;
      const a = report.assessment;
      const changed = a.change?.type;
      const changedLabels = (a.change?.changed_reasons || []).map(id =>
        id === 'grade' ? '등급' : id === 'safety_status' ? '자료 안전 상태' :
          a.reasons.find(r => r.reason_id === id)?.label || '확인되지 않은 변경 항목');
      const change = changed === 'first_observation' ? '비교할 이전 발행 기록이 없습니다.' :
        changed === 'method_change' || changed === 'mixed_change'
          ? '계산 기준이 달라져 지난 판정과 단순 비교할 수 없습니다.' :
        changed === 'freshness_change' ? '시장 지표는 같지만 원천 갱신이 지연돼 현재 판정을 보류합니다.' :
        changed === 'explanation_change' ? '같은 기준일의 판정은 유지되며 매도주의 보정 근거 설명이 추가됐습니다.' :
        changed === 'market_change' || changed === 'source_revision'
          ? `이전 ${esc(a.change.previous_grade || '판정')}에서 ${changedLabels.map(esc).join(' · ')} 근거가 달라졌습니다.${changed === 'source_revision' ? ' 같은 기준일의 원천 수정입니다.' : ''}` :
          '이전 발행 판정에서 확인된 근거 변화가 없습니다.';
      const cautionLead = report.cautions?.[0];
      const held = a.assessment_status === 'held';
      const heldReasons = report.unknowns.length
        ? report.unknowns.slice(0,2).map(riskLabel).join(' · ') +
          (report.unknowns.length > 2 ? ` · 그 밖의 사유 ${report.unknowns.length - 2}건` : '')
        : '자료와 지역 기준을 확인하기 전까지 판정을 보류합니다.';
      target.innerHTML = `<section class="signal-assessment" aria-label="지역 시그널 요약과 근거">
        <div class="signal-assessment-heading">
          <p class="v2-report-lead">${esc(a.summary)}</p>
          <p class="v2-muted">KB ${esc(report.asof)} 기준 · ${esc(a.scope_note)}</p>
        </div>
        <div class="signal-assessment-highlights">
          <section class="signal-assessment-highlight"><h3>${held ? '판단 보류 이유' : '판정 이유'}</h3>
            ${held ? `<p>${esc(heldReasons)}</p>` : report.positive.length ? report.positive.slice(0,2).map(item => briefReason(item)).join('') : '<p>충족된 강세 조건이 없거나 자료가 부족합니다.</p>'}
          </section>
          <section class="signal-assessment-highlight signal-assessment-caution"><h3>${held ? '추가로 확인할 지표' : '함께 확인할 점'}</h3>
            ${cautionLead ? briefReason(cautionLead, true) : '<p>연결된 지표에서 별도 반대 근거를 찾지 못했습니다. 위험이 없다는 뜻은 아닙니다.</p>'}
          </section>
        </div>
        <details class="signal-evidence-details"><summary>판정 근거와 한계 보기</summary>
          <h3>${held ? '기본 규칙의 충족 조건 · 현재 판정 아님' : '이번 판정의 근거'}</h3>${report.positive.length ? report.positive.slice(0,3).map(reason).join('') :
            '<p>충족된 강세 조건이 없거나 자료가 부족합니다.</p>'}
          <h3>반대 근거와 한계</h3>${report.cautions.length ? report.cautions.map(reason).join('') :
            '<p>현재 연결된 지표에서 별도 반대 근거를 확인하지 못했습니다. 위험이 없다는 뜻은 아닙니다.</p>'}
          ${report.unknowns.length ? `<p class="v2-row v2-caution">판단 보류 이유: ${report.unknowns.map(x => esc(riskLabel(x))).join(' · ')}</p>` : ''}
          <p class="v2-muted">${esc(change)}</p>
          ${a.assessment_status === 'held' ? `<details><summary>기존 규칙 산출값</summary><p>${esc(historicalGradeLabel(a.raw_grade))} · 검증되지 않아 현재 판정으로 쓰지 않습니다.</p></details>` : ''}
          <p class="v2-muted">매수우위지수 100은 KB의 응답 균형선입니다. 앱의 강세 조건 70은 별도 관찰 기준입니다.</p>
          ${enabled('contextual_explanations_enabled') ? `<div class="v2-row"><b>더 쉽게 이해하기</b><p class="v2-muted">요청할 때만 AI 설명을 만듭니다. 판정과 숫자는 위 리포트 그대로이며 비용 제한·오류 시 기본 설명을 보여 줍니다.</p>
            <button type="button" class="btn" id="v2RegionExplain">쉽게 설명</button>
            <button type="button" class="btn" id="v2RegionCounter">반대 근거 보기</button>
            <div id="v2RegionExplanation" role="status" aria-live="polite"></div></div>` : ''}
          <button type="button" class="btn" id="v2RegionDiscover">이 지역 매물 비교</button>
          <button type="button" class="btn" id="v2RegionNote">관심 이유 기록</button>
          <details><summary>이 판정 보관하기</summary><p class="v2-muted">저장 당시 근거로 남습니다. 현재 판정은 위에서 다시 확인하세요.</p>
            <button type="button" class="btn" id="v2RegionSave">현재 판정 저장</button>
            <button type="button" class="btn" id="v2RegionSaved">저장본 보기</button>
            <p id="v2RegionSaveStatus" class="v2-muted" role="status"></p></details>
        </details>
      </section>`;
      target.querySelector('#v2RegionDiscover').onclick = () => openDiscovery(region);
      for (const [id, mode] of [['v2RegionExplain','easy'],['v2RegionCounter','counterevidence']]) {
        const button = target.querySelector(`#${id}`);
        if (button) button.onclick = () => askExplanation(report,
          {type:'region',key:ref,mode}, target.querySelector('#v2RegionExplanation'),
          () => generation === regionGeneration && target.dataset.reportRegion === region);
      }
      target.querySelector('#v2RegionNote').onclick = () => openNote('region', region, report.report_id);
      target.querySelector('#v2RegionSaved').onclick = () => openSavedReports(report.subject.region_id);
      target.querySelector('#v2RegionSave').onclick = async event => {
        const button = event.currentTarget, status = target.querySelector('#v2RegionSaveStatus');
        button.disabled = true;
        status.textContent = '현재 판정을 저장하고 있습니다…';
        try {
          await json('/api/v2/report-snapshots', {method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({type:'region',region:ref,report_id:report.report_id})});
          if (generation === regionGeneration && target.dataset.reportRegion === region)
            status.textContent = '이 시점의 지역 판정을 저장했습니다.';
        } catch (error) {
          if (generation === regionGeneration && target.dataset.reportRegion === region)
            status.textContent = error.message;
        } finally {
          if (generation === regionGeneration) button.disabled = false;
        }
      };
    } catch (_) {
      if (generation === regionGeneration && target.dataset.reportRegion === region) {
        target.innerHTML = '<div class="v2-row v2-caution" role="alert"><b>판정 근거를 확인할 수 없습니다</b>' +
          '<p>지금 보이는 등급만으로 매수 판단하지 마세요. 근거를 다시 불러와 확인해 주세요.</p>' +
          '<button type="button" class="btn" id="v2RegionRetry">근거 다시 확인</button></div>';
        target.querySelector('#v2RegionRetry').onclick = () => paintRegion(region, regionId);
      }
    }
  }

  async function openListing(key, fromDiscovery = false) {
    if (!key) return;
    discoveryReturn = fromDiscovery ? {tab:location.hash.slice(1), key} : null;
    document.getElementById('v2BackToDiscovery').hidden = !discoveryReturn;
    if (!enabled('report_v2_enabled')) {
      if (typeof window.openListingReport === 'function') window.openListingReport(key);
      return;
    }
    const generation = ++listingGeneration;
    const dialog = showReportDrawer();
    const body = document.getElementById('v2ReportBody');
    body.scrollTop = 0;
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
      body.innerHTML = `<header class="v2-report-heading"><p class="v2-eyebrow">매물 확인 · 현장 검증 전</p>
        <h2>${esc(item.name || '매물')}</h2><p class="v2-report-price">${money(item.asking_manwon)}</p>
        <p class="v2-muted">${esc(item.region)} · ${esc(item.kind)}${item.exclusive_m2 ? ` · 전용 ${esc(item.exclusive_m2)}㎡` : ''}${item.floor != null ? ` · ${esc(item.floor)}층` : ''}</p>
        <p class="v2-muted">매물 정보 ${esc(item.collected_at || '수집 시각 미확인')}${item.stale ? ' · 오래된 정보' : ''}</p></header>
        ${listingFieldLinks(item)}
        <section class="v2-report-section" aria-label="궁금한 것에 대한 답"><h3>가격은 괜찮나요?</h3>
          <div class="v2-answer">${listingPriceSummary(price, report.partial_failures, item.stale)}</div>
          <h3>내 예산에 맞나요?</h3><div class="v2-answer">${listingBudgetSummary(fit, report.partial_failures)}</div></section>
        <section class="v2-report-section"><h3>현장에서 딱 두 가지 확인</h3>
          <p>① 이 매물이 아직 판매 중이고 호가가 같은지 중개사에게 묻기</p>
          <p>② 실내 사진·향·수리 상태가 실제 이 동·호수의 것인지 확인하기</p></section>
        <details class="v2-report-more"><summary>가격 근거와 판단의 한계 자세히</summary>
        <h3>가격 근거</h3><div class="v2-row">${esc(price['이유'] ||
          (price['상태'] === '관측비교' ? `동일 조건 실거래 ${price['표본수']}건과 비교했습니다.` :
            '비교 가능한 실거래가 부족합니다.'))}</div>
        ${pros ? `<h3>유리한 근거</h3>${pros}` : ''}
        ${cautions ? `<h3>주의할 근거</h3>${cautions}` : ''}
        ${(report.lines || {}).price ? `<p class="v2-muted">${esc(report.lines.price)}</p>` : ''}
        ${(report.lines || {}).cash ? `<p class="v2-muted">${esc(report.lines.cash)}</p>` : ''}
        <details><summary>근거와 기준일</summary><p class="v2-muted">${evidence || '근거 기준일을 확인할 수 없습니다.'}</p></details></details>
        <details class="v2-report-more"><summary>교통·학교·생활권 확인</summary>
        <h3>교통·학교·생활권</h3>
        <p class="v2-muted">표시 좌표 기준 참고 자료입니다. 출입구·통학 배정·실제 출퇴근 시간은 확인이 필요합니다.</p>
        <button type="button" class="btn" id="v2ListingLocation">입지 근거 확인</button>
        <div id="v2LocationResult" aria-live="polite"></div></details>
        <details class="v2-report-more"><summary>중개사에게 더 물어볼 것</summary>
          ${(report.next_actions || []).map(x => `<p>• ${esc(x)}</p>`).join('') || '<p>현재 판매 여부와 수리 상태를 확인하세요.</p>'}</details>
        <details class="v2-report-more"><summary>핵심 판단 빠르게 확인</summary>
        <div class="v2-row" aria-label="핵심 판단 바로 확인"><b>먼저 궁금한 것부터 보세요</b>
          <p class="v2-muted">현재 리포트의 가격·예산·주의 근거를 짧게 다시 보여 줍니다.</p>
          <button type="button" class="btn" data-v2-quick-question="price">가격이 싼가?</button>
          <button type="button" class="btn" data-v2-quick-question="budget">내 예산에 맞나?</button>
          <button type="button" class="btn" data-v2-quick-question="risk">뭘 조심해야 하나?</button>
          <div id="v2QuickAnswer" role="status" aria-live="polite"></div></div>
        ${enabled('contextual_explanations_enabled') ? `<div class="v2-row"><b>이 리포트 더 쉽게 보기</b><p class="v2-muted">선택하면 AI가 현재 근거만 다시 풀어 설명합니다. 기본 리포트와 숫자는 바꾸지 않습니다.</p>
          <button type="button" class="btn" data-v2-explain="easy">쉽게 설명</button>
          <button type="button" class="btn" data-v2-explain="counterevidence">반대 근거</button>
          <div id="v2ListingExplanation" role="status" aria-live="polite"></div></div>` : ''}</details>
        <details class="v2-report-more"><summary>관심 기록·비교·리포트 저장</summary>
        <button type="button" class="btn" id="v2ListingNote">관심 이유 기록</button>
        <button type="button" class="btn" id="v2ListingCompare">비교함에 담기</button>
        <button type="button" class="btn" id="v2OpenCompare">비교함 열기</button>
        ${watchAction(item)}
        <button type="button" class="btn" id="v2SaveReport">이 리포트 저장</button>
        <button type="button" class="btn" id="v2SavedReports">저장본 보기</button>
        <p id="v2SaveReportStatus" class="v2-muted" role="status"></p>
        <button type="button" class="btn" id="v2ListingLink">내 계정에서 열기 링크 복사</button>
        <p class="v2-muted">이 링크는 현재 수집분을 다시 조회합니다. 지금 보이는 리포트의 고정 사본은 아닙니다.</p>
        <p id="v2ListingLinkStatus" class="v2-muted" role="status"></p>
        <div class="v2-row"><b>이 리포트로 다음 확인 행동을 정할 수 있었나요?</b><p class="v2-muted">선택만 기록합니다. 매물명·가격·내 조건은 보내지 않습니다.</p>
          <button type="button" class="btn" data-v2-report-feedback="yes">네, 충분했어요</button>
          <button type="button" class="btn" data-v2-report-feedback="no">아니요, 더 필요해요</button>
          <p id="v2ReportFeedbackStatus" class="v2-muted" role="status"></p></div></details>`;
      revealCurrentListingMap(item);
      body.querySelector('#v2ListingNote').onclick = () => openNote('listing', key, report.report_id);
      const budgetSetup = body.querySelector('#v2BudgetSetup');
      if (budgetSetup) budgetSetup.onclick = () => {
        dialog.close();
        if (typeof window.switchTab === 'function') window.switchTab('mypage');
      };
      const explanationTarget = body.querySelector('#v2ListingExplanation');
      const currentExplanation = () => generation === listingGeneration && dialog.open;
      body.querySelectorAll('[data-v2-explain]').forEach(button => button.onclick = () =>
        askExplanation(report, {type:'listing',key,mode:button.dataset.v2Explain},
          explanationTarget, currentExplanation));
      body.querySelectorAll('[data-v2-quick-question]').forEach(button => button.onclick = () => {
        if (generation !== listingGeneration || !dialog.open) return;
        const answer = listingQuickAnswer(report, button.dataset.v2QuickQuestion);
        if (!answer) return;
        body.querySelectorAll('[data-v2-quick-question]').forEach(choice =>
          choice.setAttribute('aria-pressed', String(choice === button)));
        body.querySelector('#v2QuickAnswer').innerHTML = `<p><b>${esc(answer.title)}</b> ${esc(answer.answer)}</p>
          <p class="v2-muted">${esc(answer.limit)}</p>`;
      });
      body.querySelector('#v2ListingCompare').onclick = event => {
        event.currentTarget.textContent = addCompare(key);
      };
      body.querySelector('#v2OpenCompare').onclick = () => {
        dialog.close();
        listingCompareOpen();
      };
      body.querySelector('#v2ListingLink').onclick = () => copyListingLink(item.key);
      body.querySelector('#v2ListingLocation').onclick = () => loadLocation(key, generation);
      body.querySelectorAll('[data-v2-report-feedback]').forEach(button => button.onclick = () => {
        if (generation !== listingGeneration) return;
        track(EVENTS.REPORT_TASK_FEEDBACK, {type:'listing', answer:button.dataset.v2ReportFeedback});
        body.querySelectorAll('[data-v2-report-feedback]').forEach(choice => choice.disabled = true);
        body.querySelector('#v2ReportFeedbackStatus').textContent = '의견 고맙습니다. 리포트를 다듬는 데 참고하겠습니다.';
      });
      const refreshTrades = body.querySelector('#v2RefreshTrades');
      if (refreshTrades) refreshTrades.onclick = async () => {
        refreshTrades.disabled = true;
        const status = body.querySelector('#v2RefreshTradesStatus');
        status.textContent = '실거래 원천을 확인하고 있습니다…';
        try {
          const updated = await json('/api/v2/listings/report-enrich', {method:'POST',
            headers:{'Content-Type':'application/json'}, body:JSON.stringify({key})});
          if (typeof _laInvalidateCache === 'function') _laInvalidateCache();
          if (generation === listingGeneration && dialog.open) {
            if ((updated.partial_failures || []).some(x => x.startsWith('trade_'))) {
              status.textContent = '실거래 원천을 확인했지만 새 근거를 얻지 못했습니다. 가격 비교는 보류합니다.';
              refreshTrades.disabled = false;
            } else openListing(key);
          }
        } catch (error) {
          if (generation === listingGeneration && dialog.open) {
            status.textContent = error.message;
            refreshTrades.disabled = false;
          }
        }
      };
      body.querySelector('#v2SaveReport').onclick = async event => {
        const status = body.querySelector('#v2SaveReportStatus');
        const button = event.currentTarget;
        button.disabled = true;
        status.textContent = '현재 리포트를 저장하고 있습니다…';
        try {
          await json('/api/v2/report-snapshots', {method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({key:item.key,report_id:report.report_id})});
          if (generation === listingGeneration && dialog.open) status.textContent = '이 시점의 리포트를 저장했습니다. 저장본 보기에서 다시 확인할 수 있습니다.';
        } catch (error) {
          if (generation === listingGeneration && dialog.open) status.textContent = error.message;
        } finally {
          if (generation === listingGeneration) button.disabled = false;
        }
      };
      body.querySelector('#v2SavedReports').onclick = () => openSavedReports(item.key);
    } catch (error) {
      if (generation === listingGeneration) body.textContent = error.message;
    }
  }

  async function openSavedReports(key = '', cursor = null) {
    const generation = ++listingGeneration;
    const dialog = showReportDrawer();
    const body = document.getElementById('v2ReportBody');
    body.textContent = '저장한 리포트를 불러오고 있습니다…';
    try {
      const params = new URLSearchParams();
      if (key) params.set('key', key);
      if (cursor) params.set('cursor', cursor);
      const page = await json('/api/v2/report-snapshots' + (params.size ? `?${params}` : ''));
      if (generation !== listingGeneration || !dialog.open) return;
      savedReportList = {key, items:cursor && savedReportList.key===key
        ? [...savedReportList.items, ...(page.items||[])] : (page.items||[]),
        next_cursor:page.next_cursor||null, policy:page.policy||null};
      if (generation !== listingGeneration || !dialog.open) return;
      const data=savedReportList;
      body.innerHTML = `<h2>내가 저장한 리포트</h2><p class="v2-muted">저장 당시의 근거이며 현재 판정·매물 상태는 다시 확인하세요. 내 계정에서 삭제 전까지 보관하며 공개 공유 링크는 만들지 않습니다. 삭제한 저장본도 순환 백업에는 일정 기간 남을 수 있습니다.</p>` +
        ((data.items || []).length ? data.items.map(item => `<div class="v2-row">
          <b>${esc(item.name || '매물')} · ${esc(item.kind?.startsWith('비교') ? '비교' : item.kind)}</b>
          <p class="v2-muted">${item.kind === '지역' ? 'KB 기준' : '수집'} ${esc(item.asof || '시각 미확인')} · 저장 ${esc(new Date(item.saved_at * 1000).toLocaleString('ko-KR'))}</p>
          <button type="button" class="btn" data-saved-report="${esc(item.report_id)}">당시 리포트 보기</button>
          <button type="button" class="btn" data-delete-report="${esc(item.report_id)}">저장본 삭제</button>
        </div>`).join('') : '<p>저장한 리포트가 없습니다.</p>') +
        (data.next_cursor?'<button type="button" class="btn" id="v2SavedMore">이전 저장본 더 보기</button>':'') +
        '<p id="v2SavedStatus" role="status"></p>';
      body.querySelectorAll('[data-saved-report]').forEach(button => button.onclick = () => openSavedReport(button.dataset.savedReport, key));
      const more=body.querySelector('#v2SavedMore');
      if (more) more.onclick = () => { more.disabled=true; openSavedReports(key,data.next_cursor); };
      body.querySelectorAll('[data-delete-report]').forEach(button => button.onclick = async () => {
        if (!confirm('이 리포트 저장본 하나를 삭제할까요? 삭제 후 복구할 수 없습니다.')) return;
        button.disabled = true;
        try {
          await json(`/api/v2/report-snapshots/${encodeURIComponent(button.dataset.deleteReport)}`, {method:'DELETE'});
          if (generation === listingGeneration && dialog.open) openSavedReports(key);
        } catch (error) {
          if (generation === listingGeneration && dialog.open) {
            body.querySelector('#v2SavedStatus').textContent = error.message;
            button.disabled = false;
          }
        }
      });
    } catch (error) {
      if (generation === listingGeneration) body.textContent = error.message;
    }
  }

  async function openSavedReport(reportId, listKey = '') {
    const generation = ++listingGeneration;
    const dialog = document.getElementById('v2ReportDlg');
    const body = document.getElementById('v2ReportBody');
    body.textContent = '저장본을 불러오고 있습니다…';
    try {
      const saved = await json(`/api/v2/report-snapshots/${encodeURIComponent(reportId)}`);
      if (generation !== listingGeneration || !dialog.open) return;
      const report = saved.report || {}, item = report.subject || {};
      const rows = (items, caution = false) => (items || []).map(x =>
        `<div class="v2-row${caution ? ' v2-caution' : ''}">${esc(x.text)}</div>`).join('');
      const evidence = (report.evidence || []).map(x => `${esc(x.label)} · ${esc(x.asof || '기준일 미확인')} · ${esc(x.status)}`).join('<br>');
      if (report.type === 'region') {
        const assessment = report.assessment || {};
        body.innerHTML = `<h2>저장 당시 · ${esc(item.region || '지역')} · ${esc(assessment.display_grade || '판단 보류')}</h2>
          <p class="v2-muted">KB ${esc(report.asof || '기준일 미확인')} 기준 · 저장 ${esc(new Date(saved.saved_at * 1000).toLocaleString('ko-KR'))}</p>
          <p class="v2-caution">저장 후 바뀌지 않은 당시 판단입니다. 현재 시그널·자료 신선도·매물 가격을 뜻하지 않습니다.</p>
          <p>${esc(assessment.summary || '당시 근거를 확인하세요.')}</p>
          <h3>당시 긍정 근거</h3>${(report.positive || []).map(reason).join('') || '<p>확인된 긍정 근거가 없습니다.</p>'}
          <h3>당시 반대 근거·한계</h3>${(report.cautions || []).map(reason).join('') || '<p>당시 별도 반대 근거가 기록되지 않았습니다.</p>'}
          <button type="button" class="btn" id="v2BackSavedReports">저장본 목록으로</button>`;
        body.querySelector('#v2BackSavedReports').onclick = () => openSavedReports(listKey);
        return;
      }
      if (report.type === 'comparison') {
        const entries = report.items || [];
        body.innerHTML = `<h2>저장 당시 · ${esc(entries.length)}개 매물 비교</h2>
          <p class="v2-caution">저장 후 바뀌지 않은 당시 비교입니다. 현재 호가·판매 여부·실거래·내 자금 상태를 뜻하지 않습니다.</p>
          <p class="v2-muted">${esc(report.basis || '당시 수집분 기준')}</p>
          ${entries.map(entry => { const listing = entry.listing || {}, price = entry.price || {};
            return `<div class="v2-row"><b>${esc(listing.name || '단지 미확인')}</b> · ${money(listing.asking_manwon)}
              <p>${esc(listing.region || '')} · ${listing.exclusive_m2 ? `${esc(listing.exclusive_m2)}㎡` : '면적 미확인'} · 수집 ${esc(listing.collected_at || '시점 미확인')}</p>
              <p>당시 가격 근거: ${price['상태'] === '관측비교' ? `동일 조건 실거래 ${esc(price['표본수'])}건 대비 ${esc(price['호가차이율'])}%` : esc(price['이유'] || '비교 보류')}</p></div>`;
          }).join('')}
          ${(report.cautions || []).map(text => `<p class="v2-caution">${esc(text)}</p>`).join('')}
          <p class="v2-muted">당시 미비교 항목: ${(report.unknowns || []).map(esc).join(' · ') || '기록 없음'}</p>
          <button type="button" class="btn" id="v2BackSavedReports">저장본 목록으로</button>`;
        body.querySelector('#v2BackSavedReports').onclick = () => openSavedReports(listKey);
        return;
      }
      body.innerHTML = `<h2>저장 당시 · ${esc(item.name || '매물')} · ${money(item.asking_manwon)}</h2>
        <p class="v2-muted">${esc(item.region)} · 수집 ${esc(report.asof || '시각 미확인')} · 저장 ${esc(new Date(saved.saved_at * 1000).toLocaleString('ko-KR'))}</p>
        <p class="v2-caution">이 자료는 저장 후 바뀌지 않습니다. 현재 호가·판매 여부·내 예산·시그널을 뜻하지 않습니다.</p>
        <p>${esc((report.lines || {}).cash || '자금 계산은 확인이 필요합니다.')}</p>
        <h3>당시 가격 근거</h3><div class="v2-row">${esc((report.price || {})['이유'] || '동일 조건 실거래와의 비교를 확인하세요.')}</div>
        <h3>당시 장점</h3>${rows(report.positive) || '<p>확인된 장점이 없습니다.</p>'}
        <h3>당시 주의할 점</h3>${rows(report.cautions, true) || '<p>당시 확인된 주의 항목이 없습니다.</p>'}
        <details><summary>당시 자료와 기준일</summary><p class="v2-muted">${evidence || '자료 기준일을 확인할 수 없습니다.'}</p></details>
        <button type="button" class="btn" id="v2BackSavedReports">저장본 목록으로</button>`;
      body.querySelector('#v2BackSavedReports').onclick = () => openSavedReports(listKey);
    } catch (error) {
      if (generation === listingGeneration) body.textContent = error.message;
    }
  }

  function locationRoute(route, label) {
    if (route?.status === 'observed' && route.minutes != null && Number.isFinite(Number(route.minutes)) && Number(route.minutes) >= 0)
      return `${esc(label)} ${esc(route.destination || '')} · 약 ${esc(route.minutes)}분${route.distance_m != null ? ` · ${esc(route.distance_m)}m` : ''}`;
    return `${esc(label)} · ${esc(route?.reason || '경로를 확인하지 못했습니다.')}`;
  }

  function renderLocation(data) {
    const mobility = data.mobility || {}, school = data.school || {}, amenities = data.amenities || {};
    const zones = school.status === 'candidate' ? school.zones || [] : [];
    const schools = [...new Set(zones.flatMap(zone => (zone.schools || []).map(item => item.name).filter(Boolean)))];
    const schoolText = zones.length ? `통학구역 후보 · ${esc((schools.length ? schools : zones.map(z => z.name)).slice(0,5).join(' · '))}` :
      esc(school.reason || '통학구역 후보를 확인하지 못했습니다.');
    const places = Object.values(amenities.by_category || {}).slice(0,5).map(category => {
      const first = category.places?.[0];
      if (category.status === 'unavailable') return `${esc(category.label || '시설')} · 조회 실패`;
      if (!first) return `${esc(category.label || '시설')} · 조회 결과 없음`;
      return `${esc(category.label || '시설')} · ${esc(first.name)}${first.distance_m != null ? ` · 직선 ${esc(first.distance_m)}m` : ''}`;
    });
    const sources = (data.evidence || []).map(item => `${esc(item.label)} · ${esc(item.asof || '기준일 미확인')} · ${esc(item.status)}`);
    return `<div class="v2-row"><b>교통·직장</b><p>${locationRoute(mobility.station_walk, '가까운 역')}</p>
      <p>${locationRoute(mobility.work_transit, '저장된 직장')}</p><p class="v2-muted">${esc(mobility.reason || '표시 좌표 기준입니다.')} ${esc(mobility.api_reason || '')} ${esc(mobility.transit_note || '')}</p></div>
      <div class="v2-row"><b>통학구역</b><p>${schoolText}</p><p class="v2-muted">${school.boundary_near ? '통학구역 경계와 가까울 수 있습니다. ' : ''}${esc(school.coordinate_note || '실제 주소의 배정 학교는 교육청에 확인하세요.')}</p></div>
      <div class="v2-row"><b>가까운 시설</b><p>${places.join('<br>') || esc(amenities.reason || '주변 시설을 확인하지 못했습니다.')}</p>
      <p class="v2-muted">${esc(amenities.coordinate_note || '직선거리이며 실제 이동 경로·영업 상태는 확인되지 않았습니다.')}</p></div>
      <details><summary>입지 자료와 기준일</summary><p class="v2-muted">${sources.join('<br>') || '확인된 입지 자료가 없습니다.'}</p></details>`;
  }

  async function loadLocation(key, generation) {
    const body = document.getElementById('v2ReportBody');
    const host = body.querySelector('#v2LocationResult');
    const button = body.querySelector('#v2ListingLocation');
    if (!host || !button) return;
    host.textContent = '입지 자료를 확인하고 있습니다…';
    button.disabled = true;
    try {
      const data = await json(`/api/listing-location?key=${encodeURIComponent(key)}`);
      if (generation !== listingGeneration || !document.getElementById('v2ReportDlg').open) return;
      host.innerHTML = renderLocation(data);
      button.textContent = '입지 근거 다시 확인';
    } catch (error) {
      if (generation !== listingGeneration || !document.getElementById('v2ReportDlg').open) return;
      host.textContent = error.message;
    } finally {
      if (generation === listingGeneration) button.disabled = false;
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
    discoveryReturn = null;
    document.getElementById('v2BackToDiscovery').hidden = true;
    const dialog = document.getElementById('v2ReportDlg');
    dialog.classList.remove('v2-map-expanded');
    document.body.classList.remove('v2-report-map');
    requestAnimationFrame(() => window.dispatchEvent(new Event('resize')));
    const toggle = document.getElementById('v2MapSpace');
    if (toggle) { toggle.setAttribute('aria-pressed', 'false'); toggle.textContent = '지도 크게'; }
    const url = new URL(location.href);
    if (!url.searchParams.has('listing')) return;
    url.searchParams.delete('listing');
    history.replaceState(history.state, '', url.pathname + url.search + url.hash);
  });
  document.getElementById('v2MapSpace')?.addEventListener('click', event => {
    const expanded = document.getElementById('v2ReportDlg').classList.toggle('v2-map-expanded');
    event.currentTarget.setAttribute('aria-pressed', String(expanded));
    event.currentTarget.textContent = expanded ? '리포트 크게' : '지도 크게';
    if (expanded) revealCurrentListingMap();
  });
  document.getElementById('v2BackToDiscovery')?.addEventListener('click', () => {
    const context = discoveryReturn;
    if (!context) return;
    document.getElementById('v2ReportDlg').close();
    if (context.tab && typeof window.switchTab === 'function') window.switchTab(context.tab);
    const dialog = document.getElementById('v2DiscoverDlg');
    if (!dialog.open) dialog.showModal();
    syncDiscoveryCompareAction();
    const previous = [...document.querySelectorAll('#v2DiscoverResults [data-v2-listing]')]
      .find(button => button.dataset.v2Listing === context.key);
    (previous || document.getElementById('v2DiscoverForm').elements.max_price_manwon).focus();
  });
  document.addEventListener('keydown', event => {
    const report = document.getElementById('v2ReportDlg');
    if (event.key !== 'Escape' || !report?.open ||
        [...document.querySelectorAll('dialog[open]')].some(other => other !== report)) return;
    report.close();
  });
  document.getElementById('onbDlg')?.addEventListener('close', openInitialLink);

  async function loadDiscoveryRegions(requested = '') {
    const generation = ++discoveryRegionGeneration;
    const form = document.getElementById('v2DiscoverForm');
    const field = form.elements.region_code;
    const submit = form.querySelector('[type="submit"]');
    const status = document.getElementById('v2DiscoverRegionStatus');
    const previous = requested ? '' : field.value;
    field.disabled = true;
    submit.disabled = true;
    status.textContent = '지역 목록을 확인하고 있습니다…';
    try {
      const data = await json('/api/v2/discovery/regions');
      if (generation !== discoveryRegionGeneration || !document.getElementById('v2DiscoverDlg').open) return;
      const options = data.regions || [];
      field.replaceChildren(new Option('전체 지역 · 선택 안 함', ''));
      for (const option of options) {
        const node = new Option(option.label, option.code);
        node.dataset.regionName = option.name;
        field.add(node);
      }
      const chosen = requested ? options.filter(option => option.name === requested || option.label === requested) :
        options.filter(option => option.code === previous);
      if (chosen.length === 1) field.value = chosen[0].code;
      field.disabled = !options.length;
      submit.disabled = !!(requested && chosen.length !== 1);
      status.textContent = !options.length ? '지역 코드가 확인되지 않아 지역 필터를 잠시 사용할 수 없습니다.' :
        requested && chosen.length !== 1 ? '선택한 지역을 확인할 수 없습니다. 목록에서 다시 선택해 주세요.' :
        '서울·인천처럼 이름이 같은 지역도 코드로 구별합니다.';
      if (!options.length && requested) {
        const button = document.createElement('button');
        button.type = 'button'; button.className = 'btn'; button.textContent = '지역 조건 없이 찾기';
        button.onclick = () => { submit.disabled = false; status.textContent = '지역 조건을 제외하고 검색합니다.'; };
        status.append(' ', button);
      }
    } catch (_) {
      if (generation !== discoveryRegionGeneration || !document.getElementById('v2DiscoverDlg').open) return;
      field.disabled = true;
      submit.disabled = !!requested;
      status.textContent = '지역 목록을 불러오지 못했습니다. 다시 열어 주세요.';
      if (requested) {
        const button = document.createElement('button');
        button.type = 'button'; button.className = 'btn'; button.textContent = '지역 조건 없이 찾기';
        button.onclick = () => { submit.disabled = false; status.textContent = '지역 조건을 제외하고 검색합니다.'; };
        status.append(' ', button);
      }
    }
  }

  function openDiscovery(region = '') {
    if (!enabled('discovery_v2_enabled')) {
      if (typeof window.focusListings === 'function') window.focusListings(region);
      else if (typeof window.switchTab === 'function') window.switchTab('all');
      if (typeof window.toast === 'function') window.toast('조건별 발견은 일시 중지됐습니다. 기본 매물 목록에서 확인해 주세요.');
      return;
    }
    discoveryGeneration++;
    const dialog = document.getElementById('v2DiscoverDlg');
    const form = document.getElementById('v2DiscoverForm');
    if (!dialog.open) dialog.showModal();
    syncDiscoveryCompareAction();
    updateDiscoveryExtraSummary();
    loadDiscoveryRegions(region);
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

  function syncDiscoveryCompareAction() {
    const button = document.getElementById('v2DiscoverCompare');
    if (!button) return;
    const count = typeof _listingCompareKeys === 'function' ? _listingCompareKeys().length : 0;
    button.hidden = count < 2;
    button.textContent = `선택 매물 ${count}개 비교하기`;
  }

  function updateDiscoveryExtraSummary() {
    const form = document.getElementById('v2DiscoverForm');
    const summary = document.getElementById('v2DiscoverExtraSummary');
    if (!form || !summary) return;
    const fields = ['min_area_m2', 'min_rooms', 'move_in_by', 'max_commute_minutes',
      'max_monthly_manwon', 'prefer_max_price_manwon', 'prefer_min_area_m2'];
    const active = fields.filter(name => form.elements[name].value.trim()).length +
      Number(form.elements.priority.value !== 'balanced') + Number(form.elements.include_exceeded.checked);
    summary.textContent = active ? `추가 조건 ${active}개 적용 · 수정하기` : '조건 더하기 · 면적·입주·통근 등';
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

  async function refreshNotes(cursor = null) {
    if (cursor && (noteList.loading || cursor !== noteList.next_cursor)) return;
    if (!cursor) {
      noteList.generation += 1;
      noteList.next_cursor = null;
      notesCache = [];
    }
    const generation = noteList.generation;
    const subject = noteSubject && {...noteSubject};
    noteList.loading = true;
    const list = document.getElementById('v2NoteList');
    if (!cursor) list.textContent = '저장한 기록을 불러오는 중…';
    try {
      const params = new URLSearchParams();
      if (subject) {
        params.set('subject_type', subject.type);
        params.set('subject_key', subject.key);
      }
      if (cursor) params.set('cursor', cursor);
      const data = await json('/api/v2/decision-notes' + (params.size ? '?' + params : ''));
      if (generation !== noteList.generation || !document.getElementById('v2NoteDlg').open) return;
      notesCache = cursor ? [...notesCache, ...(data.notes || [])] : (data.notes || []);
      noteList.next_cursor = data.next_cursor || null;
      list.innerHTML = `<h3>저장한 판단 · ${notesCache.length}건 표시</h3>` +
        (notesCache.length ? notesCache.map(n => `<div class="v2-row">
          <b>${esc(n.subject_key)} · ${n.horizon_weeks}주 뒤 복기</b>
          <p>${esc(n.thesis)}</p><p class="v2-muted">다시 생각할 조건: ${esc(n.counter_condition)}</p>
          <button type="button" class="btn" data-note-edit="${n.id}">수정</button>
          <button type="button" class="btn" data-note-delete="${n.id}">삭제</button>
        </div>`).join('') : '<p class="v2-muted">아직 기록이 없습니다.</p>') +
        (noteList.next_cursor ? '<button type="button" class="btn" data-note-more>이전 판단 기록 더 보기</button>' : '');
      const more = list.querySelector('[data-note-more]');
      if (more) more.onclick = () => refreshNotes(noteList.next_cursor);
      list.querySelectorAll('[data-note-edit]').forEach(button => button.onclick = () => {
        const n = notesCache.find(x => x.id === Number(button.dataset.noteEdit));
        if (!n) return;
        noteList.generation += 1;
        noteList.next_cursor = null;
        list.querySelector('[data-note-more]')?.remove();
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
      if (generation !== noteList.generation || !document.getElementById('v2NoteDlg').open) return;
      if (cursor) document.getElementById('v2NoteStatus').textContent = error.message;
      else list.textContent = error.message;
    } finally {
      if (generation === noteList.generation) noteList.loading = false;
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

  function discoveryCard(x, spec, commuteContext) {
    const finance = x.finance;
    const preference = x.preference || {};
    const move = x.listing.move_in;
    const moveCheck = (x.constraints || []).find(check => check.field === 'move_in_by');
    const commute = x.listing.commute;
    const commuteCheck = (x.constraints || []).find(check => check.field === 'max_commute_minutes');
    const moveText = move?.status === 'immediate' ? '원천 표시: 즉시 입주' :
      move?.status === 'dated' ? `원천 표시: ${esc(move.date)} 입주 가능` :
      move?.status === 'approximate' ? '원천이 초·중·하순 등 시기로 표시 · 날짜 확인 필요' :
      spec.move_in_by ? '입주 가능일 미확인' : '';
    const preferred = (preference.details || []).filter(p => p.status === 'matched').map(p => esc(p.label));
    const unknownPreference = (preference.details || []).filter(p => p.status === 'unknown').map(p => esc(p.label));
    const priorityLabel = {region:'지역',price:'호가',area:'면적'}[preference.priority];
    const monthly = finance && Number.isFinite(Number(finance.monthly_manwon))
      ? `${esc(finance.monthly_manwon)}만원` : '미계산';
    const cash = finance && Number.isFinite(Number(finance.cash_manwon))
      ? `${esc(finance.cash_manwon)}만원` : '미계산';
    const uncertainQuote = x.listing.asking_manwon != null && (x.listing.stale || x.listing.source_conflict);
    const quoteLabel = uncertainQuote && x.listing.source_conflict
      ? `원천 정보 충돌 · ${x.listing.stale ? '지난 수집 ' : ''}표시 호가 `
      : x.listing.stale && uncertainQuote ? '지난 수집 호가 ' : '';
    const uncertainAboveLimit = uncertainQuote && x.eligibility === 'verify' && spec.max_price_manwon
      && Number(x.listing.asking_manwon) > Number(spec.max_price_manwon);
    const hasTradeoff = x.tradeoff && x.tradeoff !== '현재 알려진 조건에서는 양보할 점을 확인하지 못했습니다.';
    return `<div class="v2-row${x.eligibility === 'exceeded' || x.listing.source_conflict ? ' v2-caution' : ''}" data-v2-card><b>${esc(x.listing.name || '이름 미확인')}</b> · ${quoteLabel}${money(x.listing.asking_manwon)}${uncertainQuote ? ' · 현재 호가 미확인' : ''}
      <p>${esc(x.listing.region)} · ${esc(x.listing.kind)}${x.listing.rooms != null ? ` · 방 ${esc(x.listing.rooms)}개` : ''}</p>
      ${moveText ? `<p>${moveText}${moveCheck?.status === 'fail' ? ' · 요청한 입주일보다 늦음' : ''}</p>` : ''}
      ${spec.max_commute_minutes ? `<p>${commute?.status === 'observed' ? `저장된 직장까지 대중교통 안내 ${esc(commute.minutes)}분${commuteCheck?.status === 'fail' ? ' · 설정한 상한 초과' : ''}` : commute?.status === 'unavailable' ? '경로 조회 실패 · 잠시 후 다시 검색해 확인하세요' : x.listing.coordinate ? '직장까지 통근 안내시간 미확인' : '매물 표시 좌표가 없어 통근 미확인'} · 실제 출입구·시간대와 다를 수 있습니다.</p>` : ''}
      ${x.eligibility === 'exceeded' ? '<p>설정한 필수 조건을 넘는 비교용 후보입니다. 구매 가능 추천이 아닙니다.</p>' : ''}
      ${finance ? `<p>자금 참고 계산: 총 월 상환 약 ${monthly} · 필요현금 약 ${cash}</p><p>${esc(finance.reason)}</p>` : ''}
      <p>${uncertainAboveLimit || x.listing.source_conflict ? '판정 보류 이유' : x.eligibility === 'verify' || x.eligibility === 'exceeded' ? '확인된 점' : x.eligibility === 'explore' ? '탐색 단서' : '추천 이유'}: ${esc(x.recommendation_reason)}</p>
      ${typeof listingPriceLine==='function'&&listingPriceLine(x.listing.price_comparison)?`<p>${esc(listingPriceLine(x.listing.price_comparison))}</p>`:''}
      ${preference.total ? `<p>선호 ${preference.satisfied}/${preference.total}개 충족 · ${preference.known}/${preference.total}개 자료 확인${priorityLabel ? ` · ${priorityLabel} 우선(2배): 적합도 ${esc(preference.score)}/100 · 확인도 ${esc(preference.coverage)}/100` : ''}${preferred.length ? ` · 부합: ${preferred.join('·')}` : ''}${unknownPreference.length ? ` · 미확인: ${unknownPreference.join('·')}` : ''}</p>` : ''}
      ${hasTradeoff ? `<p>양보할 점: ${esc(x.tradeoff)}</p>` : ''}<p>확인할 점: ${esc(commuteContext?.status === 'missing_work' && x.verify_next === '저장된 직장까지의 대중교통 경로를 확인하세요.' ? '내 정보에서 직장 위치를 먼저 저장하세요.' : x.verify_next)}</p>
      <button type="button" class="btn" data-v2-listing="${esc(x.listing.key)}">리포트 보기</button>
      ${spec.move_in_by && x.listing.kind === '일반매물' && !move ? `<button type="button" class="btn" data-v2-occupancy="${esc(x.listing.key)}">입주일 확인</button>` : ''}
      ${spec.max_commute_minutes && commuteContext?.status === 'ready' && x.listing.coordinate && !commute ? `<button type="button" class="btn" data-v2-commute="${esc(x.listing.key)}">직장 경로 확인</button>` : ''}
      <button type="button" class="btn" data-v2-compare="${esc(x.listing.key)}">비교함 담기</button>
      ${watchAction(x.listing)}</div>`;
  }

  function watchAction(listing) {
    return listing?.key && listing.name && typeof watchBtn === 'function' ? watchBtn(listing.key,listing.listing_aliases) : '';
  }

  async function saveComparison() {
    const button = document.getElementById('listingCompareSave');
    const status = document.getElementById('listingCompareSaveStatus');
    const keys = typeof _listingCompareKeys === 'function' ? _listingCompareKeys() : [];
    if (!button || !status || keys.length < 2 || keys.length > 3) return;
    const sameSelection = () => document.getElementById('listingCompareDlg').open &&
      _listingCompareKeys().join('\u0000') === keys.join('\u0000');
    button.disabled = true;
    status.textContent = '현재 비교 근거를 확인하고 저장하고 있습니다…';
    try {
      const options = {method:'POST',headers:{'Content-Type':'application/json'}};
      const current = await json('/api/v2/comparisons', {...options,body:JSON.stringify({keys})});
      await json('/api/v2/report-snapshots', {...options,
        body:JSON.stringify({type:'comparison',keys,report_id:current.report_id})});
      if (sameSelection())
        status.textContent = '이 시점의 비교 근거를 저장했습니다.';
    } catch (error) {
      if (sameSelection()) status.textContent = error.message;
    } finally { button.disabled = false; }
  }

  async function runDiscovery(cursor = null, feedback = '') {
    const generation = ++discoveryGeneration;
    const form = document.getElementById('v2DiscoverForm');
    const result = document.getElementById('v2DiscoverResults');
    const spec = {};
    for (const field of ['max_price_manwon', 'min_area_m2', 'min_rooms', 'max_commute_minutes', 'max_monthly_manwon',
                         'prefer_max_price_manwon', 'prefer_min_area_m2']) {
      const value = form.elements[field].value.trim();
      if (value) spec[field] = Number(value);
    }
    if (form.elements.move_in_by.value) spec.move_in_by = form.elements.move_in_by.value;
    if (form.elements.region_code.value) spec[form.elements.region_mode.value === 'prefer' ?
      'prefer_region_code' : 'region_code'] = form.elements.region_code.value;
    if (form.elements.priority.value !== 'balanced') spec.priority = form.elements.priority.value;
    if (form.elements.include_exceeded.checked) spec.include_exceeded = true;
    if (cursor) spec.cursor = cursor;
    if (!cursor) result.textContent = '조건에 맞는 후보를 확인하고 있습니다…';
    else result.querySelector('[data-v2-next]')?.setAttribute('disabled', '');
    try {
      const response = await fetch('/api/v2/discovery', {
        method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(spec),
      });
      if (response.status === 409) throw new Error('수집 결과가 바뀌었습니다. 다시 후보 찾기를 눌러 주세요.');
      if (response.status === 422 && cursor) throw new Error('검색 조건이 바뀌었습니다. 다시 후보 찾기를 눌러 주세요.');
      if (response.status === 422) {
        const detail = (await response.json().catch(() => ({}))).detail;
        throw new Error(typeof detail === 'string' && detail.trim() ? detail : '검색 조건을 확인하고 다시 시도해 주세요.');
      }
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
          stale:'지난 수집',stale_failed:'갱신 실패',unverified:'검증 대기',identity_unverified:'지역 출처 재확인 중',
          failed:'조회 실패',never_scanned:'미수집'};
        const sources = (data.sources || []).map(s => `${esc(s.kind)} ${labels[s.state] || '상태 미확인'}`).join(' · ');
        const warning = data.source_state === 'unavailable' ? '아직 수집된 원천이 없습니다. 전국 매물 0건이라는 뜻은 아닙니다.' :
          data.source_state === 'partial_empty' ? '원천 일부가 실패하거나 제한돼 0건을 확정할 수 없습니다.' :
          data.source_state === 'partial' ? '일부 원천이 실패·제한됐습니다. 아래 후보는 전체 시장이 아닙니다.' :
          data.source_state === 'empty' ? '이번 수집분은 0건입니다. 전체 시장의 매물 수는 아닙니다.' : '';
        const requested = form.elements.region_code.selectedOptions[0]?.dataset.regionName;
        const outside = requested && regions.length && !regions.includes(requested) ?
          `<p class="v2-muted">${esc(requested)}은(는) 현재 확인된 수집 지역에 없습니다.</p>` : '';
        const financeNotice = spec.max_monthly_manwon ?
          data.finance_context?.status === 'profile_unavailable' ? '저장된 매수력을 지금 읽지 못했습니다. 월 부담은 판정하지 않습니다.' :
          data.finance_context?.status !== 'ready' ? '매수력을 설정·확정해야 월 부담을 계산할 수 있습니다.' :
          data.finance_context?.policy_status !== 'verified' ? `대출 규제·세율 최신성이 검증되지 않았습니다(${esc(data.finance_context?.policy_declared_asof || '기준일 미확인')} 기준 가정). 월 부담은 참고용이며 후보는 확인 필요로 분류됩니다.` :
          '월 부담은 입력 가정의 추정치이며 대출 승인이 아닙니다.' : '';
        const commuteNotice = spec.max_commute_minutes ?
          data.commute_context?.status === 'missing_work' ? '내 정보에서 직장 위치를 저장해야 통근 안내시간을 확인할 수 있습니다. 아직 후보를 조건 부합으로 판정하지 않습니다.' :
          data.commute_context?.status === 'profile_unavailable' ? '직장 위치를 지금 읽지 못해 통근을 판정하지 않습니다.' :
          data.commute_context?.status === 'unconfigured' ? '경로 API가 설정되지 않아 통근을 판정하지 않습니다.' :
          '통근 안내시간은 후보별 ‘직장 경로 확인’을 누를 때만 조회합니다. 매물 표시 좌표 기준이며 실제 출입구·출퇴근 시간대의 소요시간이 아닙니다.' : '';
        const hasPreference = !!(spec.prefer_region_code || spec.prefer_max_price_manwon || spec.prefer_min_area_m2);
        const priorityApplied = spec.priority === 'region' ? !!spec.prefer_region_code :
          spec.priority === 'price' ? !!spec.prefer_max_price_manwon :
          spec.priority === 'area' ? !!spec.prefer_min_area_m2 : false;
        result.innerHTML = `<p class="v2-muted">현재 수집된 ${regions.length}개 지역 · ${sources || '원천 상태 미확인'} · 전체 시장 아님</p>
          <p class="v2-muted">정렬: ${hasPreference ? '입력한 선호 충족·자료 확인도 → 수집일' : '최근 수집일'}${priorityApplied ? ' · 중요 선호 2배' : spec.priority ? ' · 선택한 중요 선호는 아직 입력되지 않아 균등 적용' : ''} · 원천 정보 충돌 후보${spec.max_price_manwon ? '와 지난 호가가 예산을 넘는 후보' : ''}는 뒤로 · 호가가 저렴하다는 이유만으로 우선하지 않습니다.</p>
          ${feedback ? `<p class="v2-row" role="status">${esc(feedback)}</p>` : ''}
          ${warning ? `<p class="v2-row v2-caution">${warning}</p>` : ''}
          ${financeNotice ? `<p class="v2-row v2-caution">${financeNotice}</p>` : ''}
          ${commuteNotice ? `<p class="v2-row v2-caution">${commuteNotice}</p>` : ''}
          ${spec.move_in_by ? '<p class="v2-row v2-caution">입주일은 상세 확인이 가능한 한방 후보를 먼저 보여 줍니다. 카드의 ‘입주일 확인’을 눌러 원천을 확인한 뒤에만 조건 부합으로 분류하며, 실제 입주는 중개사에게 다시 확인하세요.</p>' : ''}
          ${spec.max_monthly_manwon && data.finance_context?.status !== 'ready' && data.finance_context?.status !== 'profile_unavailable' ?
            '<button type="button" class="btn" data-v2-finance-setup>매수력 설정으로 이동</button>' : ''}
          ${spec.max_commute_minutes && data.commute_context?.status === 'missing_work' ?
            '<button type="button" class="btn" data-v2-work-setup>직장 위치 저장으로 이동</button>' : ''}${outside}
          <div id="v2DiscoveryGroups"></div><div id="v2DiscoveryPaging"></div>`;
      }
      const groupHost = result.querySelector('#v2DiscoveryGroups');
      const setup = result.querySelector('[data-v2-finance-setup]');
      if (setup) setup.onclick = () => { document.getElementById('v2DiscoverDlg').close(); switchTab('mypage'); };
      const workSetup = result.querySelector('[data-v2-work-setup]');
      if (workSetup) workSetup.onclick = () => {
        document.getElementById('v2DiscoverDlg').close();
        switchTab('mypage');
        document.getElementById('mpExtra').open = true;
        document.getElementById('mp_work')?.focus();
      };
      const sections = [['matched','조건 부합'],['verify','확인 필요'],['explore','탐색 후보'],
        ['exceeded','조건 초과 · 비교용']];
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
        section.querySelector('[data-v2-items]').insertAdjacentHTML('beforeend', rows.map(x => discoveryCard(x, spec, data.commute_context)).join(''));
      }
      if (!groupHost.querySelector('[data-v2-card]') && data.source_state !== 'unavailable' && data.source_state !== 'partial_empty')
        groupHost.innerHTML = `<p>현재 조건의 기본 후보가 없습니다.${data.counts.exceeded && !spec.include_exceeded ? ` 조건 초과 ${esc(data.counts.exceeded)}건을 비교용으로 보려면 위 선택란을 켜세요.` : ' 가격·면적·지역을 하나씩 조정해 보세요.'}</p>`;
      if (!cursor && !data.counts.matched && data.source_state !== 'unavailable' && data.source_state !== 'partial_empty') {
        const labels = {max_price_manwon:'호가 상한',min_area_m2:'최소 전용면적',min_rooms:'최소 방 개수',move_in_by:'입주 필요일',max_commute_minutes:'통근 상한',
          max_monthly_manwon:'월 상환 상한',region_code:'필수 지역'};
        const options = Object.entries(data.single_condition_relaxations || {}).filter(([field,count]) => labels[field] && count > 0);
        if (options.length) {
          const tip = document.createElement('div');
          tip.className = 'v2-row';
          tip.innerHTML = `<b>한 조건씩 다시 보기</b><p class="v2-muted">현재 수집분에서 다른 필수 조건을 확인한 후보만 셌습니다. 조건은 자동으로 바뀌지 않습니다.</p>`
            + options.map(([field,count]) => `<button type="button" class="btn" data-v2-relax="${field}">${esc(labels[field])} 조정 시 ${esc(count)}건 확인</button>`).join('');
          groupHost.prepend(tip);
          tip.querySelectorAll('[data-v2-relax]').forEach(button => button.onclick = () => {
            const input = form.elements[button.dataset.v2Relax];
            if (input?.closest('#v2DiscoverExtra')) document.getElementById('v2DiscoverExtra').open = true;
            input?.focus();
          });
        }
      }
      const total = (data.counts.matched || 0) + (data.counts.verify || 0) + (data.counts.explore || 0)
        + (spec.include_exceeded ? data.counts.exceeded || 0 : 0);
      const shown = groupHost.querySelectorAll('[data-v2-card]').length;
      const paging = result.querySelector('#v2DiscoveryPaging');
      paging.innerHTML = `<p class="v2-muted">현재 수집분 ${total}건 중 ${shown}건 표시${!spec.include_exceeded && data.counts.exceeded ? ` · 조건 초과 ${esc(data.counts.exceeded)}건은 기본 숨김` : ''}</p>`;
      if (data.next_cursor) {
        const more = document.createElement('button');
        more.type = 'button'; more.className = 'btn'; more.dataset.v2Next = '';
        more.textContent = '다음 후보 더 보기';
        more.onclick = () => runDiscovery(data.next_cursor);
        paging.appendChild(more);
      }
      result.querySelectorAll('[data-v2-listing]').forEach(button => button.onclick = () => {
        document.getElementById('v2DiscoverDlg').close();
        openListing(button.dataset.v2Listing, true);
      });
      result.querySelectorAll('[data-v2-occupancy]').forEach(button => button.onclick = async () => {
        const name = button.closest('[data-v2-card]')?.querySelector('b')?.textContent || '선택한 매물';
        const requestedDay = spec.move_in_by;
        const requestGeneration = discoveryGeneration;
        button.disabled = true;
        button.textContent = '입주일 확인 중…';
        try {
          const response = await fetch('/api/v2/discovery/occupancy', {method:'POST',
            headers:{'Content-Type':'application/json'},body:JSON.stringify({key:button.dataset.v2Occupancy})});
          if (!response.ok) throw new Error('상세 원천을 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.');
          const occupancy = await response.json();
          if (requestGeneration !== discoveryGeneration || !document.getElementById('v2DiscoverDlg').open) return;
          const exact = ['dated', 'immediate'].includes(occupancy.status) && /^\d{4}-\d{2}-\d{2}$/.test(occupancy.date || '');
          const feedback = exact && occupancy.date > requestedDay ?
            `${name}: 원천 입주 가능일 ${occupancy.date}은 요청한 ${requestedDay}보다 늦어 기본 후보에서 제외했습니다. 조건 초과 후보를 켜면 비교용으로 볼 수 있습니다.` :
            exact ? `${name}: 원천 입주 가능일 ${occupancy.date}을 확인했습니다. 실제 입주는 중개사에게 다시 확인하세요.` :
            `${name}: 원천 상세에도 정확한 입주일이 없어 확인 필요로 남겼습니다. 실제 입주일은 중개사에게 확인하세요.`;
          await runDiscovery(null, form.elements.move_in_by.value === requestedDay ? feedback : '');
        } catch (error) {
          button.textContent = error.message;
          button.disabled = false;
        }
      });
      result.querySelectorAll('[data-v2-commute]').forEach(button => button.onclick = async () => {
        const name = button.closest('[data-v2-card]')?.querySelector('b')?.textContent || '선택한 매물';
        const requestedMinutes = spec.max_commute_minutes;
        const requestGeneration = discoveryGeneration;
        button.disabled = true;
        button.textContent = '직장 경로 확인 중…';
        try {
          const response = await fetch('/api/v2/discovery/commute', {method:'POST',
            headers:{'Content-Type':'application/json'},body:JSON.stringify({key:button.dataset.v2Commute})});
          if (!response.ok) throw new Error('직장 경로를 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.');
          const commute = await response.json();
          if (requestGeneration !== discoveryGeneration || !document.getElementById('v2DiscoverDlg').open) return;
          const feedback = commute.status !== 'observed' ?
            `${name}: 경로 안내시간을 확인하지 못해 확인 필요로 남겼습니다.` :
            commute.minutes > requestedMinutes ?
              `${name}: 매물 표시 좌표 기준 대중교통 안내 ${commute.minutes}분으로 설정한 ${requestedMinutes}분을 넘어 기본 후보에서 제외했습니다. 조건 초과 후보에서 비교할 수 있습니다.` :
              `${name}: 매물 표시 좌표 기준 대중교통 안내 ${commute.minutes}분입니다. 실제 출입구·출퇴근 시간대는 별도로 확인하세요.`;
          await runDiscovery(null, form.elements.max_commute_minutes.value === String(requestedMinutes) ? feedback : '');
        } catch (error) {
          button.textContent = error.message;
          button.disabled = false;
        }
      });
      result.querySelectorAll('[data-v2-compare]').forEach(button => button.onclick = () => {
        button.textContent = addCompare(button.dataset.v2Compare);
        syncDiscoveryCompareAction();
      });
      syncDiscoveryCompareAction();
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
  document.getElementById('v2DiscoverForm')?.addEventListener('invalid', event => {
    for (const detail of [...(event.target.closest('#v2DiscoverExtra')?.querySelectorAll('details') || [])]) {
      if (detail.contains(event.target)) detail.open = true;
    }
    if (event.target.closest('#v2DiscoverExtra')) document.getElementById('v2DiscoverExtra').open = true;
  }, true);
  document.getElementById('v2DiscoverForm')?.addEventListener('input', updateDiscoveryExtraSummary);
  document.getElementById('v2DiscoverForm')?.addEventListener('change', updateDiscoveryExtraSummary);
  document.getElementById('v2DiscoverCompare')?.addEventListener('click', () => {
    listingCompareOpen();
  });
  document.getElementById('v2DiscoverForm')?.elements.region_code.addEventListener('change', () => {
    document.getElementById('v2DiscoverForm').querySelector('[type="submit"]').disabled = false;
    document.getElementById('v2DiscoverRegionStatus').textContent =
      '서울·인천처럼 이름이 같은 지역도 코드로 구별합니다.';
  });
  document.getElementById('v2NoteForm')?.addEventListener('submit', saveNote);
  window.SignalV2 = {paintRegion, openListing, openSavedReports, openDiscovery, openNote, openNotes, openInitialLink, saveComparison};
  if (window._signalAppReady && !document.getElementById('onbDlg')?.open) openInitialLink();
  if (typeof selected === 'string' && selected) paintRegion(selected);
})();
