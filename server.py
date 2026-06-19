#!/usr/bin/env python3
import argparse
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4
from xml.etree import ElementTree as ET

BASE_DIR = Path(__file__).resolve().parent


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_env_file(BASE_DIR / ".env")

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
EUTILS_EFETCH_BATCH = 4
LLM_SUMMARY_WORKERS = max(1, min(4, int(os.environ.get("LLM_SUMMARY_WORKERS", "4"))))
NCBI_MIN_INTERVAL_NO_KEY = float(os.environ.get("NCBI_MIN_INTERVAL_NO_KEY", "0.38"))
NCBI_MIN_INTERVAL_WITH_KEY = float(os.environ.get("NCBI_MIN_INTERVAL_WITH_KEY", "0.12"))
_NCBI_RATE_LOCK = Lock()
_NCBI_LAST_REQUEST_AT = 0.0

# OpenAI-compatible LLM config. Works with LM Studio, OpenRouter, Ollama adapters, etc.
LMSTUDIO_BASE_URL = os.environ.get(
    "LLM_BASE_URL",
    os.environ.get("LMSTUDIO_BASE_URL", "http://127.0.0.1:1234/v1"),
)
LMSTUDIO_MODEL = os.environ.get("LLM_MODEL", os.environ.get("LMSTUDIO_MODEL", "local-model"))
LLM_API_KEY = os.environ.get("LLM_API_KEY", os.environ.get("OPENROUTER_API_KEY", "")).strip()
APP_PORT = int(os.environ.get("APP_PORT", "9999"))
JOB_RETENTION_SECONDS = max(300, int(os.environ.get("JOB_RETENTION_SECONDS", "7200")))
PIPELINE_TOTAL_STEPS = 5
MIN_DEEP_REVIEW_ARTICLES = 4
_JOB_LOCK = Lock()
_JOB_STORE: Dict[str, Dict[str, Any]] = {}


def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def log_event(message: str) -> None:
    print(f"[{now_iso()}] {message}", flush=True)


def today_str() -> str:
    return date.today().isoformat()


def first_year(text: str) -> Optional[int]:
    if not text:
        return None
    m = re.search(r"(18|19|20)\d{2}", text)
    return int(m.group(0)) if m else None


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def iter_text(node: Optional[ET.Element]) -> str:
    if node is None:
        return ""
    return clean_text("".join(node.itertext()))


def ncbi_common_params() -> Dict[str, str]:
    params: Dict[str, str] = {}
    api_key = os.environ.get("PUBMED_API_KEY", "").strip()
    tool = os.environ.get("PUBMED_TOOL", "clinical-pico-workbench").strip()
    email = os.environ.get("PUBMED_EMAIL", "").strip()

    if api_key:
        params["api_key"] = api_key
    if tool:
        params["tool"] = tool
    if email:
        params["email"] = email
    return params


def ncbi_min_interval() -> float:
    if os.environ.get("PUBMED_API_KEY", "").strip():
        return max(0.0, NCBI_MIN_INTERVAL_WITH_KEY)
    return max(0.0, NCBI_MIN_INTERVAL_NO_KEY)


def wait_for_ncbi_slot() -> None:
    global _NCBI_LAST_REQUEST_AT
    min_interval = ncbi_min_interval()
    with _NCBI_RATE_LOCK:
        now = time.monotonic()
        wait_s = (_NCBI_LAST_REQUEST_AT + min_interval) - now
        if wait_s > 0:
            time.sleep(wait_s)
        _NCBI_LAST_REQUEST_AT = time.monotonic()


def ncbi_get(endpoint: str, params: Dict[str, Any], timeout: int = 25) -> str:
    merged: Dict[str, Any] = {**params, **ncbi_common_params()}
    query = urlencode(merged)
    url = f"{EUTILS_BASE}/{endpoint}?{query}"
    req = Request(url, headers={"User-Agent": "ClinicalPICOWorkbench/0.1"})
    last_error: Optional[HTTPError] = None
    for attempt in range(3):
        wait_for_ncbi_slot()
        try:
            with urlopen(req, timeout=timeout) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read().decode(charset, errors="replace")
        except HTTPError as e:
            last_error = e
            if e.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise
            retry_after = e.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else 1.5 * (attempt + 1)
            except ValueError:
                delay = 1.5 * (attempt + 1)
            log_event(f"NCBI {endpoint} HTTP {e.code}; retry in {delay:.1f}s")
            time.sleep(delay)
    if last_error:
        raise last_error
    raise RuntimeError("NCBI request failed")


def parse_ncbi_json_or_raise(raw_text: str, endpoint: str) -> Dict[str, Any]:
    text = raw_text or ""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        lower = text.lower()
        blocked = (
            "misuse.ncbi.nlm.nih.gov" in lower
            or "blocked diagnostic" in lower
            or "ncbi - www error blocked diagnostic" in lower
        )
        if blocked:
            raise ValueError(
                "PubMed 暫時拒絕請求（NCBI blocked diagnostic）。請稍後再試，或降低查詢頻率。"
            )
        if "<html" in lower or "<!doctype html" in lower:
            raise ValueError(f"PubMed 回傳非 JSON（endpoint={endpoint}），請稍後重試。")
        raise ValueError(f"PubMed JSON 解析失敗（endpoint={endpoint}）。")

    if not isinstance(parsed, dict):
        raise ValueError(f"PubMed 回傳格式異常（endpoint={endpoint}）。")
    return parsed


def llm_chat(
    *,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.2,
    max_tokens: int = 2048,
    timeout: int = 180,
) -> str:
    """Unified LLM call via LM Studio (OpenAI-compatible API)."""
    url = f"{LMSTUDIO_BASE_URL}/chat/completions"
    payload = {
        "model": LMSTUDIO_MODEL,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    data = json.dumps(payload).encode("utf-8")
    req = Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "ClinicalPICOWorkbench/2.0",
            **({"Authorization": f"Bearer {LLM_API_KEY}"} if LLM_API_KEY else {}),
            **({"HTTP-Referer": "https://github.com/castlen3/clinical-pico-workbench"} if LLM_API_KEY else {}),
            **({"X-Title": "Clinical PICO Workbench"} if LLM_API_KEY else {}),
        },
    )
    with urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        raw = resp.read().decode(charset, errors="replace")

    decoded = json.loads(raw)
    choices = decoded.get("choices", [])
    if not choices:
        raise ValueError("LLM 回應缺少 choices")
    message = choices[0].get("message", {})
    content = message.get("content", "")
    if isinstance(content, list):
        parts: List[str] = []
        for chunk in content:
            if isinstance(chunk, dict) and chunk.get("type") == "text":
                parts.append(str(chunk.get("text", "")))
        content = "\n".join(parts)
    return str(content).strip()


# Legacy alias for backward compat
def openrouter_chat(*, api_key: str = "", model: str = "", **kwargs) -> str:
    return llm_chat(**kwargs)


def extract_json_object(text: str) -> Dict[str, Any]:
    raw = text.strip()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("模型回應非 JSON 物件")
    candidate = raw[start : end + 1]
    parsed = json.loads(candidate)
    if not isinstance(parsed, dict):
        raise ValueError("模型回應非 JSON 物件")
    return parsed


def split_markdown_table_row(row: str) -> List[str]:
    raw = row.strip()
    if raw.startswith("|"):
        raw = raw[1:]
    if raw.endswith("|"):
        raw = raw[:-1]
    return [clean_text(cell) for cell in raw.split("|")]


def normalize_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").lower())


def pick_field(source: Dict[str, Any], keys: List[str]) -> str:
    if not isinstance(source, dict):
        return ""
    normalized = {normalize_key(k): v for k, v in source.items()}
    for key in keys:
        value = normalized.get(normalize_key(key))
        if value is not None:
            return clean_text(str(value))
    return ""


def pick_dict(source: Dict[str, Any], keys: List[str]) -> Dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    normalized = {normalize_key(k): v for k, v in source.items()}
    for key in keys:
        value = normalized.get(normalize_key(key))
        if isinstance(value, dict):
            return value
    return {}


def normalize_no_table_output(text: str) -> str:
    if not text:
        return ""

    # 先清除常見 HTML table tag，避免小視窗閱讀困難。
    cleaned = re.sub(
        r"</?(table|thead|tbody|tr|th|td)[^>]*>",
        "",
        text,
        flags=re.IGNORECASE,
    )
    lines = cleaned.splitlines()
    out: List[str] = []
    i = 0

    # 將 Markdown 表格轉成條列句，避免在窄欄位溢出。
    while i < len(lines):
        line = lines[i]
        next_line = lines[i + 1] if i + 1 < len(lines) else ""
        is_table_sep = bool(re.match(r"^\s*\|?[\s:\-]+\|[\s:\-\|]*\|?\s*$", next_line))
        if "|" in line and is_table_sep:
            headers = split_markdown_table_row(line)
            i += 2
            row_no = 1
            while i < len(lines) and "|" in lines[i]:
                cells = split_markdown_table_row(lines[i])
                width = max(len(headers), len(cells))
                pairs: List[str] = []
                for col in range(width):
                    label = headers[col] if col < len(headers) and headers[col] else f"欄位{col + 1}"
                    value = cells[col] if col < len(cells) else ""
                    if value:
                        pairs.append(f"{label}: {value}")
                if pairs:
                    out.append(f"- 第{row_no}列：{'；'.join(pairs)}")
                row_no += 1
                i += 1
            continue

        out.append(line)
        i += 1

    # 最後再防呆：若仍有大量 pipe，轉為一般分隔符。
    normalized_lines: List[str] = []
    for line in out:
        stripped = line.strip()
        if stripped.count("|") >= 2 and "http" not in stripped.lower():
            normalized_lines.append(stripped.replace("|", " / "))
        else:
            normalized_lines.append(line)
    return "\n".join(normalized_lines)


def heuristic_pico_query(
    text: str, current_pico: Dict[str, Any], current_query: str
) -> Dict[str, Any]:
    lines = [clean_text(x) for x in text.splitlines() if clean_text(x)]
    pico = {
        "p": clean_text(str(current_pico.get("p", ""))),
        "i": clean_text(str(current_pico.get("i", ""))),
        "c": clean_text(str(current_pico.get("c", ""))),
        "o": clean_text(str(current_pico.get("o", ""))),
    }
    query = clean_text(current_query)

    patterns = {
        "p": [r"^(?:P|Population|Patient|Patients|族群|患者族群|病人)[:：\-]\s*(.+)$"],
        "i": [r"^(?:I|Intervention|Exposure|介入|暴露|治療)[:：\-]\s*(.+)$"],
        "c": [r"^(?:C|Comparison|Comparator|Control|比較|對照)[:：\-]\s*(.+)$"],
        "o": [r"^(?:O|Outcome|Outcomes|結果|指標)[:：\-]\s*(.+)$"],
    }
    query_patterns = [
        r"^(?:query|pubmed\s*query|search\s*query|search\s*string|檢索式|查詢式|搜尋式)[:：\-]\s*(.+)$",
    ]

    for line in lines:
        for k, regs in patterns.items():
            for reg in regs:
                m = re.match(reg, line, flags=re.IGNORECASE)
                if m:
                    pico[k] = clean_text(m.group(1))
        for reg in query_patterns:
            m = re.match(reg, line, flags=re.IGNORECASE)
            if m:
                query = clean_text(m.group(1))

    # 若回應中含有像 PubMed 查詢式片段，優先抓取最長一行。
    if not query:
        candidates = [ln for ln in lines if "[" in ln and ((" AND " in ln) or (" OR " in ln))]
        if candidates:
            query = max(candidates, key=len)

    return {
        "pico": pico,
        "query": query,
    }


def normalize_analyze_output(
    parsed: Dict[str, Any],
    *,
    current_pico: Dict[str, Any],
    current_query: str,
    question: str,
    raw_text: str,
) -> Dict[str, Any]:
    fallback = heuristic_pico_query(
        raw_text,
        current_pico=current_pico,
        current_query=current_query,
    )
    pico_source = pick_dict(parsed, ["pico", "PICO", "pico_terms", "picoTerms"])
    if not pico_source:
        pico_source = parsed

    fallback_pico = fallback.get("pico") if isinstance(fallback.get("pico"), dict) else {}
    pico = {
        "p": (
            pick_field(pico_source, ["p", "population", "patient", "patients", "族群", "患者族群", "病人"])
            or clean_text(str(fallback_pico.get("p", "")))
            or clean_text(str(current_pico.get("p", "")))
        ),
        "i": (
            pick_field(pico_source, ["i", "intervention", "exposure", "treatment", "介入", "暴露", "治療"])
            or clean_text(str(fallback_pico.get("i", "")))
            or clean_text(str(current_pico.get("i", "")))
        ),
        "c": (
            pick_field(pico_source, ["c", "comparison", "comparator", "control", "比較", "對照"])
            or clean_text(str(fallback_pico.get("c", "")))
            or clean_text(str(current_pico.get("c", "")))
        ),
        "o": (
            pick_field(pico_source, ["o", "outcome", "outcomes", "結果", "指標"])
            or clean_text(str(fallback_pico.get("o", "")))
            or clean_text(str(current_pico.get("o", "")))
        ),
    }
    query = (
        pick_field(parsed, ["query", "pubmed_query", "pubmedQuery", "search_query", "searchQuery", "search_string", "searchString", "檢索式", "查詢式", "搜尋式"])
        or clean_text(str(fallback.get("query", "")))
        or clean_text(current_query)
        or clean_text(question)
    )
    return {"pico": pico, "query": query}


def normalize_from_year(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        y = int(value)
    except (TypeError, ValueError):
        return None
    this_year = date.today().year
    if y < 1800:
        return 1800
    if y > this_year:
        return this_year
    return y


def normalize_to_date(value: Any) -> str:
    today = today_str()
    if not value:
        return today
    raw = str(value)
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return today
    if raw > today:
        return today
    return raw


def build_time_filter(payload_filter: Dict[str, Any]) -> Dict[str, Any]:
    from_year = normalize_from_year(payload_filter.get("fromYear"))
    to_date = normalize_to_date(payload_filter.get("toDate"))
    to_year = int(to_date[:4])
    if from_year and from_year > to_year:
        from_year = to_year

    mode = payload_filter.get("mode") or "custom"
    if from_year is None and to_date == today_str():
        mode = "all"

    return {
        "mode": mode,
        "fromYear": from_year,
        "toDate": to_date,
    }


def pubmed_search(query: str, time_filter: Dict[str, Any], retmax: int = 10) -> Dict[str, Any]:
    term = clean_text(query)
    if not term:
        raise ValueError("query 不可為空")

    if retmax < 1:
        retmax = 1
    if retmax > 50:
        retmax = 50

    tf = build_time_filter(time_filter)

    esearch_params: Dict[str, Any] = {
        "db": "pubmed",
        "retmode": "json",
        "term": term,
        "retmax": retmax,
        "sort": "relevance",
        "datetype": "pdat",
        "maxdate": tf["toDate"],
    }
    if tf["fromYear"] is not None:
        esearch_params["mindate"] = tf["fromYear"]

    esearch_text = ncbi_get("esearch.fcgi", esearch_params)
    esearch_json = parse_ncbi_json_or_raise(esearch_text, "esearch.fcgi")
    esearch_result = esearch_json.get("esearchresult", {})

    id_list: List[str] = [str(x) for x in esearch_result.get("idlist", []) if str(x).isdigit()]
    count_raw = esearch_result.get("count", 0)
    try:
        total_count = int(count_raw)
    except (TypeError, ValueError):
        total_count = 0

    articles: List[Dict[str, Any]] = []
    if id_list:
        esummary_params: Dict[str, Any] = {
            "db": "pubmed",
            "retmode": "json",
            "id": ",".join(id_list),
        }
        esummary_text = ncbi_get("esummary.fcgi", esummary_params)
        esummary_json = parse_ncbi_json_or_raise(esummary_text, "esummary.fcgi")
        summary_result = esummary_json.get("result", {})
        uids: List[str] = summary_result.get("uids", [])

        for uid in uids:
            item = summary_result.get(uid, {})
            title = clean_text(item.get("title", ""))
            journal = clean_text(item.get("fulljournalname") or item.get("source") or "")
            pubdate = clean_text(item.get("pubdate") or item.get("sortpubdate") or "")
            year = first_year(pubdate)
            articles.append(
                {
                    "pmid": str(item.get("uid") or uid),
                    "year": year,
                    "journal": journal,
                    "title": title,
                    "pubdate": pubdate,
                }
            )

    applied_filter = {
        **tf,
        "appliedAt": now_iso(),
    }
    from_label = str(tf["fromYear"]) if tf["fromYear"] is not None else "不限"
    filters = f"Top {retmax} by relevance | 時間 {from_label} -> {tf['toDate']}"

    return {
        "totalCount": total_count,
        "articles": articles,
        "filters": filters,
        "timeFilter": applied_filter,
    }


def parse_pubmed_xml(xml_text: str) -> Dict[str, Dict[str, Any]]:
    root = ET.fromstring(xml_text)
    by_pmid: Dict[str, Dict[str, Any]] = {}

    for node in root.findall(".//PubmedArticle"):
        pmid = iter_text(node.find(".//MedlineCitation/PMID"))
        if not pmid:
            continue

        title = iter_text(node.find(".//Article/ArticleTitle"))

        abstract_parts: List[str] = []
        for abs_node in node.findall(".//Article/Abstract/AbstractText"):
            text = iter_text(abs_node)
            if not text:
                continue
            label = clean_text(abs_node.attrib.get("Label") or abs_node.attrib.get("NlmCategory") or "")
            if label and not text.lower().startswith(label.lower() + ":"):
                text = f"{label}: {text}"
            abstract_parts.append(text)

        abstract = "\n".join(abstract_parts)
        journal = iter_text(node.find(".//Article/Journal/Title"))

        year_text = iter_text(node.find(".//Article/Journal/JournalIssue/PubDate/Year"))
        if not year_text:
            year_text = iter_text(node.find(".//Article/Journal/JournalIssue/PubDate/MedlineDate"))
        year = first_year(year_text)

        by_pmid[pmid] = {
            "pmid": pmid,
            "title": title,
            "journal": journal,
            "year": year,
            "abstract": abstract,
        }

    return by_pmid


def pubmed_abstracts(pmids: List[str]) -> Dict[str, Any]:
    normalized_pmids = [str(p) for p in pmids if str(p).isdigit()]
    if not normalized_pmids:
        raise ValueError("pmids 不可為空")
    parsed: Dict[str, Dict[str, Any]] = {}
    for i in range(0, len(normalized_pmids), EUTILS_EFETCH_BATCH):
        batch_pmids = normalized_pmids[i : i + EUTILS_EFETCH_BATCH]
        log_event(
            f"PubMed efetch batch {i // EUTILS_EFETCH_BATCH + 1}: pmids={len(batch_pmids)}"
        )
        efetch_params: Dict[str, Any] = {
            "db": "pubmed",
            "id": ",".join(batch_pmids),
            "retmode": "xml",
        }
        xml_text = ncbi_get("efetch.fcgi", efetch_params)
        parsed.update(parse_pubmed_xml(xml_text))

    # 依前端選擇順序回傳
    ordered: List[Dict[str, Any]] = []
    for pmid in normalized_pmids:
        ordered.append(
            parsed.get(
                pmid,
                {
                    "pmid": pmid,
                    "title": "",
                    "journal": "",
                    "year": None,
                    "abstract": "",
                },
            )
        )

    return {"articles": ordered}


def infer_study_design(text: str) -> str:
    t = (text or "").lower()
    if "meta-analysis" in t or "systematic review" in t:
        return "系統性回顧 / Meta-analysis"
    if "randomized" in t or "randomised" in t or "rct" in t:
        return "隨機對照試驗 (RCT)"
    if "cohort" in t:
        return "世代研究 (Cohort)"
    if "case-control" in t or "case control" in t:
        return "病例對照研究"
    if "cross-sectional" in t or "cross sectional" in t:
        return "橫斷研究"
    if "observational" in t:
        return "觀察性研究"
    return "未明確標示"


def brief_conclusion(abstract: str) -> str:
    text = clean_text(abstract)
    if not text:
        return "無摘要可用"
    m = re.search(r"(?:conclusion|conclusions)[:：]\s*([^\.]{20,240}[\.]?)", text, flags=re.IGNORECASE)
    if m:
        return clean_text(m.group(1))
    sentences = re.split(r"(?<=[\.\!\?])\s+", text)
    if sentences:
        return clean_text(sentences[-1])[:240]
    return text[:240]


def safe_list(value: Any, max_items: int = 2) -> List[str]:
    if isinstance(value, list):
        out = [clean_text(str(x)) for x in value if clean_text(str(x))]
        return out[:max_items]
    text = clean_text(str(value or ""))
    if not text:
        return []
    parts = [clean_text(x) for x in re.split(r"[;\n。]", text) if clean_text(x)]
    return parts[:max_items]


def normalize_pico_relevance(value: Any) -> str:
    text = clean_text(str(value or ""))
    if text in {"直接", "部分", "低"}:
        return text
    low = text.lower()
    if "direct" in low:
        return "直接"
    if "partial" in low:
        return "部分"
    if "low" in low:
        return "低"
    return "部分"


def summarize_one_article(
    *,
    question: str,
    pico: Dict[str, Any],
    article: Dict[str, Any],
) -> Dict[str, Any]:
    pmid = clean_text(str(article.get("pmid", "")))
    log_event(f"summary article start pmid={pmid}")
    title = clean_text(str(article.get("title", "")))
    journal = clean_text(str(article.get("journal", "")))
    year = article.get("year")
    abstract = clean_text(str(article.get("abstract", "")))
    base_text = f"{title} {abstract}"

    system_prompt = (
        "你是臨床文獻速讀助手。請針對單一文獻輸出 JSON，且只能輸出 JSON 物件。"
        '格式：{"title_zh":"","study_design":"","one_line_conclusion":"","key_findings":[],"limitations":[],"pico_relevance":""}'
        "其中 pico_relevance 僅能用：直接 / 部分 / 低"
    )
    user_prompt = (
        f"問題：{clean_text(question)}\n"
        f"PICO：{json.dumps(pico, ensure_ascii=False)}\n"
        f"文獻PMID：{pmid}\n"
        f"標題：{title}\n"
        f"期刊/年份：{journal} / {year}\n"
        f"摘要：{abstract}\n"
    )

    default_obj = {
        "pmid": pmid,
        "title": title,
        "title_zh": "",
        "study_design": infer_study_design(base_text),
        "one_line_conclusion": brief_conclusion(abstract),
        "key_findings": [],
        "limitations": [],
        "pico_relevance": "部分",
    }

    try:
        raw = llm_chat(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.1,
            max_tokens=700,
        )
        parsed = extract_json_object(raw)
    except Exception:
        log_event(f"summary article fallback pmid={pmid}")
        return default_obj

    result = {
        "pmid": pmid,
        "title": title,
        "title_zh": clean_text(str(parsed.get("title_zh", ""))),
        "study_design": clean_text(str(parsed.get("study_design", ""))) or default_obj["study_design"],
        "one_line_conclusion": clean_text(str(parsed.get("one_line_conclusion", ""))) or default_obj["one_line_conclusion"],
        "key_findings": safe_list(parsed.get("key_findings"), max_items=2),
        "limitations": safe_list(parsed.get("limitations"), max_items=2),
        "pico_relevance": normalize_pico_relevance(parsed.get("pico_relevance")),
    }
    log_event(f"summary article done pmid={pmid}")
    return result


def openrouter_analyze(
    *,
    question: str,
    current_pico: Dict[str, Any],
    current_query: str,
) -> Dict[str, Any]:
    question_clean = clean_text(question)
    if not question_clean:
        raise ValueError("question 不可為空")

    system_prompt = (
        "你是臨床證據檢索助手。"
        "請將臨床問題轉為 PICO 與可直接用於 PubMed 的查詢式。"
        "只輸出 JSON 物件，格式："
        '{"pico":{"p":"","i":"","c":"","o":""},"query":""}'
        "鍵名必須完全使用小寫 p、i、c、o、query，不要改成其他名稱。"
    )
    user_prompt = (
        f"臨床問題：{question_clean}\n"
        f"目前PICO參考：{json.dumps(current_pico, ensure_ascii=False)}\n"
        f"目前查詢式參考：{current_query}\n"
        "要求：\n"
        "1) PICO 以臨床可用描述。\n"
        "2) query 使用 PubMed 布林邏輯與同義詞。\n"
        "3) 不要包含時間限制（時間由外部參數控制）。\n"
    )

    raw = llm_chat(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=0.1,
        max_tokens=1000,
    )
    try:
        parsed = extract_json_object(raw)
    except Exception:
        parsed = heuristic_pico_query(raw, current_pico=current_pico, current_query=current_query)

    return normalize_analyze_output(
        parsed,
        current_pico=current_pico,
        current_query=current_query,
        question=question_clean,
        raw_text=raw,
    )


def openrouter_summarize_abstracts(
    *,
    question: str,
    pico: Dict[str, Any],
    articles: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if not articles:
        raise ValueError("articles 不可為空")
    compact_articles: List[Dict[str, Any]] = []
    for a in articles:
        compact_articles.append(
            {
                "pmid": str(a.get("pmid", "")),
                "title": clean_text(str(a.get("title", ""))),
                "journal": clean_text(str(a.get("journal", ""))),
                "year": a.get("year"),
                "abstract": clean_text(str(a.get("abstract", "")))[:3000],
            }
        )

    entries: List[Dict[str, Any]] = [None] * len(compact_articles)  # type: ignore[list-item]
    worker_count = min(LLM_SUMMARY_WORKERS, len(compact_articles))
    log_event(
        f"summarize batch start articles={len(compact_articles)} workers={worker_count}"
    )
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_map = {
            executor.submit(
                summarize_one_article,
                question=question,
                pico=pico,
                article=article,
            ): idx
            for idx, article in enumerate(compact_articles)
        }
        for future in as_completed(future_map):
            idx = future_map[future]
            try:
                entries[idx] = future.result()
            except Exception:
                article = compact_articles[idx]
                log_event(f"summary future fallback pmid={article.get('pmid', '')}")
                entries[idx] = {
                    "pmid": article.get("pmid", ""),
                    "title": article.get("title", ""),
                    "title_zh": "",
                    "study_design": infer_study_design(
                        f"{article.get('title', '')} {article.get('abstract', '')}"
                    ),
                    "one_line_conclusion": brief_conclusion(str(article.get("abstract", ""))),
                    "key_findings": [],
                    "limitations": [],
                    "pico_relevance": "部分",
                }
    log_event(f"summarize batch done articles={len(entries)}")
    return {"entries": entries}


def openrouter_final_review(
    *,
    question: str,
    pico: Dict[str, Any],
    query: str,
    time_filter: Dict[str, Any],
    abstract_summary: str,
    search_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not clean_text(abstract_summary):
        raise ValueError("abstractSummary 不可為空")

    def to_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    ctx = search_context if isinstance(search_context, dict) else {}
    total_count = to_int(ctx.get("totalCount"), 0)
    displayed_count = to_int(ctx.get("displayedCount"), 0)
    selected_count = to_int(ctx.get("selectedCount"), 0)
    retmax = to_int(ctx.get("retmax"), 10)
    filters = clean_text(str(ctx.get("filters") or ""))
    preview_articles = ctx.get("previewArticles") if isinstance(ctx.get("previewArticles"), list) else []
    preview_lines: List[str] = []
    for idx, article in enumerate(preview_articles[:20]):
        if not isinstance(article, dict):
            continue
        pmid = clean_text(str(article.get("pmid") or ""))
        year = clean_text(str(article.get("year") or ""))
        journal = clean_text(str(article.get("journal") or ""))
        title = clean_text(str(article.get("title") or ""))
        preview_lines.append(
            f"{idx + 1}. PMID {pmid or '-'} | {year or '-'} | {journal or '-'} | {title or '-'}"
        )
    preview_text = "\n".join(preview_lines) if preview_lines else "無"

    system_prompt = (
        "你是臨床證據總結助手。請用繁體中文，語氣像朋友在專業討論：短句、直接、好讀。"
        "不要使用任何稱謂（例如主任、老師、醫師）。"
        "不要官腔或公文體，不要太制式。"
        "若資料不足，直接寫「資料不足」與缺口。"
        "禁止使用任何表格格式（包含 Markdown 表格、HTML table、ASCII 表格）。"
        "一律用段落或條列呈現。"
        "輸出請固定依下列模板（標題要一致）：\n"
        "【一句話結論】\n"
        "格式必須是：關於「{問題}」這個問題，先說結論：{直球結論}\n"
        "【這輪看了多少】\n"
        "格式必須是：我們這輪選了 X 篇來評讀，其中高度相關 N 篇、部分相關 M 篇、低相關/不相關 L 篇。\n"
        "【最有用的文獻（先看這幾篇）】\n"
        "列 2-4 篇最關鍵文獻。每篇一行，必含：PMID、年份、期刊、為何關鍵（1句）。"
        "【其他文獻怎麼看】\n"
        "用短句說明哪些是部分相關、哪些不相關；可提：命題不一致、族群不符、介入不符、或 review 僅摘要無法確認內文。"
        "【可能遺珠（低相關/不相關中）】\n"
        "列 0-3 篇。每篇格式：PMID（或標題）＋目前為何列低相關/不相關＋為何仍可能有值（例如 review 摘要不足、摘要過短）＋建議是否看全文。"
        "若沒有遺珠，請寫「本輪無明確遺珠」。"
        "【整體判斷與下一步】\n"
        "1) 可否回答原問題（Yes/Partial/No）\n"
        "2) 證據強度（高/中/低/極低）與一句理由\n"
        "3) 下一步建議（2-3 點）\n"
        "【PubMed 連結】\n"
        "列出你在上文點名的 PMID 對應連結，且每條要有簡短提示是『哪篇』。"
        "格式：PMID xxxxx（年份 期刊，這篇為何被提到）: https://pubmed.ncbi.nlm.nih.gov/xxxxx/\n"
        "若沒有 PMID，請寫「無可用 PMID 連結」。"
    )
    user_prompt = (
        f"問題：{clean_text(question)}\n"
        f"PICO：{json.dumps(pico, ensure_ascii=False)}\n"
        f"查詢式：{query}\n"
        f"時間條件：{json.dumps(time_filter, ensure_ascii=False)}\n"
        f"本次檢索統計：totalCount={total_count}, displayedCount={displayed_count}, "
        f"selectedCount={selected_count}, retmax={retmax}, filters={filters}\n"
        f"目前前段結果（標題概覽）:\n{preview_text}\n"
        f"摘要整理：{abstract_summary[:18000]}\n"
        "請特別加強：\n"
        "1) 一開頭就直球回答，不要鋪陳太長。\n"
        "2) 語氣口語但專業，像同事對話，不要稱謂。\n"
        "3) 年份新近性、研究設計層級、期刊與品質要明確提到。\n"
        "4) 不可過度外推，因果推論要保守。\n"
        "5) 盡量用我們這輪實際選到的文獻來判斷，不要憑空新增研究。\n"
        "6) 『可能遺珠』只能保守建議，不可假設全文一定支持結論。\n"
    )
    answer = llm_chat(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=0.4,
        max_tokens=3500,
    )
    return {"answer": normalize_no_table_output(answer)}


def local_query_suggestions(base_query: str) -> Dict[str, Any]:
    q = clean_text(base_query)
    fallback_base = q or "(metformin[Title/Abstract]) AND (cancer[Title/Abstract])"
    query_a = fallback_base
    query_b = (
        f"({fallback_base}) AND "
        "(systematic review[Publication Type] OR meta-analysis[Title/Abstract] OR randomized[Title/Abstract])"
    )
    query_c = (
        f"({fallback_base}) AND "
        "(cohort[Title/Abstract] OR case-control[Title/Abstract] OR adjusted[Title/Abstract])"
    )
    return {
        "queryA": query_a,
        "queryB": query_b,
        "queryC": query_c,
        "recommended": "B",
        "reasoning": "先用平衡版確認是否可回答，再依篇數與品質決定擴搜或縮搜。",
        "whenToUse": {
            "A": "需要提高召回率、避免漏掉關鍵文獻時。",
            "B": "要兼顧召回與精準度，作為預設第一輪優化。",
            "C": "文獻太多、希望快速聚焦高品質觀察性/調整分析時。",
        },
        "source": "fallback",
    }


def openrouter_query_optimizer(
    *,
    question: str,
    pico: Dict[str, Any],
    query: str,
    time_filter: Dict[str, Any],
    search_context: Optional[Dict[str, Any]],
    abstract_summary: str,
    final_report: str,
) -> Dict[str, Any]:
    fallback = local_query_suggestions(query)
    ctx = search_context if isinstance(search_context, dict) else {}

    def to_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    total_count = to_int(ctx.get("totalCount"), 0)
    displayed_count = to_int(ctx.get("displayedCount"), 0)
    selected_count = to_int(ctx.get("selectedCount"), 0)
    retmax = to_int(ctx.get("retmax"), 10)
    filters = clean_text(str(ctx.get("filters") or ""))
    preview_articles = ctx.get("previewArticles") if isinstance(ctx.get("previewArticles"), list) else []
    preview_lines: List[str] = []
    for idx, article in enumerate(preview_articles[:20]):
        if not isinstance(article, dict):
            continue
        pmid = clean_text(str(article.get("pmid") or ""))
        year = clean_text(str(article.get("year") or ""))
        journal = clean_text(str(article.get("journal") or ""))
        title = clean_text(str(article.get("title") or ""))
        preview_lines.append(f"{idx + 1}. PMID {pmid or '-'} | {year or '-'} | {journal or '-'} | {title or '-'}")
    preview_text = "\n".join(preview_lines) if preview_lines else "無"

    system_prompt = (
        "你是 PubMed 檢索策略優化助手。任務是基於目前檢索結果與最終報告，輸出下一輪查詢式。"
        "只輸出 JSON 物件，不要其他文字。格式固定："
        '{"queryA":"","queryB":"","queryC":"","recommended":"A|B|C","reasoning":"",'
        '"whenToUse":{"A":"","B":"","C":""}}'
    )
    user_prompt = (
        f"問題：{clean_text(question)}\n"
        f"PICO：{json.dumps(pico, ensure_ascii=False)}\n"
        f"目前查詢式：{clean_text(query)}\n"
        f"時間條件：{json.dumps(time_filter, ensure_ascii=False)}\n"
        f"檢索統計：total={total_count}, displayed={displayed_count}, selected={selected_count}, retmax={retmax}, filters={filters}\n"
        f"前段結果（標題概覽）:\n{preview_text}\n"
        f"最終報告（重點）:\n{clean_text(final_report)[:9000]}\n"
        f"摘要整理（重點）:\n{clean_text(abstract_summary)[:9000]}\n"
        "請給三種策略：A高召回、B平衡、C高精準；並指出先跑哪一個。"
    )
    try:
        raw = llm_chat(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.1,
            max_tokens=1800,
        )
        parsed = extract_json_object(raw)
    except Exception:
        return {"suggestions": fallback, "source": "fallback(error)"}

    query_a = clean_text(str(parsed.get("queryA") or parsed.get("query_a") or fallback["queryA"]))
    query_b = clean_text(str(parsed.get("queryB") or parsed.get("query_b") or fallback["queryB"]))
    query_c = clean_text(str(parsed.get("queryC") or parsed.get("query_c") or fallback["queryC"]))
    rec = clean_text(str(parsed.get("recommended") or "B")).upper()
    if rec not in {"A", "B", "C"}:
        rec = "B"
    when = parsed.get("whenToUse") if isinstance(parsed.get("whenToUse"), dict) else {}
    suggestions = {
        "queryA": query_a or fallback["queryA"],
        "queryB": query_b or fallback["queryB"],
        "queryC": query_c or fallback["queryC"],
        "recommended": rec,
        "reasoning": clean_text(str(parsed.get("reasoning") or fallback["reasoning"])),
        "whenToUse": {
            "A": clean_text(str(when.get("A") or fallback["whenToUse"]["A"])),
            "B": clean_text(str(when.get("B") or fallback["whenToUse"]["B"])),
            "C": clean_text(str(when.get("C") or fallback["whenToUse"]["C"])),
        },
        "source": "ai",
    }
    return {"suggestions": suggestions, "source": "ai"}


def tokenize_terms(text: str) -> List[str]:
    raw = re.split(r"[^a-zA-Z0-9]+", (text or "").lower())
    stop = {
        "and",
        "or",
        "the",
        "for",
        "with",
        "from",
        "that",
        "this",
        "these",
        "those",
        "title",
        "abstract",
        "mesh",
        "terms",
        "adult",
        "adults",
    }
    out: List[str] = []
    for token in raw:
        if len(token) < 3:
            continue
        if token in stop:
            continue
        out.append(token)
    return out


def suggest_selection_heuristic(
    *,
    question: str,
    pico: Dict[str, Any],
    query: str,
    articles: List[Dict[str, Any]],
    loose: bool = False,
) -> List[Dict[str, Any]]:
    q_terms = tokenize_terms(question)
    pico_i_terms = tokenize_terms(str(pico.get("i", "")))
    pico_o_terms = tokenize_terms(str(pico.get("o", "")))
    query_terms = tokenize_terms(query)
    generic_terms = list(dict.fromkeys(q_terms + query_terms))

    suggestions: List[Dict[str, Any]] = []
    for article in articles:
        pmid = clean_text(str(article.get("pmid", "")))
        title = clean_text(str(article.get("title", "")))
        journal = clean_text(str(article.get("journal", "")))
        hay = f"{title} {journal}".lower()

        score = 0
        hits: List[str] = []
        for t in pico_i_terms:
            if t in hay:
                score += 2
                hits.append(t)
        for t in pico_o_terms:
            if t in hay:
                score += 2
                hits.append(t)
        for t in generic_terms:
            if t in hay and t not in hits:
                score += 1
                hits.append(t)

        # 寬鬆模式：只排除大概率無關，因此門檻放低。
        recommend = score >= (1 if loose else 2)
        conf = min(0.95, 0.35 + score * 0.12)
        reason = f"關鍵詞命中: {', '.join(hits[:4])}" if hits else "與問題詞彙關聯較低"
        suggestions.append(
            {
                "pmid": pmid,
                "recommend": recommend,
                "reason": reason,
                "confidence": round(conf, 2),
            }
        )
    return suggestions


def openrouter_translate_title(*, title: str) -> Dict[str, Any]:
    source_title = clean_text(title)
    if not source_title:
        raise ValueError("title 不可為空")

    system_prompt = (
        "你是醫學論文標題翻譯助手。請把英文醫學標題翻成繁體中文，精簡且忠於原意。"
        "只輸出 JSON 物件，格式：{\"title_zh\":\"\"}"
    )
    user_prompt = f"請翻譯以下標題為繁體中文：\n{source_title}"
    try:
        raw = llm_chat(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.0,
            max_tokens=200,
        )
        parsed = extract_json_object(raw)
        zh = clean_text(str(parsed.get("title_zh", "")))
        return {"titleZh": zh}
    except Exception:
        return {"titleZh": ""}


def openrouter_suggest_selection(
    *,
    question: str,
    pico: Dict[str, Any],
    query: str,
    articles: List[Dict[str, Any]],
    mode: str = "loose",
) -> Dict[str, Any]:
    if not articles:
        raise ValueError("articles 不可為空")

    compact_articles: List[Dict[str, Any]] = []
    for a in articles:
        compact_articles.append(
            {
                "pmid": clean_text(str(a.get("pmid", ""))),
                "title": clean_text(str(a.get("title", ""))),
                "journal": clean_text(str(a.get("journal", ""))),
                "year": a.get("year"),
            }
        )

    mode_text = clean_text(mode).lower()
    loose_mode = mode_text in {"loose", "broad", "lenient", "寬鬆", "寬鬆模式"}

    heuristic = suggest_selection_heuristic(
        question=question,
        pico=pico,
        query=query,
        articles=compact_articles,
        loose=loose_mode,
    )
    system_prompt = (
        "快速判斷每篇文獻是否與臨床問題相關。只看標題。"
        "輸出 JSON：{\"s\":[{\"p\":\"PMID\",\"r\":true,\"c\":0.8}]}"
        " r=true表示建議納入。每篇都要有。不要解釋。"
    )
    mode_hint = "寬鬆" if loose_mode else "一般"
    titles_only = []
    for a in compact_articles:
        titles_only.append(f"{a.get('pmid','')}: {a.get('title','')}")
    user_prompt = (
        f"問題：{clean_text(question)}\n"
        f"模式：{mode_hint}\n"
        f"文獻標題：\n" + "\n".join(titles_only)
    )

    try:
        raw = llm_chat(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.1,
            max_tokens=600,
        )
        parsed = extract_json_object(raw)
        # Support both short format {"s":[{"p":"PMID","r":true,"c":0.8}]}
        # and legacy format {"suggestions":[{"pmid":"","recommend":true,...}]}
        ai_suggestions_raw = parsed.get("s") or parsed.get("suggestions")
        ai_map: Dict[str, Dict[str, Any]] = {}
        if isinstance(ai_suggestions_raw, list):
            for item in ai_suggestions_raw:
                if not isinstance(item, dict):
                    continue
                pmid = clean_text(str(item.get("p") or item.get("pmid", "")))
                if not pmid:
                    continue
                recommend = bool(item.get("r") if "r" in item else item.get("recommend"))
                conf_raw = item.get("c") if "c" in item else item.get("confidence", 0.5)
                try:
                    conf = float(conf_raw)
                except Exception:
                    conf = 0.5
                conf = max(0.0, min(1.0, conf))
                ai_map[pmid] = {
                    "pmid": pmid,
                    "recommend": recommend,
                    "reason": "",
                    "confidence": round(conf, 2),
                }

        merged: List[Dict[str, Any]] = []
        for h in heuristic:
            pmid = h.get("pmid", "")
            ai_item = ai_map.get(pmid)
            if ai_item:
                recommend = bool(ai_item.get("recommend"))
                # 寬鬆模式再放寬一點：若模型不勾，但信心偏中等且 heuristic 有分，仍保留。
                if loose_mode and (not recommend):
                    try:
                        ai_conf = float(ai_item.get("confidence", 0.0))
                    except Exception:
                        ai_conf = 0.0
                    recommend = ai_conf >= 0.45 and bool(h.get("recommend"))
                merged.append(
                    {
                        "pmid": pmid,
                        "recommend": recommend,
                        "reason": ai_item.get("reason") or h.get("reason", ""),
                        "confidence": ai_item.get("confidence", h.get("confidence", 0.5)),
                    }
                )
            else:
                merged.append(h)
        return {"suggestions": merged, "source": "ai"}
    except Exception:
        return {"suggestions": heuristic, "source": "heuristic"}


def apply_abstracts_to_articles(
    articles: List[Dict[str, Any]], abstract_result: Dict[str, Any]
) -> List[Dict[str, Any]]:
    abs_map: Dict[str, Dict[str, Any]] = {}
    for article in abstract_result.get("articles", []) or []:
        if not isinstance(article, dict):
            continue
        pmid = clean_text(str(article.get("pmid", "")))
        if pmid:
            abs_map[pmid] = article

    merged: List[Dict[str, Any]] = []
    for article in articles:
        pmid = clean_text(str(article.get("pmid", "")))
        abstract = ""
        if pmid and pmid in abs_map:
            abstract = clean_text(str(abs_map[pmid].get("abstract", "")))
        merged.append(
            {
                **article,
                "abstract": abstract or clean_text(str(article.get("abstract", ""))),
            }
        )
    return merged


def compute_selected_pmids(
    *,
    question: str,
    pico: Dict[str, Any],
    query: str,
    articles: List[Dict[str, Any]],
) -> List[str]:
    selected_pmids: List[str] = []
    select_result = openrouter_suggest_selection(
        question=question,
        pico=pico,
        query=query,
        articles=articles,
        mode="loose",
    )
    suggestions = select_result.get("suggestions") or []
    if isinstance(suggestions, list):
        for item in suggestions:
            if not isinstance(item, dict):
                continue
            if item.get("recommend") or item.get("r"):
                pmid = clean_text(str(item.get("pmid") or item.get("p") or ""))
                if pmid:
                    selected_pmids.append(pmid)
    if not selected_pmids:
        selected_pmids = [clean_text(str(a.get("pmid", ""))) for a in articles]

    deduped: List[str] = []
    for pmid in selected_pmids:
        if pmid and pmid not in deduped:
            deduped.append(pmid)
    return deduped


def pipeline_notice_result(
    *,
    notice_mode: str,
    question: str,
    pico: Dict[str, Any],
    query: str,
    time_filter: Dict[str, Any],
    search_limit: int,
    total_count: int,
    articles: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "kind": "notice",
        "noticeMode": notice_mode,
        "question": question,
        "pico": pico,
        "query": query,
        "timeFilter": build_time_filter(time_filter),
        "searchLimit": search_limit,
        "totalCount": total_count,
        "articles": articles,
        "selectedPmids": [],
        "abstractSummary": "",
        "finalAnswer": "",
        "executedQuery": query,
    }


def pipeline_final_result(
    *,
    question: str,
    pico: Dict[str, Any],
    query: str,
    time_filter: Dict[str, Any],
    search_limit: int,
    total_count: int,
    articles: List[Dict[str, Any]],
    selected_pmids: List[str],
    abstract_summary: str,
    final_answer: str,
) -> Dict[str, Any]:
    return {
        "kind": "final",
        "question": question,
        "pico": pico,
        "query": query,
        "timeFilter": build_time_filter(time_filter),
        "searchLimit": search_limit,
        "totalCount": total_count,
        "articles": articles,
        "selectedPmids": selected_pmids,
        "abstractSummary": abstract_summary,
        "finalAnswer": final_answer,
        "executedQuery": query,
    }


def prune_jobs() -> None:
    now = time.time()
    to_delete: List[str] = []
    with _JOB_LOCK:
        for job_id, job in _JOB_STORE.items():
            updated_at = float(job.get("_updated_ts") or 0.0)
            if updated_at and now - updated_at > JOB_RETENTION_SECONDS:
                to_delete.append(job_id)
        for job_id in to_delete:
            _JOB_STORE.pop(job_id, None)


def snapshot_job(job: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "jobId": job.get("jobId", ""),
        "status": job.get("status", "unknown"),
        "createdAt": job.get("createdAt"),
        "startedAt": job.get("startedAt"),
        "updatedAt": job.get("updatedAt"),
        "finishedAt": job.get("finishedAt"),
        "progress": dict(job.get("progress") or {}),
        "result": job.get("result"),
        "error": job.get("error"),
    }


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    prune_jobs()
    with _JOB_LOCK:
        job = _JOB_STORE.get(job_id)
        if not job:
            return None
        return snapshot_job(job)


def _update_job(job_id: str, **fields: Any) -> None:
    now = now_iso()
    with _JOB_LOCK:
        job = _JOB_STORE.get(job_id)
        if not job:
            return
        job.update(fields)
        job["updatedAt"] = now
        job["_updated_ts"] = time.time()


def set_job_progress(job_id: str, *, step: int, text: str, detail: str) -> None:
    _update_job(
        job_id,
        status="running",
        progress={
            "step": step,
            "total": PIPELINE_TOTAL_STEPS,
            "text": text,
            "detail": detail,
        },
    )


def complete_job(job_id: str, result: Dict[str, Any]) -> None:
    _update_job(
        job_id,
        status="completed",
        finishedAt=now_iso(),
        result=result,
        error="",
    )


def fail_job(job_id: str, message: str) -> None:
    _update_job(
        job_id,
        status="failed",
        finishedAt=now_iso(),
        error=clean_text(message) or "未知錯誤",
    )


def run_review_pipeline(
    *,
    question: str,
    pico: Dict[str, Any],
    query: str,
    time_filter: Dict[str, Any],
    search_limit: int,
    allow_sparse: bool,
    job_id: str,
) -> Dict[str, Any]:
    set_job_progress(
        job_id,
        step=1,
        text="搜尋 PubMed",
        detail=f"用目前查詢式抓前 {search_limit} 篇標題與期刊資訊",
    )
    search_result = pubmed_search(
        query=query,
        time_filter=time_filter,
        retmax=search_limit,
    )
    articles = search_result.get("articles") or []
    if not isinstance(articles, list):
        articles = []
    total_count = int(search_result.get("totalCount") or 0)

    if not articles:
        return pipeline_notice_result(
            notice_mode="zero",
            question=question,
            pico=pico,
            query=query,
            time_filter=time_filter,
            search_limit=search_limit,
            total_count=total_count,
            articles=[],
        )

    if len(articles) < MIN_DEEP_REVIEW_ARTICLES and not allow_sparse:
        return pipeline_notice_result(
            notice_mode="sparse",
            question=question,
            pico=pico,
            query=query,
            time_filter=time_filter,
            search_limit=search_limit,
            total_count=total_count,
            articles=articles,
        )

    set_job_progress(
        job_id,
        step=2,
        text="AI 標題篩選",
        detail=f"只看 {len(articles)} 篇標題，先挑出最值得深讀的文獻",
    )
    selected_pmids = compute_selected_pmids(
        question=question,
        pico=pico,
        query=query,
        articles=articles,
    )
    set_job_progress(
        job_id,
        step=3,
        text="抓取入選摘要",
        detail=f"抓 {len(selected_pmids)} 篇摘要；標題初篩保留幾篇，就深讀幾篇",
    )
    abstract_result = pubmed_abstracts(pmids=selected_pmids)
    merged_articles = apply_abstracts_to_articles(articles, abstract_result)

    set_job_progress(
        job_id,
        step=4,
        text="逐篇深讀",
        detail=f"後端最多 4 線程並發分析 {len(selected_pmids)} 篇文獻",
    )
    selected_articles = [a for a in merged_articles if str(a.get("pmid", "")) in selected_pmids]
    summary_result = openrouter_summarize_abstracts(
        question=question,
        pico=pico,
        articles=selected_articles,
    )
    abstract_summary = json.dumps(summary_result.get("entries") or [], ensure_ascii=False)

    set_job_progress(
        job_id,
        step=5,
        text="產生最終結論",
        detail="整合 PICO、入選摘要與檢索概況，輸出手機可讀報告",
    )
    review_result = openrouter_final_review(
        question=question,
        pico=pico,
        query=query,
        time_filter=time_filter,
        abstract_summary=abstract_summary,
        search_context={
            "totalCount": total_count,
            "displayedCount": len(merged_articles),
            "selectedCount": len(selected_pmids),
            "retmax": search_limit,
            "filters": f"Top {search_limit}",
            "previewArticles": [
                {
                    "pmid": a.get("pmid"),
                    "year": a.get("year"),
                    "journal": a.get("journal"),
                    "title": a.get("title"),
                }
                for a in merged_articles[:search_limit]
            ],
        },
    )

    return pipeline_final_result(
        question=question,
        pico=pico,
        query=query,
        time_filter=time_filter,
        search_limit=search_limit,
        total_count=total_count,
        articles=merged_articles,
        selected_pmids=selected_pmids,
        abstract_summary=abstract_summary,
        final_answer=str(review_result.get("answer") or ""),
    )


def run_review_job(job_id: str, payload: Dict[str, Any]) -> None:
    try:
        question = str(payload.get("question") or "")
        pico = payload.get("pico") or {}
        query = str(payload.get("query") or "")
        time_filter = payload.get("timeFilter") or {}
        try:
            search_limit = int(payload.get("retmax") or 10)
        except (TypeError, ValueError):
            search_limit = 10
        allow_sparse = bool(payload.get("allowSparse"))
        result = run_review_pipeline(
            question=question,
            pico=pico,
            query=query,
            time_filter=time_filter,
            search_limit=search_limit,
            allow_sparse=allow_sparse,
            job_id=job_id,
        )
        complete_job(job_id, result)
        log_event(f"review job completed job_id={job_id} kind={result.get('kind', '')}")
    except Exception as e:
        fail_job(job_id, str(e))
        log_event(f"review job failed job_id={job_id} error={clean_text(str(e))}")


def create_review_job(payload: Dict[str, Any]) -> Dict[str, Any]:
    prune_jobs()
    job_id = uuid4().hex
    created_at = now_iso()
    job = {
        "jobId": job_id,
        "status": "queued",
        "createdAt": created_at,
        "startedAt": created_at,
        "updatedAt": created_at,
        "finishedAt": None,
        "progress": {
            "step": 0,
            "total": PIPELINE_TOTAL_STEPS,
            "text": "處理中，請稍候...",
            "detail": "正在準備流程",
        },
        "result": None,
        "error": "",
        "_updated_ts": time.time(),
    }
    with _JOB_LOCK:
        _JOB_STORE[job_id] = job

    worker = Thread(
        target=run_review_job,
        args=(job_id, dict(payload)),
        daemon=True,
    )
    worker.start()
    log_event(f"review job created job_id={job_id}")
    return snapshot_job(job)


class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def _request_path(self) -> str:
        return urlparse(self.path).path

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length > 0 else b"{}"
        if not body:
            return {}
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("無效的 JSON")

    def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_error(self, status: int, message: str, detail: Optional[str] = None) -> None:
        payload = {"error": message}
        if detail:
            payload["detail"] = detail
        self._send_json(status, payload)

    @staticmethod
    def _extract_upstream_detail(raw_detail: str) -> str:
        txt = clean_text(raw_detail)
        if not txt:
            return ""
        try:
            parsed = json.loads(raw_detail)
        except json.JSONDecodeError:
            return txt[:500]

        if isinstance(parsed, dict):
            err = parsed.get("error")
            if isinstance(err, dict):
                msg = clean_text(str(err.get("message") or err.get("code") or ""))
                if msg:
                    return msg[:500]
            if isinstance(err, str):
                return clean_text(err)[:500]
            msg = clean_text(str(parsed.get("message") or ""))
            if msg:
                return msg[:500]
        return txt[:500]

    def do_POST(self) -> None:  # noqa: N802
        try:
            payload = self._read_json()
        except ValueError as e:
            self._send_error(400, str(e))
            return

        try:
            request_path = self._request_path()

            if request_path == "/api/jobs/review":
                job = create_review_job(payload)
                self._send_json(202, job)
                return

            if request_path == "/api/pubmed/search":
                query = str(payload.get("query") or "")
                time_filter = payload.get("timeFilter") or {}
                try:
                    retmax = int(payload.get("retmax") or 10)
                except (TypeError, ValueError):
                    retmax = 10
                result = pubmed_search(query=query, time_filter=time_filter, retmax=retmax)
                self._send_json(200, result)
                return

            if request_path == "/api/pubmed/abstracts":
                pmids = payload.get("pmids") or []
                if not isinstance(pmids, list):
                    raise ValueError("pmids 必須是陣列")
                result = pubmed_abstracts(pmids=pmids)
                self._send_json(200, result)
                return

            if request_path == "/api/openrouter/analyze":
                result = openrouter_analyze(
                    question=str(payload.get("question") or ""),
                    current_pico=payload.get("pico") or {},
                    current_query=str(payload.get("query") or ""),
                )
                self._send_json(200, result)
                return

            if request_path == "/api/openrouter/summarize-abstracts":
                articles = payload.get("articles") or []
                if not isinstance(articles, list):
                    raise ValueError("articles 必須是陣列")
                result = openrouter_summarize_abstracts(
                    question=str(payload.get("question") or ""),
                    pico=payload.get("pico") or {},
                    articles=articles,
                )
                self._send_json(200, result)
                return

            if request_path == "/api/openrouter/final-review":
                raw_search_context = payload.get("searchContext") or {}
                if not isinstance(raw_search_context, dict):
                    raw_search_context = {}
                result = openrouter_final_review(
                    question=str(payload.get("question") or ""),
                    pico=payload.get("pico") or {},
                    query=str(payload.get("query") or ""),
                    time_filter=payload.get("timeFilter") or {},
                    abstract_summary=str(payload.get("abstractSummary") or ""),
                    search_context=raw_search_context,
                )
                self._send_json(200, result)
                return

            if request_path == "/api/openrouter/query-optimizer":
                raw_search_context = payload.get("searchContext") or {}
                if not isinstance(raw_search_context, dict):
                    raw_search_context = {}
                result = openrouter_query_optimizer(
                    question=str(payload.get("question") or ""),
                    pico=payload.get("pico") or {},
                    query=str(payload.get("query") or ""),
                    time_filter=payload.get("timeFilter") or {},
                    search_context=raw_search_context,
                    abstract_summary=str(payload.get("abstractSummary") or ""),
                    final_report=str(payload.get("finalReport") or ""),
                )
                self._send_json(200, result)
                return

            if request_path == "/api/openrouter/suggest-selection":
                articles = payload.get("articles") or []
                if not isinstance(articles, list):
                    raise ValueError("articles 必須是陣列")
                result = openrouter_suggest_selection(
                    question=str(payload.get("question") or ""),
                    pico=payload.get("pico") or {},
                    query=str(payload.get("query") or ""),
                    articles=articles,
                    mode=str(payload.get("mode") or "loose"),
                )
                self._send_json(200, result)
                return

            if request_path == "/api/openrouter/translate-title":
                result = openrouter_translate_title(
                    title=str(payload.get("title") or ""),
                )
                self._send_json(200, result)
                return

            self._send_error(404, "找不到 API 路徑")

        except ValueError as e:
            self._send_error(400, str(e))
        except HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="replace")
            except Exception:
                detail = ""
            extracted = self._extract_upstream_detail(detail)
            self._send_error(502, "上游 API 回應錯誤", extracted or f"HTTP {e.code}")
        except URLError as e:
            self._send_error(502, "無法連線到上游 API", str(e.reason))
        except Exception as e:
            self._send_error(500, "伺服器內部錯誤", str(e))

    def do_GET(self) -> None:  # noqa: N802
        request_path = self._request_path()

        if request_path.startswith("/api/jobs/"):
            job_id = clean_text(request_path.rsplit("/", 1)[-1])
            job = get_job(job_id)
            if not job:
                self._send_error(404, "找不到 job")
                return
            self._send_json(200, job)
            return

        if request_path == "/api/health":
            lm_status = "unknown"
            lm_model = LMSTUDIO_MODEL
            try:
                req = Request(
                    f"{LMSTUDIO_BASE_URL}/models",
                    headers={"User-Agent": "ClinicalPICOWorkbench/2.0"},
                )
                with urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    models = data.get("data", [])
                    if models:
                        lm_model = models[0].get("id", LMSTUDIO_MODEL)
                    lm_status = "connected"
            except Exception:
                lm_status = "unreachable"

            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "clinical-pico-workbench",
                    "version": "2.0",
                    "time": now_iso(),
                    "llm": {
                        "base_url": LMSTUDIO_BASE_URL,
                        "model": lm_model,
                        "status": lm_status,
                    },
                },
            )
            return
        super().do_GET()


def main() -> None:
    parser = argparse.ArgumentParser(description="Clinical PICO Workbench Server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=APP_PORT)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), AppHandler)
    print(f"Serving on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
