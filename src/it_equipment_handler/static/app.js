const PRESETS = [
  {
    label: "Approve Laptop (E101)",
    employeeId: "E101",
    request: "My laptop is 4 years old and slow, can I get a standard replacement?",
  },
  {
    label: "Deny Contractor (E103)",
    employeeId: "E103",
    request: "I would like a new Dell laptop for my work.",
  },
  {
    label: "Budget Escalation (E107)",
    employeeId: "E107",
    request: "I work from home and need a Dell 27-inch monitor.",
  },
  {
    label: "Cracked Screen (E106)",
    employeeId: "E106",
    request: "My laptop is 4 years old and the screen cracked yesterday, I need a replacement.",
  },
  {
    label: "Ergonomic Chair (E108)",
    employeeId: "E108",
    request: "I have been having back pain and need an ergonomic chair for my home office.",
  },
  {
    label: "Stolen Laptop (E109)",
    employeeId: "E109",
    request: "My laptop was stolen from my car yesterday, I need an urgent replacement.",
  },
  {
    label: "Unknown ID (E999)",
    employeeId: "E999",
    request: "Can I get a new monitor?",
  },
];

let appState = null;

function showToast(message) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.classList.remove("hidden");
  clearTimeout(window._toastTimer);
  window._toastTimer = setTimeout(() => toast.classList.add("hidden"), 2800);
}

async function apiFetch(url, options = {}) {
  const resp = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await resp.json();
  if (!resp.ok) {
    throw new Error(data.error || `Request failed (${resp.status})`);
  }
  return data;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatMoney(val) {
  return `$${Number(val || 0).toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
}

function renderEmployeeSelector() {
  const select = document.getElementById("employee-select");
  const prev = select.value;
  const options = appState.employees
    .map(
      (e) =>
        `<option value="${escapeHtml(e.employee_id)}">${escapeHtml(e.employee_id)} — ${escapeHtml(e.name)} (${escapeHtml(e.role)}, ${escapeHtml(e.work_mode)}, ${escapeHtml(e.department)})</option>`
    )
    .concat('<option value="E999">E999 — Nonexistent Employee (Test Denial)</option>');
  select.innerHTML = options.join("");
  if (prev) select.value = prev;
  updateEmployeeContextStrip();
}

function updateEmployeeContextStrip() {
  const empId = document.getElementById("employee-select").value;
  const strip = document.getElementById("employee-context-strip");
  const emp = appState.employees.find((e) => e.employee_id === empId);
  if (!emp) {
    strip.innerHTML = `<span class="ctx-tag">Unregistered ID — expected DENY</span>`;
    return;
  }
  const dept = appState.departments.find((d) => d.department === emp.department);
  const rem = dept ? formatMoney(dept.remaining_budget_usd) : "N/A";
  strip.innerHTML = [
    `<span class="ctx-tag">role: ${escapeHtml(emp.role)}</span>`,
    `<span class="ctx-tag">mode: ${escapeHtml(emp.work_mode)}</span>`,
    `<span class="ctx-tag">status: ${escapeHtml(emp.status)}</span>`,
    `<span class="ctx-tag">mgr: ${escapeHtml(emp.manager_id || "none")}</span>`,
    `<span class="ctx-tag">${escapeHtml(emp.department)} left: ${rem}</span>`,
  ].join("");
}

function renderPresets() {
  const container = document.getElementById("preset-chips");
  container.innerHTML = PRESETS.map(
    (p, idx) => `<button class="preset-chip" data-preset="${idx}">${escapeHtml(p.label)}</button>`
  ).join("");
  container.querySelectorAll(".preset-chip").forEach((btn) => {
    btn.addEventListener("click", () => {
      applyPreset(Number(btn.dataset.preset));
    });
  });

  const dropdown = document.getElementById("preset-select");
  dropdown.innerHTML =
    '<option value="">Demo scenarios…</option>' +
    PRESETS.map((p, idx) => `<option value="${idx}">${escapeHtml(p.label)}</option>`).join("");
  dropdown.addEventListener("change", () => {
    if (dropdown.value !== "") {
      applyPreset(Number(dropdown.value));
      dropdown.value = "";
    }
  });
}

function applyPreset(index) {
  const preset = PRESETS[index];
  if (!preset) return;
  document.getElementById("employee-select").value = preset.employeeId;
  const input = document.getElementById("request-input");
  input.value = preset.request;
  autoResizeTextarea(input);
  input.focus();
  updateEmployeeContextStrip();
}

function autoResizeTextarea(el) {
  el.style.height = "auto";
  el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
}

function renderPolicies() {
  const tbody = document.getElementById("policies-tbody");
  tbody.innerHTML = appState.structured_policies
    .map(
      (p) => `
      <tr data-policy-id="${escapeHtml(p.policy_id)}">
        <td class="mono">${escapeHtml(p.policy_id)}</td>
        <td>${escapeHtml(p.role)}</td>
        <td class="mono">${escapeHtml(p.work_mode)}</td>
        <td class="mono">${escapeHtml(p.category)}</td>
        <td><input type="checkbox" class="pol-allowed" ${p.allowed ? "checked" : ""} /></td>
        <td><input type="number" class="cell-input pol-qty" value="${p.max_active_qty}" min="0" /></td>
        <td><input type="number" class="cell-input pol-refresh" value="${p.refresh_months}" min="0" /></td>
        <td><input type="number" class="cell-input pol-cap" value="${p.max_auto_cost_usd}" min="0" step="50" /></td>
        <td><input type="number" class="cell-input pol-tenure" value="${p.min_tenure_months}" min="0" /></td>
        <td><button class="btn-ghost btn-sm btn-save-policy">Save</button></td>
      </tr>`
    )
    .join("");

  tbody.querySelectorAll(".btn-save-policy").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const row = btn.closest("tr");
      const id = row.dataset.policyId;
      try {
        await apiFetch(`/api/policies/${encodeURIComponent(id)}`, {
          method: "PUT",
          body: JSON.stringify({
            allowed: row.querySelector(".pol-allowed").checked,
            max_active_qty: Number(row.querySelector(".pol-qty").value),
            refresh_months: Number(row.querySelector(".pol-refresh").value),
            max_auto_cost_usd: Number(row.querySelector(".pol-cap").value),
            min_tenure_months: Number(row.querySelector(".pol-tenure").value),
          }),
        });
        showToast(`Saved policy ${id}`);
        await refreshState();
      } catch (err) {
        showToast(`Error: ${err.message}`);
      }
    });
  });

  const clausesGrid = document.getElementById("clauses-grid");
  clausesGrid.innerHTML = appState.policy_clauses
    .map(
      (c) => `
      <div class="clause-card" data-clause-id="${escapeHtml(c.clause_id)}">
        <div class="clause-head">
          <span class="mono">${escapeHtml(c.clause_id)} · ${escapeHtml(c.topic)}</span>
          <span class="pill-tag">${escapeHtml(c.default_reviewer_role || "general")}</span>
        </div>
        <textarea class="input-control clause-text" rows="3">${escapeHtml(c.rule_text)}</textarea>
        <div style="display:flex;justify-content:flex-end;">
          <button class="btn-ghost btn-sm btn-save-clause">Save Clause</button>
        </div>
      </div>`
    )
    .join("");

  clausesGrid.querySelectorAll(".btn-save-clause").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const card = btn.closest(".clause-card");
      const id = card.dataset.clauseId;
      const text = card.querySelector(".clause-text").value;
      try {
        await apiFetch(`/api/clauses/${encodeURIComponent(id)}`, {
          method: "PUT",
          body: JSON.stringify({ rule_text: text }),
        });
        showToast(`Updated clause ${id}`);
        await refreshState();
      } catch (err) {
        showToast(`Error: ${err.message}`);
      }
    });
  });
}

function renderBudgetsAndCatalog() {
  const bGrid = document.getElementById("budgets-grid");
  bGrid.innerHTML = appState.departments
    .map((d) => {
      const pct = d.annual_budget_usd > 0 ? Math.min(100, (d.annual_spend_usd / d.annual_budget_usd) * 100) : 100;
      return `
      <div class="budget-card" data-dept="${escapeHtml(d.department)}">
        <div class="budget-top">
          <span class="budget-name">${escapeHtml(d.department)}</span>
          <span class="mono subtle-label">${pct.toFixed(0)}% used</span>
        </div>
        <div class="mono" style="font-size:12.5px;color:var(--text-secondary);">
          Spent ${formatMoney(d.annual_spend_usd)} / ${formatMoney(d.annual_budget_usd)} ·
          <strong style="color:${d.remaining_budget_usd < 600 ? "var(--danger)" : "var(--ok)"}">${formatMoney(d.remaining_budget_usd)} left</strong>
        </div>
        <div class="progress-track">
          <div class="progress-fill ${pct >= 80 ? "high" : ""}" style="width:${pct}%"></div>
        </div>
        <div class="inline-edit-row">
          <input type="number" class="input-control dept-budget-input" value="${d.annual_budget_usd}" min="0" step="500" />
          <button class="btn-ghost btn-sm btn-save-budget">Update</button>
        </div>
      </div>`;
    })
    .join("");

  bGrid.querySelectorAll(".btn-save-budget").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const card = btn.closest(".budget-card");
      const dept = card.dataset.dept;
      const val = Number(card.querySelector(".dept-budget-input").value);
      try {
        await apiFetch(`/api/departments/${encodeURIComponent(dept)}/budget`, {
          method: "PUT",
          body: JSON.stringify({ annual_budget_usd: val }),
        });
        showToast(`Updated ${dept} budget to ${formatMoney(val)}`);
        await refreshState();
      } catch (err) {
        showToast(`Error: ${err.message}`);
      }
    });
  });

  const cGrid = document.getElementById("catalog-grid");
  cGrid.innerHTML = appState.resources
    .map(
      (r) => `
      <div class="catalog-card" data-res="${escapeHtml(r.resource_id)}">
        <div class="budget-top">
          <span class="budget-name">${escapeHtml(r.name)}</span>
          <span class="pill-tag">${escapeHtml(r.tier)}</span>
        </div>
        <div class="mono subtle-label">${escapeHtml(r.resource_id)} · ${escapeHtml(r.category)}${r.requires_category ? ` · requires ${escapeHtml(r.requires_category)}` : ""}</div>
        <div class="inline-edit-row">
          <input type="number" class="input-control res-cost-input" value="${r.unit_cost}" min="0" step="25" />
          <button class="btn-ghost btn-sm btn-save-res">Save Cost</button>
        </div>
      </div>`
    )
    .join("");

  cGrid.querySelectorAll(".btn-save-res").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const card = btn.closest(".catalog-card");
      const resId = card.dataset.res;
      const cost = Number(card.querySelector(".res-cost-input").value);
      try {
        await apiFetch(`/api/resources/${encodeURIComponent(resId)}`, {
          method: "PUT",
          body: JSON.stringify({ unit_cost: cost }),
        });
        showToast(`Updated ${resId} cost to ${formatMoney(cost)}`);
        await refreshState();
      } catch (err) {
        showToast(`Error: ${err.message}`);
      }
    });
  });
}

function renderDirectoryAndLedgers() {
  const empBody = document.getElementById("employees-tbody");
  empBody.innerHTML = appState.employees
    .map(
      (e) => `
      <tr data-emp-id="${escapeHtml(e.employee_id)}">
        <td class="mono">${escapeHtml(e.employee_id)}</td>
        <td>${escapeHtml(e.name)}</td>
        <td class="mono">${escapeHtml(e.role)}</td>
        <td>${escapeHtml(e.department)}</td>
        <td class="mono">${escapeHtml(e.manager_id || "—")}</td>
        <td>
          <select class="cell-select emp-mode">
            ${["wfh", "hybrid", "onsite"].map((m) => `<option value="${m}" ${e.work_mode === m ? "selected" : ""}>${m}</option>`).join("")}
          </select>
        </td>
        <td>
          <select class="cell-select emp-status">
            ${["active", "probation", "notice_period", "offboarding"].map((s) => `<option value="${s}" ${e.status === s ? "selected" : ""}>${s}</option>`).join("")}
          </select>
        </td>
        <td><button class="btn-ghost btn-sm btn-save-emp">Save</button></td>
      </tr>`
    )
    .join("");

  empBody.querySelectorAll(".btn-save-emp").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const row = btn.closest("tr");
      const id = row.dataset.empId;
      try {
        await apiFetch(`/api/employees/${encodeURIComponent(id)}`, {
          method: "PUT",
          body: JSON.stringify({
            work_mode: row.querySelector(".emp-mode").value,
            status: row.querySelector(".emp-status").value,
          }),
        });
        showToast(`Updated employee ${id}`);
        await refreshState();
      } catch (err) {
        showToast(`Error: ${err.message}`);
      }
    });
  });

  document.getElementById("count-tickets").textContent = appState.tickets.length;
  document.getElementById("tickets-list").innerHTML =
    appState.tickets
      .map(
        (t) => `
        <div class="ledger-item">
          <div class="ledger-top">
            <span class="mono">${escapeHtml(t.ticket_id)} · ${escapeHtml(t.employee_id)} → Reviewer ${escapeHtml(t.reviewer_employee_id)}</span>
            <span class="decision-badge ESCALATED">${escapeHtml(t.status.toUpperCase())}</span>
          </div>
          <div style="font-size:13px;margin-bottom:4px;">${escapeHtml(t.request_text)}</div>
          <div class="subtle-label">${escapeHtml(t.reason)}</div>
        </div>`
      )
      .join("") || '<p class="subtle-label">No tickets recorded.</p>';

  document.getElementById("count-logs").textContent = appState.logs.length;
  document.getElementById("logs-list").innerHTML =
    appState.logs
      .map(
        (l) => `
        <div class="ledger-item">
          <div class="ledger-top">
            <span class="mono">${escapeHtml(l.log_id)} · ${escapeHtml(l.employee_id)}</span>
            <span class="decision-badge ${escapeHtml(l.decision)}">${escapeHtml(l.decision)}</span>
          </div>
          <div style="font-size:13px;margin-bottom:4px;">${escapeHtml(l.request_text)}</div>
          <div class="subtle-label">${escapeHtml(l.reason)}</div>
        </div>`
      )
      .join("") || '<p class="subtle-label">No finalized decisions logged yet.</p>';

  document.getElementById("count-dispatches").textContent = appState.dispatches.length;
  document.getElementById("dispatches-tbody").innerHTML = appState.dispatches
    .map(
      (d) => `
      <tr>
        <td class="mono">${escapeHtml(d.dispatch_id)}</td>
        <td class="mono">${escapeHtml(d.employee_id)}</td>
        <td>${escapeHtml(d.resource_name)}</td>
        <td class="mono">${escapeHtml(d.serial_id)}</td>
        <td class="mono">${formatMoney(d.unit_cost)}</td>
        <td class="mono">${escapeHtml(d.dispatched_at)}</td>
        <td class="mono">${escapeHtml(d.condition_status)}</td>
      </tr>`
    )
    .join("");
}

function scrollChatToBottom() {
  const wrap = document.getElementById("chat-thread-wrap");
  wrap.scrollTop = wrap.scrollHeight;
}

function appendUserMessage(empId, reqText) {
  document.getElementById("chat-empty").classList.add("hidden");
  document.getElementById("chat-messages").classList.remove("hidden");
  document.getElementById("btn-clear-chat").classList.remove("hidden");

  const emp = appState.employees.find((e) => e.employee_id === empId);
  const label = emp ? `${emp.employee_id} · ${emp.name} (${emp.role})` : `${empId} · Unregistered`;

  const row = document.createElement("div");
  row.className = "msg-user-row";
  row.innerHTML = `
    <div class="msg-user-bubble">
      <div class="msg-user-meta">${escapeHtml(label)}</div>
      <p class="msg-user-text">${escapeHtml(reqText)}</p>
    </div>`;
  document.getElementById("chat-messages").appendChild(row);
  scrollChatToBottom();
}

function appendThinkingPlaceholder() {
  const row = document.createElement("div");
  row.className = "msg-agent-row";
  row.innerHTML = `
    <div class="msg-agent-avatar">A</div>
    <div class="msg-agent-body">
      <div class="msg-agent-head">
        <span class="msg-agent-name">Aegis IT Agent</span>
        <span class="decision-badge neutral">INVESTIGATING</span>
      </div>
      <div class="msg-thinking">
        <span class="msg-thinking-dot"></span>
        <span>Querying MCP tools, checking policies &amp; budgets, and reflecting…</span>
      </div>
    </div>`;
  document.getElementById("chat-messages").appendChild(row);
  scrollChatToBottom();
  return row;
}

function populateAgentMessage(row, res) {
  const traceHtml = res.trace
    .map((step, i) => {
      let prettyObs = step.observation;
      try {
        prettyObs = JSON.stringify(JSON.parse(step.observation), null, 2);
      } catch (_) {}
      return `
      <div class="trace-step">
        <div class="step-top">
          <span class="step-num">STEP ${i + 1}</span>
          <span class="step-action">${escapeHtml(step.action)}(${escapeHtml(JSON.stringify(step.action_input))})</span>
        </div>
        <p class="step-thought">${escapeHtml(step.thought)}</p>
        <details class="step-obs">
          <summary>Inspect Tool Observation</summary>
          <pre>${escapeHtml(prettyObs)}</pre>
        </details>
      </div>`;
    })
    .join("");

  row.innerHTML = `
    <div class="msg-agent-avatar">A</div>
    <div class="msg-agent-body">
      <div class="msg-agent-head">
        <span class="msg-agent-name">Aegis IT Agent</span>
        <span class="decision-badge ${escapeHtml(res.decision)}">${escapeHtml(res.decision)}</span>
      </div>
      <p class="msg-agent-response">${escapeHtml(res.final_response)}</p>
      <div class="msg-agent-reason ${escapeHtml(res.decision)}">${escapeHtml(res.reason)}</div>

      <div class="msg-details-stack">
        <div class="telemetry-grid" style="margin-bottom:0;">
          <div class="metric-card">
            <span class="metric-label">LLM Calls</span>
            <span class="metric-value mono">${escapeHtml(res.telemetry.llm_calls)}</span>
          </div>
          <div class="metric-card">
            <span class="metric-label">Tokens (In / Out)</span>
            <span class="metric-value mono">${escapeHtml(res.telemetry.input_tokens)} / ${escapeHtml(res.telemetry.output_tokens)}</span>
          </div>
          <div class="metric-card">
            <span class="metric-label">Latency</span>
            <span class="metric-value mono">${(res.telemetry.latency_ms / 1000).toFixed(2)}s</span>
          </div>
          <div class="metric-card accent">
            <span class="metric-label">Est. Cost</span>
            <span class="metric-value mono">$${Number(res.telemetry.estimated_cost_usd).toFixed(5)}</span>
          </div>
        </div>

        <div class="reflection-card" style="margin-bottom:0;">
          <div class="reflection-head">
            <span class="section-kicker">REFLECTION SELF-CHECK</span>
            <span class="pill-tag">${escapeHtml(res.reflection.status || "CONFIRMED")}</span>
          </div>
          <p class="reflection-text">${escapeHtml(res.reflection.critique || "")}</p>
        </div>

        <div class="trace-section">
          <div class="section-kicker">REACT TRACE (THOUGHT → ACTION → OBSERVATION)</div>
          <div class="timeline">${traceHtml}</div>
        </div>
      </div>
    </div>`;
  scrollChatToBottom();
}

async function refreshState() {
  appState = await apiFetch("/api/state");
  document.getElementById("meta-year").textContent = `FY ${appState.year}`;
  document.getElementById("badge-policies").textContent = appState.structured_policies.length;
  document.getElementById("badge-depts").textContent = appState.departments.length;
  document.getElementById("badge-tickets").textContent = appState.tickets.length;

  renderEmployeeSelector();
  renderPolicies();
  renderBudgetsAndCatalog();
  renderDirectoryAndLedgers();
}

function setupNavigation() {
  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".nav-item").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById(`view-${btn.dataset.view}`).classList.add("active");
    });
  });
}

function setupActions() {
  document.getElementById("employee-select").addEventListener("change", updateEmployeeContextStrip);

  const reqInput = document.getElementById("request-input");
  reqInput.addEventListener("input", () => autoResizeTextarea(reqInput));
  reqInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      document.getElementById("btn-run-agent").click();
    }
  });

  document.getElementById("btn-clear-chat").addEventListener("click", () => {
    document.getElementById("chat-messages").innerHTML = "";
    document.getElementById("chat-messages").classList.add("hidden");
    document.getElementById("chat-empty").classList.remove("hidden");
    document.getElementById("btn-clear-chat").classList.add("hidden");
  });

  document.getElementById("btn-run-agent").addEventListener("click", async () => {
    const btn = document.getElementById("btn-run-agent");
    const spinner = document.getElementById("run-spinner");
    const arrow = document.getElementById("send-arrow");
    const timerEl = document.getElementById("run-timer");
    const empId = document.getElementById("employee-select").value;
    const reqText = reqInput.value.trim();

    if (!reqText) {
      showToast("Please enter an equipment request.");
      return;
    }

    appendUserMessage(empId, reqText);
    reqInput.value = "";
    autoResizeTextarea(reqInput);
    const agentRow = appendThinkingPlaceholder();

    btn.disabled = true;
    spinner.classList.remove("hidden");
    arrow.classList.add("hidden");
    const start = performance.now();
    const interval = setInterval(() => {
      timerEl.textContent = `${((performance.now() - start) / 1000).toFixed(1)}s…`;
    }, 100);

    try {
      const result = await apiFetch("/api/requests/run", {
        method: "POST",
        body: JSON.stringify({ employee_id: empId, request_text: reqText }),
      });
      populateAgentMessage(agentRow, result);
      showToast(`Completed: ${result.decision}`);
      await refreshState();
    } catch (err) {
      agentRow.querySelector(".msg-agent-body").innerHTML = `
        <div class="msg-agent-head">
          <span class="msg-agent-name">Aegis IT Agent</span>
          <span class="decision-badge DENIED">ERROR</span>
        </div>
        <p class="msg-agent-response">${escapeHtml(err.message)}</p>`;
      showToast(`Agent error: ${err.message}`);
    } finally {
      clearInterval(interval);
      timerEl.textContent = "";
      btn.disabled = false;
      spinner.classList.add("hidden");
      arrow.classList.remove("hidden");
    }
  });

  document.getElementById("btn-toggle-new-clause").addEventListener("click", () => {
    document.getElementById("new-clause-drawer").classList.toggle("hidden");
  });
  document.getElementById("btn-close-clause").addEventListener("click", () => {
    document.getElementById("new-clause-drawer").classList.add("hidden");
  });
  document.getElementById("btn-create-clause").addEventListener("click", async () => {
    const topic = document.getElementById("new-clause-topic").value.trim();
    const ruleText = document.getElementById("new-clause-text").value.trim();
    const reviewer = document.getElementById("new-clause-reviewer").value || null;
    try {
      await apiFetch("/api/clauses", {
        method: "POST",
        body: JSON.stringify({ topic, rule_text: ruleText, default_reviewer_role: reviewer }),
      });
      document.getElementById("new-clause-topic").value = "";
      document.getElementById("new-clause-text").value = "";
      document.getElementById("new-clause-drawer").classList.add("hidden");
      showToast("Added new policy clause");
      await refreshState();
    } catch (err) {
      showToast(`Error: ${err.message}`);
    }
  });

  document.getElementById("btn-reset-db").addEventListener("click", async () => {
    try {
      await apiFetch("/api/db/reset", { method: "POST" });
      showToast("Demo database reset to clean defaults");
      await refreshState();
    } catch (err) {
      showToast(`Error: ${err.message}`);
    }
  });
}

(async function init() {
  setupNavigation();
  renderPresets();
  setupActions();
  await refreshState();
  document.getElementById("employee-select").value = PRESETS[0].employeeId;
  updateEmployeeContextStrip();
})();
