const $ = selector => document.querySelector(selector);
const token = document.body.dataset.token;
let state;
let pending = false;
let currentPreview = null;

const esc = value => String(value ?? "").replace(/[&<>"']/g, character => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[character]);
const requestId = () => crypto.randomUUID();

async function api(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-Review-Token": token},
    body: JSON.stringify(body || {}),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || response.statusText);
  return payload;
}

function notice(text, bad = false) {
  $("#notice").textContent = text;
  $("#notice").className = bad ? "error" : "success";
}

function writable() {
  return Boolean(state && !state.read_only && (!state.session || state.session.status === "active"));
}

function syncControls() {
  document.body.classList.toggle("busy", pending);
  document.querySelectorAll("[data-write]").forEach(button => {
    button.disabled = pending || !writable() || button.dataset.ineligible === "true";
  });
  $("#validate").disabled = pending;
  $("#cancelPreview").disabled = pending;
  $("#confirm").disabled = pending || !currentPreview || !writable();
  $("#dialogPending").textContent = pending ? "Выполняется…" : "";
}

function selection() {
  return {
    sides: {
      left: $("#leftGap").checked
        ? {gap: true, reason: $("#leftReason").value}
        : {count: Number($("#leftCount").value)},
      right: $("#rightGap").checked
        ? {gap: true, reason: $("#rightReason").value}
        : {count: Number($("#rightCount").value)},
    },
    note: $("#note").value,
  };
}

function marks(side, id) {
  if (!state.bootstrap) return "";
  const found = state.bootstrap.items
    .filter(item => item[side]?.paragraphs?.includes(id))
    .map(item => item.trilingual_unit_id);
  return found.length
    ? `<div class="bootstrapMark">bootstrap: ${esc(found.join(", "))}</div>`
    : "";
}

function bindUnitActions() {
  document.querySelectorAll("[data-split]").forEach(button => {
    button.onclick = () => {
      const left = Number(prompt("Left first-child count?", "1"));
      const right = Number(prompt("Right first-child count?", "1"));
      openPreview(
        "/api/correction/split/preview",
        {unit_id: button.dataset.split, first_counts: {left, right}},
        "Split — old → two children",
        "/api/correction/split/confirm",
      );
    };
  });
  document.querySelectorAll("[data-merge]").forEach(button => {
    button.onclick = () => openPreview(
      "/api/correction/merge/preview",
      {unit_id: button.dataset.merge},
      "Merge — two units → one",
      "/api/correction/merge/confirm",
    );
  });
  document.querySelectorAll("[data-edit-disposition]").forEach(button => {
    button.onclick = () => {
      const reason = prompt("Reason?", button.dataset.reason || "metadata");
      if (!reason) return;
      const note = prompt("Note?", button.dataset.note || "");
      openPreview(
        "/api/correction/disposition/preview",
        {paragraph_id: button.dataset.editDisposition, reason, note},
        "Disposition — old → new",
        "/api/correction/disposition/confirm",
      );
    };
  });
}

function render() {
  $("#identity").textContent = `${state.work_id} ch ${state.chapter} · ${state.direction} · ${state.status} · ${state.pair_key} · revision ${state.session?.revision ?? "—"}`;
  $("#progress").textContent = `left ${state.progress.left.consumed}/${state.progress.left.total} · right ${state.progress.right.consumed}/${state.progress.right.total}`;
  $("#sources").innerHTML = ["left", "right"].map(side => {
    const source = state.sources[side];
    return `<div class="source"><h2>${side}: ${esc(source.source_id)} (${source.language})</h2>${source.paragraphs.map(paragraph => `<div class="para ${paragraph.index <= state.cursors[side] ? "consumed" : ""} ${paragraph.index === state.cursors[side] + 1 ? "current" : ""}"><b>p${paragraph.index}</b> ${esc(paragraph.normalized_text)}${marks(side, paragraph.id)}</div>`).join("")}</div>`;
  }).join("");
  $("#units").innerHTML = state.alignment_units.map((unit, index) => `<div class="unit"><b>${esc(unit.id)}</b> <span class="unitActions"><button data-write data-split="${esc(unit.id)}">Split</button><button data-write data-merge="${esc(unit.id)}" data-ineligible="${index === state.alignment_units.length - 1}">Merge next</button></span><div class="unitGrid"><div class="cell">${esc(JSON.stringify(unit.left, null, 2))}</div><div class="cell">${esc(JSON.stringify(unit.right, null, 2))}</div></div></div>`).join("") || "Нет решений.";
  $("#dispositions").innerHTML = state.paragraph_dispositions.map(item => `<div>${esc(item.paragraph_id)} · ${esc(item.reason)} <button data-write data-edit-disposition="${esc(item.paragraph_id)}" data-reason="${esc(item.reason)}" data-note="${esc(item.note || "")}">Edit</button></div>`).join("") || "Нет dispositions.";
  $("#rollbackCount").max = state.alignment_units.length;
  $("#rollbackCount").value = state.alignment_units.length;
  if (state.bootstrap) {
    $("#bootstrap").hidden = false;
    $("#bootstrap").innerHTML = `<h2>${esc(state.bootstrap.label)}</h2><p>Только read-only помощь. Ничего не подтверждается автоматически.</p>${state.bootstrap.items.filter(item => item.third_language_context?.paragraph_text?.length).map(item => `<details><summary>${esc(item.trilingual_unit_id)} · third-language context</summary>${item.third_language_context.paragraph_text.map(paragraph => `<p>${esc(paragraph.normalized_text)}</p>`).join("")}</details>`).join("")}`;
  }
  bindUnitActions();
  syncControls();
}

function previewSummary(payload) {
  const visible = {...payload};
  delete visible.preview_token;
  delete visible.session_revision;
  delete visible.candidate_working_gold_sha256;
  return JSON.stringify(visible, null, 2);
}

async function openPreview(path, payload, title, confirmPath = "/api/confirm") {
  if (pending || !writable()) return;
  currentPreview = null;
  $("#previewTitle").textContent = title;
  $("#previewBody").textContent = "";
  $("#dialogError").textContent = "";
  if (!$("#previewDialog").open) $("#previewDialog").showModal();
  pending = true;
  syncControls();
  try {
    const preview = await api(path, {
      ...payload,
      expected_revision: state.session?.revision ?? 0,
    });
    currentPreview = {
      token: preview.preview_token,
      revision: preview.session_revision,
      requestId: requestId(),
      confirmPath,
    };
    $("#previewBody").textContent = previewSummary(preview);
  } catch (error) {
    $("#dialogError").textContent = error.message;
  } finally {
    pending = false;
    syncControls();
  }
}

async function directMutation(path, payload, successMessage) {
  if (pending || !writable()) return;
  pending = true;
  syncControls();
  try {
    state = await api(path, {
      ...payload,
      expected_revision: state.session?.revision ?? 0,
      request_id: requestId(),
    });
    render();
    notice(successMessage);
  } catch (error) {
    notice(error.message, true);
  } finally {
    pending = false;
    syncControls();
  }
}

async function load() {
  const response = await fetch("/api/session");
  state = await response.json();
  if (!response.ok) throw new Error(state.error || response.statusText);
  render();
}

$("#preview").onclick = () => openPreview(
  "/api/preview", selection(), "AlignmentUnit — exact selection",
);
$("#disposition").onclick = () => openPreview(
  "/api/disposition/preview",
  {side: $("#dispSide").value, reason: $("#dispReason").value, note: $("#dispNote").value},
  "ParagraphDisposition — exact current Paragraph",
  "/api/disposition/confirm",
);
$("#rollback").onclick = () => openPreview(
  "/api/rollback/preview",
  {keep_units: Number($("#rollbackCount").value)},
  "Rollback — decisions to remove",
  "/api/rollback/confirm",
);

$("#cancelPreview").onclick = () => {
  if (pending) return;
  currentPreview = null;
  $("#previewDialog").close();
};
$("#previewDialog").addEventListener("cancel", event => {
  if (pending) event.preventDefault();
  else currentPreview = null;
});
$("#previewDialog").addEventListener("keydown", event => {
  if (event.key === "Enter") event.preventDefault();
});
$("#confirm").onclick = async () => {
  if (pending || !currentPreview) return;
  pending = true;
  $("#dialogError").textContent = "";
  syncControls();
  try {
    state = await api(currentPreview.confirmPath, {
      preview_token: currentPreview.token,
      request_id: currentPreview.requestId,
      expected_revision: currentPreview.revision,
    });
    currentPreview = null;
    $("#previewDialog").close();
    render();
    notice("Точное preview-решение атомарно сохранено только в Review Session.");
  } catch (error) {
    $("#dialogError").textContent = error.message;
  } finally {
    pending = false;
    syncControls();
  }
};

$("#undo").onclick = () => directMutation("/api/undo", {}, "Последняя journalled операция отменена.");
$("#useBootstrap").onclick = async () => {
  if (pending) return;
  pending = true;
  syncControls();
  try {
    const payload = await api("/api/bootstrap/use-remaining");
    for (const side of ["left", "right"]) {
      if (payload.sides[side].gap) {
        $("#" + side + "Gap").checked = true;
        $("#" + side + "Reason").value = payload.sides[side].reason;
      } else {
        $("#" + side + "Gap").checked = false;
        $("#" + side + "Count").value = payload.sides[side].count;
      }
    }
    notice("Bootstrap заполнил controls; Preview и Confirm всё ещё обязательны.");
  } catch (error) {
    notice(error.message, true);
  } finally {
    pending = false;
    syncControls();
  }
};
$("#validate").onclick = async () => {
  if (pending) return;
  pending = true;
  syncControls();
  try {
    const result = await api("/api/validate");
    notice("Full Pairwise Gold validation: PASS · " + JSON.stringify(result.summary));
  } catch (error) {
    notice(error.message, true);
  } finally {
    pending = false;
    syncControls();
  }
};
$("#publish").onclick = () => {
  if (!confirm("Опубликовать Review Session в tracked Pairwise Gold как status=draft?")) return;
  directMutation("/api/publish", {confirm: true}, "Pairwise Gold draft опубликован. Benchmark ещё не confirmed.");
};
$("#discard").onclick = () => {
  if (!confirm("Отбросить только эту Pairwise Review Session?")) return;
  directMutation("/api/discard", {}, "Session архивирована как discarded; Pairwise Gold не изменён.");
};

load().catch(error => notice(error.message, true));
