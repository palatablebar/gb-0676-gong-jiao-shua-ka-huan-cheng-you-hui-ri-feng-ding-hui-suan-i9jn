const feedback = document.querySelector('#feedback');
const detail = document.querySelector('#detail');

async function call(url, options) {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.message || '请求失败');
  return body;
}

function esc(value) {
  return String(value ?? '—').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function shortHash(h) { return h ? h.slice(0, 8) : '—'; }

async function submitRun() {
  const batchRef = document.querySelector('#batch_ref').value.trim();
  if (!batchRef) { feedback.textContent = '请填写业务批次号。'; return; }
  const file = document.querySelector('#file').files[0];
  const note = document.querySelector('#change_note').value.trim();
  const mode = document.querySelector('#rule_mode').value;
  feedback.textContent = '复算中…';
  try {
    if (file) {
      const data = new FormData();
      // 上传走通用入口，再以批次复算语义由后端落账（这里用 /api/upload 之外的批次接口）
      const text = await file.text();
      await call(`/api/batches/${encodeURIComponent(batchRef)}/recalc`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ csv: text, parameters: { rule_mode: mode }, change_note: note }),
      });
    } else {
      const sample = document.querySelector('#sample').value;
      await call(`/api/batches/${encodeURIComponent(batchRef)}/recalc`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sample, parameters: { rule_mode: mode }, change_note: note }),
      });
    }
    feedback.textContent = '已落账。';
    location.reload();
  } catch (error) { feedback.textContent = error.message; }
}

document.querySelector('#run').addEventListener('click', submitRun);

document.querySelector('#batch-list').addEventListener('click', event => {
  const btn = event.target.closest('button.link');
  if (btn) openBatch(btn.dataset.ref);
});

function renderVersions(batch) {
  const tbody = document.querySelector('#version-list');
  tbody.innerHTML = batch.versions.map(v => {
    const isCurrent = v.run_id === batch.current_run_id;
    const balanced = v.balanced ? '对平' : '<span class="bad">未对平</span>';
    return `<tr class="${isCurrent ? 'current' : ''}">
      <td>V${v.ledger_version}</td>
      <td>${esc(v.kind)}</td>
      <td>${esc(v.rule_version)}</td>
      <td><code>${shortHash(v.input_hash)}</code></td>
      <td>${balanced}</td>
      <td class="note">${esc(v.change_note || '—')}</td>
      <td>${isCurrent ? '<span class="seal">★当前</span>' : ''}</td>
      <td>${isCurrent ? '—' : `<button class="link" data-switch="${v.run_id}">设为当前</button>`}</td>
    </tr>`;
  }).join('');
  tbody.querySelectorAll('button[data-switch]').forEach(btn => btn.addEventListener('click', async () => {
    try {
      const body = await call(`/api/batches/${encodeURIComponent(batch.batch_ref)}/switch`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run_id: btn.dataset.switch, expected_run_id: batch.current_run_id }),
      });
      openBatch(batch.batch_ref);
    } catch (e) { document.querySelector('#chain-status').textContent = e.message; }
  }));
}

function renderRuleDiff(batch, comparison) {
  const current = batch.versions.find(v => v.run_id === batch.current_run_id);
  const change = current.rule_change;
  const box = document.querySelector('#rule-diff');
  document.querySelector('#rule-diff-meta').textContent =
    `${change.from || '首版'} → ${change.to}${change.rule_changed ? '（规则已变化）' : '（规则未变）'}`;
  let html = `<p class="note">${esc(change.changes || '该版本规则相对上一版无变化。')}</p>`;
  if (change.diff.length) {
    html += '<table><thead><tr><th>参数</th><th>原值</th><th>现值</th></tr></thead><tbody>' +
      change.diff.map(d => `<tr><td>${esc(d.param)}</td><td>${esc(d.old)}</td><td>${esc(d.new)}</td></tr>`).join('') +
      '</tbody></table>';
  }
  box.innerHTML = html;
}

function renderCompare(comparison) {
  const c = comparison;
  document.querySelector('#cmp-summary').textContent =
    `输入${c.input_changed ? '已变化' : '一致'} · 规则${c.rule_changed ? '已变化' : '一致'} · 应扣总额差 ${c.expected_total_delta} 元`;
  const kpis = [
    ['原账应扣/元', c.original_expected_total], ['复算应扣/元', c.recalc_expected_total],
    ['规则变化行', c.counts.RULE || 0], ['数据纠错行', c.counts.DATA || 0],
    ['双重变化行', c.counts.MIXED || 0], ['无变化行', c.unchanged],
  ];
  document.querySelector('#cmp-kpis').innerHTML = kpis.map(([k, v]) =>
    `<div class="kpi"><span>${k}</span><b>${esc(v)}</b></div>`).join('');
  const causeClass = { RULE: 'cause-rule', DATA: 'cause-data', MIXED: 'cause-mixed', NONE: '' };
  document.querySelector('#compare-list').innerHTML = c.lines.map(l => `<tr class="${causeClass[l.cause] || ''}">
    <td><code>${esc(l.event_id)}</code></td>
    <td>${l.sources.join(',') || '—'}</td>
    <td>${esc(l.original_status)}</td><td>${esc(l.original_expected)}</td>
    <td>${esc(l.recalc_status)}</td><td>${esc(l.recalc_expected)}</td>
    <td>${l.input_changed ? '<span class="bad">是</span>' : '否'}</td>
    <td>${l.rule_changed ? '<span class="bad">是</span>' : '否'}</td>
    <td><span class="cause ${causeClass[l.cause] || ''}">${esc(l.cause_text)}</span></td>
  </tr>`).join('') + (c.only_recalc_sources.length
    ? `<tr><td colspan="9" class="note">补录新增来源行：${c.only_recalc_sources.join(', ')}</td></tr>` : '')
    + (c.only_original_sources.length
    ? `<tr><td colspan="9" class="note">移除来源行：${c.only_original_sources.join(', ')}</td></tr>` : '');
}

async function openBatch(ref) {
  const batch = await call(`/api/batches/${encodeURIComponent(ref)}`);
  const { comparison } = await call(`/api/batches/${encodeURIComponent(ref)}/compare`);
  detail.hidden = false;
  document.querySelector('#d-ref').textContent = ref;
  document.querySelector('#d-sealed').innerHTML = batch.sealed
    ? '<span class="seal">已封存 · 结论锁定</span>' : '<span class="open">未封存</span>';
  document.querySelector('#btn-archive').href = `/api/runs/${batch.current_run_id}/archive`;
  document.querySelector('#btn-seal').disabled = batch.sealed;
  renderVersions(batch);
  renderRuleDiff(batch, comparison);
  renderCompare(comparison);
  document.querySelector('#chain-status').textContent = '';
  detail.dataset.ref = ref;
}

document.querySelector('#btn-verify').addEventListener('click', async () => {
  const ref = detail.dataset.ref;
  const status = document.querySelector('#chain-status');
  try {
    const r = await call(`/api/batches/${encodeURIComponent(ref)}/verify`);
    status.textContent = r.intact ? `复算链完好（校验 ${r.checked} 个版本）` : '断链：' + r.problems.join('；');
    status.className = r.intact ? 'ok' : 'bad';
  } catch (e) { status.textContent = e.message; status.className = 'bad'; }
});

document.querySelector('#btn-seal').addEventListener('click', async () => {
  const ref = detail.dataset.ref;
  const status = document.querySelector('#chain-status');
  try {
    await call(`/api/batches/${encodeURIComponent(ref)}/seal`, { method: 'POST' });
    status.textContent = '日结已封存，账本转为只读。'; status.className = 'ok';
    openBatch(ref);
  } catch (e) { status.textContent = e.message; status.className = 'bad'; }
});
