"""Opt-in buyer cash-flow scenarios for listing discovery.

An indicative payment is not a verified lending rule or a bank approval.
"""

from __future__ import annotations

from dataclasses import replace
from math import isfinite

from realty_signal import buying_power, regulation
from realty_signal.services.buyer_decision import finance_fingerprint


class FinanceScenario:
    def __init__(self, profile: dict | None, *, sido_of):
        profile = profile if isinstance(profile, dict) else {}
        self._sido_of = sido_of
        self.params = None
        self.fingerprint = None
        self.policy = regulation.policy_manifest()
        saved = (profile or {}).get("매수력") or {}
        if not saved.get("가정버전"):
            self.status = "no_confirmed_profile"
            return
        try:
            params = buying_power.params_from_profile(profile)
            # The statement route supplies sido separately; it is not stored in
            # profile fields. Check both that form and older no-sido statements.
            candidates = [params]
            if params.region and not params.sido:
                try:
                    sido = self._sido_of(params.region)
                except (TypeError, ValueError, LookupError):
                    sido = None
                if sido:
                    candidates.insert(0, replace(params, sido=sido))
            selected = None
            for candidate in candidates:
                forms = [candidate]
                # Confirmation stores missing income as profile 0, although its
                # saved scenario may have used None (cash-only purchase).
                if not candidate.income:
                    forms.append(replace(candidate, income=None))
                for form in forms:
                    version = finance_fingerprint(form)
                    if saved["가정버전"] == version:
                        selected = (form, version)
                        break
                if selected:
                    break
            params, version = selected or (candidates[0], finance_fingerprint(candidates[0]))
        except (TypeError, ValueError, OverflowError):
            self.status = "invalid_profile"
            return
        self.fingerprint = version
        if params.capital <= 0 or saved["가정버전"] != version:
            self.status = "stale_profile"
            return
        self.params = params
        self.status = "ready"

    def for_row(self, row: dict) -> dict:
        if self.status != "ready":
            reason = ("저장된 매수력을 읽지 못했습니다. 잠시 후 다시 시도해 주세요."
                      if self.status == "profile_unavailable" else
                      "매수력을 다시 확정해야 월 부담을 비교할 수 있습니다.")
            return {"status": self.status, "reason": reason}
        try:
            price = float(row.get("총액"))
        except (TypeError, ValueError):
            price = 0
        if not 0 < price < buying_power.DEFAULTS["탐색상한"] or row.get("stale"):
            return {"status": "price_unknown", "reason": "현재 확인 가능한 호가가 없어 자금 계산을 보류합니다."}
        region = row.get("지역")
        try:
            sido = self._sido_of(region) if region else None
        except (TypeError, ValueError, LookupError):
            sido = None
        hint = regulation.sido_hint(region)
        inconsistent = ((hint is not None and hint != sido) or
                        (region in regulation.SEOUL_GU - {"중구", "강서구"} and sido != "서울") or
                        (region in regulation.REGULATED_GYEONGGI and sido != "경기"))
        if (not region or not sido or inconsistent or
                (region in {"중구", "강서구"} and hint is None)):
            return {"status": "region_unknown", "reason": "시·도와 지역 규제를 확정할 수 없어 자금 계산을 보류합니다."}
        area = (row.get("ref") or {}).get("전용면적")
        try:
            area = float(area)
        except (TypeError, ValueError):
            area = None
        if area is not None and (not isfinite(area) or area <= 0):
            area = None
        try:
            params = buying_power.params_for_region(self.params, region, sido)
            if area is not None:
                params = replace(params, big_area=area > 85)
            result = buying_power.for_price(price, params)
        except (TypeError, ValueError, OverflowError):
            return {"status": "calculation_failed", "reason": "이 매물의 자금 계산을 완료하지 못했습니다."}
        if self.policy.get("status") != "verified":
            status, reason = "policy_unverified", "대출 규제·세율 최신성을 확인하기 전까지 월 부담 조건을 확정하지 않습니다."
        elif area is None:
            status, reason = "area_unknown", "전용면적이 없어 취득 비용 가정을 확인해야 합니다."
        elif result.get("확인필요"):
            status, reason = "income_unknown", "소득·대출 가능액을 확인해야 합니다."
        else:
            status, reason = "assessed", "입력한 가정 기준 계산입니다. 금융기관 승인은 별도입니다."
        return {"status": status, "reason": reason,
                "monthly_manwon": result["총월상환"], "cash_manwon": result["필요현금"],
                "monthly_exact_manwon": result["총월상환정밀"],
                "possible": result["가능"], "engine_version": result["계산버전"],
                "policy_version": self.policy["version"], "policy_status": self.policy["status"]}
