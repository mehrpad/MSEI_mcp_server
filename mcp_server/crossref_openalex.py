#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import requests

try:  # pragma: no cover - optional dependency
    from mcp.server.fastmcp import FastMCP
except Exception:  # pragma: no cover
    FastMCP = None  # type: ignore

try:  # pragma: no cover - optional dependency
    import yaml
except Exception:  # pragma: no cover
    yaml = None  # type: ignore

try:  # pragma: no cover - optional dependency
    from qdrant_client import QdrantClient
except Exception:  # pragma: no cover
    QdrantClient = None  # type: ignore

try:  # pragma: no cover - package context
    from .cache import SQLiteTTLCache
except Exception:  # pragma: no cover - script context
    from cache import SQLiteTTLCache


SCRIPT_DIR = Path(__file__).resolve().parent
# Unused here (kept for parity with the reference repo). In the Docker image the
# module lives flat at /app, which has no grandparent — guard against IndexError.
_parents = SCRIPT_DIR.parents
REPO_ROOT = _parents[1] if len(_parents) > 1 else SCRIPT_DIR
_APP_SUPPORT = Path.home() / "Library" / "Application Support" / "agent-augmented-research"
DEFAULT_CACHE_DB = _APP_SUPPORT / "cache" / "crossref_openalex" / "cache.sqlite"
DEFAULT_QDRANT_CONFIG_PATH = None

DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"'<>#%{}|\\^~\[\]`]+", re.I)
OPENALEX_ID_RE = re.compile(r"(?:https?://openalex\.org/)?(W\d+)$", re.I)
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
TAG_RE = re.compile(r"<[^>]+>")
ENV_PATTERN = re.compile(r"\$\{([^}:]+)(?::-(.*?))?\}")


@dataclass
class ServerConfig:
    cache_db: str
    user_agent: str
    mailto: str
    crossref_timeout: int
    openalex_timeout: int
    work_ttl_seconds: int
    query_ttl_seconds: int
    resolution_ttl_seconds: int
    negative_ttl_seconds: int
    qdrant_config_path: str
    qdrant_profile: Optional[str]
    qdrant_default_collection: str
    qdrant_presence_ttl_seconds: int
    qdrant_presence_absent_ttl_seconds: int
    qdrant_snapshot_ttl_seconds: int
    qdrant_snapshot_batch_size: int


def _normalize_doi(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    doi = str(value).strip()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.I)
    doi = re.sub(r"^doi\s*:\s*", "", doi, flags=re.I)
    doi = doi.strip().strip(" \t\r\n).,;:]}>{\"'`!?\\")
    doi = doi.lower()
    if not doi or not DOI_RE.search(doi):
        return None
    return doi


def _normalize_openalex_id(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    m = OPENALEX_ID_RE.search(str(value).strip())
    if not m:
        return None
    return m.group(1).upper()


def _normalize_text(value: Optional[str]) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_text_for_match(value: Optional[str]) -> str:
    text = _normalize_text(value).lower()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _similarity(a: Optional[str], b: Optional[str]) -> float:
    sa = _normalize_text_for_match(a)
    sb = _normalize_text_for_match(b)
    if not sa or not sb:
        return 0.0
    return SequenceMatcher(None, sa, sb).ratio()


def _parse_year(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int) and 1900 <= value <= 2100:
        return value
    m = YEAR_RE.search(str(value))
    if not m:
        return None
    year = int(m.group(0))
    if 1900 <= year <= 2100:
        return year
    return None


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        def replacer(match: re.Match[str]) -> str:
            var, default = match.group(1), match.group(2)
            env_val = os.getenv(var)
            if env_val is None:
                return default if default is not None else ""
            return env_val

        return ENV_PATTERN.sub(replacer, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def _first_str(value: Any) -> str:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                return item.strip()
        return ""
    if isinstance(value, str):
        return value.strip()
    return ""


def _strip_html(value: Optional[str]) -> str:
    text = str(value or "")
    if not text:
        return ""
    return _normalize_text(TAG_RE.sub(" ", text))


def _decode_openalex_abstract(inverted_index: Any) -> str:
    if not isinstance(inverted_index, dict) or not inverted_index:
        return ""
    max_pos = -1
    for positions in inverted_index.values():
        if not isinstance(positions, list):
            continue
        for pos in positions:
            if isinstance(pos, int) and pos > max_pos:
                max_pos = pos
    if max_pos < 0:
        return ""

    words: List[str] = [""] * (max_pos + 1)
    for token, positions in inverted_index.items():
        if not isinstance(token, str) or not isinstance(positions, list):
            continue
        for pos in positions:
            if isinstance(pos, int) and 0 <= pos <= max_pos:
                words[pos] = token
    return _normalize_text(" ".join(w for w in words if w))


def _normalize_author_family(value: Optional[str]) -> str:
    if not value:
        return ""
    raw = _normalize_text(value)
    if not raw:
        return ""
    if "," in raw:
        return raw.split(",", 1)[0].strip().lower()
    parts = raw.split()
    return (parts[-1] if parts else "").strip().lower()


def _error(code: str, message: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def _cache_meta(hit: bool, entry: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "hit": hit,
        "cache_key": entry.get("cache_key"),
        "fetched_at": entry.get("fetched_at"),
        "expires_at": entry.get("expires_at"),
        "stale": bool(entry.get("stale")),
        "age_seconds": entry.get("age_seconds"),
        "source": entry.get("source"),
        "status_code": entry.get("status_code"),
        "payload_hash": entry.get("payload_hash"),
    }


class CrossrefOpenAlexBackend:
    def __init__(self, config: ServerConfig) -> None:
        self.config = config
        self.cache = SQLiteTTLCache(config.cache_db)
        self._qdrant_runtime: Optional[Dict[str, Any]] = None
        self._qdrant_client: Optional[Any] = None

    def _headers(self) -> Dict[str, str]:
        ua = self.config.user_agent.strip() or "crossref-openalex-mcp/0.1"
        if self.config.mailto.strip() and "mailto:" not in ua:
            ua = f"{ua} (mailto:{self.config.mailto.strip()})"
        return {
            "User-Agent": ua,
            "Accept": "application/json",
        }

    def _get_qdrant_runtime(self) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        if self._qdrant_runtime is not None:
            err = self._qdrant_runtime.get("_error")
            if err:
                return None, str(err)
            return self._qdrant_runtime, None

        if QdrantClient is None:
            self._qdrant_runtime = {"_error": "qdrant-client package is not installed."}
            return None, str(self._qdrant_runtime["_error"])
        if yaml is None:
            self._qdrant_runtime = {"_error": "PyYAML is required to load Qdrant config."}
            return None, str(self._qdrant_runtime["_error"])

        config_path = Path(self.config.qdrant_config_path).expanduser().resolve()
        if not config_path.exists():
            self._qdrant_runtime = {"_error": f"Qdrant config not found: {config_path}"}
            return None, str(self._qdrant_runtime["_error"])

        try:
            with config_path.open("r", encoding="utf-8") as handle:
                raw = yaml.safe_load(handle) or {}
            raw = _expand_env(raw)
        except Exception as exc:
            self._qdrant_runtime = {"_error": f"Failed to parse Qdrant config: {exc}"}
            return None, str(self._qdrant_runtime["_error"])

        if not isinstance(raw, dict):
            self._qdrant_runtime = {"_error": "Qdrant config YAML must be a mapping."}
            return None, str(self._qdrant_runtime["_error"])

        active_profile = self.config.qdrant_profile or raw.get("active_profile")
        profiles = raw.get("profiles")
        if not isinstance(profiles, dict) or not profiles:
            self._qdrant_runtime = {"_error": "Qdrant config is missing a non-empty 'profiles' mapping."}
            return None, str(self._qdrant_runtime["_error"])
        if not active_profile:
            active_profile = next(iter(profiles.keys()))
        profile_data = profiles.get(active_profile)
        if not isinstance(profile_data, dict):
            self._qdrant_runtime = {"_error": f"Qdrant profile '{active_profile}' not found."}
            return None, str(self._qdrant_runtime["_error"])

        qdrant_cfg = profile_data.get("qdrant")
        if not isinstance(qdrant_cfg, dict):
            self._qdrant_runtime = {
                "_error": f"Qdrant profile '{active_profile}' does not define a 'qdrant' section."
            }
            return None, str(self._qdrant_runtime["_error"])

        qdrant_url = _normalize_text(qdrant_cfg.get("url"))
        if not qdrant_url:
            self._qdrant_runtime = {"_error": f"Qdrant profile '{active_profile}' has empty qdrant.url."}
            return None, str(self._qdrant_runtime["_error"])

        collections_node = qdrant_cfg.get("collections")
        collections = collections_node if isinstance(collections_node, dict) else {}
        runtime = {
            "config_path": str(config_path),
            "profile": str(active_profile),
            "url": qdrant_url,
            "api_key": _normalize_text(qdrant_cfg.get("api_key")) or None,
            "collections": {
                "text": _normalize_text(collections.get("text")),
                "figures": _normalize_text(collections.get("figures")),
            },
        }
        self._qdrant_runtime = runtime
        return runtime, None

    def _get_qdrant_client(self) -> Tuple[Optional[Any], Optional[str]]:
        runtime, err = self._get_qdrant_runtime()
        if err:
            return None, err
        if runtime is None:
            return None, "Qdrant runtime is unavailable."
        if self._qdrant_client is None:
            try:
                self._qdrant_client = QdrantClient(
                    url=runtime["url"],
                    api_key=runtime.get("api_key"),
                )
            except Exception as exc:
                return None, f"Failed to create Qdrant client: {exc}"
        return self._qdrant_client, None

    def _resolve_qdrant_collection(self, collection: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        runtime, err = self._get_qdrant_runtime()
        if err:
            return None, err
        if runtime is None:
            return None, "Qdrant runtime is unavailable."
        alias = _normalize_text(collection or self.config.qdrant_default_collection or "text").lower()
        if alias == "text":
            name = _normalize_text((runtime.get("collections") or {}).get("text")) or "text"
            return name, None
        if alias == "figures":
            name = _normalize_text((runtime.get("collections") or {}).get("figures")) or "figures"
            return name, None
        return alias, None

    def _collect_qdrant_dois(self, collection_name: str) -> Tuple[Optional[set[str]], int, Optional[str]]:
        client, err = self._get_qdrant_client()
        if err:
            return None, 0, err
        if client is None:
            return None, 0, "Qdrant client is unavailable."

        unique: set[str] = set()
        missing = 0
        offset = None
        batch_size = max(50, int(self.config.qdrant_snapshot_batch_size))

        try:
            while True:
                points, next_offset = client.scroll(
                    collection_name=collection_name,
                    with_payload=["doi"],
                    with_vectors=False,
                    limit=batch_size,
                    offset=offset,
                )
                if not points:
                    break
                for point in points:
                    payload = point.payload or {}
                    doi_norm = _normalize_doi(payload.get("doi"))
                    if doi_norm:
                        unique.add(doi_norm)
                    else:
                        missing += 1
                if next_offset is None:
                    break
                offset = next_offset
        except Exception as exc:
            return None, missing, f"Failed to scan Qdrant collection '{collection_name}': {exc}"

        return unique, missing, None

    def _refresh_qdrant_snapshot(
        self,
        collection_name: str,
        reason: str,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        doi_set, missing_count, err = self._collect_qdrant_dois(collection_name)
        if err:
            return None, err
        if doi_set is None:
            return None, "Qdrant DOI snapshot returned no data."

        source = f"qdrant_snapshot:{reason}"
        self.cache.write_presence_batch(
            collection_name=collection_name,
            doi_to_present={doi: True for doi in doi_set},
            ttl_seconds=self.config.qdrant_presence_ttl_seconds,
            source=source,
        )
        snapshot = self.cache.set_presence_snapshot(
            collection_name=collection_name,
            doi_count=len(doi_set),
            ttl_seconds=self.config.qdrant_snapshot_ttl_seconds,
            source=source,
        )
        snapshot["missing_doi_points"] = int(missing_count)
        return snapshot, None

    def _ensure_qdrant_snapshot(
        self,
        collection_name: str,
        force_refresh: bool = False,
    ) -> Tuple[Optional[Dict[str, Any]], bool, Optional[str]]:
        if not force_refresh:
            cached_snapshot = self.cache.get_presence_snapshot(collection_name, allow_stale=False)
            if cached_snapshot is not None:
                return cached_snapshot, False, None

        snapshot, err = self._refresh_qdrant_snapshot(
            collection_name=collection_name,
            reason="force" if force_refresh else "expired_or_missing",
        )
        if err:
            stale_snapshot = self.cache.get_presence_snapshot(collection_name, allow_stale=True)
            if stale_snapshot is not None:
                return stale_snapshot, False, err
            return None, False, err
        return snapshot, True, None

    def _http_json(
        self,
        url: str,
        *,
        timeout: int,
        params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        # Retry transient failures (esp. OpenAlex's intermittent 503 on /works
        # search) with a short exponential backoff before giving up.
        RETRYABLE = {429, 500, 502, 503, 504}
        attempts = 3
        for attempt in range(attempts):
            try:
                resp = requests.get(url, params=params, timeout=timeout, headers=self._headers())
            except requests.RequestException as exc:
                if attempt < attempts - 1:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                raise RuntimeError(f"Request error for {url}: {exc}") from exc
            if resp.status_code == 404:
                return None
            if resp.status_code in RETRYABLE and attempt < attempts - 1:
                time.sleep(0.5 * (2 ** attempt))  # 0.5s, then 1.0s
                continue
            try:
                resp.raise_for_status()
            except requests.HTTPError as exc:
                raise RuntimeError(f"HTTP error for {url}: {exc}") from exc
            payload = resp.json()
            return payload if isinstance(payload, dict) else None
        return None

    def _crossref_fetch_work_raw(self, doi: str) -> Optional[Dict[str, Any]]:
        url = f"https://api.crossref.org/works/{quote(doi)}"
        payload = self._http_json(url, timeout=self.config.crossref_timeout)
        if not payload:
            return None
        message = payload.get("message")
        if isinstance(message, dict):
            return message
        return None

    def _openalex_fetch_work_raw_by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        encoded = quote(f"https://doi.org/{doi}", safe="")
        url = f"https://api.openalex.org/works/{encoded}"
        params: Dict[str, Any] = {}
        if self.config.mailto.strip():
            params["mailto"] = self.config.mailto.strip()
        payload = self._http_json(url, timeout=self.config.openalex_timeout, params=params or None)
        if isinstance(payload, dict) and payload.get("id"):
            return payload
        return None

    def _openalex_fetch_work_raw_by_id(self, openalex_id: str) -> Optional[Dict[str, Any]]:
        oid = _normalize_openalex_id(openalex_id)
        if not oid:
            return None
        url = f"https://api.openalex.org/works/{oid}"
        params: Dict[str, Any] = {}
        if self.config.mailto.strip():
            params["mailto"] = self.config.mailto.strip()
        payload = self._http_json(url, timeout=self.config.openalex_timeout, params=params or None)
        if isinstance(payload, dict) and payload.get("id"):
            return payload
        return None

    def _crossref_search_raw(self, query: str, rows: int) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {
            "query.bibliographic": query,
            "rows": max(1, min(int(rows), 50)),
        }
        payload = self._http_json(
            "https://api.crossref.org/works",
            timeout=self.config.crossref_timeout,
            params=params,
        )
        if not payload:
            return []
        msg = payload.get("message")
        if not isinstance(msg, dict):
            return []
        items = msg.get("items")
        if not isinstance(items, list):
            return []
        return [it for it in items if isinstance(it, dict)]

    def _openalex_search_raw(self, query: str, rows: int, page: int = 1) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {
            "search": query,
            "per-page": max(1, min(int(rows), 50)),
            "page": max(1, int(page)),
        }
        if self.config.mailto.strip():
            params["mailto"] = self.config.mailto.strip()
        payload = self._http_json(
            "https://api.openalex.org/works",
            timeout=self.config.openalex_timeout,
            params=params,
        )
        if not payload:
            return []
        results = payload.get("results")
        if not isinstance(results, list):
            return []
        return [it for it in results if isinstance(it, dict)]

    def _parse_crossref_work(self, record: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not isinstance(record, dict):
            return None

        doi = _normalize_doi(record.get("DOI"))
        title = _first_str(record.get("title"))
        journal = _first_str(record.get("container-title"))
        publisher = _normalize_text(record.get("publisher"))
        citation_count = record.get("is-referenced-by-count")

        year = None
        for key in ("issued", "published-print", "published-online", "created"):
            node = record.get(key)
            if isinstance(node, dict):
                date_parts = node.get("date-parts")
                if isinstance(date_parts, list) and date_parts and isinstance(date_parts[0], list) and date_parts[0]:
                    year = _parse_year(date_parts[0][0])
                    if year:
                        break

        authors: List[str] = []
        author_list = record.get("author")
        if isinstance(author_list, list):
            for author in author_list:
                if not isinstance(author, dict):
                    continue
                family = _normalize_text(author.get("family"))
                given = _normalize_text(author.get("given"))
                literal = _normalize_text(author.get("name"))
                if family and given:
                    authors.append(f"{family}, {given}")
                elif family:
                    authors.append(family)
                elif literal:
                    authors.append(literal)

        return {
            "doi": doi,
            "openalex_id": None,
            "title": title,
            "authors": authors,
            "journal": journal,
            "year": year,
            "publisher": publisher,
            "type": _normalize_text(record.get("type")),
            "citation_count": int(citation_count) if isinstance(citation_count, int) else None,
            "abstract": _strip_html(record.get("abstract")),
            "ids": {
                "doi": doi,
                "openalex": None,
                "pmid": None,
                "pmcid": None,
            },
            "source": "crossref",
            "source_score": float(record.get("score") or 0.0),
        }

    def _parse_openalex_work(self, record: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not isinstance(record, dict):
            return None

        ids = record.get("ids") or {}
        ids = ids if isinstance(ids, dict) else {}

        doi = _normalize_doi(record.get("doi") or ids.get("doi"))
        openalex_id = _normalize_openalex_id(record.get("id") or ids.get("openalex"))

        authors: List[str] = []
        authorships = record.get("authorships")
        if isinstance(authorships, list):
            for entry in authorships:
                if not isinstance(entry, dict):
                    continue
                author = entry.get("author") or {}
                if not isinstance(author, dict):
                    continue
                display_name = _normalize_text(author.get("display_name"))
                if display_name:
                    authors.append(display_name)

        venue = ""
        primary_location = record.get("primary_location")
        if isinstance(primary_location, dict):
            source_node = primary_location.get("source")
            if isinstance(source_node, dict):
                venue = _normalize_text(source_node.get("display_name"))
        if not venue:
            host_venue = record.get("host_venue")
            if isinstance(host_venue, dict):
                venue = _normalize_text(host_venue.get("display_name"))

        pmid_value = ids.get("pmid")
        if isinstance(pmid_value, str) and "/" in pmid_value:
            pmid_value = pmid_value.rsplit("/", 1)[-1]

        pmcid_value = ids.get("pmcid")
        if isinstance(pmcid_value, str) and "/" in pmcid_value:
            pmcid_value = pmcid_value.rsplit("/", 1)[-1]

        abstract_text = _decode_openalex_abstract(record.get("abstract_inverted_index"))

        return {
            "doi": doi,
            "openalex_id": openalex_id,
            "title": _normalize_text(record.get("display_name")),
            "authors": authors,
            "journal": venue,
            "year": _parse_year(record.get("publication_year")),
            "publisher": "",
            "type": _normalize_text(record.get("type")),
            "citation_count": (
                int(record.get("cited_by_count")) if isinstance(record.get("cited_by_count"), int) else None
            ),
            "abstract": abstract_text,
            "ids": {
                "doi": doi,
                "openalex": openalex_id,
                "pmid": _normalize_text(pmid_value),
                "pmcid": _normalize_text(pmcid_value),
            },
            "source": "openalex",
            "source_score": float(record.get("relevance_score") or 0.0),
        }

    def _merge_works(
        self,
        crossref_work: Optional[Dict[str, Any]],
        openalex_work: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        if not crossref_work and not openalex_work:
            return None

        primary = crossref_work or {}
        secondary = openalex_work or {}

        doi = _normalize_doi(primary.get("doi") or secondary.get("doi"))
        openalex_id = _normalize_openalex_id(
            secondary.get("openalex_id") or primary.get("openalex_id")
        )

        def pick(field: str) -> Any:
            a = primary.get(field)
            b = secondary.get(field)
            if isinstance(a, str):
                return a if a.strip() else b
            if isinstance(a, list):
                return a if a else b
            if a is not None:
                return a
            return b

        merged = {
            "doi": doi,
            "openalex_id": openalex_id,
            "title": pick("title") or "",
            "authors": pick("authors") or [],
            "journal": pick("journal") or "",
            "year": pick("year"),
            "publisher": pick("publisher") or "",
            "type": pick("type") or "",
            "citation_count": (
                openalex_work.get("citation_count")
                if openalex_work and openalex_work.get("citation_count") is not None
                else pick("citation_count")
            ),
            "abstract": (
                (openalex_work.get("abstract") if openalex_work else "")
                or (crossref_work.get("abstract") if crossref_work else "")
                or ""
            ),
            "ids": {
                "doi": doi,
                "openalex": openalex_id,
                "pmid": (openalex_work or {}).get("ids", {}).get("pmid"),
                "pmcid": (openalex_work or {}).get("ids", {}).get("pmcid"),
            },
            "source_coverage": {
                "crossref": bool(crossref_work),
                "openalex": bool(openalex_work),
            },
            "retrieved_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        return merged

    def _freshness_flags(self, freshness: str) -> Tuple[bool, bool]:
        mode = str(freshness or "prefer_cache").strip().lower()
        if mode not in {"prefer_cache", "stale_ok", "force_refresh"}:
            raise ValueError("freshness must be one of: prefer_cache, stale_ok, force_refresh")
        read_cache = mode != "force_refresh"
        allow_stale = mode == "stale_ok"
        return read_cache, allow_stale

    def get_work(
        self,
        doi: Optional[str] = None,
        openalex_id: Optional[str] = None,
        freshness: str = "prefer_cache",
    ) -> Dict[str, Any]:
        doi_norm = _normalize_doi(doi)
        openalex_norm = _normalize_openalex_id(openalex_id)

        if not doi_norm and not openalex_norm:
            return _error("INVALID_ARGUMENT", "Provide doi or openalex_id.")
        if doi_norm and openalex_norm:
            return _error("INVALID_ARGUMENT", "Provide only one of doi or openalex_id, not both.")

        cache_key = f"doi:{doi_norm}" if doi_norm else f"openalex:{openalex_norm}"
        try:
            read_cache, allow_stale = self._freshness_flags(freshness)
        except ValueError as exc:
            return _error("INVALID_ARGUMENT", str(exc))

        if read_cache:
            entry = self.cache.read("work", cache_key, allow_stale=allow_stale)
            if entry is not None:
                payload = dict(entry.get("payload") or {})
                payload["cache"] = _cache_meta(True, entry)
                return payload

        crossref_raw: Optional[Dict[str, Any]] = None
        openalex_raw: Optional[Dict[str, Any]] = None
        warnings: List[str] = []

        if doi_norm:
            try:
                crossref_raw = self._crossref_fetch_work_raw(doi_norm)
            except Exception as exc:
                warnings.append(f"Crossref lookup failed: {exc}")
            try:
                openalex_raw = self._openalex_fetch_work_raw_by_doi(doi_norm)
            except Exception as exc:
                warnings.append(f"OpenAlex lookup failed: {exc}")

        if openalex_norm:
            try:
                openalex_raw = self._openalex_fetch_work_raw_by_id(openalex_norm)
            except Exception as exc:
                warnings.append(f"OpenAlex lookup failed: {exc}")
            parsed_openalex = self._parse_openalex_work(openalex_raw)
            parsed_doi = _normalize_doi((parsed_openalex or {}).get("doi"))
            if parsed_doi:
                doi_norm = parsed_doi
                try:
                    crossref_raw = self._crossref_fetch_work_raw(parsed_doi)
                except Exception as exc:
                    warnings.append(f"Crossref lookup for derived DOI failed: {exc}")

        crossref_work = self._parse_crossref_work(crossref_raw)
        openalex_work = self._parse_openalex_work(openalex_raw)
        merged = self._merge_works(crossref_work, openalex_work)

        found = bool(merged)
        payload: Dict[str, Any] = {
            "query": {
                "doi": doi_norm,
                "openalex_id": openalex_norm,
            },
            "found": found,
            "work": merged,
            "warnings": warnings,
        }
        cache_payload = dict(payload)

        ttl = self.config.work_ttl_seconds if found else self.config.negative_ttl_seconds
        written = self.cache.write(
            "work",
            cache_key,
            cache_payload,
            ttl_seconds=ttl,
            source="crossref_openalex",
            status_code=200 if found else 404,
        )
        payload["cache"] = _cache_meta(False, written)

        # Add alias cache entry if both identifiers are now known.
        if merged and merged.get("doi") and merged.get("openalex_id"):
            alias_key = f"openalex:{merged['openalex_id']}" if doi_norm else f"doi:{merged['doi']}"
            if alias_key != cache_key:
                self.cache.write(
                    "work",
                    alias_key,
                    cache_payload,
                    ttl_seconds=ttl,
                    source="crossref_openalex",
                    status_code=200,
                )

        return payload

    def search_works(
        self,
        query: str,
        max_results: int = 20,
        source: str = "both",
        year_from: Optional[int] = None,
        year_to: Optional[int] = None,
        freshness: str = "prefer_cache",
        check_qdrant: bool = False,
        qdrant_collection: Optional[str] = None,
        qdrant_freshness: str = "prefer_cache",
        exclude_present_in_qdrant: bool = False,
    ) -> Dict[str, Any]:
        query_norm = _normalize_text(query)
        if not query_norm:
            return _error("INVALID_ARGUMENT", "query must not be empty")

        if source not in {"both", "crossref", "openalex"}:
            return _error("INVALID_ARGUMENT", "source must be one of: both, crossref, openalex")

        yr_from = _parse_year(year_from)
        yr_to = _parse_year(year_to)
        if yr_from and yr_to and yr_from > yr_to:
            return _error("INVALID_ARGUMENT", "year_from must be <= year_to")

        key_obj = {
            "query": query_norm,
            "max_results": int(max_results),
            "source": source,
            "year_from": yr_from,
            "year_to": yr_to,
            "check_qdrant": bool(check_qdrant),
            "qdrant_collection": _normalize_text(qdrant_collection or ""),
            "qdrant_freshness": _normalize_text(qdrant_freshness or ""),
            "exclude_present_in_qdrant": bool(exclude_present_in_qdrant),
            "schema_version": 1,
        }
        cache_key = SQLiteTTLCache.key_from_object("search", key_obj)

        try:
            read_cache, allow_stale = self._freshness_flags(freshness)
        except ValueError as exc:
            return _error("INVALID_ARGUMENT", str(exc))

        if read_cache:
            entry = self.cache.read("query", cache_key, allow_stale=allow_stale)
            if entry is not None:
                payload = dict(entry.get("payload") or {})
                payload["cache"] = _cache_meta(True, entry)
                return payload

        rows_to_fetch = max(5, min(max_results * 3, 50))
        warnings: List[str] = []

        crossref_items: List[Dict[str, Any]] = []
        openalex_items: List[Dict[str, Any]] = []

        if source in {"both", "crossref"}:
            try:
                crossref_raw = self._crossref_search_raw(query_norm, rows=rows_to_fetch)
                crossref_items = [w for w in (self._parse_crossref_work(r) for r in crossref_raw) if w]
            except Exception as exc:
                warnings.append(f"Crossref search failed: {exc}")

        if source in {"both", "openalex"}:
            try:
                openalex_raw = self._openalex_search_raw(query_norm, rows=rows_to_fetch)
                openalex_items = [w for w in (self._parse_openalex_work(r) for r in openalex_raw) if w]
            except Exception as exc:
                warnings.append(f"OpenAlex search failed: {exc}")

        merged_map: Dict[str, Dict[str, Any]] = {}

        for item in crossref_items:
            key = item.get("doi") or SQLiteTTLCache.key_from_object(
                "crossref_fallback", {"title": item.get("title"), "year": item.get("year")}
            )
            node = merged_map.setdefault(key, {"crossref": None, "openalex": None, "score": 0.0})
            node["crossref"] = item
            node["score"] = max(float(node["score"]), float(item.get("source_score") or 0.0) / 200.0)

        for item in openalex_items:
            key = item.get("doi") or item.get("openalex_id") or SQLiteTTLCache.key_from_object(
                "openalex_fallback", {"title": item.get("title"), "year": item.get("year")}
            )
            node = merged_map.setdefault(key, {"crossref": None, "openalex": None, "score": 0.0})
            node["openalex"] = item
            node["score"] = max(float(node["score"]), float(item.get("source_score") or 0.0))

        results: List[Dict[str, Any]] = []
        for node in merged_map.values():
            merged = self._merge_works(node.get("crossref"), node.get("openalex"))
            if not merged:
                continue
            yr = _parse_year(merged.get("year"))
            if yr_from and (yr is None or yr < yr_from):
                continue
            if yr_to and (yr is None or yr > yr_to):
                continue
            results.append(
                {
                    "work": merged,
                    "score": round(float(node.get("score") or 0.0), 4),
                    "sources": {
                        "crossref": bool(node.get("crossref")),
                        "openalex": bool(node.get("openalex")),
                    },
                }
            )

        results.sort(
            key=lambda r: (
                float(r.get("score") or 0.0),
                int((r.get("work") or {}).get("citation_count") or 0),
            ),
            reverse=True,
        )
        results = results[: max(1, int(max_results))]

        qdrant_presence_payload: Optional[Dict[str, Any]] = None
        if check_qdrant:
            query_dois = [
                str((entry.get("work") or {}).get("doi"))
                for entry in results
                if (entry.get("work") or {}).get("doi")
            ]
            if query_dois:
                qdrant_presence_payload = self.qdrant_has_dois(
                    dois=query_dois,
                    collection=qdrant_collection,
                    freshness=qdrant_freshness,
                    refresh_snapshot=False,
                )
                if "error" in qdrant_presence_payload:
                    warnings.append(
                        f"Qdrant presence check failed: "
                        f"{(qdrant_presence_payload.get('error') or {}).get('message')}"
                    )
                else:
                    presence_lookup = {
                        str(item.get("doi")): item
                        for item in qdrant_presence_payload.get("results", [])
                        if item.get("doi")
                    }
                    filtered_results: List[Dict[str, Any]] = []
                    for entry in results:
                        doi = (entry.get("work") or {}).get("doi")
                        info = presence_lookup.get(str(doi)) if doi else None
                        if info is not None:
                            entry["qdrant"] = {
                                "present": info.get("present"),
                                "status": info.get("status"),
                                "checked_at": info.get("checked_at"),
                                "expires_at": info.get("expires_at"),
                                "stale": info.get("stale"),
                                "collection": (qdrant_presence_payload.get("collection") or {}).get("resolved"),
                            }
                        else:
                            entry["qdrant"] = {
                                "present": None,
                                "status": "unknown",
                                "checked_at": None,
                                "expires_at": None,
                                "stale": None,
                                "collection": (qdrant_presence_payload.get("collection") or {}).get("resolved"),
                            }
                        if exclude_present_in_qdrant and entry["qdrant"].get("present") is True:
                            continue
                        filtered_results.append(entry)
                    results = filtered_results

        payload = {
            "query": query_norm,
            "source": source,
            "year_from": yr_from,
            "year_to": yr_to,
            "result_count": len(results),
            "results": results,
            "warnings": warnings,
            "qdrant_presence": qdrant_presence_payload,
        }

        written = self.cache.write(
            "query",
            cache_key,
            payload,
            ttl_seconds=self.config.query_ttl_seconds,
            source="crossref_openalex",
            status_code=200,
        )
        payload["cache"] = _cache_meta(False, written)
        return payload

    def _score_resolution_candidate(
        self,
        work: Dict[str, Any],
        title: Optional[str],
        first_author: Optional[str],
        journal: Optional[str],
        year: Optional[int],
    ) -> float:
        score = 0.0

        if title:
            score += 0.70 * _similarity(title, work.get("title"))
        else:
            score += 0.15

        if year is not None:
            cand_year = _parse_year(work.get("year"))
            if cand_year is not None:
                if cand_year == year:
                    score += 0.15
                elif abs(cand_year - year) == 1:
                    score += 0.08

        if first_author:
            target_family = _normalize_author_family(first_author)
            if target_family:
                cand_authors = work.get("authors") or []
                joined = " ".join(str(a) for a in cand_authors).lower()
                if target_family and target_family in joined:
                    score += 0.10

        if journal:
            score += 0.07 * _similarity(journal, work.get("journal"))

        if work.get("doi"):
            score += 0.03

        return min(1.0, max(0.0, score))

    def resolve_identifiers(
        self,
        doi: Optional[str] = None,
        title: Optional[str] = None,
        first_author: Optional[str] = None,
        journal: Optional[str] = None,
        year: Optional[int] = None,
        pmid: Optional[str] = None,
        pmcid: Optional[str] = None,
        arxiv: Optional[str] = None,
        pii: Optional[str] = None,
        min_score: float = 0.72,
        max_candidates: int = 20,
        freshness: str = "prefer_cache",
    ) -> Dict[str, Any]:
        doi_norm = _normalize_doi(doi)
        title_norm = _normalize_text(title)
        first_author_norm = _normalize_text(first_author)
        journal_norm = _normalize_text(journal)
        year_norm = _parse_year(year)

        min_score = float(min_score)
        if not (0.0 <= min_score <= 1.0):
            return _error("INVALID_ARGUMENT", "min_score must be between 0 and 1")

        key_obj = {
            "doi": doi_norm,
            "title": title_norm,
            "first_author": first_author_norm,
            "journal": journal_norm,
            "year": year_norm,
            "pmid": _normalize_text(pmid),
            "pmcid": _normalize_text(pmcid),
            "arxiv": _normalize_text(arxiv),
            "pii": _normalize_text(pii),
            "min_score": min_score,
            "max_candidates": int(max_candidates),
            "schema_version": 1,
        }
        cache_key = SQLiteTTLCache.key_from_object("resolve", key_obj)

        try:
            read_cache, allow_stale = self._freshness_flags(freshness)
        except ValueError as exc:
            return _error("INVALID_ARGUMENT", str(exc))

        if read_cache:
            entry = self.cache.read("resolution", cache_key, allow_stale=allow_stale)
            if entry is not None:
                payload = dict(entry.get("payload") or {})
                payload["cache"] = _cache_meta(True, entry)
                return payload

        # Fast path: explicit DOI
        if doi_norm:
            work_payload = self.get_work(doi=doi_norm, freshness=freshness)
            if "error" in work_payload:
                return work_payload
            resolved = bool(work_payload.get("found") and (work_payload.get("work") or {}).get("doi"))
            result = {
                "resolved": resolved,
                "doi": ((work_payload.get("work") or {}).get("doi") if resolved else None),
                "openalex_id": ((work_payload.get("work") or {}).get("openalex_id") if resolved else None),
                "confidence": 1.0 if resolved else 0.0,
                "method": "explicit_doi",
                "note": "resolved from explicit DOI input" if resolved else "DOI not found in Crossref/OpenAlex",
                "work": work_payload.get("work"),
                "top_candidates": [],
            }
            written = self.cache.write(
                "resolution",
                cache_key,
                result,
                ttl_seconds=self.config.resolution_ttl_seconds if resolved else self.config.negative_ttl_seconds,
                source="crossref_openalex",
                status_code=200 if resolved else 404,
            )
            result["cache"] = _cache_meta(False, written)
            return result

        query_parts = [
            title_norm,
            first_author_norm,
            journal_norm,
            str(year_norm) if year_norm else "",
            _normalize_text(pmid),
            _normalize_text(pmcid),
            _normalize_text(arxiv),
            _normalize_text(pii),
        ]
        query_text = _normalize_text(" ".join(p for p in query_parts if p))
        if not query_text:
            return _error(
                "INVALID_ARGUMENT",
                "Insufficient identifiers. Provide DOI or at least title/author/journal/year tokens.",
            )

        search_payload = self.search_works(
            query=query_text,
            max_results=max(5, min(int(max_candidates), 50)),
            source="both",
            year_from=(year_norm - 1) if year_norm else None,
            year_to=(year_norm + 1) if year_norm else None,
            freshness=freshness,
        )
        if "error" in search_payload:
            return search_payload

        scored: List[Dict[str, Any]] = []
        for hit in search_payload.get("results", []):
            work = hit.get("work") or {}
            conf = self._score_resolution_candidate(
                work=work,
                title=title_norm or None,
                first_author=first_author_norm or None,
                journal=journal_norm or None,
                year=year_norm,
            )
            scored.append(
                {
                    "doi": work.get("doi"),
                    "openalex_id": work.get("openalex_id"),
                    "title": work.get("title"),
                    "journal": work.get("journal"),
                    "year": work.get("year"),
                    "confidence": round(conf, 4),
                }
            )

        scored.sort(key=lambda item: float(item.get("confidence") or 0.0), reverse=True)
        best = scored[0] if scored else None

        resolved = bool(
            best
            and best.get("doi")
            and float(best.get("confidence") or 0.0) >= min_score
        )

        result = {
            "resolved": resolved,
            "doi": best.get("doi") if resolved and best else None,
            "openalex_id": best.get("openalex_id") if resolved and best else None,
            "confidence": float(best.get("confidence") or 0.0) if best else 0.0,
            "method": "bibliographic_search",
            "note": (
                "best candidate above threshold"
                if resolved
                else f"no candidate reached min_score={min_score}"
            ),
            "work": None,
            "top_candidates": scored[: min(10, len(scored))],
            "search_query": query_text,
            "candidate_count": len(scored),
        }

        if resolved and result.get("doi"):
            work_payload = self.get_work(doi=str(result["doi"]), freshness=freshness)
            if "error" not in work_payload:
                result["work"] = work_payload.get("work")
                result["openalex_id"] = (
                    (work_payload.get("work") or {}).get("openalex_id")
                    or result.get("openalex_id")
                )

        written = self.cache.write(
            "resolution",
            cache_key,
            result,
            ttl_seconds=self.config.resolution_ttl_seconds if resolved else self.config.negative_ttl_seconds,
            source="crossref_openalex",
            status_code=200 if resolved else 404,
        )
        result["cache"] = _cache_meta(False, written)
        return result

    def qdrant_has_dois(
        self,
        dois: List[str],
        collection: Optional[str] = None,
        freshness: str = "prefer_cache",
        refresh_snapshot: bool = False,
    ) -> Dict[str, Any]:
        if not isinstance(dois, list) or not dois:
            return _error("INVALID_ARGUMENT", "dois must be a non-empty list of DOI strings.")

        collection_name, err = self._resolve_qdrant_collection(collection)
        if err:
            return _error("QDRANT_UNAVAILABLE", err)
        if not collection_name:
            return _error("QDRANT_UNAVAILABLE", "Could not resolve Qdrant collection.")

        try:
            read_cache, allow_stale = self._freshness_flags(freshness)
        except ValueError as exc:
            return _error("INVALID_ARGUMENT", str(exc))

        force_refresh = bool(refresh_snapshot or freshness == "force_refresh")
        read_cache = read_cache and not force_refresh

        input_rows: List[Dict[str, Any]] = []
        normalized_order: List[str] = []
        seen: set[str] = set()
        invalid_inputs = 0
        for raw in dois:
            doi_norm = _normalize_doi(raw)
            input_rows.append(
                {
                    "input": raw,
                    "doi": doi_norm,
                    "present": None if doi_norm is None else False,
                    "status": "invalid_doi" if doi_norm is None else "pending",
                    "checked_at": None,
                    "expires_at": None,
                    "stale": None,
                    "source": "",
                }
            )
            if doi_norm is None:
                invalid_inputs += 1
                continue
            if doi_norm not in seen:
                seen.add(doi_norm)
                normalized_order.append(doi_norm)

        if not normalized_order:
            return _error("INVALID_ARGUMENT", "No valid DOI values were provided.")

        presence_map: Dict[str, Dict[str, Any]] = {}
        if read_cache:
            presence_map = self.cache.read_presence_batch(
                collection_name=collection_name,
                dois=normalized_order,
                allow_stale=allow_stale,
            )

        unresolved = [doi for doi in normalized_order if doi not in presence_map]

        snapshot_used = False
        snapshot_refreshed = False
        warnings: List[str] = []
        snapshot_meta: Optional[Dict[str, Any]] = None

        if unresolved or force_refresh:
            snapshot_used = True
            snapshot_meta, snapshot_refreshed, snapshot_err = self._ensure_qdrant_snapshot(
                collection_name=collection_name,
                force_refresh=force_refresh,
            )
            if snapshot_err:
                warnings.append(snapshot_err)

            # Re-check presence after snapshot refresh (or stale snapshot fallback).
            post_snapshot_presence = self.cache.read_presence_batch(
                collection_name=collection_name,
                dois=normalized_order,
                allow_stale=True,
            )
            presence_map.update(post_snapshot_presence)

            remaining = [doi for doi in normalized_order if doi not in presence_map]
            snapshot_is_fresh = bool(snapshot_meta and not snapshot_meta.get("stale"))
            if remaining and snapshot_is_fresh:
                absent_write = self.cache.write_presence_batch(
                    collection_name=collection_name,
                    doi_to_present={doi: False for doi in remaining},
                    ttl_seconds=self.config.qdrant_presence_absent_ttl_seconds,
                    source="qdrant_snapshot_absent",
                )
                _ = absent_write
                post_absent = self.cache.read_presence_batch(
                    collection_name=collection_name,
                    dois=remaining,
                    allow_stale=True,
                )
                presence_map.update(post_absent)

        for row in input_rows:
            doi_norm = row.get("doi")
            if not doi_norm:
                continue
            info = presence_map.get(str(doi_norm))
            if not info:
                row["present"] = None
                row["status"] = "unknown"
                continue
            row["present"] = bool(info.get("present"))
            row["status"] = "present" if bool(info.get("present")) else "absent"
            row["checked_at"] = info.get("checked_at")
            row["expires_at"] = info.get("expires_at")
            row["stale"] = bool(info.get("stale"))
            row["source"] = info.get("source") or ""

        present_count = sum(1 for row in input_rows if row.get("status") == "present")
        absent_count = sum(1 for row in input_rows if row.get("status") == "absent")
        unknown_count = sum(1 for row in input_rows if row.get("status") == "unknown")

        return {
            "collection": {
                "requested": collection or self.config.qdrant_default_collection,
                "resolved": collection_name,
            },
            "freshness": freshness,
            "counts": {
                "requested": len(dois),
                "valid_unique": len(normalized_order),
                "invalid_inputs": invalid_inputs,
                "present": present_count,
                "absent": absent_count,
                "unknown": unknown_count,
            },
            "snapshot": {
                "used": snapshot_used,
                "refreshed": snapshot_refreshed,
                "meta": snapshot_meta,
            },
            "results": input_rows,
            "warnings": warnings,
        }

    def cache_stats(self) -> Dict[str, Any]:
        return self.cache.stats()

    def cache_gc(self, kind: Optional[str] = None) -> Dict[str, Any]:
        if kind == "qdrant_presence":
            # Presence GC is always included in cache.gc(); expose this as an alias.
            kind = None
        if kind and kind not in {"work", "query", "resolution"}:
            return _error(
                "INVALID_ARGUMENT",
                "kind must be one of: work, query, resolution, qdrant_presence",
            )
        return self.cache.gc(kind=kind)

    def cache_invalidate(self, kind: Optional[str] = None, prefix: str = "") -> Dict[str, Any]:
        if kind == "qdrant_presence":
            return self.cache.invalidate_presence(collection_name=prefix or None)
        if kind and kind not in {"work", "query", "resolution"}:
            return _error(
                "INVALID_ARGUMENT",
                "kind must be one of: work, query, resolution, qdrant_presence",
            )
        return self.cache.invalidate(kind=kind, prefix=prefix)

    def server_info(self) -> Dict[str, Any]:
        qdrant_runtime, qdrant_error = self._get_qdrant_runtime()
        return {
            "server": "crossref-openalex-mcp",
            "version": "0.1.0",
            "cache_db": self.config.cache_db,
            "cache_stats": self.cache.stats(),
            "defaults": {
                "crossref_timeout": self.config.crossref_timeout,
                "openalex_timeout": self.config.openalex_timeout,
                "work_ttl_seconds": self.config.work_ttl_seconds,
                "query_ttl_seconds": self.config.query_ttl_seconds,
                "resolution_ttl_seconds": self.config.resolution_ttl_seconds,
                "negative_ttl_seconds": self.config.negative_ttl_seconds,
                "qdrant_presence_ttl_seconds": self.config.qdrant_presence_ttl_seconds,
                "qdrant_presence_absent_ttl_seconds": self.config.qdrant_presence_absent_ttl_seconds,
                "qdrant_snapshot_ttl_seconds": self.config.qdrant_snapshot_ttl_seconds,
                "qdrant_snapshot_batch_size": self.config.qdrant_snapshot_batch_size,
            },
            "qdrant": {
                "enabled": qdrant_runtime is not None and not qdrant_error,
                "error": qdrant_error or "",
                "config_path": self.config.qdrant_config_path,
                "profile": self.config.qdrant_profile,
                "default_collection": self.config.qdrant_default_collection,
                "runtime": qdrant_runtime or {},
            },
        }


def build_server(config: ServerConfig) -> Any:
    if FastMCP is None:
        raise RuntimeError(
            "The 'mcp' package is required for this server. Install with: python -m pip install mcp"
        )

    backend = CrossrefOpenAlexBackend(config)
    mcp = FastMCP("crossref-openalex")

    @mcp.tool()
    def crossref_openalex_server_info() -> Dict[str, Any]:
        return backend.server_info()

    @mcp.tool()
    def crossref_openalex_get_work(
        doi: Optional[str] = None,
        openalex_id: Optional[str] = None,
        freshness: str = "prefer_cache",
    ) -> Dict[str, Any]:
        return backend.get_work(doi=doi, openalex_id=openalex_id, freshness=freshness)

    @mcp.tool()
    def crossref_openalex_search_works(
        query: str,
        max_results: int = 20,
        source: str = "both",
        year_from: Optional[int] = None,
        year_to: Optional[int] = None,
        freshness: str = "prefer_cache",
        check_qdrant: bool = False,
        qdrant_collection: Optional[str] = None,
        qdrant_freshness: str = "prefer_cache",
        exclude_present_in_qdrant: bool = False,
    ) -> Dict[str, Any]:
        return backend.search_works(
            query=query,
            max_results=max_results,
            source=source,
            year_from=year_from,
            year_to=year_to,
            freshness=freshness,
            check_qdrant=check_qdrant,
            qdrant_collection=qdrant_collection,
            qdrant_freshness=qdrant_freshness,
            exclude_present_in_qdrant=exclude_present_in_qdrant,
        )

    @mcp.tool()
    def crossref_openalex_qdrant_has_dois(
        dois: List[str],
        collection: Optional[str] = None,
        freshness: str = "prefer_cache",
        refresh_snapshot: bool = False,
    ) -> Dict[str, Any]:
        return backend.qdrant_has_dois(
            dois=dois,
            collection=collection,
            freshness=freshness,
            refresh_snapshot=refresh_snapshot,
        )

    @mcp.tool()
    def crossref_openalex_resolve_identifiers(
        doi: Optional[str] = None,
        title: Optional[str] = None,
        first_author: Optional[str] = None,
        journal: Optional[str] = None,
        year: Optional[int] = None,
        pmid: Optional[str] = None,
        pmcid: Optional[str] = None,
        arxiv: Optional[str] = None,
        pii: Optional[str] = None,
        min_score: float = 0.72,
        max_candidates: int = 20,
        freshness: str = "prefer_cache",
    ) -> Dict[str, Any]:
        return backend.resolve_identifiers(
            doi=doi,
            title=title,
            first_author=first_author,
            journal=journal,
            year=year,
            pmid=pmid,
            pmcid=pmcid,
            arxiv=arxiv,
            pii=pii,
            min_score=min_score,
            max_candidates=max_candidates,
            freshness=freshness,
        )

    @mcp.tool()
    def crossref_openalex_cache_stats() -> Dict[str, Any]:
        return backend.cache_stats()

    @mcp.tool()
    def crossref_openalex_cache_gc(kind: Optional[str] = None) -> Dict[str, Any]:
        return backend.cache_gc(kind=kind)

    @mcp.tool()
    def crossref_openalex_cache_invalidate(kind: Optional[str] = None, prefix: str = "") -> Dict[str, Any]:
        return backend.cache_invalidate(kind=kind, prefix=prefix)

    @mcp.resource("crossref-openalex://help")
    def crossref_openalex_help() -> Dict[str, Any]:
        return {
            "server": "crossref-openalex",
            "tools": [
                "crossref_openalex_server_info",
                "crossref_openalex_get_work",
                "crossref_openalex_search_works",
                "crossref_openalex_resolve_identifiers",
                "crossref_openalex_qdrant_has_dois",
                "crossref_openalex_cache_stats",
                "crossref_openalex_cache_gc",
                "crossref_openalex_cache_invalidate",
            ],
            "examples": {
                "get_work": {"doi": "10.1016/j.actamat.2014.04.061"},
                "search": {
                    "query": "hydrogen embrittlement alloy 718",
                    "check_qdrant": True,
                    "exclude_present_in_qdrant": True,
                },
                "resolve": {
                    "title": "Effect of electrochemical charging on the hydrogen embrittlement susceptibility of alloy 718",
                    "first_author": "Lu",
                    "year": 2019,
                },
                "qdrant_has_dois": {
                    "dois": ["10.1016/j.actamat.2014.04.061", "10.1016/j.corsci.2018.03.040"],
                    "collection": "text",
                },
            },
        }

    return mcp


def _build_config_from_args(ns: argparse.Namespace) -> ServerConfig:
    return ServerConfig(
        cache_db=str(Path(ns.cache_db).expanduser().resolve()),
        user_agent=str(ns.user_agent or "crossref-openalex-mcp/0.1").strip(),
        mailto=str(ns.mailto or "").strip(),
        crossref_timeout=max(1, int(ns.crossref_timeout)),
        openalex_timeout=max(1, int(ns.openalex_timeout)),
        work_ttl_seconds=max(60, int(float(ns.work_ttl_days) * 86400)),
        query_ttl_seconds=max(60, int(float(ns.query_ttl_hours) * 3600)),
        resolution_ttl_seconds=max(60, int(float(ns.resolution_ttl_days) * 86400)),
        negative_ttl_seconds=max(60, int(float(ns.negative_ttl_hours) * 3600)),
        qdrant_config_path=str(Path(ns.qdrant_config_path).expanduser().resolve()),
        qdrant_profile=str(ns.qdrant_profile).strip() if ns.qdrant_profile else None,
        qdrant_default_collection=str(ns.qdrant_default_collection or "text").strip() or "text",
        qdrant_presence_ttl_seconds=max(60, int(float(ns.qdrant_presence_ttl_hours) * 3600)),
        qdrant_presence_absent_ttl_seconds=max(60, int(float(ns.qdrant_absent_ttl_hours) * 3600)),
        qdrant_snapshot_ttl_seconds=max(60, int(float(ns.qdrant_snapshot_ttl_hours) * 3600)),
        qdrant_snapshot_batch_size=max(50, int(ns.qdrant_snapshot_batch_size)),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MCP server for Crossref/OpenAlex with local SQLite cache.")
    parser.add_argument("--config-yaml", default="", help="Optional YAML file with parser argument defaults.")
    parser.add_argument(
        "--cache-db",
        default=str(DEFAULT_CACHE_DB),
        help="SQLite cache path (default: data/cache/crossref_openalex/cache.sqlite).",
    )
    parser.add_argument(
        "--user-agent",
        default="crossref-openalex-mcp/0.1",
        help="User-Agent sent to Crossref/OpenAlex.",
    )
    parser.add_argument(
        "--mailto",
        default=os.getenv("CROSSREF_MAILTO", ""),
        help="Contact email for polite API usage (also sent to OpenAlex as mailto query param).",
    )
    parser.add_argument("--crossref-timeout", type=int, default=15, help="Crossref HTTP timeout in seconds.")
    parser.add_argument("--openalex-timeout", type=int, default=15, help="OpenAlex HTTP timeout in seconds.")
    parser.add_argument("--work-ttl-days", type=float, default=30.0, help="TTL for cached work lookups.")
    parser.add_argument("--query-ttl-hours", type=float, default=24.0, help="TTL for cached search queries.")
    parser.add_argument(
        "--resolution-ttl-days",
        type=float,
        default=14.0,
        help="TTL for cached resolution results.",
    )
    parser.add_argument(
        "--negative-ttl-hours",
        type=float,
        default=12.0,
        help="TTL for not-found/unresolved responses.",
    )
    parser.add_argument(
        "--qdrant-config-path",
        default="",
        help="Path to rag_qdrant YAML config used for DOI presence checks.",
    )
    parser.add_argument(
        "--qdrant-profile",
        default="",
        help="Profile name inside rag_qdrant config. If empty, uses active_profile.",
    )
    parser.add_argument(
        "--qdrant-default-collection",
        default="text",
        help="Default collection alias/name for DOI presence checks (text | figures | explicit name).",
    )
    parser.add_argument(
        "--qdrant-presence-ttl-hours",
        type=float,
        default=24.0,
        help="TTL for cached positive DOI presence entries.",
    )
    parser.add_argument(
        "--qdrant-absent-ttl-hours",
        type=float,
        default=6.0,
        help="TTL for cached absent DOI presence entries.",
    )
    parser.add_argument(
        "--qdrant-snapshot-ttl-hours",
        type=float,
        default=24.0,
        help="TTL for full DOI snapshot metadata from Qdrant.",
    )
    parser.add_argument(
        "--qdrant-snapshot-batch-size",
        type=int,
        default=1000,
        help="Batch size for scrolling DOI snapshots from Qdrant.",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="MCP transport. Use stdio for agent clients and sse for local debugging.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host for SSE transport.")
    parser.add_argument("--port", type=int, default=8010, help="Port for SSE transport.")
    return parser


def _apply_yaml_defaults(parser: argparse.ArgumentParser, argv: Optional[List[str]]) -> None:
    known_args, _ = parser.parse_known_args(argv)
    config_yaml = str(getattr(known_args, "config_yaml", "") or "").strip()
    if not config_yaml:
        return
    if yaml is None:
        parser.error("PyYAML is required to use --config-yaml.")
    config_path = Path(config_yaml).expanduser()
    if not config_path.exists():
        parser.error(f"Config YAML not found: {config_path}")
    with config_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        parser.error("Configuration YAML must be a mapping of argument names to values.")
    parser.set_defaults(**data)


def main(argv: Optional[List[str]] = None) -> None:
    parser = build_parser()
    _apply_yaml_defaults(parser, argv)
    ns = parser.parse_args(argv)

    config = _build_config_from_args(ns)
    server = build_server(config)
    if ns.transport == "stdio":
        # FastMCP defaults differ across versions; force stdio when requested.
        try:
            server.run(transport="stdio")
        except TypeError:
            server.run()
        return
    try:
        server.run(transport="sse", host=ns.host, port=ns.port)
    except TypeError:
        server.run(transport="sse")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log_path = SCRIPT_DIR / "startup_error.log"
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write("\n=== startup exception ===\n")
            traceback.print_exc(file=handle)
        raise
