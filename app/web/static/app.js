// Minimal, dependency-free helpers (no CDN).

// ── global loader: overlay + top progress bar (purely visual) ────────────────
let _gbarT = null, _gbarW = 0;
function _gbarStart() {
  const b = document.getElementById('gbar'); if (!b) return;
  _gbarW = 8; b.style.width = '8%'; b.classList.add('on');
  clearInterval(_gbarT);
  _gbarT = setInterval(() => { _gbarW = Math.min(92, _gbarW + Math.random() * 7 + 1); b.style.width = _gbarW + '%'; }, 420);
}
function _gbarDone() {
  const b = document.getElementById('gbar'); if (!b) return;
  clearInterval(_gbarT); b.style.width = '100%';
  setTimeout(() => { b.classList.remove('on'); b.style.width = '0'; }, 360);
}
function showLoader(msg, sub) {
  const l = document.getElementById('gloader'); if (!l) return;
  if (msg !== undefined) document.getElementById('gmsg').textContent = ('' + msg).slice(0, 120);
  document.getElementById('gsub').textContent = sub !== undefined ? sub : 'This can take a moment';
  l.classList.add('on'); _gbarStart();
}
function hideLoader() { const l = document.getElementById('gloader'); if (l) l.classList.remove('on'); _gbarDone(); }

// Any POST form shows the loader as it submits (the page navigates, which clears
// it on reload). The message is derived from the clicked button, so it is always
// contextual. Opt a form out with data-noloader, or override with data-loading.
document.addEventListener('submit', (e) => {
  if (e.defaultPrevented) return;                 // a confirm()/validation cancelled the submit
  const f = e.target;
  if (!f || !f.matches || !f.matches('form[method=post]') || f.hasAttribute('data-noloader')) return;
  let msg = f.getAttribute('data-loading');
  if (!msg && e.submitter) msg = (e.submitter.textContent || '')
    .replace(/[\u{1F000}-\u{1FAFF}\u{2190}-\u{27BF}\u{2B00}-\u{2BFF}\u{FE00}-\u{FE0F}①②③]/gu, '')
    .replace(/\s+/g, ' ').trim();
  showLoader(msg || 'Working');
});
// never leave a stuck loader (back/forward cache, or the page finishing load)
window.addEventListener('pageshow', hideLoader);
window.addEventListener('load', hideLoader);

// Plain <a> navigation (the sidebar nav, "This Week" included) never went through
// the submit-loader above, so a slow nav (e.g. a live Sheets read on /pending)
// showed nothing at all — it just looked frozen. Show the loader on click for
// any same-origin, unmodified left-click to a normal page link.
document.addEventListener('click', (e) => {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  const a = e.target.closest && e.target.closest('a[href]');
  if (!a || a.hasAttribute('data-noloader') || a.target === '_blank') return;
  const href = a.getAttribute('href') || '';
  if (!href || href.startsWith('#') || href.startsWith('javascript:') || a.hasAttribute('download')) return;
  if (a.origin !== window.location.origin) return;
  showLoader(a.getAttribute('data-loading') || a.textContent.trim() || 'Loading', 'This can take a moment');
});

// Auto-loader for JS-driven POST fetches (setup steps, atomize, dataset, finalize,
// download QC, etc.). Transparent passthrough: the original promise is returned
// unchanged. Background GET polling (/job, /roster) is excluded so it never flickers.
(function () {
  const _fetch = window.fetch;
  window.fetch = function (input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    const method = ((init && init.method) || (typeof input === 'object' && input && input.method) || 'GET').toUpperCase();
    const track = method === 'POST' && url.charAt(0) === '/' && !/\/(job|roster)(\?|$)/.test(url);
    if (track) showLoader();
    let p;
    try { p = _fetch.apply(this, arguments); }
    catch (err) { if (track) hideLoader(); throw err; }
    if (track) { const done = () => hideLoader(); p.then(done, done); }
    return p;
  };
})();


// Poll the job partial while a job is running, swap it into #jobpanel.
// data-running lives on the .card INSIDE #jobpanel, so query for it (reading it
// off #jobpanel itself always returned undefined — that froze the panel).
// Drive the full-page overlay loader from a job panel's live progress text, so
// "stage N of M" is visible on the LOADER itself, not just a small card below it
// that's easy to miss — and so the loader only clears once the job is actually
// done, not as soon as the redirected page finishes loading (which happens long
// before a metabase fetch / pending-sheet import actually completes).
function _syncLoaderToJob(panel, running) {
  if (running) {
    const msg = panel.querySelector('.small b, .small')?.textContent?.trim();
    showLoader(msg || 'Working', 'This can take a moment');
  } else {
    hideLoader();
  }
}

function pollJob(slug) {
  const panel = document.getElementById('jobpanel');
  if (!panel) return;
  const running = () => !!panel.querySelector('[data-running="1"]');
  // Grading / report / download jobs show their own live progress + log inline in
  // #jobpanel (and the roster updates row-by-row below it) — the full-screen loader
  // overlay used to also cover the page for these, hiding that live progress behind
  // a blocking spinner. Just clear it here instead of driving it from job state.
  hideLoader();
  // live roster: re-render the Results card so rows flip to graded/downloaded
  // as each student commits, without a manual refresh. Preserve search + filter.
  async function refreshRoster() {
    const roster = document.getElementById('roster');
    if (!roster) return;
    const oldBox = roster.querySelector('.ftable');
    const q = oldBox?.querySelector('.ft-search')?.value || '';
    const f = oldBox?.querySelector('.ft-filter.on')?.dataset.val || '*';
    let html;
    try { html = await (await fetch(`/evals/${slug}/roster`)).text(); }
    catch (e) { return; }
    roster.innerHTML = html;
    const nb = roster.querySelector('.ftable');
    if (nb) {                                    // restore the user's search/filter
      const si = nb.querySelector('.ft-search'); if (si && q) si.value = q;
      nb.querySelectorAll('.ft-filter').forEach(p => p.classList.toggle('on', p.dataset.val === f));
      if (window.ftApply) ftApply(nb);
    }
  }
  async function tick() {
    try { panel.innerHTML = await (await fetch(`/evals/${slug}/job`)).text(); }
    catch (e) { /* keep trying */ }
    hideLoader();
    await refreshRoster();
    if (running()) setTimeout(tick, 2000);
  }
  setTimeout(tick, 1500);   // always refresh once; keep polling while a job runs
}

// Same idea for the "This Week" tracker-sheet fetch, which isn't scoped to one
// eval (no roster to refresh) and reloads the whole page once done, since a
// completed fetch changes which rows show up and their "in grader" badges.
function pollPendingJob() {
  const panel = document.getElementById('pendingjobpanel');
  if (!panel) return;
  showLoader('Reading tracker sheet…', 'This can take a moment');
  async function tick() {
    let html = '';
    try { html = await (await fetch('/pending/job')).text(); }
    catch (e) { setTimeout(tick, 2000); return; }
    panel.innerHTML = html;
    const running = !!panel.querySelector('[data-running="1"]');
    _syncLoaderToJob(panel, running);
    if (running) { setTimeout(tick, 1500); return; }
    // done — drop the ?job= param and reload so the picks table reflects the fetch
    setTimeout(() => { window.location.href = '/pending'; }, 500);
  }
  setTimeout(tick, 800);
}

async function openCost(slug) {
  const m = document.getElementById('costModal'), body = document.getElementById('costBody');
  if (!m) return;
  body.innerHTML = '<p class="muted">Loading…</p>';
  m.classList.add('open');
  try { body.innerHTML = await (await fetch(`/evals/${slug}/cost`)).text(); }
  catch (e) { body.innerHTML = '<p>Could not load cost data.</p>'; }
}
function closeCost() {
  const m = document.getElementById('costModal');
  if (m) m.classList.remove('open');
}
async function finalizeCost(slug, runType) {
  const body = document.getElementById('costBody');
  if (!body) return;
  body.innerHTML = '<p class="muted">Writing summary and clearing the per-call log…</p>';
  const fd = new FormData(); fd.append('run_type', runType);
  try { body.innerHTML = await (await fetch(`/evals/${slug}/cost/finalize`, {method: 'POST', body: fd})).text(); }
  catch (e) { body.innerHTML = '<p>Failed to finalize.</p>'; }
}

// ── sticky table headers sit just under the (sticky) per-eval navbar ─────────
function _syncEvalbar() {
  const b = document.querySelector('.evalbar');
  if (b) document.documentElement.style.setProperty('--evalbar-h', b.offsetHeight + 'px');
}
_syncEvalbar();                                 // app.js loads at end of <body> → navbar exists
window.addEventListener('load', _syncEvalbar);  // re-measure once fonts/images settle
window.addEventListener('resize', _syncEvalbar);

// ── generic filterable table: search (data-s) + filter pills (data-f tags) ───
// Any `.ftable` block with a `.ft-search` input, `.ft-filter` pills (data-val),
// a table whose rows carry data-s (haystack) + data-f (space-separated tags),
// and an optional `.ft-nomatch` / `.ft-count`. Used by Results + Submissions.
function ftApply(box) {
  if (!box) return;
  const q = (box.querySelector('.ft-search')?.value || '').toLowerCase().trim();
  const on = box.querySelector('.ft-filter.on');
  const active = on ? on.dataset.val : '*';
  let shown = 0;
  box.querySelectorAll('tbody tr').forEach(r => {
    const hitQ = !q || (r.dataset.s || '').includes(q);
    const hitF = active === '*' || (' ' + (r.dataset.f || '') + ' ').includes(' ' + active + ' ');
    const show = hitQ && hitF;
    r.style.display = show ? '' : 'none';
    if (show) shown++;
  });
  const nm = box.querySelector('.ft-nomatch'); if (nm) nm.style.display = shown ? 'none' : '';
  const cnt = box.querySelector('.ft-count'); if (cnt) cnt.textContent = shown;
}
// show an inline spinner in a form's submit button on submit (full-page POSTs)
function btnLoading(form, label) {
  const b = form.querySelector('button[type=submit], button:not([type])');
  if (b && !b.disabled) {
    const txt = label || 'Working…';
    // defer so the browser still submits the form before we disable the button
    setTimeout(() => { b.disabled = true; b.innerHTML = '<span class="spin"></span>' + txt; }, 0);
  }
  return true;
}
function ftSearch(el) { ftApply(el.closest('.ftable')); }
function ftFilter(pill) {
  const box = pill.closest('.ftable');
  box.querySelectorAll('.ft-filter').forEach(p => p.classList.remove('on'));
  pill.classList.add('on');
  ftApply(box);
}
// back-compat shim (older inline handler name)
function filterRows() { ftApply(document.querySelector('.ftable')); }

// ── copy report-card link + toast ───────────────────────────────────────────
function toast(msg) {
  let t = document.getElementById('toast');
  if (!t) { t = document.createElement('div'); t.id = 'toast'; t.className = 'toast'; document.body.appendChild(t); }
  t.textContent = msg; t.classList.add('show');
  clearTimeout(window.__toastT); window.__toastT = setTimeout(() => t.classList.remove('show'), 1800);
}
async function copyLink(url) {
  try { await navigator.clipboard.writeText(url); toast('Report-card link copied'); }
  catch (e) { window.prompt('Copy the report-card link:', url); }
}

// ── report-card modal ───────────────────────────────────────────────────────
function openCard(slug, code, title) {
  document.getElementById('cardTitle').textContent = title || code;
  document.getElementById('cardFrame').src = `/evals/${slug}/card/${code}`;
  document.getElementById('cardModal').classList.add('open');
}
function closeCard() {
  const m = document.getElementById('cardModal');
  if (m) { m.classList.remove('open'); document.getElementById('cardFrame').src = ''; }
}
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeCard(); });
document.addEventListener('click', e => {   // click backdrop to close
  if (e.target && e.target.id === 'cardModal') closeCard();
});

// ── rubric setup ────────────────────────────────────────────────────────────
function _esc(s) {
  return (s || '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}

// Student-submitted links are untrusted — only ever render http(s) as a clickable
// href. Escaping alone doesn't stop a 'javascript:' URI from running when clicked.
function _safeHref(url) {
  return /^https?:\/\//i.test(url || '') ? _esc(url) : '#';
}

async function analyzePaper(slug) {
  const ps = document.getElementById('problem_statement').value;
  const box = document.getElementById('analysis');
  box.hidden = false;
  box.innerHTML = 'Analyzing the paper…';
  const fd = new FormData(); fd.append('problem_statement', ps);
  try {
    const r = await fetch(`/evals/${slug}/setup/analyze`, {method: 'POST', body: fd});
    const d = await r.json();
    if (d.error) { box.innerHTML = '<span style="color:#b23">' + _esc(d.error) + '</span>'; return; }
    let h = d.rubric_present
      ? '<b style="color:#2c7a3d">✓ A rubric / mark scheme is already in the paper.</b> ' + _esc(d.rubric_evidence)
      : '<b style="color:#a4670f">No rubric found in the paper.</b> Use Generate below to author a strict one.';
    h += '<div class="muted small" style="margin:6px 0">Total marks detected: ' + (d.total_marks_detected || '—') + '</div>';
    if (d.parts && d.parts.length) {
      h += '<table><thead><tr><th>Part</th><th>Title</th><th>Marks</th><th>What it asks</th></tr></thead><tbody>';
      d.parts.forEach(p => { h += '<tr><td><b>' + _esc(p.label) + '</b></td><td>' + _esc(p.title) +
        '</td><td>' + (p.marks || '—') + '</td><td class="small">' + _esc(p.summary) + '</td></tr>'; });
      h += '</tbody></table>';
    }
    if (d.notes) h += '<div class="muted small" style="margin-top:6px">' + _esc(d.notes) + '</div>';
    box.innerHTML = h;
  } catch (e) { box.innerHTML = '<span style="color:#b23">' + _esc(String(e)) + '</span>'; }
}

// ── download QC (read-only cross-check) ─────────────────────────────────────
async function downloadQC(slug) {
  const box = document.getElementById('dlqc');
  box.innerHTML = '<div class="card" style="background:#fafbfd"><span class="spin"></span> Cross-checking downloaded files…</div>';
  try {
    const d = await (await fetch(`/evals/${slug}/submissions/download-qc`)).json();
    if (d.error) { box.innerHTML = '<div class="card"><span style="color:#b23">' + _esc(d.error) + '</span></div>'; return; }
    const pills = Object.entries(d.by_status || {})
      .map(([k, v]) => `<span class="pill ${k === 'ok' ? 'ok' : (k === 'empty' ? 'partial' : 'failed')}">${_esc(k)} ${v}</span>`).join(' ');
    let h = '<div class="card" style="background:#fafbfd">'
      + '<div class="between"><h3 style="margin:0">Download QC — check only (no re-download)</h3>'
      + '<span class="muted small">' + d.n_files_total.toLocaleString() + ' files on disk</span></div>'
      + '<p class="small" style="margin:6px 0">'
      + '<b>' + d.n_submitted + '</b> submitted · <b style="color:#2c7a3d">' + d.fully_ok + '</b> fully ok · '
      + (d.n_approved ? '<b style="color:#2c7a3d">' + d.n_approved + '</b> manually approved · ' : '')
      + '<b style="color:' + (d.with_issues ? '#b23' : '#2c7a3d') + '">' + d.with_issues + '</b> with issues &nbsp; ' + pills + '</p>';
    if (!d.with_issues) {
      h += '<div class="flash ok" style="margin:6px 0 0">✓ Every submitted link downloaded with files. Ready to grade.</div>';
    } else {
      h += '<div class="tblwrap" style="max-height:340px;margin-top:6px"><table><thead><tr>'
        + '<th>Student</th><th>Name</th><th>OK</th><th>Problem link(s)</th><th>Action</th></tr></thead><tbody>';
      d.issues.forEach(i => {
        const probs = i.problems.map(p =>
          `<div><span class="pill failed" style="font-size:10px">${_esc(p.status)}</span> `
          + `<a href="${_safeHref(p.link)}" target="_blank" rel="noopener">${_esc(p.link.replace('https://', '')).slice(0, 52)} ↗</a>`
          + ` <span class="muted small">— ${_esc(p.note)}</span></div>`).join('');
        h += `<tr><td><b>${_esc(i.code)}</b></td><td class="small">${_esc(i.name || '—')}</td>`
          + `<td class="small">${i.n_ok}/${i.n_links}</td><td class="small">${probs}</td>`
          + `<td><button class="iact" title="mark verified — include this student in grading" `
          + `onclick="qcPass('${slug}','${_esc(i.code)}',this)">✓ Pass</button></td></tr>`;
      });
      h += '</tbody></table></div>'
        + '<p class="muted small" style="margin:8px 0 0"><b>✓ Pass</b> marks a student verified-OK '
        + '(e.g. an acceptable partial) so grading includes them — no re-download. Otherwise fix at the '
        + 'source (ask students to re-share / push files) and re-download only if needed.</p>';
    }
    box.innerHTML = h + '</div>';
  } catch (e) { box.innerHTML = '<div class="card"><span style="color:#b23">' + _esc(String(e)) + '</span></div>'; }
}

async function qcPass(slug, code, btn) {
  btn.disabled = true; btn.textContent = '…';
  try {
    const d = await (await fetch(`/evals/${slug}/submissions/download-qc/pass/${code}`, {method: 'POST'})).json();
    if (d.error) { btn.disabled = false; btn.textContent = '✓ Pass'; toast(_esc(d.error)); return; }
    const tr = btn.closest('tr'); if (tr) tr.style.opacity = '.5';
    btn.outerHTML = '<span class="pill ok">✓ verified · will grade</span>';
    toast(code + ' marked verified — included in grading');
  } catch (e) { btn.disabled = false; btn.textContent = '✓ Pass'; toast(String(e)); }
}

// ── dataset grounding ───────────────────────────────────────────────────────
async function detectDataset(slug) {
  const ps = document.getElementById('problem_statement').value;
  const box = document.getElementById('ds_candidates');
  box.hidden = false; box.innerHTML = 'Scanning the paper for dataset links…';
  const fd = new FormData(); fd.append('problem_statement', ps);
  try {
    const d = await (await fetch(`/evals/${slug}/setup/dataset/detect`, {method: 'POST', body: fd})).json();
    if (d.error) { box.innerHTML = '<span style="color:#b23">' + _esc(d.error) + '</span>'; return; }
    if (!d.candidates.length) { box.innerHTML = '<span class="muted small">No dataset-looking links found — paste one below if there is one.</span>'; return; }
    box.innerHTML = '<div class="small muted" style="margin-bottom:4px">Detected candidate(s) — click to use:</div>' +
      d.candidates.map(c => `<div class="small"><a href="#" onclick="document.getElementById('ds_link').value='${_esc(c.raw)}';return false">${_esc(c.raw)}</a> <span class="muted">— ${_esc(c.why)}</span></div>`).join('');
  } catch (e) { box.innerHTML = '<span style="color:#b23">' + _esc(String(e)) + '</span>'; }
}

async function buildDataset(slug) {
  const link = document.getElementById('ds_link').value.trim();
  const btn = document.getElementById('ds_build_btn');
  const status = document.getElementById('ds_status');
  if (!link) { status.innerHTML = '<span style="color:#b23">Paste or pick a dataset link first.</span>'; return; }
  btn.disabled = true; status.innerHTML = '<span class="spin"></span> Downloading &amp; profiling the dataset…';
  const fd = new FormData(); fd.append('links', link);
  try {
    const d = await (await fetch(`/evals/${slug}/setup/dataset/build`, {method: 'POST', body: fd})).json();
    if (d.error) { status.innerHTML = '<span style="color:#b23">' + _esc(d.error) + '</span>'; return; }
    const tbls = (d.tables || []).map(t => `${t.file} ${t.rows.toLocaleString()}×${t.n_cols}`).join(' · ');
    status.innerHTML = '<span style="color:#2c7a3d">✓ Profiled ' + d.n_tables + ' file(s), ' +
      d.n_rows.toLocaleString() + ' rows.</span>' + (d.skipped && d.skipped.length ? ' Skipped: ' + _esc(d.skipped.join(', ')) : '');
    const sum = document.getElementById('ds_summary'); sum.hidden = false;
    document.getElementById('ds_brief').textContent = d.brief || '';
    if (!d.n_tables) status.innerHTML += ' <span style="color:#a4670f">No tabular files found — check the link is a CSV/Excel dataset (not the submission folder).</span>';
  } catch (e) { status.innerHTML = '<span style="color:#b23">' + _esc(String(e)) + '</span>'; }
  finally { btn.disabled = false; }
}

async function draftReference(slug) {
  const btn = document.getElementById('ds_ref_btn');
  const status = document.getElementById('ds_ref_status');
  btn.disabled = true; status.innerHTML = ' <span class="spin"></span> drafting from the dataset…';
  try {
    const d = await (await fetch(`/evals/${slug}/setup/dataset/reference`, {method: 'POST'})).json();
    if (d.error) { status.innerHTML = '<span style="color:#b23">' + _esc(d.error) + '</span>'; return; }
    document.getElementById('ds_reference').value = d.reference_md || '';
    status.innerHTML = '<span style="color:#2c7a3d">✓ drafted — review/edit, then tick “use in grading” and Save.</span>';
  } catch (e) { status.innerHTML = '<span style="color:#b23">' + _esc(String(e)) + '</span>'; }
  finally { btn.disabled = false; }
}

async function saveReference(slug) {
  const status = document.getElementById('ds_ref_status');
  const fd = new FormData();
  fd.append('reference_md', document.getElementById('ds_reference').value);
  fd.append('approved', document.getElementById('ds_ref_approved').checked ? '1' : '0');
  try {
    const d = await (await fetch(`/evals/${slug}/setup/dataset/save-reference`, {method: 'POST', body: fd})).json();
    if (d.error) { status.innerHTML = '<span style="color:#b23">' + _esc(d.error) + '</span>'; return; }
    status.innerHTML = '<span style="color:#2c7a3d">✓ saved' + (d.approved ? ' — will be used in grading.' : ' — grounding on the factual profile only.') + '</span>';
  } catch (e) { status.innerHTML = '<span style="color:#b23">' + _esc(String(e)) + '</span>'; }
}

// ── rubric atomisation ──────────────────────────────────────────────────────
async function atomizeRubric(slug) {
  const btn = document.getElementById('atombtn');
  const status = document.getElementById('atomstatus');
  btn.disabled = true;
  status.innerHTML = '<span class="spin"></span> Decomposing the rubric into checkable atoms… (~30s)';
  try {
    const fd = new FormData();
    fd.append('rubric_markdown', (document.getElementById('rubric_markdown') || {}).value || '');
    const d = await (await fetch(`/evals/${slug}/setup/atomize`, {method: 'POST', body: fd})).json();
    if (d.error) { status.innerHTML = '<span style="color:#b23">' + _esc(d.error) + '</span>'; return; }
    document.getElementById('atombox').hidden = false;
    document.getElementById('atommd').value = d.markdown || '';
    document.getElementById('atomapproved').checked = false;
    status.innerHTML = '<span style="color:#2c7a3d">✓ Atomised · total ' + d.total +
      ' marks (preserved).</span> ' + _esc(d.notes || '') + ' Review, then tick “use in grading” and Save.';
  } catch (e) { status.innerHTML = '<span style="color:#b23">' + _esc(String(e)) + '</span>'; }
  finally { btn.disabled = false; }
}

async function approveAtoms(slug) {
  const save = document.getElementById('atomsave');
  const fd = new FormData();
  fd.append('approved', document.getElementById('atomapproved').checked ? '1' : '0');
  fd.append('markdown', (document.getElementById('atommd') || {}).value || '');
  try {
    const d = await (await fetch(`/evals/${slug}/setup/atomize/approve`, {method: 'POST', body: fd})).json();
    if (d.error) { save.innerHTML = '<span style="color:#b23">' + _esc(d.error) + '</span>'; return; }
    if (d.markdown) document.getElementById('atommd').value = d.markdown;   // normalised
    let msg = '✓ saved' + (d.approved ? ' — grading will use the atomised rubric.'
                                       : ' — grading uses the standard rubric.');
    save.innerHTML = '<span style="color:#2c7a3d">' + msg + '</span>' +
      (d.warning ? ' <span style="color:#b23">⚠ ' + _esc(d.warning) + '</span>' : '');
  } catch (e) { save.innerHTML = '<span style="color:#b23">' + _esc(String(e)) + '</span>'; }
}

async function saveRubric(slug) {
  const btn = document.getElementById('saverubricbtn');
  const out = document.getElementById('rubricsave');
  btn.disabled = true;
  const fd = new FormData();
  fd.append('rubric_markdown', (document.getElementById('rubric_markdown') || {}).value || '');
  fd.append('problem_statement', (document.getElementById('problem_statement') || {}).value || '');
  fd.append('total_marks', (document.getElementById('total_marks') || {}).value || '');
  fd.append('normalize_to', (document.getElementById('normalize_to') || {}).value || '');
  try {
    const d = await (await fetch(`/evals/${slug}/setup/save-rubric`, {method: 'POST', body: fd})).json();
    if (d.error) { out.innerHTML = '<span style="color:#b23">' + _esc(d.error) + '</span>'; return; }
    out.innerHTML = '<span style="color:#2c7a3d">✓ Rubric saved · ' + d.n_parts +
      ' sections · total ' + d.total + ' marks. You can now atomise it.</span>';
  } catch (e) { out.innerHTML = '<span style="color:#b23">' + _esc(String(e)) + '</span>'; }
  finally { btn.disabled = false; }
}

async function metabaseQC(slug) {
  const out = document.getElementById('mbqc');
  out.innerHTML = '<span class="spin"></span> checking the submission sheet…';
  try {
    const d = await (await fetch(`/evals/${slug}/metabase/qc`, {method: 'POST'})).json();
    if (d.error) { out.innerHTML = '<span style="color:#b23">' + _esc(d.error) + '</span>'; return; }
    const s = d.stats || {};
    let html = d.ok ? '<span style="color:#2c7a3d">✓ QC passed</span>'
                    : '<span style="color:#b23">✗ QC found issues</span>';
    html += ` · ${s.rows} rows · ${s.students} students · ${s.github_links} github · ${s.blank_code} blank`;
    if (d.issues && d.issues.length)
      html += '<br><span style="color:#b23">Issues: ' + d.issues.map(_esc).join('; ') + '</span>';
    if (d.warnings && d.warnings.length)
      html += '<br><span style="color:#a4670f">⚠ ' + d.warnings.map(_esc).join('; ') + '</span>';
    out.innerHTML = html;
  } catch (e) { out.innerHTML = '<span style="color:#b23">' + _esc(String(e)) + '</span>'; }
}

async function resetField(slug, field, label) {
  if (!confirm('Reset ' + label + '? This clears the saved ' + label + ' for this evaluation.')) return;
  const fd = new FormData(); fd.append('field', field);
  try {
    const d = await (await fetch(`/evals/${slug}/setup/reset`, {method: 'POST', body: fd})).json();
    if (d.error) { toast(d.error); return; }
    location.reload();
  } catch (e) { toast(String(e)); }
}

async function extractRubric(slug) {
  const ps = document.getElementById('problem_statement').value;
  const btn = document.getElementById('extractbtn');
  const status = document.getElementById('genstatus');
  btn.disabled = true;
  status.textContent = 'Transcribing the paper’s own rubric… (may take ~30s)';
  const fd = new FormData(); fd.append('problem_statement', ps);
  try {
    const r = await fetch(`/evals/${slug}/setup/extract`, {method: 'POST', body: fd});
    const d = await r.json();
    if (d.error) { status.innerHTML = '<span style="color:#b23">' + _esc(d.error) + '</span>'; return; }
    document.getElementById('rubric_markdown').value = d.rubric_markdown;
    status.innerHTML = '<span style="color:#2c7a3d">✓ Lifted the paper’s own rubric · total ' + d.total +
      ' marks</span> — verbatim, nothing added.' + (d.notes ? ' ' + _esc(d.notes) : '') +
      ' Review and edit before saving.';
  } catch (e) { status.innerHTML = '<span style="color:#b23">' + _esc(String(e)) + '</span>'; }
  finally { btn.disabled = false; }
}

async function generateRubric(slug) {
  const ps = document.getElementById('problem_statement').value;
  const total = document.getElementById('total_marks').value;
  const btn = document.getElementById('genbtn');
  const status = document.getElementById('genstatus');
  btn.disabled = true;
  status.textContent = 'Generating a strict rubric… (may take ~30s)';
  const fd = new FormData(); fd.append('problem_statement', ps); fd.append('total_marks', total);
  try {
    const r = await fetch(`/evals/${slug}/setup/generate`, {method: 'POST', body: fd});
    const d = await r.json();
    if (d.error) { status.innerHTML = '<span style="color:#b23">' + _esc(d.error) + '</span>'; return; }
    document.getElementById('rubric_markdown').value = d.rubric_markdown;
    status.innerHTML = '<span style="color:#2c7a3d">✓ Generated · total ' + d.total + ' marks</span>' +
      (d.posture_label ? ' · <b>' + _esc(d.posture_label) + '</b> posture' : '') +
      (d.notes ? ' · ' + _esc(d.notes) : '') + ' — review and edit before saving.';
  } catch (e) { status.innerHTML = '<span style="color:#b23">' + _esc(String(e)) + '</span>'; }
  finally { btn.disabled = false; }
}

// Expand a student row's evidence detail (QC page) via fetch.
async function toggleStudent(slug, code, el) {
  const box = document.getElementById('det_' + code);
  if (box.dataset.loaded === '1') { box.hidden = !box.hidden; return; }
  const r = await fetch(`/evals/${slug}/student/${code}`);
  box.innerHTML = await r.text();
  box.dataset.loaded = '1';
  box.hidden = false;
}
