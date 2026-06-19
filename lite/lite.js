const STORAGE_KEY = "pico_lite_settings_v1";

const state = {
  phase: "idle", // idle | busy | error
  phaseLabel: "待命",
  startedAt: 0,
  apiKey: "",
  model: "google/gemini-2.5-flash-lite",
  question: "",
  pico: { p: "", i: "", c: "", o: "" },
  query: "",
  timeFilter: {
    mode: "all",
    fromYear: null,
    toDate: new Date().toISOString().slice(0, 10)
  },
  search: {
    totalCount: 0,
    articles: []
  },
  selectedPmids: [],
  finalAnswer: ""
};

const el = {
  apiKey: document.getElementById("api-key"),
  model: document.getElementById("model"),
  btnSaveSettings: document.getElementById("btn-save-settings"),
  question: document.getElementById("question"),
  btnAnalyze: document.getElementById("btn-analyze"),
  btnRun: document.getElementById("btn-run"),
  previewCard: document.getElementById("preview-card"),
  picoP: document.getElementById("pico-p"),
  picoI: document.getElementById("pico-i"),
  picoC: document.getElementById("pico-c"),
  picoO: document.getElementById("pico-o"),
  query: document.getElementById("query"),
  statusDot: document.getElementById("status-dot"),
  statusText: document.getElementById("status-text"),
  statusElapsed: document.getElementById("status-elapsed"),
  statusNote: document.getElementById("status-note"),
  zeroCard: document.getElementById("zero-card"),
  btnReanalyze: document.getElementById("btn-reanalyze"),
  btnLoosenAnd: document.getElementById("btn-loosen-and"),
  btnRerunSearch: document.getElementById("btn-rerun-search"),
  finalCard: document.getElementById("final-card"),
  finalAnswer: document.getElementById("final-answer"),
  meta: document.getElementById("meta"),
  errorCard: document.getElementById("error-card"),
  errorText: document.getElementById("error-text")
};

function setError(message, detail = "") {
  const body = [message, detail].filter(Boolean).join("\n\n");
  el.errorText.value = body;
  el.errorCard.hidden = false;
  setStatus("error", `錯誤：${message}`);
}

function loadSettings() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    const apiKey = String(parsed?.apiKey || "").trim();
    const model = String(parsed?.model || "").trim();
    if (apiKey) {
      state.apiKey = apiKey;
      el.apiKey.value = apiKey;
    }
    if (model) {
      state.model = model;
      el.model.value = model;
    }
  } catch {
    // ignore invalid local settings
  }
}

function saveSettings() {
  syncInputsToState();
  const payload = {
    apiKey: state.apiKey || "",
    model: state.model || "google/gemini-2.5-flash-lite"
  };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
  setStatus("idle", "設定已儲存");
}

function clearError() {
  el.errorCard.hidden = true;
  el.errorText.value = "";
}

function setStatus(phase, text) {
  state.phase = phase;
  state.phaseLabel = text || "待命";
  if (phase === "busy") {
    state.startedAt = Date.now();
  }
  renderStatus();
}

function formatElapsed(ms) {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return m ? `${m}m ${String(sec).padStart(2, "0")}s` : `${sec}s`;
}

function renderStatus() {
  el.statusDot.className = "dot";
  if (state.phase === "busy") {
    el.statusDot.classList.add("busy");
    el.statusText.textContent = state.phaseLabel;
    el.statusElapsed.textContent = `已執行 ${formatElapsed(Date.now() - state.startedAt)}`;
    return;
  }
  if (state.phase === "error") {
    el.statusDot.classList.add("error");
    el.statusText.textContent = state.phaseLabel;
    el.statusElapsed.textContent = "";
    return;
  }
  el.statusDot.classList.add("idle");
  el.statusText.textContent = state.phaseLabel || "待命";
  el.statusElapsed.textContent = "";
}

async function postJson(url, payload) {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  const rawText = await resp.text();
  let data = {};
  try {
    data = rawText ? JSON.parse(rawText) : {};
  } catch {
    data = {};
  }
  if (!resp.ok) {
    const detail = data.detail || rawText.slice(0, 250);
    throw new Error(`${data.error || `API error (${resp.status})`}\n${detail || ""}`.trim());
  }
  return data;
}

function syncInputsToState() {
  state.apiKey = String(el.apiKey.value || "").trim();
  state.model = String(el.model.value || "").trim() || "google/gemini-2.5-flash-lite";
  state.question = String(el.question.value || "").trim();
  state.query = String(el.query.value || state.query || "").trim();
}

function renderPreview() {
  el.previewCard.hidden = false;
  el.picoP.textContent = state.pico.p || "-";
  el.picoI.textContent = state.pico.i || "-";
  el.picoC.textContent = state.pico.c || "-";
  el.picoO.textContent = state.pico.o || "-";
  el.query.value = state.query || "";
  el.btnRun.disabled = !state.query;
}

async function analyzeOnly() {
  syncInputsToState();
  clearError();
  el.zeroCard.hidden = true;
  el.finalCard.hidden = true;
  if (!state.question) {
    setError("請先輸入臨床問題");
    return;
  }
  if (!state.apiKey || !state.model) {
    setError("請先輸入 API Key 與模型");
    return;
  }

  setStatus("busy", "分析問題中");
  try {
    const res = await postJson("/api/openrouter/analyze", {
      apiKey: state.apiKey,
      model: state.model,
      question: state.question,
      pico: state.pico,
      query: state.query
    });
    state.pico = {
      p: String(res?.pico?.p || "").trim(),
      i: String(res?.pico?.i || "").trim(),
      c: String(res?.pico?.c || "").trim(),
      o: String(res?.pico?.o || "").trim()
    };
    state.query = String(res?.query || "").trim();
    renderPreview();
    setStatus("idle", "待命");
  } catch (err) {
    setError("分析失敗", err.message || String(err));
  }
}

function buildSearchContext(selectedCount) {
  const previewArticles = state.search.articles.slice(0, 20).map((a) => ({
    pmid: String(a.pmid || ""),
    year: a.year || "",
    journal: String(a.journal || ""),
    title: String(a.title || "")
  }));
  return {
    totalCount: Number(state.search.totalCount || 0),
    displayedCount: state.search.articles.length,
    selectedCount,
    retmax: 15,
    filters: "Top 15",
    selectedPmids: [...state.selectedPmids],
    previewArticles
  };
}

function formatRawEvidence(articles) {
  return articles
    .map(
      (item, idx) =>
        `[${idx + 1}] PMID ${item.pmid || ""}\n` +
        `Title: ${item.title || ""}\n` +
        `Journal/Year: ${item.journal || "-"} / ${item.year || "-"}\n` +
        `Abstract (original): ${(item.abstract || "").trim() || "No abstract available"}`
    )
    .join("\n\n====================\n\n");
}

function loosenAndQuery(q) {
  const parts = String(q || "").split(/\s+AND\s+/i).map((x) => x.trim()).filter(Boolean);
  if (parts.length <= 1) return String(q || "");
  return parts.slice(0, -1).join(" AND ");
}

async function searchOnly() {
  const res = await postJson("/api/pubmed/search", {
    query: state.query,
    timeFilter: state.timeFilter,
    retmax: 15
  });
  state.search.totalCount = Number(res.totalCount || 0);
  state.search.articles = Array.isArray(res.articles) ? res.articles : [];
  return res;
}

async function runAutoFlow() {
  syncInputsToState();
  clearError();
  el.zeroCard.hidden = true;
  el.finalCard.hidden = true;

  if (!state.query) {
    setError("請先分析問題，產生查詢式");
    return;
  }

  try {
    setStatus("busy", "PubMed 搜尋中");
    await searchOnly();

    if (state.search.totalCount === 0) {
      el.zeroCard.hidden = false;
      setStatus("idle", "待命（0 篇，請調整條件）");
      return;
    }

    setStatus("busy", "AI 寬鬆勾選中");
    const pickRes = await postJson("/api/openrouter/suggest-selection", {
      apiKey: state.apiKey,
      model: state.model,
      question: state.question,
      pico: state.pico,
      query: state.query,
      mode: "loose",
      articles: state.search.articles.map((a) => ({
        pmid: String(a.pmid || ""),
        title: String(a.title || ""),
        journal: String(a.journal || ""),
        year: a.year || ""
      }))
    });

    const suggestions = Array.isArray(pickRes?.suggestions) ? pickRes.suggestions : [];
    const set = new Set(
      suggestions.filter((x) => x && x.recommend).map((x) => String(x.pmid || "")).filter(Boolean)
    );
    state.selectedPmids = state.search.articles
      .map((x) => String(x.pmid || "").trim())
      .filter((pmid) => set.has(pmid));
    if (!state.selectedPmids.length) {
      state.selectedPmids = state.search.articles.slice(0, Math.min(5, state.search.articles.length)).map((x) => String(x.pmid || ""));
    }

    setStatus("busy", "抓取摘要中");
    const absRes = await postJson("/api/pubmed/abstracts", { pmids: state.selectedPmids });
    const selectedArticles = Array.isArray(absRes.articles) ? absRes.articles : [];

    const selectedMetaText = selectedArticles
      .map((item, idx) => `[${idx + 1}] PMID ${item.pmid || "-"} | ${item.journal || "-"} | ${item.year || "-"} | ${item.title || "-"}`)
      .join("\n");

    const finalReviewInput = [
      "【文獻素材索引（PMID/期刊/年份）】",
      selectedMetaText || "無",
      "【檢索統計與目前結果】",
      [
        `總篇數: ${state.search.totalCount}`,
        `目前顯示: ${state.search.articles.length}`,
        "目前上限(retmax): 15",
        `已選篇數: ${state.selectedPmids.length}`
      ].join("\n"),
      "【中文逐篇重點】",
      "（Lite 模式略過中文摘要，直接用原文摘要評讀）",
      "【原文摘要與文獻資訊】",
      formatRawEvidence(selectedArticles)
    ].join("\n\n");

    setStatus("busy", "產生最終結論中");
    const finalRes = await postJson("/api/openrouter/final-review", {
      apiKey: state.apiKey,
      model: state.model,
      question: state.question,
      pico: state.pico,
      query: state.query,
      timeFilter: state.timeFilter,
      abstractSummary: finalReviewInput,
      searchContext: buildSearchContext(state.selectedPmids.length)
    });

    state.finalAnswer = String(finalRes.answer || "").trim();
    el.finalAnswer.value = state.finalAnswer;
    el.meta.textContent = `本輪共搜到 ${state.search.totalCount} 篇，顯示前 15 篇，AI 勾選 ${state.selectedPmids.length} 篇進行最終評讀。`;
    el.finalCard.hidden = false;
    setStatus("idle", "完成");
  } catch (err) {
    setError("自動流程失敗", err.message || String(err));
  }
}

async function rerunSearchOnly() {
  syncInputsToState();
  clearError();
  setStatus("busy", "重新搜尋中");
  try {
    await searchOnly();
    if (state.search.totalCount === 0) {
      el.zeroCard.hidden = false;
      setStatus("idle", "待命（0 篇，請再調整）");
      return;
    }
    el.zeroCard.hidden = true;
    setStatus("idle", `已找到 ${state.search.totalCount} 篇，請按「同意，開始自動流程」`);
  } catch (err) {
    setError("重跑搜尋失敗", err.message || String(err));
  }
}

function bind() {
  el.btnSaveSettings.addEventListener("click", saveSettings);
  el.btnAnalyze.addEventListener("click", analyzeOnly);
  el.btnRun.addEventListener("click", runAutoFlow);

  el.btnReanalyze.addEventListener("click", () => {
    el.zeroCard.hidden = true;
    el.question.focus();
    setStatus("idle", "請修改問題後重新分析");
  });

  el.btnLoosenAnd.addEventListener("click", () => {
    const next = loosenAndQuery(el.query.value || state.query);
    state.query = next;
    el.query.value = next;
    setStatus("idle", "已移除部分 AND，請按重跑");
  });

  el.btnRerunSearch.addEventListener("click", rerunSearchOnly);

  setInterval(() => {
    if (state.phase === "busy") renderStatus();
  }, 1000);
}

bind();
loadSettings();
renderStatus();
