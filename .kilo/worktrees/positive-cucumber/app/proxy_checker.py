import re
import time
from dataclasses import dataclass
from typing import Iterable, Optional

try:
    import requests
except ImportError:
    requests = None

USER_AGENT = "ProxyCheckerGUI/1.0"

@dataclass
class CheckResult:
    raw_proxy: str
    proxy_url: str
    status: str
    latency_ms: Optional[int]
    ip: str
    error: str

def normalize_proxy(raw_proxy: str, scheme: str) -> str:
    proxy = raw_proxy.strip()
    if "://" in proxy:
        return proxy
    return f"{scheme}://{proxy}"

def unique_proxy_entries(text: str) -> list[str]:
    seen: set[str] = set()
    proxies: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for item in re.split(r"[\s,;]+", stripped):
            item = item.strip()
            if not item or item.startswith("#"):
                continue
            if item not in seen:
                seen.add(item)
                proxies.append(item)
    return proxies

def extract_ip(response: requests.Response) -> str:
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            data = response.json()
            if isinstance(data, dict):
                return str(data.get("origin") or data.get("ip") or "")
        except ValueError:
            pass
    return response.text.strip()[:120]

def check_proxy(raw_proxy: str, schemes: Iterable[str], test_url: str, timeout: float) -> CheckResult:
    if requests is None:
        return CheckResult(raw_proxy, "", "ERROR", None, "", "Install dependency: pip install requests")

    last_error = ""
    headers = {"User-Agent": USER_AGENT}
    proxy_candidates = [raw_proxy.strip()] if "://" in raw_proxy else [normalize_proxy(raw_proxy, scheme) for scheme in schemes]
    
    for proxy_url in proxy_candidates:
        proxies = {"http": proxy_url, "https": proxy_url}
        start = time.perf_counter()
        try:
            response = requests.get(
                test_url,
                proxies=proxies,
                timeout=timeout,
                headers=headers,
            )
            latency_ms = int((time.perf_counter() - start) * 1000)
            if 200 <= response.status_code < 400:
                return CheckResult(raw_proxy, proxy_url, "LIVE", latency_ms, extract_ip(response), "")
            last_error = f"HTTP {response.status_code}"
        except requests.RequestException as exc:
            last_error = str(exc).replace("\n", " ")[:180]
            if "Missing dependencies for SOCKS support" in last_error:
                last_error = "Install SOCKS support: pip install pysocks"
            elif "No connection could be made" in last_error or "refused it" in last_error:
                last_error = "Connection Refused (Dead Proxy)"
            elif "Max retries exceeded" in last_error:
                last_error = "Timeout/Unreachable (Dead Proxy)"

    fallback_proxy = proxy_candidates[0] if proxy_candidates else raw_proxy
    return CheckResult(raw_proxy, fallback_proxy, "DEAD", None, "", last_error)
