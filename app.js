/* ============================================================
   Clinical PICO Workbench v2 — app.js
   Simplified 2-step flow: Input → Results
   Auto-chains: search → AI select → summarize → final report
   ============================================================ */

const STORAGE_KEY = 'pico_workbench_v2';
const HEALTH_POLL_MS = 60000;
const DEFAULT_SEARCH_LIMIT = 8;
const MIN_DEEP_REVIEW_ARTICLES = 4;
const MAX_DEEP_REVIEW_ARTICLES = 8;

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

/* ============================================================
   Error Handling
   ============================================================ */
function showError(msg) {
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
    const res = await fetch('/api/health');
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
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
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

// Step 1: Analyze question → show PICO
async function analyze() {
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

    // Show PICO card
    $.picoResult.style.display = 'block';
    $.analyzeBtn.textContent = '重新分析';
    hideLoading();
  } catch (err) {
    hideLoading();
    showError(`分析失敗：${err.message}`);
  }
}

// Step 1→2: Confirm & Search → auto-chain with parallel burst
async function confirmAndSearch(options = {}) {
  const allowSparse = Boolean(options.allowSparse);
  // Sync from form
  state.pico.p = $.picoP.value.trim();
  state.pico.i = $.picoI.value.trim();
  state.pico.c = $.picoC.value.trim();
  state.pico.o = $.picoO.value.trim();
  state.query = $.query.value.trim();
  state.searchLimit = parseInt($.searchLimit.value) || DEFAULT_SEARCH_LIMIT;

  if (!state.query) { showError('請填寫查詢式'); return; }
  hideSearchNotice();

  const TOTAL = 5;
  showProgress(1, TOTAL, '搜尋 PubMed', `用目前查詢式抓前 ${state.searchLimit} 篇標題與期刊資訊`);
  try {
    // === Step 1: Search PubMed ===
    const searchResult = await apiPost('/api/pubmed/search', {
      query: state.query,
      timeFilter: getTimeFilter(),
      retmax: state.searchLimit,
    });
    state.articles = searchResult.articles || [];
    state.totalCount = searchResult.totalCount || 0;
    state.executedQuery = state.query;

    if (state.articles.length === 0) {
      hideLoading();
      showSearchNotice({
        totalCount: state.totalCount,
        displayedCount: 0,
        mode: 'zero',
      });
      $.picoResult.style.display = 'block';
      goToStep(1);
      saveState();
      return;
    }

    if (state.articles.length < MIN_DEEP_REVIEW_ARTICLES && !allowSparse) {
      hideLoading();
      showSearchNotice({
        totalCount: state.totalCount,
        displayedCount: state.articles.length,
        mode: 'sparse',
      });
      $.picoResult.style.display = 'block';
      goToStep(1);
      saveState();
      return;
    }

    // === Step 2: AI select by titles only ===
    showProgress(2, TOTAL, 'AI 標題篩選', `只看 ${state.articles.length} 篇標題，先挑出最值得深讀的文獻`);
    const selectResult = await Promise.allSettled([
      apiPost('/api/openrouter/suggest-selection', {
        question: state.question,
        pico: state.pico,
        query: state.query,
        articles: state.articles,
        mode: 'loose',
      }),
    ]);

    // Process AI selection
    let selectedPmids = [];
    if (selectResult[0].status === 'fulfilled') {
      const suggestions = selectResult[0].value.suggestions || [];
      if (Array.isArray(suggestions)) {
        selectedPmids = suggestions
          .filter(s => s.recommend || s.r)
          .map(s => s.pmid || s.p);
      }
    }
    if (selectedPmids.length === 0) {
      selectedPmids = state.articles.slice(0, 3).map(a => a.pmid);
    }
    selectedPmids = selectedPmids
      .filter(Boolean)
      .filter((pmid, idx, arr) => arr.indexOf(pmid) === idx)
      .slice(0, MAX_DEEP_REVIEW_ARTICLES);
    for (const article of state.articles) {
      if (selectedPmids.length >= Math.min(MIN_DEEP_REVIEW_ARTICLES, state.articles.length)) break;
      if (article.pmid && !selectedPmids.includes(article.pmid)) {
        selectedPmids.push(article.pmid);
      }
    }
    state.selectedPmids = selectedPmids;

    // === Step 3: Fetch abstracts only for selected articles ===
    showProgress(3, TOTAL, '抓取入選摘要', `抓 ${selectedPmids.length} 篇摘要；AI 勾選不足 4 篇時會用 PubMed 排名前段補足`);
    const abstractResult = await apiPost('/api/pubmed/abstracts', {
      pmids: selectedPmids,
    });
    if (abstractResult) {
      const absMap = {};
      (abstractResult.articles || []).forEach(a => { absMap[a.pmid] = a; });
      state.articles = state.articles.map(a => ({
        ...a,
        abstract: absMap[a.pmid]?.abstract || a.abstract || '',
      }));
    }

    // === Step 4: Summarize selected articles ===
    showProgress(4, TOTAL, '逐篇深讀', `後端最多 4 線程並發分析 ${selectedPmids.length} 篇文獻`);
    const selectedArticles = state.articles.filter(a => selectedPmids.includes(a.pmid));
    const summaryResult = await apiPost('/api/openrouter/summarize-abstracts', {
      question: state.question,
      pico: state.pico,
      articles: selectedArticles,
    });
    state.abstractSummary = JSON.stringify(summaryResult.entries || []);

    // === Step 5: Final review ===
    showProgress(5, TOTAL, '產生最終結論', '整合 PICO、入選摘要與檢索概況，輸出手機可讀報告');
    const reviewResult = await apiPost('/api/openrouter/final-review', {
      question: state.question,
      pico: state.pico,
      query: state.query,
      timeFilter: getTimeFilter(),
      abstractSummary: state.abstractSummary,
      searchContext: {
        totalCount: state.totalCount,
        displayedCount: state.articles.length,
        selectedCount: selectedPmids.length,
        retmax: state.searchLimit,
        filters: `Top ${state.searchLimit}`,
        previewArticles: state.articles.slice(0, state.searchLimit).map(a => ({
          pmid: a.pmid, year: a.year, journal: a.journal, title: a.title,
        })),
      },
    });
    state.finalAnswer = reviewResult.answer || '';

    // Done!
    hideLoading();
    renderResults();
    goToStep(2);
    saveState();

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
  }

  // PMID links
  if (state.selectedPmids.length > 0) {
    $.pmidLinks.innerHTML = state.selectedPmids.map(pmid => {
      const a = state.articles.find(x => x.pmid === pmid);
      const label = a ? `${a.year || ''} ${a.journal || ''}` : pmid;
      return `<a href="https://pubmed.ncbi.nlm.nih.gov/${pmid}/" target="_blank" class="pmid-link">PMID ${pmid} (${escapeHtml(label)})</a>`;
    }).join('');
    $.pmidLinksCard.style.display = 'block';
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
  $.searchLimit.value = String(state.searchLimit || DEFAULT_SEARCH_LIMIT);
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
  state.question = '';
  state.pico = { p: '', i: '', c: '', o: '' };
  state.query = '';
  state.articles = [];
  state.selectedPmids = [];
  state.abstractSummary = '';
  state.finalAnswer = '';
  $.question.value = '';
  hideSearchNotice();
  $.picoResult.style.display = 'none';
  $.analyzeBtn.textContent = '分析問題';
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
  const blob = new Blob([content], { type });
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
      state.searchLimit = saved.searchLimit || DEFAULT_SEARCH_LIMIT;
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
}

/* ============================================================
   Init
   ============================================================ */
function init() {
  cacheElements();
  loadState();
  checkHealth();
  setInterval(checkHealth, HEALTH_POLL_MS);
}

document.addEventListener('DOMContentLoaded', () => {
  init();
  bindEvents();
});
