#!/usr/bin/env python3
"""Fetch daily literature candidates for Codex-authored digest summaries.

This script intentionally does not call an LLM. It gathers open metadata and
abstracts, then writes a JSON payload for a Codex automation to summarize.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


RECIPIENT_EMAIL = ""
CROSSREF_MAILTO = ""
LANGUAGE = "zh-CN"
TIMEZONE = ""
SCHEDULE_TIME = "09:00"
DEFAULT_OUTPUT_DIR = Path("daily-literature-digests")
DEFAULT_STATE_FILE = DEFAULT_OUTPUT_DIR / "state.json"

PUBLISHERS = [
    {
        "key": "elsevier",
        "display": "Elsevier",
        "crossref_member": "78",
        "crossref_name": "Elsevier BV",
        "crossref_date_mode": "created-date",
        "openalex_publishers": ["P4310320990"],
    },
    {
        "key": "springer-nature",
        "display": "Springer Nature",
        "crossref_member": "297",
        "crossref_name": "Springer Science and Business Media LLC",
        "crossref_date_mode": "pub-date",
        "openalex_publishers": ["P4310319965", "P4310320108", "P4404664013"],
    },
    {
        "key": "wiley",
        "display": "Wiley",
        "crossref_member": "311",
        "crossref_name": "Wiley",
        "crossref_date_mode": "pub-date",
        "openalex_publishers": ["P4310320595"],
    },
    {
        "key": "taylor-francis-routledge",
        "display": "Taylor & Francis / Routledge",
        "crossref_member": "301",
        "crossref_name": "Informa UK Limited",
        "crossref_date_mode": "pub-date",
        "openalex_publishers": ["P4310320547", "P4310319847"],
    },
]

KEYWORD_GROUPS = [
    {
        "label": "multi-objective optimization",
        "terms": [
            "multi-objective optimization",
            "multi objective optimization",
            "multiobjective optimization",
        ],
    },
    {
        "label": "surrogate-assisted optimization",
        "terms": [
            "surrogate-assisted optimization",
            "surrogate assisted optimization",
            "surrogate model optimization",
            "surrogate models",
        ],
    },
    {
        "label": "reinforcement learning",
        "terms": ["reinforcement learning", "deep reinforcement learning", "multi-agent reinforcement learning"],
    },
    {
        "label": "prefabricated/modular construction",
        "terms": [
            "prefabricated construction",
            "prefabricated buildings",
            "precast components",
            "modular construction",
            "off-site construction",
        ],
    },
    {
        "label": "building design/operation",
        "terms": [
            "building design",
            "building operation",
            "sustainable building design",
            "building energy",
            "building performance",
        ],
    },
    {
        "label": "HVAC control",
        "terms": ["HVAC control", "HVAC", "heating ventilation air conditioning", "multi-zone buildings"],
    },
]

EXCLUDED_TITLE_PATTERNS = [
    r"\bcorrection\b",
    r"\berratum\b",
    r"\bretraction\b",
    r"\bexpression of concern\b",
    r"\beditorial board\b",
    r"\bannouncement\b",
    r"\bbook review\b",
    r"\bcalendar\b",
]

USER_AGENT_BASE = "CodexDailyLiteratureDigest/1.0"
ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_date(value: str) -> dt.datetime:
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def date_only(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).date().isoformat()


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def clean_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [clean_text(value) for value in values if clean_text(value)]


def configured_keyword_groups(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    groups: list[dict[str, Any]] = []
    for group in values:
        if not isinstance(group, dict):
            continue
        label = clean_text(group.get("label"))
        terms = clean_list(group.get("terms"))
        if label and terms:
            groups.append({"label": label, "terms": terms})
    return groups


def configured_publishers(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    publishers: list[dict[str, Any]] = []
    for publisher in values:
        if not isinstance(publisher, dict):
            continue
        key = clean_text(publisher.get("key"))
        display = clean_text(publisher.get("display"))
        member = clean_text(publisher.get("crossref_member"))
        if not key or not display or not member:
            continue
        publishers.append(
            {
                "key": key,
                "display": display,
                "crossref_member": member,
                "crossref_name": clean_text(publisher.get("crossref_name")) or display,
                "crossref_date_mode": clean_text(publisher.get("crossref_date_mode")) or "pub-date",
                "openalex_publishers": clean_list(publisher.get("openalex_publishers")),
            }
        )
    return publishers


def read_config(path_value: str | None) -> dict[str, Any]:
    if not path_value:
        return {}
    path = Path(path_value)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    return read_json(path, {})


def int_setting(cli_value: int | None, config: dict[str, Any], key: str, default: int) -> int:
    if cli_value is not None:
        return cli_value
    value = config.get(key)
    if value is None:
        return default
    return int(value)


def float_setting(cli_value: float | None, config: dict[str, Any], key: str, default: float) -> float:
    if cli_value is not None:
        return cli_value
    value = config.get(key)
    if value is None:
        return default
    return float(value)


def apply_runtime_config(args: argparse.Namespace) -> None:
    config = read_config(getattr(args, "config", None))
    global RECIPIENT_EMAIL, CROSSREF_MAILTO, LANGUAGE, TIMEZONE, SCHEDULE_TIME, PUBLISHERS, KEYWORD_GROUPS
    RECIPIENT_EMAIL = clean_text(config.get("recipient_email"))
    CROSSREF_MAILTO = clean_text(config.get("crossref_mailto")) or RECIPIENT_EMAIL
    LANGUAGE = clean_text(config.get("language")) or LANGUAGE
    TIMEZONE = clean_text(config.get("timezone")) or TIMEZONE
    SCHEDULE_TIME = clean_text(config.get("schedule_time")) or SCHEDULE_TIME

    configured_groups = configured_keyword_groups(config.get("keyword_groups"))
    if configured_groups:
        KEYWORD_GROUPS = configured_groups
    configured_sources = configured_publishers(config.get("publishers"))
    if configured_sources:
        PUBLISHERS = configured_sources

    if args.command == "fetch":
        output_dir = args.output_dir or clean_text(config.get("output_dir")) or str(DEFAULT_OUTPUT_DIR)
        args.output_dir = output_dir
        args.state_file = args.state_file or clean_text(config.get("state_file")) or str(Path(output_dir) / "state.json")
        args.lookback_days = int_setting(args.lookback_days, config, "lookback_days", 7)
        args.rows = int_setting(args.rows, config, "rows", 20)
        args.arxiv_rows = int_setting(args.arxiv_rows, config, "arxiv_rows", 25)
        args.max_papers = int_setting(args.max_papers, config, "max_papers", 30)
        args.sleep = float_setting(args.sleep, config, "sleep", 0.25)
        if args.include_arxiv is None:
            args.include_arxiv = bool(config.get("include_arxiv", True))
    elif args.command == "mark-success":
        args.state_file = args.state_file or clean_text(config.get("state_file")) or str(DEFAULT_STATE_FILE)


def user_agent() -> str:
    if CROSSREF_MAILTO:
        return f"{USER_AGENT_BASE} (mailto:{CROSSREF_MAILTO})"
    return USER_AGENT_BASE


def http_json(url: str, *, retries: int = 3, delay: float = 0.6) -> Any:
    headers = {"User-Agent": user_agent(), "Accept": "application/json"}
    last_error: str | None = None
    for attempt in range(1, retries + 1):
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code} for {url}"
            if exc.code in {429, 500, 502, 503, 504} and attempt < retries:
                retry_after = exc.headers.get("Retry-After")
                sleep_for = float(retry_after) if retry_after and retry_after.isdigit() else delay * attempt
                time.sleep(sleep_for)
                continue
            raise RuntimeError(last_error) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                time.sleep(delay * attempt)
                continue
            raise RuntimeError(last_error) from exc
    raise RuntimeError(last_error or f"Failed to fetch {url}")


def http_text(url: str, *, retries: int = 3, delay: float = 0.6) -> str:
    headers = {"User-Agent": user_agent(), "Accept": "application/atom+xml,text/xml,*/*"}
    last_error: str | None = None
    for attempt in range(1, retries + 1):
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code} for {url}"
            if exc.code in {429, 500, 502, 503, 504} and attempt < retries:
                retry_after = exc.headers.get("Retry-After")
                sleep_for = float(retry_after) if retry_after and retry_after.isdigit() else delay * attempt
                time.sleep(sleep_for)
                continue
            raise RuntimeError(last_error) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                time.sleep(delay * attempt)
                continue
            raise RuntimeError(last_error) from exc
    raise RuntimeError(last_error or f"Failed to fetch {url}")


def clean_text(value: Any) -> str:
    if isinstance(value, list):
        value = " ".join(str(item) for item in value if item)
    if not isinstance(value, str):
        return ""
    text = html.unescape(value)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_doi(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    doi = value.strip().lower()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
    doi = doi.strip()
    return doi


def doi_url(doi: str) -> str:
    return f"https://doi.org/{doi}" if doi else ""


def date_from_parts(parts: Any) -> str:
    if not isinstance(parts, dict):
        return ""
    date_parts = parts.get("date-parts")
    if not date_parts or not isinstance(date_parts, list) or not date_parts[0]:
        return ""
    nums = date_parts[0]
    year = int(nums[0])
    month = int(nums[1]) if len(nums) > 1 else 1
    day = int(nums[2]) if len(nums) > 2 else 1
    try:
        return dt.date(year, month, day).isoformat()
    except ValueError:
        return ""


def crossref_date(item: dict[str, Any]) -> str:
    for field in ("published-print", "published-online", "published", "issued", "created"):
        value = date_from_parts(item.get(field))
        if value:
            return value
    return ""


def format_authors(authors: Any, max_authors: int = 6) -> str:
    if not isinstance(authors, list):
        return ""
    names: list[str] = []
    for author in authors[:max_authors]:
        if not isinstance(author, dict):
            continue
        given = clean_text(author.get("given"))
        family = clean_text(author.get("family"))
        literal = clean_text(author.get("name"))
        name = " ".join(part for part in [given, family] if part).strip() or literal
        if name:
            names.append(name)
    if len(authors) > max_authors:
        names.append("et al.")
    return "; ".join(names)


def inverted_abstract(index: Any) -> str:
    if not isinstance(index, dict):
        return ""
    positions: list[tuple[int, str]] = []
    for word, indexes in index.items():
        if not isinstance(indexes, list):
            continue
        for position in indexes:
            if isinstance(position, int):
                positions.append((position, word))
    positions.sort(key=lambda pair: pair[0])
    return clean_text(" ".join(word for _, word in positions))


def text_blob(*parts: str) -> str:
    return " ".join(part for part in parts if part).lower()


def keyword_hits(title: str, abstract: str, subjects: list[str]) -> tuple[list[str], int]:
    title_l = title.lower()
    abstract_l = abstract.lower()
    subjects_l = " ".join(subjects).lower()
    hits: list[str] = []
    score = 0
    for group in KEYWORD_GROUPS:
        group_hit = False
        for term in group["terms"]:
            term_l = term.lower()
            if term_l in title_l:
                score += 3
                group_hit = True
            if term_l in abstract_l:
                score += 2
                group_hit = True
            if term_l in subjects_l:
                score += 1
                group_hit = True
        if group_hit:
            hits.append(group["label"])
    return hits, score


def keyword_group_for_term(term: str) -> str:
    term_l = term.lower()
    for group in KEYWORD_GROUPS:
        if term_l == group["label"].lower() or term_l in [item.lower() for item in group["terms"]]:
            return group["label"]
    return term


def priority_for(score: int, abstract: str) -> str:
    if score >= 6 and abstract:
        return "High"
    if score >= 3:
        return "Medium"
    return "Low"


def is_excluded_title(title: str) -> bool:
    title_l = title.lower()
    return any(re.search(pattern, title_l) for pattern in EXCLUDED_TITLE_PATTERNS)


def crossref_date_filter(date_mode: str, from_date: str, until_date: str) -> str:
    if date_mode == "created-date":
        return f"from-created-date:{from_date},until-created-date:{until_date}"
    if date_mode == "index-date":
        return f"from-index-date:{from_date},until-index-date:{until_date}"
    return f"from-pub-date:{from_date},until-pub-date:{until_date}"


def crossref_query_url(member: str, term: str, from_date: str, until_date: str, rows: int, date_mode: str = "pub-date") -> str:
    params = {
        "filter": f"member:{member},type:journal-article,{crossref_date_filter(date_mode, from_date, until_date)}",
        "query.bibliographic": term,
        "rows": str(rows),
        "sort": "created" if date_mode == "created-date" else "published",
        "order": "desc",
    }
    if CROSSREF_MAILTO:
        params["mailto"] = CROSSREF_MAILTO
    return "https://api.crossref.org/works?" + urllib.parse.urlencode(params)


def arxiv_query_url(term: str, rows: int) -> str:
    quoted = f'all:"{term}"'
    params = {
        "search_query": quoted,
        "start": "0",
        "max_results": str(rows),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    return ARXIV_API + "?" + urllib.parse.urlencode(params)


def openalex_doi_url(doi: str) -> str:
    params = {"filter": f"doi:{doi}", "per-page": "1"}
    if CROSSREF_MAILTO:
        params["mailto"] = CROSSREF_MAILTO
    return "https://api.openalex.org/works?" + urllib.parse.urlencode(params)


def normalize_crossref_item(item: dict[str, Any], publisher: dict[str, Any], query_term: str) -> dict[str, Any] | None:
    title = clean_text(item.get("title"))
    doi = normalize_doi(item.get("DOI"))
    if not title or is_excluded_title(title):
        return None
    journal = clean_text(item.get("container-title"))
    abstract = clean_text(item.get("abstract"))
    subjects = [clean_text(value) for value in item.get("subject", []) if clean_text(value)]
    hits, score = keyword_hits(title, abstract, subjects)
    if not hits:
        hits = [keyword_group_for_term(query_term)]
        score = 1
    published = crossref_date(item)
    return {
        "title": title,
        "doi": doi,
        "url": doi_url(doi) or clean_text(item.get("URL")),
        "publisher": publisher["display"],
        "publisher_key": publisher["key"],
        "crossref_publisher": clean_text(item.get("publisher")) or publisher["crossref_name"],
        "journal": journal,
        "published_date": published,
        "authors": format_authors(item.get("author")),
        "abstract": abstract,
        "abstract_source": "Crossref" if abstract else "",
        "subjects": subjects,
        "keyword_hits": hits,
        "query_term": query_term,
        "metadata_match_confidence": "direct" if score > 1 else "query-only",
        "relevance_score": score,
        "priority": priority_for(score, abstract),
        "openalex_id": "",
        "openalex_url": "",
        "open_access_url": "",
        "pdf_url": "",
        "source": "Crossref",
    }


def merge_openalex(paper: dict[str, Any], openalex_work: dict[str, Any]) -> dict[str, Any]:
    paper["openalex_id"] = clean_text(openalex_work.get("id"))
    paper["openalex_url"] = clean_text(openalex_work.get("id"))
    if not paper.get("abstract"):
        abstract = inverted_abstract(openalex_work.get("abstract_inverted_index"))
        if abstract:
            paper["abstract"] = abstract
            paper["abstract_source"] = "OpenAlex"
    concepts = [
        clean_text(topic.get("display_name"))
        for topic in openalex_work.get("concepts", [])
        if isinstance(topic, dict) and clean_text(topic.get("display_name"))
    ]
    topics = [
        clean_text(topic.get("display_name"))
        for topic in openalex_work.get("topics", [])
        if isinstance(topic, dict) and clean_text(topic.get("display_name"))
    ]
    combined_subjects = list(dict.fromkeys([*paper.get("subjects", []), *concepts, *topics]))
    paper["subjects"] = combined_subjects
    primary_location = openalex_work.get("primary_location") if isinstance(openalex_work.get("primary_location"), dict) else {}
    landing = clean_text(primary_location.get("landing_page_url"))
    pdf = clean_text(primary_location.get("pdf_url"))
    if landing and not paper.get("url"):
        paper["url"] = landing
    paper["open_access_url"] = landing
    paper["pdf_url"] = pdf
    hits, score = keyword_hits(paper["title"], paper.get("abstract", ""), combined_subjects)
    if hits:
        paper["keyword_hits"] = hits
        paper["relevance_score"] = score
        paper["metadata_match_confidence"] = "direct"
    else:
        paper["keyword_hits"] = paper.get("keyword_hits", [])
        paper["relevance_score"] = paper.get("relevance_score", 1)
    paper["priority"] = priority_for(paper["relevance_score"], paper.get("abstract", ""))
    return paper


def parse_arxiv_date(value: str) -> str:
    if not value:
        return ""
    try:
        return parse_date(value).date().isoformat()
    except ValueError:
        return ""


def arxiv_id_from_url(value: str) -> str:
    value = value.strip()
    match = re.search(r"arxiv\.org/abs/([^?#]+)", value)
    if match:
        return match.group(1)
    return value.rsplit("/", 1)[-1]


def normalize_arxiv_entry(entry: ET.Element) -> dict[str, Any] | None:
    title = clean_text(entry.findtext("atom:title", default="", namespaces=ARXIV_NS))
    abstract = clean_text(entry.findtext("atom:summary", default="", namespaces=ARXIV_NS))
    published_raw = clean_text(entry.findtext("atom:published", default="", namespaces=ARXIV_NS))
    updated_raw = clean_text(entry.findtext("atom:updated", default="", namespaces=ARXIV_NS))
    entry_url = clean_text(entry.findtext("atom:id", default="", namespaces=ARXIV_NS))
    if not title or is_excluded_title(title):
        return None
    arxiv_id = arxiv_id_from_url(entry_url)
    authors = []
    for author in entry.findall("atom:author", namespaces=ARXIV_NS):
        name = clean_text(author.findtext("atom:name", default="", namespaces=ARXIV_NS))
        if name:
            authors.append(name)
    subjects = []
    for category in entry.findall("atom:category", namespaces=ARXIV_NS):
        term = clean_text(category.attrib.get("term"))
        if term:
            subjects.append(term)
    pdf_url = ""
    for link in entry.findall("atom:link", namespaces=ARXIV_NS):
        if link.attrib.get("title") == "pdf":
            pdf_url = clean_text(link.attrib.get("href"))
            break
    hits, score = keyword_hits(title, abstract, subjects)
    if not hits:
        return None
    return {
        "title": title,
        "doi": "",
        "arxiv_id": arxiv_id,
        "url": entry_url or f"https://arxiv.org/abs/{arxiv_id}",
        "publisher": "arXiv",
        "publisher_key": "arxiv",
        "crossref_publisher": "",
        "journal": "arXiv preprint",
        "published_date": parse_arxiv_date(published_raw) or parse_arxiv_date(updated_raw),
        "authors": "; ".join(authors[:6] + (["et al."] if len(authors) > 6 else [])),
        "abstract": abstract,
        "abstract_source": "arXiv",
        "subjects": subjects,
        "keyword_hits": hits,
        "relevance_score": score,
        "priority": priority_for(score, abstract),
        "openalex_id": "",
        "openalex_url": "",
        "open_access_url": entry_url,
        "pdf_url": pdf_url,
        "source": "arXiv",
        "source_type": "preprint",
    }


def fetch_arxiv_papers(args: argparse.Namespace, window_from: dt.datetime, window_until: dt.datetime, seen_keys: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    papers_by_key: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []
    for group in KEYWORD_GROUPS:
        term = group["label"]
        url = arxiv_query_url(term, args.arxiv_rows)
        try:
            xml_text = http_text(url)
            root = ET.fromstring(xml_text)
        except Exception as exc:  # noqa: BLE001
            errors.append({"source": "arXiv", "term": term, "error": str(exc)})
            continue
        for entry in root.findall("atom:entry", namespaces=ARXIV_NS):
            paper = normalize_arxiv_entry(entry)
            if not paper:
                continue
            published = paper.get("published_date")
            if published:
                published_dt = dt.datetime.fromisoformat(published).replace(tzinfo=dt.timezone.utc)
                if published_dt.date() < window_from.date() or published_dt.date() > window_until.date():
                    continue
            state_key = f"arxiv:{paper.get('arxiv_id') or paper['title'].lower()}"
            if state_key in seen_keys and not args.include_seen:
                continue
            paper["state_key"] = state_key
            existing = papers_by_key.get(state_key)
            if not existing or paper["relevance_score"] > existing["relevance_score"]:
                papers_by_key[state_key] = paper
        time.sleep(max(args.sleep, 3.1))
    return list(papers_by_key.values()), errors


def fetch_candidates(args: argparse.Namespace) -> Path:
    output_dir = Path(args.output_dir)
    state_file = Path(args.state_file)
    state = read_json(state_file, {})
    now = utc_now()
    if args.from_date:
        window_from = parse_date(args.from_date)
    elif state.get("last_success_utc"):
        window_from = parse_date(state["last_success_utc"])
    else:
        window_from = now - dt.timedelta(days=args.lookback_days)
    if args.until_date:
        window_until = parse_date(args.until_date)
    else:
        window_until = now

    from_date = date_only(window_from)
    until_date = date_only(window_until)
    seen_dois = {normalize_doi(doi) for doi in state.get("seen_dois", []) if normalize_doi(doi)}
    seen_keys = {str(item) for item in state.get("seen_items", []) if item}
    seen_keys.update(f"doi:{doi}" for doi in seen_dois)
    papers_by_key: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []

    terms = sorted({term for group in KEYWORD_GROUPS for term in group["terms"]})
    for publisher in PUBLISHERS:
        for term in terms:
            url = crossref_query_url(
                publisher["crossref_member"],
                term,
                from_date,
                until_date,
                args.rows,
                publisher.get("crossref_date_mode", "pub-date"),
            )
            try:
                payload = http_json(url)
                items = payload.get("message", {}).get("items", [])
            except Exception as exc:  # noqa: BLE001 - record partial failures for digest transparency.
                errors.append({"source": "Crossref", "publisher": publisher["display"], "term": term, "error": str(exc)})
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                paper = normalize_crossref_item(item, publisher, term)
                if not paper:
                    continue
                key = paper["doi"] or f"{paper['title'].lower()}|{paper.get('journal', '').lower()}|{paper.get('published_date', '')}"
                state_key = f"doi:{paper['doi']}" if paper["doi"] else f"title:{key}"
                if state_key in seen_keys and not args.include_seen:
                    continue
                paper["state_key"] = state_key
                existing = papers_by_key.get(key)
                if not existing or paper["relevance_score"] > existing["relevance_score"]:
                    papers_by_key[key] = paper
            time.sleep(args.sleep)

    if args.include_arxiv:
        arxiv_papers, arxiv_errors = fetch_arxiv_papers(args, window_from, window_until, seen_keys)
        errors.extend(arxiv_errors)
        for paper in arxiv_papers:
            papers_by_key[paper["state_key"]] = paper

    papers = sorted(papers_by_key.values(), key=lambda item: (item.get("priority") == "High", item.get("relevance_score", 0), item.get("published_date", "")), reverse=True)
    papers = papers[: args.max_papers]

    for paper in papers:
        doi = paper.get("doi", "")
        if not doi:
            continue
        try:
            payload = http_json(openalex_doi_url(doi), retries=2)
            results = payload.get("results", [])
            if results:
                merge_openalex(paper, results[0])
        except Exception as exc:  # noqa: BLE001
            errors.append({"source": "OpenAlex", "doi": doi, "error": str(exc)})
        time.sleep(args.sleep)

    papers = [paper for paper in papers if paper.get("keyword_hits")]
    papers.sort(key=lambda item: (item.get("priority") == "High", item.get("relevance_score", 0), item.get("published_date", "")), reverse=True)

    run_utc = window_until.astimezone(dt.timezone.utc)
    run_id = run_utc.strftime("%Y-%m-%d")
    run_stamp = run_utc.strftime("%Y-%m-%dT%H%M%SZ")
    output_path = output_dir / "data" / f"{run_stamp}.json"
    payload = {
        "run_id": run_id,
        "run_stamp": run_stamp,
        "created_utc": now.isoformat(),
        "recipient_email": RECIPIENT_EMAIL,
        "language": LANGUAGE,
        "timezone": TIMEZONE,
        "schedule_time": SCHEDULE_TIME,
        "window_from_utc": window_from.isoformat(),
        "window_until_utc": window_until.isoformat(),
        "window_from_date": from_date,
        "window_until_date": until_date,
        "keywords": KEYWORD_GROUPS,
        "publishers": [
            *PUBLISHERS,
            *([{"key": "arxiv", "display": "arXiv", "source_type": "preprint", "url": "https://arxiv.org/"}] if args.include_arxiv else []),
        ],
        "papers": papers,
        "errors": errors,
        "notes": [
            "AI interpretation must be based only on title, abstract, keywords, and metadata in this JSON.",
            "Do not infer research goals, methods, or results when abstract is missing.",
        ],
    }
    write_json(output_path, payload)
    print(str(output_path.resolve()))
    return output_path


def mark_success(args: argparse.Namespace) -> None:
    state_file = Path(args.state_file)
    data_file = Path(args.data_file)
    state = read_json(state_file, {})
    payload = read_json(data_file, {})
    seen = {normalize_doi(doi) for doi in state.get("seen_dois", []) if normalize_doi(doi)}
    seen_items = {str(item) for item in state.get("seen_items", []) if item}
    for paper in payload.get("papers", []):
        doi = normalize_doi(paper.get("doi"))
        if doi:
            seen.add(doi)
            seen_items.add(f"doi:{doi}")
        state_key = clean_text(paper.get("state_key"))
        if state_key:
            seen_items.add(state_key)
    state.update(
        {
            "last_success_utc": payload.get("window_until_utc") or utc_now().isoformat(),
            "last_run_id": payload.get("run_id"),
            "last_data_file": str(data_file.resolve()),
            "last_digest_file": str(Path(args.digest_file).resolve()) if args.digest_file else "",
            "last_email_status": args.email_status,
            "updated_utc": utc_now().isoformat(),
            "seen_dois": sorted(seen)[-2000:],
            "seen_items": sorted(seen_items)[-3000:],
        }
    )
    write_json(state_file, state)
    print(str(state_file.resolve()))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch daily literature digest candidates.")
    parser.add_argument("--config", help="Path to daily-literature-digest.config.json.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser("fetch", help="Fetch candidate papers and write JSON.")
    fetch.add_argument("--output-dir")
    fetch.add_argument("--state-file")
    fetch.add_argument("--lookback-days", type=int)
    fetch.add_argument("--from-date", help="UTC ISO timestamp or date for forced start.")
    fetch.add_argument("--until-date", help="UTC ISO timestamp or date for forced end.")
    fetch.add_argument("--rows", type=int, help="Crossref rows per publisher/keyword query.")
    fetch.add_argument("--arxiv-rows", type=int, help="arXiv rows per keyword query.")
    fetch.add_argument("--max-papers", type=int)
    fetch.add_argument("--sleep", type=float)
    fetch.add_argument("--include-arxiv", dest="include_arxiv", action="store_true", default=None)
    fetch.add_argument("--no-arxiv", dest="include_arxiv", action="store_false")
    fetch.add_argument("--include-seen", action="store_true")
    fetch.set_defaults(func=fetch_candidates)

    success = subparsers.add_parser("mark-success", help="Update state after a digest is generated.")
    success.add_argument("--state-file")
    success.add_argument("--data-file", required=True)
    success.add_argument("--digest-file", default="")
    success.add_argument("--email-status", choices=["sent", "failed", "not-configured", "skipped"], default="skipped")
    success.set_defaults(func=mark_success)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    apply_runtime_config(args)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
