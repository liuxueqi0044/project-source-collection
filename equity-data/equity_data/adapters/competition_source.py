"""Bounded public competition snapshots on the existing acquisition engine.

No competitive event is emitted here.  Current web observations are not
historical disclosure copies, including pages carrying an old publication date.
"""
from __future__ import annotations

import copy
import hashlib
import ipaddress
import json
import re
import socket
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter

from .base import BaseAdapter, DependencyMissing, UnsupportedCapability
from ..http import redact_url

PARSER_VERSION = "competition-web-1.0"
_REDIRECTS = {301, 302, 303, 307, 308}
_NEWS = re.compile(r"news|press|release|product|pricing|price|update|announcement", re.I)


def validate_url(url, allowed_domains, *, resolve=False):
    """Return canonical URL and public DNS addresses; reject mixed DNS answers."""
    p = urlsplit(str(url))
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        raise ValueError("COMPETITION_URL_REJECTED: public HTTP(S) without credentials required")
    host = p.hostname.rstrip(".").lower().encode("idna").decode("ascii")
    domains = [str(d).rstrip(".").lower().encode("idna").decode("ascii") for d in allowed_domains]
    if not domains or not any(host == d or host.endswith("." + d) for d in domains):
        raise ValueError("COMPETITION_DOMAIN_REJECTED: URL outside explicit allowed_domains")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("COMPETITION_PRIVATE_ADDRESS: local hostname")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address and not address.is_global:
        raise ValueError("COMPETITION_PRIVATE_ADDRESS: non-public literal address")
    port = p.port or (443 if p.scheme == "https" else 80)
    if port not in {80, 443}:
        raise ValueError("COMPETITION_PORT_REJECTED: only public HTTP/HTTPS ports supported")
    canonical = urlunsplit((p.scheme, p.netloc.lower(), p.path or "/", p.query, ""))
    addresses = []
    if resolve:
        # Fail closed on any private/reserved address, rather than selecting just
        # one attractive answer from mixed public/private DNS results.
        answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addresses = sorted({answer[4][0] for answer in answers}, key=lambda a: (":" in a, a))
        if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
            raise ValueError("COMPETITION_PRIVATE_ADDRESS: DNS answer contains non-public address")
    return canonical, addresses


class _PinnedAdapter(HTTPAdapter):
    """Connect to the validated address while checking TLS for the original host.

    This uses the installed, pinned requests 2.34.2 public subclass extension.
    DNS is resolved by validate_url once for each request, not again by urllib3.
    """
    def __init__(self, pins):
        self.pins = pins
        super().__init__(max_retries=0)

    def get_connection_with_tls_context(self, request, verify, proxies=None, cert=None):
        if proxies:
            raise ValueError("COMPETITION_PROXY_UNSUPPORTED: direct public HTTP transport required")
        parts = urlsplit(request.url)
        host = parts.hostname.rstrip(".").lower().encode("idna").decode("ascii")
        ip = self.pins[(host, parts.port or (443 if parts.scheme == "https" else 80))]
        host_params, pool_kwargs = self.build_connection_pool_key_attributes(request, verify, cert)
        host_params["host"] = ip
        if parts.scheme == "https":
            pool_kwargs.update(assert_hostname=host, server_hostname=host)
        return self.poolmanager.connection_from_host(**host_params, pool_kwargs=pool_kwargs)


def public_resolution(client, endpoint, url):
    """Opt-in DoH for fake-IP Windows DNS; never accept reserved target IPs."""
    mode = endpoint.get("transport", {}).get("dns_mode", "system")
    if mode == "system":
        return validate_url(url, endpoint["allowed_domains"], resolve=True)
    if mode != "cloudflare_doh":
        raise UnsupportedCapability("COMPETITION_DNS_MODE_UNSUPPORTED")
    canonical, _ = validate_url(url, endpoint["allowed_domains"], resolve=False)
    host = urlsplit(canonical).hostname.rstrip(".").lower().encode("idna").decode("ascii")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address:
        return canonical, [str(address)]  # validate_url already rejected private literals
    cache = getattr(client, "_competition_dns_cache", None)
    if cache is None:
        cache = client._competition_dns_cache = {}
    if host in cache and cache[host][0] > time.monotonic():
        return canonical, cache[host][1]
    resolver = "cloudflare-dns.com"
    session = requests.Session()
    session.trust_env = False
    session.mount("https://", _PinnedAdapter({(resolver, 443): "1.1.1.1"}))
    try:
        for query_type, code in (("A", 1), ("AAAA", 28)):
            wait = client.interval - (time.monotonic() - client.last_request.get(resolver, 0))
            if wait > 0:
                time.sleep(wait)
            if client.budget:
                client.budget.before_request("competition_web")
            client.last_request[resolver] = time.monotonic()
            response = session.get("https://cloudflare-dns.com/dns-query", params={"name": host, "type": query_type},
                headers={"Host": resolver, "Accept": "application/dns-json", "User-Agent": client.user_agent},
                allow_redirects=False, stream=True, timeout=client.budget.timeout(client.timeout) if client.budget else client.timeout)
            try:
                chunks = []
                for chunk in response.iter_content(chunk_size=65536):
                    if client.budget:
                        client.budget.consume_bytes("competition_web", len(chunk))
                    chunks.append(chunk)
                body = b"".join(chunks)
                client.store.put_raw(body, source="competition_dns", url=redact_url(response.url),
                    media_type=response.headers.get("Content-Type", "application/dns-json"),
                    metadata={"status_code": response.status_code, "resolver": resolver, "bootstrap_public_ip": "1.1.1.1",
                              "queried_hostname": host, "query_type": query_type, "purpose": "public_address_validation"})
                if response.status_code != 200:
                    raise ValueError("COMPETITION_DOH_FAILED: HTTPS resolver did not return 200")
                payload = json.loads(body)
            finally:
                response.close()
            if payload.get("Status") != 0 or payload.get("TC"):
                raise ValueError("COMPETITION_DOH_FAILED: DNS failure or truncated answer")
            records = [r for r in payload.get("Answer", []) if r.get("type") == code]
            addresses = sorted({str(ipaddress.ip_address(r["data"])) for r in records})
            if any(not ipaddress.ip_address(a).is_global for a in addresses):
                raise ValueError("COMPETITION_PRIVATE_ADDRESS: DoH returned non-public address")
            if addresses:
                ttl = min([300] + [max(0, int(r.get("TTL", 0))) for r in payload.get("Answer", [])])
                cache[host] = (time.monotonic() + ttl, addresses)
                return canonical, addresses
        raise ValueError("COMPETITION_DOH_FAILED: no supported public A/AAAA answer")
    finally:
        session.close()


class GuardedSession:
    """Follow redirects explicitly, charging/throttling every extra request."""
    def __init__(self, client, endpoint, *, transport=None):
        self.client, self.endpoint, self.pins = client, endpoint, {}
        self.transport = transport or requests.Session()
        if transport is None:
            self.transport.trust_env = False  # no ambient proxies or .netrc secrets
            for scheme in ("http://", "https://"):
                self.transport.mount(scheme, _PinnedAdapter(self.pins))
        self.max_redirects = min(5, max(0, int(endpoint.get("max_redirects", 5))))
        self.initial_resolution = None

    def close(self):
        self.transport.close()

    def get(self, url, **kwargs):
        headers = dict(kwargs.pop("headers", {}))
        kwargs.pop("allow_redirects", None)
        current = url
        for hop in range(self.max_redirects + 1):
            if hop == 0 and self.initial_resolution and self.initial_resolution[0] == current:
                current, addresses = self.initial_resolution
                self.initial_resolution = None
            else:
                current, addresses = public_resolution(self.client, self.endpoint, current)
            p = urlsplit(current)
            host = p.hostname.rstrip(".").lower().encode("idna").decode("ascii")
            port = p.port or (443 if p.scheme == "https" else 80)
            self.pins[(host, port)] = addresses[0]
            headers["Host"] = ("[" + host + "]" if ":" in host else host) + (":" + str(p.port) if p.port else "")
            if hop:
                wait = self.client.interval - (time.monotonic() - self.client.last_request.get(host, 0))
                if wait > 0:
                    time.sleep(wait)
                if self.client.budget:
                    self.client.budget.before_request("competition_web")
                    kwargs["timeout"] = self.client.budget.timeout(self.client.timeout)
                self.client.last_request[host] = time.monotonic()
            response = self.transport.get(current, headers=headers, allow_redirects=False, **kwargs)
            if response.status_code not in _REDIRECTS:
                return response
            try:
                chunks = []
                for chunk in response.iter_content(chunk_size=65536):
                    if self.client.budget:
                        self.client.budget.consume_bytes("competition_web", len(chunk))
                    chunks.append(chunk)
                body = b"".join(chunks)
                location = response.headers.get("Location")
                self.client.store.put_raw(body, source="competition_web", url=redact_url(current),
                    media_type=response.headers.get("Content-Type", "application/octet-stream"),
                    metadata={"status_code": response.status_code, "location": redact_url(urljoin(current, location or "")),
                              "redirect_hop": hop, "transport": "public_dns_pinned"})
            finally:
                response.close()
            if not location or hop >= self.max_redirects:
                raise ValueError("COMPETITION_REDIRECT_LIMIT: missing Location or too many redirects")
            destination = urljoin(current, location)
            # This happens before a request to the target, including DNS checks.
            validate_url(destination, self.endpoint["allowed_domains"], resolve=False)
            if p.scheme == "https" and urlsplit(destination).scheme != "https":
                raise ValueError("COMPETITION_REDIRECT_DOWNGRADE: HTTPS to HTTP refused")
            current = destination
        raise AssertionError("unreachable redirect state")


def _context(endpoint):
    value = copy.deepcopy(endpoint.get("observation_context") or {})
    value.update({key: endpoint.get(key) for key in ("locale", "currency", "auth_context", "selectors", "config_version",
            "business_scope", "product_or_business", "geography", "customer_segment", "official_status", "format")
            if endpoint.get(key) is not None})
    return value


def endpoint_resource(endpoint, plan_id, *, url=None, depth=0, parent=None):
    url, _ = validate_url(url or endpoint["url"], endpoint.get("allowed_domains", []))
    signature = hashlib.sha256(json.dumps([endpoint["entity_id"], url, _context(endpoint)], sort_keys=True).encode()).hexdigest()
    return {"source_id": "competition_web", "resource_key": "competition:web:" + signature,
            "kind": "competition_endpoint" if depth == 0 else "competition_document", "data_type": "competition_snapshots",
            "url": url, "company_id": endpoint.get("company_id"), "security_id": None,
            "metadata": {"endpoint": copy.deepcopy(endpoint), "plan_id": plan_id, "depth": depth,
                         "parent": parent, "refresh": True, "immutable_archive": False}}


def _links(content, endpoint, base):
    channel = endpoint.get("channel", "web")
    if channel in {"rss", "atom", "sitemap"}:
        soup = BeautifulSoup(content, "xml")
        if channel == "sitemap":
            for i, node in enumerate(soup.find_all("loc")):
                yield node.get_text(strip=True), "sitemap/loc[" + str(i + 1) + "]"
        else:
            for i, node in enumerate(soup.find_all(["item", "entry"])):
                link = node.find("link")
                if link:
                    yield link.get("href") or link.get_text(strip=True), "feed/item[" + str(i + 1) + "]/link"
        return
    soup = BeautifulSoup(content, "html.parser")
    selector = endpoint.get("link_selector")
    nodes = soup.select(selector) if selector else soup.find_all("a", href=True)
    for i, node in enumerate(nodes):
        value = node.get("href", "")
        if selector or _NEWS.search(node.get_text(" ", strip=True) + " " + value):
            yield value, "css:" + selector if selector else "a[" + str(i + 1) + "]@href"


class CompetitionWebAdapter(BaseAdapter):
    source_id = "competition_web"
    scope_kind = "global"
    data_types = ("competition_snapshots",)

    def discover(self, ctx, scope):
        plan = ctx.config.get("_competition_plan")
        if not plan:
            ctx.gap("COMPETITION_PLAN_REQUIRED", "No frozen reviewed competition monitoring plan provided", source_id=self.source_id,
                    data_type="competition_snapshots", status="not_discovered")
            return {"enumeration_complete": False, "discovered": 0}
        count, errors = 0, []
        for endpoint in plan.get("endpoints", []):
            try:
                if endpoint.get("fetch_mode", "http") != "http":
                    raise UnsupportedCapability("COMPETITION_RENDERER_UNAVAILABLE: optional dynamic renderer not installed")
                resource = endpoint_resource(endpoint, plan["plan_id"])
                resource["metadata"]["cycle_generation"] = ctx.config.get("_generation", "backfill")
                ctx.emit(resource)
                count += 1
            except (ValueError, UnsupportedCapability) as exc:
                errors.append({"endpoint_id": endpoint.get("endpoint_id"), "reason": str(exc)})
                ctx.gap("COMPETITION_ENDPOINT_UNAVAILABLE", str(exc), source_id=self.source_id,
                        data_type="competition_snapshots", details=errors[-1])
        return {"enumeration_complete": not errors, "discovered": count, "errors": errors,
                "coverage_basis": "Frozen configured entrypoints; not complete open-web disclosure coverage"}

    def acquire(self, ctx, resource):
        endpoint = copy.deepcopy(resource["metadata"]["endpoint"])
        endpoint.setdefault("transport", getattr(ctx, "config", {}).get("competition", {}).get("transport", {}))
        if endpoint.get("fetch_mode", "http") != "http":
            raise UnsupportedCapability("COMPETITION_RENDERER_UNAVAILABLE")
        if endpoint.get("auth_context", "public") not in (None, "public", "anonymous"):
            raise DependencyMissing("COMPETITION_AUTH_UNSUPPORTED: authorized login transport not configured")
        if getattr(ctx.client, "offline", False):
            validate_url(resource["url"], endpoint.get("allowed_domains", []), resolve=False)
            return ctx.client.fetch(resource["url"], source=self.source_id, refresh=False)
        initial_resolution = public_resolution(ctx.client, endpoint, resource["url"])
        if not hasattr(ctx.client, "session"):
            # Explicit dependency-injected test transports retain URI validation;
            # real HttpClient instances always use the guarded pinned transport.
            return ctx.client.fetch(resource["url"], source=self.source_id, refresh=True)
        guarded = copy.copy(ctx.client)
        guarded.session = GuardedSession(ctx.client, endpoint)
        guarded.session.initial_resolution = initial_resolution
        try:
            return guarded.fetch(resource["url"], source=self.source_id, refresh=True,
                                 headers={"Accept-Language": endpoint.get("locale") or "en"})
        finally:
            guarded.session.close()

    def parse(self, ctx, resource, content, raw):
        from ..competition.change_detector import normalize_snapshot
        endpoint = resource["metadata"]["endpoint"]
        endpoint_for_parse = dict(endpoint)
        if resource["metadata"].get("depth", 0):
            endpoint_for_parse.update(format="html", channel="web", selectors=endpoint.get("document_selectors", {}))
        elif endpoint.get("channel") in {"rss", "atom"}:
            endpoint_for_parse.setdefault("format", "rss")
        endpoint_for_parse["observation_context"] = _context(endpoint_for_parse)
        snapshot = normalize_snapshot(content, endpoint_for_parse)
        data = {**snapshot, "endpoint_id": endpoint["endpoint_id"], "entity_id": endpoint["entity_id"],
                "url": resource["url"], "source_url": resource["url"], "source_content_sha256": raw["sha256"],
                "raw_id": raw["raw_id"], "resource_key": resource["resource_key"], "observation_context": _context(endpoint_for_parse),
                "official_status": endpoint.get("official_status", "unknown"), "entry_evidence_refs": endpoint.get("evidence_refs", []),
                "semantic_stage": "raw_normalized_snapshot", "is_structured_competitive_fact": False}
        # Acquisition IDs/times belong to envelope + evidence rows, not semantic
        # payload: an unchanged refresh must not manufacture a snapshot revision.
        prior = ctx.store.db.execute("SELECT * FROM records WHERE dataset='competition_snapshots' AND source=? AND record_key=? ORDER BY sequence DESC LIMIT 1",
                                     (self.source_id, resource["resource_key"])).fetchone()
        available = prior["available_at"] if prior and json.loads(prior["data_json"]) == data else raw["retrieved_at"]
        record_id = ctx.store.put_record("competition_snapshots", resource["resource_key"], data,
            source=self.source_id, raw_id=raw["raw_id"], company_id=resource.get("company_id"),
            published_at=snapshot.get("publication_time"), publication_precision=snapshot.get("publication_precision", "unknown"),
            available_at=available, availability_basis="first_observed_current_web_snapshot",
            source_locator="document", parser_version=snapshot.get("parser_version", PARSER_VERSION),
            quality="normalized_needs_verification" if snapshot.get("valid") else "normalization_rejected")
        errors = list(snapshot.get("issues", []))
        children, omitted = self._enumerate(ctx, resource, content, raw) if snapshot.get("valid") else (0, [])
        if omitted:
            errors.append({"code": "COMPETITION_ENUMERATION_LIMIT", "message": "Discovered links remain unacquired", "omitted": omitted})
        for error in errors:
            ctx.gap(error.get("code", "COMPETITION_NORMALIZATION_LIMIT") if isinstance(error, dict) else "COMPETITION_NORMALIZATION_LIMIT",
                    str(error), source_id=self.source_id, data_type="competition_snapshots", status="partial_parse",
                    details={"resource_key": resource["resource_key"], "raw_id": raw["raw_id"], "issue": error})
        return {"status": "partial" if errors or not snapshot.get("valid") else "parsed", "record_ids": [record_id],
                "parsed_count": int(bool(snapshot.get("valid"))), "errors": errors, "children_discovered": children,
                "semantic_stage": "raw_normalized_snapshot", "events_complete": False}

    def _enumerate(self, ctx, resource, content, raw):
        meta, endpoint = resource["metadata"], resource["metadata"]["endpoint"]
        if not endpoint.get("enumerate_links", endpoint.get("channel") in {"rss", "atom", "sitemap"}):
            return 0, []
        max_depth = min(3, max(0, int(endpoint.get("max_depth", 1))))
        maximum = min(1000, max(0, int(endpoint.get("max_documents", 20))))
        current_depth = int(meta.get("depth", 0))
        prior_urls = {row[0] for row in ctx.store.db.execute("SELECT url FROM machine_inventory WHERE source_id=? AND json_extract(metadata_json,'$.endpoint.endpoint_id')=? AND json_extract(metadata_json,'$.plan_id')=? AND json_extract(metadata_json,'$.cycle_generation')=?",
                                                           (self.source_id, endpoint["endpoint_id"], meta["plan_id"], ctx.config.get("_generation", "backfill")))}
        omitted, emitted = [], 0
        for value, locator in _links(content, endpoint if current_depth == 0 else {**endpoint, "channel": "web"}, resource["url"]):
            try:
                url, _ = validate_url(urljoin(resource["url"], value), endpoint.get("allowed_domains", []))
            except ValueError as exc:
                omitted.append({"url": redact_url(urljoin(resource["url"], value)), "reason": str(exc), "locator": locator})
                continue
            if url in prior_urls or url == endpoint["url"]:
                continue
            if current_depth >= max_depth or len(prior_urls) - 1 >= maximum:
                omitted.append({"url": redact_url(url), "reason": "depth_limit" if current_depth >= max_depth else "document_limit", "locator": locator})
                continue
            child = endpoint_resource(endpoint, meta["plan_id"], url=url, depth=current_depth + 1,
                                      parent={"raw_id": raw["raw_id"], "sha256": raw["sha256"], "url": resource["url"], "locator": locator})
            child["metadata"]["cycle_generation"] = ctx.config.get("_generation", "backfill")
            ctx.emit(child)
            prior_urls.add(url)
            emitted += 1
        return emitted, omitted

    def health(self, ctx):
        return {"status": "implemented", "network_checked": False, "source_id": self.source_id,
                "capabilities": ["public static HTTP snapshots", "RSS/Atom links", "bounded same-domain links", "public DNS-pinned redirects"],
                "limitations": ["current observations only", "no browser rendering", "no login/auth transport", "no all-web completeness", "snapshot success is not event extraction success"]}
