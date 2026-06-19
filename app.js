/* ============================================================
   Clinical PICO Workbench v2 — app.js
   Simplified 2-step flow: Input → Results
   Auto-chains: search → AI select → summarize → final report
   ============================================================ */

const STORAGE_KEY = 'pico_workbench_v2';
const RUN_STATE_KEY = 'pico_workbench_run_v1';
const HEALTH_POLL_MS = 60000;
const JOB_POLL_MS = 2500;
const DEFAULT_SEARCH_LIMIT = 8;
const MIN_DEEP_REVIEW_ARTICLES = 4;
const APP_BASE_PATH = (() => {
  const path = window.location.pathname || '/';
  return path.endsWith('/') ? path : path.replace(/\/[^/]*$/, '/');
})();

function appUrl(path) {
  return `${APP_BASE_PATH}${String(path || '').replace(/^\/+/, '')}`;
}

function effectiveSearchLimit(value) {
  const parsed = parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : DEFAULT_SEARCH_LIMIT;
}

/* ---------- State ---------- */
const state = {
  currentStep: 1,
  question: '',
  pico: { p: '', i: '', c: '', o: '' },
  query: '',
  fromYear: '5',
  searchLimit: DEFAULT_SEARCH_LIMIT,
  articles: [],
  selectedPmids: [],
  abstractSummary: '',
  finalAnswer: '',
  totalCount: 0,
  executedQuery: '',
};

const $ = {};
let loadingStartedAt = 0;
let loadingTimer = null;
let activeRunId = null;
let activeJobId = null;
let jobPollTimer = null;
let jobPollInFlight = false;
let jobPollErrorCount = 0;

/* ============================================================
   DOM References
   ============================================================ */
function cacheElements() {
  $.question = document.getElementById('question');
  $.analyzeBtn = document.getElementById('analyze-btn');
  $.picoResult = document.getElementById('pico-result');
  $.picoP = document.getElementById('pico-p');
  $.picoI = document.getElementById('pico-i');
  $.picoC = document.getElementById('pico-c');
  $.picoO = document.getElementById('pico-o');
  $.query = document.getElementById('query');
  $.searchLimit = document.getElementById('search-limit');
  $.confirmSearchBtn = document.getElementById('confirm-search-btn');
  $.searchNotice = document.getElementById('search-notice');
  $.searchNoticeTitle = document.getElementById('search-notice-title');
  $.searchNoticeMessage = document.getElementById('search-notice-message');
  $.notice10yBtn = document.getElementById('notice-10y-btn');
  $.noticeAllBtn = document.getElementById('notice-all-btn');
  $.noticeContinueBtn = document.getElementById('notice-continue-btn');
  $.steps = document.querySelectorAll('.step');
  $.pills = document.querySelectorAll('.pill[data-years]');
  $.resultSummary = document.getElementById('result-summary');
  $.finalAnswer = document.getElementById('final-answer');
  $.finalAnswerCard = document.getElementById('final-answer-card');
  $.articlesCard = document.getElementById('articles-card');
  $.articleList = document.getElementById('article-list');
  $.pmidLinksCard = document.getElementById('pmid-links-card');
  $.pmidLinks = document.getElementById('pmid-links');
  $.retryBtn = document.getElementById('retry-btn');
  $.newQuestionBtn = document.getElementById('new-question-btn');
  $.exportBtn = document.getElementById('export-btn');
  $.loadingOverlay = document.getElementById('loading-overlay');
  $.loadingText = document.getElementById('loading-text');
  $.loadingDetail = document.getElementById('loading-detail');
  $.loadingElapsed = document.getElementById('loading-elapsed');
  $.errorPanel = document.getElementById('error-panel');
  $.errorMessage = document.getElementById('error-message');
  $.errorDismiss = document.getElementById('error-dismiss');
  $.healthIndicator = document.getElementById('health-indicator');
}

/* ============================================================
   Step Navigation
   ============================================================ */
function goToStep(n) {
  state.currentStep = n;
  $.steps.forEach(s => {
    s.classList.toggle('active', parseInt(s.dataset.step) === n);
  });
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

/* ============================================================
   Loading Overlay
   ============================================================ */
function showLoading(text) {
  startLoadingTimer();
  $.loadingText.textContent = text || '處理中，請稍候...';
  if ($.loadingDetail) $.loadingDetail.textContent = '正在準備流程';
  const bar = document.getElementById('progress-bar-fill');
  if (bar) bar.style.width = '0%';
  $.loadingOverlay.style.display = 'flex';
}

function startLoadingTimer() {
  if (loadingTimer) return;
  loadingStartedAt = Date.now();
  updateLoadingElapsed();
  loadingTimer = setInterval(updateLoadingElapsed, 1000);
}

function updateLoadingElapsed() {
  if (!$.loadingElapsed || !loadingStartedAt) return;
  const seconds = Math.max(0, Math.round((Date.now() - loadingStartedAt) / 1000));
  $.loadingElapsed.textContent = `已執行 ${seconds} 秒`;
}

function stopLoadingTimer() {
  if (loadingTimer) clearInterval(loadingTimer);
  loadingTimer = null;
  loadingStartedAt = 0;
}

function showProgress(step, total, text, detail = '') {
  startLoadingTimer();
  const pct = Math.round((step / total) * 100);
  $.loadingText.innerHTML = `<span class="progress-step">${step}/${total}</span> ${escapeHtml(text)}`;
  if ($.loadingDetail) $.loadingDetail.textContent = detail;
  const bar = document.getElementById('progress-bar-fill');
  if (bar) bar.style.width = `${pct}%`;
  $.loadingOverlay.style.display = 'flex';
}

function hideLoading() {
  stopLoadingTimer();
  $.loadingOverlay.style.display = 'none';
}

function persistRunState(data) {
  try {
    localStorage.setItem(RUN_STATE_KEY, JSON.stringify(data));
  } catch {}
}

function stopJobPolling() {
  if (jobPollTimer) clearInterval(jobPollTimer);
  jobPollTimer = null;
  jobPollInFlight = false;
  jobPollErrorCount = 0;
}

function clearRunState() {
  stopJobPolling();
  activeRunId = null;
  activeJobId = null;
  try {
    localStorage.removeItem(RUN_STATE_KEY);
  } catch {}
}

function startRunState() {
  activeRunId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  activeJobId = null;
  persistRunState({
    runId: activeRunId,
    startedAt: Date.now(),
    question: state.question,
    query: state.query,
    fromYear: state.fromYear,
    searchLimit: state.searchLimit,
    jobId: null,
    step: 0,
    loadingText: '處理中，請稍候...',
    loadingDetail: '正在準備流程',
  });
}

function updateRunState(patch = {}) {
  if (!activeRunId) return;
  let current = {};
  try {
    current = JSON.parse(localStorage.getItem(RUN_STATE_KEY) || '{}') || {};
  } catch {}
  persistRunState({
    ...current,
    ...patch,
    runId: activeRunId,
  });
}

function restoreRunStateIfNeeded() {
  let saved = null;
  try {
    saved = JSON.parse(localStorage.getItem(RUN_STATE_KEY) || 'null');
  } catch {}
  if (!saved || !saved.runId) return;
  activeRunId = saved.runId;
  activeJobId = saved.jobId || null;
  startLoadingTimer();
  $.loadingOverlay.style.display = 'flex';
  $.loadingText.innerHTML = saved.loadingText || '處理中，請稍候...';
  if ($.loadingDetail) {
    $.loadingDetail.textContent = saved.loadingDetail || '上一輪執行中，請稍候，不會自動重跑';
  }
  if ($.loadingElapsed && saved.startedAt) {
    loadingStartedAt = saved.startedAt;
    updateLoadingElapsed();
  }
  if (saved.question) state.question = saved.question;
  if (saved.query) state.query = saved.query;
  if (saved.fromYear) state.fromYear = saved.fromYear;
  if (saved.searchLimit) state.searchLimit = effectiveSearchLimit(saved.searchLimit);
  syncSearchFormFromState();
  $.picoResult.style.display = state.query ? 'block' : 'none';
  $.analyzeBtn.textContent = state.query ? '重新分析' : '分析問題';
}

/* ============================================================
   Error Handling
   ============================================================ */
function showError(msg, options = {}) {
  if (options.clearRunState !== false) {
    clearRunState();
  }
  $.errorMessage.textContent = friendlyErrorMessage(msg);
  $.errorPanel.style.display = 'flex';
}

function hideError() {
  $.errorPanel.style.display = 'none';
}

function friendlyErrorMessage(msg) {
  const text = String(msg || '').trim();
  if (!text) return '請回到上一格確認內容後再試一次。';
  if (text.includes('請輸入臨床問題')) return '先輸入一句臨床問題，再讓 AI 幫你整理查詢式。';
  if (text.includes('請填寫查詢式')) return '目前沒有可搜尋的 PubMed 查詢式。可以重新分析問題，或手動填入查詢式。';
  if (text.startsWith('分析失敗')) return 'AI 這次沒有順利完成分析。請確認 LM Studio 已連線，或稍後再按一次「分析問題」。';
  if (text.startsWith('查詢失敗')) return '這輪查詢沒有順利完成。可以先放寬年份或篇數，或稍後再試一次。';
  return text;
}

function setTimeFilterValue(value) {
  state.fromYear = value || '5';
  $.pills.forEach(pill => {
    pill.classList.toggle('active', pill.dataset.years === state.fromYear);
  });
}

function hideSearchNotice() {
  if ($.searchNotice) $.searchNotice.style.display = 'none';
}

function showSearchNotice({ totalCount, displayedCount, mode }) {
  const noResults = mode === 'zero';
  $.searchNoticeTitle.textContent = noResults ? '這輪沒有找到文獻' : `這輪只找到 ${displayedCount} 篇`;
  $.searchNoticeMessage.textContent = noResults
    ? '建議先放寬年份，或回頭簡化查詢式。通常先改成近 10 年或不限年份會最快看出是不是條件太窄。'
    : `PubMed 總數 ${totalCount} 篇，這次可分析的文獻偏少。可以放寬年份再查，或先用目前 ${displayedCount} 篇做初步判斷。`;
  $.noticeContinueBtn.style.display = noResults ? 'none' : 'flex';
  $.searchNotice.style.display = 'block';
}

/* ============================================================
   Health Check
   ============================================================ */
async function checkHealth() {
  try {
    const res = await fetch(appUrl('/api/health'));
    const data = await res.json();
    const dot = $.healthIndicator.querySelector('.health-dot');
    const text = $.healthIndicator.querySelector('.health-text');
    if (data.llm && data.llm.status === 'connected') {
      dot.className = 'health-dot connected';
      text.textContent = '已連線';
    } else {
      dot.className = 'health-dot error';
      text.textContent = 'LLM 離線';
    }
  } catch {
    const dot = $.healthIndicator.querySelector('.health-dot');
    const text = $.healthIndicator.querySelector('.health-text');
    dot.className = 'health-dot error';
    text.textContent = '離線';
  }
}

/* ============================================================
   API Helpers
   ============================================================ */
async function apiPost(url, body) {
  const res = await fetch(appUrl(url), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

async function apiGet(url) {
  const res = await fetch(appUrl(url));
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function getTimeFilter() {
  const val = state.fromYear || '5';
  const today = new Date();
  const toDate = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
  if (val === 'all') return { mode: 'all', fromYear: null, toDate };
  const years = parseInt(val, 10);
  if (isNaN(years)) return { mode: 'all', fromYear: null, toDate };
  return { mode: 'custom', fromYear: today.getFullYear() - years, toDate };
}

/* ============================================================
   Core Flow: Analyze → Search → AI Select → Summarize → Report
   ============================================================ */

function syncStateFromPipelineResult(result = {}) {
  state.question = result.question || state.question;
  state.pico = result.pico || state.pico;
  state.query = result.query || state.query;
  state.executedQuery = result.executedQuery || result.query || state.query;
  state.articles = Array.isArray(result.articles) ? result.articles : [];
  state.selectedPmids = Array.isArray(result.selectedPmids) ? result.selectedPmids : [];
  state.abstractSummary = result.abstractSummary || '';
  state.finalAnswer = result.finalAnswer || '';
  state.totalCount = result.totalCount || 0;
  state.searchLimit = effectiveSearchLimit(result.searchLimit || state.searchLimit || DEFAULT_SEARCH_LIMIT);
  if (result.timeFilter && result.timeFilter.mode === 'all') {
    state.fromYear = 'all';
  }
}

function applyCompletedResult(result = {}) {
  syncStateFromPipelineResult(result);
  syncSearchFormFromState();
  $.picoResult.style.display = 'block';
  $.analyzeBtn.textContent = '重新分析';
  hideSearchNotice();
  renderResults();
  goToStep(2);
  saveState();
}

function applyNoticeResult(result = {}) {
  syncStateFromPipelineResult(result);
  syncSearchFormFromState();
  $.picoResult.style.display = 'block';
  $.analyzeBtn.textContent = '重新分析';
  hideLoading();
  showSearchNotice({
    totalCount: state.totalCount,
    displayedCount: state.articles.length,
    mode: result.noticeMode || 'sparse',
  });
  goToStep(1);
  saveState();
}

function applyJobProgress(progress = {}, startedAt = null) {
  const step = parseInt(progress.step, 10) || 0;
  const total = parseInt(progress.total, 10) || 5;
  const text = progress.text || '處理中，請稍候...';
  const detail = progress.detail || '正在準備流程';
  if (step > 0) {
    showProgress(step, total, text, detail);
    updateRunState({
      jobId: activeJobId,
      startedAt: startedAt || Date.now(),
      step,
      loadingText: `<span class="progress-step">${step}/${total}</span> ${escapeHtml(text)}`,
      loadingDetail: detail,
    });
  } else {
    showLoading(text);
    if ($.loadingDetail) $.loadingDetail.textContent = detail;
    updateRunState({
      jobId: activeJobId,
      startedAt: startedAt || Date.now(),
      step: 0,
      loadingText: text,
      loadingDetail: detail,
    });
  }
  if ($.loadingElapsed && startedAt) {
    loadingStartedAt = startedAt;
    updateLoadingElapsed();
  }
}

function showReconnectState(err) {
  const detail = jobPollErrorCount <= 1
    ? '手機剛恢復或網路暫時中斷，正在重新連線；後端會繼續跑，不會重送。'
    : `正在重新連線（第 ${jobPollErrorCount} 次）；後端若已完成，抓到結果後會直接顯示。`;
  startLoadingTimer();
  $.loadingOverlay.style.display = 'flex';
  if ($.loadingDetail) $.loadingDetail.textContent = detail;
  updateRunState({
    jobId: activeJobId,
    loadingDetail: detail,
  });
  console.warn('job poll reconnecting', err);
}

async function pollJobStatus() {
  if (!activeJobId || jobPollInFlight) return;
  jobPollInFlight = true;
  try {
    const job = await apiGet(`/api/jobs/${activeJobId}`);
    jobPollErrorCount = 0;
    const progress = job.progress || {};
    applyJobProgress(progress, job.startedAt ? Date.parse(job.startedAt) : null);

    if (job.status === 'completed') {
      const result = job.result || {};
      clearRunState();
      hideLoading();
      if (result.kind === 'notice') {
        applyNoticeResult(result);
        return;
      }
      applyCompletedResult(result);
      return;
    }

    if (job.status === 'failed') {
      hideLoading();
      showError(`查詢失敗：${job.error || '背景工作失敗'}`);
    }
  } catch (err) {
    jobPollErrorCount += 1;
    showReconnectState(err);
  } finally {
    jobPollInFlight = false;
  }
}

function startJobPolling(jobId) {
  activeJobId = jobId;
  updateRunState({ jobId });
  stopJobPolling();
  pollJobStatus();
  jobPollTimer = setInterval(() => {
    pollJobStatus();
  }, JOB_POLL_MS);
}

// Step 1: Analyze question → show PICO
async function analyze() {
  if (activeRunId) {
    showError('上一輪流程仍在執行或結果尚未確認，請稍候，不會自動重跑。', { clearRunState: false });
    return;
  }
  const q = $.question.value.trim();
  if (!q) { showError('請輸入臨床問題'); return; }
  state.question = q;
  hideSearchNotice();

  showLoading('AI 正在分析問題...');
  try {
    const result = await apiPost('/api/openrouter/analyze', {
      question: q,
      pico: state.pico,
      query: state.query,
    });
    state.pico = result.pico || state.pico;
    state.query = result.query || '';

    // Fill form
    $.picoP.value = state.pico.p;
    $.picoI.value = state.pico.i;
    $.picoC.value = state.pico.c;
    $.picoO.value = state.pico.o;
    $.query.value = state.query;
    state.articles = [];
    state.selectedPmids = [];
    state.abstractSummary = '';
    state.finalAnswer = '';
    state.totalCount = 0;
    state.executedQuery = '';

    // Show PICO card
    $.picoResult.style.display = 'block';
    $.analyzeBtn.textContent = '重新分析';
    saveState();
    hideLoading();
  } catch (err) {
    hideLoading();
    showError(`分析失敗：${err.message}`);
  }
}

// Step 1→2: Confirm & Search → auto-chain with parallel burst
async function confirmAndSearch(options = {}) {
  if (activeRunId) {
    showError('上一輪流程仍在執行或結果尚未確認，請稍候，不會自動重跑。', { clearRunState: false });
    return;
  }
  const allowSparse = Boolean(options.allowSparse);
  // Sync from form
  state.pico.p = $.picoP.value.trim();
  state.pico.i = $.picoI.value.trim();
  state.pico.c = $.picoC.value.trim();
  state.pico.o = $.picoO.value.trim();
  state.query = $.query.value.trim();
  state.searchLimit = effectiveSearchLimit($.searchLimit.value);
  $.searchLimit.value = String(state.searchLimit);
  state.articles = [];
  state.selectedPmids = [];
  state.abstractSummary = '';
  state.finalAnswer = '';
  state.totalCount = 0;
  state.executedQuery = '';

  if (!state.query) { showError('請填寫查詢式'); return; }
  hideSearchNotice();
  startRunState();
  saveState();
  applyJobProgress({
    step: 0,
    total: 5,
    text: '建立背景工作',
    detail: '手機休眠後仍會繼續執行，醒來會自動接回進度',
  }, Date.now());
  try {
    const job = await apiPost('/api/jobs/review', {
      question: state.question,
      pico: state.pico,
      query: state.query,
      timeFilter: getTimeFilter(),
      retmax: state.searchLimit,
      allowSparse,
    });
    if (!job.jobId) {
      throw new Error('背景工作建立失敗');
    }
    startJobPolling(job.jobId);
  } catch (err) {
    hideLoading();
    showError(`查詢失敗：${err.message}`);
  }
}

/* ============================================================
   Render Results
   ============================================================ */
function renderResults() {
  // Summary line
  $.resultSummary.textContent = `共找到 ${state.totalCount} 篇，AI 選擇 ${state.selectedPmids.length} 篇`;

  // Final answer
  if (state.finalAnswer) {
    $.finalAnswer.innerHTML = formatReport(state.finalAnswer);
    $.finalAnswerCard.style.display = 'block';
  } else {
    $.finalAnswer.innerHTML = '';
    $.finalAnswerCard.style.display = 'none';
  }

  // Selected articles list
  const selected = state.articles.filter(a => state.selectedPmids.includes(a.pmid));
  if (selected.length > 0) {
    $.articleList.innerHTML = selected.map(a => `
      <div class="article-item">
        <span class="article-year">${a.year || ''}</span>
        <span class="article-title">${escapeHtml(a.title || '')}</span>
        ${a.titleZh ? `<span class="article-title-zh">${escapeHtml(a.titleZh)}</span>` : ''}
      </div>
    `).join('');
    $.articlesCard.style.display = 'block';
  } else {
    $.articleList.innerHTML = '';
    $.articlesCard.style.display = 'none';
  }

  // PMID links
  if (state.selectedPmids.length > 0) {
    $.pmidLinks.innerHTML = state.selectedPmids.map(pmid => {
      const a = state.articles.find(x => x.pmid === pmid);
      const label = a ? `${a.year || ''} ${a.journal || ''}` : pmid;
      return `<a href="https://pubmed.ncbi.nlm.nih.gov/${pmid}/" target="_blank" class="pmid-link">PMID ${pmid} (${escapeHtml(label)})</a>`;
    }).join('');
    $.pmidLinksCard.style.display = 'block';
  } else {
    $.pmidLinks.innerHTML = '';
    $.pmidLinksCard.style.display = 'none';
  }
}

function formatReport(text) {
  return escapeHtml(text)
    .replace(/\n/g, '<br>')
    .replace(/【([^】]+)】/g, '<strong>【$1】</strong>')
    .replace(/PMID\s*(\d+)/g, '<a href="https://pubmed.ncbi.nlm.nih.gov/$1/" target="_blank">PMID $1</a>');
}

function escapeHtml(text) {
  return String(text || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function syncSearchFormFromState() {
  $.question.value = state.question || $.question.value || '';
  $.picoP.value = state.pico.p || '';
  $.picoI.value = state.pico.i || '';
  $.picoC.value = state.pico.c || '';
  $.picoO.value = state.pico.o || '';
  $.query.value = state.query || '';
  state.searchLimit = effectiveSearchLimit(state.searchLimit || DEFAULT_SEARCH_LIMIT);
  $.searchLimit.value = String(state.searchLimit);
  setTimeFilterValue(state.fromYear || '5');
}

/* ============================================================
   Actions
   ============================================================ */
function retrySearch() {
  hideSearchNotice();
  syncSearchFormFromState();
  $.picoResult.style.display = 'block';
  $.analyzeBtn.textContent = '重新分析';
  goToStep(1);
}

function newQuestion() {
  clearRunState();
  state.question = '';
  state.pico = { p: '', i: '', c: '', o: '' };
  state.query = '';
  state.articles = [];
  state.selectedPmids = [];
  state.abstractSummary = '';
  state.finalAnswer = '';
  state.totalCount = 0;
  state.executedQuery = '';
  $.question.value = '';
  hideSearchNotice();
  $.picoResult.style.display = 'none';
  $.analyzeBtn.textContent = '分析問題';
  renderResults();
  goToStep(1);
  saveState();
}

function exportReport() {
  const md = buildMarkdownReport();
  const date = new Date().toISOString().slice(0, 10);
  const slug = makeFilenameSlug(state.question || 'pico-report');
  downloadTextFile(`pico-report-${date}-${slug}.md`, md, 'text/markdown;charset=utf-8');
}

function buildMarkdownReport() {
  const generatedAt = new Date().toLocaleString('zh-TW', { hour12: false });
  const selected = state.articles.filter(a => state.selectedPmids.includes(a.pmid));
  const lines = [
    '# 臨床 PICO 查詢報告',
    '',
    `產生時間：${generatedAt}`,
    '',
    '## 臨床問題',
    '',
    state.question || '未記錄',
    '',
    '## PubMed 查詢',
    '',
    `- 查詢式：\`${state.executedQuery || state.query || '未記錄'}\``,
    `- 搜尋結果總數：${state.totalCount || 0}`,
    `- 本輪納入分析：${state.selectedPmids.length}`,
    '',
    '## PICO',
    '',
    `- P：${state.pico.p || '未填'}`,
    `- I：${state.pico.i || '未填'}`,
    `- C：${state.pico.c || '未填'}`,
    `- O：${state.pico.o || '未填'}`,
    '',
    '## AI 分析報告',
    '',
    normalizeMarkdownReport(state.finalAnswer || '尚未產生報告'),
    '',
    '## 納入文獻',
    '',
  ];

  if (selected.length === 0) {
    lines.push('未記錄納入文獻。');
  } else {
    selected.forEach((article, idx) => {
      const meta = [article.year, article.journal].filter(Boolean).join(' / ');
      lines.push(`${idx + 1}. [PMID ${article.pmid}](https://pubmed.ncbi.nlm.nih.gov/${article.pmid}/)${meta ? ` - ${meta}` : ''}`);
      lines.push(`   - ${article.title || '未記錄標題'}`);
    });
  }

  lines.push('', '## PubMed 連結', '');
  if (state.selectedPmids.length === 0) {
    lines.push('無。');
  } else {
    state.selectedPmids.forEach(pmid => {
      lines.push(`- [PMID ${pmid}](https://pubmed.ncbi.nlm.nih.gov/${pmid}/)`);
    });
  }

  return `${lines.join('\n')}\n`;
}

function normalizeMarkdownReport(text) {
  return String(text || '')
    .replace(/\r\n/g, '\n')
    .replace(/【([^】]+)】/g, '### $1')
    .trim();
}

function makeFilenameSlug(text) {
  return String(text || '')
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 36) || 'report';
}

function downloadTextFile(filename, content, type) {
  // Add UTF-8 BOM so mobile file viewers are less likely to mis-detect
  // Traditional Chinese Markdown as a legacy encoding.
  const utf8Bom = new Uint8Array([0xEF, 0xBB, 0xBF]);
  const blob = new Blob([utf8Bom, content], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/* ============================================================
   Persistence
   ============================================================ */
function saveState() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      question: state.question,
      pico: state.pico,
      query: state.query,
      fromYear: state.fromYear,
      searchLimit: state.searchLimit,
      articles: state.articles,
      abstractSummary: state.abstractSummary,
      executedQuery: state.executedQuery,
      finalAnswer: state.finalAnswer,
      selectedPmids: state.selectedPmids,
      totalCount: state.totalCount,
    }));
  } catch {}
}

function loadState() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
    if (saved) {
      state.question = saved.question || '';
      state.pico = saved.pico || state.pico;
      state.query = saved.query || '';
      state.fromYear = saved.fromYear || '5';
      state.searchLimit = effectiveSearchLimit(saved.searchLimit || DEFAULT_SEARCH_LIMIT);
      state.articles = saved.articles || [];
      state.abstractSummary = saved.abstractSummary || '';
      state.executedQuery = saved.executedQuery || '';
      state.finalAnswer = saved.finalAnswer || '';
      state.selectedPmids = saved.selectedPmids || [];
      state.totalCount = saved.totalCount || 0;

      syncSearchFormFromState();
      if (state.finalAnswer) {
        // Restore last result
        $.picoResult.style.display = 'block';
        $.analyzeBtn.textContent = '重新分析';
        renderResults();
        goToStep(2);
      }
    }
  } catch {}
}

/* ============================================================
   Event Binding
   ============================================================ */
function bindEvents() {
  $.analyzeBtn.addEventListener('click', analyze);
  $.confirmSearchBtn.addEventListener('click', confirmAndSearch);
  $.retryBtn.addEventListener('click', retrySearch);
  $.newQuestionBtn.addEventListener('click', newQuestion);
  $.exportBtn.addEventListener('click', exportReport);
  $.errorDismiss.addEventListener('click', hideError);

  // Click app title to go back to step 1
  document.querySelector('.app-title').addEventListener('click', () => {
    if (state.currentStep !== 1) {
      hideSearchNotice();
      syncSearchFormFromState();
      $.picoResult.style.display = state.query ? 'block' : 'none';
      $.analyzeBtn.textContent = state.query ? '重新分析' : '分析問題';
      goToStep(1);
    }
  });
  $.notice10yBtn.addEventListener('click', () => {
    setTimeFilterValue('10');
    confirmAndSearch();
  });
  $.noticeAllBtn.addEventListener('click', () => {
    setTimeFilterValue('all');
    confirmAndSearch();
  });
  $.noticeContinueBtn.addEventListener('click', () => {
    confirmAndSearch({ allowSparse: true });
  });

  // Time filter pills
  $.pills.forEach(pill => {
    pill.addEventListener('click', () => {
      setTimeFilterValue(pill.dataset.years);
      hideSearchNotice();
    });
  });

  // Enter key on question textarea → analyze
  $.question.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (!$.picoResult.style.display || $.picoResult.style.display === 'none') {
        analyze();
      }
    }
  });

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && activeJobId) {
      pollJobStatus();
    }
  });
  window.addEventListener('pageshow', () => {
    if (activeJobId) {
      pollJobStatus();
    }
  });
  window.addEventListener('online', () => {
    if (activeJobId) {
      pollJobStatus();
    }
  });
}

/* ============================================================
   Init
   ============================================================ */
function init() {
  cacheElements();
  loadState();
  restoreRunStateIfNeeded();
  if (activeJobId) {
    startJobPolling(activeJobId);
  }
  checkHealth();
  setInterval(checkHealth, HEALTH_POLL_MS);
}

document.addEventListener('DOMContentLoaded', () => {
  init();
  bindEvents();
});
