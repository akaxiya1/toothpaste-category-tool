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

function formatCurrency(value) {
  const number = Number(value || 0);
  return `¥${number.toFixed(2)}`;
}

function actionBadgeClass(action) {
  if (["建议上新", "建议替换现有SKU", "优先上新", "优先替换", "建议替换上新"].includes(action)) return "green";
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

function sourceMethodLabel(method) {
  if (method === "browser_assisted") return "浏览器辅助采集";
  if (method === "bulk_paste") return "批量粘贴采集";
  return "平台直连抓取";
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

function roundTo(value, digits = 2) {
  const factor = 10 ** digits;
  return Math.round(Number(value || 0) * factor) / factor;
}

function getAutoSelection() {
  return appState.data?.recommendations?.auto_selection || { selected: [], waitlist: [], summary: {} };
}

function addDaysLabel(days) {
  const date = new Date();
  date.setDate(date.getDate() + Number(days || 0));
  return `${date.getMonth() + 1}/${date.getDate()}`;
}

function estimateCandidateOrderQty(item) {
  const role = String(item?.proposed_role || "");
  const heat = Number(item?.heat_score || 0);
  let qty = 10;
  if (role.includes("引流")) qty = 24;
  else if (role.includes("常规") || role.includes("主销")) qty = 16;
  else if (role.includes("利润")) qty = 10;
  else if (role.includes("儿童") || role.includes("补位")) qty = 8;
  if (heat >= 85) qty += 4;
  if (heat < 50) qty -= 2;
  if ((item?.replacement_targets || []).length) qty += 2;
  return Math.max(4, Math.round(qty / 2) * 2);
}

function estimateReviewDays(item, kind) {
  if (kind === "candidate") {
    if ((item?.replacement_targets || []).length) return 14;
    if (String(item?.proposed_role || "").includes("引流")) return 7;
    if (Number(item?.heat_score || 0) >= 80) return 10;
    return 21;
  }
  if (item?.action === "建议低价引流") return 7;
  if (item?.action === "建议调整售价") return 14;
  if (item?.action === "建议利润定价") return 21;
  return 30;
}

function firstNonEmpty(items = []) {
  return items.find((item) => String(item || "").trim()) || "";
}

function candidatePrimaryReason(item) {
  return firstNonEmpty(item?.auto_select_reasons) || item?.differentiation || "适合纳入本期候选。";
}

function buildProcurementActions() {
  const existingRows = appState.data?.recommendations?.existing || [];
  const selectedCandidates = getAutoSelection().selected || [];
  const actions = [];

  selectedCandidates.forEach((item) => {
    const orderQty = estimateCandidateOrderQty(item);
    const unitCost = Number(item.expected_purchase_price || 0);
    const reviewDays = estimateReviewDays(item, "candidate");
    const action = String(item.auto_pick_decision || "").includes("替换") ? "建议替换上新" : "建议上新";
    actions.push({
      kind: "candidate",
      priority: action === "建议替换上新" ? 0 : 1,
      action,
      brand: item.brand,
      product_name: item.product_name,
      spec_text: item.spec_text || "-",
      suggested_price: Number(item.suggested_price || 0),
      current_price: 0,
      margin: Number(item.expected_margin || 0),
      order_qty: orderQty,
      budget: roundTo(unitCost * orderQty, 2),
      expected_profit: roundTo(Math.max(0, (Number(item.suggested_price || 0) - unitCost) * orderQty), 2),
      replace_targets: (item.replacement_targets || []).join(" / "),
      review_days: reviewDays,
      review_date_label: addDaysLabel(reviewDays),
      reason: candidatePrimaryReason(item),
      role: item.proposed_role || "-",
      heat_score: Number(item.heat_score || 0),
    });
  });

  existingRows
    .filter((item) => item.action && item.action !== "建议维持常规价")
    .forEach((item) => {
      const reviewDays = estimateReviewDays(item, "existing");
      actions.push({
        kind: "existing",
        priority:
          item.action === "建议下架" ? 2 : item.action === "建议低价引流" ? 3 : item.action === "建议调整售价" ? 4 : 5,
        action: item.action,
        brand: item.brand,
        product_name: item.product_name,
        spec_text: item.spec_text || "-",
        suggested_price: Number(item.suggested_price || 0),
        current_price: Number(item.current_price || 0),
        margin: Number(item.gross_margin || 0),
        order_qty: 0,
        budget: 0,
        expected_profit: Number(item.half_year_gross_profit || 0),
        replace_targets: "",
        review_days: reviewDays,
        review_date_label: addDaysLabel(reviewDays),
        reason: firstNonEmpty(item.recommendation_basis) || item.reason || "建议继续跟踪当前 SKU。",
        role: item.structural_role || "-",
        heat_score: Number(item.online_heat_score || 0),
      });
    });

  return actions.sort((a, b) => {
    if (a.priority !== b.priority) return a.priority - b.priority;
    return `${a.brand}${a.product_name}`.localeCompare(`${b.brand}${b.product_name}`, "zh-CN");
  });
}

function deriveBrandOpportunities() {
  const brandDistribution = appState.data?.dashboard?.brand_distribution || [];
  const candidateRows = appState.data?.recommendations?.candidate || [];
  const totalSkus = Number(appState.data?.dashboard?.summary?.sku_count || 0);
  const candidateMap = new Map();

  candidateRows.forEach((item) => {
    const brand = String(item.brand || "").trim();
    if (!brand) return;
    if (!candidateMap.has(brand)) candidateMap.set(brand, []);
    candidateMap.get(brand).push(item);
  });

  const opportunities = brandDistribution
    .map((row) => {
      const topCandidate = (candidateMap.get(row.brand) || [])
        .slice()
        .sort((a, b) => Number(b.recommendation_score || 0) - Number(a.recommendation_score || 0))[0];
      const sharePenalty = totalSkus ? (row.sku_count / totalSkus) * 10 : 0;
      if (!topCandidate && row.sku_count > 1) return null;
      if (topCandidate) {
        return {
          brand: row.brand,
          label: row.sku_count <= 1 ? "同品牌补款" : "同品牌爆款补位",
          reason:
            row.sku_count <= 1
              ? `当前只上架 ${row.sku_count} 款，建议优先看 ${topCandidate.product_name}。`
              : `该品牌还有门店未上的高热度候选 ${topCandidate.product_name}。`,
          focus: `${topCandidate.efficacy_tags || "-"} / ${topCandidate.spec_text || "-"}`,
          score: Number(topCandidate.recommendation_score || 0) + (row.sku_count <= 1 ? 12 : 0) - sharePenalty,
        };
      }
      return {
        brand: row.brand,
        label: "待补抓品牌机会",
        reason: `当前只上架 ${row.sku_count} 款，建议点击品牌集中度补抓同品牌缺失爆款。`,
        focus: "从品牌集中度进入查看",
        score: 45 - sharePenalty,
      };
    })
    .filter(Boolean);

  candidateRows
    .filter((item) => !brandDistribution.some((row) => row.brand === item.brand))
    .slice()
    .sort((a, b) => Number(b.recommendation_score || 0) - Number(a.recommendation_score || 0))
    .slice(0, 2)
    .forEach((item) => {
      opportunities.push({
        brand: item.brand,
        label: "新品牌机会",
        reason: `门店还没有这个品牌，可评估 ${item.product_name} 是否适合做新增品牌切入。`,
        focus: `${item.efficacy_tags || "-"} / ${item.spec_text || "-"}`,
        score: Number(item.recommendation_score || 0) - 5,
      });
    });

  return opportunities.sort((a, b) => b.score - a.score).slice(0, 5);
}

function deriveRiskAlerts(actions) {
  const alerts = [];
  const dashboard = appState.data?.dashboard || {};
  const diagnostics = appState.data?.market_tools?.diagnostics || [];

  (dashboard.structure_gaps || []).slice(0, 3).forEach((item) => {
    alerts.push({ label: "结构缺口", text: item });
  });

  if (Number(dashboard.summary?.market_pending_count || 0) > 0) {
    alerts.push({
      label: "市场样本待补",
      text: `还有 ${dashboard.summary.market_pending_count} 个 SKU 没拿到可靠市场样本，建议优先刷新市场快照。`,
    });
  }

  const blockedCount = diagnostics.filter((item) => String(item.market_sample_status || "").includes("拦截")).length;
  if (blockedCount > 0) {
    alerts.push({
      label: "抓取拦截",
      text: `${blockedCount} 个 SKU 抓样本时被拦截，建议改走浏览器辅助采集或人工补样本。`,
    });
  }

  const delistCount = actions.filter((item) => item.action === "建议下架").length;
  if (delistCount > 0) {
    alerts.push({
      label: "低效 SKU",
      text: `当前有 ${delistCount} 个 SKU 被建议下架，建议核对库存占用和货架位。`,
    });
  }

  return alerts.slice(0, 5);
}

function buildReviewItems(actions) {
  const candidateReviews = actions
    .filter((item) => item.kind === "candidate")
    .map((item) => ({
      product: `${item.brand} ${item.product_name}`,
      action: item.action,
      checkpoint: `首单 ${item.order_qty} 支 / ${item.review_days} 天后复盘`,
      focus: item.action === "建议替换上新" ? "重点看是否接住被替换 SKU 的销量" : "重点看首周动销和顾客接受度",
      target: item.margin ? `毛利目标 ${formatPercent(item.margin)}` : "先看动销",
      review_date_label: item.review_date_label,
    }));

  const existingReviews = actions
    .filter((item) => item.kind === "existing" && item.action !== "建议下架")
    .slice(0, 6)
    .map((item) => ({
      product: `${item.brand} ${item.product_name}`,
      action: item.action,
      checkpoint: `${item.review_days} 天后回看售价和毛利`,
      focus:
        item.action === "建议低价引流"
          ? "看销量是否明显抬升"
          : item.action === "建议利润定价"
            ? "看高价是否影响动销"
            : "看调价后毛利率是否回到目标区间",
      target: `${item.role} / ${formatPercent(item.margin)}`,
      review_date_label: item.review_date_label,
    }));

  return candidateReviews.concat(existingReviews).slice(0, 12);
}

function buildProcurementModel() {
  const actions = buildProcurementActions();
  const purchaseActions = actions.filter((item) => item.kind === "candidate");
  return {
    actions,
    overviewCards: [
      ["现有 SKU 数", appState.data?.dashboard?.summary?.sku_count || 0, "当前门店在售牙膏数量"],
      ["建议上新数", purchaseActions.filter((item) => item.action === "建议上新").length, "本期建议新增款"],
      ["建议替换数", purchaseActions.filter((item) => item.action === "建议替换上新").length, "本期建议以新换旧"],
      ["建议下架数", actions.filter((item) => item.action === "建议下架").length, "建议清理的低效 SKU"],
      ["建议采购预算", formatCurrency(purchaseActions.reduce((sum, item) => sum + item.budget, 0)), "按建议首单量估算"],
      ["首单预计毛利", formatCurrency(purchaseActions.reduce((sum, item) => sum + item.expected_profit, 0)), "只按首单试单估算"],
      ["待补结构缺口", appState.data?.dashboard?.structure_gaps?.length || 0, "价格带 / 功效 / 品牌缺口"],
      ["待复盘项目", buildReviewItems(actions).length, "建议重点跟踪的动作"],
    ],
    brandOpportunities: deriveBrandOpportunities(),
    riskAlerts: deriveRiskAlerts(actions),
    reviewItems: buildReviewItems(actions),
  };
}

function renderSummary() {
  const procurement = buildProcurementModel();
  document.getElementById("summaryGrid").innerHTML = procurement.overviewCards
    .slice(0, 6)
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

function renderOverviewModule() {
  const model = buildProcurementModel();
  const kpiContainer = document.getElementById("overviewKpiGrid");
  const actionTable = document.getElementById("procurementActionTable");
  const brandPanel = document.getElementById("overviewBrandOpportunityPanel");
  const riskPanel = document.getElementById("overviewRiskPanel");

  kpiContainer.innerHTML = model.overviewCards
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

  actionTable.querySelector("thead").innerHTML = `
    <tr>
      <th>动作</th>
      <th>商品</th>
      <th>建议价</th>
      <th>建议首单量</th>
      <th>预算</th>
      <th>替换谁</th>
      <th>复盘日期</th>
      <th>原因</th>
    </tr>
  `;
  actionTable.querySelector("tbody").innerHTML = model.actions.length
    ? model.actions
        .map(
          (item) => `
            <tr>
              <td><span class="badge ${actionBadgeClass(item.action)}">${escapeHtml(item.action)}</span></td>
              <td><strong>${escapeHtml(item.brand)}</strong><br />${escapeHtml(item.product_name)}<br /><span class="muted">${escapeHtml(item.spec_text)}</span></td>
              <td>${item.current_price ? `${formatCurrency(item.current_price)} → ` : ""}${item.suggested_price ? formatCurrency(item.suggested_price) : "-"}</td>
              <td>${item.order_qty || "-"}</td>
              <td>${item.budget ? formatCurrency(item.budget) : "-"}</td>
              <td>${escapeHtml(item.replace_targets || "-")}</td>
              <td>${escapeHtml(item.review_date_label)}</td>
              <td class="muted">${escapeHtml(item.reason)}</td>
            </tr>
          `,
        )
        .join("")
    : `<tr><td colspan="8"><div class="empty-state">当前还没有可执行采购动作。</div></td></tr>`;

  brandPanel.innerHTML = `
    <div class="inline-actions">
      <div>
        <h3>重点品牌机会</h3>
        <p class="muted">优先告诉你哪些品牌值得继续补款，哪些品牌值得从集中度里点开深挖。</p>
      </div>
    </div>
    ${
      model.brandOpportunities.length
        ? `<div class="stack compact-stack">${model.brandOpportunities
            .map(
              (item) => `
                <article class="detail-card">
                  <div class="inline-actions">
                    <strong>${escapeHtml(item.brand)}</strong>
                    <span class="badge gray">${escapeHtml(item.label)}</span>
                  </div>
                  <p class="muted">${escapeHtml(item.reason)}</p>
                  <p class="muted">关注点：${escapeHtml(item.focus)}</p>
                </article>
              `,
            )
            .join("")}</div>`
        : `<div class="empty-state">当前还没有足够突出的品牌机会，建议继续补候选池。</div>`
    }
  `;

  riskPanel.innerHTML = `
    <div class="inline-actions">
      <div>
        <h3>高风险提醒</h3>
        <p class="muted">优先处理结构缺口、市场样本不足和低效 SKU。</p>
      </div>
    </div>
    ${
      model.riskAlerts.length
        ? `<ul class="risk-list">${model.riskAlerts
            .map((item) => `<li><strong>${escapeHtml(item.label)}：</strong>${escapeHtml(item.text)}</li>`)
            .join("")}</ul>`
        : `<div class="empty-state">当前没有特别突出的高风险提醒，可以继续看采购决策单。</div>`
    }
  `;
}

function renderCandidateTable() {
  const table = document.getElementById("candidateTable");
  const rows = appState.data.candidates || [];
  table.querySelector("thead").innerHTML = `
    <tr>
      <th>商品</th>
      <th>规格</th>
      <th>平台</th>
      <th>参考价</th>
      <th>建议售价</th>
      <th>预计毛利率</th>
      <th>热度</th>
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
              <td>${escapeHtml(item.spec_text || "-")}</td>
              <td>${escapeHtml(item.source_platform || "其他")}</td>
              <td>${formatCurrency(item.online_reference_price)}</td>
              <td>${formatCurrency(item.suggested_price)}</td>
              <td>${formatPercent(item.expected_margin)}</td>
              <td>${escapeHtml(item.heat_score || 0)}</td>
              <td><span class="badge ${actionBadgeClass(item.suggestion_status)}">${escapeHtml(item.suggestion_status)}</span></td>
              <td>
                <button class="action-link" data-action="compare" data-id="${item.id}">对比</button>
                /
                <button class="action-link" data-action="edit" data-id="${item.id}">编辑</button>
                /
                <button class="action-link" data-action="delete" data-id="${item.id}">删除</button>
              </td>
            </tr>
          `,
        )
        .join("")
    : `<tr><td colspan="9"><div class="empty-state">还没有候选新品，先录入一条试试。</div></td></tr>`;
}

function renderComparisonSelector() {
  const select = document.getElementById("comparisonSelector");
  const currentValue = select.value;
  const options = appState.data.candidates.map(
    (item) => `<option value="${item.id}">${escapeHtml(item.brand)} - ${escapeHtml(item.product_name)}</option>`,
  );
  select.innerHTML = options.length ? options.join("") : `<option value="">暂无候选新品</option>`;
  if (currentValue && appState.data.candidates.some((item) => String(item.id) === String(currentValue))) {
    select.value = currentValue;
  }
}

function renderDecisionSummaryPanel() {
  const model = buildProcurementModel();
  const purchaseActions = model.actions.filter((item) => item.kind === "candidate");
  const container = document.getElementById("decisionSummaryPanel");
  container.innerHTML = `
    <div class="card stack">
      <div class="inline-actions">
        <div>
          <h3>本期采购决策摘要</h3>
          <p class="muted">先看要上新、替换、下架和调价的数量，再往下看具体 SKU 依据。</p>
        </div>
      </div>
      <div class="selection-summary">
        <div class="score-chip"><div class="label">建议上新</div><div class="value">${purchaseActions.filter((item) => item.action === "建议上新").length}</div></div>
        <div class="score-chip"><div class="label">建议替换</div><div class="value">${purchaseActions.filter((item) => item.action === "建议替换上新").length}</div></div>
        <div class="score-chip"><div class="label">建议下架</div><div class="value">${model.actions.filter((item) => item.action === "建议下架").length}</div></div>
        <div class="score-chip"><div class="label">建议调价</div><div class="value">${model.actions.filter((item) => ["建议低价引流", "建议调整售价", "建议利润定价"].includes(item.action)).length}</div></div>
        <div class="score-chip"><div class="label">采购预算</div><div class="value">${formatCurrency(purchaseActions.reduce((sum, item) => sum + item.budget, 0))}</div></div>
      </div>
    </div>
  `;
}

function renderRecommendations() {
  renderDecisionSummaryPanel();
  renderAutoSelectionPanel();
  renderPricingFocusPanel();
  renderPricingSimulatorPanel();
  renderMarketDiagnosticsPanel();

  const skuTable = document.getElementById("skuRecommendationTable");
  const skuRows = appState.data.recommendations.existing || [];
  skuTable.querySelector("thead").innerHTML = `
    <tr>
      <th>商品</th>
      <th>动作</th>
      <th>当前售价</th>
      <th>市场参考</th>
      <th>样本质量</th>
      <th>建议价区间</th>
      <th>推荐价</th>
      <th>毛利率</th>
      <th>利润贡献</th>
      <th>系统分层</th>
      <th>详情</th>
    </tr>
  `;
  skuTable.querySelector("tbody").innerHTML = skuRows.length
    ? skuRows
        .map(
          (item) => `
            <tr>
              <td><strong>${escapeHtml(item.brand)}</strong><br />${escapeHtml(item.product_name)}<br /><span class="muted">${escapeHtml(item.spec_text || "-")}</span></td>
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
                    <div class="muted">${escapeHtml(item.reason || "")}</div>
                    <div class="muted">快照：${escapeHtml(item.market_snapshot_status || "待更新")} / ${escapeHtml(item.market_snapshot_at || "暂无")}</div>
                    <div class="muted">区间 ${formatPriceRange(item.taobao_min_price, item.taobao_max_price)} / 热度 ${escapeHtml(item.online_heat_score || 0)} / ${escapeHtml(item.price_disorder_label || "")}</div>
                    ${item.market_fallback_note ? `<div class="muted">说明：${escapeHtml(item.market_fallback_note)}</div>` : ""}
                    ${renderReasonList(item.recommendation_basis)}
                  </div>
                </details>
              </td>
            </tr>
          `,
        )
        .join("")
    : `<tr><td colspan="11"><div class="empty-state">暂无建议。</div></td></tr>`;

  const candidateTable = document.getElementById("candidateRecommendationTable");
  const candidateRows = appState.data.recommendations.candidate || [];
  candidateTable.querySelector("thead").innerHTML = `
    <tr>
      <th>商品</th>
      <th>建议分层</th>
      <th>建议售价</th>
      <th>预计毛利率</th>
      <th>综合评分</th>
      <th>平台 / 热度</th>
      <th>结论</th>
    </tr>
  `;
  candidateTable.querySelector("tbody").innerHTML = candidateRows.length
    ? candidateRows
        .map(
          (item) => `
            <tr>
              <td><strong>${escapeHtml(item.brand)}</strong><br />${escapeHtml(item.product_name)}<br /><span class="muted">${escapeHtml(item.spec_text || "-")}</span></td>
              <td>${escapeHtml(item.proposed_role)}</td>
              <td>${formatCurrency(item.suggested_price)}</td>
              <td>${formatPercent(item.expected_margin)}</td>
              <td>${escapeHtml(item.recommendation_score)}</td>
              <td>${escapeHtml(item.source_platform || "其他")}<br /><span class="muted">热度 ${escapeHtml(item.heat_score || 0)}</span></td>
              <td><span class="badge ${actionBadgeClass(item.suggestion_status)}">${escapeHtml(item.suggestion_status)}</span></td>
            </tr>
          `,
        )
        .join("")
    : `<tr><td colspan="7"><div class="empty-state">暂无候选新品建议。</div></td></tr>`;
}

function renderReviewModule() {
  const reviewItems = buildProcurementModel().reviewItems;
  const summaryPanel = document.getElementById("reviewSummaryPanel");
  const reviewTable = document.getElementById("reviewTable");
  summaryPanel.innerHTML = `
    <div class="card stack">
      <div class="inline-actions">
        <div>
          <h3>复盘重点</h3>
          <p class="muted">当前版本先把要跟踪的项目列清楚，下一轮再补真实销量与补货闭环。</p>
        </div>
      </div>
      <div class="selection-summary">
        <div class="score-chip"><div class="label">待跟踪新品</div><div class="value">${reviewItems.filter((item) => item.action.includes("上新")).length}</div></div>
        <div class="score-chip"><div class="label">待跟踪调价</div><div class="value">${reviewItems.filter((item) => !item.action.includes("上新")).length}</div></div>
        <div class="score-chip"><div class="label">最近复盘点</div><div class="value">${reviewItems[0]?.review_date_label || "-"}</div></div>
      </div>
    </div>
  `;
  reviewTable.querySelector("thead").innerHTML = `
    <tr>
      <th>商品</th>
      <th>当前动作</th>
      <th>复盘节点</th>
      <th>重点关注</th>
      <th>目标</th>
      <th>建议日期</th>
    </tr>
  `;
  reviewTable.querySelector("tbody").innerHTML = reviewItems.length
    ? reviewItems
        .map(
          (item) => `
            <tr>
              <td>${escapeHtml(item.product)}</td>
              <td><span class="badge ${actionBadgeClass(item.action)}">${escapeHtml(item.action)}</span></td>
              <td>${escapeHtml(item.checkpoint)}</td>
              <td class="muted">${escapeHtml(item.focus)}</td>
              <td>${escapeHtml(item.target)}</td>
              <td>${escapeHtml(item.review_date_label)}</td>
            </tr>
          `,
        )
        .join("")
    : `<tr><td colspan="6"><div class="empty-state">当前还没有待复盘项目。</div></td></tr>`;
}

function bindWorkflowEnhancements() {
  const candidateTable = document.getElementById("candidateTable");
  candidateTable.addEventListener("click", async (event) => {
    const target = event.target.closest('[data-action="compare"]');
    if (!target) return;
    const select = document.getElementById("comparisonSelector");
    if (!select) return;
    select.value = target.dataset.id;
    await renderComparisonPanel();
    document.getElementById("comparisonPanel")?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}


function procurementActionsData() {
  return appState.data?.procurement_actions || { existing: [], candidates: [], all: [], summary: {} };
}

function feedbackProposalsData() {
  return appState.data?.feedback_proposals || [];
}

function strategyOverridesSummary() {
  return appState.data?.strategy_overrides_summary || { active_count: 0, rows: [] };
}

function currentReviewCandidate() {
  const queue = procurementQueue();
  if (!queue.length) return null;
  if (!appState.selectedReviewCandidateId || !queue.some((item) => Number(item.candidate_id) === Number(appState.selectedReviewCandidateId))) {
    appState.selectedReviewCandidateId = Number(queue[0].candidate_id);
  }
  return queue.find((item) => Number(item.candidate_id) === Number(appState.selectedReviewCandidateId)) || queue[0];
}

function procurementStatuses() {
  return ["待确认", "已确认", "已执行", "继续观察", "已放弃"];
}

function buildProcurementModel() {
  const actions = procurementActionsData();
  const proposals = feedbackProposalsData();
  const procurement = appState.data?.procurement || { summary: {} };
  const marketSummary = appState.data?.market_reference?.summary || {};
  const brandOpportunities = (actions.candidates || [])
    .filter((item) => ["建议上新", "建议替换"].includes(item.action_type))
    .slice(0, 5)
    .map((item) => ({
      brand: item.brand,
      label: item.gap_type || "候选机会",
      reason: item.reason_summary || "系统识别为值得优先处理的候选机会。",
      focus: `${item.action_type} / 优先级 ${item.priority_score} / 可信度 ${item.confidence_level}`,
    }));
  const riskAlerts = [];
  if (actions.summary?.weak_confidence_count) {
    riskAlerts.push({ label: "弱可信数据", text: `当前有 ${actions.summary.weak_confidence_count} 个现有 SKU 的市场参考可信度偏低，定价建议需结合人工复核。` });
  }
  if (marketSummary.disorder_count) {
    riskAlerts.push({ label: "价格混乱", text: `当前有 ${marketSummary.disorder_count} 个 SKU 呈现明显乱价，需要优先看是否做引流或下架。` });
  }
  const pendingProposals = proposals.filter((item) => item.decision_status === "pending");
  if (pendingProposals.length) {
    riskAlerts.push({ label: "待确认规则提案", text: `已有 ${pendingProposals.length} 条复盘反哺提案待你确认。` });
  }
  if (!riskAlerts.length) {
    riskAlerts.push({ label: "当前节奏", text: "当前没有突出的数据风控或结构性风险，可以优先执行采购动作单。" });
  }

  return {
    actions,
    proposals,
    procurement,
    overviewCards: [
      ["建议上新数", actions.summary?.new_count || 0, "候选新品里当前适合直接推进的款数"],
      ["建议替换数", actions.summary?.replace_count || 0, "适合拿来替换低效老品的款数"],
      ["建议下架数", actions.summary?.delist_count || 0, "现有 SKU 建议清退的数量"],
      ["本期总预算", formatCurrency(actions.summary?.total_budget || 0), "按当前首单模型测算"],
      ["首单预计毛利", formatCurrency(actions.summary?.expected_first_order_profit || 0), "候选新品首单毛利估算"],
      ["高优先品牌机会", (actions.summary?.high_priority_brands || []).join(" / ") || "暂无", "优先值得补款或补爆款的品牌"],
      ["弱可信数据提醒", marketSummary.weak_confidence_count || 0, "市场锚点可信度偏低的现有 SKU"],
      ["待复盘项目数", procurement.summary?.tracked_count || 0, "已进入执行追踪的新品或动作"],
    ],
    brandOpportunities,
    riskAlerts,
  };
}

function renderSummary() {
  const model = buildProcurementModel();
  document.getElementById("summaryGrid").innerHTML = model.overviewCards
    .slice(0, 6)
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

function renderOverviewModule() {
  const model = buildProcurementModel();
  const kpiContainer = document.getElementById("overviewKpiGrid");
  const actionTable = document.getElementById("procurementActionTable");
  const brandPanel = document.getElementById("overviewBrandOpportunityPanel");
  const riskPanel = document.getElementById("overviewRiskPanel");
  const cycleOptions = (appState.meta?.review_cycle_options || [7, 14, 21, 30])
    .map((item) => `<option value="${item}">${item}天</option>`)
    .join("");
  const statusOptions = procurementStatuses()
    .map((item) => `<option value="${escapeHtml(item)}">${escapeHtml(item)}</option>`)
    .join("");
  const actionRows = [...(model.actions.candidates || []).slice(0, 8), ...(model.actions.existing || []).slice(0, 6)];

  kpiContainer.innerHTML = model.overviewCards
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

  actionTable.querySelector("thead").innerHTML = `
    <tr>
      <th>动作</th>
      <th>商品</th>
      <th>优先级</th>
      <th>可信度</th>
      <th>推荐价</th>
      <th>首单量</th>
      <th>预算</th>
      <th>复盘</th>
      <th>状态</th>
      <th>保存</th>
    </tr>
  `;
  actionTable.querySelector("tbody").innerHTML = actionRows.length
    ? actionRows
        .map(
          (item) => `
            <tr data-action-key="${escapeHtml(item.action_key)}" data-item-type="${escapeHtml(item.item_type)}">
              <td><span class="badge ${actionBadgeClass(item.action_type)}">${escapeHtml(item.action_type)}</span></td>
              <td>
                <strong>${escapeHtml(item.brand)}</strong><br />${escapeHtml(item.product_name)}<br />
                <span class="muted">${escapeHtml(item.spec_text || "-")}</span><br />
                <span class="muted">${escapeHtml(item.reason_summary || "-")}</span>
              </td>
              <td>${escapeHtml(item.priority_score || 0)}</td>
              <td>
                <span class="badge ${item.confidence_level === "高" ? "green" : item.confidence_level === "中" ? "gray" : "orange"}">${escapeHtml(item.confidence_level || "-")}</span><br />
                <span class="muted">${escapeHtml(item.market_anchor_source || item.gap_type || "-")}</span>
              </td>
              <td>
                <input class="table-input" data-field="recommended_price" type="number" min="0" step="0.01" value="${escapeHtml(item.recommended_price || 0)}" />
              </td>
              <td>
                ${
                  item.item_type === "candidate"
                    ? `<input class="table-input" data-field="suggested_first_order_qty" type="number" min="0" step="1" value="${escapeHtml(item.suggested_first_order_qty || 0)}" /><div class="muted">模型 ${escapeHtml(item.suggested_first_order_qty_base || item.suggested_first_order_qty || 0)} 支</div>`
                    : `<span class="muted">-</span>`
                }
              </td>
              <td>${item.item_type === "candidate" ? formatCurrency(item.planned_budget || 0) : "-"}</td>
              <td>
                <select class="table-input" data-field="review_cycle_days">${cycleOptions}</select>
              </td>
              <td>
                <select class="table-input" data-field="status">${statusOptions}</select>
              </td>
              <td><button type="button" class="primary-btn small-btn" data-action="save-procurement-action" data-action-key="${escapeHtml(item.action_key)}">保存</button></td>
            </tr>
          `,
        )
        .join("")
    : `<tr><td colspan="10"><div class="empty-state">当前还没有可执行的采购动作。</div></td></tr>`;

  actionRows.forEach((item) => {
    const row = actionTable.querySelector(`tr[data-action-key="${item.action_key}"]`);
    if (row?.querySelector('[data-field="review_cycle_days"]')) {
      row.querySelector('[data-field="review_cycle_days"]').value = String(item.review_cycle_days || 14);
    }
    if (row?.querySelector('[data-field="status"]')) {
      row.querySelector('[data-field="status"]').value = item.status || "待确认";
    }
  });

  brandPanel.innerHTML = `
    <div class="inline-actions">
      <div>
        <h3>重点品牌机会</h3>
        <p class="muted">优先看系统当前识别出的补品牌爆款、补价格带和替换机会。</p>
      </div>
    </div>
    ${
      model.brandOpportunities.length
        ? `<div class="stack compact-stack">${model.brandOpportunities
            .map(
              (item) => `
                <article class="detail-card">
                  <div class="inline-actions">
                    <strong>${escapeHtml(item.brand)}</strong>
                    <span class="badge gray">${escapeHtml(item.label)}</span>
                  </div>
                  <p class="muted">${escapeHtml(item.reason)}</p>
                  <p class="muted">关注点：${escapeHtml(item.focus)}</p>
                </article>
              `,
            )
            .join("")}</div>`
        : `<div class="empty-state">当前还没有特别突出的品牌机会，建议先补抓候选池或刷新市场快照。</div>`
    }
  `;

  riskPanel.innerHTML = `
    <div class="inline-actions">
      <div>
        <h3>高风险提醒</h3>
        <p class="muted">先处理弱可信样本、明显乱价和待确认的复盘提案。</p>
      </div>
    </div>
    <ul class="risk-list">${model.riskAlerts.map((item) => `<li><strong>${escapeHtml(item.label)}：</strong>${escapeHtml(item.text)}</li>`).join("")}</ul>
  `;
}

function renderRecommendations() {
  const actions = procurementActionsData();
  const decisionSummaryPanel = document.getElementById("decisionSummaryPanel");
  const autoSelectionPanel = document.getElementById("autoSelectionPanel");
  const pricingFocusPanel = document.getElementById("pricingFocusPanel");
  const skuTable = document.getElementById("skuRecommendationTable");
  const candidateTable = document.getElementById("candidateRecommendationTable");
  const proposals = feedbackProposalsData();
  const marketSummary = appState.data?.market_reference?.summary || {};

  decisionSummaryPanel.innerHTML = `
    <div class="selection-summary">
      <div class="score-chip"><div class="label">建议上新</div><div class="value">${actions.summary?.new_count || 0}</div></div>
      <div class="score-chip"><div class="label">建议替换</div><div class="value">${actions.summary?.replace_count || 0}</div></div>
      <div class="score-chip"><div class="label">建议下架</div><div class="value">${actions.summary?.delist_count || 0}</div></div>
      <div class="score-chip"><div class="label">本期预算</div><div class="value">${escapeHtml(formatCurrency(actions.summary?.total_budget || 0))}</div></div>
      <div class="score-chip"><div class="label">首单毛利</div><div class="value">${escapeHtml(formatCurrency(actions.summary?.expected_first_order_profit || 0))}</div></div>
      <div class="score-chip"><div class="label">规则覆盖</div><div class="value">${strategyOverridesSummary().active_count || 0}</div></div>
    </div>
  `;

  autoSelectionPanel.innerHTML = `
    <div class="card stack">
      <div class="inline-actions">
        <div>
          <h3>动作优先级提示</h3>
          <p class="muted">采购动作单已经把结构补位、毛利可做性、市场可信度、热度、替换收益和资金效率合成优先级分。</p>
        </div>
      </div>
      ${
        proposals.length
          ? `<ul class="risk-list">${proposals
              .slice(0, 4)
              .map((item) => `<li><strong>${escapeHtml(item.title)}：</strong>${escapeHtml(item.decision_status === "pending" ? "待确认" : item.decision_status === "accepted" ? "已生效" : "已驳回")}</li>`)
              .join("")}</ul>`
          : `<div class="empty-state">当前还没有新的复盘反哺提案。</div>`
      }
    </div>
  `;

  pricingFocusPanel.innerHTML = `
    <div class="card stack">
      <div class="inline-actions">
        <div>
          <h3>市场参考可信度</h3>
          <p class="muted">主锚点来自淘宝 / 天猫 / 京东聚合；抓不到时会自动降级到跨平台替代或人工补样本。</p>
        </div>
      </div>
      <div class="selection-summary">
        <div class="score-chip"><div class="label">高可信</div><div class="value">${marketSummary.high_confidence_count || 0}</div></div>
        <div class="score-chip"><div class="label">弱可信</div><div class="value">${marketSummary.weak_confidence_count || 0}</div></div>
        <div class="score-chip"><div class="label">乱价 SKU</div><div class="value">${marketSummary.disorder_count || 0}</div></div>
      </div>
    </div>
  `;

  skuTable.querySelector("thead").innerHTML = `
    <tr>
      <th>商品</th>
      <th>动作</th>
      <th>主锚点</th>
      <th>可信度</th>
      <th>参考区间</th>
      <th>推荐价</th>
      <th>原因</th>
    </tr>
  `;
  skuTable.querySelector("tbody").innerHTML = (actions.existing || []).length
    ? actions.existing
        .map(
          (item) => `
            <tr>
              <td><strong>${escapeHtml(item.brand)}</strong><br />${escapeHtml(item.product_name)}<br /><span class="muted">${escapeHtml(item.spec_text || "-")}</span></td>
              <td><span class="badge ${actionBadgeClass(item.action_type)}">${escapeHtml(item.action_type)}</span></td>
              <td>${escapeHtml(item.market_anchor_source || "-")}<br /><span class="muted">${formatMaybeCurrency(item.market_reference_price)}</span></td>
              <td><span class="badge ${item.confidence_level === "高" ? "green" : item.confidence_level === "中" ? "gray" : "orange"}">${escapeHtml(item.confidence_level || "-")}</span></td>
              <td>${formatMaybeCurrency(item.market_reference_range?.[0])} - ${formatMaybeCurrency(item.market_reference_range?.[1])}</td>
              <td>${formatMaybeCurrency(item.recommended_price)}</td>
              <td class="muted">${escapeHtml(item.reason_summary || "-")}</td>
            </tr>
          `,
        )
        .join("")
    : `<tr><td colspan="7"><div class="empty-state">当前没有现有 SKU 处置动作。</div></td></tr>`;

  candidateTable.querySelector("thead").innerHTML = `
    <tr>
      <th>商品</th>
      <th>动作</th>
      <th>补位类型</th>
      <th>热度</th>
      <th>可信度</th>
      <th>建议价</th>
      <th>首单量</th>
      <th>预算</th>
      <th>14天目标</th>
      <th>原因</th>
    </tr>
  `;
  candidateTable.querySelector("tbody").innerHTML = (actions.candidates || []).length
    ? actions.candidates
        .map(
          (item) => `
            <tr>
              <td><strong>${escapeHtml(item.brand)}</strong><br />${escapeHtml(item.product_name)}<br /><span class="muted">${escapeHtml(item.spec_text || "-")}</span></td>
              <td><span class="badge ${actionBadgeClass(item.action_type)}">${escapeHtml(item.action_type)}</span></td>
              <td>${escapeHtml(item.gap_type || "-")}<br /><span class="muted">${escapeHtml(item.structural_role || "-")}</span></td>
              <td>${escapeHtml(item.heat_score || 0)}</td>
              <td><span class="badge ${item.confidence_level === "高" ? "green" : item.confidence_level === "中" ? "gray" : "orange"}">${escapeHtml(item.confidence_level || "-")}</span></td>
              <td>${formatMaybeCurrency(item.recommended_price)}</td>
              <td>${escapeHtml(item.suggested_first_order_qty || 0)}</td>
              <td>${formatCurrency(item.planned_budget || 0)}</td>
              <td>${escapeHtml(formatPercent(item.expected_sell_through_14d || 0))}</td>
              <td class="muted">${escapeHtml(item.reason_summary || "-")}</td>
            </tr>
          `,
        )
        .join("")
    : `<tr><td colspan="10"><div class="empty-state">当前没有候选新品引入动作。</div></td></tr>`;
}

function renderReviewModule() {
  const queue = procurementQueue();
  const summary = appState.data?.procurement?.summary || {};
  const selected = currentReviewCandidate();
  const summaryPanel = document.getElementById("reviewSummaryPanel");
  const reviewTable = document.getElementById("reviewTable");
  const decisionOptions = (appState.meta?.review_decision_options || [])
    .map((item) => `<option value="${escapeHtml(item.key)}">${escapeHtml(item.label)}</option>`)
    .join("");
  const selectedLog = selected && appState.selectedReviewLogId
    ? (selected.review_logs || []).find((item) => Number(item.id) === Number(appState.selectedReviewLogId))
    : null;
  const templateButtons = [7, 14, 30]
    .map((days) => `<button type="button" class="ghost-btn small-btn" data-action="apply-review-template" data-days="${days}">${days}天模板</button>`)
    .join("");
  const proposalCards = feedbackProposalsData()
    .map(
      (item) => `
        <article class="detail-card">
          <div class="inline-actions">
            <strong>${escapeHtml(item.title)}</strong>
            <span class="badge ${item.decision_status === "accepted" ? "green" : item.decision_status === "rejected" ? "gray" : "orange"}">
              ${escapeHtml(item.decision_status === "accepted" ? "已生效" : item.decision_status === "rejected" ? "已驳回" : "待确认")}
            </span>
          </div>
          <p class="muted">${escapeHtml(item.evidence_summary || "-")}</p>
          <p class="muted">${escapeHtml(item.impact_summary || "-")}</p>
          ${
            item.decision_status === "pending"
              ? `<div class="inline-actions">
                  <button type="button" class="primary-btn small-btn" data-action="decide-feedback-proposal" data-proposal-key="${escapeHtml(item.proposal_key)}" data-decision="accepted">确认生效</button>
                  <button type="button" class="ghost-btn small-btn" data-action="decide-feedback-proposal" data-proposal-key="${escapeHtml(item.proposal_key)}" data-decision="rejected">暂不采纳</button>
                </div>`
              : `<div class="muted">当前状态：${escapeHtml(item.decision_status === "accepted" ? "已生效" : "已驳回")}</div>`
          }
        </article>
      `,
    )
    .join("");

  summaryPanel.innerHTML = `
    <div class="stack">
      <div class="selection-summary">
        <div class="score-chip"><div class="label">已追踪</div><div class="value">${summary.tracked_count || 0}</div></div>
        <div class="score-chip"><div class="label">待复盘</div><div class="value">${summary.review_due_count || 0}</div></div>
        <div class="score-chip"><div class="label">优于预期</div><div class="value">${summary.excellent_count || 0}</div></div>
        <div class="score-chip"><div class="label">偏弱/失败</div><div class="value">${summary.weak_count || 0}</div></div>
      </div>
      <div class="overview-layout">
        <article class="card stack">
          <div class="inline-actions">
            <div>
              <h3>执行卡</h3>
              <p class="muted">先把首单、实际上新和复盘节奏记录下来，后面所有复盘都围绕这张执行卡走。</p>
            </div>
          </div>
          ${
            selected
              ? `
                <label>
                  <span>当前跟踪商品</span>
                  <select id="reviewCandidateSelect">
                    ${queue.map((item) => `<option value="${item.candidate_id}" ${Number(item.candidate_id) === Number(selected.candidate_id) ? "selected" : ""}>${escapeHtml(item.brand)} - ${escapeHtml(item.product_name)}</option>`).join("")}
                  </select>
                </label>
                <div class="filter-grid compact">
                  <label><span>首单数量</span><input type="number" id="launchFirstOrderQtyInput" min="0" step="1" value="${escapeHtml(selected.first_order_qty || 0)}" /></label>
                  <label><span>实际上新</span><input type="number" id="launchActualQtyInput" min="0" step="1" value="${escapeHtml(selected.actual_launch_qty || 0)}" /></label>
                  <label><span>上新日期</span><input type="date" id="launchDateInput" value="${escapeHtml(selected.actual_launch_date || "")}" /></label>
                  <label><span>实际售价</span><input type="number" id="launchPriceInput" min="0" step="0.01" value="${escapeHtml(selected.actual_launch_price || selected.suggested_price || 0)}" /></label>
                  <label><span>复盘周期</span><input type="number" id="launchCycleInput" min="1" step="1" value="${escapeHtml(selected.review_cycle_days || 14)}" /></label>
                  <label><span>状态</span>
                    <select id="launchStatusInput">
                      ${(appState.meta?.launch_status_options || []).map((item) => `<option value="${escapeHtml(item.key)}" ${item.key === selected.launch_status ? "selected" : ""}>${escapeHtml(item.label)}</option>`).join("")}
                    </select>
                  </label>
                </div>
                <label><span>执行备注</span><textarea id="launchNotesInput" rows="2">${escapeHtml(selected.launch_notes || "")}</textarea></label>
                <div class="inline-actions">
                  <button type="button" class="primary-btn" data-action="save-launch-plan-v3">保存执行卡</button>
                  <span class="muted">最新结果：${escapeHtml(selected.latest_review_result || "暂无")} / 下次复盘 ${escapeHtml(selected.next_review_date || "-")}</span>
                </div>
              `
              : `<div class="empty-state">当前还没有进入执行追踪的候选新品。</div>`
          }
        </article>
        <article class="card stack">
          <div class="inline-actions">
            <div>
              <h3>${selectedLog ? "编辑复盘记录" : "新增复盘记录"}</h3>
              <p class="muted">用 7 / 14 / 30 天模板快速记录销量、sell-through 和处理结论。</p>
            </div>
            <div class="inline-actions">${templateButtons}</div>
          </div>
          ${
            selected
              ? `
                <div class="filter-grid compact">
                  <label><span>复盘日期</span><input type="date" id="reviewDateInput" value="${escapeHtml(selectedLog?.review_date || new Date().toISOString().slice(0, 10))}" /></label>
                  <label><span>周期标签</span><input type="text" id="reviewCycleLabelInput" value="${escapeHtml(selectedLog?.cycle_label || `${selected.review_cycle_days || 14}天复盘`)}" /></label>
                  <label><span>销量</span><input type="number" id="reviewSalesUnitsInput" min="0" step="1" value="${escapeHtml(selectedLog?.sales_units || 0)}" /></label>
                  <label><span>销售额</span><input type="number" id="reviewSalesAmountInput" min="0" step="0.01" value="${escapeHtml(selectedLog?.sales_amount || 0)}" /></label>
                  <label><span>毛利率(%)</span><input type="number" id="reviewMarginRateInput" min="0" step="0.1" value="${escapeHtml(selectedLog ? (Number(selectedLog.gross_margin_rate || 0) * 100).toFixed(1) : (Number(selected.expected_margin || 0) * 100).toFixed(1))}" /></label>
                  <label><span>复盘结论</span><select id="reviewDecisionInput">${decisionOptions}</select></label>
                </div>
                <label><span>备注</span><textarea id="reviewNotesInput" rows="3">${escapeHtml(selectedLog?.notes || "")}</textarea></label>
                <div class="inline-actions">
                  <button type="button" class="primary-btn" data-action="save-review-log-v3">${selectedLog ? "保存修改" : "保存复盘记录"}</button>
                  ${selectedLog ? `<button type="button" class="ghost-btn" data-action="cancel-review-edit">取消编辑</button>` : ""}
                  <span class="muted">${selected.latest_review_result ? `当前最新结果：${selected.latest_review_result}` : "还没有复盘记录"}</span>
                </div>
              `
              : `<div class="empty-state">先选择一个已上新的候选商品，再开始记录复盘。</div>`
          }
        </article>
      </div>
      <article class="card stack">
        <div class="inline-actions">
          <div>
            <h3>当前商品复盘历史</h3>
            <p class="muted">每条复盘会自动判断优于预期 / 达标 / 偏弱 / 失败。</p>
          </div>
        </div>
        ${
          selected
            ? `
              <div class="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>日期</th>
                      <th>周期</th>
                      <th>销量</th>
                      <th>sell-through</th>
                      <th>目标</th>
                      <th>结果</th>
                      <th>处理</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    ${
                      (selected.review_logs || []).length
                        ? selected.review_logs
                            .map(
                              (log) => `
                                <tr>
                                  <td>${escapeHtml(log.review_date)}</td>
                                  <td>${escapeHtml(log.cycle_label || "-")}</td>
                                  <td>${escapeHtml(log.sales_units || 0)}</td>
                                  <td>${formatPercent(log.sell_through || 0)}</td>
                                  <td>${formatPercent(log.target_sell_through || 0)}</td>
                                  <td><span class="badge ${log.review_result === "优于预期" ? "green" : log.review_result === "达标" ? "gray" : "orange"}">${escapeHtml(log.review_result || "-")}</span></td>
                                  <td>${escapeHtml(log.decision_label || reviewDecisionLabel(log.decision))}</td>
                                  <td>
                                    <button type="button" class="ghost-btn small-btn" data-action="edit-review-log" data-review-id="${log.id}">编辑</button>
                                    <button type="button" class="ghost-btn small-btn" data-action="delete-review-log" data-review-id="${log.id}">删除</button>
                                  </td>
                                </tr>
                              `,
                            )
                            .join("")
                        : `<tr><td colspan="8"><div class="empty-state">当前商品还没有复盘记录。</div></td></tr>`
                    }
                  </tbody>
                </table>
              </div>
            `
            : `<div class="empty-state">当前还没有选中复盘商品。</div>`
        }
      </article>
      <article class="card stack">
        <div class="inline-actions">
          <div>
            <h3>规则修正提案</h3>
            <p class="muted">系统根据连续复盘结果给出半自动提案，只有你确认后才会进入下一轮推荐。</p>
          </div>
        </div>
        ${proposalCards || `<div class="empty-state">当前还没有新的复盘反哺提案。</div>`}
      </article>
    </div>
  `;

  if (selectedLog && document.getElementById("reviewDecisionInput")) {
    document.getElementById("reviewDecisionInput").value = selectedLog.decision || "observe";
  }

  reviewTable.querySelector("thead").innerHTML = `
    <tr>
      <th>商品</th>
      <th>状态</th>
      <th>首单</th>
      <th>实际上新</th>
      <th>最新结果</th>
      <th>下次复盘</th>
      <th>操作</th>
    </tr>
  `;
  reviewTable.querySelector("tbody").innerHTML = queue.length
    ? queue
        .map(
          (item) => `
            <tr>
              <td><strong>${escapeHtml(item.brand)}</strong><br />${escapeHtml(item.product_name)}</td>
              <td><span class="badge ${item.review_due ? "orange" : "gray"}">${escapeHtml(item.launch_status_label || launchStatusLabel(item.launch_status))}</span></td>
              <td>${escapeHtml(item.first_order_qty || 0)}</td>
              <td>${escapeHtml(item.actual_launch_qty || 0)} 支<br /><span class="muted">${escapeHtml(item.actual_launch_date || "-")}</span></td>
              <td>${escapeHtml(item.latest_review_result || "-")}</td>
              <td>${escapeHtml(item.next_review_date || "-")}</td>
              <td><button type="button" class="ghost-btn small-btn" data-action="select-review-candidate" data-candidate-id="${item.candidate_id}">查看</button></td>
            </tr>
          `,
        )
        .join("")
    : `<tr><td colspan="7"><div class="empty-state">当前还没有进入执行追踪的候选商品。</div></td></tr>`;
}

async function refreshState() {
  appState.pricingSimulation = null;
  appState.data = await fetchJson("/api/state");
  renderSummary();
  renderOverviewModule();
  renderSkuFilters();
  renderSkuTable();
  renderCandidateTable();
  renderCrawlPreview();
  renderComparisonSelector();
  await renderComparisonPanel();
  renderDashboard();
  renderRecommendations();
  renderReviewModule();
}

function bindWorkflowEnhancements() {
  document.getElementById("candidateTable")?.addEventListener("click", async (event) => {
    const compareTarget = event.target.closest('[data-action="compare"]');
    if (!compareTarget) return;
    const select = document.getElementById("comparisonSelector");
    if (!select) return;
    select.value = compareTarget.dataset.id;
    await renderComparisonPanel();
    document.getElementById("comparisonPanel")?.scrollIntoView({ behavior: "smooth", block: "start" });
  });

  document.getElementById("procurementActionTable")?.addEventListener("click", async (event) => {
    const button = event.target.closest('[data-action="save-procurement-action"]');
    if (!button) return;
    const row = button.closest("tr");
    const actionKey = button.dataset.actionKey;
    if (!row || !actionKey) return;
    const payload = {
      recommended_price: Number(row.querySelector('[data-field="recommended_price"]')?.value || 0),
      suggested_first_order_qty: Number(row.querySelector('[data-field="suggested_first_order_qty"]')?.value || 0),
      review_cycle_days: Number(row.querySelector('[data-field="review_cycle_days"]')?.value || 14),
      status: row.querySelector('[data-field="status"]')?.value || "待确认",
    };
    try {
      button.disabled = true;
      await fetchJson(`/api/procurement-actions/${actionKey}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      await refreshState();
    } catch (error) {
      alert(error.message);
    } finally {
      button.disabled = false;
    }
  });

  document.getElementById("reviewModule")?.addEventListener("change", (event) => {
    const select = event.target.closest("#reviewCandidateSelect");
    if (!select) return;
    appState.selectedReviewCandidateId = Number(select.value || 0);
    appState.selectedReviewLogId = null;
    renderReviewModule();
  });

  document.getElementById("reviewModule")?.addEventListener("click", async (event) => {
    const selectButton = event.target.closest('[data-action="select-review-candidate"]');
    if (selectButton) {
      appState.selectedReviewCandidateId = Number(selectButton.dataset.candidateId || 0);
      appState.selectedReviewLogId = null;
      renderReviewModule();
      return;
    }

    const applyTemplate = event.target.closest('[data-action="apply-review-template"]');
    if (applyTemplate) {
      const days = Number(applyTemplate.dataset.days || 7);
      const selected = currentReviewCandidate();
      const reviewDateInput = document.getElementById("reviewDateInput");
      const reviewCycleLabelInput = document.getElementById("reviewCycleLabelInput");
      const reviewDecisionInput = document.getElementById("reviewDecisionInput");
      if (reviewDateInput && selected?.actual_launch_date) {
        const launchDate = new Date(selected.actual_launch_date);
        if (!Number.isNaN(launchDate.getTime())) {
          launchDate.setDate(launchDate.getDate() + days);
          reviewDateInput.value = launchDate.toISOString().slice(0, 10);
        }
      }
      if (reviewCycleLabelInput) reviewCycleLabelInput.value = `${days}天复盘`;
      if (reviewDecisionInput) reviewDecisionInput.value = "observe";
      return;
    }

    const saveLaunch = event.target.closest('[data-action="save-launch-plan-v3"]');
    if (saveLaunch) {
      const selected = currentReviewCandidate();
      if (!selected) return;
      try {
        saveLaunch.disabled = true;
        await fetchJson(`/api/candidates/${selected.candidate_id}/launch-plan`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            first_order_qty: Number(document.getElementById("launchFirstOrderQtyInput")?.value || 0),
            actual_launch_qty: Number(document.getElementById("launchActualQtyInput")?.value || 0),
            actual_launch_date: document.getElementById("launchDateInput")?.value || "",
            actual_launch_price: Number(document.getElementById("launchPriceInput")?.value || 0),
            review_cycle_days: Number(document.getElementById("launchCycleInput")?.value || 14),
            launch_status: document.getElementById("launchStatusInput")?.value || "planned",
            launch_notes: document.getElementById("launchNotesInput")?.value || "",
          }),
        });
        await refreshState();
      } catch (error) {
        alert(error.message);
      } finally {
        saveLaunch.disabled = false;
      }
      return;
    }

    const saveReview = event.target.closest('[data-action="save-review-log-v3"]');
    if (saveReview) {
      const selected = currentReviewCandidate();
      if (!selected) return;
      const reviewId = appState.selectedReviewLogId;
      const url = reviewId ? `/api/candidates/${selected.candidate_id}/review-logs/${reviewId}` : `/api/candidates/${selected.candidate_id}/review-logs`;
      const method = reviewId ? "PUT" : "POST";
      try {
        saveReview.disabled = true;
        await fetchJson(url, {
          method,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            review_date: document.getElementById("reviewDateInput")?.value || "",
            cycle_label: document.getElementById("reviewCycleLabelInput")?.value || "",
            sales_units: Number(document.getElementById("reviewSalesUnitsInput")?.value || 0),
            sales_amount: Number(document.getElementById("reviewSalesAmountInput")?.value || 0),
            gross_margin_rate: Number(document.getElementById("reviewMarginRateInput")?.value || 0),
            decision: document.getElementById("reviewDecisionInput")?.value || "observe",
            notes: document.getElementById("reviewNotesInput")?.value || "",
          }),
        });
        appState.selectedReviewLogId = null;
        await refreshState();
      } catch (error) {
        alert(error.message);
      } finally {
        saveReview.disabled = false;
      }
      return;
    }

    const cancelEdit = event.target.closest('[data-action="cancel-review-edit"]');
    if (cancelEdit) {
      appState.selectedReviewLogId = null;
      renderReviewModule();
      return;
    }

    const editLog = event.target.closest('[data-action="edit-review-log"]');
    if (editLog) {
      appState.selectedReviewLogId = Number(editLog.dataset.reviewId || 0);
      renderReviewModule();
      return;
    }

    const deleteLog = event.target.closest('[data-action="delete-review-log"]');
    if (deleteLog) {
      const selected = currentReviewCandidate();
      if (!selected) return;
      try {
        await fetchJson(`/api/candidates/${selected.candidate_id}/review-logs/${deleteLog.dataset.reviewId}`, { method: "DELETE" });
        appState.selectedReviewLogId = null;
        await refreshState();
      } catch (error) {
        alert(error.message);
      }
      return;
    }

    const proposalDecision = event.target.closest('[data-action="decide-feedback-proposal"]');
    if (proposalDecision) {
      try {
        await fetchJson(`/api/review-feedback/proposals/${proposalDecision.dataset.proposalKey}/decision`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ decision: proposalDecision.dataset.decision }),
        });
        await refreshState();
      } catch (error) {
        alert(error.message);
      }
    }
  });
}

function reviewTemplateValues(days, selected) {
  const safeDays = Number(days || 14);
  const launchDate = selected?.actual_launch_date || new Date().toISOString().slice(0, 10);
  const baseDate = new Date(launchDate);
  if (Number.isNaN(baseDate.getTime())) {
    return {
      review_date: new Date().toISOString().slice(0, 10),
      cycle_label: `${safeDays}天复盘`,
      decision: "observe",
      gross_margin_rate: selected ? ((Number(selected.expected_margin || 0) * 100).toFixed(1)) : "0",
    };
  }
  baseDate.setDate(baseDate.getDate() + safeDays);
  return {
    review_date: baseDate.toISOString().slice(0, 10),
    cycle_label: `${safeDays}天复盘`,
    decision: safeDays <= 7 ? "observe" : safeDays <= 14 ? "replenish" : "reprice",
    gross_margin_rate: selected ? ((Number(selected.expected_margin || 0) * 100).toFixed(1)) : "0",
  };
}

function populateReviewForm(selected, values = {}) {
  const defaults = values.review_date ? values : reviewTemplateValues(selected?.review_cycle_days || 14, selected);
  const reviewDateInput = document.getElementById("reviewDateInput");
  const reviewCycleLabelInput = document.getElementById("reviewCycleLabelInput");
  const reviewSalesUnitsInput = document.getElementById("reviewSalesUnitsInput");
  const reviewSalesAmountInput = document.getElementById("reviewSalesAmountInput");
  const reviewMarginRateInput = document.getElementById("reviewMarginRateInput");
  const reviewDecisionInput = document.getElementById("reviewDecisionInput");
  const reviewNotesInput = document.getElementById("reviewNotesInput");
  if (reviewDateInput) reviewDateInput.value = values.review_date || defaults.review_date || "";
  if (reviewCycleLabelInput) reviewCycleLabelInput.value = values.cycle_label || defaults.cycle_label || "周期复盘";
  if (reviewSalesUnitsInput) reviewSalesUnitsInput.value = values.sales_units ?? 0;
  if (reviewSalesAmountInput) reviewSalesAmountInput.value = values.sales_amount ?? 0;
  if (reviewMarginRateInput) reviewMarginRateInput.value = values.gross_margin_rate ?? defaults.gross_margin_rate ?? "0";
  if (reviewDecisionInput) reviewDecisionInput.value = values.decision || defaults.decision || "observe";
  if (reviewNotesInput) reviewNotesInput.value = values.notes || "";
}

function currentReviewCandidate() {
  const queue = procurementQueue();
  if (!queue.length) return null;
  if (!appState.selectedReviewCandidateId || !queue.some((item) => Number(item.candidate_id) === Number(appState.selectedReviewCandidateId))) {
    appState.selectedReviewCandidateId = Number(queue[0].candidate_id);
  }
  const selected = queue.find((item) => Number(item.candidate_id) === Number(appState.selectedReviewCandidateId)) || queue[0];
  const availableLogs = selected?.review_logs || [];
  if (appState.selectedReviewLogId && !availableLogs.some((item) => Number(item.id) === Number(appState.selectedReviewLogId))) {
    appState.selectedReviewLogId = null;
  }
  return selected;
}

function renderReviewModule() {
  const queue = procurementQueue();
  const summary = procurementSummary();
  const selected = currentReviewCandidate();
  const selectedLog = (selected?.review_logs || []).find((item) => Number(item.id) === Number(appState.selectedReviewLogId)) || null;
  const summaryPanel = document.getElementById("reviewSummaryPanel");
  const reviewTable = document.getElementById("reviewTable");
  const decisionOptions = (appState.meta?.review_decision_options || [])
    .map((item) => `<option value="${escapeHtml(item.key)}">${escapeHtml(item.label)}</option>`)
    .join("");

  summaryPanel.innerHTML = `
    <div class="stack">
      <div class="card stack">
        <div class="inline-actions">
          <div>
            <h3>复盘重点</h3>
            <p class="muted">这里记录真实上新和周期复盘，不再只是系统推演。</p>
          </div>
        </div>
        <div class="selection-summary">
          <div class="score-chip"><div class="label">计划中</div><div class="value">${summary.planned_count || 0}</div></div>
          <div class="score-chip"><div class="label">已上新</div><div class="value">${summary.launched_count || 0}</div></div>
          <div class="score-chip"><div class="label">待复盘</div><div class="value">${summary.review_due_count || 0}</div></div>
          <div class="score-chip"><div class="label">已追踪</div><div class="value">${summary.tracked_count || 0}</div></div>
        </div>
      </div>
      <div class="overview-layout">
        <article class="card stack">
          <div class="inline-actions">
            <div>
              <h3>${selectedLog ? "编辑复盘记录" : "新增复盘记录"}</h3>
              <p class="muted">支持 7 / 14 / 30 天快捷模板，也可以从历史记录中点编辑继续调整。</p>
            </div>
          </div>
          <label>
            <span>复盘商品</span>
            <select id="reviewCandidateSelect">
              ${queue
                .map(
                  (item) =>
                    `<option value="${item.candidate_id}" ${Number(item.candidate_id) === Number(selected?.candidate_id) ? "selected" : ""}>${escapeHtml(item.brand)} - ${escapeHtml(item.product_name)}</option>`,
                )
                .join("")}
            </select>
          </label>
          <div class="inline-actions">
            <button type="button" class="ghost-btn small-btn" data-action="apply-review-template" data-days="7">7天模板</button>
            <button type="button" class="ghost-btn small-btn" data-action="apply-review-template" data-days="14">14天模板</button>
            <button type="button" class="ghost-btn small-btn" data-action="apply-review-template" data-days="30">30天模板</button>
            ${selectedLog ? `<button type="button" class="ghost-btn small-btn" data-action="cancel-review-edit">取消编辑</button>` : ""}
          </div>
          <div class="filter-grid compact">
            <label>
              <span>复盘日期</span>
              <input type="date" id="reviewDateInput" value="" />
            </label>
            <label>
              <span>周期标签</span>
              <input type="text" id="reviewCycleLabelInput" value="" />
            </label>
            <label>
              <span>销量数量</span>
              <input type="number" id="reviewSalesUnitsInput" min="0" step="1" value="0" />
            </label>
            <label>
              <span>销售额</span>
              <input type="number" id="reviewSalesAmountInput" min="0" step="0.01" value="0" />
            </label>
            <label>
              <span>毛利率(%)</span>
              <input type="number" id="reviewMarginRateInput" min="0" step="0.1" value="0" />
            </label>
            <label>
              <span>复盘结论</span>
              <select id="reviewDecisionInput">${decisionOptions}</select>
            </label>
          </div>
          <label>
            <span>备注</span>
            <textarea id="reviewNotesInput" rows="3" placeholder="例如：首周动销符合预期，可继续补货。"></textarea>
          </label>
          <div class="inline-actions">
            <button type="button" class="primary-btn" id="saveReviewLogBtn" data-review-id="${selectedLog ? selectedLog.id : ""}">${selectedLog ? "保存修改" : "保存复盘记录"}</button>
          </div>
        </article>
        <article class="card stack">
          <div class="inline-actions">
            <div>
              <h3>当前商品复盘历史</h3>
              <p class="muted">这里看实际上新、最近复盘和下一次该回看的时间，也可以直接编辑或删除记录。</p>
            </div>
          </div>
          ${
            selected
              ? `
                <div class="detail-card">
                  <strong>${escapeHtml(selected.brand)} - ${escapeHtml(selected.product_name)}</strong>
                  <p class="muted">状态：${escapeHtml(selected.launch_status_label || launchStatusLabel(selected.launch_status))} / 实际上新 ${escapeHtml(selected.actual_launch_qty || 0)} 支 / 日期 ${escapeHtml(selected.actual_launch_date || "-")}</p>
                  <p class="muted">首单 ${escapeHtml(selected.first_order_qty || 0)} 支 / 下次复盘 ${escapeHtml(selected.next_review_date || "-")}</p>
                  <p class="muted">备注：${escapeHtml(selected.launch_notes || "暂无")}</p>
                </div>
                <div class="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>复盘日期</th>
                        <th>周期</th>
                        <th>销量</th>
                        <th>销售额</th>
                        <th>毛利率</th>
                        <th>结论</th>
                        <th>备注</th>
                        <th>操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      ${
                        (selected.review_logs || []).length
                          ? selected.review_logs
                              .map(
                                (log) => `
                                  <tr>
                                    <td>${escapeHtml(log.review_date)}</td>
                                    <td>${escapeHtml(log.cycle_label || "-")}</td>
                                    <td>${escapeHtml(log.sales_units || 0)}</td>
                                    <td>${formatCurrency(log.sales_amount || 0)}</td>
                                    <td>${log.gross_margin_rate ? formatPercent(log.gross_margin_rate) : "-"}</td>
                                    <td>${escapeHtml(log.decision_label || reviewDecisionLabel(log.decision))}</td>
                                    <td class="muted">${escapeHtml(log.notes || "-")}</td>
                                    <td>
                                      <button type="button" class="action-link" data-action="edit-review-log" data-review-id="${log.id}">编辑</button>
                                      /
                                      <button type="button" class="action-link" data-action="delete-review-log" data-review-id="${log.id}">删除</button>
                                    </td>
                                  </tr>
                                `,
                              )
                              .join("")
                          : `<tr><td colspan="8"><div class="empty-state">这款商品还没有复盘记录。</div></td></tr>`
                      }
                    </tbody>
                  </table>
                </div>
              `
              : `<div class="empty-state">当前还没有进入执行追踪的候选商品。</div>`
          }
        </article>
      </div>
    </div>
  `;

  reviewTable.querySelector("thead").innerHTML = `
    <tr>
      <th>商品</th>
      <th>状态</th>
      <th>首单数量</th>
      <th>实际上新</th>
      <th>最近复盘</th>
      <th>下次复盘</th>
      <th>操作</th>
    </tr>
  `;
  reviewTable.querySelector("tbody").innerHTML = queue.length
    ? queue
        .map(
          (item) => `
            <tr>
              <td><strong>${escapeHtml(item.brand)}</strong><br />${escapeHtml(item.product_name)}</td>
              <td><span class="badge ${item.review_due ? "orange" : "gray"}">${escapeHtml(item.launch_status_label || launchStatusLabel(item.launch_status))}</span></td>
              <td>${escapeHtml(item.first_order_qty || 0)}</td>
              <td>${escapeHtml(item.actual_launch_qty || 0)} 支<br /><span class="muted">${escapeHtml(item.actual_launch_date || "-")}</span></td>
              <td>${item.latest_review ? `${escapeHtml(item.latest_review.review_date)} / ${escapeHtml(item.latest_review.decision_label || reviewDecisionLabel(item.latest_review.decision))}` : "-"}</td>
              <td>${escapeHtml(item.next_review_date || "-")}</td>
              <td><button type="button" class="ghost-btn small-btn" data-action="select-review-candidate" data-candidate-id="${item.candidate_id}">查看并记录</button></td>
            </tr>
          `,
        )
        .join("")
    : `<tr><td colspan="7"><div class="empty-state">当前还没有进入执行追踪的候选商品。</div></td></tr>`;

  populateReviewForm(
    selected,
    selectedLog
      ? {
          review_date: selectedLog.review_date || "",
          cycle_label: selectedLog.cycle_label || "",
          sales_units: selectedLog.sales_units || 0,
          sales_amount: selectedLog.sales_amount || 0,
          gross_margin_rate: selectedLog.gross_margin_rate ? (Number(selectedLog.gross_margin_rate) * 100).toFixed(1) : "0",
          decision: selectedLog.decision || "observe",
          notes: selectedLog.notes || "",
        }
      : reviewTemplateValues(selected?.review_cycle_days || 14, selected),
  );
}

function bindWorkflowEnhancements() {
  const candidateTable = document.getElementById("candidateTable");
  candidateTable.addEventListener("click", async (event) => {
    const compareTarget = event.target.closest('[data-action="compare"]');
    if (compareTarget) {
      const select = document.getElementById("comparisonSelector");
      if (!select) return;
      select.value = compareTarget.dataset.id;
      await renderComparisonPanel();
      document.getElementById("comparisonPanel")?.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
  });

  document.getElementById("procurementActionTable")?.addEventListener("click", async (event) => {
    const button = event.target.closest('[data-action="save-launch-plan"]');
    if (!button) return;
    const row = button.closest("tr");
    const candidateId = Number(button.dataset.candidateId || 0);
    if (!row || !candidateId) return;
    const payload = {
      first_order_qty: Number(row.querySelector('[data-field="first_order_qty"]')?.value || 0),
      actual_launch_qty: Number(row.querySelector('[data-field="actual_launch_qty"]')?.value || 0),
      actual_launch_date: row.querySelector('[data-field="actual_launch_date"]')?.value || "",
      review_cycle_days: Number(row.querySelector('[data-field="review_cycle_days"]')?.value || 14),
      launch_status: row.querySelector('[data-field="launch_status"]')?.value || "planned",
    };
    try {
      button.disabled = true;
      await fetchJson(`/api/candidates/${candidateId}/launch-plan`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      await refreshState();
      appState.selectedReviewCandidateId = candidateId;
    } catch (error) {
      alert(error.message);
    } finally {
      button.disabled = false;
    }
  });

  document.getElementById("reviewModule")?.addEventListener("change", (event) => {
    const select = event.target.closest("#reviewCandidateSelect");
    if (!select) return;
    appState.selectedReviewCandidateId = Number(select.value || 0);
    appState.selectedReviewLogId = null;
    renderReviewModule();
  });

  document.getElementById("reviewModule")?.addEventListener("click", async (event) => {
    const selectButton = event.target.closest('[data-action="select-review-candidate"]');
    if (selectButton) {
      appState.selectedReviewCandidateId = Number(selectButton.dataset.candidateId || 0);
      appState.selectedReviewLogId = null;
      renderReviewModule();
      return;
    }

    const templateButton = event.target.closest('[data-action="apply-review-template"]');
    if (templateButton) {
      const selected = currentReviewCandidate();
      populateReviewForm(selected, reviewTemplateValues(Number(templateButton.dataset.days || 14), selected));
      appState.selectedReviewLogId = null;
      const saveButton = document.getElementById("saveReviewLogBtn");
      if (saveButton) {
        saveButton.dataset.reviewId = "";
        saveButton.textContent = "保存复盘记录";
      }
      return;
    }

    const editButton = event.target.closest('[data-action="edit-review-log"]');
    if (editButton) {
      const selected = currentReviewCandidate();
      const log = (selected?.review_logs || []).find((item) => Number(item.id) === Number(editButton.dataset.reviewId || 0));
      if (!log) return;
      appState.selectedReviewLogId = Number(log.id);
      renderReviewModule();
      return;
    }

    const cancelButton = event.target.closest('[data-action="cancel-review-edit"]');
    if (cancelButton) {
      appState.selectedReviewLogId = null;
      renderReviewModule();
      return;
    }

    const deleteButton = event.target.closest('[data-action="delete-review-log"]');
    if (deleteButton) {
      const candidateId = Number(document.getElementById("reviewCandidateSelect")?.value || 0);
      const reviewId = Number(deleteButton.dataset.reviewId || 0);
      if (!candidateId || !reviewId) return;
      if (!window.confirm("确认删除这条复盘记录吗？")) return;
      try {
        await fetchJson(`/api/candidates/${candidateId}/review-logs/${reviewId}`, { method: "DELETE" });
        if (Number(appState.selectedReviewLogId) === reviewId) {
          appState.selectedReviewLogId = null;
        }
        await refreshState();
        appState.selectedReviewCandidateId = candidateId;
      } catch (error) {
        alert(error.message);
      }
      return;
    }

    const saveButton = event.target.closest("#saveReviewLogBtn");
    if (!saveButton) return;
    const candidateId = Number(document.getElementById("reviewCandidateSelect")?.value || 0);
    if (!candidateId) return;
    const reviewId = Number(saveButton.dataset.reviewId || 0);
    const payload = {
      review_date: document.getElementById("reviewDateInput")?.value || "",
      cycle_label: document.getElementById("reviewCycleLabelInput")?.value || "",
      sales_units: Number(document.getElementById("reviewSalesUnitsInput")?.value || 0),
      sales_amount: Number(document.getElementById("reviewSalesAmountInput")?.value || 0),
      gross_margin_rate: Number(document.getElementById("reviewMarginRateInput")?.value || 0),
      decision: document.getElementById("reviewDecisionInput")?.value || "observe",
      notes: document.getElementById("reviewNotesInput")?.value || "",
    };
    try {
      saveButton.disabled = true;
      if (reviewId) {
        await fetchJson(`/api/candidates/${candidateId}/review-logs/${reviewId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      } else {
        await fetchJson(`/api/candidates/${candidateId}/review-logs`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      }
      await refreshState();
      appState.selectedReviewCandidateId = candidateId;
      appState.selectedReviewLogId = null;
    } catch (error) {
      alert(error.message);
    } finally {
      saveButton.disabled = false;
    }
  });
}

appState.brandStrategyDetailCache = appState.brandStrategyDetailCache || new Map();
appState.selectedBrandStrategy = appState.selectedBrandStrategy || "";

function ensureStrategicWorkbenchContainersV5() {
  const candidateModule = document.getElementById("candidateModule");
  if (candidateModule && !document.getElementById("competitorWorkbenchPanel")) {
    const panel = document.createElement("div");
    panel.id = "competitorWorkbenchPanel";
    panel.className = "stack";
    const moduleHeader = candidateModule.querySelector(".module-header");
    candidateModule.insertBefore(panel, moduleHeader?.nextSibling || candidateModule.firstChild);
  }
}

function strategicOverviewCardsV5() {
  const market = appState.data?.market_intelligence?.summary || {};
  const strategy = appState.data?.category_strategy?.summary || {};
  const competitor = appState.data?.competitor_watch?.summary || {};
  const procurement = appState.data?.procurement_actions?.summary || {};
  const learning = appState.data?.learning_summary?.summary || {};
  return [
    ["本期战略动作", (appState.data?.category_strategy?.strategic_actions_flat || []).length, "本月需要优先处理的类目层动作"],
    ["本期采购动作", (appState.data?.procurement_actions?.all || []).length, "已经收束到执行层的具体动作"],
    ["高风险提醒", (strategy.structure_gap_count || 0) + (market.weak_confidence_count || 0), "结构缺口和弱可信样本需要优先处理"],
    ["重点竞品变化", competitor.event_count || 0, "近期品牌爆款、价格异动和赛道升温"],
    ["弱可信数据", market.weak_confidence_count || 0, "当前市场锚点还不够稳的 SKU 数"],
    ["待复盘项目", learning.evidence_count || 0, "已经进入学习闭环的复盘证据数"],
  ];
}

function strategicRiskRowsV5() {
  const risks = [];
  const market = appState.data?.market_intelligence || {};
  const strategy = appState.data?.category_strategy || {};
  const competitor = appState.data?.competitor_watch || {};
  if ((market.summary?.weak_confidence_count || 0) > 0) {
    risks.push({ label: "数据可信", text: `还有 ${market.summary.weak_confidence_count} 个 SKU 市场参考偏弱，建议优先补证据。` });
  }
  if ((strategy.summary?.brand_concentration_risk || false) && strategy.summary?.top_brand) {
    risks.push({ label: "品牌集中", text: `${strategy.summary.top_brand} 占比偏高，建议检查是否要收缩或分散。` });
  }
  if ((competitor.summary?.new_hot_count || 0) > 0) {
    risks.push({ label: "竞品变化", text: `本期有 ${competitor.summary.new_hot_count} 个同品牌缺失爆款机会，需要尽快判断是否补进。` });
  }
  return risks;
}

function strategicActionsV5() {
  return appState.data?.category_strategy?.strategic_actions_flat || [];
}

async function loadBrandStrategyDetailV5(brand) {
  if (!brand) return null;
  const key = String(brand);
  if (appState.brandStrategyDetailCache.has(key)) {
    return appState.brandStrategyDetailCache.get(key);
  }
  const detail = await fetchJson(`/api/brand-strategy/${encodeURIComponent(key)}`);
  appState.brandStrategyDetailCache.set(key, detail);
  return detail;
}

function renderBrandStrategyDetailV5(detail) {
  if (!detail?.brand) {
    return `<div class="empty-state">点击品牌战略卡后，这里会展开该品牌的在店角色、缺失爆款和近期变化。</div>`;
  }
  const card = detail.card || {};
  const missingHits = detail.missing_brand_hits || [];
  const recentEvents = detail.recent_events || [];
  return `
    <article class="card stack">
      <div class="inline-actions">
        <div>
          <h3>${escapeHtml(detail.brand)} 品牌战略卡</h3>
          <p class="muted">${escapeHtml(card.strategy_note || "这张卡会告诉你这个品牌在店里该扩、该收，还是该转定位。")}</p>
        </div>
        <span class="badge ${card.recommended_action === "建议扩品" ? "green" : card.recommended_action === "建议收缩" ? "red" : "orange"}">${escapeHtml(card.recommended_action || "建议维持")}</span>
      </div>
      <div class="selection-summary">
        <div class="score-chip"><div class="label">当前角色</div><div class="value">${escapeHtml(card.current_role || "-")}</div></div>
        <div class="score-chip"><div class="label">在店深度</div><div class="value">${escapeHtml(card.sku_count || 0)}</div></div>
        <div class="score-chip"><div class="label">缺失爆款</div><div class="value">${escapeHtml(card.missing_hit_count || 0)}</div></div>
        <div class="score-chip"><div class="label">目标深度</div><div class="value">${escapeHtml(card.target_depth || 0)}</div></div>
      </div>
      <div class="two-column">
        <div class="stack">
          <strong>当前在店 SKU</strong>
          ${
            (detail.current_brand_skus || []).length
              ? `<ul class="plain-list">${detail.current_brand_skus
                  .map((item) => `<li>${escapeHtml(item.product_name)} / ${escapeHtml(item.spec_text || "-")} / ${formatCurrency(item.current_price || 0)}</li>`)
                  .join("")}</ul>`
              : `<div class="empty-state">这个品牌当前还没有上架 SKU。</div>`
          }
        </div>
        <div class="stack">
          <strong>缺失爆款推荐</strong>
          ${
            missingHits.length
              ? `<ul class="plain-list">${missingHits
                  .map(
                    (item) =>
                      `<li>${escapeHtml(item.product_name)} / ${escapeHtml(item.spec_text || "-")} / 热度 ${escapeHtml(item.heat_score || 0)} / ${escapeHtml(item.recommendation_action || "")}</li>`,
                  )
                  .join("")}</ul>`
              : `<div class="empty-state">当前没有明确的同品牌缺失爆款。</div>`
          }
        </div>
      </div>
      <div class="stack">
        <strong>近期变化</strong>
        ${
          recentEvents.length
            ? `<ul class="plain-list">${recentEvents
                .map((item) => `<li>${escapeHtml(item.title)}：${escapeHtml(item.summary || "")}</li>`)
                .join("")}</ul>`
            : `<div class="empty-state">这个品牌最近没有明显变化事件。</div>`
        }
      </div>
    </article>
  `;
}

function renderCompetitorWorkbenchV5() {
  ensureStrategicWorkbenchContainersV5();
  const panel = document.getElementById("competitorWorkbenchPanel");
  if (!panel) return;
  const competitor = appState.data?.competitor_watch || {};
  const market = appState.data?.market_intelligence || {};
  const suggested = competitor.suggested_watch_brands || [];
  const events = competitor.events || [];
  panel.innerHTML = `
    <article class="card stack">
      <div class="inline-actions">
        <div>
          <h3>竞品情报</h3>
          <p class="muted">把品牌观察名单、关键事件和数据空洞放在一起看，避免只靠单次抓取做判断。</p>
        </div>
      </div>
      <div class="selection-summary">
        <div class="score-chip"><div class="label">观察品牌</div><div class="value">${escapeHtml(competitor.summary?.tracked_brand_count || 0)}</div></div>
        <div class="score-chip"><div class="label">事件总数</div><div class="value">${escapeHtml(competitor.summary?.event_count || 0)}</div></div>
        <div class="score-chip"><div class="label">新增爆款</div><div class="value">${escapeHtml(competitor.summary?.new_hot_count || 0)}</div></div>
        <div class="score-chip"><div class="label">数据空洞</div><div class="value">${escapeHtml(market.highlights?.data_holes || 0)}</div></div>
      </div>
      <div class="inline-actions">
        <label class="inline-select">
          <span>加入观察名单</span>
          <input type="text" id="watchBrandInput" placeholder="输入品牌名，如：狮王" />
        </label>
        <button type="button" class="ghost-btn" data-action="save-watch-brand">加入观察</button>
      </div>
      ${
        suggested.length
          ? `<div class="inline-actions">${suggested
              .map(
                (item) =>
                  `<button type="button" class="ghost-btn small-btn" data-action="quick-watch-brand" data-brand="${escapeHtml(item.brand)}">${escapeHtml(item.brand)}</button>`,
              )
              .join("")}</div>`
          : ""
      }
      <div class="two-column">
        <div class="stack">
          <strong>近期事件流</strong>
          ${
            events.length
              ? `<ul class="risk-list">${events
                  .slice(0, 8)
                  .map((item) => `<li><strong>${escapeHtml(item.title)}：</strong>${escapeHtml(item.summary || "")}</li>`)
                  .join("")}</ul>`
              : `<div class="empty-state">当前还没有竞品变化事件。</div>`
          }
        </div>
        <div class="stack">
          <strong>观察名单</strong>
          ${
            (competitor.watchlists || []).length
              ? `<ul class="plain-list">${competitor.watchlists
                  .map((item) => `<li>${escapeHtml(item.brand)} / ${item.active ? "监控中" : "已暂停"} / ${escapeHtml((item.source_platforms || []).join("、") || "默认平台")}</li>`)
                  .join("")}</ul>`
              : `<div class="empty-state">当前还没有手动指定的重点品牌观察名单。</div>`
          }
        </div>
      </div>
    </article>
  `;
}

const __renderMetaV5Base = renderMeta;
const __renderOverviewModuleV5Base = renderOverviewModule;
const __renderReviewModuleV5Base = renderReviewModule;
const __refreshStateV5Base = refreshState;

renderMeta = function renderMetaV5() {
  __renderMetaV5Base();
  const navButtons = Array.from(document.querySelectorAll(".nav-btn"));
  const workbenchNav = appState.meta?.workbench_nav || [];
  workbenchNav.forEach((item, index) => {
    if (navButtons[index]) {
      navButtons[index].textContent = item.label;
      navButtons[index].dataset.target = item.target;
    }
  });
  if (navButtons[4]) navButtons[4].textContent = "执行层";
  if (navButtons[5]) navButtons[5].textContent = "工具与数据";
  document.querySelector('#overviewModule .module-header h2')?.replaceChildren(document.createTextNode("今日决策"));
  document.querySelector('#dashboardModule .module-header h2')?.replaceChildren(document.createTextNode("类目战略"));
  document.querySelector('#candidateModule .module-header h2')?.replaceChildren(document.createTextNode("竞品情报"));
  document.querySelector('#reviewModule .module-header h2')?.replaceChildren(document.createTextNode("复盘学习"));
};

renderSummary = function renderSummaryV5() {
  const cards = strategicOverviewCardsV5();
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
};

renderOverviewModule = function renderOverviewModuleV5() {
  __renderOverviewModuleV5Base();
  const kpiContainer = document.getElementById("overviewKpiGrid");
  const brandPanel = document.getElementById("overviewBrandOpportunityPanel");
  const riskPanel = document.getElementById("overviewRiskPanel");
  const cards = strategicOverviewCardsV5();
  kpiContainer.innerHTML = cards
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
  const actions = strategicActionsV5();
  const events = appState.data?.competitor_watch?.events || [];
  brandPanel.innerHTML = `
    <div class="inline-actions">
      <div>
        <h3>本期战略动作</h3>
        <p class="muted">先看类目层和品牌层的动作，再往下落到具体采购动作。</p>
      </div>
    </div>
    ${
      actions.length
        ? `<ul class="risk-list">${actions
            .slice(0, 6)
            .map((item) => `<li><strong>${escapeHtml(item.type)}：</strong>${escapeHtml(item.label)}，${escapeHtml(item.summary || "")}</li>`)
            .join("")}</ul>`
        : `<div class="empty-state">当前还没有明确的战略动作，建议先补市场情报或复核结构目标。</div>`
    }
  `;
  const riskRows = strategicRiskRowsV5();
  riskPanel.innerHTML = `
    <div class="inline-actions">
      <div>
        <h3>重点竞品变化与风险</h3>
        <p class="muted">把高风险提醒和竞品变化放在一起看，避免只盯门店内部数据。</p>
      </div>
    </div>
    ${
      riskRows.length || events.length
        ? `<ul class="risk-list">${riskRows
            .map((item) => `<li><strong>${escapeHtml(item.label)}：</strong>${escapeHtml(item.text)}</li>`)
            .join("")}${events
            .slice(0, 3)
            .map((item) => `<li><strong>${escapeHtml(item.title)}：</strong>${escapeHtml(item.summary || "")}</li>`)
            .join("")}</ul>`
        : `<div class="empty-state">当前没有明显的高风险提醒或竞品异动。</div>`
    }
  `;
};

renderDashboard = function renderDashboardV5() {
  const panel = document.getElementById("dashboardPanel");
  const strategy = appState.data?.category_strategy || {};
  const brandCards = appState.data?.brand_strategy_cards || [];
  if (!appState.selectedBrandStrategy && brandCards.length) {
    appState.selectedBrandStrategy = brandCards[0].brand;
    loadBrandStrategyDetailV5(appState.selectedBrandStrategy).then(() => renderDashboard()).catch(() => {});
  }
  const selectedDetail = appState.brandStrategyDetailCache.get(appState.selectedBrandStrategy || "") || null;
  panel.innerHTML = `
    <div class="stack">
      <div class="overview-layout">
        <article class="card stack">
          <div class="inline-actions">
            <div>
              <h3>类目战略地图</h3>
              <p class="muted">回答四个问题：价格带是否均衡、功效是否完整、品牌是否过度集中、引流/常规/利润是否合理。</p>
            </div>
          </div>
          <div class="selection-summary">
            <div class="score-chip"><div class="label">结构缺口</div><div class="value">${escapeHtml(strategy.summary?.structure_gap_count || 0)}</div></div>
            <div class="score-chip"><div class="label">品牌数</div><div class="value">${escapeHtml(strategy.summary?.brand_count || 0)}</div></div>
            <div class="score-chip"><div class="label">头部品牌</div><div class="value">${escapeHtml(strategy.summary?.top_brand || "-")}</div></div>
            <div class="score-chip"><div class="label">头部占比</div><div class="value">${formatPercent(strategy.summary?.top_brand_share || 0)}</div></div>
          </div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>角色</th><th>当前占比</th><th>目标占比</th><th>偏差</th></tr></thead>
              <tbody>
                ${(strategy.role_mix || [])
                  .map(
                    (item) => `
                      <tr>
                        <td>${escapeHtml(item.role)}</td>
                        <td>${formatPercent(item.current_share || 0)}</td>
                        <td>${formatPercent(item.target_share || 0)}</td>
                        <td>${formatPercent(item.gap || 0)}</td>
                      </tr>
                    `,
                  )
                  .join("") || `<tr><td colspan="4"><div class="empty-state">当前还没有角色目标数据。</div></td></tr>`}
              </tbody>
            </table>
          </div>
        </article>
        <article class="card stack">
          <div class="inline-actions">
            <div>
              <h3>价格架构</h3>
              <p class="muted">把当前门店价格架构和市场主流结构放在一起看，明确哪些商品负责打价格，哪些负责守利润。</p>
            </div>
          </div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>价格带</th><th>门店</th><th>市场候选</th><th>偏差</th><th>定位</th></tr></thead>
              <tbody>
                ${(strategy.price_architecture || [])
                  .map(
                    (item) => `
                      <tr>
                        <td>${escapeHtml(item.band)}</td>
                        <td>${escapeHtml(item.current_count || 0)}</td>
                        <td>${escapeHtml(item.market_count || 0)}</td>
                        <td>${escapeHtml(item.gap_count || 0)}</td>
                        <td>${escapeHtml(item.decision_role || "")}</td>
                      </tr>
                    `,
                  )
                  .join("") || `<tr><td colspan="5"><div class="empty-state">当前还没有价格架构数据。</div></td></tr>`}
              </tbody>
            </table>
          </div>
        </article>
      </div>
      <article class="card stack">
        <div class="inline-actions">
          <div>
            <h3>品牌战略卡</h3>
            <p class="muted">把每个品牌当成一个组合决策对象，而不是孤立的几个 SKU。</p>
          </div>
        </div>
        <div class="stack compact-stack">
          ${(brandCards || [])
            .slice(0, 12)
            .map(
              (item) => `
                <button type="button" class="detail-card ${appState.selectedBrandStrategy === item.brand ? "selected-card" : ""}" data-action="open-brand-strategy" data-brand="${escapeHtml(item.brand)}">
                  <div class="inline-actions">
                    <strong>${escapeHtml(item.brand)}</strong>
                    <span class="badge ${item.recommended_action === "建议扩品" ? "green" : item.recommended_action === "建议收缩" ? "red" : "orange"}">${escapeHtml(item.recommended_action)}</span>
                  </div>
                  <p class="muted">${escapeHtml(item.current_role)} / 在店 ${escapeHtml(item.sku_count || 0)} 个 / 缺失爆款 ${escapeHtml(item.missing_hit_count || 0)} 个</p>
                </button>
              `,
            )
            .join("") || `<div class="empty-state">当前还没有可用的品牌战略卡。</div>`}
        </div>
      </article>
      <div id="brandStrategyDetailPanel">${renderBrandStrategyDetailV5(selectedDetail)}</div>
    </div>
  `;
};

renderReviewModule = function renderReviewModuleV5() {
  __renderReviewModuleV5Base();
  const summaryPanel = document.getElementById("reviewSummaryPanel");
  const learning = appState.data?.learning_summary || {};
  const proposals = learning.feedback_proposals || [];
  const oldBlock = summaryPanel.querySelector('[data-role="strategic-learning"]');
  oldBlock?.remove();
  const wrapper = document.createElement("div");
  wrapper.setAttribute("data-role", "strategic-learning");
  wrapper.className = "stack";
  wrapper.innerHTML = `
    <div class="card stack">
      <div class="inline-actions">
        <div>
          <h3>学习面板</h3>
          <p class="muted">把复盘从“记日志”升级成“学规律”：哪个平台更有效、哪个价格带更容易成功、哪些品牌适合做引流或利润。</p>
        </div>
      </div>
      <div class="selection-summary">
        <div class="score-chip"><div class="label">证据数</div><div class="value">${escapeHtml(learning.summary?.evidence_count || 0)}</div></div>
        <div class="score-chip"><div class="label">30天成功率</div><div class="value">${formatPercent(learning.summary?.success_rate_30d || 0)}</div></div>
        <div class="score-chip"><div class="label">90天存活率</div><div class="value">${formatPercent(learning.summary?.survival_rate_90d || 0)}</div></div>
        <div class="score-chip"><div class="label">待确认提案</div><div class="value">${escapeHtml(learning.summary?.pending_proposal_count || 0)}</div></div>
      </div>
      <div class="overview-layout">
        <div class="stack">
          <strong>平台有效性</strong>
          ${
            (learning.platform_effectiveness || []).length
              ? `<ul class="plain-list">${learning.platform_effectiveness
                  .slice(0, 5)
                  .map((item) => `<li>${escapeHtml(item.label)} / 成功率 ${formatPercent(item.success_rate || 0)} / 样本 ${escapeHtml(item.sample_count || 0)}</li>`)
                  .join("")}</ul>`
              : `<div class="empty-state">当前复盘样本还不够，先积累 3 条以上证据再看学习结论。</div>`
          }
        </div>
        <div class="stack">
          <strong>角色与功效表现</strong>
          ${
            (learning.role_performance || []).length
              ? `<ul class="plain-list">${learning.role_performance
                  .slice(0, 4)
                  .map((item) => `<li>${escapeHtml(item.label)} / 成功率 ${formatPercent(item.success_rate || 0)} / 样本 ${escapeHtml(item.sample_count || 0)}</li>`)
                  .join("")}</ul>`
              : `<div class="empty-state">当前还没有足够的角色复盘证据。</div>`
          }
        </div>
      </div>
    </div>
    <article class="card stack">
      <div class="inline-actions">
        <div>
          <h3>规则修正提案</h3>
          <p class="muted">系统只提案，不自动改规则。你确认后，下一轮推荐和首单量才会变化。</p>
        </div>
      </div>
      ${
        proposals.length
          ? `<div class="stack compact-stack">${proposals
              .slice(0, 8)
              .map(
                (item) => `
                  <article class="detail-card">
                    <div class="inline-actions">
                      <strong>${escapeHtml(item.title || item.proposal_key)}</strong>
                      <span class="badge ${item.decision_status === "accepted" ? "green" : item.decision_status === "rejected" ? "red" : "orange"}">${escapeHtml(item.decision_status || "pending")}</span>
                    </div>
                    <p class="muted">${escapeHtml(item.evidence_summary || item.impact_summary || "")}</p>
                    <div class="inline-actions">
                      <button type="button" class="ghost-btn small-btn" data-action="proposal-decision" data-decision="accepted" data-proposal-key="${escapeHtml(item.proposal_key)}">确认生效</button>
                      <button type="button" class="ghost-btn small-btn" data-action="proposal-decision" data-decision="rejected" data-proposal-key="${escapeHtml(item.proposal_key)}">暂不采纳</button>
                    </div>
                  </article>
                `,
              )
              .join("")}</div>`
          : `<div class="empty-state">当前还没有形成稳定的规则修正提案。</div>`
      }
    </article>
  `;
  summaryPanel.prepend(wrapper);
};

refreshState = async function refreshStateV5() {
  await __refreshStateV5Base();
  renderCompetitorWorkbenchV5();
};

if (!appState.__strategicWorkbenchBound) {
  appState.__strategicWorkbenchBound = true;
  document.addEventListener("click", async (event) => {
    const brandButton = event.target.closest('[data-action="open-brand-strategy"]');
    if (brandButton) {
      const brand = brandButton.dataset.brand || "";
      appState.selectedBrandStrategy = brand;
      try {
        await loadBrandStrategyDetailV5(brand);
      } catch (error) {
        alert(error.message);
      }
      renderDashboard();
      return;
    }

    const saveWatch = event.target.closest('[data-action="save-watch-brand"]');
    if (saveWatch) {
      const input = document.getElementById("watchBrandInput");
      const brand = input?.value?.trim();
      if (!brand) return;
      try {
        await fetchJson("/api/competitor/watchlists", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ brand, source_platforms: ["taobao", "tmall", "jd"] }),
        });
        if (input) input.value = "";
        await refreshState();
      } catch (error) {
        alert(error.message);
      }
      return;
    }

    const quickWatch = event.target.closest('[data-action="quick-watch-brand"]');
    if (quickWatch) {
      const brand = quickWatch.dataset.brand || "";
      try {
        await fetchJson("/api/competitor/watchlists", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ brand, source_platforms: ["taobao", "tmall", "jd"] }),
        });
        await refreshState();
      } catch (error) {
        alert(error.message);
      }
      return;
    }

    const proposalButton = event.target.closest('[data-action="proposal-decision"]');
    if (proposalButton) {
      try {
        await fetchJson(`/api/review-feedback/proposals/${encodeURIComponent(proposalButton.dataset.proposalKey || "")}/decision`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ decision: proposalButton.dataset.decision || "rejected" }),
        });
        await refreshState();
      } catch (error) {
        alert(error.message);
      }
    }
  });
}

if (appState.meta && appState.data) {
  renderMeta();
  renderSummary();
  renderOverviewModule();
  renderDashboard();
  renderReviewModule();
  renderCompetitorWorkbenchV5();
}

// Final override for review workflow: quick templates + edit/delete review logs.
function reviewTemplateValues(days, selected) {
  const safeDays = Number(days || 14);
  const rawLaunchDate = selected?.actual_launch_date || new Date().toISOString().slice(0, 10);
  const baseDate = new Date(rawLaunchDate);
  const reviewDate = Number.isNaN(baseDate.getTime()) ? new Date() : baseDate;
  reviewDate.setDate(reviewDate.getDate() + safeDays);
  return {
    review_date: reviewDate.toISOString().slice(0, 10),
    cycle_label: `${safeDays}天复盘`,
    sales_units: 0,
    sales_amount: 0,
    gross_margin_rate: selected ? (Number(selected.expected_margin || 0) * 100).toFixed(1) : "0",
    decision: safeDays <= 7 ? "observe" : safeDays <= 14 ? "replenish" : "reprice",
    notes: "",
  };
}

function populateReviewForm(selected, values = {}) {
  const defaults = values.review_date ? values : reviewTemplateValues(selected?.review_cycle_days || 14, selected);
  const reviewDateInput = document.getElementById("reviewDateInput");
  const reviewCycleLabelInput = document.getElementById("reviewCycleLabelInput");
  const reviewSalesUnitsInput = document.getElementById("reviewSalesUnitsInput");
  const reviewSalesAmountInput = document.getElementById("reviewSalesAmountInput");
  const reviewMarginRateInput = document.getElementById("reviewMarginRateInput");
  const reviewDecisionInput = document.getElementById("reviewDecisionInput");
  const reviewNotesInput = document.getElementById("reviewNotesInput");
  if (reviewDateInput) reviewDateInput.value = values.review_date ?? defaults.review_date ?? "";
  if (reviewCycleLabelInput) reviewCycleLabelInput.value = values.cycle_label ?? defaults.cycle_label ?? "周期复盘";
  if (reviewSalesUnitsInput) reviewSalesUnitsInput.value = values.sales_units ?? defaults.sales_units ?? 0;
  if (reviewSalesAmountInput) reviewSalesAmountInput.value = values.sales_amount ?? defaults.sales_amount ?? 0;
  if (reviewMarginRateInput) reviewMarginRateInput.value = values.gross_margin_rate ?? defaults.gross_margin_rate ?? "0";
  if (reviewDecisionInput) reviewDecisionInput.value = values.decision ?? defaults.decision ?? "observe";
  if (reviewNotesInput) reviewNotesInput.value = values.notes ?? defaults.notes ?? "";
}

function currentReviewCandidate() {
  const queue = procurementQueue();
  if (!queue.length) return null;
  if (!appState.selectedReviewCandidateId || !queue.some((item) => Number(item.candidate_id) === Number(appState.selectedReviewCandidateId))) {
    appState.selectedReviewCandidateId = Number(queue[0].candidate_id);
  }
  const selected = queue.find((item) => Number(item.candidate_id) === Number(appState.selectedReviewCandidateId)) || queue[0];
  const availableLogs = selected?.review_logs || [];
  if (appState.selectedReviewLogId && !availableLogs.some((item) => Number(item.id) === Number(appState.selectedReviewLogId))) {
    appState.selectedReviewLogId = null;
  }
  return selected;
}

function renderReviewModule() {
  const queue = procurementQueue();
  const summary = procurementSummary();
  const selected = currentReviewCandidate();
  const selectedLog = (selected?.review_logs || []).find((item) => Number(item.id) === Number(appState.selectedReviewLogId)) || null;
  const summaryPanel = document.getElementById("reviewSummaryPanel");
  const reviewTable = document.getElementById("reviewTable");
  if (!summaryPanel || !reviewTable) return;

  const decisionOptions = (appState.meta?.review_decision_options || [])
    .map((item) => `<option value="${escapeHtml(item.key)}">${escapeHtml(item.label)}</option>`)
    .join("");

  summaryPanel.innerHTML = `
    <div class="stack">
      <div class="card stack">
        <div class="inline-actions">
          <div>
            <h3>复盘重点</h3>
            <p class="muted">这里记录真实上新和周期复盘，不再只是系统推演。</p>
          </div>
        </div>
        <div class="selection-summary">
          <div class="score-chip"><div class="label">计划中</div><div class="value">${summary.planned_count || 0}</div></div>
          <div class="score-chip"><div class="label">已上新</div><div class="value">${summary.launched_count || 0}</div></div>
          <div class="score-chip"><div class="label">待复盘</div><div class="value">${summary.review_due_count || 0}</div></div>
          <div class="score-chip"><div class="label">已追踪</div><div class="value">${summary.tracked_count || 0}</div></div>
        </div>
      </div>
      <div class="overview-layout">
        <article class="card stack">
          <div class="inline-actions">
            <div>
              <h3>${selectedLog ? "编辑复盘记录" : "新增复盘记录"}</h3>
              <p class="muted">支持 7 / 14 / 30 天快捷模板，也可以从历史记录里点编辑继续调整。</p>
            </div>
          </div>
          <label>
            <span>复盘商品</span>
            <select id="reviewCandidateSelect">
              ${queue
                .map(
                  (item) =>
                    `<option value="${item.candidate_id}" ${Number(item.candidate_id) === Number(selected?.candidate_id) ? "selected" : ""}>${escapeHtml(item.brand)} - ${escapeHtml(item.product_name)}</option>`,
                )
                .join("")}
            </select>
          </label>
          <div class="inline-actions">
            <button type="button" class="ghost-btn small-btn" data-action="apply-review-template" data-days="7">7天模板</button>
            <button type="button" class="ghost-btn small-btn" data-action="apply-review-template" data-days="14">14天模板</button>
            <button type="button" class="ghost-btn small-btn" data-action="apply-review-template" data-days="30">30天模板</button>
            ${selectedLog ? `<button type="button" class="ghost-btn small-btn" data-action="cancel-review-edit">取消编辑</button>` : ""}
          </div>
          <div class="filter-grid compact">
            <label>
              <span>复盘日期</span>
              <input type="date" id="reviewDateInput" value="" />
            </label>
            <label>
              <span>周期标签</span>
              <input type="text" id="reviewCycleLabelInput" value="" />
            </label>
            <label>
              <span>销量数量</span>
              <input type="number" id="reviewSalesUnitsInput" min="0" step="1" value="0" />
            </label>
            <label>
              <span>销售额</span>
              <input type="number" id="reviewSalesAmountInput" min="0" step="0.01" value="0" />
            </label>
            <label>
              <span>毛利率(%)</span>
              <input type="number" id="reviewMarginRateInput" min="0" step="0.1" value="0" />
            </label>
            <label>
              <span>复盘结论</span>
              <select id="reviewDecisionInput">${decisionOptions}</select>
            </label>
          </div>
          <label>
            <span>备注</span>
            <textarea id="reviewNotesInput" rows="3" placeholder="例如：首周动销符合预期，可以继续补货。"></textarea>
          </label>
          <div class="inline-actions">
            <button type="button" class="primary-btn" id="saveReviewLogBtn" data-review-id="${selectedLog ? selectedLog.id : ""}">${selectedLog ? "保存修改" : "保存复盘记录"}</button>
          </div>
        </article>
        <article class="card stack">
          <div class="inline-actions">
            <div>
              <h3>当前商品复盘历史</h3>
              <p class="muted">这里看实际上新、最近复盘和下一次该回看的时间，也可以直接编辑或删除记录。</p>
            </div>
          </div>
          ${
            selected
              ? `
                <div class="detail-card">
                  <strong>${escapeHtml(selected.brand)} - ${escapeHtml(selected.product_name)}</strong>
                  <p class="muted">状态：${escapeHtml(selected.launch_status_label || launchStatusLabel(selected.launch_status))} / 实际上新 ${escapeHtml(selected.actual_launch_qty || 0)} 支 / 日期 ${escapeHtml(selected.actual_launch_date || "-")}</p>
                  <p class="muted">首单 ${escapeHtml(selected.first_order_qty || 0)} 支 / 下次复盘 ${escapeHtml(selected.next_review_date || "-")}</p>
                  <p class="muted">备注：${escapeHtml(selected.launch_notes || "暂无")}</p>
                </div>
                <div class="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>复盘日期</th>
                        <th>周期</th>
                        <th>销量</th>
                        <th>销售额</th>
                        <th>毛利率</th>
                        <th>结论</th>
                        <th>备注</th>
                        <th>操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      ${
                        (selected.review_logs || []).length
                          ? selected.review_logs
                              .map(
                                (log) => `
                                  <tr>
                                    <td>${escapeHtml(log.review_date)}</td>
                                    <td>${escapeHtml(log.cycle_label || "-")}</td>
                                    <td>${escapeHtml(log.sales_units || 0)}</td>
                                    <td>${formatCurrency(log.sales_amount || 0)}</td>
                                    <td>${log.gross_margin_rate || log.gross_margin_rate === 0 ? formatPercent(log.gross_margin_rate) : "-"}</td>
                                    <td>${escapeHtml(log.decision_label || reviewDecisionLabel(log.decision))}</td>
                                    <td class="muted">${escapeHtml(log.notes || "-")}</td>
                                    <td>
                                      <button type="button" class="action-link" data-action="edit-review-log" data-review-id="${log.id}">编辑</button>
                                      /
                                      <button type="button" class="action-link" data-action="delete-review-log" data-review-id="${log.id}">删除</button>
                                    </td>
                                  </tr>
                                `,
                              )
                              .join("")
                          : `<tr><td colspan="8"><div class="empty-state">这款商品还没有复盘记录。</div></td></tr>`
                      }
                    </tbody>
                  </table>
                </div>
              `
              : `<div class="empty-state">当前还没有进入执行追踪的候选商品。</div>`
          }
        </article>
      </div>
    </div>
  `;

  reviewTable.querySelector("thead").innerHTML = `
    <tr>
      <th>商品</th>
      <th>状态</th>
      <th>首单数量</th>
      <th>实际上新</th>
      <th>最近复盘</th>
      <th>下次复盘</th>
      <th>操作</th>
    </tr>
  `;

  reviewTable.querySelector("tbody").innerHTML = queue.length
    ? queue
        .map(
          (item) => `
            <tr>
              <td><strong>${escapeHtml(item.brand)}</strong><br />${escapeHtml(item.product_name)}</td>
              <td><span class="badge ${item.review_due ? "orange" : "gray"}">${escapeHtml(item.launch_status_label || launchStatusLabel(item.launch_status))}</span></td>
              <td>${escapeHtml(item.first_order_qty || 0)}</td>
              <td>${escapeHtml(item.actual_launch_qty || 0)} 支<br /><span class="muted">${escapeHtml(item.actual_launch_date || "-")}</span></td>
              <td>${item.latest_review ? `${escapeHtml(item.latest_review.review_date)} / ${escapeHtml(item.latest_review.decision_label || reviewDecisionLabel(item.latest_review.decision))}` : "-"}</td>
              <td>${escapeHtml(item.next_review_date || "-")}</td>
              <td><button type="button" class="ghost-btn small-btn" data-action="select-review-candidate" data-candidate-id="${item.candidate_id}">查看并记录</button></td>
            </tr>
          `,
        )
        .join("")
    : `<tr><td colspan="7"><div class="empty-state">当前还没有进入执行追踪的候选商品。</div></td></tr>`;

  populateReviewForm(
    selected,
    selectedLog
      ? {
          review_date: selectedLog.review_date || "",
          cycle_label: selectedLog.cycle_label || "",
          sales_units: selectedLog.sales_units || 0,
          sales_amount: selectedLog.sales_amount || 0,
          gross_margin_rate:
            selectedLog.gross_margin_rate || selectedLog.gross_margin_rate === 0
              ? (Number(selectedLog.gross_margin_rate) * 100).toFixed(1)
              : "0",
          decision: selectedLog.decision || "observe",
          notes: selectedLog.notes || "",
        }
      : reviewTemplateValues(selected?.review_cycle_days || 14, selected),
  );
}

function bindWorkflowEnhancements() {
  const candidateTable = document.getElementById("candidateTable");
  candidateTable?.addEventListener("click", async (event) => {
    const compareTarget = event.target.closest('[data-action="compare"]');
    if (!compareTarget) return;
    const select = document.getElementById("comparisonSelector");
    if (!select) return;
    select.value = compareTarget.dataset.id;
    await renderComparisonPanel();
    document.getElementById("comparisonPanel")?.scrollIntoView({ behavior: "smooth", block: "start" });
  });

  document.getElementById("procurementActionTable")?.addEventListener("click", async (event) => {
    const button = event.target.closest('[data-action="save-launch-plan"]');
    if (!button) return;
    const row = button.closest("tr");
    const candidateId = Number(button.dataset.candidateId || 0);
    if (!row || !candidateId) return;
    const payload = {
      first_order_qty: Number(row.querySelector('[data-field="first_order_qty"]')?.value || 0),
      actual_launch_qty: Number(row.querySelector('[data-field="actual_launch_qty"]')?.value || 0),
      actual_launch_date: row.querySelector('[data-field="actual_launch_date"]')?.value || "",
      review_cycle_days: Number(row.querySelector('[data-field="review_cycle_days"]')?.value || 14),
      launch_status: row.querySelector('[data-field="launch_status"]')?.value || "planned",
    };
    try {
      button.disabled = true;
      await fetchJson(`/api/candidates/${candidateId}/launch-plan`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      await refreshState();
      appState.selectedReviewCandidateId = candidateId;
    } catch (error) {
      alert(error.message);
    } finally {
      button.disabled = false;
    }
  });

  document.getElementById("reviewModule")?.addEventListener("change", (event) => {
    const select = event.target.closest("#reviewCandidateSelect");
    if (!select) return;
    appState.selectedReviewCandidateId = Number(select.value || 0);
    appState.selectedReviewLogId = null;
    renderReviewModule();
  });

  document.getElementById("reviewModule")?.addEventListener("click", async (event) => {
    const selectButton = event.target.closest('[data-action="select-review-candidate"]');
    if (selectButton) {
      appState.selectedReviewCandidateId = Number(selectButton.dataset.candidateId || 0);
      appState.selectedReviewLogId = null;
      renderReviewModule();
      return;
    }

    const templateButton = event.target.closest('[data-action="apply-review-template"]');
    if (templateButton) {
      const selected = currentReviewCandidate();
      populateReviewForm(selected, reviewTemplateValues(Number(templateButton.dataset.days || 14), selected));
      appState.selectedReviewLogId = null;
      return;
    }

    const editButton = event.target.closest('[data-action="edit-review-log"]');
    if (editButton) {
      appState.selectedReviewLogId = Number(editButton.dataset.reviewId || 0);
      renderReviewModule();
      return;
    }

    const cancelEditButton = event.target.closest('[data-action="cancel-review-edit"]');
    if (cancelEditButton) {
      appState.selectedReviewLogId = null;
      renderReviewModule();
      return;
    }

    const deleteButton = event.target.closest('[data-action="delete-review-log"]');
    if (deleteButton) {
      const candidateId = Number(document.getElementById("reviewCandidateSelect")?.value || 0);
      const reviewId = Number(deleteButton.dataset.reviewId || 0);
      if (!candidateId || !reviewId) return;
      try {
        await fetchJson(`/api/candidates/${candidateId}/review-logs/${reviewId}`, { method: "DELETE" });
        if (Number(appState.selectedReviewLogId) === reviewId) {
          appState.selectedReviewLogId = null;
        }
        await refreshState();
        appState.selectedReviewCandidateId = candidateId;
      } catch (error) {
        alert(error.message);
      }
      return;
    }

    const saveButton = event.target.closest("#saveReviewLogBtn");
    if (!saveButton) return;

    const candidateId = Number(document.getElementById("reviewCandidateSelect")?.value || 0);
    const reviewId = Number(saveButton.dataset.reviewId || 0);
    if (!candidateId) return;

    const payload = {
      review_date: document.getElementById("reviewDateInput")?.value || "",
      cycle_label: document.getElementById("reviewCycleLabelInput")?.value || "",
      sales_units: Number(document.getElementById("reviewSalesUnitsInput")?.value || 0),
      sales_amount: Number(document.getElementById("reviewSalesAmountInput")?.value || 0),
      gross_margin_rate: Number(document.getElementById("reviewMarginRateInput")?.value || 0),
      decision: document.getElementById("reviewDecisionInput")?.value || "observe",
      notes: document.getElementById("reviewNotesInput")?.value || "",
    };

    try {
      saveButton.disabled = true;
      await fetchJson(
        reviewId
          ? `/api/candidates/${candidateId}/review-logs/${reviewId}`
          : `/api/candidates/${candidateId}/review-logs`,
        {
          method: reviewId ? "PUT" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        },
      );
      appState.selectedReviewCandidateId = candidateId;
      appState.selectedReviewLogId = null;
      await refreshState();
    } catch (error) {
      alert(error.message);
    } finally {
      saveButton.disabled = false;
    }
  });
}

async function refreshState() {
  appState.pricingSimulation = null;
  appState.data = await fetchJson("/api/state");
  renderSummary();
  renderOverviewModule();
  renderSkuFilters();
  renderSkuTable();
  renderCandidateTable();
  renderCrawlPreview();
  renderComparisonSelector();
  await renderComparisonPanel();
  renderDashboard();
  renderRecommendations();
  renderReviewModule();
}

async function init() {
  try {
    appState.meta = await fetchJson("/api/meta");
    renderMeta();
    renderNav();
    bindEvents();
    bindBrandDashboardEnhancements();
    bindWorkflowEnhancements();
    fillCandidateForm(null);
    await refreshState();
  } catch (error) {
    document.body.innerHTML = `<div class="app-shell"><div class="card"><h1>启动失败</h1><p>${escapeHtml(error.message)}</p></div></div>`;
  }
}

init();

function launchStatusLabel(status) {
  const options = appState.meta?.launch_status_options || [];
  return options.find((item) => item.key === status)?.label || "计划中";
}

function reviewDecisionLabel(decision) {
  const options = appState.meta?.review_decision_options || [];
  return options.find((item) => item.key === decision)?.label || "";
}

function procurementQueue() {
  return appState.data?.procurement?.launch_queue || [];
}

function procurementSummary() {
  return appState.data?.procurement?.summary || { planned_count: 0, launched_count: 0, review_due_count: 0, tracked_count: 0 };
}

function existingActionRows() {
  return (appState.data?.recommendations?.existing || []).filter((item) => item.action && item.action !== "建议维持常规价");
}

function buildProcurementModel() {
  const queue = procurementQueue();
  const existingRows = existingActionRows();
  const budget = queue.reduce((sum, item) => sum + Number(item.planned_budget || 0), 0);
  const expectedProfit = queue.reduce(
    (sum, item) =>
      sum + Math.max(0, (Number(item.suggested_price || 0) - Number(item.expected_purchase_price || 0)) * Number(item.first_order_qty || 0)),
    0,
  );
  return {
    queue,
    existingRows,
    overviewCards: [
      ["现有 SKU 数", appState.data?.dashboard?.summary?.sku_count || 0, "当前门店在售牙膏数量"],
      ["建议上新数", queue.filter((item) => item.suggested_action === "建议上新").length, "本期建议新增款"],
      ["建议替换数", queue.filter((item) => item.suggested_action === "建议替换上新").length, "本期建议以新换旧"],
      ["建议下架数", existingRows.filter((item) => item.action === "建议下架").length, "建议清理的低效 SKU"],
      ["建议采购预算", formatCurrency(budget), "按已填写首单量估算"],
      ["首单预计毛利", formatCurrency(expectedProfit), "只按首单试单估算"],
      ["待补结构缺口", appState.data?.dashboard?.structure_gaps?.length || 0, "价格带 / 功效 / 品牌缺口"],
      ["待复盘项目", procurementSummary().tracked_count, "已进入执行与复盘追踪"],
    ],
    brandOpportunities: deriveBrandOpportunities(),
    riskAlerts: deriveRiskAlerts(existingRows.map((item) => ({ action: item.action }))),
    reviewItems: queue,
  };
}

function renderSummary() {
  const procurement = buildProcurementModel();
  document.getElementById("summaryGrid").innerHTML = procurement.overviewCards
    .slice(0, 6)
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

function renderOverviewModule() {
  const model = buildProcurementModel();
  const kpiContainer = document.getElementById("overviewKpiGrid");
  const actionTable = document.getElementById("procurementActionTable");
  const brandPanel = document.getElementById("overviewBrandOpportunityPanel");
  const riskPanel = document.getElementById("overviewRiskPanel");
  const statusOptions = (appState.meta?.launch_status_options || [])
    .map((item) => `<option value="${escapeHtml(item.key)}">${escapeHtml(item.label)}</option>`)
    .join("");
  const cycleOptions = (appState.meta?.review_cycle_options || [])
    .map((item) => `<option value="${item}">${item}天</option>`)
    .join("");

  kpiContainer.innerHTML = model.overviewCards
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

  actionTable.querySelector("thead").innerHTML = `
    <tr>
      <th>动作</th>
      <th>商品</th>
      <th>首单数量</th>
      <th>实际上新</th>
      <th>上新日期</th>
      <th>复盘周期</th>
      <th>预算</th>
      <th>保存</th>
      <th>原因</th>
    </tr>
  `;
  const candidateRows = model.queue
    .map(
      (item) => `
        <tr data-candidate-id="${item.candidate_id}">
          <td><span class="badge ${actionBadgeClass(item.suggested_action)}">${escapeHtml(item.suggested_action)}</span></td>
          <td><strong>${escapeHtml(item.brand)}</strong><br />${escapeHtml(item.product_name)}<br /><span class="muted">${escapeHtml(item.spec_text || "-")}</span></td>
          <td>
            <input class="table-input" data-field="first_order_qty" type="number" min="0" step="1" value="${escapeHtml(item.first_order_qty || item.suggested_first_order_qty || 0)}" />
            <div class="muted">建议 ${escapeHtml(item.suggested_first_order_qty || 0)} 支</div>
          </td>
          <td>
            <select class="table-input" data-field="launch_status">${statusOptions}</select>
            <input class="table-input top-gap" data-field="actual_launch_qty" type="number" min="0" step="1" value="${escapeHtml(item.actual_launch_qty || 0)}" placeholder="实际上新数" />
          </td>
          <td>
            <input class="table-input" data-field="actual_launch_date" type="date" value="${escapeHtml(item.actual_launch_date || "")}" />
            <div class="muted">${escapeHtml(item.launch_status_label || launchStatusLabel(item.launch_status))}</div>
          </td>
          <td>
            <select class="table-input" data-field="review_cycle_days">${cycleOptions}</select>
            <div class="muted">下次复盘 ${escapeHtml(item.next_review_date || "-")}</div>
          </td>
          <td>${formatCurrency(item.planned_budget || 0)}<br /><span class="muted">建议价 ${formatCurrency(item.suggested_price || 0)}</span></td>
          <td><button type="button" class="primary-btn small-btn" data-action="save-launch-plan" data-candidate-id="${item.candidate_id}">保存</button></td>
          <td class="muted">${escapeHtml((item.reason || []).join("；") || "-")}</td>
        </tr>
      `,
    )
    .join("");
  const existingRows = model.existingRows
    .map(
      (item) => `
        <tr>
          <td><span class="badge ${actionBadgeClass(item.action)}">${escapeHtml(item.action)}</span></td>
          <td><strong>${escapeHtml(item.brand)}</strong><br />${escapeHtml(item.product_name)}<br /><span class="muted">${escapeHtml(item.spec_text || "-")}</span></td>
          <td colspan="4"><span class="muted">现有 SKU 处置动作不需要首单记录，请在采购决策单里看详细依据。</span></td>
          <td>${formatCurrency(item.half_year_gross_profit || 0)}</td>
          <td>-</td>
          <td class="muted">${escapeHtml(item.reason || "-")}</td>
        </tr>
      `,
    )
    .join("");
  actionTable.querySelector("tbody").innerHTML = candidateRows || existingRows
    ? `${candidateRows}${existingRows}`
    : `<tr><td colspan="9"><div class="empty-state">当前还没有可执行采购动作。</div></td></tr>`;

  model.queue.forEach((item) => {
    const row = actionTable.querySelector(`tr[data-candidate-id="${item.candidate_id}"]`);
    row?.querySelector('[data-field="launch_status"]')?.setAttribute("value", item.launch_status || "planned");
    if (row?.querySelector('[data-field="launch_status"]')) {
      row.querySelector('[data-field="launch_status"]').value = item.launch_status || "planned";
    }
    if (row?.querySelector('[data-field="review_cycle_days"]')) {
      row.querySelector('[data-field="review_cycle_days"]').value = String(item.review_cycle_days || item.suggested_review_cycle_days || 14);
    }
  });

  brandPanel.innerHTML = `
    <div class="inline-actions">
      <div>
        <h3>重点品牌机会</h3>
        <p class="muted">优先告诉你哪些品牌值得继续补款，哪些品牌值得从集中度里点开深挖。</p>
      </div>
    </div>
    ${
      model.brandOpportunities.length
        ? `<div class="stack compact-stack">${model.brandOpportunities
            .map(
              (item) => `
                <article class="detail-card">
                  <div class="inline-actions">
                    <strong>${escapeHtml(item.brand)}</strong>
                    <span class="badge gray">${escapeHtml(item.label)}</span>
                  </div>
                  <p class="muted">${escapeHtml(item.reason)}</p>
                  <p class="muted">关注点：${escapeHtml(item.focus)}</p>
                </article>
              `,
            )
            .join("")}</div>`
        : `<div class="empty-state">当前还没有足够突出的品牌机会，建议继续补候选池。</div>`
    }
  `;

  riskPanel.innerHTML = `
    <div class="inline-actions">
      <div>
        <h3>高风险提醒</h3>
        <p class="muted">优先处理结构缺口、市场样本不足和低效 SKU。</p>
      </div>
    </div>
    ${
      model.riskAlerts.length
        ? `<ul class="risk-list">${model.riskAlerts
            .map((item) => `<li><strong>${escapeHtml(item.label)}：</strong>${escapeHtml(item.text)}</li>`)
            .join("")}</ul>`
        : `<div class="empty-state">当前没有特别突出的高风险提醒，可以继续看采购决策单。</div>`
    }
  `;
}

function currentReviewCandidate() {
  const queue = procurementQueue();
  if (!queue.length) return null;
  if (!appState.selectedReviewCandidateId || !queue.some((item) => Number(item.candidate_id) === Number(appState.selectedReviewCandidateId))) {
    appState.selectedReviewCandidateId = Number(queue[0].candidate_id);
  }
  return queue.find((item) => Number(item.candidate_id) === Number(appState.selectedReviewCandidateId)) || queue[0];
}

function renderReviewModule() {
  const queue = procurementQueue();
  const summary = procurementSummary();
  const selected = currentReviewCandidate();
  const summaryPanel = document.getElementById("reviewSummaryPanel");
  const reviewTable = document.getElementById("reviewTable");
  const decisionOptions = (appState.meta?.review_decision_options || [])
    .map((item) => `<option value="${escapeHtml(item.key)}">${escapeHtml(item.label)}</option>`)
    .join("");

  summaryPanel.innerHTML = `
    <div class="stack">
      <div class="card stack">
        <div class="inline-actions">
          <div>
            <h3>复盘重点</h3>
            <p class="muted">这里记录真实上新和周期复盘，不再只是系统推演。</p>
          </div>
        </div>
        <div class="selection-summary">
          <div class="score-chip"><div class="label">计划中</div><div class="value">${summary.planned_count || 0}</div></div>
          <div class="score-chip"><div class="label">已上新</div><div class="value">${summary.launched_count || 0}</div></div>
          <div class="score-chip"><div class="label">待复盘</div><div class="value">${summary.review_due_count || 0}</div></div>
          <div class="score-chip"><div class="label">已追踪</div><div class="value">${summary.tracked_count || 0}</div></div>
        </div>
      </div>
      <div class="overview-layout">
        <article class="card stack">
          <div class="inline-actions">
            <div>
              <h3>新增复盘记录</h3>
              <p class="muted">选中一个已追踪候选，记录销量、销售额和复盘结论。</p>
            </div>
          </div>
          <label>
            <span>复盘商品</span>
            <select id="reviewCandidateSelect">
              ${queue
                .map(
                  (item) =>
                    `<option value="${item.candidate_id}" ${Number(item.candidate_id) === Number(selected?.candidate_id) ? "selected" : ""}>${escapeHtml(item.brand)} - ${escapeHtml(item.product_name)}</option>`,
                )
                .join("")}
            </select>
          </label>
          <div class="filter-grid compact">
            <label>
              <span>复盘日期</span>
              <input type="date" id="reviewDateInput" value="${new Date().toISOString().slice(0, 10)}" />
            </label>
            <label>
              <span>周期标签</span>
              <input type="text" id="reviewCycleLabelInput" value="${escapeHtml(selected ? `${selected.review_cycle_days}天复盘` : "周期复盘")}" />
            </label>
            <label>
              <span>销量数量</span>
              <input type="number" id="reviewSalesUnitsInput" min="0" step="1" value="0" />
            </label>
            <label>
              <span>销售额</span>
              <input type="number" id="reviewSalesAmountInput" min="0" step="0.01" value="0" />
            </label>
            <label>
              <span>毛利率(%)</span>
              <input type="number" id="reviewMarginRateInput" min="0" step="0.1" value="${selected ? ((Number(selected.expected_margin || 0) * 100).toFixed(1)) : "0"}" />
            </label>
            <label>
              <span>复盘结论</span>
              <select id="reviewDecisionInput">${decisionOptions}</select>
            </label>
          </div>
          <label>
            <span>备注</span>
            <textarea id="reviewNotesInput" rows="3" placeholder="例如：首周动销符合预期，可继续补货。"></textarea>
          </label>
          <div class="inline-actions">
            <button type="button" class="primary-btn" id="saveReviewLogBtn">保存复盘记录</button>
          </div>
        </article>
        <article class="card stack">
          <div class="inline-actions">
            <div>
              <h3>当前商品复盘历史</h3>
              <p class="muted">这里看实际上新、最近复盘和下一次该回看的时间。</p>
            </div>
          </div>
          ${
            selected
              ? `
                <div class="detail-card">
                  <strong>${escapeHtml(selected.brand)} - ${escapeHtml(selected.product_name)}</strong>
                  <p class="muted">状态：${escapeHtml(selected.launch_status_label || launchStatusLabel(selected.launch_status))} / 实际上新 ${escapeHtml(selected.actual_launch_qty || 0)} 支 / 日期 ${escapeHtml(selected.actual_launch_date || "-")}</p>
                  <p class="muted">首单 ${escapeHtml(selected.first_order_qty || 0)} 支 / 下次复盘 ${escapeHtml(selected.next_review_date || "-")}</p>
                  <p class="muted">备注：${escapeHtml(selected.launch_notes || "暂无")}</p>
                </div>
                <div class="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>复盘日期</th>
                        <th>周期</th>
                        <th>销量</th>
                        <th>销售额</th>
                        <th>毛利率</th>
                        <th>结论</th>
                        <th>备注</th>
                      </tr>
                    </thead>
                    <tbody>
                      ${
                        (selected.review_logs || []).length
                          ? selected.review_logs
                              .map(
                                (log) => `
                                  <tr>
                                    <td>${escapeHtml(log.review_date)}</td>
                                    <td>${escapeHtml(log.cycle_label || "-")}</td>
                                    <td>${escapeHtml(log.sales_units || 0)}</td>
                                    <td>${formatCurrency(log.sales_amount || 0)}</td>
                                    <td>${log.gross_margin_rate ? formatPercent(log.gross_margin_rate) : "-"}</td>
                                    <td>${escapeHtml(log.decision_label || reviewDecisionLabel(log.decision))}</td>
                                    <td class="muted">${escapeHtml(log.notes || "-")}</td>
                                  </tr>
                                `,
                              )
                              .join("")
                          : `<tr><td colspan="7"><div class="empty-state">这款商品还没有复盘记录。</div></td></tr>`
                      }
                    </tbody>
                  </table>
                </div>
              `
              : `<div class="empty-state">当前还没有进入执行追踪的候选商品。</div>`
          }
        </article>
      </div>
    </div>
  `;

  reviewTable.querySelector("thead").innerHTML = `
    <tr>
      <th>商品</th>
      <th>状态</th>
      <th>首单数量</th>
      <th>实际上新</th>
      <th>最近复盘</th>
      <th>下次复盘</th>
      <th>操作</th>
    </tr>
  `;
  reviewTable.querySelector("tbody").innerHTML = queue.length
    ? queue
        .map(
          (item) => `
            <tr>
              <td><strong>${escapeHtml(item.brand)}</strong><br />${escapeHtml(item.product_name)}</td>
              <td><span class="badge ${item.review_due ? "orange" : "gray"}">${escapeHtml(item.launch_status_label || launchStatusLabel(item.launch_status))}</span></td>
              <td>${escapeHtml(item.first_order_qty || 0)}</td>
              <td>${escapeHtml(item.actual_launch_qty || 0)} 支<br /><span class="muted">${escapeHtml(item.actual_launch_date || "-")}</span></td>
              <td>${item.latest_review ? `${escapeHtml(item.latest_review.review_date)} / ${escapeHtml(item.latest_review.decision_label || reviewDecisionLabel(item.latest_review.decision))}` : "-"}</td>
              <td>${escapeHtml(item.next_review_date || "-")}</td>
              <td><button type="button" class="ghost-btn small-btn" data-action="select-review-candidate" data-candidate-id="${item.candidate_id}">查看并记录</button></td>
            </tr>
          `,
        )
        .join("")
    : `<tr><td colspan="7"><div class="empty-state">当前还没有进入执行追踪的候选商品。</div></td></tr>`;
}

function bindWorkflowEnhancements() {
  const candidateTable = document.getElementById("candidateTable");
  candidateTable.addEventListener("click", async (event) => {
    const compareTarget = event.target.closest('[data-action="compare"]');
    if (compareTarget) {
      const select = document.getElementById("comparisonSelector");
      if (!select) return;
      select.value = compareTarget.dataset.id;
      await renderComparisonPanel();
      document.getElementById("comparisonPanel")?.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
  });

  document.getElementById("procurementActionTable")?.addEventListener("click", async (event) => {
    const button = event.target.closest('[data-action="save-launch-plan"]');
    if (!button) return;
    const row = button.closest("tr");
    const candidateId = Number(button.dataset.candidateId || 0);
    if (!row || !candidateId) return;
    const payload = {
      first_order_qty: Number(row.querySelector('[data-field="first_order_qty"]')?.value || 0),
      actual_launch_qty: Number(row.querySelector('[data-field="actual_launch_qty"]')?.value || 0),
      actual_launch_date: row.querySelector('[data-field="actual_launch_date"]')?.value || "",
      review_cycle_days: Number(row.querySelector('[data-field="review_cycle_days"]')?.value || 14),
      launch_status: row.querySelector('[data-field="launch_status"]')?.value || "planned",
    };
    try {
      button.disabled = true;
      await fetchJson(`/api/candidates/${candidateId}/launch-plan`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      await refreshState();
    } catch (error) {
      alert(error.message);
    } finally {
      button.disabled = false;
    }
  });

  document.getElementById("reviewModule")?.addEventListener("change", (event) => {
    const select = event.target.closest("#reviewCandidateSelect");
    if (!select) return;
    appState.selectedReviewCandidateId = Number(select.value || 0);
    renderReviewModule();
  });

  document.getElementById("reviewModule")?.addEventListener("click", async (event) => {
    const selectButton = event.target.closest('[data-action="select-review-candidate"]');
    if (selectButton) {
      appState.selectedReviewCandidateId = Number(selectButton.dataset.candidateId || 0);
      renderReviewModule();
      return;
    }

    const saveButton = event.target.closest("#saveReviewLogBtn");
    if (!saveButton) return;
    const candidateId = Number(document.getElementById("reviewCandidateSelect")?.value || 0);
    if (!candidateId) return;
    const payload = {
      review_date: document.getElementById("reviewDateInput")?.value || "",
      cycle_label: document.getElementById("reviewCycleLabelInput")?.value || "",
      sales_units: Number(document.getElementById("reviewSalesUnitsInput")?.value || 0),
      sales_amount: Number(document.getElementById("reviewSalesAmountInput")?.value || 0),
      gross_margin_rate: Number(document.getElementById("reviewMarginRateInput")?.value || 0),
      decision: document.getElementById("reviewDecisionInput")?.value || "observe",
      notes: document.getElementById("reviewNotesInput")?.value || "",
    };
    try {
      saveButton.disabled = true;
      await fetchJson(`/api/candidates/${candidateId}/review-logs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      await refreshState();
      appState.selectedReviewCandidateId = candidateId;
    } catch (error) {
      alert(error.message);
    } finally {
      saveButton.disabled = false;
    }
  });
}
