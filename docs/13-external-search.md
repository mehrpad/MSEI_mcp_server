# 13 · External discovery — Crossref + OpenAlex

> Find related publications that are **not in the local library** — with DOI,
> abstract, and metadata — by querying the Crossref and OpenAlex scholarly APIs.

The server searches your local corpus (deep, full-text, semantic) **and**, when
useful, the outside world (~150M Crossref records, ~250M OpenAlex works). Results
are flagged **`in_local_library`** so you can tell what you already have vs. what
is external-only.

---

## What the user gets (three new tools)

| Tool | What it does |
|------|--------------|
| `search_external` | Search Crossref + OpenAlex by query → ranked works (DOI, title, authors, year, venue, **abstract when available**, citation count), each flagged `in_local_library`. |
| `get_external_work` | Full merged Crossref+OpenAlex metadata + abstract for one **DOI** or OpenAlex id. |
| `resolve_reference` | Turn a fuzzy citation (title / first author / year / journal) into a **DOI** + metadata. |

The assistant typically **searches local first** (`search_text`), then calls
`search_external` to broaden — returning the external hits that aren't already in
your corpus.

Example prompts:
> *"Search our library for rhenium creep in Ni-base superalloys, then use
> search_external to find related work we don't have yet — give DOIs and abstracts."*
> *"Resolve this reference to a DOI: 'Fleischmann, solid solution hardening creep, 2015'."*
> *"Get the abstract for DOI 10.1016/j.actamat.2014.12.011."*

---

## Setup (admin)

It's **on by default**. Two things to configure in `.env`, then rebuild:

```bash
# .env
CONTACT_EMAIL=you@fau.de     # joins the APIs' "polite pool" (higher rate limits)
# EXTERNAL_SEARCH=off        # to disable the whole feature
# XREF_CACHE_DB=/data/crossref_openalex_cache.sqlite   # cache location (persisted)
```

```bash
cd ~/MSEI_mcp_server
git fetch origin && git reset --hard origin/main
docker compose up -d --build mcp
```

Confirm: `docker compose logs mcp | grep "external discovery"` → `external discovery ON`.
No API key is needed (both APIs are free); `CONTACT_EMAIL` is just courtesy.

**Prerequisite — internet egress:** the server calls `api.crossref.org` and
`api.openalex.org`. On the FAU VM that goes through the **proxy** (the same
`HTTP_PROXY`/`HTTPS_PROXY` in `.env` that Google uses). Confirm the proxy allows
them:

```bash
curl -x http://proxy.rrze.uni-erlangen.de:80 -sS -m 10 -o /dev/null -w "crossref %{http_code}\n" https://api.crossref.org/works?rows=1
curl -x http://proxy.rrze.uni-erlangen.de:80 -sS -m 10 -o /dev/null -w "openalex %{http_code}\n" https://api.openalex.org/works?per-page=1
```

---

## How "already in our library?" works

`search_external` compares each result's DOI against the DOIs in your
`materials_v2_summaries` collection (case-insensitive), cached in memory and
refreshed hourly. Each hit gets `in_local_library: true|false`; pass
`exclude_in_library: true` to return only the genuinely new ones.

Repeated lookups are served from a local **SQLite cache** (`XREF_CACHE_DB`, in the
`mcp_data` volume) so you stay well under the APIs' rate limits.

---

## Advantages

- Reach far beyond the ~31k local corpus for **discovery** of related work.
- **Free**, no API key; abstracts reconstructed from OpenAlex; Crossref+OpenAlex
  records merged for the best available metadata.
- Dedup against your library, so users see what's new vs. already searchable here.

## Limitations (important)

- **Metadata + abstract only — not full text, not embedded.** External hits are
  *discovery-level*; you can't do the deep passage-level semantic search on them
  (that only works on the ingested local corpus). Relevance is the APIs' keyword
  ranking, not your vector search.
- **Abstracts vary.** OpenAlex has abstracts for many works (reconstructed from an
  inverted index); **Crossref abstracts are sparse/often missing**. If OpenAlex is
  down or a work lacks one, you'll get metadata without an abstract.
- **Rate limits / etiquette.** Free "polite pool" (~10 req/s, ~100k/day) — set
  `CONTACT_EMAIL`; the SQLite cache absorbs repeats.
- **Latency & concurrency.** +0.3–1 s per external call (proxy + API), and it
  shares the blocking-serialization limit (see [docs/09]); heavy use is a reason
  to add the async/threadpool improvement later.
- **Discovery ≠ ingestion.** A found external paper is **not** added to the
  searchable library automatically — that still requires the separate ingest
  pipeline. (A future "add this DOI to the library" handoff is possible.)

---

⬅️ Back: [07 · Connect OpenCode](07-connect-opencode.md)  ·  🏠 [Overview](00-overview.md)
