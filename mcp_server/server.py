#!/usr/bin/env python3
"""MCP server for Qdrant v2 vector store (paperRAG-v2).

Exposes 23 tools for interactive research sessions across 4 collections:
  - materials_v2           (text chunks)
  - materials_v2_figures   (figure images + captions)
  - materials_v2_tables    (table CSV data)
  - materials_v2_summaries (paper-level summaries)

Capabilities: semantic search, keyword pre-filtering, citation graph queries,
cross-collection linking, image similarity search, structured table retrieval.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# A keyword/metadata filter: a JSON array of conditions, accepted either as a
# native list (what most models pass) or a JSON-encoded string (back-compat).
WhereFilter = Optional[Union[str, List[Dict[str, Any]]]]

# Optional .env loading — convenient when running the server directly (not in
# Docker). Harmless if python-dotenv is missing: Docker/compose inject the same
# variables through the real environment.
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("ERROR: mcp package required. Install with: pip install mcp", file=sys.stderr)
    sys.exit(1)

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

# ── Configuration (every value overridable via environment / .env) ────────
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "").strip()
EMBED_MODEL = os.getenv("EMBED_MODEL", "gemini-embedding-2-preview")
COLLECTION_PREFIX = os.getenv("COLLECTION_PREFIX", "materials_v2")
MCP_HOST = os.getenv("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.getenv("MCP_PORT", "8080"))
MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "streamable-http")
AUDIT_LOG = os.getenv("AUDIT_LOG", "").strip()


def _parse_tokens(raw: str) -> Dict[str, str]:
    """Parse MCP_AUTH_TOKENS ("token=username,token2=username2") into a map.

    Empty input → empty map → token authentication is DISABLED (the default).
    A bare token with no "=username" maps to a generated label.
    """
    out: Dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            tok, name = part.split("=", 1)
        else:
            tok, name = part, ""
        tok = tok.strip()
        if tok:
            out[tok] = name.strip() or ("user-" + tok[:6])
    return out


# Token auth is opt-in: set MCP_AUTH_TOKENS to enable it. While empty the server
# trusts the network and the self-declared X-User header (see docs/10).
AUTH_TOKENS = _parse_tokens(os.getenv("MCP_AUTH_TOKENS", ""))
AUTH_ENABLED = bool(AUTH_TOKENS)


def _coll(suffix: str = "") -> str:
    """Resolve a logical collection to its real name under the active prefix.

    Switching the whole server to a different vector database is a one-line
    environment change, e.g. COLLECTION_PREFIX=materials_v2_external_2026_05_28.
    """
    return COLLECTION_PREFIX if not suffix else f"{COLLECTION_PREFIX}_{suffix}"


COLL_TEXT = _coll()
COLL_FIGURES = _coll("figures")
COLL_TABLES = _coll("tables")
COLL_SUMMARIES = _coll("summaries")
ALL_COLLECTIONS = [COLL_TEXT, COLL_FIGURES, COLL_TABLES, COLL_SUMMARIES]

# ── Logging + audit trail ─────────────────────────────────────────────────
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("paperRAG")
audit = logging.getLogger("paperRAG.audit")
if AUDIT_LOG:
    try:
        _h = logging.FileHandler(AUDIT_LOG, encoding="utf-8")
        _h.setFormatter(logging.Formatter("%(message)s"))
        audit.addHandler(_h)
        audit.propagate = False
    except Exception as exc:  # pragma: no cover - best effort
        log.warning("could not open AUDIT_LOG %s: %s", AUDIT_LOG, exc)

# Keyword vocabularies — kept in sync with qdrant_v2_schema.py
KEYWORD_GROUPS = {
    "kw_alloy_system": [
        "ni_base_superalloy", "co_base_superalloy",
        "austenitic_steel", "ferritic_steel", "martensitic_steel",
        "duplex_steel", "maraging_steel",
        "refractory_alloy", "refractory_hea",
        "hea_mea", "titanium_alloy", "aluminum_alloy",
        "intermetallic", "shape_memory_alloy", "other",
    ],
    "kw_phases": [
        "gamma", "gamma_prime", "gamma_double_prime",
        "delta", "eta", "sigma", "mu", "laves", "chi",
        "carbide_MC", "carbide_M23C6", "carbide_M6C",
        "boride", "nitride", "oxide",
        "ferrite", "austenite", "martensite", "bainite", "pearlite",
        "B2", "L12", "L21", "D019", "D022", "TCP", "amorphous",
    ],
    "kw_techniques": [
        "SEM", "TEM", "STEM", "HAADF", "EBSD", "EDS", "WDS", "EELS",
        "APT", "XRD", "synchrotron_XRD", "neutron_diffraction",
        "DSC", "DTA", "TGA", "optical_microscopy", "confocal",
        "nanoindentation", "micropillar", "in_situ", "ex_situ",
        "CALPHAD", "thermodynamic_modeling", "FIB",
    ],
    "kw_testing": [
        "tensile", "compression", "shear", "creep", "stress_rupture",
        "fatigue_HCF", "fatigue_LCF", "fatigue_crack_growth",
        "fracture_toughness", "impact",
        "hardness_Vickers", "hardness_Rockwell", "nanoindentation",
        "wear", "erosion", "oxidation", "hot_corrosion", "hydrogen_embrittlement",
    ],
    "kw_processing": [
        "casting", "single_crystal_growth", "directional_solidification",
        "wrought", "forging", "rolling", "extrusion",
        "powder_metallurgy", "HIP", "SPS",
        "SLM", "EBM", "DED", "WAAM",
        "heat_treatment", "solution_treatment", "aging",
        "homogenization", "annealing",
        "cold_working", "warm_working", "hot_working",
        "welding", "joining", "surface_treatment", "coating",
    ],
    "kw_mechanisms": [
        "dislocation_glide", "dislocation_climb",
        "precipitate_shearing", "orowan_looping",
        "cross_slip", "kink_pair",
        "deformation_twinning", "TWIP", "TRIP", "MBIP",
        "grain_boundary_sliding", "grain_boundary_migration",
        "diffusional_creep", "dislocation_creep", "power_law_creep",
        "dynamic_recrystallization", "static_recrystallization",
        "void_nucleation", "void_growth", "coalescence",
        "cleavage", "intergranular_fracture",
    ],
    "kw_phenomena": [
        "yield_anomaly", "yield_drop", "strain_hardening", "strain_softening",
        "serrated_flow", "PLC_effect", "rafting", "coarsening",
        "precipitation", "dissolution", "spinodal_decomposition",
        "ordering", "short_range_order", "segregation", "partitioning",
        "recrystallization", "recovery", "grain_growth",
        "oxidation", "hot_corrosion", "carburization",
        "hydrogen_embrittlement", "stress_corrosion",
        "radiation_damage", "irradiation_hardening",
        "phase_transformation", "martensitic_transformation",
    ],
    "kw_properties": [
        "yield_strength", "ultimate_tensile_strength",
        "elongation", "ductility", "reduction_of_area",
        "hardness", "microhardness",
        "creep_life", "creep_rate", "creep_threshold_stress",
        "fatigue_life", "fatigue_limit", "fatigue_crack_growth_rate",
        "fracture_toughness", "impact_toughness",
        "elastic_modulus", "shear_modulus", "density",
        "thermal_conductivity", "thermal_expansion",
        "oxidation_rate", "corrosion_rate",
        "stacking_fault_energy", "APB_energy",
    ],
    "kw_approach": [
        "experimental", "simulation", "theoretical",
        "CALPHAD", "phase_field", "DFT", "MD",
        "finite_element", "crystal_plasticity", "dislocation_dynamics",
        "machine_learning", "data_driven",
        "review", "meta_analysis", "alloy_design", "optimization",
    ],
    "kw_alloys": [],   # open vocabulary — specific alloy names
    "kw_elements": [],  # open vocabulary — chemical elements
}


# ── Qdrant filter builder ────────────────────────────────────────────────

def _build_qdrant_filter(where: WhereFilter) -> Optional[qm.Filter]:
    """Convert a JSON where clause to a Qdrant Filter.

    where format (JSON array of conditions):
    [
      {"field": "kw_alloy_system", "op": "eq", "value": "ni_base_superalloy"},
      {"field": "year", "op": "gte", "value": 2015},
      {"field": "kw_techniques", "op": "in", "value": ["TEM", "APT"]},
      {"field": "journal_sjr", "op": "gte", "value": 2.0},
      {"field": "has_table", "op": "eq", "value": true},
      {"field": "affiliations", "op": "contains_any", "value": ["FAU"]}
    ]

    Operators: eq, neq, in, gte, lte, contains_any
    """
    if not where:
        return None

    conditions_raw = json.loads(where) if isinstance(where, str) else where
    if not conditions_raw:
        return None

    must = []
    must_not = []

    for cond in conditions_raw:
        field = cond["field"]
        op = cond["op"]
        value = cond["value"]

        if op == "eq":
            must.append(qm.FieldCondition(
                key=field, match=qm.MatchValue(value=value)
            ))
        elif op == "neq":
            must_not.append(qm.FieldCondition(
                key=field, match=qm.MatchValue(value=value)
            ))
        elif op == "in":
            # Match any of the values in the list
            must.append(qm.FieldCondition(
                key=field, match=qm.MatchAny(any=value)
            ))
        elif op == "gte":
            must.append(qm.FieldCondition(
                key=field, range=qm.Range(gte=value)
            ))
        elif op == "lte":
            must.append(qm.FieldCondition(
                key=field, range=qm.Range(lte=value)
            ))
        elif op == "contains_any":
            # For array fields: at least one value must match
            must.append(qm.FieldCondition(
                key=field, match=qm.MatchAny(any=value)
            ))

    parts = {}
    if must:
        parts["must"] = must
    if must_not:
        parts["must_not"] = must_not
    return qm.Filter(**parts) if parts else None


def _snippet(text: str, max_chars: int) -> str:
    """Truncate text to max_chars, breaking at word boundary."""
    if not text or len(text) <= max_chars:
        return text or ""
    cut = text[:max_chars].rsplit(" ", 1)[0]
    return cut + "..."


def _provenance(payload: Dict[str, Any], collection: str, point_id: str) -> Dict[str, Any]:
    """Standard provenance fields for every result."""
    return {
        "collection": collection,
        "point_id": point_id,
        "doi": payload.get("doi", ""),
        "title": payload.get("title", ""),
        "year": payload.get("year"),
        "first_author": payload.get("first_author", ""),
    }


# ── Backend class ─────────────────────────────────────────────────────────

class QdrantV2Backend:
    """Lazy-loaded Qdrant + Gemini clients."""

    def __init__(self, qdrant_url: str = QDRANT_URL):
        self._qdrant_url = qdrant_url
        self._qdrant: Optional[QdrantClient] = None
        self._gemini = None
        # Small in-memory LRU-ish cache so repeated identical queries don't
        # re-hit the Gemini embedding API (saves latency + quota).
        self._qcache: Dict[str, List[float]] = {}

    @property
    def qdrant(self) -> QdrantClient:
        if self._qdrant is None:
            kwargs: Dict[str, Any] = {"url": self._qdrant_url}
            if QDRANT_API_KEY:
                kwargs["api_key"] = QDRANT_API_KEY
            self._qdrant = QdrantClient(**kwargs)
        return self._qdrant

    @property
    def gemini(self):
        if self._gemini is None:
            if not (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")):
                raise RuntimeError(
                    "No Google API key found. Set GEMINI_API_KEY (or GOOGLE_API_KEY) "
                    "in the environment or .env file. See docs/05-google-api-key.md."
                )
            from google import genai
            self._gemini = genai.Client()
        return self._gemini

    def embed_text(self, text: str) -> List[float]:
        """Embed a query text with Gemini (RETRIEVAL_QUERY task type), cached."""
        cached = self._qcache.get(text)
        if cached is not None:
            return cached
        from google.genai import types
        result = self.gemini.models.embed_content(
            model=EMBED_MODEL,
            contents=text,
            config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
        )
        vec = result.embeddings[0].values
        if len(self._qcache) < 4096:
            self._qcache[text] = vec
        return vec

    def embed_image(self, image_path: str) -> Optional[List[float]]:
        """Embed an image file with Gemini."""
        from google.genai import types
        path = Path(image_path)
        if not path.exists():
            return None
        img_bytes = path.read_bytes()
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        img_part = types.Part.from_bytes(data=img_bytes, mime_type=mime)
        result = self.gemini.models.embed_content(
            model=EMBED_MODEL,
            contents=img_part,
            config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
        )
        return result.embeddings[0].values

    def embed_multimodal(
        self, image_path: str, text: str
    ) -> Optional[List[float]]:
        """Embed an image + text together as one fused query vector.

        Gemini's embed_content accepts a list of Parts (image bytes +
        plain string); the returned vector lives in the same space as
        embed_image / embed_text, so it can be queried against the
        existing materials_v2_figures `gemini` vector without any
        schema change.

        Falls back to embed_image when text is empty.
        """
        text = (text or "").strip()
        if not text:
            return self.embed_image(image_path)
        from google.genai import types
        path = Path(image_path)
        if not path.exists():
            return None
        img_bytes = path.read_bytes()
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        img_part = types.Part.from_bytes(data=img_bytes, mime_type=mime)
        result = self.gemini.models.embed_content(
            model=EMBED_MODEL,
            contents=[img_part, text],
            config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
        )
        return result.embeddings[0].values


# ── Server builder ────────────────────────────────────────────────────────

def build_server(
    qdrant_url: str = QDRANT_URL,
    host: str = MCP_HOST,
    port: int = MCP_PORT,
) -> FastMCP:
    backend = QdrantV2Backend(qdrant_url)
    mcp = FastMCP("paperRAG-v2", host=host, port=port)

    # ═══════════════════════════════════════════════════════════════════════
    # 1. SEMANTIC SEARCH (4 tools)
    # ═══════════════════════════════════════════════════════════════════════

    @mcp.tool()
    def search_text(
        query: str,
        top_k: int = 10,
        where: WhereFilter = None,
        group_by: Optional[str] = None,
        snippet_chars: int = 500,
    ) -> Dict[str, Any]:
        """Semantic search across text chunks with keyword pre-filtering.

        Searches the materials_v2 collection (section-level text chunks).
        Returns ranked chunks with scores, text snippet, section, DOI, citation_map, referenced_figures.

        Args:
            query: Natural language search query.
            top_k: Number of results (default 10).
            where: JSON filter array, e.g. [{"field":"kw_alloy_system","op":"eq","value":"ni_base_superalloy"}]
            group_by: Limit results per group (e.g. "doi" for 1 result per paper).
            snippet_chars: Max characters in text snippet (default 500).
        """
        try:
            vector = backend.embed_text(query)
            filt = _build_qdrant_filter(where)

            if group_by:
                results = backend.qdrant.query_points(
                    collection_name=COLL_TEXT,
                    query=vector,
                    using="gemini",
                    query_filter=filt,
                    limit=top_k,
                    group_by=group_by,
                    group_size=1,
                    with_payload=True,
                )
                # Unpack grouped results
                hits = []
                for group in results.groups:
                    for hit in group.hits:
                        hits.append(hit)
            else:
                results = backend.qdrant.query_points(
                    collection_name=COLL_TEXT,
                    query=vector,
                    using="gemini",
                    query_filter=filt,
                    limit=top_k,
                    with_payload=True,
                )
                hits = results.points

            records = []
            for hit in hits:
                p = hit.payload
                rec = _provenance(p, COLL_TEXT, str(hit.id))
                rec.update({
                    "score": hit.score,
                    "section": p.get("section", ""),
                    "chunk_type": p.get("chunk_type", ""),
                    "text": _snippet(p.get("text", ""), snippet_chars),
                    "journal": p.get("journal", ""),
                    "citation_count": p.get("citation_count"),
                    "journal_sjr": p.get("journal_sjr"),
                    "kw_alloy_system": p.get("kw_alloy_system", []),
                    "citation_map": p.get("citation_map", {}),
                    "referenced_figures": p.get("referenced_figures", []),
                    "has_table": p.get("has_table", False),
                })
                records.append(rec)

            return {"count": len(records), "results": records}
        except Exception as exc:
            return {"error": str(exc)}

    @mcp.tool()
    def search_figures(
        query: str,
        top_k: int = 10,
        where: WhereFilter = None,
        search_mode: str = "text",
    ) -> Dict[str, Any]:
        """Search figures by text description OR by image similarity.

        Args:
            query: Text description (search_mode="text") or absolute image path (search_mode="visual").
            top_k: Number of results (default 10).
            where: JSON filter array for pre-filtering (e.g. by figure_type, kw_alloy_system).
            search_mode: "text" (default) searches by caption, "visual" searches by image similarity.
        """
        try:
            if search_mode == "visual":
                vector = backend.embed_image(query)
                if vector is None:
                    return {"error": "Image not found: %s" % query}
                vector_name = "gemini"
            else:
                vector = backend.embed_text(query)
                vector_name = "gemini_text"

            filt = _build_qdrant_filter(where)
            results = backend.qdrant.query_points(
                collection_name=COLL_FIGURES,
                query=vector,
                using=vector_name,
                query_filter=filt,
                limit=top_k,
                with_payload=True,
            )

            records = []
            for hit in results.points:
                p = hit.payload
                rec = _provenance(p, COLL_FIGURES, str(hit.id))
                rec.update({
                    "score": hit.score,
                    "figure_id": p.get("figure_id", ""),
                    "paper_figure_number": p.get("paper_figure_number"),
                    "figure_type": p.get("figure_type", ""),
                    "enriched_caption": p.get("enriched_caption", ""),
                    "original_caption": p.get("original_caption", ""),
                    "kw_alloy_system": p.get("kw_alloy_system", []),
                    "kw_techniques": p.get("kw_techniques", []),
                    "kw_features": p.get("kw_features", []),
                    "referenced_by_chunks": p.get("referenced_by_chunks", []),
                })
                records.append(rec)

            return {"count": len(records), "results": records}
        except Exception as exc:
            return {"error": str(exc)}

    @mcp.tool()
    def search_tables(
        query: str,
        top_k: int = 10,
        where: WhereFilter = None,
    ) -> Dict[str, Any]:
        """Search for tables by description. Returns actual CSV data.

        Searches materials_v2_tables by semantic similarity of table descriptions.

        Args:
            query: Natural language description of desired table content.
            top_k: Number of results (default 10).
            where: JSON filter array (e.g. by table_type, kw_alloy_system, year).
        """
        try:
            vector = backend.embed_text(query)
            filt = _build_qdrant_filter(where)

            results = backend.qdrant.query_points(
                collection_name=COLL_TABLES,
                query=vector,
                using="gemini",
                query_filter=filt,
                limit=top_k,
                with_payload=True,
            )

            records = []
            for hit in results.points:
                p = hit.payload
                rec = _provenance(p, COLL_TABLES, str(hit.id))
                rec.update({
                    "score": hit.score,
                    "table_index": p.get("table_index"),
                    "table_description": p.get("table_description", ""),
                    "caption": p.get("caption", ""),
                    "table_type": p.get("table_type", ""),
                    "csv_data": p.get("csv_data", ""),
                    "headers": p.get("headers", []),
                    "n_rows": p.get("n_rows"),
                    "n_cols": p.get("n_cols"),
                    "materials": p.get("materials", []),
                    "properties_listed": p.get("properties_listed", []),
                    "kw_alloy_system": p.get("kw_alloy_system", []),
                })
                records.append(rec)

            return {"count": len(records), "results": records}
        except Exception as exc:
            return {"error": str(exc)}

    @mcp.tool()
    def search_papers(
        query: str = "",
        top_k: int = 10,
        where: WhereFilter = None,
    ) -> Dict[str, Any]:
        """Paper-level coarse search across summaries.

        Searches materials_v2_summaries for paper-level results. If query is empty,
        returns papers matching the filter only (scrolls instead of vector search).

        Args:
            query: Natural language search query (can be empty if using filters).
            top_k: Number of results (default 10).
            where: JSON filter array (e.g. by kw_alloy_system, year, journal).
        """
        try:
            filt = _build_qdrant_filter(where)

            if query.strip():
                vector = backend.embed_text(query)
                results = backend.qdrant.query_points(
                    collection_name=COLL_SUMMARIES,
                    query=vector,
                    using="gemini",
                    query_filter=filt,
                    limit=top_k,
                    with_payload=True,
                )
                hits = results.points
            else:
                # Filter-only: scroll without vector search
                results = backend.qdrant.scroll(
                    collection_name=COLL_SUMMARIES,
                    scroll_filter=filt,
                    limit=top_k,
                    with_payload=True,
                )
                hits = results[0]  # scroll returns (points, next_offset)

            records = []
            for hit in hits:
                p = hit.payload
                rec = _provenance(p, COLL_SUMMARIES, str(hit.id))
                rec.update({
                    "score": getattr(hit, "score", None),
                    "journal": p.get("journal", ""),
                    "authors": p.get("authors", []),
                    "summary_short": p.get("summary_short", ""),
                    "citation_count": p.get("citation_count"),
                    "journal_sjr": p.get("journal_sjr"),
                    "article_type": p.get("article_type", ""),
                    "article_category": p.get("article_category", ""),
                    "affiliations": p.get("affiliations", []),
                    "kw_alloy_system": p.get("kw_alloy_system", []),
                    "kw_techniques": p.get("kw_techniques", []),
                    "kw_mechanisms": p.get("kw_mechanisms", []),
                    "kw_properties": p.get("kw_properties", []),
                    "kw_approach": p.get("kw_approach", []),
                    "n_references": p.get("n_references"),
                    "n_resolved_references": p.get("n_resolved_references"),
                })
                records.append(rec)

            return {"count": len(records), "results": records}
        except Exception as exc:
            return {"error": str(exc)}

    # ═══════════════════════════════════════════════════════════════════════
    # 2. CROSS-COLLECTION QUERIES (3 tools)
    # ═══════════════════════════════════════════════════════════════════════

    @mcp.tool()
    def evidence_pack(
        question: str,
        top_k: int = 5,
        where: WhereFilter = None,
    ) -> Dict[str, Any]:
        """Multi-collection evidence gathering. Searches chunks + tables + figures simultaneously.

        Deduplicates by DOI and returns ranked evidence with provenance from all collections.
        Use this for comprehensive research questions that benefit from text, tabular, and visual evidence.

        Args:
            question: Research question in natural language.
            top_k: Results per collection (default 5, total up to 3x this).
            where: JSON filter array applied to all collections.
        """
        try:
            vector = backend.embed_text(question)
            filt = _build_qdrant_filter(where)

            all_evidence = []

            # Search text chunks
            try:
                text_results = backend.qdrant.query_points(
                    collection_name=COLL_TEXT,
                    query=vector, using="gemini",
                    query_filter=filt, limit=top_k, with_payload=True,
                )
                for hit in text_results.points:
                    p = hit.payload
                    all_evidence.append({
                        **_provenance(p, COLL_TEXT, str(hit.id)),
                        "score": hit.score,
                        "evidence_type": "text",
                        "section": p.get("section", ""),
                        "text": _snippet(p.get("text", ""), 400),
                    })
            except Exception:
                pass

            # Search tables
            try:
                table_results = backend.qdrant.query_points(
                    collection_name=COLL_TABLES,
                    query=vector, using="gemini",
                    query_filter=filt, limit=top_k, with_payload=True,
                )
                for hit in table_results.points:
                    p = hit.payload
                    all_evidence.append({
                        **_provenance(p, COLL_TABLES, str(hit.id)),
                        "score": hit.score,
                        "evidence_type": "table",
                        "table_description": p.get("table_description", ""),
                        "csv_data": p.get("csv_data", ""),
                        "table_type": p.get("table_type", ""),
                    })
            except Exception:
                pass

            # Search figures (text mode)
            try:
                fig_results = backend.qdrant.query_points(
                    collection_name=COLL_FIGURES,
                    query=vector, using="gemini_text",
                    query_filter=filt, limit=top_k, with_payload=True,
                )
                for hit in fig_results.points:
                    p = hit.payload
                    all_evidence.append({
                        **_provenance(p, COLL_FIGURES, str(hit.id)),
                        "score": hit.score,
                        "evidence_type": "figure",
                        "figure_id": p.get("figure_id", ""),
                        "figure_type": p.get("figure_type", ""),
                        "enriched_caption": p.get("enriched_caption", ""),
                    })
            except Exception:
                pass

            # Sort by score descending
            all_evidence.sort(key=lambda x: x.get("score", 0), reverse=True)

            # Deduplicate: keep best result per DOI per evidence_type
            seen = set()
            deduped = []
            for ev in all_evidence:
                key = (ev.get("doi", ""), ev.get("evidence_type", ""))
                if key not in seen:
                    seen.add(key)
                    deduped.append(ev)

            # Collect unique DOIs
            unique_dois = list(dict.fromkeys(ev["doi"] for ev in deduped if ev.get("doi")))

            return {
                "count": len(deduped),
                "unique_papers": len(unique_dois),
                "dois": unique_dois,
                "evidence": deduped,
            }
        except Exception as exc:
            return {"error": str(exc)}

    @mcp.tool()
    def get_chunk_figures(point_id: str) -> Dict[str, Any]:
        """Given a chunk point_id, return all figures it references.

        Resolves figure_ids from the chunk's referenced_figures field,
        then retrieves full figure metadata from materials_v2_figures.

        Args:
            point_id: The point ID of a chunk in materials_v2.
        """
        try:
            # Get the chunk
            points = backend.qdrant.retrieve(
                collection_name=COLL_TEXT,
                ids=[point_id],
                with_payload=True,
            )
            if not points:
                return {"error": "Chunk not found: %s" % point_id}

            chunk = points[0]
            figure_ids = chunk.payload.get("referenced_figures", [])
            if not figure_ids:
                return {
                    "chunk_id": point_id,
                    "doi": chunk.payload.get("doi", ""),
                    "section": chunk.payload.get("section", ""),
                    "figures": [],
                    "message": "This chunk does not reference any figures.",
                }

            doc_uid = chunk.payload.get("doc_uid", "")

            # Find figures matching these figure_ids for this paper
            figures = []
            for fig_id in figure_ids:
                fig_filter = qm.Filter(must=[
                    qm.FieldCondition(key="doc_uid", match=qm.MatchValue(value=doc_uid)),
                    qm.FieldCondition(key="figure_id", match=qm.MatchValue(value=fig_id)),
                ])
                fig_results = backend.qdrant.scroll(
                    collection_name=COLL_FIGURES,
                    scroll_filter=fig_filter,
                    limit=1,
                    with_payload=True,
                )
                for fig_point in fig_results[0]:
                    fp = fig_point.payload
                    figures.append({
                        "point_id": str(fig_point.id),
                        "figure_id": fp.get("figure_id", ""),
                        "paper_figure_number": fp.get("paper_figure_number"),
                        "figure_type": fp.get("figure_type", ""),
                        "enriched_caption": fp.get("enriched_caption", ""),
                        "original_caption": fp.get("original_caption", ""),
                        "kw_techniques": fp.get("kw_techniques", []),
                        "kw_features": fp.get("kw_features", []),
                    })

            return {
                "chunk_id": point_id,
                "doi": chunk.payload.get("doi", ""),
                "section": chunk.payload.get("section", ""),
                "figure_count": len(figures),
                "figures": figures,
            }
        except Exception as exc:
            return {"error": str(exc)}

    @mcp.tool()
    def get_figure_context(point_id: str) -> Dict[str, Any]:
        """Given a figure point_id, return all text chunks that discuss it.

        Uses the referenced_by_chunks field to find chunk indices, then retrieves
        those chunks from materials_v2.

        Args:
            point_id: The point ID of a figure in materials_v2_figures.
        """
        try:
            # Get the figure
            points = backend.qdrant.retrieve(
                collection_name=COLL_FIGURES,
                ids=[point_id],
                with_payload=True,
            )
            if not points:
                return {"error": "Figure not found: %s" % point_id}

            fig = points[0]
            fp = fig.payload
            chunk_indices = fp.get("referenced_by_chunks", [])
            doc_uid = fp.get("doc_uid", "")

            if not chunk_indices:
                return {
                    "figure_id": fp.get("figure_id", ""),
                    "doi": fp.get("doi", ""),
                    "enriched_caption": fp.get("enriched_caption", ""),
                    "chunks": [],
                    "message": "No chunks reference this figure.",
                }

            # Retrieve chunks by doc_uid + chunk_index
            chunks = []
            for idx in chunk_indices:
                chunk_filter = qm.Filter(must=[
                    qm.FieldCondition(key="doc_uid", match=qm.MatchValue(value=doc_uid)),
                    qm.FieldCondition(key="chunk_index", match=qm.MatchValue(value=idx)),
                ])
                chunk_results = backend.qdrant.scroll(
                    collection_name=COLL_TEXT,
                    scroll_filter=chunk_filter,
                    limit=1,
                    with_payload=True,
                )
                for cp in chunk_results[0]:
                    chunks.append({
                        "point_id": str(cp.id),
                        "chunk_index": cp.payload.get("chunk_index"),
                        "section": cp.payload.get("section", ""),
                        "chunk_type": cp.payload.get("chunk_type", ""),
                        "text": _snippet(cp.payload.get("text", ""), 500),
                    })

            return {
                "figure_id": fp.get("figure_id", ""),
                "doi": fp.get("doi", ""),
                "enriched_caption": fp.get("enriched_caption", ""),
                "chunk_count": len(chunks),
                "chunks": chunks,
            }
        except Exception as exc:
            return {"error": str(exc)}

    # ═══════════════════════════════════════════════════════════════════════
    # 3. CITATION GRAPH (4 tools)
    # ═══════════════════════════════════════════════════════════════════════

    @mcp.tool()
    def find_citing_papers(doi: str, top_k: int = 20) -> Dict[str, Any]:
        """Find papers in the corpus that cite a given DOI.

        Searches materials_v2_summaries where cited_dois contains the target DOI.

        Args:
            doi: The DOI to find citations for.
            top_k: Maximum number of citing papers to return (default 20).
        """
        try:
            filt = qm.Filter(must=[
                qm.FieldCondition(key="cited_dois", match=qm.MatchValue(value=doi))
            ])
            results = backend.qdrant.scroll(
                collection_name=COLL_SUMMARIES,
                scroll_filter=filt,
                limit=top_k,
                with_payload=True,
            )

            records = []
            for hit in results[0]:
                p = hit.payload
                records.append({
                    **_provenance(p, COLL_SUMMARIES, str(hit.id)),
                    "journal": p.get("journal", ""),
                    "authors": p.get("authors", []),
                    "summary_short": p.get("summary_short", ""),
                    "kw_alloy_system": p.get("kw_alloy_system", []),
                })

            return {
                "cited_doi": doi,
                "citing_count": len(records),
                "citing_papers": records,
            }
        except Exception as exc:
            return {"error": str(exc)}

    @mcp.tool()
    def find_cited_by(doi: str) -> Dict[str, Any]:
        """Get the full reference list for a paper — all DOIs it cites.

        Reads cited_dois from the paper's summary record in materials_v2_summaries.

        Args:
            doi: DOI of the paper whose references you want.
        """
        try:
            filt = qm.Filter(must=[
                qm.FieldCondition(key="doi", match=qm.MatchValue(value=doi))
            ])
            results = backend.qdrant.scroll(
                collection_name=COLL_SUMMARIES,
                scroll_filter=filt,
                limit=1,
                with_payload=True,
            )

            if not results[0]:
                return {"error": "Paper not found: %s" % doi}

            p = results[0][0].payload
            cited_dois = p.get("cited_dois", [])

            return {
                "doi": doi,
                "title": p.get("title", ""),
                "n_references": p.get("n_references"),
                "n_resolved_references": p.get("n_resolved_references"),
                "cited_dois": cited_dois,
            }
        except Exception as exc:
            return {"error": str(exc)}

    @mcp.tool()
    def find_shared_citations(doi_a: str, doi_b: str) -> Dict[str, Any]:
        """Find DOIs cited by both papers. Reveals common intellectual foundations.

        Args:
            doi_a: First paper DOI.
            doi_b: Second paper DOI.
        """
        try:
            shared = []
            dois_a = set()
            dois_b = set()

            for doi_query, target_set in [(doi_a, dois_a), (doi_b, dois_b)]:
                filt = qm.Filter(must=[
                    qm.FieldCondition(key="doi", match=qm.MatchValue(value=doi_query))
                ])
                results = backend.qdrant.scroll(
                    collection_name=COLL_SUMMARIES,
                    scroll_filter=filt,
                    limit=1,
                    with_payload=True,
                )
                if results[0]:
                    target_set.update(results[0][0].payload.get("cited_dois", []))

            shared = sorted(dois_a & dois_b)

            return {
                "doi_a": doi_a,
                "doi_b": doi_b,
                "refs_a": len(dois_a),
                "refs_b": len(dois_b),
                "shared_count": len(shared),
                "shared_dois": shared,
            }
        except Exception as exc:
            return {"error": str(exc)}

    @mcp.tool()
    def resolve_citation(doi: str, ref_key: str) -> Dict[str, Any]:
        """Given a paper DOI and citation key (e.g. "[9]"), return the resolved DOI.

        Looks up citation_map in the paper's text chunks to find what DOI
        a reference key like "[9]" or "[23]" points to.

        Args:
            doi: DOI of the paper containing the citation.
            ref_key: Citation key as it appears in text, e.g. "[9]", "[23]".
        """
        try:
            # Normalize ref_key format
            key = ref_key.strip()
            if not key.startswith("["):
                key = "[%s]" % key

            filt = qm.Filter(must=[
                qm.FieldCondition(key="doi", match=qm.MatchValue(value=doi))
            ])
            results = backend.qdrant.scroll(
                collection_name=COLL_TEXT,
                scroll_filter=filt,
                limit=100,
                with_payload=True,
            )

            # Search through chunks for citation_map containing this key
            for point in results[0]:
                citation_map = point.payload.get("citation_map", {})
                if key in citation_map:
                    resolved_doi = citation_map[key]
                    return {
                        "paper_doi": doi,
                        "ref_key": key,
                        "resolved_doi": resolved_doi,
                        "found_in_section": point.payload.get("section", ""),
                    }

            return {
                "paper_doi": doi,
                "ref_key": key,
                "resolved_doi": None,
                "message": "Citation key not found in any chunk for this paper.",
            }
        except Exception as exc:
            return {"error": str(exc)}

    # ═══════════════════════════════════════════════════════════════════════
    # 4. DOCUMENT NAVIGATION (4 tools)
    # ═══════════════════════════════════════════════════════════════════════

    @mcp.tool()
    def get_paper(doi: str) -> Dict[str, Any]:
        """Complete paper overview: summary, metadata, keyword tags, affiliations, citation stats.

        Args:
            doi: Paper DOI.
        """
        try:
            filt = qm.Filter(must=[
                qm.FieldCondition(key="doi", match=qm.MatchValue(value=doi))
            ])
            results = backend.qdrant.scroll(
                collection_name=COLL_SUMMARIES,
                scroll_filter=filt,
                limit=1,
                with_payload=True,
            )

            if not results[0]:
                return {"error": "Paper not found: %s" % doi}

            p = results[0][0].payload
            return {
                "doi": p.get("doi", ""),
                "title": p.get("title", ""),
                "year": p.get("year"),
                "journal": p.get("journal", ""),
                "publisher": p.get("publisher", ""),
                "authors": p.get("authors", []),
                "first_author": p.get("first_author", ""),
                "affiliations": p.get("affiliations", []),
                "citation_count": p.get("citation_count"),
                "journal_sjr": p.get("journal_sjr"),
                "journal_h_index": p.get("journal_h_index"),
                "article_type": p.get("article_type", ""),
                "article_category": p.get("article_category", ""),
                "article_subcategory": p.get("article_subcategory", ""),
                "summary_short": p.get("summary_short", ""),
                "summary_full": p.get("summary_full", ""),
                "kw_alloy_system": p.get("kw_alloy_system", []),
                "kw_alloys": p.get("kw_alloys", []),
                "kw_elements": p.get("kw_elements", []),
                "kw_phases": p.get("kw_phases", []),
                "kw_techniques": p.get("kw_techniques", []),
                "kw_testing": p.get("kw_testing", []),
                "kw_processing": p.get("kw_processing", []),
                "kw_mechanisms": p.get("kw_mechanisms", []),
                "kw_phenomena": p.get("kw_phenomena", []),
                "kw_properties": p.get("kw_properties", []),
                "kw_approach": p.get("kw_approach", []),
                "has_composition": p.get("has_composition"),
                "has_properties": p.get("has_properties"),
                "has_mechanisms": p.get("has_mechanisms"),
                "has_processing": p.get("has_processing"),
                "n_references": p.get("n_references"),
                "n_resolved_references": p.get("n_resolved_references"),
            }
        except Exception as exc:
            return {"error": str(exc)}

    @mcp.tool()
    def get_paper_chunks(
        doi: str,
        chunk_type: Optional[str] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """Get text chunks for a paper, optionally filtered by chunk_type.

        Args:
            doi: Paper DOI.
            chunk_type: Optional filter: abstract, introduction, methods, results, conclusion, body, table, references.
            limit: Max chunks to return (default 50).
        """
        try:
            conditions = [
                qm.FieldCondition(key="doi", match=qm.MatchValue(value=doi))
            ]
            if chunk_type:
                conditions.append(
                    qm.FieldCondition(key="chunk_type", match=qm.MatchValue(value=chunk_type))
                )
            filt = qm.Filter(must=conditions)

            results = backend.qdrant.scroll(
                collection_name=COLL_TEXT,
                scroll_filter=filt,
                limit=limit,
                with_payload=True,
            )

            chunks = []
            for point in results[0]:
                p = point.payload
                chunks.append({
                    "point_id": str(point.id),
                    "chunk_index": p.get("chunk_index"),
                    "section": p.get("section", ""),
                    "chunk_type": p.get("chunk_type", ""),
                    "text": p.get("text", ""),
                    "has_table": p.get("has_table", False),
                    "has_equation": p.get("has_equation", False),
                    "referenced_figures": p.get("referenced_figures", []),
                    "citation_map": p.get("citation_map", {}),
                })

            # Sort by chunk_index
            chunks.sort(key=lambda c: c.get("chunk_index", 0))

            return {
                "doi": doi,
                "chunk_count": len(chunks),
                "chunks": chunks,
            }
        except Exception as exc:
            return {"error": str(exc)}

    @mcp.tool()
    def get_paper_figures(doi: str) -> Dict[str, Any]:
        """All figures for a paper with enriched captions, figure types, and paper_figure_numbers.

        Args:
            doi: Paper DOI.
        """
        try:
            filt = qm.Filter(must=[
                qm.FieldCondition(key="doi", match=qm.MatchValue(value=doi))
            ])
            results = backend.qdrant.scroll(
                collection_name=COLL_FIGURES,
                scroll_filter=filt,
                limit=100,
                with_payload=True,
            )

            figures = []
            for point in results[0]:
                p = point.payload
                figures.append({
                    "point_id": str(point.id),
                    "figure_id": p.get("figure_id", ""),
                    "paper_figure_number": p.get("paper_figure_number"),
                    "figure_type": p.get("figure_type", ""),
                    "enriched_caption": p.get("enriched_caption", ""),
                    "original_caption": p.get("original_caption", ""),
                    "kw_alloy_system": p.get("kw_alloy_system", []),
                    "kw_techniques": p.get("kw_techniques", []),
                    "kw_features": p.get("kw_features", []),
                    "kw_keywords": p.get("kw_keywords", []),
                    "referenced_by_chunks": p.get("referenced_by_chunks", []),
                })

            # Sort by figure_id
            figures.sort(key=lambda f: f.get("figure_id", ""))

            return {
                "doi": doi,
                "figure_count": len(figures),
                "figures": figures,
            }
        except Exception as exc:
            return {"error": str(exc)}

    @mcp.tool()
    def get_paper_tables(doi: str) -> Dict[str, Any]:
        """All tables for a paper with CSV data, descriptions, types, and column headers.

        Args:
            doi: Paper DOI.
        """
        try:
            filt = qm.Filter(must=[
                qm.FieldCondition(key="doi", match=qm.MatchValue(value=doi))
            ])
            results = backend.qdrant.scroll(
                collection_name=COLL_TABLES,
                scroll_filter=filt,
                limit=100,
                with_payload=True,
            )

            tables = []
            for point in results[0]:
                p = point.payload
                tables.append({
                    "point_id": str(point.id),
                    "table_index": p.get("table_index"),
                    "caption": p.get("caption", ""),
                    "table_description": p.get("table_description", ""),
                    "table_type": p.get("table_type", ""),
                    "csv_data": p.get("csv_data", ""),
                    "headers": p.get("headers", []),
                    "n_rows": p.get("n_rows"),
                    "n_cols": p.get("n_cols"),
                    "materials": p.get("materials", []),
                    "properties_listed": p.get("properties_listed", []),
                    "kw_alloy_system": p.get("kw_alloy_system", []),
                })

            # Sort by table_index
            tables.sort(key=lambda t: t.get("table_index", 0))

            return {
                "doi": doi,
                "table_count": len(tables),
                "tables": tables,
            }
        except Exception as exc:
            return {"error": str(exc)}

    # ═══════════════════════════════════════════════════════════════════════
    # 5. COMPOSITION & PROPERTY SEARCH (2 tools)
    # ═══════════════════════════════════════════════════════════════════════

    @mcp.tool()
    def search_by_composition(
        elements: str,
        alloy_system: Optional[str] = None,
        top_k: int = 10,
    ) -> Dict[str, Any]:
        """Find papers discussing specific element combinations.

        Filters by kw_elements + optional kw_alloy_system, then performs semantic search
        with a query constructed from the element list.

        Args:
            elements: Comma-separated element list, e.g. "Ni,Cr,Al,Ti".
            alloy_system: Optional alloy system filter, e.g. "ni_base_superalloy".
            top_k: Number of results (default 10).
        """
        try:
            el_list = [e.strip() for e in elements.split(",") if e.strip()]
            if not el_list:
                return {"error": "No elements provided."}

            conditions = [
                qm.FieldCondition(
                    key="kw_elements",
                    match=qm.MatchAny(any=el_list),
                )
            ]
            if alloy_system:
                conditions.append(
                    qm.FieldCondition(
                        key="kw_alloy_system",
                        match=qm.MatchValue(value=alloy_system),
                    )
                )
            filt = qm.Filter(must=conditions)

            # Build a semantic query from elements
            query = "alloy composition containing %s" % "-".join(el_list)
            vector = backend.embed_text(query)

            results = backend.qdrant.query_points(
                collection_name=COLL_SUMMARIES,
                query=vector,
                using="gemini",
                query_filter=filt,
                limit=top_k,
                with_payload=True,
            )

            records = []
            for hit in results.points:
                p = hit.payload
                rec = _provenance(p, COLL_SUMMARIES, str(hit.id))
                rec.update({
                    "score": hit.score,
                    "journal": p.get("journal", ""),
                    "summary_short": p.get("summary_short", ""),
                    "kw_alloy_system": p.get("kw_alloy_system", []),
                    "kw_elements": p.get("kw_elements", []),
                    "kw_alloys": p.get("kw_alloys", []),
                    "has_composition": p.get("has_composition"),
                })
                records.append(rec)

            return {"elements": el_list, "count": len(records), "results": records}
        except Exception as exc:
            return {"error": str(exc)}

    @mcp.tool()
    def search_by_properties(
        property_type: str,
        alloy_system: Optional[str] = None,
        where: WhereFilter = None,
        top_k: int = 10,
    ) -> Dict[str, Any]:
        """Find papers reporting specific property measurements.

        Combines kw_properties filter with semantic search on summaries.

        Args:
            property_type: Property keyword, e.g. "yield_strength", "creep_life", "elongation".
            alloy_system: Optional alloy system filter.
            where: Additional JSON filter conditions.
            top_k: Number of results (default 10).
        """
        try:
            conditions = [
                qm.FieldCondition(
                    key="kw_properties",
                    match=qm.MatchValue(value=property_type),
                )
            ]
            if alloy_system:
                conditions.append(
                    qm.FieldCondition(
                        key="kw_alloy_system",
                        match=qm.MatchValue(value=alloy_system),
                    )
                )

            # Merge with additional where conditions
            extra_filt = _build_qdrant_filter(where)
            if extra_filt and extra_filt.must:
                conditions.extend(extra_filt.must)
            filt = qm.Filter(must=conditions)

            # Semantic query
            readable = property_type.replace("_", " ")
            query = "%s measurements and data" % readable
            vector = backend.embed_text(query)

            results = backend.qdrant.query_points(
                collection_name=COLL_SUMMARIES,
                query=vector,
                using="gemini",
                query_filter=filt,
                limit=top_k,
                with_payload=True,
            )

            records = []
            for hit in results.points:
                p = hit.payload
                rec = _provenance(p, COLL_SUMMARIES, str(hit.id))
                rec.update({
                    "score": hit.score,
                    "journal": p.get("journal", ""),
                    "summary_short": p.get("summary_short", ""),
                    "kw_alloy_system": p.get("kw_alloy_system", []),
                    "kw_properties": p.get("kw_properties", []),
                    "has_properties": p.get("has_properties"),
                })
                records.append(rec)

            return {"property_type": property_type, "count": len(records), "results": records}
        except Exception as exc:
            return {"error": str(exc)}

    # ═══════════════════════════════════════════════════════════════════════
    # 6. VISUAL SIMILARITY (1 tool)
    # ═══════════════════════════════════════════════════════════════════════

    @mcp.tool()
    def find_similar_images(
        image_path: str,
        top_k: int = 10,
        where: WhereFilter = None,
        text: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Find visually similar figures across the corpus.

        Embeds the provided image (optionally fused with a text query)
        and searches against the gemini vector in materials_v2_figures.
        Can pre-filter by figure_type, alloy_system, technique.

        Args:
            image_path: Absolute path to an image file (PNG or JPEG).
            top_k: Number of results (default 10).
            where: JSON filter array for pre-filtering.
            text: Optional natural-language query fused with the image
                into one multimodal embedding. Use it to bias retrieval
                toward the question the user is asking about the image
                (e.g. "is this gamma prime?"). When omitted, falls back
                to pure image-similarity search.
        """
        try:
            if text and text.strip():
                vector = backend.embed_multimodal(image_path, text)
            else:
                vector = backend.embed_image(image_path)
            if vector is None:
                return {"error": "Image not found or unreadable: %s" % image_path}

            filt = _build_qdrant_filter(where)

            results = backend.qdrant.query_points(
                collection_name=COLL_FIGURES,
                query=vector,
                using="gemini",
                query_filter=filt,
                limit=top_k,
                with_payload=True,
            )

            records = []
            for hit in results.points:
                p = hit.payload
                rec = _provenance(p, COLL_FIGURES, str(hit.id))
                rec.update({
                    "score": hit.score,
                    "figure_id": p.get("figure_id", ""),
                    "paper_figure_number": p.get("paper_figure_number"),
                    "figure_type": p.get("figure_type", ""),
                    "enriched_caption": p.get("enriched_caption", ""),
                    "kw_alloy_system": p.get("kw_alloy_system", []),
                    "kw_techniques": p.get("kw_techniques", []),
                    "kw_features": p.get("kw_features", []),
                })
                records.append(rec)

            return {"count": len(records), "results": records}
        except Exception as exc:
            return {"error": str(exc)}

    # ═══════════════════════════════════════════════════════════════════════
    # 7. FACET & DISCOVERY (3 tools)
    # ═══════════════════════════════════════════════════════════════════════

    @mcp.tool()
    def facet_counts(
        field: str,
        where: WhereFilter = None,
        top_n: int = 20,
    ) -> Dict[str, Any]:
        """Count distribution of any keyword/metadata field.

        E.g. "top 20 alloy systems", "most common techniques in HEA papers",
        "publication year distribution".

        Args:
            field: Payload field name, e.g. "kw_alloy_system", "journal", "year", "kw_techniques".
            where: Optional JSON filter to scope the counts.
            top_n: Number of top values to return (default 20).
        """
        try:
            filt = _build_qdrant_filter(where)

            # Use Qdrant facet API
            result = backend.qdrant.facet(
                collection_name=COLL_SUMMARIES,
                key=field,
                facet_filter=filt,
                limit=top_n,
            )

            counts = [{"value": hit.value, "count": hit.count} for hit in result.hits]

            return {
                "field": field,
                "total_values": len(counts),
                "counts": counts,
            }
        except Exception as exc:
            return {"error": str(exc)}

    @mcp.tool()
    def list_keywords(
        group: Optional[str] = None,
        show_corpus_counts: bool = False,
    ) -> Dict[str, Any]:
        """Return controlled vocabulary for a keyword group (or all groups).

        Shows valid filter values. Optionally includes corpus counts (how many papers
        actually have each value). Use this to answer questions like "what alloy systems
        are in the corpus?" or "what techniques can I filter by?".

        Args:
            group: Keyword group name, e.g. "kw_alloy_system", "kw_phases", "kw_techniques".
                   Omit to return all groups.
            show_corpus_counts: If true, also queries Qdrant for actual value counts in the corpus.
        """
        if group and group not in KEYWORD_GROUPS:
            return {"error": "Unknown group: %s. Available: %s" % (group, ", ".join(KEYWORD_GROUPS.keys()))}

        groups_to_show = {group: KEYWORD_GROUPS[group]} if group else {k: v for k, v in KEYWORD_GROUPS.items() if v}

        if not show_corpus_counts:
            if group:
                return {"group": group, "keywords": KEYWORD_GROUPS[group]}
            return {"groups": groups_to_show}

        # Include corpus counts
        try:
            result = {}
            for g, vocab in groups_to_show.items():
                try:
                    facet = backend.qdrant.facet(
                        collection_name=COLL_SUMMARIES,
                        key=g,
                        limit=200,
                    )
                    counts = {h.value: h.count for h in facet.hits}
                    result[g] = [
                        {"value": v, "count": counts.get(v, 0)}
                        for v in vocab
                    ]
                    # Also include any values present in corpus but not in vocabulary
                    extra = [
                        {"value": h.value, "count": h.count}
                        for h in facet.hits if h.value not in vocab
                    ]
                    if extra:
                        result[g].extend(extra)
                except Exception:
                    result[g] = vocab
            return {"groups": result}
        except Exception as exc:
            return {"error": str(exc)}

    @mcp.tool()
    def corpus_stats() -> Dict[str, Any]:
        """Overall corpus statistics: total papers, chunks, figures, tables.

        Also returns top journals and top alloy systems from the summaries collection.
        """
        try:
            stats = {}
            for name in [COLL_TEXT, COLL_FIGURES, COLL_TABLES, COLL_SUMMARIES]:
                try:
                    info = backend.qdrant.get_collection(name)
                    stats[name] = info.points_count
                except Exception:
                    stats[name] = 0

            # Top alloy systems
            try:
                alloy_facet = backend.qdrant.facet(
                    collection_name=COLL_SUMMARIES,
                    key="kw_alloy_system",
                    limit=15,
                )
                top_alloy_systems = [
                    {"value": h.value, "count": h.count} for h in alloy_facet.hits
                ]
            except Exception:
                top_alloy_systems = []

            # Top journals
            try:
                journal_facet = backend.qdrant.facet(
                    collection_name=COLL_SUMMARIES,
                    key="journal",
                    limit=15,
                )
                top_journals = [
                    {"value": h.value, "count": h.count} for h in journal_facet.hits
                ]
            except Exception:
                top_journals = []

            # Year range
            year_min = None
            year_max = None
            try:
                year_facet = backend.qdrant.facet(
                    collection_name=COLL_SUMMARIES,
                    key="year",
                    limit=200,
                )
                years = [h.value for h in year_facet.hits if isinstance(h.value, int)]
                if years:
                    year_min = min(years)
                    year_max = max(years)
            except Exception:
                pass

            return {
                "collections": stats,
                "total_papers": stats.get(COLL_SUMMARIES, 0),
                "total_chunks": stats.get(COLL_TEXT, 0),
                "total_figures": stats.get(COLL_FIGURES, 0),
                "total_tables": stats.get(COLL_TABLES, 0),
                "year_range": {"min": year_min, "max": year_max},
                "top_alloy_systems": top_alloy_systems,
                "top_journals": top_journals,
            }
        except Exception as exc:
            return {"error": str(exc)}

    # ═══════════════════════════════════════════════════════════════════════
    # 8. METADATA (2 tools)
    # ═══════════════════════════════════════════════════════════════════════

    @mcp.tool()
    def server_info() -> Dict[str, Any]:
        """Collection stats (point counts, status), server URL, available vectors per collection."""
        try:
            collections = {}
            for name in [COLL_TEXT, COLL_FIGURES, COLL_TABLES, COLL_SUMMARIES]:
                try:
                    info = backend.qdrant.get_collection(name)
                    vectors = {}
                    if hasattr(info.config.params, "vectors") and isinstance(info.config.params.vectors, dict):
                        for vname, vparams in info.config.params.vectors.items():
                            vectors[vname] = {"size": vparams.size, "distance": str(vparams.distance)}
                    collections[name] = {
                        "points_count": info.points_count,
                        "status": str(info.status),
                        "vectors": vectors,
                    }
                except Exception as e:
                    collections[name] = {"error": str(e)}

            return {
                "server_url": backend._qdrant_url,
                "embedding_model": EMBED_MODEL,
                "collections": collections,
            }
        except Exception as exc:
            return {"error": str(exc)}

    @mcp.tool()
    def get_schema(collection: str) -> Dict[str, Any]:
        """Payload fields and types for a collection. Useful for understanding what can be filtered.

        Args:
            collection: Collection name, e.g. COLL_TEXT, COLL_FIGURES.
        """
        try:
            info = backend.qdrant.get_collection(collection)

            # Extract payload schema from collection info
            payload_schema = {}
            if info.payload_schema:
                for field_name, field_info in info.payload_schema.items():
                    payload_schema[field_name] = {
                        "data_type": str(field_info.data_type) if hasattr(field_info, "data_type") else str(field_info),
                        "points": getattr(field_info, "points", None),
                    }

            # Vector info
            vectors = {}
            if hasattr(info.config.params, "vectors") and isinstance(info.config.params.vectors, dict):
                for vname, vparams in info.config.params.vectors.items():
                    vectors[vname] = {"size": vparams.size, "distance": str(vparams.distance)}

            return {
                "collection": collection,
                "points_count": info.points_count,
                "vectors": vectors,
                "payload_fields": payload_schema,
            }
        except Exception as exc:
            return {"error": str(exc)}

    @mcp.tool()
    def list_databases() -> Dict[str, Any]:
        """List the vector databases (collection groups) available on this server.

        Returns every Qdrant collection grouped by prefix and flags which prefix
        this server currently serves (COLLECTION_PREFIX). Use it to discover
        other corpora that an administrator could switch the server to.
        """
        try:
            cols = backend.qdrant.get_collections().collections
            names = sorted(c.name for c in cols)
            groups: Dict[str, List[str]] = {}
            for n in names:
                base = n
                for suffix in ("_summaries", "_figures", "_tables"):
                    if n.endswith(suffix):
                        base = n[: -len(suffix)]
                        break
                groups.setdefault(base, []).append(n)
            return {
                "active_prefix": COLLECTION_PREFIX,
                "active_collections": ALL_COLLECTIONS,
                "available_databases": groups,
            }
        except Exception as exc:
            return {"error": str(exc)}

    return mcp


async def _asgi_json(send, status: int, payload: Dict[str, Any]) -> None:
    """Send a small JSON HTTP response directly over raw ASGI."""
    body = json.dumps(payload).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
    })
    await send({"type": "http.response.body", "body": body})


def _audit_wrapper(app, server_label: str):
    """Pure-ASGI middleware around the FastMCP app.

    Responsibilities:
      * answer GET /health (and /healthz) WITHOUT auth, so Docker / load
        balancers can health-check without touching the MCP protocol;
      * optionally enforce API-token auth — only when MCP_AUTH_TOKENS is set;
      * log the client IP and the username (token-mapped when auth is on, else
        the self-declared X-User header) for every request.

    Non-HTTP scopes (lifespan, websocket) pass straight through so FastMCP's
    streamable-HTTP session manager starts and stops normally.
    """
    async def wrapped(scope, receive, send):
        if scope.get("type") != "http":
            await app(scope, receive, send)
            return

        headers = {
            k.decode("latin1").lower(): v.decode("latin1")
            for k, v in scope.get("headers", [])
        }
        client = scope.get("client") or ("-", 0)
        # Behind a reverse proxy (Caddy/nginx) the real client is in
        # X-Forwarded-For; fall back to the direct socket peer otherwise.
        ip = headers.get("x-forwarded-for", client[0]).split(",")[0].strip()
        path = scope.get("path", "")
        method = scope.get("method", "")

        # Health is always open so monitoring never gets blocked by auth.
        if path.rstrip("/") in ("/health", "/healthz"):
            await _asgi_json(send, 200, {
                "status": "ok",
                "server": server_label,
                "prefix": COLLECTION_PREFIX,
                "auth": "token" if AUTH_ENABLED else "open",
            })
            return

        # Identity: a token-mapped username when auth is on, otherwise the
        # self-declared X-User header (honour system on a trusted network).
        user = headers.get("x-user", "-")
        if AUTH_ENABLED:
            token = ""
            authz = headers.get("authorization", "")
            if authz[:7].lower() == "bearer ":
                token = authz[7:].strip()
            if not token:
                token = headers.get("x-api-key", "").strip()
            mapped = AUTH_TOKENS.get(token)
            if mapped is None:
                audit.info(json.dumps({
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "ip": ip, "user": "?", "method": method, "path": path,
                    "auth": "denied",
                }))
                await _asgi_json(send, 401, {
                    "error": "unauthorized",
                    "detail": "a valid API token is required "
                              "(Authorization: Bearer <token>)",
                })
                return
            user = mapped

        audit.info(json.dumps({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "ip": ip,
            "user": user,
            "method": method,
            "path": path,
            "auth": "token" if AUTH_ENABLED else "open",
        }))
        await app(scope, receive, send)

    return wrapped


def main() -> None:
    parser = argparse.ArgumentParser(
        description="paperRAG-v2 MCP server (Qdrant + Gemini embeddings)."
    )
    parser.add_argument("--qdrant-url", default=QDRANT_URL, help="Qdrant server URL.")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default=MCP_TRANSPORT,
        help="MCP transport. Default comes from MCP_TRANSPORT env (streamable-http).",
    )
    parser.add_argument("--host", default=MCP_HOST, help="Bind host for HTTP transports.")
    parser.add_argument("--port", type=int, default=MCP_PORT, help="Bind port for HTTP transports.")
    args = parser.parse_args()

    server = build_server(qdrant_url=args.qdrant_url, host=args.host, port=args.port)
    log.info(
        "paperRAG-v2 starting | transport=%s | qdrant=%s | prefix=%s | model=%s | auth=%s",
        args.transport, args.qdrant_url, COLLECTION_PREFIX, EMBED_MODEL,
        ("token (%d keys)" % len(AUTH_TOKENS)) if AUTH_ENABLED else "open (IP + X-User)",
    )

    # stdio: classic local transport (handy for testing with `mcp dev`).
    if args.transport == "stdio":
        server.run()
        return

    # HTTP transports: serve the ASGI app ourselves so we can attach the audit
    # wrapper and /health endpoint.
    try:
        import uvicorn
    except ImportError:
        log.error("uvicorn is required for HTTP transports: pip install uvicorn")
        sys.exit(1)

    app = server.sse_app() if args.transport == "sse" else server.streamable_http_app()
    uvicorn.run(
        _audit_wrapper(app, "paperRAG-v2"),
        host=args.host,
        port=args.port,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
