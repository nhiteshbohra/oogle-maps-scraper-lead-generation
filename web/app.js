function remember(name, value) { try { localStorage.setItem(name, value); } catch (err) {} }
function recall(name) { try { return localStorage.getItem(name); } catch (err) { return null; } }

let DATA = [], epoch = 0, lastAdded = '', LEADS = {}, phoneCount = new Map();
let PAGE_SIZE = 50;
const savedPs = recall('lf-pagesize');
if (savedPs !== null) { const parsed = parseInt(savedPs, 10); if (!isNaN(parsed)) PAGE_SIZE = parsed; }
const MAP_LIMIT = 4000, TOP_N = 15, REFRESH_MS = 10000, HOT_SCORE = 70, DAY_MINUTES = 1440;
const MIN_RATINGS_FOR_SHARE = 10, UNHAPPY_PERCENT = 20, FIRST_BUSY_HOUR = 6;
const MIN_LOCALITY_PLACES = 5, MAP_DELAY_MS = 150, SEARCH_DELAY_MS = 250, LIVE_MAP_INTERVAL_MS = 60000;
const BASIC_TILES = 4, SEARCHES_PER_MINUTE = 25, KM_PER_DEGREE = 111, SCRAPE_POLL_MS = 5000;
const ADVANCED_FILTERS = ['f-pin', 'f-web', 'f-rating', 'f-rev', 'f-hours', 'f-active', 'f-neg', 'f-claim', 'f-closed', 'f-fit'];
const optionCache = {};
let dirty = false;
const unhappy = r => r[26] >= UNHAPPY_PERCENT && r[11] >= MIN_RATINGS_FOR_SHARE;
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = n => n.toLocaleString('en-IN');
const pct = (a, b) => b ? Math.round(100 * a / b) + '%' : '-';
const WEB_NAMES = {o: 'Own website', s: 'Social / listing only', n: 'No website'};
const LEAD_NAMES = {new: 'Not contacted', called: 'Called', interested: 'Interested', rejected: 'Not interested', customer: 'Customer'};
const DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const NUMERIC_COLS = [10, 11, 20];

function showToast(msg) {
  let t = $('toast');
  if (!t) {
    t = document.createElement('div');
    t.id = 'toast';
    t.className = 'toast';
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove('show'), 2200);
}

function copyText(text, label) {
  if (!text) return;
  const finish = () => showToast(label ? `${label} copied!` : 'Copied to clipboard!');
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(finish).catch(() => {
      fallbackCopy(text);
      finish();
    });
  } else {
    fallbackCopy(text);
    finish();
  }
}

function fallbackCopy(text) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand('copy'); } catch (_) {}
  ta.remove();
}

function copyLeadSummary(key) {
  const r = DATA.find(x => x[23] === key);
  if (!r) return;
  const status = leadStatus(r);
  const text = `${r[0]}\nCategory: ${r[1]}\nPhone: ${r[5] || 'None'}\nAddress: ${r[4] || 'None'}\nWebsite: ${r[7] || 'None'}\nRating: ${r[10] > 0 ? r[10] + ' (' + r[11] + ' reviews)' : 'None'}\nLead score: ${r[20]}/100\nStatus: ${LEAD_NAMES[status] || status}`;
  copyText(text, 'Lead details');
}

async function updateLeadStatus(key, status) {
  const r = DATA.find(x => x[23] === key);
  const title = r ? r[0] : 'Lead';
  const existing = LEADS[key] || {};
  const note = existing.note || '';
  try {
    const res = await fetch('/lead', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({key, status, note})
    });
    if (!res.ok) throw new Error(res.status);
    if (status === 'new' && !note) {
      delete LEADS[key];
    } else {
      LEADS[key] = {status, note, updated: new Date().toISOString().slice(0, 19)};
    }
    showToast(`${title}: ${LEAD_NAMES[status]}`);
    filterRows();
    renderTiles();
    renderCharts();
    renderPlaces();
  } catch (err) {
    showToast('Failed to save status - is analyze.py running?');
  }
}

function getPagePills(currPage, totalPages) {
  if (totalPages <= 7) {
    return Array.from({length: totalPages}, (_, i) => i);
  }
  const pills = [];
  pills.push(0);
  if (currPage > 2) pills.push('...');
  const start = Math.max(1, currPage - 1);
  const end = Math.min(totalPages - 2, currPage + 1);
  for (let i = start; i <= end; i++) {
    pills.push(i);
  }
  if (currPage < totalPages - 3) pills.push('...');
  pills.push(totalPages - 1);
  return pills;
}

function renderPager(id, currPage, totalPages) {
  const container = $(id);
  if (!container) return;

  const pills = getPagePills(currPage, totalPages);
  const pillHtml = pills.map(p => {
    if (p === '...') return `<span class="pager-ellipsis">&hellip;</span>`;
    const num = p + 1;
    const isAct = p === currPage;
    return `<button type="button" class="pager-btn${isAct ? ' active' : ''}" data-page="${p}" title="Page ${num}">${num}</button>`;
  }).join('');

  container.innerHTML = `
    <div class="pager-size">
      <span class="dim">Rows:</span>
      <select class="p-size-select" title="Rows per page">
        <option value="25"${PAGE_SIZE === 25 ? ' selected' : ''}>25</option>
        <option value="50"${PAGE_SIZE === 50 ? ' selected' : ''}>50</option>
        <option value="100"${PAGE_SIZE === 100 ? ' selected' : ''}>100</option>
        <option value="250"${PAGE_SIZE === 250 ? ' selected' : ''}>250</option>
        <option value="-1"${PAGE_SIZE === -1 ? ' selected' : ''}>All</option>
      </select>
    </div>
    <div class="pager-nav">
      <button type="button" class="pager-btn p-first" title="First page (Home)" ${currPage === 0 ? 'disabled' : ''}>&laquo;</button>
      <button type="button" class="pager-btn p-prev" title="Previous page (Left arrow)" ${currPage === 0 ? 'disabled' : ''}>&lsaquo;</button>
      <div class="pager-pills" style="display:inline-flex;gap:3px;align-items:center">${pillHtml}</div>
      <button type="button" class="pager-btn p-next" title="Next page (Right arrow)" ${currPage >= totalPages - 1 ? 'disabled' : ''}>&rsaquo;</button>
      <button type="button" class="pager-btn p-last" title="Last page (End)" ${currPage >= totalPages - 1 ? 'disabled' : ''}>&raquo;</button>
    </div>
    <div class="pager-jump">
      <span class="dim">Page:</span>
      <input type="number" class="p-jump-input" min="1" max="${totalPages}" value="${currPage + 1}" title="Type page number and press Enter">
    </div>
    <button type="button" class="p-export-btn" title="Export matching places as CSV">Export CSV</button>
  `;

  const sizeSelect = container.querySelector('.p-size-select');
  if (sizeSelect) {
    sizeSelect.onchange = e => {
      PAGE_SIZE = +e.target.value;
      remember('lf-pagesize', PAGE_SIZE);
      page = 0;
      renderPlaces();
    };
  }
  const btnFirst = container.querySelector('.p-first');
  if (btnFirst) btnFirst.onclick = () => { if (page > 0) { page = 0; renderPlaces(); } };
  const btnPrev = container.querySelector('.p-prev');
  if (btnPrev) btnPrev.onclick = () => { if (page > 0) { page--; renderPlaces(); } };
  const btnNext = container.querySelector('.p-next');
  if (btnNext) btnNext.onclick = () => { if (page < totalPages - 1) { page++; renderPlaces(); } };
  const btnLast = container.querySelector('.p-last');
  if (btnLast) btnLast.onclick = () => { if (page < totalPages - 1) { page = totalPages - 1; renderPlaces(); } };

  container.querySelectorAll('.pager-pills button').forEach(btn => {
    btn.onclick = () => {
      page = +btn.dataset.page;
      renderPlaces();
    };
  });

  const jumpInput = container.querySelector('.p-jump-input');
  if (jumpInput) {
    jumpInput.onkeydown = e => {
      if (e.key === 'Enter') {
        const val = parseInt(jumpInput.value, 10);
        if (val >= 1 && val <= totalPages) {
          page = val - 1;
          renderPlaces();
        } else {
          jumpInput.value = currPage + 1;
        }
      }
    };
  }

  const exportBtn = container.querySelector('.p-export-btn');
  if (exportBtn) exportBtn.onclick = exportCsv;
}

let sortCol = 20, sortDir = -1, page = 0, rows = [], openKey = '';

const isClosed = r => /closed/i.test(r[15]);
const leadStatus = r => (LEADS[r[23]] || {}).status || 'new';
const today = () => (new Date().getDay() + 6) % 7;  // Monday = 0, like the data

// true / false when hours are known, null when Google lists none.
function openNow(r) {
  const week = r[21];
  if (!week) return null;
  const now = new Date(), day = today(), minute = now.getHours() * 60 + now.getMinutes();
  if (week[day].some(([start, end]) => start <= minute && minute < end)) return true;
  // Still inside yesterday's late shift (a range that runs past midnight).
  return week[(day + 6) % 7].some(([, end]) => end > DAY_MINUTES && minute < end - DAY_MINUTES);
}
const alwaysOpen = r => !!r[21] && r[21].every(d => d.length === 1 && d[0][0] === 0 && d[0][1] === DAY_MINUTES);

function countBy(list, idx) {
  const m = new Map();
  for (const r of list) m.set(r[idx], (m.get(r[idx]) || 0) + 1);
  return [...m.entries()].sort((a, b) => b[1] - a[1]);
}

// minCount keeps one-off values out of the list: a dropdown with thousands of options opens slowly.
function fillSelect(id, idx, label, minCount = 1) {
  const el = $(id), current = el.value;
  const html = `<option value="">${label}: all</option>` + countBy(DATA, idx)
    .filter(([k, n]) => k !== '' && (n >= minCount || k === current))
    .map(([k, n]) => `<option value="${esc(k)}">${esc(k)} (${n})</option>`).join('');
  if (html === optionCache[id]) return;
  optionCache[id] = html;
  el.innerHTML = html;
  el.value = current;
}

function fillSelects() {
  fillSelect('f-cat', 1, 'Category');
  fillSelect('f-loc', 2, 'Locality', MIN_LOCALITY_PLACES);
  fillSelect('f-pin', 3, 'Pincode');
  // Suggestions for the edit form, so corrected values match existing spellings.
  for (const [id, idx] of [['dl-category', 1], ['dl-locality', 2]]) {
    $(id).innerHTML = countBy(DATA, idx).slice(0, 400).map(([k]) => `<option value="${esc(k)}">`).join('');
  }
}

function countPhones() {
  phoneCount = new Map();
  for (const r of DATA) if (r[5]) phoneCount.set(r[5], (phoneCount.get(r[5]) || 0) + 1);
}

function applyFilters() { page = 0; filterRows(); render(); }

// Typing or an open dropdown must not be disturbed by a background refresh.
const userBusy = () => ['SELECT', 'INPUT', 'TEXTAREA'].includes((document.activeElement || {}).tagName);

// Redraw after new places arrived from the scraper.
function showNewData() {
  if (userBusy()) { dirty = true; return; }
  dirty = false;
  fillSelects();
  filterRows();
  render(Date.now() - lastMapDraw < LIVE_MAP_INTERVAL_MS);
}

function filterRows() {
  const v = id => $(id).value;
  const q = v('f-q').trim().toLowerCase(), cat = v('f-cat'), loc = v('f-loc'), pin = v('f-pin');
  const web = v('f-web'), phone = v('f-phone'), rating = v('f-rating'), hours = v('f-hours');
  const minRev = +v('f-rev'), minScore = +v('f-score'), claim = v('f-claim'), closed = v('f-closed'), lead = v('f-lead'), fit = v('f-fit');
  const active = v('f-active'), minNeg = +v('f-neg'), hiddenOpt = v('f-hidden');
  rows = DATA.filter(r => {
    const isHidden = r[28] === 1;
    if (hiddenOpt === 'only') { if (!isHidden) return false; }
    else if (hiddenOpt === 'all') { /* include both */ }
    else { if (isHidden) return false; }
    if (cat && r[1] !== cat) return false;
    if (loc && r[2] !== loc) return false;
    if (pin && r[3] !== pin) return false;
    if (web && !web.includes(r[8])) return false;
    if (phone === 'y' && !r[5]) return false;
    if (phone === 'x' && r[5]) return false;
    if ((phone === 'm' || phone === 'l') && r[6] !== phone) return false;
    if (rating === 'none' ? r[10] > 0 : rating === 'low' ? !(r[10] > 0 && r[10] < 3.5) : rating && r[10] < +rating) return false;
    if (r[11] < minRev || r[20] < minScore) return false;
    if (claim && r[14] !== +claim) return false;
    if (active === 'recent' && !r[25]) return false;
    if (active === 'year' && !(r[22] >= 0 && r[22] <= 12)) return false;
    if (active === 'stale' && !(r[22] > 12)) return false;
    // A low-rating share only means something with enough ratings behind it.
    if (minNeg && !(r[26] >= minNeg && r[11] >= MIN_RATINGS_FOR_SHARE)) return false;
    if (fit === '' && !r[24]) return false;
    if (fit === 'low' && r[24]) return false;
    if (closed === '' && isClosed(r)) return false;
    if (closed === 'only' && !isClosed(r)) return false;
    if (hours === 'now' && openNow(r) !== true) return false;
    if (hours === 'sun' && !(r[21] && r[21][6].length)) return false;
    if (hours === '24' && !alwaysOpen(r)) return false;
    if (hours === 'none' && r[21]) return false;
    if (lead && leadStatus(r) !== lead) return false;
    if (q && !(r[0] + ' ' + r[1] + ' ' + r[4] + ' ' + r[5] + ' ' + r[17] + ' ' + r[18]).toLowerCase().includes(q)) return false;
    return true;
  });
}

function renderTiles() {
  const hot = rows.filter(r => r[20] >= HOT_SCORE).length;
  const noWebMobile = rows.filter(r => r[8] === 'n' && r[6] === 'm').length;
  const withPhone = rows.filter(r => r[5]).length;
  const rated = rows.filter(r => r[10] > 0);
  const avgRating = rated.length ? (rated.reduce((s, r) => s + r[10], 0) / rated.length).toFixed(2) : '-';
  const openCount = rows.filter(r => openNow(r) === true).length;
  const recentCount = rows.filter(r => r[25]).length;
  const unhappyCount = rows.filter(unhappy).length;
  const myLeads = rows.filter(r => leadStatus(r) !== 'new').length;

  const tiles = [
    ['Best leads', hot, `${pct(hot, rows.length)} score ${HOT_SCORE}+`],
    ['No website + mobile', noWebMobile, `${pct(noWebMobile, rows.length)} easy to reach`],
    ['Have a phone', withPhone, `${pct(withPhone, rows.length)} can be called`],
    ['Average rating', avgRating, `${fmt(rated.length)} places rated`],
    ['Open now', openCount, 'based on hours'],
    ['Recent reviews', recentCount, 'last 3 months'],
    ['Unhappy customers', unhappyCount, '20%+ low ratings'],
    ['Worked on', myLeads, 'marked in dashboard'],
  ];
  $('tiles').innerHTML = tiles.map(([label, val, note], i) => `
    <div class="card tile${i >= BASIC_TILES ? ' adv' : ''}">
      <div class="v">${typeof val === 'number' ? fmt(val) : val}</div>
      <div class="l">${label}</div>
      <div class="n">${note}</div>
    </div>`).join('');
}

function bars(id, items, maxVal) {
  const m = maxVal || Math.max(1, ...items.map(x => x[1]));
  $(id).innerHTML = items.map(([k, v, note, extra]) => `
    <div class="row${extra ? ' ' + extra : ''}" data-v="${esc(k)}">
      <div class="lab" title="${esc(k)}">${esc(k)}</div>
      <div class="track"><div class="bar" style="width:${Math.round(100 * v / m)}%"></div></div>
      <div class="val">${fmt(v)}${note ? ' <small>' + note + '</small>' : ''}</div>
    </div>`).join('');
}

function renderCharts() {
  const top = (idx, n) => countBy(rows, idx).slice(0, n);
  bars('c-cat', top(1, TOP_N).map(([k, v]) => [k, v, pct(v, rows.length), 'click']));
  bars('c-loc', top(2, TOP_N).map(([k, v]) => [k, v, pct(v, rows.length), 'click']));

  const ratings = [
    ['4.5 - 5.0', rows.filter(r => r[10] >= 4.5).length],
    ['4.0 - 4.4', rows.filter(r => r[10] >= 4.0 && r[10] < 4.5).length],
    ['3.5 - 3.9', rows.filter(r => r[10] >= 3.5 && r[10] < 4.0).length],
    ['Under 3.5', rows.filter(r => r[10] > 0 && r[10] < 3.5).length],
    ['Unrated', rows.filter(r => !(r[10] > 0)).length],
  ];
  bars('c-rating', ratings.map(([k, v]) => [k, v, pct(v, rows.length)]));

  const webs = Object.entries(WEB_NAMES).map(([k, name]) => [name, rows.filter(r => r[8] === k).length]);
  bars('c-web', webs.map(([k, v]) => [k, v, pct(v, rows.length)]));

  const scores = [
    ['80 - 100 (top)', rows.filter(r => r[20] >= 80).length],
    ['70 - 79 (good)', rows.filter(r => r[20] >= 70 && r[20] < 80).length],
    ['50 - 69 (medium)', rows.filter(r => r[20] >= 50 && r[20] < 70).length],
    ['Below 50 (poor)', rows.filter(r => r[20] < 50).length],
  ];
  bars('c-score', scores.map(([k, v]) => [k, v, pct(v, rows.length)]));

  const leads = Object.entries(LEAD_NAMES).map(([k, name]) => [name, rows.filter(r => leadStatus(r) === k).length]);
  bars('c-lead', leads.map(([k, v]) => [k, v, pct(v, rows.length)]));

  $('c-cat').querySelectorAll('.row.click').forEach(el => el.onclick = () => { $('f-cat').value = el.dataset.v; applyFilters(); });
  $('c-loc').querySelectorAll('.row.click').forEach(el => el.onclick = () => { $('f-loc').value = el.dataset.v; applyFilters(); });
}

function median(nums) {
  const s = nums.filter(n => n > 0).sort((a, b) => a - b);
  if (!s.length) return 0;
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : Math.round((s[m - 1] + s[m]) / 2);
}

function renderCategoryTable() {
  const top = countBy(rows, 1).slice(0, 25);
  $('t-cat').innerHTML = `
    <tr><th>Category</th><th class="num">Places</th><th class="num">Avg rating</th><th class="num">Typical reviews</th>
      <th class="num">Has phone</th><th class="num">Website</th><th class="num">Unclaimed</th></tr>` +
    top.map(([cat, count]) => {
      const list = rows.filter(r => r[1] === cat);
      const rated = list.filter(r => r[10] > 0);
      return `<tr class="click" data-v="${esc(cat)}">
        <td><b>${esc(cat)}</b></td>
        <td class="num">${fmt(count)}</td>
        <td class="num">${rated.length ? (rated.reduce((s, r) => s + r[10], 0) / rated.length).toFixed(2) : '-'}</td>
        <td class="num">${fmt(median(list.map(r => r[11])))}</td>
        <td class="num">${pct(list.filter(r => r[5]).length, list.length)}</td>
        <td class="num">${pct(list.filter(r => r[8] !== 'o').length, list.length)}</td>
        <td class="num">${pct(list.filter(r => !r[14]).length, list.length)}</td></tr>`;
    }).join('');
  $('t-cat').querySelectorAll('tr.click').forEach(el => el.onclick = () => { $('f-cat').value = el.dataset.v; applyFilters(); });
}

let selectedLeadKeys = new Set();
const COLS = [['Name and address', 0], ['What they do', 1], ['Locality', 2], ['Phone', 5], ['Website', 8], ['Rating', 10, 1], ['Reviews', 11, 1], ['Score', 20, 1], ['Lead', -1]];

const waLink = r => r[6] === 'm' ? `https://wa.me/${r[5].replace(/\D/g, '')}` : '';
const siteLink = r => /^https?:\/\//i.test(r[7]) ? `<a href="${esc(r[7])}" target="_blank" rel="noopener">${r[8] === 's' ? 'social link' : 'website'}</a>` : '<span class="dim">none</span>';

function renderPlaces() {
  const sorted = [...rows].sort((a, b) => {
    const x = a[sortCol], y = b[sortCol];
    return (typeof x === 'number' ? x - y : String(x).localeCompare(String(y))) * sortDir || b[11] - a[11];
  });
  const pageSize = PAGE_SIZE === -1 ? (sorted.length || 1) : PAGE_SIZE;
  const pages = Math.max(1, Math.ceil(sorted.length / pageSize));
  page = Math.min(Math.max(0, page), pages - 1);
  const startIdx = page * pageSize;
  const endIdx = PAGE_SIZE === -1 ? sorted.length : Math.min(startIdx + pageSize, sorted.length);
  const visibleRows = sorted.slice(startIdx, endIdx);

  const allSelected = visibleRows.length > 0 && visibleRows.every(r => selectedLeadKeys.has(r[23]));
  const head = '<tr><th class="select-col"><input type="checkbox" id="select-all-places" title="Select all visible places" ' + (allSelected ? 'checked' : '') + '></th>' + COLS.map(([name, idx, num]) => idx < 0 ? `<th>${name}</th>` :
    `<th class="sort${num ? ' num' : ''}" data-i="${idx}">${name}${sortCol === idx ? (sortDir > 0 ? ' &#9650;' : ' &#9660;') : ''}</th>`).join('') + '</tr>';
  const body = visibleRows.map(r => {
    const open = openNow(r), status = leadStatus(r);
    const scoreCls = r[20] >= 80 ? 'hi' : r[20] >= 50 ? 'mid' : 'lo';
    const wa = waLink(r);
    const leadOpts = Object.keys(LEAD_NAMES).map(k =>
      `<option value="${k}"${status === k ? ' selected' : ''}>${LEAD_NAMES[k]}</option>`
    ).join('');
    const isChecked = selectedLeadKeys.has(r[23]);
    return `<tr class="click" data-key="${esc(r[23])}">
      <td class="select-col" onclick="event.stopPropagation()">
        <input type="checkbox" class="row-checkbox" data-chk-key="${esc(r[23])}" ${isChecked ? 'checked' : ''}>
      </td>
      <td>
        <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:4px">
          <b>${esc(r[0])}</b>
          <button type="button" class="copy-btn" title="Copy business info" onclick="event.stopPropagation();copyLeadSummary('${esc(r[23])}')">&#128203;</button>
        </div>
        ${r[28] === 1 ? ' <span class="tag hidden-tag">Hidden</span>' : ''}${r[27] & 8 ? ' <span class="tag">added by me</span>' : r[27] & 4 ? ' <span class="tag">edited</span>' : ''}
        <div class="dim">${esc(r[4])}${r[3] && !r[4].includes(r[3]) ? ' - ' + esc(r[3]) : ''}</div>
        ${isClosed(r) ? `<div class="closed">${esc(r[15])}</div>` : ''}
      </td>
      <td>
        ${esc(r[1])}${r[27] & 1 ? ' <span class="dim">(guessed)</span>' : ''}
        ${r[17] ? `<div class="dim">${esc(r[17])}</div>` : ''}
        ${r[18] ? `<div class="dim">Offers: ${esc(r[18])}</div>` : ''}
        ${r[19] ? `<div class="dim">${esc(r[19])}${open === null ? '' : open ? ' <span class="ok">(open now)</span>' : ' (closed now)'}</div>` : ''}
      </td>
      <td>${esc(r[2])}${r[27] & 2 && r[2] !== '(unknown)' ? ' <span class="dim">(worked out)</span>' : ''}</td>
      <td style="white-space:nowrap">
        ${r[5] ? `
          <div style="display:flex;align-items:center;gap:3px">
            <a href="tel:${esc(r[5])}" onclick="event.stopPropagation()">${esc(r[5])}</a>
            <button type="button" class="copy-btn" title="Copy phone" onclick="event.stopPropagation();copyText('${esc(r[5])}', 'Phone')">&#128203;</button>
          </div>
          ${wa ? `<div style="margin-top:3px"><a class="wa-link" href="${wa}" target="_blank" rel="noopener" onclick="event.stopPropagation()">&#128172; WhatsApp</a></div>` : ''}
        ` : '<span class="dim">none</span>'}
      </td>
      <td>${siteLink(r)}</td>
      <td class="num">
        ${r[10] > 0 ? `<span style="color:#d97706">&#9733;</span> ${r[10].toFixed(1)}` : '<span class="dim">-</span>'}
        ${unhappy(r) ? `<div class="closed" style="font-size:11px">${r[26]}% low</div>` : ''}
      </td>
      <td class="num">${fmt(r[11])}${r[25] ? `<div class="dim">${r[25]} recent</div>` : ''}</td>
      <td class="num"><span class="score-pill ${scoreCls}">${r[20]}</span></td>
      <td>
        <select class="lead-select ${status}" onclick="event.stopPropagation()" onchange="updateLeadStatus('${esc(r[23])}', this.value)" title="Change lead status">
          ${leadOpts}
        </select>
      </td>
    </tr>`;
  }).join('');
  $('t-places').innerHTML = head + (body || '<tr><td colspan="10" class="empty">No places match these filters</td></tr>');

  const selectAll = $('select-all-places');
  if (selectAll) {
    selectAll.onchange = () => {
      visibleRows.forEach(r => {
        if (selectAll.checked) selectedLeadKeys.add(r[23]);
        else selectedLeadKeys.delete(r[23]);
      });
      renderPlaces();
      updateBulkToolbar();
    };
  }

  $('t-places').querySelectorAll('.row-checkbox').forEach(cb => {
    cb.onchange = () => {
      const k = cb.dataset.chkKey;
      if (cb.checked) selectedLeadKeys.add(k);
      else selectedLeadKeys.delete(k);
      updateBulkToolbar();
    };
  });
  $('t-places').querySelectorAll('th.sort').forEach(th => th.onclick = () => {
    const i = +th.dataset.i;
    if (sortCol === i) sortDir = -sortDir; else { sortCol = i; sortDir = NUMERIC_COLS.includes(i) ? -1 : 1; }
    page = 0; renderPlaces();
  });
  $('t-places').querySelectorAll('tr.click').forEach(tr => tr.onclick = ev => {
    if (!ev.target.closest('a, button, select')) openPlace(tr.dataset.key);
  });
  const rangeText = sorted.length === 0 ? '0 places' :
    `Showing ${fmt(startIdx + 1)}–${fmt(endIdx)} of ${fmt(sorted.length)} places`;
  $('count').textContent = `- ${fmt(rows.length)} match`;
  const rangeEl = $('showing-range');
  if (rangeEl) rangeEl.textContent = rangeText;

  renderPager('pager-top', page, pages);
  renderPager('pager-bottom', page, pages);
}

let map, layer, fitted = false, mapTimer = 0, lastMapDraw = 0;
function renderMap() {
  lastMapDraw = Date.now();
  if (!window.L) { $('map-card').style.display = 'none'; return; }
  if (!map) {
    map = L.map('map', {preferCanvas: true});
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 19, attribution: '&copy; OpenStreetMap contributors'}).addTo(map);
    layer = L.layerGroup().addTo(map);
  }
  layer.clearLayers();
  const valid = rows.filter(r => r[12] && r[13]).slice(0, MAP_LIMIT);
  $('map-note').textContent = rows.length > MAP_LIMIT ? `showing ${fmt(MAP_LIMIT)} of ${fmt(rows.length)}` : `${fmt(valid.length)} places`;
  for (const r of valid) {
    const color = r[20] >= 80 ? '#006300' : r[20] >= 50 ? '#2a78d6' : '#898781';
    L.circleMarker([r[12], r[13]], {radius: 5, color, fillOpacity: .7, weight: 1})
      .bindPopup(`<b>${esc(r[0])}</b><br>${esc(r[1])}<br>${esc(r[2])}<br>Score: ${r[20]}<br><a href="#" data-open="${esc(r[23])}">Open details</a>`)
      .addTo(layer);
  }
  if (!fitted && valid.length) {
    const lats = valid.map(r => r[12]).sort((a, b) => a - b);
    const lons = valid.map(r => r[13]).sort((a, b) => a - b);
    const q = (arr, p) => arr[Math.floor((arr.length - 1) * p)];
    map.fitBounds([[q(lats, .02), q(lons, .02)], [q(lats, .98), q(lons, .98)]]);
    fitted = true;
  }
}

// The map is the slow part, so it is drawn after the rest of the page has updated.
function scheduleMap() {
  clearTimeout(mapTimer);
  mapTimer = setTimeout(renderMap, MAP_DELAY_MS);
}

const insightsShown = () => document.body.classList.contains('show-insights');

function render(skipMap) {
  renderTiles(); renderPlaces();
  // Charts and map are hidden until asked for; skip the work while they are.
  if (!insightsShown()) return;
  renderCharts(); renderCategoryTable();
  if (!skipMap) scheduleMap();
}

// ---- place detail panel ----
async function openPlace(key) {
  const r = DATA.find(x => x[23] === key);
  if (!r) return;
  openKey = key;
  $('panel').classList.add('open');
  $('panel').innerHTML = `<div class="head"><div class="name">${esc(r[0])}</div><button type="button" id="p-close" aria-label="Close">Close</button></div><p class="dim">Loading...</p>`;
  $('p-close').onclick = closePlace;
  let d = {};
  try { d = await (await fetch('/place.json?key=' + encodeURIComponent(key))).json(); } catch (err) { /* panel still shows the basics */ }
  if (openKey !== key) return;

  const lead = LEADS[key] || {}, open = openNow(r), sameNumber = r[5] ? (phoneCount.get(r[5]) || 1) - 1 : 0;
  const starTotal = (d.stars || []).reduce((s, n) => s + n, 0);
  const ageTotal = (d.ages || []).reduce((s, n) => s + n, 0);
  const link = (href, text) => `<a class="btn" href="${esc(href)}" target="_blank" rel="noopener">${text}</a>`;
  const kv = (k, v) => v ? `<div>${k}</div><div>${v}</div>` : '';
  $('panel').innerHTML = `
    <div class="head"><div><div class="name">${esc(r[0])}</div><div class="dim">${esc(r[1])}</div></div><button type="button" id="p-close" aria-label="Close">Close</button></div>
    ${isClosed(r) ? `<div class="closed">${esc(r[15])}</div>` : ''}
    ${d.photo ? `<img class="photo" src="${esc(d.photo)}" alt="" referrerpolicy="no-referrer" onerror="this.remove()">` : ''}
    <div class="actions">
      ${r[5] ? `<a class="btn" href="tel:${esc(r[5])}">Call ${esc(r[5])}</a>` : ''}
      ${waLink(r) ? link(waLink(r), 'WhatsApp') : ''}
      ${r[16] ? link(r[16], 'Google Maps') : ''}
      ${(r[28] === 1 || d.hidden)
        ? '<button type="button" id="p-restore-lead" class="primary">Restore lead</button>'
        : '<button type="button" id="p-hide-lead" class="crit">Hide / Exclude</button>'}
      <button type="button" id="p-edit">Edit details</button>
      ${/^https?:\/\//i.test(r[7]) ? link(r[7], r[8] === 's' ? 'Social link' : 'Website') : ''}
      ${d.street ? link(d.street, 'Street View') : ''}
    </div>
    <div class="kv">
      ${kv('Address', esc(r[4]))}
      ${kv('Locality', esc(r[2]) + (r[3] ? ' - ' + esc(r[3]) : ''))}
      ${kv('Plus code', esc(d.plus || ''))}
      ${kv('Phone', r[5] ? esc(r[5]) + (r[6] === 'm' ? ' (mobile)' : ' (landline)') + (sameNumber ? `<div class="dim">Same number on <a href="#" id="p-same">${sameNumber} other listing${sameNumber > 1 ? 's' : ''}</a></div>` : '') : '<span class="dim">not listed</span>')}
      ${kv('Email', esc(r[9]))}
      ${kv('About', esc(r[17]))}
      ${kv('Offers', esc(r[18]))}
      ${kv('Listing', r[14] ? 'Claimed by owner' + (d.owner ? ` - <a href="${esc(d.owner)}" target="_blank" rel="noopener">profile</a>` : '') : 'Not claimed by owner')}
      ${kv('Classified', (d.notes || []).concat(d.edited && d.edited.length ? ['Edited by you: ' + d.edited.join(', ')] : []).map(esc).join('<br>'))}
      ${kv('Order online', (d.order || []).map(o => `<a href="${esc(o.link)}" target="_blank" rel="noopener">${esc(o.source)}</a>`).join(', '))}
    </div>

    <h3>Lead score ${r[20]} / 100</h3>
    <div class="why">${esc((d.why || []).join('\n'))}</div>

    <h3>My lead notes</h3>
    <select id="p-status">${Object.keys(LEAD_NAMES).map(k => `<option value="${k}"${(lead.status || 'new') === k ? ' selected' : ''}>${LEAD_NAMES[k]}</option>`).join('')}</select>
    <textarea id="p-note" placeholder="Notes: who you spoke to, what they said, follow-up date">${esc(lead.note || '')}</textarea>
    <div class="actions" style="margin-top:6px"><button type="button" id="p-save">Save</button><span id="p-saved" class="dim">${lead.updated ? 'Last saved ' + esc(lead.updated.replace('T', ' ')) : ''}</span></div>

    <h3>Rating ${r[10] > 0 ? r[10].toFixed(1) + ' from ' + fmt(r[11]) + ' reviews' : '- no reviews yet'}${r[22] >= 0 ? `<span class="dim" style="font-weight:400"> - latest review ${r[22] === 0 ? 'this month' : r[22] + ' month' + (r[22] > 1 ? 's' : '') + ' ago'}</span>` : ''}</h3>
    ${starTotal ? `<div class="bars narrow" id="p-stars"></div>
      <div class="dim" style="margin-top:4px">${r[26]}% of ratings are 1-2 star${unhappy(r) ? ' <span class="closed">- many unhappy customers</span>' : ''}</div>` : ''}

    ${ageTotal ? `<h3>When the saved reviews were written <span class="dim" style="font-weight:400">- ${ageTotal} of ${fmt(r[11])}, a sample</span></h3>
      <div class="bars narrow wide-label" id="p-ages"></div>` : ''}

    ${d.busy && d.busy.length ? `<h3>Busy hours <span class="dim" style="font-weight:400">- typical week, Google's 0-100 scale</span></h3>
      <select id="p-day">${DAY_NAMES.map((day, i) => `<option value="${i}"${i === today() ? ' selected' : ''}>${day}</option>`).join('')}</select>
      <div id="p-busy"></div><div class="dim" id="p-peak"></div>` : ''}

    ${d.hours && d.hours.length ? `<h3>Opening hours ${open === null ? '' : open ? '<span class="ok" style="font-weight:400">- open now</span>' : '<span class="dim" style="font-weight:400">- closed now</span>'}</h3>
      <table class="hours">${d.hours.map(([day, text], i) => `<tr class="${i === today() ? 'today' : ''}"><td>${day}</td><td>${esc(text || 'Closed')}</td></tr>`).join('')}</table>` : ''}

    ${d.reviews && d.reviews.length ? `<h3>Recent reviews</h3>` + d.reviews.map(v => `<div class="review"><b>${esc(v.n)}</b> <span class="dim">- ${v.r ? v.r + ' star' + (v.r === 1 ? '' : 's') : ''} ${esc(v.w)}</span>
      ${v.t ? `<div>${esc(v.t)}</div>` : '<div class="dim">(rating only, no text)</div>'}</div>`).join('') : ''}`;

  if (starTotal) bars('p-stars', [5, 4, 3, 2, 1].map(n => [n + ' star', d.stars[n - 1]]), starTotal);
  if (ageTotal) bars('p-ages', ['Last 3 months', '4 - 11 months ago', '1 - 2 years ago', 'Older'].map((label, i) => [label, d.ages[i]]), ageTotal);
  if ($('p-day')) { $('p-day').onchange = () => renderBusy(d.busy); renderBusy(d.busy); }
  $('p-close').onclick = closePlace;
  $('p-edit').onclick = () => showEditForm(r, d);
  if ($('p-hide-lead')) {
    $('p-hide-lead').onclick = async () => {
      if (!confirm(`Hide "${r[0]}" from active leads?`)) return;
      try {
        const res = await fetch('/hide', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({key: r[23], hidden: true})
        });
        if (!res.ok) throw new Error('Failed to hide lead');
        r[28] = 1;
        applyFilters();
        openPlace(r[23]);
        showToast(`Hidden "${r[0]}"`);
      } catch (err) {
        showToast(err.message);
      }
    };
  }
  if ($('p-restore-lead')) {
    $('p-restore-lead').onclick = async () => {
      try {
        const res = await fetch('/hide', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({key: r[23], hidden: false})
        });
        if (!res.ok) throw new Error('Failed to restore lead');
        r[28] = 0;
        applyFilters();
        openPlace(r[23]);
        showToast(`Restored "${r[0]}"`);
      } catch (err) {
        showToast(err.message);
      }
    };
  }
  $('p-save').onclick = () => saveLead(key);
  if ($('p-same')) $('p-same').onclick = ev => { ev.preventDefault(); resetFilters(); $('f-closed').value = 'all'; $('f-q').value = r[5]; applyFilters(); };
}

const hourLabel = h => (h % 12 || 12) + (h < 12 ? 'am' : 'pm');

// Column chart of one weekday's busy level, plus the week's peak in words.
function renderBusy(week) {
  const day = +$('p-day').value, hours = week[day].slice(FIRST_BUSY_HOUR), nowHour = new Date().getHours();
  $('p-busy').innerHTML = `<div class="cols">${hours.map((level, i) => {
    const hour = i + FIRST_BUSY_HOUR;
    return `<div class="col${day === today() && hour === nowHour ? ' now' : ''}" title="${DAY_NAMES[day]} ${hourLabel(hour)}: ${level} of 100"><div style="height:${level}%"></div></div>`;
  }).join('')}</div><div class="cols ticks">${hours.map((_, i) => `<div>${i % 3 ? '' : hourLabel(i + FIRST_BUSY_HOUR)}</div>`).join('')}</div>`;
  let peak = [0, 0, 0];
  week.forEach((levels, d) => levels.forEach((level, h) => { if (level > peak[0]) peak = [level, d, h]; }));
  $('p-peak').textContent = peak[0] ? `Busiest: ${DAY_NAMES[peak[1]]} around ${hourLabel(peak[2])}` : 'No busy data for this day';
}

// ---- correcting a place, or adding one that Google does not list ----
const EDIT_FIELDS = [['title', 'Name', 0], ['category', 'Type', 1, 'dl-category'], ['phone', 'Phone', 5], ['website', 'Website', 7],
  ['emails', 'Email', 9], ['address', 'Address', 4], ['locality', 'Area', 2, 'dl-locality'], ['pincode', 'Pincode', 3]];

// r is the place to correct, or null to add a new business.
function showEditForm(r, d) {
  openKey = r ? r[23] : '';
  $('panel').classList.add('open');
  const value = idx => r && !['(no category)', '(unknown)'].includes(r[idx]) ? r[idx] : '';
  $('panel').innerHTML = `
    <div class="head"><div class="name">${r ? 'Edit: ' + esc(r[0]) : 'Add a business'}</div><button type="button" id="p-close" aria-label="Close">Close</button></div>
    <p class="dim">${r ? 'Your changes are kept separately and survive new scrapes. Google data is not overwritten.' : 'For a business that is not on Google Maps. Only the name is required.'}</p>
    <div class="form">${EDIT_FIELDS.map(([name, label, idx, list]) =>
      `<label for="e-${name}">${label}</label><input id="e-${name}" value="${esc(value(idx))}"${list ? ` list="${list}"` : ''} autocomplete="off">`).join('')}</div>
    <div class="actions">
      <button type="button" id="e-save">${r ? 'Save changes' : 'Add business'}</button>
      <button type="button" id="e-cancel">Cancel</button>
      ${r && d && d.edited && d.edited.length ? '<button type="button" id="e-restore">Restore Google data</button>' : ''}
    </div>
    <div id="e-status" class="dim" style="margin-top:8px"></div>`;
  $('p-close').onclick = closePlace;
  $('e-cancel').onclick = () => r ? openPlace(r[23]) : closePlace();
  $('e-save').onclick = () => saveEdit(r, false);
  if ($('e-restore')) $('e-restore').onclick = () => saveEdit(r, true);
  $('e-' + (r ? 'phone' : 'title')).focus();
}

async function saveEdit(r, restore) {
  const fields = Object.fromEntries(EDIT_FIELDS.map(([name]) => [name, $('e-' + name).value.trim()]));
  if (!fields.title) { $('e-status').innerHTML = '<span class="closed">Name cannot be empty</span>'; return; }
  $('e-status').textContent = 'Saving...';
  try {
    const res = await fetch(r ? '/edit' : '/add', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(r ? {key: r[23], fields, restore} : {fields})});
    if (!res.ok) throw new Error(res.status);
    const updated = await res.json();
    if (r) {
      const idx = DATA.findIndex(x => x[23] === r[23]);
      if (idx >= 0) DATA[idx] = updated;
    } else {
      DATA.unshift(updated);
    }
    countPhones(); fillSelects(); filterRows(); render();
    openPlace(updated[23]);
  } catch (err) {
    $('e-status').innerHTML = '<span class="closed">Could not save changes - check analyze.py is running</span>';
  }
}

function closePlace() { openKey = ''; $('panel').classList.remove('open'); }

async function saveLead(key) {
  const status = $('p-status').value, note = $('p-note').value.trim();
  $('p-saved').textContent = 'Saving...';
  try {
    const res = await fetch('/lead', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({key, status, note})});
    if (!res.ok) throw new Error(res.status);
    if (status === 'new' && !note) delete LEADS[key]; else LEADS[key] = {status, note, updated: new Date().toISOString().slice(0, 19)};
    $('p-saved').textContent = 'Saved';
    filterRows(); renderTiles(); renderCharts(); renderPlaces();
  } catch (err) {
    $('p-saved').innerHTML = '<span class="closed">Not saved - is analyze.py still running?</span>';
  }
}

function exportCsv() {
  if (!rows || rows.length === 0) {
    showToast('No places to export');
    return;
  }
  const head = ['title', 'category', 'locality', 'pincode', 'address', 'phone', 'phone_type', 'website', 'website_type', 'emails',
    'rating', 'reviews', 'latitude', 'longitude', 'claimed', 'status', 'maps_link', 'details', 'services', 'hours',
    'lead_score', 'months_since_review', 'lead_status', 'lead_note', 'business_fit', 'reviews_last_3_months', 'low_rating_percent', 'guessed_fields', 'edited_by_me'];
  const cell = v => /[",\n]/.test(String(v)) ? '"' + String(v).replace(/"/g, '""') + '"' : v;
  const lines = [head.join(',')].concat(rows.map(r => {
    const lead = LEADS[r[23]] || {};
    const out = r.slice(0, 21).map((v, i) =>
      i === 6 ? ({m: 'mobile', l: 'landline'}[v] || '') : i === 8 ? ({o: 'own', s: 'social/listing', n: 'none'}[v]) : i === 14 ? (v ? 'yes' : 'no') : v);
    return out.concat([r[22] >= 0 ? r[22] : '', lead.status || 'new', lead.note || '', r[24] ? 'good' : 'low', r[25], r[26] >= 0 ? r[26] : '', ['', 'category', 'locality', 'category, locality'][r[27] & 3], r[27] & 8 ? 'added' : r[27] & 4 ? 'edited' : '']).map(cell).join(',');
  }));
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob(['\ufeff' + lines.join('\r\n')], {type: 'text/csv'}));
  a.download = `leads_filtered_${rows.length}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
  showToast(`Exported ${fmt(rows.length)} places to CSV`);
}

const FILTER_IDS = ['f-q', 'f-cat', 'f-loc', 'f-pin', 'f-web', 'f-phone', 'f-rating', 'f-rev', 'f-score', 'f-hours', 'f-active', 'f-neg', 'f-claim', 'f-closed', 'f-fit', 'f-hidden', 'f-lead'];
const PRESETS = {
  'best': {'f-score': '70'},
  'noweb-mobile': {'f-web': 'n', 'f-phone': 'm'},
  'popular-noweb': {'f-web': 'sn', 'f-rev': '50'},
  'low-rated': {'f-rating': 'low', 'f-rev': '10'},
  'unclaimed': {'f-claim': '0'},
  'open-now': {'f-hours': 'now', 'f-phone': 'y'},
  'followup': {'f-lead': 'interested'},
  'unhappy': {'f-neg': '20'},
  'active': {'f-active': 'recent'},
};
function resetFilters() { FILTER_IDS.forEach(id => { if (id === 'f-q') $(id).value = ''; else $(id).selectedIndex = 0; }); }

// ---- polling for places added by the scraper while this page is open ----
async function load() {
  try {
    const res = await fetch(`/data.json?epoch=${epoch}&have=${DATA.length}`);
    if (!res.ok) throw new Error(res.status);
    const d = await res.json();
    if (d.epoch !== epoch) { DATA = []; epoch = d.epoch; }
    if (d.places && d.places.length) {
      DATA.push(...d.places);
      lastAdded = new Date().toLocaleTimeString();
      showNewData();
    }
    $('total').textContent = `${fmt(DATA.length)} places in file`;
    if ($('leads-badge')) $('leads-badge').textContent = fmt(DATA.length);
    if ($('leads-empty-hero')) $('leads-empty-hero').hidden = DATA.length > 0;
    $('live').textContent = lastAdded ? `last added at ${lastAdded}` : 'watching for new places';
    $('live').className = '';
  } catch (err) {
    $('live').textContent = 'not connected - start Lead Finder again';
    $('live').className = 'closed';
  }
}

$('f-lead').innerHTML = '<option value="">Lead status: any</option>' + Object.keys(LEAD_NAMES).map(k => `<option value="${k}">${LEAD_NAMES[k]}</option>`).join('');
FILTER_IDS.filter(id => id !== 'f-q').forEach(id => $(id).addEventListener('change', ev => {
  ev.target.blur();  // let background refreshes resume
  applyFilters();
}));

let searchTimer = 0;
const qInput = $('f-q');
const qClear = $('f-q-clear');
if (qInput) {
  qInput.addEventListener('input', () => {
    if (qClear) qClear.style.display = qInput.value ? 'block' : 'none';
    clearTimeout(searchTimer);
    searchTimer = setTimeout(applyFilters, SEARCH_DELAY_MS);
  });
  if (qClear) {
    qClear.onclick = () => {
      qInput.value = '';
      qClear.style.display = 'none';
      applyFilters();
      qInput.focus();
    };
  }
}
// Keyboard shortcuts for interactive navigation
document.addEventListener('keydown', ev => {
  if (['INPUT', 'SELECT', 'TEXTAREA'].includes((document.activeElement || {}).tagName)) {
    if (ev.key === 'Escape') document.activeElement.blur();
    return;
  }
  if (ev.key === 'ArrowLeft' || ev.key === '[') {
    if (page > 0) { page--; renderPlaces(); }
  } else if (ev.key === 'ArrowRight' || ev.key === ']') {
    const pageSize = PAGE_SIZE === -1 ? (rows.length || 1) : PAGE_SIZE;
    const pages = Math.max(1, Math.ceil(rows.length / pageSize));
    if (page < pages - 1) { page++; renderPlaces(); }
  } else if (ev.key === '/') {
    ev.preventDefault();
    if (qInput) { qInput.focus(); qInput.select(); }
  } else if (ev.key === 'Escape') {
    if (openKey) closePlace();
  }
});

// Show places that arrived while the user was typing or had a dropdown open.
document.addEventListener('focusout', () => setTimeout(() => { if (dirty) showNewData(); }, 0));
$('reset').onclick = () => {
  resetFilters();
  if (qClear) qClear.style.display = 'none';
  applyFilters();
};
document.querySelectorAll('[data-preset]').forEach(b => b.onclick = () => {
  resetFilters();
  Object.entries(PRESETS[b.dataset.preset]).forEach(([id, v]) => $(id).value = v);
  if (qClear) qClear.style.display = (qInput && qInput.value) ? 'block' : 'none';
  applyFilters();
});

// ---- simple view: a few filters by default, the rest behind two buttons ----
function setSimple(simple) {
  document.body.classList.toggle('simple', simple);
  $('more').textContent = simple ? 'More filters' : 'Fewer filters';
  remember('lf-simple', simple ? '1' : '0');
}
function setInsights(shown) {
  document.body.classList.toggle('show-insights', shown);
  $('insights').textContent = shown ? 'Hide insights' : 'Show insights';
  remember('lf-insights', shown ? '1' : '0');
}
$('more').onclick = () => {
  const simple = !document.body.classList.contains('simple');
  setSimple(simple);
  // A hidden filter that still narrows the list would be confusing: clear them.
  if (simple) { ADVANCED_FILTERS.forEach(id => { $(id).selectedIndex = 0; }); applyFilters(); }
};
$('insights').onclick = () => {
  setInsights(!insightsShown());
  if (insightsShown()) { render(); if (map) map.invalidateSize(); }
};
setSimple(recall('lf-simple') !== '0');
setInsights(recall('lf-insights') === '1');

// ---- finding businesses: start, watch and stop the Google Maps search ----
let scrapeStatus = null, scrapeTimer = 0;

function scrapeProgress(s) {
  const pctText = s.total ? pct(s.done, s.total) : '';
  const ratio = s.total ? `${fmt(s.done)} / ${fmt(s.total)}` : `${fmt(s.done)} searches finished`;
  const errors = s.errors ? ` (${s.errors} error${s.errors === 1 ? '' : 's'})` : '';
  return `Pass ${s.pass} &middot; ${ratio}${errors}${pctText ? ' &middot; ' + pctText : ''}`;
}

function updateScrapeBar(s) {
  scrapeStatus = s;
  const barText = {
    'idle': 'No search running.',
    'waiting': `Waiting between passes... ${scrapeProgress(s)}`,
    'running': `Searching... ${scrapeProgress(s)}`,
    'complete': `Search finished: ${fmt(s.done)} searches completed.`,
    'blocked': 'Google is asking for a CAPTCHA. Wait 15 minutes before continuing.',
    'error': s.message || 'The scraper stopped unexpectedly.',
  }[s.state] || s.message;
  $('scrape-text').innerHTML = (s.area ? `<b>${esc(s.area)}</b>: ` : '') + barText;
  $('scrape-stop').hidden = !(s.state === 'running' || s.state === 'waiting');
  $('scrape-continue').hidden = !(s.state === 'idle' || s.state === 'blocked' || s.state === 'error') || !s.area;
  if ($('s-status')) renderScrapeStatus();
}

async function scrapePost(path, body) {
  const res = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body || {})});
  const data = await res.json();
  if (!res.ok) throw new Error(data.message || `Request failed (${res.status})`);
  updateScrapeBar(data);
  return data;
}

$('scrape-stop').onclick = () => scrapePost('/scrape/stop');
$('scrape-continue').onclick = () => scrapePost('/scrape/start');
// ---- Navigation: Tabs between Leads CRM and Target & Scrape Campaign ----
function switchView(viewName) {
  const isCampaign = viewName === 'campaign';
  $('view-campaign').hidden = !isCampaign;
  $('view-leads').hidden = isCampaign;
  $('tab-campaign').classList.toggle('active', isCampaign);
  $('tab-leads').classList.toggle('active', !isCampaign);
  if (isCampaign) {
    loadCampaignSettings();
    setTimeout(() => { if (campaignMap) campaignMap.invalidateSize(); }, 120);
  } else {
    setTimeout(() => { if (map) map.invalidateSize(); }, 120);
  }
  window.location.hash = isCampaign ? 'campaign' : 'leads';
}

$('tab-leads').onclick = () => switchView('leads');
$('tab-campaign').onclick = () => switchView('campaign');
$('scrape-open').onclick = () => switchView('campaign');
$('s-view-leads-btn').onclick = () => switchView('leads');
$('btn-first-campaign').onclick = () => switchView('campaign');

// ---- Campaign View: Location, Keyword Studio, Search Settings & Scraper Execution ----
let campaignMap = null, campaignRectangle = null, campaignBbox = null;
let campaignPacks = {}, campaignRecommended = [];

function renderScrapeStatus() {
  const s = scrapeStatus;
  if (!s) return;
  const percent = s.total ? Math.min(100, Math.round(100 * s.done / s.total)) : 0;
  const isRunning = s.state === 'running' || s.state === 'waiting';

  if ($('s-main-status')) {
    $('s-main-status').innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
        <div><b>Campaign Status:</b> ${esc(s.message)} ${s.area ? `&middot; <b>${esc(s.area)}</b>` : ''}</div>
        <div class="dim">${scrapeProgress(s)}</div>
      </div>
      ${s.total ? `<div class="progress" style="margin-top:8px"><div style="width:${percent}%"></div></div>` : ''}
    `;
  }
  if ($('scraper-logs-pre') && s.recent_logs && s.recent_logs.length) {
    $('scraper-logs-pre').textContent = s.recent_logs.join('\n');
    const container = $('scraper-logs-container');
    if (container && !container.hidden) {
      container.scrollTop = container.scrollHeight;
    }
  }
  if ($('s-start-main')) {
    $('s-start-main').textContent = isRunning ? 'Stop Search' : 'Start Search';
    $('s-start-main').className = isRunning ? 'campaign-launch-btn' : 'primary campaign-launch-btn';
  }
}

async function loadCampaignSettings() {
  try {
    const res = await fetch('/scrape.json');
    if (!res.ok) return;
    const s = await res.json();
    scrapeStatus = s;
    campaignPacks = s.packs || {};
    campaignRecommended = s.recommended || [];
    const saved = s.settings || {};
    if (saved.area && !$('s-area').value) $('s-area').value = saved.area;
    if (saved.bbox && !campaignBbox) campaignBbox = saved.bbox;
    if (saved.cell) $('s-cell').value = String(saved.cell);
    if (saved.concurrency) $('s-speed').value = String(saved.concurrency);

    if (!$('s-keywords').value.trim()) {
      const defaultKw = (saved.keywords && saved.keywords.length ? saved.keywords : campaignRecommended) || [];
      $('s-keywords').value = defaultKw.join('\n');
    }

    if (campaignBbox && !rawBbox) rawBbox = [...campaignBbox];
    renderKeywordChips();
    loadCustomPacks();
    initCampaignMap();
    describeCampaign();
    renderScrapeStatus();

    if ($('btn-toggle-logs')) {
      $('btn-toggle-logs').onclick = () => {
        const container = $('scraper-logs-container');
        if (!container) return;
        const isHidden = container.hidden;
        container.hidden = !isHidden;
        $('btn-toggle-logs').textContent = isHidden ? 'Hide Live Logs ↑' : 'Show Live Logs ↓';
        if (isHidden) container.scrollTop = container.scrollHeight;
      };
    }

    if ($('btn-backup-db')) {
      $('btn-backup-db').onclick = async () => {
        try {
          const res = await fetch('/database/backup', {method: 'POST', headers: {'Content-Type': 'application/json'}});
          const data = await res.json();
          if (!res.ok) throw new Error(data.error || 'Backup failed');
          showToast(`Backup saved: ${data.backup}`);
        } catch (err) {
          showToast(err.message);
        }
      };
    }

    if ($('btn-reset-db')) {
      $('btn-reset-db').onclick = async () => {
        if (scrapeStatus && (scrapeStatus.state === 'running' || scrapeStatus.state === 'waiting')) {
          showToast('Stop the active search before resetting the database');
          return;
        }
        if (!confirm('Are you sure you want to clear all leads and start fresh?\n\nA full backup will be automatically saved in data/backups/ before clearing.')) return;
        try {
          const res = await fetch('/database/reset', {method: 'POST', headers: {'Content-Type': 'application/json'}});
          const data = await res.json();
          if (!res.ok) throw new Error(data.error || 'Reset failed');
          DATA = [];
          showNewData();
          load();
          showToast(`Database cleared. Auto-backup saved: ${data.backup}`);
          switchView('campaign');
        } catch (err) {
          showToast(err.message);
        }
      };
    }
  } catch (err) {
    console.error('Failed to load campaign settings:', err);
  }
}

function getCampaignKeywords() {
  return $('s-keywords').value.split('\n').map(w => w.trim()).filter(Boolean);
}

function setCampaignKeywords(words) {
  $('s-keywords').value = words.join('\n');
  renderKeywordChips();
  describeCampaign();
}

function renderKeywordChips() {
  const words = getCampaignKeywords();
  $('s-count').textContent = `${fmt(words.length)} business type${words.length === 1 ? '' : 's'} selected`;
  const container = $('kw-chips-container');
  if (!container) return;
  if (!words.length) {
    container.innerHTML = '<div class="empty-chips-hint">No business types added yet. Pick a pack above or type a custom business name.</div>';
    return;
  }
  container.innerHTML = words.map(w => `
    <span class="kw-chip">
      <span>${esc(w)}</span>
      <button type="button" class="kw-chip-remove" data-del-kw="${esc(w)}" title="Remove ${esc(w)}">&times;</button>
    </span>
  `).join('');

  container.querySelectorAll('[data-del-kw]').forEach(btn => {
    btn.onclick = () => {
      const toRemove = btn.dataset.delKw.toLowerCase();
      const updated = getCampaignKeywords().filter(k => k.toLowerCase() !== toRemove);
      setCampaignKeywords(updated);
    };
  });
}

function addCampaignKeywords(newWords) {
  const existing = new Set(getCampaignKeywords().map(w => w.toLowerCase()));
  const toAdd = [];
  newWords.forEach(w => {
    const clean = w.trim();
    if (clean && !existing.has(clean.toLowerCase())) {
      existing.add(clean.toLowerCase());
      toAdd.push(clean);
    }
  });
  if (toAdd.length > 0) {
    const all = [...getCampaignKeywords(), ...toAdd];
    setCampaignKeywords(all);
    showToast(`Added ${toAdd.length} business type${toAdd.length > 1 ? 's' : ''}`);
  } else {
    showToast('All those business types are already in your list');
  }
}

$('s-kw-add').onclick = () => {
  const val = $('s-kw-input').value.trim();
  if (!val) return;
  addCampaignKeywords([val]);
  $('s-kw-input').value = '';
  $('s-kw-input').focus();
};
$('s-kw-input').onkeydown = ev => {
  if (ev.key === 'Enter') {
    ev.preventDefault();
    $('s-kw-add').click();
  }
};

document.querySelectorAll('#view-campaign button[data-pack]').forEach(btn => {
  btn.onclick = () => {
    const pack = btn.dataset.pack;
    const list = campaignPacks[pack] || [];
    if (list.length) addCampaignKeywords(list);
  };
});

$('s-recommended').onclick = () => {
  if (campaignRecommended && campaignRecommended.length) {
    setCampaignKeywords(campaignRecommended);
    showToast(`Loaded ${campaignRecommended.length} curated business types`);
  }
};

$('s-clear-kw').onclick = () => {
  setCampaignKeywords([]);
  showToast('Cleared all business types');
};

$('btn-kw-chips').onclick = () => {
  $('btn-kw-chips').classList.add('active');
  $('btn-kw-raw').classList.remove('active');
  $('kw-chips-container').hidden = false;
  $('s-keywords').hidden = true;
  renderKeywordChips();
};
$('btn-kw-raw').onclick = () => {
  $('btn-kw-raw').classList.add('active');
  $('btn-kw-chips').classList.remove('active');
  $('kw-chips-container').hidden = true;
  $('s-keywords').hidden = false;
};
$('s-keywords').addEventListener('input', () => {
  renderKeywordChips();
  describeCampaign();
});
['s-cell', 's-speed'].forEach(id => {
  const el = $(id);
  if (el) el.addEventListener('change', describeCampaign);
});

function initCampaignMap() {
  if (!window.L || !$('s-map')) return;
  if (!campaignMap) {
    campaignMap = L.map('s-map');
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap contributors'
    }).addTo(campaignMap);
    campaignMap.setView([22.5, 79], 4);
  }
  if (campaignRectangle) campaignRectangle.remove();
  if (campaignBbox) {
    const bounds = [[campaignBbox[0], campaignBbox[1]], [campaignBbox[2], campaignBbox[3]]];
    campaignRectangle = L.rectangle(bounds, {
      color: getComputedStyle(document.documentElement).getPropertyValue('--bar').trim(),
      weight: 2,
      fillOpacity: 0.08
    }).addTo(campaignMap);
    campaignMap.fitBounds(bounds, {padding: [16, 16]});
  }
  campaignMap.invalidateSize();
}

let rawBbox = null;
let currentPaddingKm = 0;

function applyAreaPadding(padKm) {
  currentPaddingKm = Math.max(0, padKm);
  if (!rawBbox) return;
  const avgLat = (rawBbox[0] + rawBbox[2]) / 2;
  const latPad = currentPaddingKm / KM_PER_DEGREE;
  const lonPad = currentPaddingKm / (KM_PER_DEGREE * Math.cos(avgLat * Math.PI / 180));
  campaignBbox = [
    rawBbox[0] - latPad,
    rawBbox[1] - lonPad,
    rawBbox[2] + latPad,
    rawBbox[3] + lonPad
  ];
  if ($('current-padding-label')) {
    $('current-padding-label').textContent = currentPaddingKm > 0 ? `(+${currentPaddingKm} km padding)` : '(City core)';
  }
  document.querySelectorAll('#view-campaign [data-pad]').forEach(btn => {
    btn.classList.toggle('active', +btn.dataset.pad === currentPaddingKm);
  });
  initCampaignMap();
  describeCampaign();
}

async function findCampaignArea() {
  const name = $('s-area').value.trim();
  if (!name) {
    $('s-error').textContent = 'Please type a city or area name first.';
    return;
  }
  $('s-error').textContent = '';
  $('s-size').textContent = 'Looking up location...';
  try {
    const found = await (await fetch('https://nominatim.openstreetmap.org/search?format=json&limit=1&q=' + encodeURIComponent(name))).json();
    if (!found.length) throw new Error('not found');
    const [south, north, west, east] = found[0].boundingbox.map(Number);
    rawBbox = [south, west, north, east];
    currentPaddingKm = 0;
    applyAreaPadding(0);
  } catch (err) {
    $('s-size').textContent = '';
    $('s-error').textContent = 'Could not find that location. Check spelling and internet connection.';
  }
}

$('s-find').onclick = findCampaignArea;
$('s-area').onkeydown = ev => { if (ev.key === 'Enter') findCampaignArea(); };

function describeCampaign() {
  const count = getCampaignKeywords().length;
  $('s-count').textContent = `${fmt(count)} business type${count === 1 ? '' : 's'} selected`;
  if (!campaignBbox) {
    $('s-size').textContent = 'No area chosen yet.';
    $('s-estimate').textContent = 'Choose an area and keywords to see search estimates.';
    return;
  }
  const cell = +$('s-cell').value;
  const heightKm = (campaignBbox[2] - campaignBbox[0]) * KM_PER_DEGREE;
  const widthKm = (campaignBbox[3] - campaignBbox[1]) * KM_PER_DEGREE * Math.cos(campaignBbox[0] * Math.PI / 180);
  const searches = Math.ceil(heightKm / cell) * Math.ceil(widthKm / cell) * count;
  const speed = +$('s-speed').value;
  const hours = searches / (SEARCHES_PER_MINUTE * speed / 8) / 60;
  $('s-size').textContent = `Target Area: about ${Math.round(widthKm)} × ${Math.round(heightKm)} km`;
  $('s-estimate').innerHTML = `
    <b>Search Estimate:</b> ~${fmt(searches)} searches across ${Math.ceil(heightKm / cell) * Math.ceil(widthKm / cell)} grid squares.<br>
    Estimated time: <b>${hours < 1 ? 'under an hour' : Math.round(hours) + ' hour' + (Math.round(hours) === 1 ? '' : 's')}</b> at current speed.
  `;
}

$('s-start-main').onclick = async () => {
  $('s-error').textContent = '';
  const active = scrapeStatus && (scrapeStatus.state === 'running' || scrapeStatus.state === 'waiting');
  try {
    if (active) {
      await scrapePost('/scrape/stop');
      showToast('Search stopped');
    } else {
      const area = $('s-area').value.trim();
      const keywords = getCampaignKeywords();
      if (!area) {
        $('s-error').textContent = 'Please enter a target city or area first.';
        return;
      }
      if (!keywords.length) {
        $('s-error').textContent = 'Please select at least one business keyword or category pack.';
        return;
      }
      await scrapePost('/scrape/start', {
        settings: {
          area,
          bbox: campaignBbox,
          keywords,
          cell: +$('s-cell').value,
          concurrency: +$('s-speed').value
        }
      });
      showToast('Search campaign started!');
    }
  } catch (err) {
    $('s-error').textContent = err.message;
  }
};

// Continuous scrape status polling
setInterval(async () => {
  try {
    const res = await fetch('/scrape.json');
    if (res.ok) updateScrapeBar(await res.json());
  } catch (err) {}
}, SCRAPE_POLL_MS);

// Initialize view routing based on hash or if user has 0 leads
if (window.location.hash === '#campaign') {
  switchView('campaign');
} else {
  loadCampaignSettings();
}


// ---- Bulk Lead Actions ----
function updateBulkToolbar() {
  const bar = $('bulk-toolbar');
  if (!bar) return;
  const count = selectedLeadKeys.size;
  bar.hidden = count === 0;
  bar.classList.toggle('open', count > 0);
  if ($('bulk-count')) $('bulk-count').textContent = fmt(count);
}

if ($('bulk-status-select')) {
  $('bulk-status-select').onchange = async () => {
    const status = $('bulk-status-select').value;
    if (!status || selectedLeadKeys.size === 0) return;
    const keys = [...selectedLeadKeys];
    let okCount = 0;
    for (const key of keys) {
      try {
        await fetch('/lead', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({key, status, note: ''})
        });
        LEADS[key] = {status, note: (LEADS[key] && LEADS[key].note) || '', updated: new Date().toISOString()};
        okCount++;
      } catch (err) {}
    }
    selectedLeadKeys.clear();
    $('bulk-status-select').value = '';
    applyFilters();
    updateBulkToolbar();
    showToast(`Updated ${okCount} lead${okCount === 1 ? '' : 's'} to "${LEAD_NAMES[status]}"`);
  };
}

if ($('btn-bulk-hide')) {
  $('btn-bulk-hide').onclick = async () => {
    if (selectedLeadKeys.size === 0) return;
    if (!confirm(`Hide ${selectedLeadKeys.size} selected leads from active view?`)) return;
    const keys = [...selectedLeadKeys];
    for (const key of keys) {
      try {
        await fetch('/hide', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({key, hidden: true})
        });
        const r = DATA.find(p => p[23] === key);
        if (r) r[28] = 1;
      } catch (err) {}
    }
    selectedLeadKeys.clear();
    applyFilters();
    updateBulkToolbar();
    showToast(`Hidden ${keys.length} leads`);
  };
}

if ($('btn-bulk-export')) {
  $('btn-bulk-export').onclick = () => {
    if (selectedLeadKeys.size === 0) return;
    const targetRows = rows.filter(r => selectedLeadKeys.has(r[23]));
    if (!targetRows.length) return;
    const head = ['title', 'category', 'locality', 'pincode', 'address', 'phone', 'phone_type', 'website', 'website_type', 'emails',
      'rating', 'reviews', 'latitude', 'longitude', 'claimed', 'status', 'maps_link', 'details', 'services', 'hours',
      'lead_score', 'months_since_review', 'lead_status', 'lead_note', 'business_fit', 'reviews_last_3_months', 'low_rating_percent', 'guessed_fields', 'edited_by_me'];
    const cell = v => /[",\n]/.test(String(v)) ? '"' + String(v).replace(/"/g, '""') + '"' : v;
    const lines = [head.join(',')].concat(targetRows.map(r => {
      const lead = LEADS[r[23]] || {};
      const out = r.slice(0, 21).map((v, i) =>
        i === 6 ? ({m: 'mobile', l: 'landline'}[v] || '') : i === 8 ? ({o: 'own', s: 'social/listing', n: 'none'}[v]) : i === 14 ? (v ? 'yes' : 'no') : v);
      return out.concat([r[22] >= 0 ? r[22] : '', lead.status || 'new', lead.note || '', r[24] ? 'good' : 'low', r[25], r[26] >= 0 ? r[26] : '', ['', 'category', 'locality', 'category, locality'][r[27] & 3], r[27] & 8 ? 'added' : r[27] & 4 ? 'edited' : '']).map(cell).join(',');
    }));
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob(['\ufeff' + lines.join('\r\n')], {type: 'text/csv'}));
    a.download = `leads_selected_${targetRows.length}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
    showToast(`Exported ${fmt(targetRows.length)} selected leads`);
  };
}

if ($('btn-bulk-clear')) {
  $('btn-bulk-clear').onclick = () => {
    selectedLeadKeys.clear();
    renderPlaces();
    updateBulkToolbar();
  };
}

// ---- Scope and Radius Adjustments ----
document.querySelectorAll('#view-campaign [data-pad]').forEach(btn => {
  btn.onclick = () => applyAreaPadding(+btn.dataset.pad);
});

if ($('btn-pad-sub-10')) $('btn-pad-sub-10').onclick = () => applyAreaPadding(currentPaddingKm - 10);
if ($('btn-pad-sub-5')) $('btn-pad-sub-5').onclick = () => applyAreaPadding(currentPaddingKm - 5);
if ($('btn-pad-add-5')) $('btn-pad-add-5').onclick = () => applyAreaPadding(currentPaddingKm + 5);
if ($('btn-pad-add-10')) $('btn-pad-add-10').onclick = () => applyAreaPadding(currentPaddingKm + 10);
if ($('btn-pad-add-25')) $('btn-pad-add-25').onclick = () => applyAreaPadding(currentPaddingKm + 25);

// ---- Custom Keyword Packs (localStorage) ----
function getCustomPacks() {
  try { return JSON.parse(localStorage.getItem('lf_custom_packs') || '{}'); } catch (e) { return {}; }
}

function saveCustomPacks(packs) {
  localStorage.setItem('lf_custom_packs', JSON.stringify(packs));
  loadCustomPacks();
}

function loadCustomPacks() {
  const container = $('custom-packs-container');
  if (!container) return;
  const packs = getCustomPacks();
  const keys = Object.keys(packs);
  container.hidden = keys.length === 0;
  container.innerHTML = keys.map(k => `
    <span class="custom-pack-pill">
      <button type="button" class="btn-pack custom-pack-btn" data-custom-pack="${esc(k)}">⭐ ${esc(k)} (${packs[k].length})</button>
      <button type="button" class="custom-pack-del" data-del-pack="${esc(k)}" title="Delete pack">&times;</button>
    </span>
  `).join('');

  container.querySelectorAll('[data-custom-pack]').forEach(btn => {
    btn.onclick = () => {
      const name = btn.dataset.customPack;
      const list = (getCustomPacks())[name] || [];
      if (list.length) addCampaignKeywords(list);
    };
  });

  container.querySelectorAll('[data-del-pack]').forEach(btn => {
    btn.onclick = () => {
      const name = btn.dataset.delPack;
      if (!confirm(`Delete custom pack "${name}"?`)) return;
      const packs = getCustomPacks();
      delete packs[name];
      saveCustomPacks(packs);
      showToast(`Deleted pack "${name}"`);
    };
  });
}

if ($('btn-save-custom-pack')) {
  $('btn-save-custom-pack').onclick = () => {
    const words = getCampaignKeywords();
    if (!words.length) {
      showToast('Add at least one business keyword before saving a pack');
      return;
    }
    const name = prompt('Name your custom keyword pack (e.g. My Jodhpur Clinics):');
    if (!name || !name.trim()) return;
    const cleanName = name.trim();
    const packs = getCustomPacks();
    packs[cleanName] = words;
    saveCustomPacks(packs);
    showToast(`Saved custom pack "${cleanName}" with ${words.length} keywords`);
  };
}

// ---- Google Maps Official Categories Directory Modal ----
let ALL_CATEGORIES = {};
let selectedModalCategories = new Set();
let activeCatIndustry = 'All';

async function loadCategoriesDirectory() {
  if (Object.keys(ALL_CATEGORIES).length) return;
  try {
    const res = await fetch('/categories.json');
    if (!res.ok) return;
    ALL_CATEGORIES = await res.json();
    renderCategoryIndustryTabs();
    renderCategoryModalList();
  } catch (err) {
    console.error('Failed to load categories', err);
  }
}

function renderCategoryIndustryTabs() {
  const container = $('cat-industry-tabs');
  if (!container) return;
  const industries = ['All', ...Object.keys(ALL_CATEGORIES)];
  container.innerHTML = industries.map(ind => {
    const count = ind === 'All' ? Object.values(ALL_CATEGORIES).flat().length : (ALL_CATEGORIES[ind] || []).length;
    const isAct = ind === activeCatIndustry;
    return `<button type="button" class="btn-pack${isAct ? ' active' : ''}" data-cat-ind="${esc(ind)}">${esc(ind)} (${count})</button>`;
  }).join('');

  container.querySelectorAll('[data-cat-ind]').forEach(btn => {
    btn.onclick = () => {
      activeCatIndustry = btn.dataset.catInd;
      renderCategoryIndustryTabs();
      renderCategoryModalList();
    };
  });
}

function getFilteredCategories() {
  const query = ($('cat-modal-search') ? $('cat-modal-search').value : '').trim().toLowerCase();
  let list = [];
  if (activeCatIndustry === 'All') {
    Object.entries(ALL_CATEGORIES).forEach(([ind, cats]) => {
      cats.forEach(c => list.push({name: c, industry: ind}));
    });
  } else {
    (ALL_CATEGORIES[activeCatIndustry] || []).forEach(c => list.push({name: c, industry: activeCatIndustry}));
  }
  if (query) {
    list = list.filter(item => item.name.toLowerCase().includes(query) || item.industry.toLowerCase().includes(query));
  }
  return list;
}

function renderCategoryModalList() {
  const container = $('cat-list-container');
  if (!container) return;
  const list = getFilteredCategories();
  if (!list.length) {
    container.innerHTML = '<div class="dim" style="padding:20px;grid-column:1/-1;text-align:center">No matching categories found.</div>';
    updateCategoryModalCount();
    return;
  }
  container.innerHTML = list.map(item => {
    const isChecked = selectedModalCategories.has(item.name.toLowerCase());
    return `
      <label class="cat-item">
        <input type="checkbox" data-cat-name="${esc(item.name)}" ${isChecked ? 'checked' : ''}>
        <div>
          <div>${esc(item.name)}</div>
          <div class="dim" style="font-size:11px">${esc(item.industry)}</div>
        </div>
      </label>
    `;
  }).join('');

  container.querySelectorAll('input[type="checkbox"]').forEach(cb => {
    cb.onchange = () => {
      const name = cb.dataset.catName.toLowerCase();
      if (cb.checked) selectedModalCategories.add(name);
      else selectedModalCategories.delete(name);
      updateCategoryModalCount();
    };
  });
  updateCategoryModalCount();
}

function updateCategoryModalCount() {
  if ($('cat-modal-selection-count')) {
    $('cat-modal-selection-count').textContent = `${selectedModalCategories.size} categories selected`;
  }
}

function openCategoryModal() {
  const m = $('modal-categories');
  if (m) {
    m.hidden = false;
    m.classList.add('open');
  }
}

function closeCategoryModal() {
  const m = $('modal-categories');
  if (m) {
    m.hidden = true;
    m.classList.remove('open');
  }
}

if ($('btn-browse-categories')) {
  $('btn-browse-categories').onclick = async () => {
    await loadCategoriesDirectory();
    selectedModalCategories.clear();
    const existing = new Set(getCampaignKeywords().map(w => w.toLowerCase()));
    Object.values(ALL_CATEGORIES).flat().forEach(c => {
      if (existing.has(c.toLowerCase())) selectedModalCategories.add(c.toLowerCase());
    });
    openCategoryModal();
    renderCategoryModalList();
  };
}

if ($('btn-close-cat-modal')) $('btn-close-cat-modal').onclick = closeCategoryModal;
if ($('btn-cancel-cat-modal')) $('btn-cancel-cat-modal').onclick = closeCategoryModal;
if ($('modal-categories')) {
  $('modal-categories').addEventListener('click', ev => {
    if (ev.target === $('modal-categories')) closeCategoryModal();
  });
}

if ($('cat-modal-search')) {
  $('cat-modal-search').addEventListener('input', () => {
    if ($('cat-modal-clear')) $('cat-modal-clear').style.display = $('cat-modal-search').value ? 'block' : 'none';
    renderCategoryModalList();
  });
}
if ($('cat-modal-clear')) {
  $('cat-modal-clear').onclick = () => {
    $('cat-modal-search').value = '';
    $('cat-modal-clear').style.display = 'none';
    renderCategoryModalList();
    $('cat-modal-search').focus();
  };
}

if ($('btn-cat-select-all')) {
  $('btn-cat-select-all').onclick = () => {
    getFilteredCategories().forEach(item => selectedModalCategories.add(item.name.toLowerCase()));
    renderCategoryModalList();
  };
}
if ($('btn-cat-deselect-all')) {
  $('btn-cat-deselect-all').onclick = () => {
    getFilteredCategories().forEach(item => selectedModalCategories.delete(item.name.toLowerCase()));
    renderCategoryModalList();
  };
}

if ($('btn-apply-categories')) {
  $('btn-apply-categories').onclick = () => {
    if (selectedModalCategories.size === 0) {
      showToast('No categories selected');
      return;
    }
    const toAdd = [];
    const lookup = {};
    Object.values(ALL_CATEGORIES).flat().forEach(c => { lookup[c.toLowerCase()] = c; });
    selectedModalCategories.forEach(cLower => {
      toAdd.push(lookup[cLower] || cLower);
    });
    addCampaignKeywords(toAdd);
    closeCategoryModal();
    showToast(`Added ${toAdd.length} categories to search campaign`);
  };
}

$('add-place').onclick = () => showEditForm(null, null);
document.addEventListener('click', ev => {
  const el = ev.target.closest('[data-open]');
  if (el) { ev.preventDefault(); openPlace(el.dataset.open); }
});
document.addEventListener('keydown', ev => {
  if (ev.key === 'Escape') {
    closePlace();
    closeCategoryModal();
  }
});

fetch('/leads.json').then(res => res.json()).then(leads => { LEADS = leads; }).catch(() => {}).finally(() => {
  load();
  setInterval(load, REFRESH_MS);
});

