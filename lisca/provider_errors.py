"""Classify provider failures only from structured, documented evidence."""
import re
import socket

import httpx


_CODE_CATEGORIES = {
    "InvalidApiKey": "authentication",
    "invalid_api_key": "authentication",
    "AccessDenied": "permission",
    "access_denied": "permission",
    "Model.AccessDenied": "permission",
    "App.AccessDenied": "permission",
    "AccessDenied.Unpurchased": "permission",
    "Arrearage": "quota",
    "BudgetLimitExceeded": "quota",
    "insufficient_quota": "quota",
    "CommodityNotPurchased": "quota",
    "PrepaidBillOverdue": "quota",
    "PostpaidBillOverdue": "quota",
    "Throttling.RateQuota": "rate_limited",
    "Throttling.AllocationQuota": "rate_limited",
    "Throttling.BurstRate": "rate_limited",
    "Throttling.Concurrency": "rate_limited",
    "LimitRequests": "rate_limited",
    "limit_requests": "rate_limited",
    "Throttling.ServiceOverloaded": "temporarily_unavailable",
    "Throttling.ResourceExhausted": "temporarily_unavailable",
    "InternalError": "temporarily_unavailable",
    "InternalError.Algo": "temporarily_unavailable",
}

_MESSAGES = {
    "authentication": "识别服务认证失败，请检查 API Key",
    "permission": "识别服务权限不足，请检查模型权限或服务开通状态",
    "quota": "识别额度或账单条件不满足，请检查百炼账户额度",
    "rate_limited": "识别请求触发服务限流，可稍后重试",
    "network": "识别网络连接暂时中断，可重试",
    "temporarily_unavailable": "识别服务暂不可用，可稍后重试",
    "unknown": "识别失败；现有服务商证据不足，原因未知",
}


def _provider_code(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", value):
        return None
    return value


def _provider_status(value):
    if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599:
        return value
    return None


def classify_provider_failure(error):
    """Return a safe category and evidence; never inspect raw exception text."""
    status = _provider_status(getattr(error, "provider_status", None))
    if status is None:
        status = _provider_status(getattr(error, "status_code", None))
    code = _provider_code(getattr(error, "provider_code", None))
    if code is None:
        code = _provider_code(getattr(error, "code", None))

    category = _CODE_CATEGORIES.get(code)
    if category is None and status == 401:
        category = "authentication"
    if category is None and status == 403:
        category = "permission"
    if category is None and status == 429:
        category = "rate_limited"
    if category is None and status is not None and 500 <= status <= 599:
        category = "temporarily_unavailable"
    if category is None and isinstance(error, (httpx.TransportError, socket.timeout,
                                                socket.gaierror, ConnectionError, TimeoutError)):
        category = "network"
    if category is None:
        category = "unknown"

    return {
        "failure_category": category,
        "retryable": category in ("rate_limited", "network", "temporarily_unavailable"),
        "provider_status": status,
        # Only allowlisted provider codes are exposed; arbitrary error fields may contain secrets.
        "provider_code": code if code in _CODE_CATEGORIES else None,
        "message": _MESSAGES[category],
    }
