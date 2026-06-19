const state = {
  phase: "idle",
  phaseLabel: "待命",
  startedAt: 0,
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
  zeroCard: document.getElementById("zero-card"),
  btnReanalyze: document.getElementById("btn-reanalyze"),
  btnLoosenAnd: document.getElementById("btn-loosen-and"),
  btnRerunSearch: document.getElementById("btn-rerun-search"),
  finalCard: document.getElementById("final-card"),
  finalAnswer: document.getElementById("final-answer"),
  meta: document.getElementById("meta"),
  errorCard: document.getElementById("error-card"),
  errorText: document.getElementById("error-text"),
  quotaBadge: document.getElementById("quota-badge"),
  limitDialog: document.getElementById("limit-dialog"),
  limitText: document.getElementById("limit-text"),
  btnCloseLimit: document.getElementById("btn-close-limit")
};

class ApiError extends Error {
  constructor(message, status = 0, detail = "") {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

function setError(message, detail = "") {
  const body = [message, detail].filter(Boolean).join("\n\n");
  el.errorText.value = body;
  el.errorCard.hidden = false;
  setStatus("error", `錯誤：${message}`);
}

function clearError() {
  el.errorCard.hidden = true;
  el.errorText.value = "";
}

function setStatus(phase, text) {
  state.phase = phase;
  state.phaseLabel = text || "待命";
  if (phase === "busy") state.startedAt = Date.now();
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
    const message = data.error || `API error (${resp.status})`;
    const detail = String(data.detail || rawText || "").slice(0, 400);
    throw new ApiError(message, resp.status, detail);
  }

  return data;
}

async function getJson(url) {
  const resp = await fetch(url);
  const rawText = await resp.text();
  let data = {};
  try {
    data = rawText ? JSON.parse(rawText) : {};
  } catch {
    data = {};
  }
  if (!resp.ok) {
    throw new ApiError(data.error || `API error (${resp.status})`, resp.status, String(data.detail || ""));
  }
  return data;
}

function syncInputsToState() {
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

function showLimitDialog(text) {
  el.limitText.textContent = text || "今日試用次數已達上限，請明天再試。";
  if (typeof el.limitDialog.showModal === "function") {
    el.limitDialog.showModal();
  } else {
    alert(el.limitText.textContent);
  }
}

function closeLimitDialog() {
  if (el.limitDialog.open) {
    el.limitDialog.close();
  }
}

function setQuotaBadge(quota) {
  if (!quota || typeof quota !== "object") {
    el.quotaBadge.textContent = "額度資訊暫時不可用";
    return;
  }
  if (quota.localBypass) {
    el.quotaBadge.textContent = "本機測試模式：不計次";
    return;
  }
  const used = Number(quota.used || 0);
  const limit = Number(quota.limit || 10);
  const remaining = Number(quota.remaining || 0);
  el.quotaBadge.textContent = `今日額度：${used}/${limit}（剩餘 ${remaining} 次）`;
}

async function refreshQuota() {
  try {
    const quota = await getJson("/api/demo/quota");
    setQuotaBadge(quota);
  } catch {
    el.quotaBadge.textContent = "額度資訊暫時不可用";
  }
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

  setStatus("busy", "分析問題中");
  try {
    const res = await postJson("/api/demo/analyze", {
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
    if (err instanceof ApiError && err.status === 429) {
      showLimitDialog(err.detail || "今日試用次數已達上限，請明天再試。");
    } else {
      setError("分析失敗", err.message || String(err));
    }
    setStatus("idle", "待命");
  }
}

function loosenAndQuery(q) {
  const parts = String(q || "")
    .split(/\s+AND\s+/i)
    .map((x) => x.trim())
    .filter(Boolean);
  if (parts.length <= 1) return String(q || "");
  return parts.slice(0, -1).join(" AND ");
}

async function rerunSearchOnly() {
  syncInputsToState();
  clearError();
  if (!state.query) {
    setError("請先分析問題，產生查詢式");
    return;
  }

  setStatus("busy", "重新搜尋中");
  try {
    const res = await postJson("/api/demo/search", {
      query: state.query,
      timeFilter: state.timeFilter
    });
    state.search.totalCount = Number(res.totalCount || 0);
    state.search.articles = Array.isArray(res.articles) ? res.articles : [];

    if (state.search.totalCount === 0) {
      el.zeroCard.hidden = false;
      setStatus("idle", "待命（0 篇，請再調整）");
      return;
    }

    el.zeroCard.hidden = true;
    setStatus("idle", `已找到 ${state.search.totalCount} 篇，請按「同意，開始自動流程」`);
  } catch (err) {
    setError("重跑搜尋失敗", err.message || String(err));
    setStatus("idle", "待命");
  }
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

  setStatus("busy", "執行自動流程中");
  try {
    const res = await postJson("/api/demo/run", {
      question: state.question,
      pico: state.pico,
      query: state.query,
      timeFilter: state.timeFilter
    });

    if (res.status === "zero") {
      state.search.totalCount = Number(res?.search?.totalCount || 0);
      state.search.articles = Array.isArray(res?.search?.articles) ? res.search.articles : [];
      el.zeroCard.hidden = false;
      setStatus("idle", "待命（0 篇，請調整查詢式）");
      return;
    }
    if (res.status === "zero_selected") {
      state.search.totalCount = Number(res?.search?.totalCount || 0);
      state.search.articles = Array.isArray(res?.search?.articles) ? res.search.articles : [];
      state.selectedPmids = [];
      el.zeroCard.hidden = false;
      setStatus("idle", "待命（勾選結果為 0 篇，請調整查詢式）");
      return;
    }

    state.search.totalCount = Number(res?.search?.totalCount || 0);
    state.search.articles = Array.isArray(res?.search?.articles) ? res.search.articles : [];
    state.selectedPmids = Array.isArray(res?.selectedPmids) ? res.selectedPmids : [];
    state.finalAnswer = String(res?.answer || "").trim();

    el.finalAnswer.value = state.finalAnswer;
    el.meta.textContent =
      `本輪共搜到 ${state.search.totalCount} 篇，顯示前 10 篇，` +
      `AI 勾選 ${state.selectedPmids.length} 篇進行最終評讀。`;
    el.finalCard.hidden = false;

    if (res.quota) {
      setQuotaBadge(res.quota);
    } else {
      await refreshQuota();
    }

    setStatus("idle", "完成");
  } catch (err) {
    if (err instanceof ApiError && err.status === 429) {
      showLimitDialog(err.detail || "今日試用次數已達上限，請明天再試。");
      await refreshQuota();
      setStatus("idle", "待命");
      return;
    }
    setError("自動流程失敗", err.message || String(err));
    setStatus("idle", "待命");
  }
}

function bind() {
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
  el.btnCloseLimit.addEventListener("click", closeLimitDialog);

  setInterval(() => {
    if (state.phase === "busy") renderStatus();
  }, 1000);
}

bind();
renderStatus();
refreshQuota();
