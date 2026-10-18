"""Bounded HTTP retries, per-host throttling, immutable acquisition evidence."""
from __future__ import annotations

import email.utils
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import requests

SECRET_KEYS = {"api_key", "api-key", "apikey", "token", "access_token", "secret", "key", "client_secret", "authorization", "password", "signature"}


def redact_url(url):
    p = urlsplit(url)
    netloc=p.netloc.rsplit('@',1)[-1]
    return urlunsplit((p.scheme, netloc, p.path, urlencode([
        (k, "REDACTED" if k.lower() in SECRET_KEYS else v)
        for k, v in parse_qsl(p.query, keep_blank_values=True)]), p.fragment))


class HttpClient:
    def __init__(self, store, user_agent="EquityDataResearch/0.1", requests_per_second=2,
                 max_attempts=3, timeout=40):
        self.store = store
        self.user_agent = user_agent
        self.interval = 1 / max(0.01, min(float(requests_per_second), 5))
        self.max_attempts = max(1, min(int(max_attempts), 6))
        self.timeout = timeout
        self.session = requests.Session()
        self.last_request = {}
        self.budget = None

    def fetch(self, url, *, source, headers=None, params=None, refresh=True):
        req = requests.Request("GET", url, params=params).prepare()
        request_url = req.url
        safe_url = redact_url(request_url)
        host = urlsplit(request_url).hostname or ""
        request_headers = {"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"}
        request_headers.update(headers or {})
        if host == "sec.gov" or host.endswith(".sec.gov"):
            ua = request_headers["User-Agent"]
            if not re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", ua) or "example.com" in ua:
                self.store.issue("SEC_IDENTITY_REQUIRED", "Set SEC_USER_AGENT to a real contact identity.", dataset="filings")
                raise ValueError("SEC requires a real contact identity in User-Agent")
        if not refresh:
            prior = self.store.latest_raw(source, safe_url)
            if prior:
                path = Path(prior["path"])
                if not path.is_absolute():
                    path = self.store.root / path
                content = path.read_bytes()
                if hashlib.sha256(content).hexdigest() != prior["sha256"]:
                    raise ValueError("Raw cache hash mismatch")
                return content, prior
        for attempt in range(self.max_attempts):
            wait = self.interval - (time.monotonic() - self.last_request.get(host, 0))
            if wait > 0:
                time.sleep(wait)
            self.last_request[host] = time.monotonic()
            response = None
            try:
                if self.budget:
                    self.budget.before_request(source)
                    response = self.session.get(request_url, headers=request_headers, timeout=self.budget.timeout(self.timeout),stream=True)
                else:
                    response = self.session.get(request_url, headers=request_headers, timeout=self.timeout)
                if response.status_code in {408, 429, 500, 502, 503, 504}:
                    response.raise_for_status()
                if not response.ok:
                    raise requests.HTTPError(f"HTTP {response.status_code}", response=response)
                if self.budget:
                    chunks=[]
                    try:
                        for chunk in response.iter_content(chunk_size=65536):
                            self.budget.consume_bytes(source,len(chunk))
                            chunks.append(chunk)
                    finally:
                        response.close()
                    content=b''.join(chunks)
                else:
                    content = response.content
                content_type = response.headers.get("Content-Type", "application/octet-stream")
                if 'html' in content_type.lower():
                    title = re.search(r'<title[^>]*>(.*?)</title>', content[:100000].decode('utf-8',errors='replace'), re.S|re.I)
                    if title and re.search(r'access denied|request rate threshold exceeded|forbidden|just a moment|robot check', title.group(1), re.I):
                        raise ValueError('HTTP 200 error/challenge page is not source evidence')
                # Successful error pages must never be mistaken for data.
                if (urlsplit(request_url).path.endswith(".json") or "application/json" in content_type):
                    json.loads(content)
                raw = self.store.put_raw(content, source=source, url=safe_url, media_type=content_type,
                    metadata={"status_code": response.status_code, "etag": response.headers.get("ETag"),
                              "last_modified": response.headers.get("Last-Modified"),
                              "final_url": redact_url(response.url), "attempt": attempt + 1})
                self.store.checkpoint("http:" + hashlib.sha256(safe_url.encode()).hexdigest(),
                                      {"raw_id": raw["raw_id"], "retrieved_at": raw["retrieved_at"]})
                return content, raw
            except (requests.RequestException, ValueError) as exc:
                status = response.status_code if response is not None else None
                if self.budget and response is not None and hasattr(response,'close'):
                    response.close()
                transient = isinstance(exc, (requests.Timeout, requests.ConnectionError)) or status in {408,429,500,502,503,504}
                self.store.issue("HTTP_ATTEMPT_FAILED", f"{source}: {type(exc).__name__}; HTTP {status}",
                    severity="warning", details={"url": safe_url, "attempt": attempt+1, "status":status})
                if not transient or attempt + 1 == self.max_attempts:
                    raise RuntimeError(f"Fetch failed: {safe_url}; HTTP {status}; {type(exc).__name__}") from None
                delay = min(30, 2 ** attempt)
                retry_after = response.headers.get("Retry-After") if response is not None else None
                if retry_after:
                    try:
                        delay = min(60, max(delay, float(retry_after)))
                    except ValueError:
                        try:
                            delta = (email.utils.parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds()
                            delay = min(60, max(delay, delta))
                        except (TypeError, ValueError):
                            pass
                time.sleep(delay)
        raise RuntimeError("Unreachable HTTP retry state")
