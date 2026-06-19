#!/usr/bin/env python3
import argparse
import json
import os
import threading
from datetime import date, datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError

from server import (
    clean_text,
    normalize_no_table_output,
    openrouter_analyze,
    openrouter_chat,
    openrouter_suggest_selection,
    pubmed_abstracts,
    pubmed_search,
)

BASE_DIR = Path(__file__).resolve().parent
DEMO_DIR = BASE_DIR / "lite-demo"
USAGE_FILE = BASE_DIR / ".lite_demo_usage.json"
USAGE_LOCK = threading.Lock()

DEMO_RETMAX = 10
DEMO_DAILY_RUN_LIMIT = int(os.environ.get("DEMO_DAILY_RUN_LIMIT", "10"))
DEMO_MAX_TOKENS_FINAL = int(os.environ.get("DEMO_MAX_TOKENS_FINAL", "5200"))


def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def today_iso() -> str:
    return date.today().isoformat()


def tomorrow_iso() -> str:
    return (date.today() + timedelta(days=1)).isoformat()


def get_demo_api_key() -> str:
    return clean_text(os.environ.get("DEMO_OPENROUTER_API_KEY", ""))


def get_demo_model() -> str:
    return clean_text(os.environ.get("DEMO_OPENROUTER_MODEL", "google/gemini-2.5-flash-lite"))


def ensure_demo_key_ready() -> None:
    if not get_demo_api_key():
        raise ValueError("Demo API key 未設定，請由 start_lite_demo.command 啟動")


def clamp_question(question: str) -> str:
    return clean_text(question)[:800]


def clamp_query(query: str) -> str:
    return clean_text(query)[:1800]


def is_local_ip(ip: str) -> bool:
    normalized = clean_text(ip).lower()
    return normalized in {"127.0.0.1", "::1", "localhost"}


def load_usage_data() -> Dict[str, Dict[str, int]]:
    if not USAGE_FILE.exists():
        return {}
    try:
        raw = USAGE_FILE.read_text(encoding="utf-8")
        parsed = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(parsed, dict):
        return {}

    normalized: Dict[str, Dict[str, int]] = {}
    for day, item in parsed.items():
        if not isinstance(day, str) or not isinstance(item, dict):
            continue
        day_map: Dict[str, int] = {}
        for ip, count in item.items():
            ip_key = clean_text(str(ip))
            try:
                n = int(count)
            except Exception:
                continue
            if not ip_key or n < 0:
                continue
            day_map[ip_key] = n
        if day_map:
            normalized[day] = day_map
    return normalized


def save_usage_data(data: Dict[str, Dict[str, int]]) -> None:
    tmp = USAGE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(USAGE_FILE)


def prune_usage(data: Dict[str, Dict[str, int]], keep_days: int = 14) -> Dict[str, Dict[str, int]]:
    keep: Dict[str, Dict[str, int]] = {}
    cutoff = date.today() - timedelta(days=keep_days)
    for day, ip_map in data.items():
        try:
            d = datetime.strptime(day, "%Y-%m-%d").date()
        except Exception:
            continue
        if d >= cutoff:
            keep[day] = ip_map
    return keep


def get_quota(ip: str) -> Dict[str, Any]:
    if is_local_ip(ip):
        return {
            "localBypass": True,
            "limit": None,
            "used": 0,
            "remaining": None,
            "blocked": False,
            "resetAt": None,
            "day": today_iso(),
        }

    day = today_iso()
    with USAGE_LOCK:
        data = prune_usage(load_usage_data())
        used = int(data.get(day, {}).get(ip, 0))
    remaining = max(0, DEMO_DAILY_RUN_LIMIT - used)
    blocked = used >= DEMO_DAILY_RUN_LIMIT
    return {
        "localBypass": False,
        "limit": DEMO_DAILY_RUN_LIMIT,
        "used": used,
        "remaining": remaining,
        "blocked": blocked,
        "resetAt": tomorrow_iso(),
        "day": day,
    }


def consume_quota(ip: str) -> Dict[str, Any]:
    if is_local_ip(ip):
        return {
            "allowed": True,
            "localBypass": True,
            "limit": None,
            "used": 0,
            "remaining": None,
            "resetAt": None,
            "day": today_iso(),
        }

    day = today_iso()
    with USAGE_LOCK:
        data = prune_usage(load_usage_data())
        day_map = data.setdefault(day, {})
        used = int(day_map.get(ip, 0))
        if used >= DEMO_DAILY_RUN_LIMIT:
            save_usage_data(data)
            return {
                "allowed": False,
                "localBypass": False,
                "limit": DEMO_DAILY_RUN_LIMIT,
                "used": used,
                "remaining": 0,
                "resetAt": tomorrow_iso(),
                "day": day,
            }

        used += 1
        day_map[ip] = used
        data[day] = day_map
        save_usage_data(data)

    return {
        "allowed": True,
        "localBypass": False,
        "limit": DEMO_DAILY_RUN_LIMIT,
        "used": used,
        "remaining": max(0, DEMO_DAILY_RUN_LIMIT - used),
        "resetAt": tomorrow_iso(),
        "day": day,
    }


def format_raw_evidence(articles: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for idx, item in enumerate(articles):
        abstract = clean_text(str(item.get("abstract") or ""))[:2200]
        block = (
            f"[{idx + 1}] PMID {clean_text(str(item.get('pmid') or ''))}\n"
            f"Title: {clean_text(str(item.get('title') or ''))}\n"
            f"Journal/Year: {clean_text(str(item.get('journal') or '-'))} / {clean_text(str(item.get('year') or '-'))}\n"
            f"Abstract (original): {abstract or 'No abstract available'}"
        )
        parts.append(block)
    return "\n\n====================\n\n".join(parts)


def build_search_context(search_articles: List[Dict[str, Any]], total_count: int, selected_count: int) -> Dict[str, Any]:
    preview = []
    for a in search_articles[:DEMO_RETMAX]:
        preview.append(
            {
                "pmid": clean_text(str(a.get("pmid") or "")),
                "year": a.get("year") or "",
                "journal": clean_text(str(a.get("journal") or "")),
                "title": clean_text(str(a.get("title") or "")),
            }
        )
    return {
        "totalCount": int(total_count or 0),
        "displayedCount": len(search_articles),
        "selectedCount": int(selected_count or 0),
        "retmax": DEMO_RETMAX,
        "filters": f"Top {DEMO_RETMAX}",
        "previewArticles": preview,
    }


def openrouter_final_review_demo(
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

    ctx = search_context if isinstance(search_context, dict) else {}

    def to_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    total_count = to_int(ctx.get("totalCount"), 0)
    displayed_count = to_int(ctx.get("displayedCount"), 0)
    selected_count = to_int(ctx.get("selectedCount"), 0)
    retmax = to_int(ctx.get("retmax"), DEMO_RETMAX)
    filters = clean_text(str(ctx.get("filters") or ""))

    preview_articles = ctx.get("previewArticles") if isinstance(ctx.get("previewArticles"), list) else []
    preview_lines: List[str] = []
    for idx, article in enumerate(preview_articles[:DEMO_RETMAX]):
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
        "你是臨床證據總結助手。請用繁體中文，短句、直接、好讀。"
        "禁止使用任何表格格式。"
        "輸出請固定依下列模板：\n"
        "【一句話結論】\n"
        "【這輪看了多少】\n"
        "【最有用的文獻（先看這幾篇）】\n"
        "【其他文獻怎麼看】\n"
        "【可能遺珠（低相關/不相關中）】\n"
        "【整體判斷與下一步】\n"
        "【PubMed 連結】\n"
        "這一節每行格式固定為：PMID xxxxx（年份, 期刊）: https://pubmed.ncbi.nlm.nih.gov/xxxxx/\n"
        "不要加提示語或附註。"
    )
    user_prompt = (
        f"問題：{clean_text(question)}\n"
        f"PICO：{json.dumps(pico, ensure_ascii=False)}\n"
        f"查詢式：{clean_text(query)}\n"
        f"時間條件：{json.dumps(time_filter, ensure_ascii=False)}\n"
        f"本次檢索統計：totalCount={total_count}, displayedCount={displayed_count}, "
        f"selectedCount={selected_count}, retmax={retmax}, filters={filters}\n"
        f"目前前段結果（標題概覽）:\n{preview_text}\n"
        f"摘要整理：{clean_text(abstract_summary)[:12000]}\n"
        "請直接回答，不要官腔，不要憑空新增研究。"
    )

    answer = openrouter_chat(
        api_key=get_demo_api_key(),
        model=get_demo_model(),
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=0.1,
        max_tokens=DEMO_MAX_TOKENS_FINAL,
    )
    return {"answer": normalize_no_table_output(answer)}


class DemoHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(DEMO_DIR), **kwargs)

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

    def _client_ip(self) -> str:
        cf_ip = clean_text(self.headers.get("CF-Connecting-IP", ""))
        if cf_ip:
            return cf_ip.split(",")[0].strip()
        fwd = clean_text(self.headers.get("X-Forwarded-For", ""))
        if fwd:
            return fwd.split(",")[0].strip()
        if self.client_address and self.client_address[0]:
            return clean_text(self.client_address[0])
        return "unknown"

    def do_POST(self) -> None:  # noqa: N802
        try:
            payload = self._read_json()
        except ValueError as e:
            self._send_error(400, str(e))
            return

        try:
            if self.path == "/api/demo/analyze":
                ensure_demo_key_ready()
                question = clamp_question(str(payload.get("question") or ""))
                if not question:
                    raise ValueError("question 不可為空")
                result = openrouter_analyze(
                    api_key=get_demo_api_key(),
                    model=get_demo_model(),
                    question=question,
                    current_pico=payload.get("pico") or {},
                    current_query=clamp_query(str(payload.get("query") or "")),
                )
                self._send_json(200, result)
                return

            if self.path == "/api/demo/search":
                query = clamp_query(str(payload.get("query") or ""))
                if not query:
                    raise ValueError("query 不可為空")
                time_filter = payload.get("timeFilter") or {}
                result = pubmed_search(query=query, time_filter=time_filter, retmax=DEMO_RETMAX)
                result["articles"] = (result.get("articles") or [])[:DEMO_RETMAX]
                self._send_json(200, result)
                return

            if self.path == "/api/demo/run":
                ensure_demo_key_ready()
                question = clamp_question(str(payload.get("question") or ""))
                query = clamp_query(str(payload.get("query") or ""))
                pico = payload.get("pico") or {}
                time_filter = payload.get("timeFilter") or {}

                if not question:
                    raise ValueError("question 不可為空")
                if not query:
                    raise ValueError("query 不可為空")

                search_result = pubmed_search(query=query, time_filter=time_filter, retmax=DEMO_RETMAX)
                articles = (search_result.get("articles") or [])[:DEMO_RETMAX]
                total_count = int(search_result.get("totalCount") or 0)

                if total_count == 0:
                    self._send_json(
                        200,
                        {
                            "status": "zero",
                            "search": {
                                "totalCount": total_count,
                                "articles": articles,
                            },
                        },
                    )
                    return

                ip = self._client_ip()
                quota_before = get_quota(ip)
                if quota_before.get("blocked"):
                    msg = (
                        f"今日試用次數已達上限（{quota_before['limit']} 次），"
                        f"請於 {quota_before['resetAt']} 後再試。"
                    )
                    self._send_error(429, "試用額度已用完", msg)
                    return

                pick_res = openrouter_suggest_selection(
                    api_key=get_demo_api_key(),
                    model=get_demo_model(),
                    question=question,
                    pico=pico,
                    query=query,
                    mode="loose",
                    articles=[
                        {
                            "pmid": clean_text(str(a.get("pmid") or "")),
                            "title": clean_text(str(a.get("title") or "")),
                            "journal": clean_text(str(a.get("journal") or "")),
                            "year": a.get("year") or "",
                        }
                        for a in articles
                    ],
                )

                suggestions = pick_res.get("suggestions") if isinstance(pick_res.get("suggestions"), list) else []
                recommended = {
                    clean_text(str(item.get("pmid") or ""))
                    for item in suggestions
                    if isinstance(item, dict) and bool(item.get("recommend"))
                }

                selected_pmids = [
                    clean_text(str(a.get("pmid") or ""))
                    for a in articles
                    if clean_text(str(a.get("pmid") or "")) in recommended
                ]
                if not selected_pmids:
                    self._send_json(
                        200,
                        {
                            "status": "zero_selected",
                            "search": {
                                "totalCount": total_count,
                                "articles": articles,
                            },
                            "selectedPmids": [],
                        },
                    )
                    return

                abs_res = pubmed_abstracts(pmids=selected_pmids)
                selected_articles = abs_res.get("articles") if isinstance(abs_res.get("articles"), list) else []
                selected_meta_text = "\n".join(
                    [
                        f"[{idx + 1}] PMID {clean_text(str(item.get('pmid') or '-'))} | "
                        f"{clean_text(str(item.get('journal') or '-'))} | "
                        f"{clean_text(str(item.get('year') or '-'))} | "
                        f"{clean_text(str(item.get('title') or '-'))}"
                        for idx, item in enumerate(selected_articles)
                    ]
                )

                final_input = [
                    "【文獻素材索引（PMID/期刊/年份）】",
                    selected_meta_text or "無",
                    "【檢索統計與目前結果】",
                    "\n".join(
                        [
                            f"總篇數: {total_count}",
                            f"目前顯示: {len(articles)}",
                            f"目前上限(retmax): {DEMO_RETMAX}",
                            f"已選篇數: {len(selected_pmids)}",
                        ]
                    ),
                    "【中文逐篇重點】",
                    "（Demo 模式略過中文摘要，直接用原文摘要評讀）",
                    "【原文摘要與文獻資訊】",
                    format_raw_evidence(selected_articles),
                ]
                final_input_text = "\n\n".join(final_input)[:14000]

                final_res = openrouter_final_review_demo(
                    question=question,
                    pico=pico,
                    query=query,
                    time_filter=time_filter,
                    abstract_summary=final_input_text,
                    search_context=build_search_context(articles, total_count, len(selected_pmids)),
                )
                quota_after = consume_quota(ip)
                if not quota_after.get("allowed"):
                    quota_after = get_quota(ip)

                self._send_json(
                    200,
                    {
                        "status": "ok",
                        "answer": str(final_res.get("answer") or "").strip(),
                        "search": {
                            "totalCount": total_count,
                            "articles": articles,
                        },
                        "selectedPmids": selected_pmids,
                        "quota": {
                            "localBypass": quota_after.get("localBypass", False),
                            "limit": quota_after.get("limit"),
                            "used": quota_after.get("used"),
                            "remaining": quota_after.get("remaining"),
                            "resetAt": quota_after.get("resetAt"),
                        },
                    },
                )
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
        if self.path == "/api/health":
            key_ready = bool(get_demo_api_key())
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "clinical-pico-lite-demo",
                    "time": now_iso(),
                    "keyReady": key_ready,
                    "model": get_demo_model(),
                },
            )
            return

        if self.path == "/api/demo/quota":
            quota = get_quota(self._client_ip())
            self._send_json(200, quota)
            return

        super().do_GET()


def main() -> None:
    if not DEMO_DIR.exists():
        raise SystemExit(f"找不到 demo 靜態目錄: {DEMO_DIR}")

    parser = argparse.ArgumentParser(description="Clinical PICO Lite Demo Server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8790)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), DemoHandler)
    print(f"Demo serving on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
