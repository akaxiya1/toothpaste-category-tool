const appState = {
  meta: null,
  data: null,
  importPreview: null,
  comparisonCache: new Map(),
  crawlResult: null,
  selectedPriceBand: null,
  selectedDashboardDetail: null,
  brandRecommendationCache: new Map(),
  pricePreviewCache: new Map(),
  previewTimers: new Map(),
  autoMarketRefreshAttempted: false,
  pricingSimulation: null,
  selectedManualSkuId: null,
};

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "请求失败");
  }
  return payload;
}

function formatCurrency(value) {
  const number = Number(value || 0);
  return `¥${number.toFixed(2)}`;
}

function formatPercent(value) {
  const number = Number(value || 0);
  return `${(number * 100).toFixed(1)}%`;
}

function formatMaybeCurrency(value) {
  const number = Number(value || 0);
  return number ? formatCurrency(number) : "-";
}

function formatPriceRange(minValue, maxValue) {
  const minNumber = Number(minValue || 0);
  const maxNumber = Number(maxValue || 0);
  if (!minNumber && !maxNumber) return "-";
  if (minNumber && maxNumber && Math.abs(minNumber - maxNumber) >= 0.01) {
    return `${formatCurrency(minNumber)} - ${formatCurrency(maxNumber)}`;
  }
  return formatCurrency(maxNumber || minNumber);
}

function renderReasonList(items) {
  if (!items?.length) {
    return `<span class="muted">暂无依据说明。</span>`;
  }
  return `<ul class="reason-list">${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function actionBadgeClass(action) {
  if (["建议上新", "建议替换现有SKU", "优先上新", "优先替换"].includes(action)) return "green";
  if (["建议调整售价", "建议低价引流", "建议利润定价", "建议观察"].includes(action)) return "orange";
  if (action === "建议下架") return "red";
  return "gray";
}

function sampleQualityBadgeClass(label) {
  if (label === "高") return "green";
  if (["中", "低", "需刷新", "替代", "人工"].includes(label)) return "orange";
  if (label === "无样本") return "red";
  return "gray";
}

function marketStatusPriority(label) {
  const order = {
    被拦截: 0,
    无结果: 1,
    样本不足: 2,
    近似样本: 3,
    跨平台替代: 4,
    人工补样本: 5,
    待更新: 6,
  };
  return order[label] ?? 99;
}

function showBanner(id, message) {
  const banner = document.getElementById(id);
  banner.textContent = message;
  banner.classList.add("show");
}

function isSelectedDashboardDetail(type, key) {
  return (
    appState.selectedDashboardDetail &&
    appState.selectedDashboardDetail.type === type &&
    appState.selectedDashboardDetail.key === key
  );
}

function setSelectedDashboardDetail(type, key) {
  appState.selectedDashboardDetail = { type, key };
  appState.selectedPriceBand = type === "price_band" ? key : null;
}

function sourceMethodLabel(method) {
  if (method === "browser_assisted") return "浏览器辅助采集";
  if (method === "bulk_paste") return "批量粘贴采集";
  return "平台直连抓取";
}

function setModule(targetId) {
  document.querySelectorAll(".nav-btn").forEach((button) => {
    button.classList.toggle("active", button.dataset.target === targetId);
  });
  document.querySelectorAll(".module").forEach((node) => {
    node.classList.toggle("active", node.id === targetId);
  });
  if (["dashboardModule", "recommendationModule"].includes(targetId)) {
    maybeAutoRefreshMarket(targetId);
  }
}

function renderNav() {
  document.querySelectorAll(".nav-btn").forEach((button) => {
    button.addEventListener("click", () => {
      setModule(button.dataset.target);
    });
  });
}

function renderMeta() {
  document.getElementById("dbPath").textContent = `数据库位置：${appState.meta.db_path}`;
  document.getElementById("cookieNotice").textContent = appState.meta.crawler_cookie_notice;
  document.getElementById("browserHelperNotice").textContent = appState.meta.crawler_browser_helper_notice;
  const keywordInput = document.getElementById("crawlKeyword");
  if (keywordInput) {
    keywordInput.placeholder = "支持逗号分隔多个关键词，例如：牙膏, 美白牙膏, 儿童牙膏";
    keywordInput.title = `默认会自动补齐词包：${(appState.meta.crawler_default_keywords || []).join(" / ")}`;
  }
  const templateLinks = document.getElementById("templateLinks");
  templateLinks.innerHTML = appState.meta.sample_files
    .map((item) => `<a href="${item.path}" download>${escapeHtml(item.label)}</a>`)
    .join("");

  populateSelect(document.getElementById("candidateEfficacy"), appState.meta.efficacy_options, "其他");
  populateSelect(document.getElementById("candidatePlatform"), appState.meta.platforms, "其他");
  populateSelect(document.getElementById("candidateTargetGroup"), appState.meta.target_groups, "成人");
  populateSelect(document.getElementById("candidatePromoType"), appState.meta.promo_types, "常规款");
  renderCrawlerPlatforms();
  renderCaptureHelperPlatformSelects();
}

function renderCrawlerPlatforms() {
  const container = document.getElementById("crawlPlatforms");
  const selected = new Set(appState.meta.crawler_default_platforms || []);
  container.innerHTML = (appState.meta.crawler_platforms || [])
    .map(
      (item) => `
        <label class="crawl-platform">
          <input type="checkbox" value="${escapeHtml(item.key)}" ${selected.has(item.key) ? "checked" : ""} />
          <span>${escapeHtml(item.label)}</span>
        </label>
      `,
    )
    .join("");
}

function renderCaptureHelperPlatformSelects() {
  const options = [`<option value="">自动识别</option>`]
    .concat((appState.meta.crawler_platforms || []).map((item) => `<option value="${escapeHtml(item.key)}">${escapeHtml(item.label)}</option>`))
    .join("");
  const browserSelect = document.getElementById("browserCapturePlatform");
  const pasteSelect = document.getElementById("pasteCapturePlatform");
  if (browserSelect) browserSelect.innerHTML = options;
  if (pasteSelect) {
    pasteSelect.innerHTML = options;
    pasteSelect.value = "taobao";
  }
}

function populateSelect(select, options, defaultValue = "") {
  select.innerHTML = options.map((item) => `<option value="${escapeHtml(item)}">${escapeHtml(item)}</option>`).join("");
  if (defaultValue) {
    select.value = defaultValue;
  }
}

function renderSummary() {
  const summary = appState.data.dashboard.summary;
  const cards = [
    ["现有SKU数", summary.sku_count, "门店当前在售牙膏数量"],
    ["候选新品数", summary.candidate_count, "待评估新品池"],
    ["覆盖品牌数", summary.brand_count, "当前参与陈列品牌"],
    ["半年总销量", summary.sales_total, "近6个月销量汇总"],
    ["平均毛利率", formatPercent(summary.average_margin), "现有 SKU 平均毛利"],
    ["待补市场快照", summary.market_pending_count, "还没拿到淘宝价格样本的 SKU"],
  ];
  document.getElementById("summaryGrid").innerHTML = cards
    .map(
      ([label, value, sub]) => `
        <article class="summary-card">
          <div class="label">${escapeHtml(label)}</div>
          <div class="value">${escapeHtml(value)}</div>
          <div class="sub">${escapeHtml(sub)}</div>
        </article>
      `,
    )
    .join("");
}

function renderSkuFilters() {
  const skus = appState.data.skus;
  const unique = (items) => Array.from(new Set(items.filter(Boolean))).sort((a, b) => a.localeCompare(b, "zh-CN"));
  setFilterOptions("skuBrandFilter", ["", ...unique(skus.map((item) => item.brand))], "全部品牌");
  setFilterOptions("skuEfficacyFilter", ["", ...unique(skus.map((item) => item.efficacy_tags))], "全部功效");
  setFilterOptions("skuPriceBandFilter", ["", ...appState.meta.price_bands], "全部价格带");
}

function setFilterOptions(id, values, allLabel) {
  const select = document.getElementById(id);
  select.innerHTML = values
    .map((value, index) => {
      const label = index === 0 ? allLabel : value;
      return `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`;
    })
    .join("");
}

function filteredSkuRows() {
  const search = document.getElementById("skuSearch").value.trim().toLowerCase();
  const brand = document.getElementById("skuBrandFilter").value;
  const efficacy = document.getElementById("skuEfficacyFilter").value;
  const band = document.getElementById("skuPriceBandFilter").value;
  const action = document.getElementById("skuActionFilter").value;
  return appState.data.skus.filter((item) => {
    const matchesSearch =
      !search ||
      [item.brand, item.product_name, item.sku_code].some((value) => String(value || "").toLowerCase().includes(search));
    return (
      matchesSearch &&
      (!brand || item.brand === brand) &&
      (!efficacy || item.efficacy_tags === efficacy) &&
      (!band || item.price_band === band) &&
      (!action || item.action === action)
    );
  });
}

function renderSkuTable() {
  const table = document.getElementById("skuTable");
  const rows = filteredSkuRows();
  table.querySelector("thead").innerHTML = `
    <tr>
      <th>SKU编码</th>
      <th>品牌 / 名称</th>
      <th>规格</th>
      <th>功效</th>
      <th>售价</th>
      <th>进价</th>
      <th>毛利率</th>
      <th>淘宝均价</th>
      <th>热度</th>
      <th>系统分层</th>
      <th>价格带</th>
      <th>建议动作</th>
    </tr>
  `;
  table.querySelector("tbody").innerHTML = rows.length
    ? rows
        .map(
          (item) => `
      <tr>
        <td>${escapeHtml(item.sku_code)}</td>
        <td><strong>${escapeHtml(item.brand)}</strong><br />${escapeHtml(item.product_name)}</td>
        <td>${escapeHtml(item.spec_text || "-")}</td>
        <td>${escapeHtml(item.efficacy_tags)}</td>
        <td>${formatCurrency(item.current_price)}</td>
        <td>${formatCurrency(item.purchase_price)}</td>
        <td>${formatPercent(item.gross_margin)}</td>
        <td>${item.taobao_avg_price ? formatCurrency(item.taobao_avg_price) : "-"}</td>
        <td>${escapeHtml(item.online_heat_score || 0)}</td>
        <td>${escapeHtml(item.structural_role)}</td>
        <td>${escapeHtml(item.price_band)}</td>
        <td><span class="badge ${actionBadgeClass(item.action)}">${escapeHtml(item.action)}</span></td>
      </tr>
    `,
        )
        .join("")
    : `<tr><td colspan="12"><div class="empty-state">暂无匹配结果。</div></td></tr>`;
}

function renderCandidateTable() {
  const table = document.getElementById("candidateTable");
  const rows = appState.data.candidates;
  table.querySelector("thead").innerHTML = `
    <tr>
      <th>品牌 / 名称</th>
      <th>平台</th>
      <th>参考价</th>
      <th>建议售价</th>
      <th>预计毛利率</th>
      <th>热度分</th>
      <th>结论</th>
      <th>操作</th>
    </tr>
  `;
  table.querySelector("tbody").innerHTML = rows.length
    ? rows
        .map(
          (item) => `
      <tr>
        <td><strong>${escapeHtml(item.brand)}</strong><br />${escapeHtml(item.product_name)}</td>
        <td>${escapeHtml(item.source_platform || "其他")}</td>
        <td>${formatCurrency(item.online_reference_price)}</td>
        <td>${formatCurrency(item.suggested_price)}</td>
        <td>${formatPercent(item.expected_margin)}</td>
        <td>${escapeHtml(item.heat_score || 0)}</td>
        <td><span class="badge ${actionBadgeClass(item.suggestion_status)}">${escapeHtml(item.suggestion_status)}</span></td>
        <td>
          <button class="action-link" data-action="edit" data-id="${item.id}">编辑</button>
          /
          <button class="action-link" data-action="delete" data-id="${item.id}">删除</button>
        </td>
      </tr>
    `,
        )
        .join("")
    : `<tr><td colspan="8"><div class="empty-state">还没有候选新品，先录入一条试试。</div></td></tr>`;
}

function renderCrawlPreview() {
  const container = document.getElementById("crawlPreview");
  const result = appState.crawlResult;
  if (!result) {
    container.innerHTML = "";
    return;
  }
  const errorEntries = Object.entries(result.errors || {});
  const errorHtml = errorEntries.length
    ? `<ul class="risk-list">${errorEntries.map(([platform, message]) => `<li>${escapeHtml(platform)}：${escapeHtml(message)}</li>`).join("")}</ul>`
    : `<p class="muted">本次抓取没有返回平台错误。</p>`;
  const reportHtml = (result.platform_reports || []).length
    ? `
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>平台</th>
              <th>模式</th>
              <th>尝试</th>
              <th>结果</th>
              <th>说明</th>
            </tr>
          </thead>
          <tbody>
            ${result.platform_reports
              .map(
                (item) => `
              <tr>
                <td>${escapeHtml(item.platform_label || item.platform)}</td>
                <td>${escapeHtml(item.used_cookie_fallback ? "匿名 + Cookie兜底" : "匿名优先")}</td>
                <td>${escapeHtml(item.queries_attempted || 0)}</td>
                <td>${escapeHtml(item.success_count || 0)}</td>
                <td class="muted">${escapeHtml(item.message || "-")}</td>
              </tr>
            `,
              )
              .join("")}
          </tbody>
        </table>
      </div>
    `
    : `<p class="muted">这次没有平台级反馈。</p>`;

  const previewHtml = (result.preview || []).length
    ? `
      <div class="crawl-preview-grid">
        ${result.preview
          .map(
            (item) => `
          <article class="crawl-preview-item">
            <div class="inline-actions">
              <strong>${escapeHtml(item.brand)}</strong>
              <span class="badge green">${escapeHtml(item.source_platform)}</span>
            </div>
            <p class="muted">${escapeHtml(item.product_name)}</p>
            <p class="muted">参考价 ${formatCurrency(item.online_reference_price)} · 热度 ${escapeHtml(item.heat_score)}</p>
            <p class="muted compact-note">${escapeHtml(item.notes || item.differentiation || "")}</p>
          </article>
        `,
          )
          .join("")}
      </div>
    `
    : `<div class="empty-state">这次没有整理出可入库的候选商品。</div>`;

  container.innerHTML = `
    <div class="card stack">
      <div class="inline-actions">
        <h3>${escapeHtml(sourceMethodLabel(result.source_method))}</h3>
        <span class="badge gray">关键词 ${escapeHtml((result.keywords_used || []).join(" / ") || "牙膏")}</span>
      </div>
      <div class="selection-summary">
        <div class="score-chip"><div class="label">原始抓取</div><div class="value">${result.fetched_raw_count || 0}</div></div>
        <div class="score-chip"><div class="label">候选整理</div><div class="value">${result.candidate_payload_count || 0}</div></div>
        <div class="score-chip"><div class="label">新增入库</div><div class="value">${result.inserted || 0}</div></div>
        <div class="score-chip"><div class="label">更新已有</div><div class="value">${result.updated || 0}</div></div>
      </div>
      <div class="two-column">
        <div class="stack">
          <h3>抓取预览</h3>
          ${previewHtml}
        </div>
        <div class="stack">
          <h3>平台反馈</h3>
          ${reportHtml}
          ${errorHtml}
        </div>
      </div>
    </div>
  `;
}

function renderComparisonSelector() {
  const select = document.getElementById("comparisonSelector");
  const options = appState.data.candidates.map(
    (item) => `<option value="${item.id}">${escapeHtml(item.brand)} - ${escapeHtml(item.product_name)}</option>`,
  );
  select.innerHTML = options.length ? options.join("") : `<option value="">暂无候选新品</option>`;
}

async function renderComparisonPanel() {
  const select = document.getElementById("comparisonSelector");
  const candidateId = select.value;
  const panel = document.getElementById("comparisonPanel");
  if (!candidateId) {
    panel.innerHTML = `<div class="empty-state">先在“候选新品库”录入候选商品，再进行对比。</div>`;
    return;
  }
  let payload = appState.comparisonCache.get(candidateId);
  if (!payload) {
    payload = await fetchJson(`/api/candidates/${candidateId}/comparison`);
    appState.comparisonCache.set(candidateId, payload);
  }
  const candidate = payload.candidate;
  const breakdown = candidate.score_breakdown || {};
  const scoreCards = Object.entries(breakdown)
    .map(
      ([label, value]) => `
        <div class="score-chip">
          <div class="label">${escapeHtml(label)}</div>
          <div class="value">${escapeHtml(value)}</div>
        </div>
      `,
    )
    .join("");
  const comparisonCards = payload.comparisons.length
    ? payload.comparisons
        .map(
          (item) => `
        <article class="comparison-card">
          <div class="inline-actions">
            <h3>${escapeHtml(item.brand)} · ${escapeHtml(item.product_name)}</h3>
            <span class="badge ${actionBadgeClass(item.cannibalization_risk === "高" ? "建议下架" : item.cannibalization_risk === "中" ? "建议观察" : "建议维持常规价")}">
              蚕食风险 ${escapeHtml(item.cannibalization_risk)}
            </span>
          </div>
          <div class="comparison-stats">
            <div class="stat-box"><strong>售价</strong><br />${formatCurrency(item.current_price)}</div>
            <div class="stat-box"><strong>进价</strong><br />${formatCurrency(item.purchase_price)}</div>
            <div class="stat-box"><strong>毛利率</strong><br />${formatPercent(item.gross_margin)}</div>
            <div class="stat-box"><strong>半年销量</strong><br />${escapeHtml(item.six_month_sales)}</div>
            <div class="stat-box"><strong>单克售价</strong><br />${item.unit_price ? item.unit_price.toFixed(4) : "-"}</div>
            <div class="stat-box"><strong>系统分层</strong><br />${escapeHtml(item.structural_role)}</div>
          </div>
          <p class="muted">匹配分：${item.match_score}，价格带 ${escapeHtml(item.price_band)}，功效 ${escapeHtml(item.efficacy_tags)}</p>
        </article>
      `,
        )
        .join("")
    : `<div class="empty-state">当前没有可对比的在售 SKU。</div>`;

  panel.innerHTML = `
    <div class="comparison-grid">
      <article class="comparison-card highlight">
        <div class="inline-actions">
          <h3>${escapeHtml(candidate.brand)} · ${escapeHtml(candidate.product_name)}</h3>
          <span class="badge ${actionBadgeClass(candidate.suggestion_status)}">${escapeHtml(candidate.suggestion_status)}</span>
        </div>
        <p class="muted">${escapeHtml(candidate.source_platform || "其他")} · ${escapeHtml(candidate.efficacy_tags)} · ${escapeHtml(candidate.spec_text)}</p>
        <div class="comparison-stats">
          <div class="stat-box"><strong>线上参考价</strong><br />${formatCurrency(candidate.online_reference_price)}</div>
          <div class="stat-box"><strong>预计进价</strong><br />${formatCurrency(candidate.expected_purchase_price)}</div>
          <div class="stat-box"><strong>建议售价</strong><br />${formatCurrency(candidate.suggested_price)}</div>
          <div class="stat-box"><strong>预计毛利率</strong><br />${formatPercent(candidate.expected_margin)}</div>
          <div class="stat-box"><strong>建议分层</strong><br />${escapeHtml(candidate.proposed_role)}</div>
          <div class="stat-box"><strong>综合评分</strong><br />${candidate.recommendation_score}</div>
        </div>
        <p class="muted">${escapeHtml(candidate.differentiation || "暂无差异化卖点说明")}</p>
      </article>
      <article class="comparison-card">
        <h3>评分拆解</h3>
        <div class="score-grid">${scoreCards}</div>
      </article>
    </div>
    <div class="comparison-grid" style="margin-top:18px;">${comparisonCards}</div>
  `;
}

function renderBarList(items, labelKey, valueKey, formatter = (value) => value) {
  const max = Math.max(...items.map((item) => Number(item[valueKey] || 0)), 1);
  return `
    <div class="bar-list">
      ${items
        .map(
          (item) => `
            <div class="bar-row">
              <div>${escapeHtml(item[labelKey])}</div>
              <div class="bar-track"><div class="bar-fill" style="width:${(Number(item[valueKey] || 0) / max) * 100}%"></div></div>
              <div>${escapeHtml(formatter(item[valueKey]))}</div>
            </div>
          `,
        )
        .join("")}
    </div>
  `;
}

function renderPriceBandBars(items) {
  const max = Math.max(...items.map((item) => Number(item.count || 0)), 1);
  return `
    <div class="bar-list">
      ${items
        .map(
          (item) => `
            <button class="bar-row bar-row-button ${isSelectedDashboardDetail("price_band", item.label) ? "active" : ""}" data-dashboard-type="price_band" data-dashboard-key="${escapeHtml(item.label)}" type="button">
              <div>${escapeHtml(item.label)}</div>
              <div class="bar-track"><div class="bar-fill" style="width:${(Number(item.count || 0) / max) * 100}%"></div></div>
              <div>${escapeHtml(item.count)}</div>
            </button>
          `,
        )
        .join("")}
    </div>
  `;
}

function renderDashboardBars(items, type, labelKey, valueKey) {
  const max = Math.max(...items.map((item) => Number(item[valueKey] || 0)), 1);
  return `
    <div class="bar-list">
      ${items
        .map(
          (item) => `
            <button class="bar-row bar-row-button ${isSelectedDashboardDetail(type, item[labelKey]) ? "active" : ""}" data-dashboard-type="${escapeHtml(type)}" data-dashboard-key="${escapeHtml(item[labelKey])}" type="button">
              <div>${escapeHtml(item[labelKey])}</div>
              <div class="bar-track"><div class="bar-fill" style="width:${(Number(item[valueKey] || 0) / max) * 100}%"></div></div>
              <div>${escapeHtml(item[valueKey])}</div>
            </button>
          `,
        )
        .join("")}
    </div>
  `;
}

function scrollToBandDetailPanel() {
  const panel = document.getElementById("bandDetailPanel");
  if (!panel) return;
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function getPreviewItem(item) {
  const preview = appState.pricePreviewCache.get(Number(item.id));
  return preview?.item || item;
}

function previewStatusText(item) {
  const preview = appState.pricePreviewCache.get(Number(item.id));
  if (!preview) return "改价后会实时预览毛利率、价格带和动作变化。";
  const changes = preview.changes || {};
  const notes = [];
  if (changes.price_band_changed) notes.push("价格带会变化");
  if (changes.role_changed) notes.push("系统分层会变化");
  if (changes.action_changed) notes.push("建议动作会变化");
  if (!notes.length) notes.push("当前结构判断基本不变");
  return `预览毛利率 ${formatPercent(changes.preview_margin || preview.item.gross_margin)}；${notes.join("，")}。`;
}

function getBrandRecommendationEntry(brand) {
  return appState.brandRecommendationCache.get(String(brand || "")) || null;
}

function invalidateBrandRecommendationCache(brand = null) {
  if (brand) {
    appState.brandRecommendationCache.delete(String(brand));
    return;
  }
  appState.brandRecommendationCache.clear();
}

function seedBrandCaptureKeywords(brand) {
  const keyword = `${brand} 牙膏`;
  const crawlKeyword = document.getElementById("crawlKeyword");
  const pasteKeyword = document.getElementById("pasteCaptureKeyword");
  const browserSource = document.getElementById("browserCaptureSourceUrl");
  if (crawlKeyword) crawlKeyword.value = keyword;
  if (pasteKeyword) pasteKeyword.value = keyword;
  if (browserSource && !browserSource.value.trim()) {
    browserSource.placeholder = `可选：粘贴 ${brand} 搜索页链接`;
  }
}

async function loadBrandRecommendations(brand, { force = false } = {}) {
  const normalizedBrand = String(brand || "").trim();
  if (!normalizedBrand) return null;

  const cached = getBrandRecommendationEntry(normalizedBrand);
  if (!force && cached?.data && !cached.loading) {
    return cached.data;
  }

  appState.brandRecommendationCache.set(normalizedBrand, {
    loading: true,
    error: "",
    data: force ? null : cached?.data || null,
  });
  renderDashboard();

  try {
    const result = await fetchJson("/api/dashboard/brand-recommendations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        brand: normalizedBrand,
        force_refresh: force,
        cookies: readCrawlerCookies(),
      }),
    });
    appState.brandRecommendationCache.set(normalizedBrand, {
      loading: false,
      error: "",
      data: result,
    });
    if (result.auto_crawl_triggered) {
      await refreshState();
    } else {
      renderDashboard();
    }
    return result;
  } catch (error) {
    appState.brandRecommendationCache.set(normalizedBrand, {
      loading: false,
      error: error.message,
      data: cached?.data || null,
    });
    renderDashboard();
    throw error;
  }
}

async function reloadActiveBrandRecommendations({ force = false } = {}) {
  const detail = appState.selectedDashboardDetail;
  if (!detail || detail.type !== "brand") return null;
  return loadBrandRecommendations(detail.key, { force });
}

function dashboardDetailConfig() {
  const dashboard = appState.data.dashboard;
  const detail = appState.selectedDashboardDetail;
  if (!detail) {
    return null;
  }

  if (detail.type === "price_band") {
    return {
      title: `${detail.key} 价格带明细`,
      subtitle: "直接改售价即可实时预览新毛利率，点击保存后会重算看板和定价建议。",
      rows: (dashboard.price_band_details?.[detail.key] || []).slice(),
    };
  }

  if (detail.type === "brand") {
    const selectedBrand = (dashboard.brand_distribution || []).find((item) => item.brand === detail.key);
    return {
      title: `${detail.key} 品牌明细`,
      subtitle: `当前品牌共有 ${selectedBrand?.sku_count || 0} 个 SKU，点开后可以直接查看并比较同品牌价格结构。`,
      rows: (dashboard.brand_details?.[detail.key] || []).slice(),
    };
  }

  if (detail.type === "efficacy") {
    const selectedEfficacy = (dashboard.efficacy_distribution || []).find((item) => item.efficacy === detail.key);
    return {
      title: `${detail.key} 明细`,
      subtitle: selectedEfficacy?.description || "从消费者主要购买诉求看这一组商品的覆盖情况。",
      rows: (dashboard.efficacy_details?.[detail.key] || []).slice(),
    };
  }

  return null;
}

function renderDashboardDetailPanel() {
  const detail = dashboardDetailConfig();
  if (!detail) {
    return `
      <article class="card stack band-detail-panel" id="bandDetailPanel">
        <h3>看板明细</h3>
        <div class="empty-state">点击价格带、品牌集中度或功效覆盖中的任一项，直接展开对应牙膏清单；在这里也可以实时改价预览毛利率。</div>
      </article>
    `;
  }
  const rows = detail.rows
    .slice()
    .sort((a, b) => `${a.brand}${a.product_name}`.localeCompare(`${b.brand}${b.product_name}`, "zh-CN"));
  return `
    <article class="card stack band-detail-panel" id="bandDetailPanel">
      <div class="inline-actions">
        <div>
          <h3>${escapeHtml(detail.title)}</h3>
          <p class="muted">${escapeHtml(detail.subtitle)}</p>
        </div>
      </div>
      <div class="table-wrap">
        <table id="bandDetailTable">
          <thead>
            <tr>
              <th>品牌 / 名称</th>
              <th>规格</th>
              <th>当前售价</th>
              <th>进价</th>
              <th>毛利率</th>
              <th>系统分层</th>
              <th>淘宝均价</th>
              <th>价格状态</th>
              <th>热度</th>
              <th>系统动作</th>
              <th>预览</th>
              <th>保存</th>
            </tr>
          </thead>
          <tbody>
            ${
              rows.length
                ? rows
                    .map((row) => {
                      const liveItem = getPreviewItem(row);
                      return `
                        <tr>
                          <td><strong>${escapeHtml(row.brand)}</strong><br />${escapeHtml(row.product_name)}</td>
                          <td>${escapeHtml(row.spec_text || "-")}</td>
                          <td>
                            <input
                              class="price-edit-input"
                              data-sku-id="${row.id}"
                              type="number"
                              min="0"
                              step="0.01"
                              value="${Number(liveItem.current_price || row.current_price).toFixed(2)}"
                            />
                          </td>
                          <td>${formatCurrency(row.purchase_price)}</td>
                          <td>${formatPercent(liveItem.gross_margin)}</td>
                          <td>${escapeHtml(liveItem.structural_role)}</td>
                          <td>${liveItem.taobao_avg_price ? formatCurrency(liveItem.taobao_avg_price) : "-"}</td>
                          <td>${escapeHtml(liveItem.price_disorder_label || "待更新")}</td>
                          <td>${escapeHtml(liveItem.online_heat_score || 0)}</td>
                          <td><span class="badge ${actionBadgeClass(liveItem.action)}">${escapeHtml(liveItem.action)}</span></td>
                          <td><div class="mini-status">${escapeHtml(previewStatusText(row))}</div></td>
                          <td><button class="ghost-btn small-btn" type="button" data-action="save-price" data-sku-id="${row.id}">保存</button></td>
                        </tr>
                      `;
                    })
                    .join("")
                : `<tr><td colspan="12"><div class="empty-state">当前筛选维度下暂无商品。</div></td></tr>`
            }
          </tbody>
        </table>
      </div>
    </article>
  `;
}

function dashboardDetailConfig() {
  const dashboard = appState.data.dashboard;
  const detail = appState.selectedDashboardDetail;
  if (!detail) {
    return null;
  }

  if (detail.type === "price_band") {
    return {
      title: `${detail.key} 价格带明细`,
      subtitle: "直接改售价即可实时预览新毛利率，点击保存后会重算看板和定价建议。",
      rows: (dashboard.price_band_details?.[detail.key] || []).slice(),
      type: detail.type,
      key: detail.key,
    };
  }

  if (detail.type === "brand") {
    const selectedBrand = (dashboard.brand_distribution || []).find((item) => item.brand === detail.key);
    return {
      title: `${detail.key} 品牌现有上架 SKU`,
      subtitle: `当前这个品牌已上架 ${selectedBrand?.sku_count || 0} 个 SKU，下方仍可直接改价并实时预览毛利率。`,
      rows: (dashboard.brand_details?.[detail.key] || []).slice(),
      type: detail.type,
      key: detail.key,
      brand: detail.key,
    };
  }

  if (detail.type === "efficacy") {
    const selectedEfficacy = (dashboard.efficacy_distribution || []).find((item) => item.efficacy === detail.key);
    return {
      title: `${detail.key} 明细`,
      subtitle: selectedEfficacy?.description || "从消费者主要购买诉求看这一组商品的覆盖情况。",
      rows: (dashboard.efficacy_details?.[detail.key] || []).slice(),
      type: detail.type,
      key: detail.key,
    };
  }

  return null;
}

function renderSkuDetailTable(rows, emptyMessage = "当前这个维度下暂无商品。") {
  const sortedRows = rows
    .slice()
    .sort((a, b) => `${a.brand}${a.product_name}`.localeCompare(`${b.brand}${b.product_name}`, "zh-CN"));
  return `
    <div class="table-wrap">
      <table id="bandDetailTable">
        <thead>
          <tr>
            <th>品牌 / 名称</th>
            <th>规格</th>
            <th>当前售价</th>
            <th>进价</th>
            <th>毛利率</th>
            <th>系统分层</th>
            <th>淘宝均价</th>
            <th>价格状态</th>
            <th>热度</th>
            <th>系统动作</th>
            <th>预览</th>
            <th>保存</th>
          </tr>
        </thead>
        <tbody>
          ${
            sortedRows.length
              ? sortedRows
                  .map((row) => {
                    const liveItem = getPreviewItem(row);
                    return `
                      <tr>
                        <td><strong>${escapeHtml(row.brand)}</strong><br />${escapeHtml(row.product_name)}</td>
                        <td>${escapeHtml(row.spec_text || "-")}</td>
                        <td>
                          <input
                            class="price-edit-input"
                            data-sku-id="${row.id}"
                            type="number"
                            min="0"
                            step="0.01"
                            value="${Number(liveItem.current_price || row.current_price).toFixed(2)}"
                          />
                        </td>
                        <td>${formatCurrency(row.purchase_price)}</td>
                        <td>${formatPercent(liveItem.gross_margin)}</td>
                        <td>${escapeHtml(liveItem.structural_role)}</td>
                        <td>${liveItem.taobao_avg_price ? formatCurrency(liveItem.taobao_avg_price) : "-"}</td>
                        <td>${escapeHtml(liveItem.price_disorder_label || "待更新")}</td>
                        <td>${escapeHtml(liveItem.online_heat_score || 0)}</td>
                        <td><span class="badge ${actionBadgeClass(liveItem.action)}">${escapeHtml(liveItem.action)}</span></td>
                        <td><div class="mini-status">${escapeHtml(previewStatusText(row))}</div></td>
                        <td><button class="ghost-btn small-btn" type="button" data-action="save-price" data-sku-id="${row.id}">保存</button></td>
                      </tr>
                    `;
                  })
                  .join("")
              : `<tr><td colspan="12"><div class="empty-state">${escapeHtml(emptyMessage)}</div></td></tr>`
          }
        </tbody>
      </table>
    </div>
  `;
}

function renderBrandRecommendationSection(brand) {
  const entry = getBrandRecommendationEntry(brand);
  const result = entry?.data || null;
  const recommendationRows = result?.missing_brand_hits || [];
  const badges = [];

  if (result?.used_cached_candidates) badges.push(`<span class="badge">已读本地候选池</span>`);
  if (result?.auto_crawl_triggered) {
    badges.push(`<span class="badge ${result.crawl_status === "crawl_success" ? "green" : "orange"}">已按需自动补抓</span>`);
  }
  if (result?.fallback_mode === "local_after_crawl_failure") {
    badges.push(`<span class="badge orange">补抓失败，已回退本地候选</span>`);
  } else if (result?.fallback_mode === "cached_candidates") {
    badges.push(`<span class="badge">当前优先使用缓存候选</span>`);
  } else if (result?.fallback_mode === "no_candidates") {
    badges.push(`<span class="badge red">暂时没有同品牌候选</span>`);
  }

  let summaryText = "系统会优先读取本地同品牌候选池；如果这个品牌候选不足或超过 24 小时未刷新，会自动补抓同品牌热销结果。";
  if (entry?.loading) {
    summaryText = "正在分析这个品牌还没上的热销爆款，并按需补抓同品牌候选，请稍等。";
  } else if (result?.fallback_message) {
    summaryText = result.fallback_message;
  } else if (result?.auto_crawl_triggered && result?.crawl_status === "crawl_success") {
    summaryText = "已补抓这个品牌的热销候选，并结合本地候选池做同品牌缺失爆款推荐。";
  } else if (result?.used_cached_candidates) {
    summaryText = "当前推荐基于本地候选池里的同品牌商品，并按品牌补位和类目结构一起排序。";
  }

  const tableHtml = recommendationRows.length
    ? `
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>品牌 / 名称</th>
              <th>平台</th>
              <th>线上参考价</th>
              <th>热度分</th>
              <th>建议售价</th>
              <th>预计毛利率</th>
              <th>推荐原因</th>
              <th>系统结论</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            ${recommendationRows
              .map(
                (item, index) => `
                  <tr>
                    <td><strong>${escapeHtml(item.brand)}</strong><br />${escapeHtml(item.product_name)}<br /><span class="muted">${escapeHtml(item.spec_text || "-")}</span></td>
                    <td>${escapeHtml(item.source_platform || "其他")}</td>
                    <td>${formatMaybeCurrency(item.online_reference_price)}</td>
                    <td>${escapeHtml(item.heat_score || 0)}</td>
                    <td>${formatMaybeCurrency(item.suggested_price)}</td>
                    <td>${formatPercent(item.expected_margin)}</td>
                    <td><div class="mini-status">${escapeHtml(item.brand_gap_reason || "-")}</div></td>
                    <td><span class="badge ${actionBadgeClass(item.recommendation_action)}">${escapeHtml(item.recommendation_action)}</span></td>
                    <td><button class="ghost-btn small-btn" type="button" data-action="open-brand-candidate" data-brand="${escapeHtml(brand)}" data-hit-index="${index}">编辑候选</button></td>
                  </tr>
                `,
              )
              .join("")}
          </tbody>
        </table>
      </div>
    `
    : `<div class="empty-state">${escapeHtml(result?.fallback_message || "这个品牌暂时没有可直接推荐的未上架爆款，可以试试浏览器辅助采集或批量粘贴采集。")}</div>`;

  return `
    <article class="card stack">
      <div class="inline-actions">
        <div>
          <h3>${escapeHtml(brand)} 缺失爆款推荐</h3>
          <p class="muted">只推荐同品牌、门店还没上的商品，默认展示 Top 3。</p>
        </div>
        <div class="inline-actions">
          <button class="ghost-btn small-btn" type="button" data-action="refresh-brand-recommendations" data-brand="${escapeHtml(brand)}">刷新这个品牌</button>
          <button class="ghost-btn small-btn" type="button" data-action="go-browser-capture" data-brand="${escapeHtml(brand)}">浏览器辅助采集</button>
          <button class="ghost-btn small-btn" type="button" data-action="go-paste-capture" data-brand="${escapeHtml(brand)}">批量粘贴采集</button>
        </div>
      </div>
      ${badges.length ? `<div class="principle-strip">${badges.join("")}</div>` : ""}
      <p class="muted">${escapeHtml(summaryText)}</p>
      ${entry?.error ? `<div class="mini-status">${escapeHtml(entry.error)}</div>` : ""}
      ${tableHtml}
    </article>
  `;
}

function renderDashboardDetailPanel() {
  const detail = dashboardDetailConfig();
  if (!detail) {
    return `
      <article class="card stack band-detail-panel" id="bandDetailPanel">
        <h3>看板明细</h3>
        <div class="empty-state">点击价格带、品牌集中度或功效覆盖中的任一项，就能直接展开对应商品清单；在这里也可以实时改价预览毛利率。</div>
      </article>
    `;
  }

  if (detail.type === "brand") {
    return `
      <section class="stack band-detail-panel" id="bandDetailPanel">
        ${renderBrandRecommendationSection(detail.brand)}
        <article class="card stack">
          <div class="inline-actions">
            <div>
              <h3>${escapeHtml(detail.title)}</h3>
              <p class="muted">${escapeHtml(detail.subtitle)}</p>
            </div>
          </div>
          ${renderSkuDetailTable(detail.rows, "当前这个品牌下暂无已上架商品。")}
        </article>
      </section>
    `;
  }

  return `
    <article class="card stack band-detail-panel" id="bandDetailPanel">
      <div class="inline-actions">
        <div>
          <h3>${escapeHtml(detail.title)}</h3>
          <p class="muted">${escapeHtml(detail.subtitle)}</p>
        </div>
      </div>
      ${renderSkuDetailTable(detail.rows)}
    </article>
  `;
}

function renderDashboard() {
  const dashboard = appState.data.dashboard;
  const panel = document.getElementById("dashboardPanel");
  panel.innerHTML = `
    <div class="dashboard-grid">
      <article class="card stack">
        <h3>价格带覆盖</h3>
        <p class="muted">点击任一价格带，展开该带下所有商品并直接改价。</p>
        ${renderPriceBandBars(dashboard.price_band_distribution)}
      </article>
      <article class="card stack">
        <h3>品牌集中度</h3>
        <p class="muted">品牌会随着后续 SKU 导入自动扩展；点击任一品牌可查看该品牌全部牙膏。</p>
        ${dashboard.brand_distribution.length ? renderDashboardBars(dashboard.brand_distribution, "brand", "brand", "sku_count") : `<div class="empty-state">暂无数据</div>`}
      </article>
      <article class="card stack">
        <h3>功效覆盖</h3>
        <p class="muted">按消费者最常见的购买诉求来分组，点击任一功效可查看这一类牙膏。</p>
        ${renderDashboardBars(dashboard.efficacy_distribution, "efficacy", "efficacy", "count")}
      </article>
      <article class="card stack">
        <h3>系统分层分布</h3>
        ${renderBarList(dashboard.role_distribution, "role", "count")}
      </article>
      <article class="card stack">
        <h3>结构性提醒</h3>
        ${
          dashboard.structure_gaps.length
            ? `<ul class="risk-list">${dashboard.structure_gaps.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
            : `<div class="empty-state">当前结构没有明显硬缺口，可重点关注市场价格和替换效率。</div>`
        }
      </article>
      <article class="card stack">
        <h3>毛利健康度</h3>
        <div class="score-grid">
          <div class="score-chip"><div class="label">低于目标</div><div class="value">${dashboard.margin_health.below}</div></div>
          <div class="score-chip"><div class="label">落在区间</div><div class="value">${dashboard.margin_health.within}</div></div>
          <div class="score-chip"><div class="label">高于区间</div><div class="value">${dashboard.margin_health.above}</div></div>
        </div>
      </article>
    </div>
    ${renderDashboardDetailPanel()}
  `;
}

function renderAutoSelectionPanel() {
  const container = document.getElementById("autoSelectionPanel");
  const autoSelection = appState.data.recommendations.auto_selection;
  if (!autoSelection) {
    container.innerHTML = "";
    return;
  }

  const summary = autoSelection.summary || {};
  const selectedHtml = autoSelection.selected.length
    ? autoSelection.selected
        .map(
          (item) => `
        <article class="selection-item">
          <div class="inline-actions">
            <h3>${escapeHtml(item.brand)} · ${escapeHtml(item.product_name)}</h3>
            <span class="badge ${actionBadgeClass(item.auto_pick_decision === "优先替换" ? "建议替换现有SKU" : "建议上新")}">${escapeHtml(item.auto_pick_decision)}</span>
          </div>
          <p class="muted">${escapeHtml(item.proposed_role)} · ${escapeHtml(item.efficacy_tags)} · ${escapeHtml(item.price_band)}</p>
          <div class="comparison-stats">
            <div class="stat-box"><strong>建议售价</strong><br />${formatCurrency(item.suggested_price)}</div>
            <div class="stat-box"><strong>预计毛利率</strong><br />${formatPercent(item.expected_margin)}</div>
            <div class="stat-box"><strong>综合评分</strong><br />${escapeHtml(item.recommendation_score)}</div>
            <div class="stat-box"><strong>自动选品分</strong><br />${escapeHtml(item.auto_select_score)}</div>
          </div>
          <ul class="reason-list">
            ${(item.auto_select_reasons || []).map((reason) => `<li>${escapeHtml(reason)}</li>`).join("")}
          </ul>
        </article>
      `,
        )
        .join("")
    : `<div class="empty-state">候选新品还不够完整，暂时无法自动生成上新名单。</div>`;

  const waitlistHtml = autoSelection.waitlist.length
    ? autoSelection.waitlist
        .map(
          (item) => `
        <article class="selection-item">
          <div class="inline-actions">
            <strong>${escapeHtml(item.brand)} · ${escapeHtml(item.product_name)}</strong>
            <span class="badge gray">${escapeHtml(item.auto_pick_decision)}</span>
          </div>
          <p class="muted">${escapeHtml(item.proposed_role)} · 自动选品分 ${escapeHtml(item.auto_select_score)}</p>
          <p class="muted">${escapeHtml((item.auto_select_reasons || []).slice(0, 2).join("；"))}</p>
        </article>
      `,
        )
        .join("")
    : `<div class="empty-state">当前没有候补观察项。</div>`;

  container.innerHTML = `
    <div class="card stack selection-card">
      <div class="inline-actions">
        <div>
          <h3>自动选品清单</h3>
          <p class="muted">系统会优先补结构缺口、替换低效 SKU，并控制蚕食风险，自动给出建议上新的牙膏名单。</p>
        </div>
      </div>
      <div class="selection-summary">
        <div class="score-chip"><div class="label">优先名单</div><div class="value">${summary.selected_count || 0}</div></div>
        <div class="score-chip"><div class="label">候补观察</div><div class="value">${summary.waitlist_count || 0}</div></div>
        <div class="score-chip"><div class="label">替换型上新</div><div class="value">${summary.replacement_count || 0}</div></div>
        <div class="score-chip"><div class="label">结构补位</div><div class="value">${summary.gap_fill_count || 0}</div></div>
      </div>
      <div class="principle-strip">
        ${(autoSelection.principles || []).map((item) => `<span class="badge">${escapeHtml(item)}</span>`).join("")}
      </div>
      <div class="selection-grid">
        <div class="stack">
          <h3>优先上新 / 替换</h3>
          ${selectedHtml}
        </div>
        <div class="stack">
          <h3>候补观察</h3>
          ${waitlistHtml}
        </div>
      </div>
    </div>
  `;
}

function renderPricingFocusPanel() {
  const container = document.getElementById("pricingFocusPanel");
  const rows = appState.data.recommendations.existing || [];
  const counts = {
    引流: rows.filter((item) => item.action === "建议低价引流").length,
    调价: rows.filter((item) => item.action === "建议调整售价").length,
    利润: rows.filter((item) => item.action === "建议利润定价").length,
    下架: rows.filter((item) => item.action === "建议下架").length,
  };
  const sampleReadyCount = rows.filter((item) => Number(item.taobao_sample_count || 0) > 0).length;
  const sampleMissingCount = rows.length - sampleReadyCount;
  container.innerHTML = `
    <div class="card stack">
      <div class="inline-actions">
        <div>
          <h3>定价焦点</h3>
          <p class="muted">先确认哪些 SKU 需要调价、哪些还缺淘宝样本。样本抓取优先，其次才是具体定价动作。</p>
        </div>
      </div>
      <div class="focus-grid">
        <div class="score-chip"><div class="label">已有淘宝样本</div><div class="value">${sampleReadyCount}</div></div>
        <div class="score-chip"><div class="label">缺淘宝样本</div><div class="value">${sampleMissingCount}</div></div>
        <div class="score-chip"><div class="label">建议低价引流</div><div class="value">${counts.引流}</div></div>
        <div class="score-chip"><div class="label">建议调整售价</div><div class="value">${counts.调价}</div></div>
        <div class="score-chip"><div class="label">建议利润定价</div><div class="value">${counts.利润}</div></div>
        <div class="score-chip"><div class="label">建议下架</div><div class="value">${counts.下架}</div></div>
      </div>
    </div>
  `;
}

function manualSkuOptions() {
  const rows = appState.data?.recommendations?.existing || [];
  if (!rows.length) {
    return `<option value="">暂无SKU</option>`;
  }
  const selectedId =
    appState.selectedManualSkuId ||
    Number(appState.data?.market_tools?.diagnostics?.[0]?.id || rows[0]?.id || 0);
  if (!appState.selectedManualSkuId && selectedId) {
    appState.selectedManualSkuId = selectedId;
  }
  return rows
    .map(
      (item) =>
        `<option value="${item.id}" ${Number(item.id) === Number(selectedId) ? "selected" : ""}>${escapeHtml(item.brand)} - ${escapeHtml(item.product_name)}</option>`,
    )
    .join("");
}

function selectedManualSku() {
  return (appState.data?.recommendations?.existing || []).find((item) => Number(item.id) === Number(appState.selectedManualSkuId));
}

function readManualSampleForm() {
  const pricesRaw = document.getElementById("manualSamplePrices")?.value || "";
  const urlsRaw = document.getElementById("manualSampleUrls")?.value || "";
  return {
    source_platform: document.getElementById("manualSamplePlatform")?.value || "淘宝",
    sample_prices: pricesRaw
      .split(/[\n,，;；\s]+/)
      .map((item) => Number(item))
      .filter((value) => Number.isFinite(value) && value > 0),
    source_urls: urlsRaw
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean),
    note: document.getElementById("manualSampleNote")?.value || "",
  };
}

async function saveManualSampleOverride() {
  const sku = selectedManualSku();
  if (!sku) {
    throw new Error("请先选择一个SKU。");
  }
  const payload = readManualSampleForm();
  return fetchJson(`/api/skus/${sku.id}/manual-market-override`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

async function deleteManualSampleOverride() {
  const sku = selectedManualSku();
  if (!sku) {
    throw new Error("请先选择一个SKU。");
  }
  return fetchJson(`/api/skus/${sku.id}/manual-market-override`, { method: "DELETE" });
}

function renderMarketDiagnosticsPanel() {
  const container = document.getElementById("marketDiagnosticsPanel");
  if (!container) return;
  const diagnostics = (appState.data?.market_tools?.diagnostics || [])
    .slice()
    .sort((a, b) => {
      const statusGap = marketStatusPriority(a.market_sample_status || "待更新") - marketStatusPriority(b.market_sample_status || "待更新");
      if (statusGap) return statusGap;
      return Number(a.taobao_sample_count || 0) - Number(b.taobao_sample_count || 0);
    });
  const allRows = appState.data?.recommendations?.existing || [];
  const statusCounts = diagnostics.reduce((acc, item) => {
    const key = item.market_sample_status || "待更新";
    acc[key] = (acc[key] || 0) + 1;
    return acc;
  }, {});
  const selectedSku = selectedManualSku() || diagnostics[0] || allRows[0];
  const selectedPlatform = selectedSku?.manual_sample_source_platform || "淘宝";
  const matchedTitles = selectedSku?.market_matched_titles || [];
  const queryLogs = selectedSku?.market_query_logs || [];
  const blocked = selectedSku?.market_blocked_platforms || [];
  const manualPrices = selectedSku?.manual_sample_prices || [];
  const manualUrls = selectedSku?.manual_sample_urls || [];
  const fallbackNote = selectedSku?.market_fallback_note || "";
  const platformOptions = ["淘宝", "天猫", "京东", "小红书", "其他"]
    .map((label) => `<option value="${escapeHtml(label)}" ${label === selectedPlatform ? "selected" : ""}>${escapeHtml(label)}</option>`)
    .join("");

  container.innerHTML = `
    <details class="card stack diagnostics-details">
      <summary>抓取诊断与人工补样本</summary>
      <p class="muted">先排查抓不到淘宝样本的 SKU，再决定是继续补抓、接受跨平台替代，还是直接人工补样本。Cookie 仍然只是可选兜底，不是默认前提。</p>
      <div class="selection-summary">
        ${Object.entries(statusCounts)
          .sort((a, b) => marketStatusPriority(a[0]) - marketStatusPriority(b[0]))
          .map(
            ([status, count]) =>
              `<div class="score-chip"><div class="label">${escapeHtml(status)}</div><div class="value">${count}</div></div>`,
          )
          .join("") || `<div class="muted">当前没有需要重点排查的样本状态。</div>`}
      </div>
      <div class="diagnostics-grid">
        <div class="stack">
          <h3>问题 SKU 列表</h3>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>商品</th>
                  <th>状态</th>
                  <th>样本</th>
                  <th>说明</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                ${
                  diagnostics.length
                    ? diagnostics
                        .map(
                          (item) => `
                      <tr>
                        <td><strong>${escapeHtml(item.brand)}</strong><br />${escapeHtml(item.product_name)}</td>
                        <td><span class="badge ${sampleQualityBadgeClass(item.market_sample_quality)}">${escapeHtml(item.market_sample_status || "待更新")}</span></td>
                        <td>${escapeHtml(item.taobao_sample_count || 0)} 条<br /><span class="muted">${escapeHtml(item.market_source_mode || "-")}</span></td>
                        <td class="muted">${escapeHtml(item.market_diagnostic_summary || "暂无诊断说明")}</td>
                        <td><button type="button" class="ghost-btn small-btn" data-action="select-manual-sku" data-sku-id="${item.id}">补样本</button></td>
                      </tr>
                    `,
                        )
                        .join("")
                    : `<tr><td colspan="5"><div class="empty-state">当前大部分 SKU 已有可用样本，暂时没有重点异常项。</div></td></tr>`
                }
              </tbody>
            </table>
          </div>
        </div>
        <div class="stack">
          <h3>人工补样本</h3>
          <label>
            <span>选择 SKU</span>
            <select id="manualSampleSku">${manualSkuOptions()}</select>
          </label>
          <label>
            <span>来源平台</span>
            <select id="manualSamplePlatform">${platformOptions}</select>
          </label>
          <label>
            <span>价格样本</span>
            <textarea id="manualSamplePrices" rows="3" placeholder="用逗号或换行分隔，例如：18.9, 19.5, 20.0">${escapeHtml(manualPrices.join(", "))}</textarea>
          </label>
          <label>
            <span>样本链接（可选）</span>
            <textarea id="manualSampleUrls" rows="3" placeholder="每行一个链接">${escapeHtml(manualUrls.join("\n"))}</textarea>
          </label>
          <label>
            <span>备注</span>
            <textarea id="manualSampleNote" rows="2" placeholder="例如：公开页一直无结果，改为人工补录。">${escapeHtml(selectedSku?.manual_sample_note || "")}</textarea>
          </label>
          <div class="inline-actions">
            <button type="button" class="primary-btn" id="saveManualSampleBtn">保存人工样本</button>
            <button type="button" class="ghost-btn" id="deleteManualSampleBtn">删除人工样本</button>
          </div>
          <div class="detail-card">
            <strong>当前诊断</strong>
            <p class="muted">${escapeHtml(selectedSku?.market_diagnostic_summary || "暂无诊断说明")}</p>
            <p class="muted">拦截平台：${escapeHtml(blocked.join(" / ") || "无")}</p>
            <p class="muted">匹配标题：${escapeHtml(matchedTitles.join(" / ") || "暂无")}</p>
            ${fallbackNote ? `<p class="muted">替代说明：${escapeHtml(fallbackNote)}</p>` : ""}
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>平台</th>
                    <th>查询词</th>
                    <th>原始结果</th>
                    <th>入样本</th>
                    <th>层级</th>
                  </tr>
                </thead>
                <tbody>
                  ${
                    queryLogs.length
                      ? queryLogs
                          .map(
                            (item) => `
                        <tr>
                          <td>${escapeHtml(item.platform)}</td>
                          <td>${escapeHtml(item.query)}</td>
                          <td>${escapeHtml(item.raw_count || 0)}</td>
                          <td>${escapeHtml(item.selected_count || 0)}</td>
                          <td>${escapeHtml(item.quality || "-")}</td>
                        </tr>
                      `,
                          )
                          .join("")
                      : `<tr><td colspan="5"><div class="empty-state">还没有抓取诊断日志。</div></td></tr>`
                  }
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </details>
  `;

  document.getElementById("manualSampleSku")?.addEventListener("change", async (event) => {
    appState.selectedManualSkuId = Number(event.target.value || 0);
    renderMarketDiagnosticsPanel();
  });
  document.getElementById("saveManualSampleBtn")?.addEventListener("click", async () => {
    try {
      await saveManualSampleOverride();
      await refreshState();
      showBanner("marketRefreshFeedback", "人工补样本已保存，当前 SKU 的市场参考已更新。");
    } catch (error) {
      alert(error.message);
    }
  });
  document.getElementById("deleteManualSampleBtn")?.addEventListener("click", async () => {
    const sku = selectedManualSku();
    if (!sku) return;
    try {
      await deleteManualSampleOverride();
      await refreshState();
      showBanner("marketRefreshFeedback", "人工补样本已删除，系统将恢复使用自动抓取结果。");
    } catch (error) {
      alert(error.message);
    }
  });
  container.querySelectorAll('[data-action="select-manual-sku"]').forEach((button) => {
    button.addEventListener("click", () => {
      appState.selectedManualSkuId = Number(button.dataset.skuId || 0);
      renderMarketDiagnosticsPanel();
    });
  });
}

function simulatorBrandOptions() {
  const brands = Array.from(new Set((appState.data.recommendations.existing || []).map((item) => item.brand).filter(Boolean))).sort((a, b) =>
    a.localeCompare(b, "zh-CN"),
  );
  return [`<option value="">全部品牌</option>`, ...brands.map((brand) => `<option value="${escapeHtml(brand)}">${escapeHtml(brand)}</option>`)].join("");
}

function readPricingSimulatorForm() {
  return {
    brand: document.getElementById("simulatorBrand")?.value || "",
    structural_role: document.getElementById("simulatorRole")?.value || "",
    price_band: document.getElementById("simulatorBand")?.value || "",
    strategy: document.getElementById("simulatorStrategy")?.value || "adjust_by_amount",
    amount: Number(document.getElementById("simulatorAmount")?.value || 0),
  };
}

async function runPricingSimulation() {
  const payload = readPricingSimulatorForm();
  const result = await fetchJson("/api/pricing/simulate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  appState.pricingSimulation = result;
  renderPricingSimulatorPanel();
}

function renderPricingSimulationResult() {
  const result = appState.pricingSimulation;
  if (!result) {
    return `<div class="muted">模拟器默认收起。需要时再展开做批量试算，避免主页面过于拥挤。</div>`;
  }
  const summary = result.summary || {};
  const rows = result.items || [];
  return `
    <div class="stack">
      <div class="selection-summary">
        <div class="score-chip"><div class="label">影响SKU</div><div class="value">${summary.affected_count || 0}</div></div>
        <div class="score-chip"><div class="label">平均毛利率</div><div class="value">${formatPercent(summary.avg_margin_before || 0)} → ${formatPercent(summary.avg_margin_after || 0)}</div></div>
        <div class="score-chip"><div class="label">半年毛利额</div><div class="value">${formatCurrency(summary.total_half_year_profit_before || 0)} → ${formatCurrency(summary.total_half_year_profit_after || 0)}</div></div>
        <div class="score-chip"><div class="label">毛利变化</div><div class="value">${formatCurrency(summary.profit_delta || 0)}</div></div>
      </div>
      <p class="muted">动作变化 ${summary.action_changed_count || 0} 个，分层变化 ${summary.role_changed_count || 0} 个。这里只是模拟，不会改动数据库。</p>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>商品</th>
              <th>售价变化</th>
              <th>毛利变化</th>
              <th>动作变化</th>
              <th>分层变化</th>
            </tr>
          </thead>
          <tbody>
            ${
              rows.length
                ? rows
                    .map(
                      (item) => `
                  <tr>
                    <td><strong>${escapeHtml(item.brand)}</strong><br />${escapeHtml(item.product_name)}</td>
                    <td>${formatCurrency(item.before_price)} → ${formatCurrency(item.after_price)}<br /><span class="muted">${escapeHtml(item.before_price_band)} → ${escapeHtml(item.after_price_band)}</span></td>
                    <td>${formatPercent(item.before_margin)} → ${formatPercent(item.after_margin)}<br /><span class="muted">${formatCurrency(item.before_profit)} → ${formatCurrency(item.after_profit)}</span></td>
                    <td><span class="badge ${actionBadgeClass(item.after_action)}">${escapeHtml(item.before_action)} → ${escapeHtml(item.after_action)}</span></td>
                    <td>${escapeHtml(item.before_role)} → ${escapeHtml(item.after_role)}</td>
                  </tr>
                `,
                    )
                    .join("")
                : `<tr><td colspan="5"><div class="empty-state">当前筛选条件下没有命中 SKU。</div></td></tr>`
            }
          </tbody>
        </table>
      </div>
    </div>
  `;
}

function renderPricingSimulatorPanel() {
  const container = document.getElementById("pricingSimulatorPanel");
  const strategyOptions = (appState.meta.pricing_simulation_strategies || [])
    .map((item) => `<option value="${escapeHtml(item.key)}">${escapeHtml(item.label)}</option>`)
    .join("");
  container.innerHTML = `
    <details class="card stack pricing-details">
      <summary>批量调价模拟器</summary>
      <p class="muted">先选品牌 / 分层 / 价格带，再批量试算。系统会重算毛利、动作和分层，但不会保存到库里。</p>
      <div class="filter-grid compact">
        <label>
          <span>品牌</span>
          <select id="simulatorBrand">${simulatorBrandOptions()}</select>
        </label>
        <label>
          <span>系统分层</span>
          <select id="simulatorRole">
            <option value="">全部分层</option>
            ${(appState.meta.roles || []).map((item) => `<option value="${escapeHtml(item)}">${escapeHtml(item)}</option>`).join("")}
          </select>
        </label>
        <label>
          <span>价格带</span>
          <select id="simulatorBand">
            <option value="">全部价格带</option>
            ${(appState.meta.price_bands || []).map((item) => `<option value="${escapeHtml(item)}">${escapeHtml(item)}</option>`).join("")}
          </select>
        </label>
        <label>
          <span>模拟方式</span>
          <select id="simulatorStrategy">${strategyOptions}</select>
        </label>
        <label>
          <span>加减金额</span>
          <input type="number" id="simulatorAmount" step="0.1" value="-1" />
        </label>
      </div>
      <div class="inline-actions">
        <button type="button" class="primary-btn" id="runPricingSimulationBtn">运行模拟</button>
        <span class="muted">“加减金额”只在“每个SKU统一加减金额”模式下生效。</span>
      </div>
      ${renderPricingSimulationResult()}
    </details>
  `;
  const runButton = document.getElementById("runPricingSimulationBtn");
  if (runButton) {
    runButton.addEventListener("click", async () => {
      try {
        runButton.disabled = true;
        await runPricingSimulation();
      } catch (error) {
        alert(error.message);
      } finally {
        runButton.disabled = false;
      }
    });
  }
}

function renderRecommendations() {
  renderAutoSelectionPanel();
  renderPricingFocusPanel();
  renderPricingSimulatorPanel();
  renderMarketDiagnosticsPanel();

  const skuTable = document.getElementById("skuRecommendationTable");
  const skuRows = appState.data.recommendations.existing;
  skuTable.querySelector("thead").innerHTML = `
    <tr>
      <th>商品</th>
      <th>动作</th>
      <th>当前售价</th>
      <th>淘宝参考</th>
      <th>样本</th>
      <th>建议价区间</th>
      <th>推荐价</th>
      <th>毛利率</th>
      <th>利润贡献</th>
      <th>分层</th>
      <th>详情</th>
    </tr>
  `;
  skuTable.querySelector("tbody").innerHTML = skuRows.length
    ? skuRows
        .map(
          (item) => `
      <tr>
        <td><strong>${escapeHtml(item.brand)}</strong><br />${escapeHtml(item.product_name)}</td>
        <td><span class="badge ${actionBadgeClass(item.action)}">${escapeHtml(item.action)}</span></td>
        <td>${formatCurrency(item.current_price)}</td>
        <td>${item.taobao_avg_price ? formatCurrency(item.taobao_avg_price) : `<span class="muted">${escapeHtml(item.market_snapshot_status || "待更新")}</span>`}</td>
        <td>
          <span class="badge ${sampleQualityBadgeClass(item.market_sample_quality)}">${escapeHtml(item.market_sample_quality || "-")}</span><br />
          <span class="muted">${escapeHtml(item.taobao_sample_count || 0)} 条</span>
        </td>
        <td>${escapeHtml(item.suggested_price_range_label || "-")}</td>
        <td>${formatMaybeCurrency(item.suggested_price)}</td>
        <td>${formatPercent(item.gross_margin)}<br /><span class="muted">目标 ${escapeHtml(item.target_margin_range)}</span></td>
        <td>${formatCurrency(item.half_year_gross_profit)}<br /><span class="muted">${formatPercent(item.profit_contribution_share)}</span></td>
        <td>${escapeHtml(item.structural_role)}<br /><span class="muted">${escapeHtml(item.price_band)}</span></td>
        <td>
          <details class="inline-detail">
            <summary>展开</summary>
            <div class="detail-stack">
              <div class="muted">${escapeHtml(item.reason)}</div>
              <div class="muted">快照：${escapeHtml(item.market_snapshot_status || "待更新")} · ${escapeHtml(item.market_snapshot_at || "暂无")}</div>
              <div class="muted">区间 ${formatPriceRange(item.taobao_min_price, item.taobao_max_price)} · 热度 ${escapeHtml(item.online_heat_score || 0)} · ${escapeHtml(item.price_disorder_label)}</div>
              ${item.market_fallback_note ? `<div class="muted">说明：${escapeHtml(item.market_fallback_note)}</div>` : ""}
              ${renderReasonList(item.recommendation_basis)}
            </div>
          </details>
        </td>
      </tr>
    `,
        )
        .join("")
    : `<tr><td colspan="11"><div class="empty-state">暂无建议</div></td></tr>`;

  const candidateTable = document.getElementById("candidateRecommendationTable");
  const candidateRows = appState.data.recommendations.candidate;
  candidateTable.querySelector("thead").innerHTML = `
    <tr>
      <th>商品</th>
      <th>建议分层</th>
      <th>建议售价</th>
      <th>预计毛利率</th>
      <th>综合评分</th>
      <th>结论</th>
    </tr>
  `;
  candidateTable.querySelector("tbody").innerHTML = candidateRows.length
    ? candidateRows
        .map(
          (item) => `
      <tr>
        <td><strong>${escapeHtml(item.brand)}</strong><br />${escapeHtml(item.product_name)}</td>
        <td>${escapeHtml(item.proposed_role)}</td>
        <td>${formatCurrency(item.suggested_price)}</td>
        <td>${formatPercent(item.expected_margin)}</td>
        <td>${escapeHtml(item.recommendation_score)}</td>
        <td><span class="badge ${actionBadgeClass(item.suggestion_status)}">${escapeHtml(item.suggestion_status)}</span></td>
      </tr>
    `,
        )
        .join("")
    : `<tr><td colspan="6"><div class="empty-state">暂无候选新品建议</div></td></tr>`;
}

function renderImportPreview() {
  const container = document.getElementById("importPreview");
  const preview = appState.importPreview;
  if (!preview) {
    container.innerHTML = "";
    return;
  }
  const kind = preview.kind;
  const fieldDefs = appState.meta.import_fields[kind];
  const mappingHtml = fieldDefs
    .map((field) => {
      const options = ["", ...preview.headers]
        .map((header) => {
          const selected = preview.mapping[field.key] === header ? "selected" : "";
          const label = header || "不映射";
          return `<option value="${escapeHtml(header)}" ${selected}>${escapeHtml(label)}</option>`;
        })
        .join("");
      return `
        <label>
          <span>${escapeHtml(field.label)} ${field.required ? "*" : ""}</span>
          <select data-map-key="${field.key}">${options}</select>
        </label>
      `;
    })
    .join("");
  const sampleTable = preview.sample_rows.length
    ? `
      <div class="table-wrap">
        <table>
          <thead>
            <tr>${preview.headers.map((header) => `<th>${escapeHtml(header)}</th>`).join("")}</tr>
          </thead>
          <tbody>
            ${preview.sample_rows
              .map(
                (row) => `
              <tr>${preview.headers.map((header) => `<td>${escapeHtml(row[header] ?? "")}</td>`).join("")}</tr>
            `,
              )
              .join("")}
          </tbody>
        </table>
      </div>
    `
    : `<div class="empty-state">没有检测到可导入行。</div>`;

  container.innerHTML = `
    <div class="card stack" style="margin-top:18px;">
      <div class="inline-actions">
        <h3>导入预览</h3>
        <div id="previewMissingBadge"></div>
        <span class="badge gray">共 ${preview.row_count} 行</span>
      </div>
      <div class="mapping-grid">${mappingHtml}</div>
      <div class="preview-grid">
        <article class="card stack">
          <h3>重复提醒</h3>
          ${
            preview.duplicate_skus.length
              ? `<ul class="risk-list">${preview.duplicate_skus
                  .map((item) => `<li>${escapeHtml(item.sku_code)} 已存在，对应商品：${escapeHtml(item.existing_name)}</li>`)
                  .join("")}</ul>`
              : `<p class="muted">暂无条码重复。</p>`
          }
          ${
            preview.similar_names.length
              ? `<ul class="risk-list">${preview.similar_names
                  .map(
                    (item) =>
                      `<li>${escapeHtml(item.incoming_name)} 与在售 ${escapeHtml(item.existing_name)}（${escapeHtml(item.existing_sku)}）相似。</li>`,
                  )
                  .join("")}</ul>`
              : `<p class="muted">暂无同名高相似提醒。</p>`
          }
        </article>
        <article class="card stack">
          <h3>样例数据</h3>
          ${sampleTable}
        </article>
      </div>
      <div class="inline-actions">
        <button class="primary-btn" id="commitImportBtn">确认导入</button>
      </div>
    </div>
  `;

  const commitButton = document.getElementById("commitImportBtn");
  const validateMapping = () => {
    const requiredFields = fieldDefs.filter((field) => field.required);
    const missingRequired = requiredFields.filter((field) => {
      const select = container.querySelector(`[data-map-key="${field.key}"]`);
      return !select?.value;
    });
    const badge = document.getElementById("previewMissingBadge");
    if (!missingRequired.length) {
      badge.innerHTML = `<div class="badge green">必填字段已全部识别</div>`;
      commitButton.disabled = false;
      return;
    }
    badge.innerHTML = `<div class="badge red">缺失必填字段：${escapeHtml(missingRequired.map((field) => field.label).join(" / "))}</div>`;
    commitButton.disabled = true;
  };
  container.querySelectorAll("[data-map-key]").forEach((select) => {
    select.addEventListener("change", validateMapping);
  });
  validateMapping();
  if (commitButton) {
    commitButton.addEventListener("click", async () => {
      const mapping = {};
      container.querySelectorAll("[data-map-key]").forEach((select) => {
        mapping[select.dataset.mapKey] = select.value;
      });
      try {
        commitButton.disabled = true;
        const result = await fetchJson("/api/import/commit", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token: preview.token, mapping }),
        });
        alert(`导入完成：新增 ${result.inserted}，更新 ${result.updated || 0}。`);
        appState.importPreview = null;
        container.innerHTML = "";
        await refreshState();
      } catch (error) {
        alert(error.message);
        commitButton.disabled = false;
      }
    });
  }
}

function fillCandidateForm(candidate) {
  document.getElementById("candidateId").value = candidate?.id || "";
  document.getElementById("candidateBrand").value = candidate?.brand || "";
  document.getElementById("candidateName").value = candidate?.product_name || "";
  document.getElementById("candidateSpec").value = candidate?.spec_text || "";
  document.getElementById("candidateEfficacy").value = candidate?.efficacy_tags || "其他";
  document.getElementById("candidateOnlinePrice").value = candidate?.online_reference_price || "";
  document.getElementById("candidateCost").value = candidate?.expected_purchase_price || "";
  document.getElementById("candidatePlatform").value = candidate?.source_platform || "其他";
  document.getElementById("candidateHeat").value = candidate?.heat_score || "";
  document.getElementById("candidateTargetGroup").value = candidate?.target_group || "成人";
  document.getElementById("candidatePromoType").value = candidate?.promo_type || "常规款";
  document.getElementById("candidateUrl").value = candidate?.product_url || "";
  document.getElementById("candidateReplaceSku").value = candidate?.intended_replace_sku || "";
  document.getElementById("candidateFluoride").value = String(candidate?.fluoride || 0);
  document.getElementById("candidateMustKeep").value = String(candidate?.must_keep || 0);
  document.getElementById("candidateDifferentiation").value = candidate?.differentiation || "";
  document.getElementById("candidateSubstituteRelation").value = candidate?.substitute_relation || "";
  document.getElementById("candidateNotes").value = candidate?.notes || "";
  document.getElementById("candidateSubmitBtn").textContent = candidate ? "更新候选新品" : "保存候选新品";
}

function readCandidateForm() {
  return {
    brand: document.getElementById("candidateBrand").value,
    product_name: document.getElementById("candidateName").value,
    spec_text: document.getElementById("candidateSpec").value,
    efficacy_tags: document.getElementById("candidateEfficacy").value,
    online_reference_price: Number(document.getElementById("candidateOnlinePrice").value || 0),
    expected_purchase_price: Number(document.getElementById("candidateCost").value || 0),
    source_platform: document.getElementById("candidatePlatform").value,
    heat_score: Number(document.getElementById("candidateHeat").value || 0),
    target_group: document.getElementById("candidateTargetGroup").value,
    promo_type: document.getElementById("candidatePromoType").value,
    product_url: document.getElementById("candidateUrl").value,
    intended_replace_sku: document.getElementById("candidateReplaceSku").value,
    fluoride: Number(document.getElementById("candidateFluoride").value),
    must_keep: Number(document.getElementById("candidateMustKeep").value),
    differentiation: document.getElementById("candidateDifferentiation").value,
    substitute_relation: document.getElementById("candidateSubstituteRelation").value,
    notes: document.getElementById("candidateNotes").value,
  };
}

function readCrawlerCookies() {
  return {
    jd: document.getElementById("cookieJd").value.trim(),
    tmall: document.getElementById("cookieTmall").value.trim(),
    xiaohongshu: document.getElementById("cookieXiaohongshu").value.trim(),
    taobao: document.getElementById("cookieTaobao").value.trim(),
  };
}

function readCrawlerPayload() {
  const selectedPlatforms = Array.from(document.querySelectorAll("#crawlPlatforms input:checked")).map((node) => node.value);
  const keywordInput = document.getElementById("crawlKeyword").value || "牙膏";
  const keywords = keywordInput
    .split(/[\n,，]+/)
    .map((item) => item.trim())
    .filter(Boolean);
  return {
    keyword: keywords[0] || "牙膏",
    keywords,
    limit_per_platform: Number(document.getElementById("crawlLimit").value || 20),
    platforms: selectedPlatforms.length ? selectedPlatforms : [...(appState.meta.crawler_default_platforms || [])],
    cookies: readCrawlerCookies(),
  };
}

function buildBrowserHelperScript(platform) {
  const explicitPlatform = platform || "";
  return `(function(){const explicitPlatform=${JSON.stringify(explicitPlatform)};const text=(value)=>String(value||'').replace(/\\s+/g,' ').trim();const detectPlatform=()=>{const host=location.hostname.toLowerCase();if(host.includes('taobao'))return 'taobao';if(host.includes('tmall'))return 'tmall';if(host.includes('jd.com'))return 'jd';if(host.includes('xiaohongshu'))return 'xiaohongshu';if(host.includes('douyin'))return 'douyin';return explicitPlatform||'';};const visible=(node)=>{if(!node||!(node instanceof HTMLElement))return false;const rect=node.getBoundingClientRect();return rect.width>0&&rect.height>0;};const priceFrom=(value)=>{const textValue=text(value);const match=textValue.match(/(?:¥|￥)\\s*(\\d+(?:\\.\\d+)?)/)||textValue.match(/(\\d+(?:\\.\\d+)?)\\s*元/);return match?Number(match[1]):0;};const salesFrom=(value)=>{const textValue=text(value);const match=textValue.match(/(?:月销|销量|已售|售出|付款|热度)[^\\n]{0,18}/);return match?match[0].trim():'';};const titleFrom=(anchor,scopeText)=>{const candidates=[anchor?.getAttribute?.('title'),anchor?.innerText,anchor?.textContent,...scopeText.split('\\n')].map(text).filter(Boolean);return candidates.find((item)=>/牙膏/.test(item)&&item.length>=4)||'';};const anchors=Array.from(document.querySelectorAll('a[href]')).filter((anchor)=>visible(anchor)&&/牙膏/.test(text(anchor.innerText||anchor.textContent||anchor.getAttribute('title'))));const seen=new Set();const items=[];for(const anchor of anchors){let container=anchor;for(let depth=0;depth<4&&container?.parentElement;depth+=1){container=container.parentElement;}const scope=container||anchor;const scopeText=text(scope?.innerText||scope?.textContent||anchor.innerText||anchor.textContent);const title=titleFrom(anchor,scopeText);const price=priceFrom(scopeText);const salesText=salesFrom(scopeText);const url=anchor.href||location.href;const key=[title,price,url].join('|');if(!title||seen.has(key))continue;seen.add(key);items.push({platform:detectPlatform(),title,url,price,sales_text:salesText,rank:items.length+1});if(items.length>=30)break;}const payload={platform:detectPlatform(),source_url:location.href,captured_at:new Date().toISOString(),items};const output=JSON.stringify(payload,null,2);const promptCopy=()=>window.prompt('复制下面的 JSON，回到本地工具粘贴导入：',output);if(!items.length){alert('当前页面没有识别到可用的牙膏商品卡片，请滚动页面后重试，或改用批量粘贴采集。');return;}if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(output).then(()=>alert('已复制浏览器采集 JSON，请回到本地工具粘贴导入。')).catch(promptCopy);}else{promptCopy();}})();`;
}

async function copyBrowserHelperScript() {
  const platform = document.getElementById("browserCapturePlatform")?.value || "";
  const script = buildBrowserHelperScript(platform);
  await navigator.clipboard.writeText(script);
}

function readBrowserCapturePayload() {
  return {
    platform: document.getElementById("browserCapturePlatform")?.value || "",
    source_url: document.getElementById("browserCaptureSourceUrl")?.value || "",
    keyword: document.getElementById("crawlKeyword")?.value || "牙膏",
    capture_text: document.getElementById("browserCaptureInput")?.value || "",
  };
}

function readPasteCapturePayload() {
  return {
    platform: document.getElementById("pasteCapturePlatform")?.value || "",
    keyword: document.getElementById("pasteCaptureKeyword")?.value || "牙膏",
    raw_text: document.getElementById("pasteCaptureInput")?.value || "",
  };
}

async function refreshMarketSnapshots({ force = false, silent = false } = {}) {
  const statusId = "marketRefreshFeedback";
  if (!silent) {
    showBanner(statusId, "正在慢速刷新市场快照，请稍等...");
  }
  try {
    appState.autoMarketRefreshAttempted = true;
    const previousBand = appState.selectedPriceBand;
    const previousDetail = appState.selectedDashboardDetail ? { ...appState.selectedDashboardDetail } : null;
    const result = await fetchJson("/api/market/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        force,
        cookies: readCrawlerCookies(),
      }),
    });
    appState.selectedPriceBand = previousBand;
    appState.selectedDashboardDetail = previousDetail;
    await refreshState();
    const errorCount = Object.keys(result.errors || {}).length;
    showBanner(
      statusId,
      `市场快照刷新完成：更新 ${result.refreshed || 0} 个，拿到样本 ${result.with_samples || 0} 个，无样本 ${result.without_samples || 0} 个，跳过 ${result.skipped || 0} 个${errorCount ? `，失败 ${errorCount} 个` : ""}。`,
    );
  } catch (error) {
    showBanner(statusId, `市场快照刷新失败：${error.message}`);
    if (!silent) {
      alert(error.message);
    }
  }
}

function hasPendingMarketSnapshots() {
  return (appState.data?.dashboard?.summary?.market_pending_count || 0) > 0;
}

function maybeAutoRefreshMarket(targetId) {
  if (appState.autoMarketRefreshAttempted) return;
  if (!appState.data?.skus?.length) return;
  if (!hasPendingMarketSnapshots()) return;
  if (!["dashboardModule", "recommendationModule"].includes(targetId)) return;
  refreshMarketSnapshots({ silent: true });
}

async function requestPricePreview(skuId, price) {
  const result = await fetchJson(`/api/skus/${skuId}/price-preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ current_price: price }),
  });
  appState.pricePreviewCache.set(Number(skuId), result);
  renderDashboard();
}

async function saveSkuPrice(skuId, price) {
  const previousBand = appState.selectedPriceBand;
  const previousDetail = appState.selectedDashboardDetail ? { ...appState.selectedDashboardDetail } : null;
  await fetchJson(`/api/skus/${skuId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ current_price: price }),
  });
  appState.pricePreviewCache.delete(Number(skuId));
  appState.selectedPriceBand = previousBand;
  appState.selectedDashboardDetail = previousDetail;
  await refreshState();
  showBanner("marketRefreshFeedback", "售价已保存，相关毛利率、价格带和建议动作已同步刷新。");
}

async function saveSkuPrice(skuId, price) {
  const previousBand = appState.selectedPriceBand;
  const previousDetail = appState.selectedDashboardDetail ? { ...appState.selectedDashboardDetail } : null;
  const activeBrand = previousDetail?.type === "brand" ? previousDetail.key : "";
  await fetchJson(`/api/skus/${skuId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ current_price: price }),
  });
  appState.pricePreviewCache.delete(Number(skuId));
  appState.selectedPriceBand = previousBand;
  appState.selectedDashboardDetail = previousDetail;
  if (activeBrand) {
    invalidateBrandRecommendationCache(activeBrand);
  }
  await refreshState();
  if (activeBrand) {
    await reloadActiveBrandRecommendations({ force: true });
  }
  showBanner("marketRefreshFeedback", "售价已保存，相关毛利率、价格带和建议动作已同步刷新。");
}

function bindBrandDashboardEnhancements() {
  const dashboardPanel = document.getElementById("dashboardPanel");
  dashboardPanel.addEventListener("click", async (event) => {
    const detailButton = event.target.closest("[data-dashboard-type][data-dashboard-key]");
    if (detailButton?.dataset.dashboardType === "brand") {
      try {
        await loadBrandRecommendations(detailButton.dataset.dashboardKey);
      } catch (error) {
        showBanner("marketRefreshFeedback", `品牌推荐加载失败：${error.message}`);
      }
      return;
    }

    const refreshButton = event.target.closest('[data-action="refresh-brand-recommendations"]');
    if (refreshButton) {
      try {
        invalidateBrandRecommendationCache(refreshButton.dataset.brand);
        await loadBrandRecommendations(refreshButton.dataset.brand, { force: true });
      } catch (error) {
        showBanner("marketRefreshFeedback", `品牌推荐刷新失败：${error.message}`);
      }
      return;
    }

    const openCandidateButton = event.target.closest('[data-action="open-brand-candidate"]');
    if (openCandidateButton) {
      const brand = openCandidateButton.dataset.brand || "";
      const hitIndex = Number(openCandidateButton.dataset.hitIndex || -1);
      const entry = getBrandRecommendationEntry(brand);
      const target = entry?.data?.missing_brand_hits?.[hitIndex];
      if (target) {
        fillCandidateForm(target);
        setModule("candidateModule");
      }
      return;
    }

    const browserCaptureButton = event.target.closest('[data-action="go-browser-capture"]');
    if (browserCaptureButton) {
      const brand = browserCaptureButton.dataset.brand || "";
      seedBrandCaptureKeywords(brand);
      setModule("candidateModule");
      return;
    }

    const pasteCaptureButton = event.target.closest('[data-action="go-paste-capture"]');
    if (pasteCaptureButton) {
      const brand = pasteCaptureButton.dataset.brand || "";
      seedBrandCaptureKeywords(brand);
      setModule("candidateModule");
    }
  });
}

function bindEvents() {
  document.getElementById("importForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const fileInput = document.getElementById("importFile");
    if (!fileInput.files.length) {
      alert("请选择要导入的文件。");
      return;
    }
    const formData = new FormData();
    formData.append("kind", document.getElementById("importKind").value);
    formData.append("file", fileInput.files[0]);
    try {
      appState.importPreview = await fetchJson("/api/import/preview", {
        method: "POST",
        body: formData,
      });
      renderImportPreview();
    } catch (error) {
      alert(error.message);
    }
  });

  ["skuSearch", "skuBrandFilter", "skuEfficacyFilter", "skuPriceBandFilter", "skuActionFilter"].forEach((id) => {
    document.getElementById(id).addEventListener("input", renderSkuTable);
    document.getElementById(id).addEventListener("change", renderSkuTable);
  });

  document.getElementById("candidateForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const candidateId = document.getElementById("candidateId").value;
    const method = candidateId ? "PUT" : "POST";
    const url = candidateId ? `/api/candidates/${candidateId}` : "/api/candidates";
    try {
      await fetchJson(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(readCandidateForm()),
      });
      fillCandidateForm(null);
      appState.comparisonCache.clear();
      await refreshState();
    } catch (error) {
      alert(error.message);
    }
  });

  document.getElementById("candidateResetBtn").addEventListener("click", () => fillCandidateForm(null));

  document.getElementById("crawlBtn").addEventListener("click", async () => {
    const payload = readCrawlerPayload();
    const status = document.getElementById("crawlStatus");
    status.textContent = "正在慢速抓取并整理候选池，请稍等...";
    try {
      appState.crawlResult = await fetchJson("/api/crawl/hot-products", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      status.textContent = `抓取完成：已按多关键词慢速轮询，整理出 ${appState.crawlResult.candidate_payload_count || 0} 个候选，新增 ${appState.crawlResult.inserted || 0} 个。`;
      renderCrawlPreview();
      appState.comparisonCache.clear();
      await refreshState();
      setModule("candidateModule");
    } catch (error) {
      status.textContent = "抓取失败，请检查网络或补充对应平台 Cookie。";
      alert(error.message);
    }
  });

  document.getElementById("copyBrowserScriptBtn").addEventListener("click", async () => {
    const status = document.getElementById("browserCaptureStatus");
    try {
      await copyBrowserHelperScript();
      status.textContent = "辅助脚本已复制。去目标平台页面打开控制台运行，然后把输出 JSON 粘贴回这里。";
    } catch (error) {
      status.textContent = "复制失败，请手动重试。";
      alert(error.message);
    }
  });

  document.getElementById("importBrowserCaptureBtn").addEventListener("click", async () => {
    const status = document.getElementById("browserCaptureStatus");
    status.textContent = "正在导入浏览器辅助采集结果...";
    try {
      appState.crawlResult = await fetchJson("/api/crawl/browser-capture", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(readBrowserCapturePayload()),
      });
      status.textContent = `浏览器辅助采集已导入：整理出 ${appState.crawlResult.candidate_payload_count || 0} 个候选，新增 ${appState.crawlResult.inserted || 0} 个。`;
      renderCrawlPreview();
      appState.comparisonCache.clear();
      await refreshState();
      setModule("candidateModule");
    } catch (error) {
      status.textContent = "浏览器辅助采集导入失败，请检查粘贴内容。";
      alert(error.message);
    }
  });

  document.getElementById("importPasteCaptureBtn").addEventListener("click", async () => {
    const status = document.getElementById("pasteCaptureStatus");
    status.textContent = "正在解析批量粘贴内容...";
    try {
      appState.crawlResult = await fetchJson("/api/crawl/paste-candidates", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(readPasteCapturePayload()),
      });
      status.textContent = `批量粘贴已导入：整理出 ${appState.crawlResult.candidate_payload_count || 0} 个候选，新增 ${appState.crawlResult.inserted || 0} 个。`;
      renderCrawlPreview();
      appState.comparisonCache.clear();
      await refreshState();
      setModule("candidateModule");
    } catch (error) {
      status.textContent = "批量粘贴解析失败，请保留商品名和价格后重试。";
      alert(error.message);
    }
  });

  document.getElementById("candidateTable").addEventListener("click", async (event) => {
    const target = event.target.closest("[data-action]");
    if (!target) return;
    const candidateId = Number(target.dataset.id);
    const candidate = appState.data.candidates.find((item) => item.id === candidateId);
    if (target.dataset.action === "edit" && candidate) {
      fillCandidateForm(candidate);
      setModule("candidateModule");
    }
    if (target.dataset.action === "delete" && candidate) {
      if (!window.confirm(`确认删除 ${candidate.brand} - ${candidate.product_name} 吗？`)) return;
      await fetchJson(`/api/candidates/${candidateId}`, { method: "DELETE" });
      appState.comparisonCache.clear();
      await refreshState();
    }
  });

  document.getElementById("comparisonSelector").addEventListener("change", renderComparisonPanel);

  document.getElementById("backupBtn").addEventListener("click", async () => {
    try {
      const result = await fetchJson("/api/backups", { method: "POST" });
      showBanner("backupFeedback", `备份已生成：SQLite ${result.sqlite_path} ｜ JSON ${result.json_path}`);
    } catch (error) {
      alert(error.message);
    }
  });

  document.getElementById("marketRefreshBtn").addEventListener("click", () => refreshMarketSnapshots({ force: true }));

  const dashboardPanel = document.getElementById("dashboardPanel");
  dashboardPanel.addEventListener("click", async (event) => {
    const detailButton = event.target.closest("[data-dashboard-type][data-dashboard-key]");
    if (detailButton) {
      setSelectedDashboardDetail(detailButton.dataset.dashboardType, detailButton.dataset.dashboardKey);
      renderDashboard();
      scrollToBandDetailPanel();
      return;
    }

    const saveButton = event.target.closest('[data-action="save-price"]');
    if (!saveButton) return;
    const skuId = Number(saveButton.dataset.skuId);
    const input = dashboardPanel.querySelector(`.price-edit-input[data-sku-id="${skuId}"]`);
    if (!input) return;
    const price = Number(input.value || 0);
    if (!price) {
      alert("请输入有效售价。");
      return;
    }
    try {
      saveButton.disabled = true;
      await saveSkuPrice(skuId, price);
    } catch (error) {
      alert(error.message);
    } finally {
      saveButton.disabled = false;
    }
  });

  dashboardPanel.addEventListener("input", (event) => {
    const input = event.target.closest(".price-edit-input");
    if (!input) return;
    const skuId = Number(input.dataset.skuId);
    const price = Number(input.value || 0);
    if (!price) return;
    window.clearTimeout(appState.previewTimers.get(skuId));
    const timer = window.setTimeout(() => {
      requestPricePreview(skuId, price).catch((error) => {
        showBanner("marketRefreshFeedback", `改价预览失败：${error.message}`);
      });
    }, 260);
    appState.previewTimers.set(skuId, timer);
  });
}

async function refreshState() {
  appState.pricingSimulation = null;
  appState.data = await fetchJson("/api/state");
  renderSummary();
  renderSkuFilters();
  renderSkuTable();
  renderCandidateTable();
  renderCrawlPreview();
  renderComparisonSelector();
  await renderComparisonPanel();
  renderDashboard();
  renderRecommendations();
}

async function init() {
  try {
    appState.meta = await fetchJson("/api/meta");
    renderMeta();
    renderNav();
    bindEvents();
    bindBrandDashboardEnhancements();
    fillCandidateForm(null);
    await refreshState();
  } catch (error) {
    document.body.innerHTML = `<div class="app-shell"><div class="card"><h1>启动失败</h1><p>${escapeHtml(error.message)}</p></div></div>`;
  }
}

init();
