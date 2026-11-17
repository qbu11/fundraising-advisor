#!/usr/bin/env python3
"""Query and rank fundraising candidates without exposing API credentials."""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import random
import sys
import time
import unicodedata
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://api.miracleplus.com/build"
DEFAULT_SCOPE = "public funds"
ALLOWED_ROOT = {"project", "preferences", "top_k", "language"}
ALLOWED_PROJECT = {
    "name",
    "summary",
    "product",
    "sectors",
    "stage",
    "round",
    "amount",
    "traction",
    "business_model",
    "team_highlights",
    "location",
    "use_of_funds",
    "fundraising_goal",
}
ALLOWED_AMOUNT = {"min_wan_cny", "max_wan_cny", "currency"}
ALLOWED_PREFERENCES = {"person_expertise", "exclude_funds", "compliance"}


class InputError(ValueError):
    pass


class ApiError(RuntimeError):
    def __init__(self, status: int | None, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code


def load_skill_env() -> None:
    """Load the ignored skill-local .env without overriding process variables."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] == value[-1:] and value[:1] in {'"', "'"}:
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def _dedupe_text(values: list[Any]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            output.append(text)
    return output


def _as_text_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise InputError(f"{field} must be an array")
    return _dedupe_text(value)


def _reject_query_delimiter(values: list[str], field: str) -> None:
    if any("," in value for value in values):
        raise InputError(f"{field} values cannot contain commas; provide separate array items")


def _strict_keys(value: dict[str, Any], allowed: set[str], field: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise InputError(f"{field} contains unknown fields: {', '.join(unknown)}")


def _amount_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InputError(f"{field} must be an integer in RMB 10k")
    if value < 0:
        raise InputError(f"{field} must be non-negative")
    return value


def normalize_project(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise InputError("input must be a JSON object")
    _strict_keys(raw, ALLOWED_ROOT, "root")
    project = raw.get("project")
    if not isinstance(project, dict):
        raise InputError("project must be an object")
    _strict_keys(project, ALLOWED_PROJECT, "project")

    summary = str(project.get("summary") or "").strip()
    sectors = _as_text_list(project.get("sectors"), "project.sectors")
    _reject_query_delimiter(sectors, "project.sectors")
    if not summary:
        raise InputError("project.summary is required")
    if not sectors:
        raise InputError("project.sectors requires at least one value")

    amount_raw = project.get("amount")
    amount = None
    if amount_raw is not None:
        if not isinstance(amount_raw, dict):
            raise InputError("project.amount must be an object or null")
        _strict_keys(amount_raw, ALLOWED_AMOUNT, "project.amount")
        if set(amount_raw) != ALLOWED_AMOUNT:
            raise InputError("project.amount requires min_wan_cny, max_wan_cny, and currency")
        minimum = _amount_integer(amount_raw["min_wan_cny"], "project.amount.min_wan_cny")
        maximum = _amount_integer(amount_raw["max_wan_cny"], "project.amount.max_wan_cny")
        currency = str(amount_raw.get("currency") or "").strip()
        if not currency:
            raise InputError("project.amount.currency is required")
        if minimum > maximum:
            raise InputError("project.amount.min_wan_cny cannot exceed max_wan_cny")
        amount = {"min_wan_cny": minimum, "max_wan_cny": maximum, "currency": currency}

    preferences_raw = raw.get("preferences") or {}
    if not isinstance(preferences_raw, dict):
        raise InputError("preferences must be an object")
    _strict_keys(preferences_raw, ALLOWED_PREFERENCES, "preferences")
    compliance = _as_text_list(preferences_raw.get("compliance") or ["trusted"], "preferences.compliance")
    invalid_compliance = sorted(set(compliance) - {"trusted", "suspect"})
    if invalid_compliance:
        raise InputError(f"invalid compliance values: {', '.join(invalid_compliance)}")

    top_k = raw.get("top_k", 15)
    if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 30:
        raise InputError("top_k must be an integer from 1 to 30")

    stage = _optional_text(project.get("stage"))
    if stage:
        _reject_query_delimiter([stage], "project.stage")
    person_expertise = _as_text_list(
        preferences_raw.get("person_expertise") or [], "preferences.person_expertise"
    )
    _reject_query_delimiter(person_expertise, "preferences.person_expertise")

    normalized_project = {
        "name": _optional_text(project.get("name")),
        "summary": summary,
        "product": _optional_text(project.get("product")),
        "sectors": sectors,
        "stage": stage,
        "round": _optional_text(project.get("round")),
        "amount": amount,
        "traction": _optional_text(project.get("traction")),
        "business_model": _optional_text(project.get("business_model")),
        "team_highlights": _as_text_list(project.get("team_highlights") or [], "project.team_highlights"),
        "location": _optional_text(project.get("location")),
        "use_of_funds": _as_text_list(project.get("use_of_funds") or [], "project.use_of_funds"),
        "fundraising_goal": _optional_text(project.get("fundraising_goal")),
    }
    return {
        "project": normalized_project,
        "preferences": {
            "person_expertise": person_expertise,
            "exclude_funds": _as_text_list(
                preferences_raw.get("exclude_funds") or [], "preferences.exclude_funds"
            ),
            "compliance": compliance,
        },
        "top_k": top_k,
        "language": str(raw.get("language") or "zh-CN"),
    }


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def load_taxonomy() -> dict[str, Any]:
    path = Path(__file__).resolve().parent.parent / "references" / "taxonomy.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _alias_key(value: Any) -> str:
    """Return the comparison key shared by canonicalization and query expansion."""
    return " ".join(unicodedata.normalize("NFKC", str(value)).strip().split()).casefold()


def _matching_alias_groups(value: str, aliases: dict[str, list[str]]) -> list[tuple[str, list[str]]]:
    key = _alias_key(value)
    canonical_match = next(
        ((canonical, items) for canonical, items in aliases.items() if _alias_key(canonical) == key),
        None,
    )
    if canonical_match:
        return [canonical_match]

    matches = [
        (canonical, items)
        for canonical, items in aliases.items()
        if any(_alias_key(alias) == key for alias in items)
    ]
    # Ambiguous broad labels such as “早期” intentionally belong to several
    # stage groups. Do not guess a canonical value in that case.
    return matches if len(matches) == 1 else []


def canonicalize_aliases(values: list[str], aliases: dict[str, list[str]]) -> list[str]:
    """Normalize registered aliases to one canonical value for persisted/input data."""
    canonical: list[str] = []
    for value in values:
        matches = _matching_alias_groups(value, aliases)
        canonical.append(matches[0][0] if matches else value)
    return _dedupe_text(canonical)


def expand_aliases(values: list[str], aliases: dict[str, list[str]]) -> list[str]:
    """Expand canonical values or any unambiguous alias to the same query terms."""
    output: list[str] = []
    for value in values:
        matches = _matching_alias_groups(value, aliases)
        if matches:
            canonical, items = matches[0]
            output.extend([canonical, *items])
        else:
            output.append(value)
    return _dedupe_text(output)


def build_query_passes(profile: dict[str, Any], taxonomy: dict[str, Any]) -> list[dict[str, Any]]:
    project = profile["project"]
    preferences = profile["preferences"]
    sectors = expand_aliases(project["sectors"], taxonomy.get("sector_aliases", {}))
    stage = project.get("stage")
    stages = expand_aliases([stage], taxonomy.get("stage_aliases", {})) if stage else []
    base: dict[str, Any] = {
        "sector_focus": ",".join(sectors),
        "include": "research,compliance",
        "order_by": "updated_at_desc",
    }
    if stages:
        base["investment_stage"] = ",".join(stages)
    expertise = preferences.get("person_expertise") or []
    if expertise:
        base["person_expertise"] = ",".join(expertise)
    amount = project.get("amount")
    if amount:
        base["currency"] = amount["currency"]

    passes: list[dict[str, Any]] = []

    def add(name: str, params: dict[str, Any]) -> None:
        signature = json.dumps(params, ensure_ascii=False, sort_keys=True)
        if not any(item["_signature"] == signature for item in passes):
            passes.append({"name": name, "params": params, "_signature": signature})

    strict = dict(base)
    if amount:
        strict.update({"min_amount": amount["min_wan_cny"], "max_amount": amount["max_wan_cny"]})
    if "trusted" in preferences["compliance"]:
        strict["compliance_overall"] = "trusted"
    add("strict", strict)

    if amount:
        fit = dict(base)
        fit.update({"min_amount": amount["min_wan_cny"], "max_amount": amount["max_wan_cny"]})
        add("fit_without_compliance", fit)

        discovery = dict(base)
        if "trusted" in preferences["compliance"]:
            discovery["compliance_overall"] = "trusted"
        add("discovery_unknown_ticket", discovery)

    for item in passes:
        item.pop("_signature", None)
    return passes


def parse_amount(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number < 0 or number != number.to_integral_value():
        return None
    return int(number)


def ranges_overlap(inv_min: int | None, inv_max: int | None, ask_min: int, ask_max: int) -> bool:
    if inv_min is None or inv_max is None:
        return False
    return inv_min <= ask_max and inv_max >= ask_min


@dataclass
class PageResult:
    items: list[dict[str, Any]]
    total: int
    fetched: int
    unique: int
    duplicates: int
    complete: bool
    pages: int


class InvestorApi:
    def __init__(self) -> None:
        self.base_url = os.getenv("INVESTOR_API_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        if not self.base_url.startswith("https://"):
            raise InputError("INVESTOR_API_BASE_URL must use HTTPS")
        self.client_id = os.getenv("INVESTOR_API_CLIENT_ID")
        self.client_secret = os.getenv("INVESTOR_API_CLIENT_SECRET")
        if not self.client_id or not self.client_secret:
            raise InputError(
                "missing INVESTOR_API_CLIENT_ID or INVESTOR_API_CLIENT_SECRET environment variable"
            )
        self.scope = os.getenv("INVESTOR_API_SCOPE", DEFAULT_SCOPE)
        self.timeout = float(os.getenv("INVESTOR_API_TIMEOUT_SECONDS", "20"))
        self._token: str | None = None
        self._token_expires_at = 0.0

    def _get_token(self, force: bool = False) -> str:
        if not force and self._token and time.time() < self._token_expires_at - 60:
            return self._token
        basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode("utf-8")).decode("ascii")
        body = urlencode({"grant_type": "client_credentials", "scope": self.scope}).encode("ascii")
        request = Request(
            f"{self.base_url}/oauth/token",
            data=body,
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "User-Agent": "FundraisingAdvisorSkill/1.0",
            },
            method="POST",
        )
        payload = self._send(request, allow_retry=False)
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not token:
            raise ApiError(None, "invalid_token_response", "token response did not contain access_token")
        self._token = str(token)
        self._token_expires_at = time.time() + int(payload.get("expires_in") or 3600)
        return self._token

    def get(self, path: str, params: dict[str, Any] | None = None, retry_auth: bool = True) -> Any:
        query = f"?{urlencode(params or {})}" if params else ""
        request = Request(
            f"{self.base_url}/open_api/v2/server{path}{query}",
            headers={
                "Authorization": f"Bearer {self._get_token()}",
                "Accept": "application/json",
                "User-Agent": "FundraisingAdvisorSkill/1.0",
            },
            method="GET",
        )
        try:
            return self._send(request, allow_retry=True)
        except ApiError as error:
            if error.status == 401 and retry_auth:
                self._get_token(force=True)
                return self.get(path, params, retry_auth=False)
            raise

    def _send(self, request: Request, allow_retry: bool) -> Any:
        attempts = 3 if allow_retry else 1
        for attempt in range(attempts):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    content = response.read()
                    return json.loads(content.decode("utf-8")) if content else {}
            except HTTPError as error:
                content = error.read()
                payload: Any = None
                if content:
                    try:
                        payload = json.loads(content.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        payload = None
                code = payload.get("error") if isinstance(payload, dict) else None
                message = payload.get("message") if isinstance(payload, dict) else None
                if error.code in (429, 500, 502, 503, 504) and attempt + 1 < attempts:
                    retry_after = error.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else 0.5 * (2**attempt)
                    time.sleep(delay + random.uniform(0, 0.2))
                    continue
                if error.code == 403 and not message:
                    message = "insufficient scope or access denied"
                raise ApiError(error.code, str(code or f"http_{error.code}"), str(message or "API request failed"))
            except (URLError, TimeoutError) as error:
                if attempt + 1 < attempts:
                    time.sleep(0.5 * (2**attempt) + random.uniform(0, 0.2))
                    continue
                raise ApiError(None, "network_error", f"API network failure: {type(error).__name__}")
        raise ApiError(None, "request_failed", "API request failed")

    def fetch_pages(self, params: dict[str, Any], max_pages: int) -> PageResult:
        seen: dict[str, dict[str, Any]] = {}
        fetched = 0
        total = 0
        pages = 0
        consecutive_empty = 0
        for page in range(1, max_pages + 1):
            request_params = dict(params)
            request_params.update({"page": page, "page_size": 100})
            payload = self.get("/fund_investors", request_params)
            if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                raise ApiError(None, "invalid_schema", "fund_investors returned an invalid response")
            pages = page
            total = int(payload.get("total") or 0)
            items = payload["items"]
            fetched += len(items)
            before = len(seen)
            for item in items:
                identifier = str(item.get("id") or "")
                if identifier:
                    seen.setdefault(identifier, item)
            consecutive_empty = consecutive_empty + 1 if len(seen) == before else 0
            if len(seen) >= total or len(items) < 100 or consecutive_empty >= 2:
                break
        return PageResult(
            items=list(seen.values()),
            total=total,
            fetched=fetched,
            unique=len(seen),
            duplicates=fetched - len(seen),
            complete=len(seen) >= total,
            pages=pages,
        )


class FixtureApi(InvestorApi):
    """Credential-free replay of the investor API for demos and end-to-end tests.

    The fixture file mirrors the shape of real responses: a ``funds`` object keyed
    by fund id plus an ``investors`` array. Query parameters are matched locally
    with the same semantics as the server: OR within a multi-value field, AND
    across fields, and unknown ticket sizes are dropped when an amount is asked.
    """

    def __init__(self, fixture: dict[str, Any]) -> None:
        if not isinstance(fixture, dict) or not isinstance(fixture.get("investors"), list):
            raise InputError("fixture must be an object with an investors array")
        funds = fixture.get("funds") or {}
        if not isinstance(funds, dict):
            raise InputError("fixture funds must be an object keyed by fund id")
        self.funds = {str(key): value for key, value in funds.items() if isinstance(value, dict)}
        self.investors = [item for item in fixture["investors"] if isinstance(item, dict)]

    def _fund_detail(self, item: dict[str, Any]) -> dict[str, Any]:
        fund = item.get("fund") if isinstance(item.get("fund"), dict) else {}
        return self.funds.get(str(fund.get("id") or ""), {})

    @staticmethod
    def _contains_any(value: Any, terms: list[str]) -> bool:
        text = str(value or "").casefold()
        return any(term.casefold() in text for term in terms if term)

    def _matches(self, item: dict[str, Any], params: dict[str, Any]) -> bool:
        fund_detail = self._fund_detail(item)
        sectors = [value for value in str(params.get("sector_focus") or "").split(",") if value]
        if sectors:
            fund_sectors = fund_detail.get("org_sector_focus") or []
            if isinstance(fund_sectors, list) and fund_sectors:
                if not self._contains_any(" ".join(str(value) for value in fund_sectors), sectors):
                    return False
            elif not self._contains_any(
                f"{item.get('organization') or ''} {item.get('direction') or ''}", sectors
            ):
                return False
        stages = [value for value in str(params.get("investment_stage") or "").split(",") if value]
        if stages:
            fund_stages = fund_detail.get("org_investment_stage") or []
            stage_text = " ".join(str(value) for value in fund_stages) if isinstance(fund_stages, list) else str(fund_stages)
            if not self._contains_any(stage_text, stages):
                return False
        currency = str(params.get("currency") or "")
        if currency and currency.casefold() not in str(item.get("currency") or "").casefold():
            return False
        compliance = str(params.get("compliance_overall") or "")
        if compliance and str(item.get("compliance_overall") or "").casefold() != compliance.casefold():
            return False
        expertise = [value for value in str(params.get("person_expertise") or "").split(",") if value]
        if expertise and not self._contains_any(item.get("person_expertise"), expertise):
            return False
        min_amount = parse_amount(params.get("min_amount"))
        max_amount = parse_amount(params.get("max_amount"))
        if min_amount is not None and max_amount is not None:
            inv_min = parse_amount(item.get("inv_min"))
            inv_max = parse_amount(item.get("inv_max"))
            if inv_min is None or inv_max is None:
                return False
            if not ranges_overlap(inv_min, inv_max, min_amount, max_amount):
                return False
        query = str(params.get("q") or "").casefold()
        if query:
            haystack = f"{item.get('name') or ''} {item.get('organization') or ''}".casefold()
            if query not in haystack:
                return False
        return True

    def fetch_pages(self, params: dict[str, Any], max_pages: int) -> PageResult:
        matched = [item for item in self.investors if self._matches(item, params)]
        matched.sort(key=lambda item: (str(item.get("last_updated") or ""), str(item.get("id") or "")))
        page = parse_amount(params.get("page")) or 1
        page_size = max(1, parse_amount(params.get("page_size")) or 20)
        start = (page - 1) * page_size
        items = matched[start : start + page_size]
        return PageResult(
            items=items,
            total=len(matched),
            fetched=len(items),
            unique=len(items),
            duplicates=0,
            complete=start + len(items) >= len(matched),
            pages=min(page, max_pages),
        )

    def get(self, path: str, params: dict[str, Any] | None = None, retry_auth: bool = True) -> Any:
        if path.startswith("/funds/"):
            return self.funds.get(path[len("/funds/"):]) or {}
        if path == "/funds":
            return {"total": len(self.funds), "items": list(self.funds.values())}
        raise ApiError(None, "fixture_route_not_supported", f"fixture does not implement {path}")


def _contains_any(text: Any, terms: list[str]) -> bool:
    value = str(text or "").casefold()
    return any(term.casefold() in value for term in terms if term)


def score_candidate(
    item: dict[str, Any],
    fund_detail: dict[str, Any] | None,
    profile: dict[str, Any],
    taxonomy: dict[str, Any],
    matched_passes: list[str],
) -> tuple[float, list[str], list[str], dict[str, float]]:
    project = profile["project"]
    reasons: list[str] = []
    gaps: list[str] = []
    breakdown: dict[str, float] = {}

    pass_score = 15.0 if "strict" in matched_passes else 10.0 if "fit_without_compliance" in matched_passes else 5.0
    breakdown["query_fit"] = pass_score
    reasons.append(f"命中查询层级：{', '.join(matched_passes)}")

    compliance = str(item.get("compliance_overall") or "").lower()
    compliance_score = 10.0 if compliance == "trusted" else 2.0 if compliance == "suspect" else 0.0
    breakdown["compliance"] = compliance_score
    if compliance == "trusted":
        reasons.append("合规结论为 trusted")
    elif compliance:
        gaps.append(f"合规结论为 {compliance}，联系前核验")
    else:
        gaps.append("缺少合规结论")

    amount_score = 0.0
    amount = project.get("amount")
    inv_min = parse_amount(item.get("inv_min"))
    inv_max = parse_amount(item.get("inv_max"))
    if amount:
        if ranges_overlap(inv_min, inv_max, amount["min_wan_cny"], amount["max_wan_cny"]):
            amount_score = 15.0
            reasons.append(f"已知票面 {inv_min}–{inv_max} 万元与融资区间重叠")
        elif inv_min is None or inv_max is None:
            gaps.append("投资金额边界未知，需确认")
        else:
            gaps.append(f"已知票面 {inv_min}–{inv_max} 万元不匹配")
    breakdown["amount"] = amount_score

    expertise_terms = profile["preferences"].get("person_expertise") or expand_aliases(
        project["sectors"], taxonomy.get("sector_aliases", {})
    )
    expertise_text = " ".join(
        str(item.get(key) or "")
        for key in ("person_expertise", "direction", "person_background", "person_representative_deals")
    )
    expertise_score = 15.0 if _contains_any(expertise_text, expertise_terms) else 0.0
    breakdown["personal_expertise"] = expertise_score
    if expertise_score:
        reasons.append("个人专业方向与项目赛道有文本证据匹配")
    else:
        gaps.append("个人赛道专长证据不足")

    sector_score = 0.0
    stage_score = 0.0
    if fund_detail:
        sector_values = fund_detail.get("org_sector_focus") or []
        sector_text = " ".join(sector_values if isinstance(sector_values, list) else [str(sector_values)])
        sector_terms = expand_aliases(project["sectors"], taxonomy.get("sector_aliases", {}))
        if _contains_any(sector_text, sector_terms):
            sector_score = 25.0
            reasons.append("机构投资领域与项目赛道匹配")
        else:
            gaps.append("机构赛道详情未显示直接匹配")
        if project.get("stage"):
            stage_values = fund_detail.get("org_investment_stage") or []
            stage_text = " ".join(stage_values if isinstance(stage_values, list) else [str(stage_values)])
            stage_terms = expand_aliases([project["stage"]], taxonomy.get("stage_aliases", {}))
            if _contains_any(stage_text, stage_terms):
                stage_score = 15.0
                reasons.append("机构投资阶段匹配")
            else:
                gaps.append("机构阶段详情需进一步确认")
    else:
        gaps.append("未取得机构详情")
    breakdown["sector"] = sector_score
    breakdown["stage"] = stage_score

    confidence = str(item.get("person_data_confidence") or "")
    verification = str(item.get("person_verification_status") or "")
    evidence_score = {"高": 5.0, "中": 3.0, "低": 1.0}.get(confidence, 0.0)
    if verification == "verified":
        evidence_score = min(5.0, evidence_score + 2.0)
    breakdown["evidence_quality"] = evidence_score
    if not confidence:
        gaps.append("缺少个人数据置信度")

    total = round(sum(breakdown.values()), 1)
    return total, reasons[:6], gaps[:6], breakdown


def _safe_excerpt(value: Any, limit: int = 400) -> str | None:
    text = str(value or "").strip()
    return text[:limit] if text else None


def candidate_sort_key(
    candidate: dict[str, Any], source_items: dict[str, dict[str, Any]]
) -> tuple[int, float, int, str]:
    investor_id = candidate["investor"]["id"]
    return (
        0 if candidate["recommendation_tier"] == "core" else 1,
        -float(candidate["score"]["total"]),
        -int(source_items[investor_id].get("reviews_count") or 0),
        investor_id,
    )


def recommend(
    profile: dict[str, Any],
    api: InvestorApi,
    top_k: int,
    max_pages: int,
    max_per_fund: int,
) -> dict[str, Any]:
    taxonomy = load_taxonomy()
    passes = build_query_passes(profile, taxonomy)
    merged: dict[str, dict[str, Any]] = {}
    matched: dict[str, list[str]] = {}
    trace: list[dict[str, Any]] = []
    warnings: list[str] = []

    for query_pass in passes:
        result = api.fetch_pages(query_pass["params"], max_pages=max_pages)
        trace.append(
            {
                "name": query_pass["name"],
                "params": query_pass["params"],
                "server_total": result.total,
                "fetched": result.fetched,
                "unique": result.unique,
                "duplicates": result.duplicates,
                "pages": result.pages,
                "complete": result.complete,
            }
        )
        if not result.complete:
            warnings.append(f"{query_pass['name']} 查询达到页数上限，结果可能不完整")
        if result.duplicates:
            warnings.append(f"{query_pass['name']} 查询跨页发现 {result.duplicates} 条重复记录")
        for item in result.items:
            identifier = str(item.get("id") or "")
            if not identifier:
                continue
            merged.setdefault(identifier, item)
            matched.setdefault(identifier, []).append(query_pass["name"])

    excluded = {value.casefold() for value in profile["preferences"].get("exclude_funds") or []}
    candidates: list[dict[str, Any]] = []
    fund_cache: dict[str, dict[str, Any] | None] = {}

    preliminary = sorted(
        merged.values(),
        key=lambda item: (
            0 if str(item.get("compliance_overall") or "").lower() == "trusted" else 1,
            -int(item.get("reviews_count") or 0),
            str(item.get("id") or ""),
        ),
    )
    detail_budget = max(top_k * 3, 20)
    for item in preliminary[:detail_budget]:
        if str(item.get("compliance_overall") or "").lower() == "risk":
            continue
        project_amount = profile["project"].get("amount")
        inv_min = parse_amount(item.get("inv_min"))
        inv_max = parse_amount(item.get("inv_max"))
        amount_verified = not project_amount
        if project_amount:
            if inv_min is not None and inv_max is not None:
                if not ranges_overlap(
                    inv_min,
                    inv_max,
                    project_amount["min_wan_cny"],
                    project_amount["max_wan_cny"],
                ):
                    continue
                amount_verified = True
        fund = item.get("fund") if isinstance(item.get("fund"), dict) else {}
        fund_id = str(fund.get("id") or "")
        fund_name = str(fund.get("chinese_name") or item.get("organization") or "").strip()
        if fund_id.casefold() in excluded or fund_name.casefold() in excluded:
            continue
        if fund_id and fund_id not in fund_cache:
            try:
                detail = api.get(f"/funds/{fund_id}")
                fund_cache[fund_id] = detail if isinstance(detail, dict) else None
            except ApiError as error:
                fund_cache[fund_id] = None
                warnings.append(f"机构 {fund_id} 详情获取失败：{error.code}")
        fund_detail = fund_cache.get(fund_id)
        identifier = str(item.get("id"))
        score, reasons, gaps, breakdown = score_candidate(
            item, fund_detail, profile, taxonomy, matched.get(identifier, [])
        )
        compliance = str(item.get("compliance_overall") or "").lower()
        candidates.append(
            {
                "recommendation_tier": (
                    "core" if compliance == "trusted" and amount_verified else "verify"
                ),
                "investor": {
                    "id": identifier,
                    "name": item.get("name"),
                    "job_title": _optional_text(item.get("job_title")),
                },
                "fund": {
                    "id": fund_id or None,
                    "name": fund_name or None,
                    "sector_focus": fund_detail.get("org_sector_focus") if fund_detail else None,
                    "investment_stage": fund_detail.get("org_investment_stage") if fund_detail else None,
                },
                "score": {"total": score, "breakdown": breakdown},
                "match_reasons": reasons,
                "gaps_and_risks": gaps,
                "professional_context": {
                    "expertise": _safe_excerpt(item.get("person_expertise")),
                    "direction": _safe_excerpt(item.get("direction")),
                    "representative_deals": _safe_excerpt(item.get("person_representative_deals")),
                    "background": _safe_excerpt(item.get("person_background")),
                },
                "investment_range_wan_cny": {
                    "min": parse_amount(item.get("inv_min")),
                    "max": parse_amount(item.get("inv_max")),
                    "source_text": _safe_excerpt(item.get("inv_range"), 120),
                },
                "data_quality": {
                    "confidence": item.get("person_data_confidence"),
                    "verification_status": item.get("person_verification_status"),
                    "compliance_overall": item.get("compliance_overall"),
                    "last_updated": item.get("last_updated"),
                },
                "evidence": {
                    "source": "miracleplus_investor_api",
                    "investor_id": identifier,
                    "fund_id": fund_id or None,
                },
            }
        )

    candidates.sort(key=lambda item: candidate_sort_key(item, merged))

    selected: list[dict[str, Any]] = []
    fund_counts: dict[str, int] = {}
    for candidate in candidates:
        fund_key = str(candidate["fund"]["id"] or candidate["fund"]["name"] or "")
        if fund_counts.get(fund_key, 0) >= max_per_fund:
            continue
        fund_counts[fund_key] = fund_counts.get(fund_key, 0) + 1
        candidate["rank"] = len(selected) + 1
        selected.append(candidate)
        if len(selected) >= top_k:
            break

    all_core = bool(selected) and all(
        candidate["recommendation_tier"] == "core" for candidate in selected
    )
    status = "complete" if all_core and not warnings else "partial" if selected else "no_match"
    return {
        "schema_version": "1.0",
        "status": status,
        "retrieved_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "project_profile": profile["project"],
        "recommendations": selected,
        "data_quality": {"warnings": _dedupe_text(warnings)},
        "query_trace": {"attempts": trace, "merged_unique": len(merged)},
    }


class OfflineTests(unittest.TestCase):
    def test_normalization_and_aliases(self) -> None:
        profile = normalize_project(
            {
                "project": {
                    "summary": "AI product for factories",
                    "sectors": ["AI"],
                    "amount": {"min_wan_cny": 1000, "max_wan_cny": 2000, "currency": "人民币"},
                }
            }
        )
        passes = build_query_passes(profile, load_taxonomy())
        self.assertIn("AI", passes[0]["params"]["sector_focus"])
        self.assertIn("人工智能", passes[0]["params"]["sector_focus"])

    def test_alias_inputs_generate_identical_query_plans(self) -> None:
        plans = []
        for sector in ("AI", "人工智能", "ai", "Artificial Intelligence"):
            profile = normalize_project({"project": {"summary": "x", "sectors": [sector]}})
            plans.append(build_query_passes(profile, load_taxonomy()))
        self.assertTrue(all(plan == plans[0] for plan in plans[1:]))

    def test_unknown_field_rejected(self) -> None:
        with self.assertRaises(InputError):
            normalize_project({"project": {"summary": "x", "sectors": ["AI"], "typo": True}})

    def test_query_delimiter_in_sector_is_rejected(self) -> None:
        with self.assertRaises(InputError):
            normalize_project({"project": {"summary": "x", "sectors": ["AI,机器人"]}})

    def test_reversed_amount_rejected(self) -> None:
        with self.assertRaises(InputError):
            normalize_project(
                {
                    "project": {
                        "summary": "x",
                        "sectors": ["AI"],
                        "amount": {"min_wan_cny": 20, "max_wan_cny": 10, "currency": "人民币"},
                    }
                }
            )

    def test_amount_parser_and_closed_overlap(self) -> None:
        self.assertEqual(parse_amount("1000.0"), 1000)
        self.assertIsNone(parse_amount("1.5"))
        self.assertTrue(ranges_overlap(1000, 3000, 3000, 5000))
        self.assertFalse(ranges_overlap(None, 3000, 1000, 2000))

    def test_pagination_deduplicates_ids(self) -> None:
        class FakeApi(InvestorApi):
            def __init__(self) -> None:
                pass

            def get(self, path: str, params: dict[str, Any] | None = None, retry_auth: bool = True) -> Any:
                page = int((params or {}).get("page") or 1)
                if page == 1:
                    return {"total": 101, "items": [{"id": str(index)} for index in range(100)]}
                return {"total": 101, "items": [{"id": "99"}, {"id": "100"}]}

        result = FakeApi().fetch_pages({"order_by": "updated_at_desc"}, max_pages=2)
        self.assertEqual(result.unique, 101)
        self.assertEqual(result.duplicates, 1)
        self.assertTrue(result.complete)

    def test_trusted_core_sorts_before_higher_scoring_verify(self) -> None:
        source = {"1": {"reviews_count": 0}, "2": {"reviews_count": 0}}
        core = {
            "recommendation_tier": "core",
            "score": {"total": 60},
            "investor": {"id": "1"},
        }
        verify = {
            "recommendation_tier": "verify",
            "score": {"total": 90},
            "investor": {"id": "2"},
        }
        ordered = sorted([verify, core], key=lambda item: candidate_sort_key(item, source))
        self.assertEqual(ordered[0]["investor"]["id"], "1")

    def test_known_non_overlapping_amount_is_excluded(self) -> None:
        class FakeApi(InvestorApi):
            def __init__(self) -> None:
                pass

            def fetch_pages(self, params: dict[str, Any], max_pages: int) -> PageResult:
                item = {
                    "id": "1",
                    "name": "Out of range",
                    "inv_min": "5000.0",
                    "inv_max": "10000.0",
                    "compliance_overall": "trusted",
                    "fund": {"id": "10", "chinese_name": "Example Fund"},
                }
                return PageResult([item], 1, 1, 1, 0, 1, True)

        profile = normalize_project(
            {
                "project": {
                    "summary": "AI product for factories",
                    "sectors": ["AI"],
                    "amount": {
                        "min_wan_cny": 1000,
                        "max_wan_cny": 2000,
                        "currency": "人民币",
                    },
                }
            }
        )
        result = recommend(profile, FakeApi(), top_k=5, max_pages=1, max_per_fund=3)
        self.assertEqual(result["status"], "no_match")
        self.assertEqual(result["recommendations"], [])

    def test_unknown_amount_is_verify_and_partial(self) -> None:
        class FakeApi(InvestorApi):
            def __init__(self) -> None:
                pass

            def fetch_pages(self, params: dict[str, Any], max_pages: int) -> PageResult:
                item = {
                    "id": "1",
                    "name": "Unknown ticket",
                    "inv_min": None,
                    "inv_max": None,
                    "compliance_overall": "trusted",
                    "fund": {"id": "10", "chinese_name": "Example Fund"},
                }
                return PageResult([item], 1, 1, 1, 0, 1, True)

            def get(self, path: str, params: dict[str, Any] | None = None, retry_auth: bool = True) -> Any:
                return {"id": "10", "org_sector_focus": ["人工智能"]}

        profile = normalize_project(
            {
                "project": {
                    "summary": "AI product for factories",
                    "sectors": ["AI"],
                    "amount": {
                        "min_wan_cny": 1000,
                        "max_wan_cny": 2000,
                        "currency": "人民币",
                    },
                }
            }
        )
        result = recommend(profile, FakeApi(), top_k=5, max_pages=1, max_per_fund=3)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["recommendations"][0]["recommendation_tier"], "verify")

    def test_synthetic_fixture_runs_end_to_end(self) -> None:
        fixture_path = Path(__file__).resolve().parent.parent / "fixtures" / "synthetic-replay.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        profile = normalize_project(
            {
                "project": {
                    "summary": "AI quality inspection for manufacturers",
                    "sectors": ["AI"],
                    "amount": {
                        "min_wan_cny": 1000,
                        "max_wan_cny": 2000,
                        "currency": "人民币",
                    },
                },
                "preferences": {"person_expertise": ["AI"], "compliance": ["trusted"]},
            }
        )
        result = recommend(profile, FixtureApi(fixture), top_k=10, max_pages=3, max_per_fund=3)
        self.assertIn(result["status"], ("complete", "partial"))
        ids = [item["investor"]["id"] for item in result["recommendations"]]
        self.assertIn("I-CORE-1", ids)
        self.assertNotIn("I-RISK", ids)
        self.assertNotIn("I-OUT", ids)
        fund_counts: dict[str, int] = {}
        for item in result["recommendations"]:
            fund_key = str(item["fund"]["id"])
            fund_counts[fund_key] = fund_counts.get(fund_key, 0) + 1
        self.assertTrue(all(count <= 3 for count in fund_counts.values()))
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("synthetic-core@example.invalid", serialized)
        self.assertNotIn("13800138000", serialized)


def _read_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Recommend fundraising investors from the primary API")
    parser.add_argument("--project", help="Path to strict project JSON")
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument("--max-per-fund", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--fixture",
        help="Path to a synthetic replay fixture; runs the full pipeline without credentials or network",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(OfflineTests)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1
    if not args.project:
        parser.error("--project is required unless --self-test is used")
    if not 1 <= args.max_pages <= 20:
        raise InputError("--max-pages must be from 1 to 20")
    if not 1 <= args.max_per_fund <= 10:
        raise InputError("--max-per-fund must be from 1 to 10")

    try:
        profile = normalize_project(_read_json(args.project))
        top_k = args.top_k if args.top_k is not None else profile["top_k"]
        if not 1 <= top_k <= 30:
            raise InputError("--top-k must be from 1 to 30")
        taxonomy = load_taxonomy()
        if args.dry_run:
            output = {
                "status": "dry_run",
                "project_profile": profile,
                "query_passes": build_query_passes(profile, taxonomy),
            }
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return 0
        if args.fixture:
            fixture = _read_json(args.fixture)
            api: InvestorApi = FixtureApi(fixture)
        else:
            load_skill_env()
            api = InvestorApi()
        output = recommend(profile, api, top_k, args.max_pages, args.max_per_fund)
        if args.fixture:
            output["data_source"] = {
                "mode": "fixture",
                "fixture_path": str(args.fixture),
                "notice": "合成演示数据，仅用于验证流程，不代表真实投资人",
            }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except (InputError, ApiError, OSError, json.JSONDecodeError) as error:
        code = error.code if isinstance(error, ApiError) else "invalid_input"
        output = {
            "schema_version": "1.0",
            "status": "data_source_unavailable" if isinstance(error, ApiError) else "needs_input",
            "error": {"code": code, "message": str(error)},
            "recommendations": [],
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
