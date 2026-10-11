/* ACID Ledger dashboard — vanilla JS, polls /api/state every 1.2s. */
"use strict";

const $ = (sel) => document.querySelector(sel);
let lastBalances = {};

function fmt(n) {
  return Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function short(h, n = 8) {
  return h ? h.slice(0, n) : "—";
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

let toastTimer = null;
function toast(msg, kind = "ok") {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "toast " + kind;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 4200);
}

function renderAccounts(accounts) {
  const box = $("#accounts");
  $("#seed-btn").classList.toggle("hidden", accounts.length > 0);
  if (!accounts.length) {
    box.innerHTML = '<p class="empty">Ledger is empty — seed the demo data to get started.</p>';
    $("#from").innerHTML = "";
    $("#to").innerHTML = "";
    return;
  }
  box.innerHTML = accounts
    .map(({ document: d }) => {
      const prev = lastBalances[d.accountId];
      const changed = prev !== undefined && prev !== d.balance;
      lastBalances[d.accountId] = d.balance;
      return `<div class="card${changed ? " flash" : ""}">
        <div>
          <div class="who">${esc(d.accountId)}</div>
          <div class="meta">${esc(d.accountType)} · ${esc(d.accountNumber)} · ${esc(d.customerId)} · ${esc(d.status)}</div>
        </div>
        <div class="bal">${fmt(d.balance)} <span class="meta">${esc(d.currency)}</span></div>
      </div>`;
    })
    .join("");
  const opts = accounts
    .map(({ document: d }) => `<option value="${esc(d.accountId)}">${esc(d.accountId)} (${fmt(d.balance)} ${esc(d.currency)})</option>`)
    .join("");
  const fromSel = $("#from"), toSel = $("#to");
  const fromVal = fromSel.value, toVal = toSel.value;
  fromSel.innerHTML = opts;
  toSel.innerHTML = opts;
  fromSel.value = accounts.some(({ document: d }) => d.accountId === fromVal) ? fromVal : accounts[0].document.accountId;
  const fallbackTo = accounts.find(({ document: d }) => d.accountId !== fromSel.value);
  toSel.value = accounts.some(({ document: d }) => d.accountId === toVal) && toVal !== fromSel.value
    ? toVal : (fallbackTo ? fallbackTo.document.accountId : fromSel.value);
}

function opChip(op) {
  const cls = op.action === "INSERT" ? "insert" : op.action === "UPDATE" ? "update" : "";
  return `<span class="chip ${cls}">${esc(op.action)} <b>${esc(op.table)}</b> ${esc(op.key)}</span>`;
}

function renderJournal(entries) {
  const body = $("#journal-table tbody");
  if (!entries.length) {
    body.innerHTML = '<tr><td colspan="4" class="empty">no journal entries yet</td></tr>';
    return;
  }
  body.innerHTML = entries
    .map((e) => `<tr>
      <td class="mono">${e.seq}</td>
      <td class="mono">${esc(e.txTime)}</td>
      <td>${e.payload.ops.map(opChip).join("")}</td>
      <td class="hashlink mono" title="${esc(e.prevHash)} → ${esc(e.hash)}">
        ${short(e.prevHash)}<span class="arrow">→</span><span class="new">${short(e.hash)}</span>
      </td>
    </tr>`)
    .join("");
}

function renderTransactions(txs) {
  const body = $("#tx-table tbody");
  if (!txs.length) {
    body.innerHTML = '<tr><td colspan="6" class="empty">no transactions yet</td></tr>';
    return;
  }
  body.innerHTML = txs
    .map(({ document: d }) => `<tr>
      <td class="mono">${esc(d.transactionId)}</td>
      <td class="mono">${esc(d.fromAccountId)}</td>
      <td class="mono">${esc(d.toAccountId)}</td>
      <td class="mono">${fmt(d.amount)} ${esc(d.currency)}</td>
      <td class="mono">${esc(d.timestamp)}</td>
      <td class="mono">${esc(d.status)}</td>
    </tr>`)
    .join("");
}

function renderVerify(report, digest) {
  const pill = $("#status-pill");
  if (report.ok) {
    pill.className = "pill ok";
    pill.textContent = "VERIFIED";
  } else {
    pill.className = "pill bad";
    pill.textContent = "TAMPERING DETECTED";
  }
  $("#digest-short").textContent = short(digest.digest, 16);
  $("#entry-count").textContent = report.chain.entries;

  const section = (name, data) => {
    const errs = (data.errors || []).map((e) => `<li>${esc(e)}</li>`).join("");
    return `<div><span class="${data.ok ? "badge-ok" : "badge-bad"}">${data.ok ? "✓" : "✗"} ${name}</span>
      ${errs ? `<ul class="err-list">${errs}</ul>` : ""}</div>`;
  };
  $("#verify-report").innerHTML =
    `<div style="display:flex; gap:28px; flex-wrap:wrap">
      ${section("hash chain", report.chain)}
      ${section("state ↔ journal replay", report.state)}
      ${section("merkle digest" + (report.digest.expected ? " vs pinned digest" : ""), report.digest)}
     </div>`;
}

function render(state) {
  renderAccounts(state.accounts);
  renderJournal(state.journal);
  renderTransactions(state.transactions);
  renderVerify(state.verify, state.digest);
}

async function refresh() {
  try {
    render(await api("/api/state"));
  } catch (e) {
    const pill = $("#status-pill");
    pill.className = "pill unknown";
    pill.textContent = "server unreachable";
  }
}

$("#transfer-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const btn = $("#transfer-btn");
  btn.disabled = true;
  try {
    const payload = {
      from: $("#from").value,
      to: $("#to").value,
      amount: parseFloat($("#amount").value),
    };
    const res = await api("/api/transfer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    toast(`✔ ${res.result.transactionId}: ${payload.from} → ${payload.to}  ${fmt(payload.amount)} USD (journal entry ${res.result.seq})`, "ok");
    await refresh();
  } catch (e) {
    toast(`✘ Transfer rejected: ${e.message}`, "err");
  } finally {
    btn.disabled = false;
  }
});

$("#seed-btn").addEventListener("click", async () => {
  try {
    await api("/api/seed", { method: "POST", body: "{}" });
    toast("✔ Sample financial data seeded", "ok");
    await refresh();
  } catch (e) {
    toast(`✘ ${e.message}`, "err");
  }
});

$("#tamper-btn").addEventListener("click", async () => {
  if (!confirm("Mutate ACC-001's balance directly in the table, bypassing the journal?\nThe dashboard will flip to TAMPERING DETECTED (reset with scripts/10_cleanup.sh && ./demo.sh).")) return;
  try {
    const res = await api("/api/tamper", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account: "ACC-001", balance: 999999.0 }),
    });
    toast(`⚡ ${res.warning}`, "err");
    await refresh();
  } catch (e) {
    toast(`✘ ${e.message}`, "err");
  }
});

refresh();
setInterval(refresh, 1200);
