const money = value => "$" + Number(value).toFixed(2);
const escapeText = value => String(value).replace(/[&<>"']/g, character => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[character]));
let currentProducts = [];

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) }
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "The request did not work.");
  return result;
}

async function refreshBoard() {
  try {
    const category = document.querySelector("#category-filter").value;
    const [products, suggestions] = await Promise.all([
      api(`/products?category=${category}`), api("/suggestions")
    ]);
    currentProducts = products;
    showProducts(products);
    showSuggestions(suggestions);
    const allProducts = category === "ALL" ? products : await api("/products");
    document.querySelector("#product-count").textContent = allProducts.length;
    document.querySelector("#low-count").textContent = allProducts.filter(item => item.stock_level < item.reorder_threshold).length;
    document.querySelector("#demand-count").textContent = allProducts.filter(item => item.demand_velocity > 8).length;
    document.querySelector("#message").hidden = true;
  } catch (error) {
    showMessage(error.message);
  }
}

function showProducts(products) {
  const area = document.querySelector("#products");
  if (!products.length) { area.innerHTML = '<div class="empty">No products in this category.</div>'; return; }
  area.innerHTML = products.map(item => {
    const low = item.stock_level < item.reorder_threshold;
    const status = item.stock_level === 0 ? "Out of stock" : low ? "Low stock" : "Healthy";
    const stockBar = Math.min(100, item.stock_level / Math.max(1, item.reorder_threshold * 2) * 100);
    return `<div class="product-row">
      <div class="product-name"><span class="product-icon ${item.category.toLowerCase()}">${item.category === "APPAREL" ? "◈" : item.category === "HOME" ? "⌂" : "▣"}</span><div><b>${escapeText(item.name)}</b><small>${escapeText(item.sku)} · ${escapeText(item.category)}</small></div></div>
      <strong>${money(item.current_price)}</strong>
      <div class="stock"><b class="${low ? "low" : ""}">${item.stock_level}</b><small> / ${item.reorder_threshold} min</small><div class="bar"><i class="${low ? "low" : ""}" style="width:${stockBar}%"></i></div></div>
      <span class="velocity">↗ &nbsp;${item.demand_velocity} <small>orders</small></span>
      <span class="status ${low ? "attention" : "healthy"}"><i></i>${status}</span>
      <button class="sale-button" data-sale="${escapeText(item.id)}" ${item.stock_level === 0 ? "disabled" : ""}>＋ Sale</button>
    </div>`;
  }).join("");
  area.querySelectorAll("[data-sale]").forEach(button => button.addEventListener("click", () => simulateSale(button.dataset.sale)));
}

function showSuggestions(suggestions) {
  document.querySelector("#suggestion-count").textContent = suggestions.length;
  document.querySelector("#pending-pill").textContent = suggestions.length;
  const area = document.querySelector("#suggestions");
  if (!suggestions.length) {
    area.innerHTML = '<div class="empty">✓ &nbsp; All caught up. New suggestions appear when stock or demand changes.</div>';
    return;
  }
  area.innerHTML = suggestions.map(item => {
    const isPrice = item.kind === "PRICE";
    const headline = isPrice ? "PRICE RECOMMENDATION" : "REPLENISHMENT";
    const change = isPrice
      ? `<span>${money(item.current_price)}</span><b>→</b><strong>${money(item.recommended_price)}</strong>`
      : `<span>${item.current_stock} units</span><b>→</b><strong>+${item.recommended_quantity} units</strong><small>${item.lead_time_days} day lead</small>`;
    return `<article class="suggestion-card">
      <div class="suggest-symbol">${isPrice ? "↗" : "▤"}</div>
      <div class="suggestion-copy"><div class="suggestion-meta">${headline}<span class="trigger">● ${escapeText(item.trigger_reason.replaceAll("_", " "))}</span></div>
      <h3>${escapeText(item.product_name)}</h3><p>${escapeText(item.reasoning)}</p>
      <div class="recommendation">${change}<span class="confidence">Rule suggestion · ${Math.round(item.confidence * 100)}% confidence</span></div></div>
      <div class="actions"><button class="accept" data-action="accept" data-id="${item.id}">✓ Accept</button><button class="reject" data-action="reject" data-id="${item.id}">× Reject</button></div>
    </article>`;
  }).join("");
  area.querySelectorAll("[data-action]").forEach(button => button.addEventListener("click", () => review(button.dataset.id, button.dataset.action)));
}

async function review(id, action) {
  try { await api(`/suggestions/${id}`, { method: "PATCH", body: JSON.stringify({ action }) }); await refreshBoard(); }
  catch (error) { showMessage(error.message); }
}

async function simulateSale(id) {
  try {
    const result = await api(`/products/${id}/orders`, { method: "POST", body: JSON.stringify({ quantity: 1 }) });
    showMessage(result.triggerReason ? `${result.triggerReason.replaceAll("_", " ")} signal found. Suggestions will appear shortly.` : "Sale recorded.");
    await refreshBoard();
  } catch (error) { showMessage(error.message); }
}

function showMessage(text) {
  const box = document.querySelector("#message");
  box.textContent = text; box.hidden = false;
}

document.querySelector("#refresh").addEventListener("click", refreshBoard);
document.querySelector("#category-filter").addEventListener("change", refreshBoard);
refreshBoard();
setInterval(refreshBoard, 3000);
