const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;
const nativeWindow = window.__TAURI__.window.getCurrentWindow();
const strings = await fetch("./strings.json").then((response) =>
  response.json(),
);
const defaults = await fetch("./defaults.json").then((response) =>
  response.json(),
);
const $ = (id) => document.getElementById(id);
const state = {
  language: "zh",
  mode: "bib",
  name: "",
  text: "",
  original: "",
  before: "",
  history: [],
  pointer: 0,
  comparison: [],
  issues: [],
  reports: [],
  arxiv: [],
  all: false,
  running: false,
  paused: false,
  runId: 0,
  generation: 0,
  tableInput: "",
  tableResult: null,
  tableConfig: null,
  tableOptions: {
    amp_style: "space",
    blank_lines_around_rules: true,
    row_indent: 0,
  },
  settings: structuredClone(defaults),
  drafts: {},
};
try {
  const saved = JSON.parse(
    localStorage.getItem("hx-bib-native-settings") || "{}",
  );
  for (const key of Object.keys(defaults)) {
    if (typeof defaults[key] === "object" && !Array.isArray(defaults[key])) {
      Object.assign(state.settings[key], saved[key] || {});
    }
  }
  state.language = ["zh", "en"].includes(saved.lang) ? saved.lang : "zh";
} catch {
  /* A malformed preference must not prevent startup. */
}
const t = (key) => strings[state.language][key] || key;
const el = (tag, className = "", text = "") => {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = text;
  return node;
};
function toast(message) {
  $("toast").textContent = message;
  $("toast").hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => {
    $("toast").hidden = true;
  }, 3500);
}
function action(node, callback) {
  node.addEventListener("click", async () => {
    try {
      await callback();
    } catch (error) {
      toast(t(String(error)));
    }
  });
}
const process = (request) => invoke("process", { request });
function persist() {
  state.settings.lang = state.language;
  localStorage.setItem(
    "hx-bib-native-settings",
    JSON.stringify(state.settings),
  );
}
function field(parent, label, value, change, choices = null) {
  const wrapper = el("label", typeof value === "boolean" ? "toggle" : "");
  let input;
  if (choices) {
    input = el("select");
    input.multiple = Array.isArray(value);
    for (const [key, name] of Object.entries(choices)) {
      const option = el("option", "", name);
      option.value = key;
      option.selected = Array.isArray(value)
        ? value.includes(key)
        : String(value) === key;
      input.append(option);
    }
  } else {
    input = el("input");
    input.type =
      typeof value === "boolean"
        ? "checkbox"
        : typeof value === "number"
          ? "number"
          : "text";
    if (input.type === "checkbox") input.checked = value;
    else input.value = value;
    if (input.type === "number") {
      input.min = "0";
      input.step = "any";
    }
  }
  input.setAttribute("aria-label", label);
  wrapper.append(input, el("span", "", label));
  if (input.type !== "checkbox") wrapper.prepend(wrapper.lastChild);
  input.addEventListener("change", async () => {
    const updated = input.multiple
      ? [...input.selectedOptions].map((option) => option.value)
      : input.type === "checkbox"
        ? input.checked
        : input.type === "number"
          ? Number(input.value)
          : input.value;
    try {
      await change(updated);
      persist();
    } catch (error) {
      toast(String(error));
    }
  });
  parent.append(wrapper);
  return input;
}
function setting(parent, group, key, label, choices) {
  return field(
    parent,
    t(label),
    state.settings[group][key],
    (value) => {
      state.settings[group][key] = value;
    },
    choices,
  );
}
function section(group, title) {
  const details = el("details");
  const summary = el("summary", "", t(title));
  details.append(summary);
  const options = el("div", "options");
  details.append(options);
  if (group in state.settings.enabled) {
    field(options, t("bib_include"), state.settings.enabled[group], (value) => {
      state.settings.enabled[group] = value;
      summary.textContent = t(title) + (value ? " · " + t("bib_enabled") : "");
    });
    if (state.settings.enabled[group])
      summary.textContent += " · " + t("bib_enabled");
  }
  details.open = state.drafts["open_" + group] || false;
  details.addEventListener("toggle", () => {
    state.drafts["open_" + group] = details.open;
  });
  $("bib-settings").append(details);
  return options;
}
function renderSettings() {
  $("bib-settings").replaceChildren();
  const clean = section("clean", "bib_cleanup");
  const fields = [
    "abstract",
    "keywords",
    "url",
    "doi",
    "isbn",
    "issn",
    "month",
    "note",
    "address",
    "editor",
    "publisher",
    "eprint",
    "volume",
    "number",
  ];
  setting(
    clean,
    "clean",
    "remove",
    "remove_fields",
    Object.fromEntries(fields.map((name) => [name, name])),
  );
  for (const key of ["custom_remove", "add_key", "add_value"]) {
    field(clean, t(key), state.drafts["clean_" + key] || "", (value) => {
      state.drafts["clean_" + key] = value;
    });
  }
  field(
    clean,
    t("overwrite"),
    state.drafts.clean_overwrite || false,
    (value) => {
      state.drafts.clean_overwrite = value;
    },
  );
  for (const [key, label] of [
    ["drop_empty", "drop_empty"],
    ["drop_others", "drop_others"],
    ["months", "months"],
    ["title_period", "title_period"],
    ["doi", "opt_doi"],
    ["pages", "opt_pages"],
    ["clean_urls", "opt_clean_urls"],
    ["dedupe", "dedupe"],
    ["dedupe_title", "opt_dedupe_title"],
    ["keep_last", "keep_last"],
  ])
    setting(clean, "clean", key, label);
  const format = section("format", "tab_format");
  setting(format, "format", "indent", "opt_indent", {
    0: "0",
    2: "2 spaces",
    4: "4 spaces",
    tab: "Tab",
    align: t("bib_indent_align"),
  });
  setting(format, "format", "field_order", "opt_order", {
    standard: t("order_standard"),
    alphabetical: t("order_alpha"),
    keep: t("order_keep"),
  });
  setting(
    format,
    "format",
    "sort",
    "opt_sort",
    Object.fromEntries(
      ["none", "key", "year", "type", "title"].map((key) => [
        key,
        t(key === "none" ? "sort_none" : "bib_sort_" + key),
      ]),
    ),
  );
  for (const key of ["align_equals", "trailing_comma", "drop_comments"])
    setting(format, "format", key, "opt_" + key);
  const normalize = section("normalize", "tab_normalize");
  setting(
    normalize,
    "normalize",
    "venue_style",
    "venue_style",
    Object.fromEntries(
      ["abbr", "full", "family", "keep"].map((key) => [
        key,
        t("style_" + key + "_short"),
      ]),
    ),
  );
  setting(normalize, "normalize", "arxiv_style", "arxiv_style", {
    journal: t("arxiv_journal_short"),
    misc: t("arxiv_misc_short"),
    keep: t("style_keep_short"),
  });
  for (const key of ["fix_types", "drop_arxiv_volume", "add_arxiv_url"])
    setting(normalize, "normalize", key, key);
  const check = section("check", "tab_check");
  for (const key of Object.keys(defaults.check))
    setting(check, "check", key, "check_opt_" + key);
  const venues = section("venues", "tab_venues");
  setting(
    venues,
    "venues",
    "preset_style",
    "venue_style",
    Object.fromEntries(
      ["abbr", "full", "family"].map((key) => [
        key,
        t("style_" + key + "_short"),
      ]),
    ),
  );
  const catalog = el("div", "catalog");
  const query = field(
    venues,
    t("search_venues"),
    state.drafts.venueQuery || "",
    async (value) => {
      state.drafts.venueQuery = value;
      await loadVenues();
    },
  );
  const kind = field(venues, t("venue_kind"), "ALL", loadVenues, {
    ALL: t("venue_all_kinds"),
    conference: t("venue_kind_conference"),
    journal: t("venue_kind_journal"),
  });
  const family = field(venues, t("family"), "", loadVenues);
  venues.append(catalog);
  async function loadVenues() {
    const matches = await process({ op: "venues", text: query.value });
    catalog.replaceChildren();
    for (const venue of matches
      .filter(
        (v) =>
          (kind.value === "ALL" || v.kind === kind.value) &&
          (!family.value ||
            v.family.toLowerCase().includes(family.value.toLowerCase())),
      )
      .slice(0, 8)) {
      const card = el("div", "venue");
      card.append(
        el("strong", "", venue.abbr + " · " + venue.family),
        el("div", "", venue.name),
      );
      for (const style of ["abbr", "full", "family"]) {
        const button = el("button", "", t("style_" + style + "_short"));
        action(button, async () => {
          const result = await process({
            op: "venue-match",
            text: venue.name,
            options: { style },
          });
          await invoke("copy_text", { text: result.rendered });
        });
        card.append(button);
      }
      catalog.append(card);
    }
  }
  loadVenues().catch((error) => toast(String(error)));
  const online = section("verify", "workbench_verify");
  field(online, t("only_keys"), state.drafts.verify_keys || "", (value) => {
    state.drafts.verify_keys = value;
  });
  setting(online, "verify", "delay", "delay");
  const workers = setting(online, "verify", "workers", "workers");
  workers.min = "1";
  workers.max = "8";
  workers.step = "1";
  setting(online, "verify", "use_cache", "use_cache");
  const verify = el("button", "", t("workbench_verify"));
  verify.id = "verify";
  action(verify, () => runOnline(false));
  const report = el("button", "", t("download_report"));
  action(report, async () => {
    const results = state.lastScope === "arxiv" ? state.arxiv : state.reports;
    if (!results.length) return;
    await invoke("save_text", {
      name: "references.report.html",
      text: await invoke("build_report", { results }),
    });
  });
  online.append(verify, report);
}
async function loadBib(text, name) {
  const generation = ++state.generation;
  const parsed = await process({ op: "parse", text });
  if (generation !== state.generation) return;
  if (
    !parsed.entries.length &&
    !parsed.strings.length &&
    !parsed.preamble.length
  )
    throw new Error(t("invalid_bib"));
  if (state.running) await invoke("control_run", { action: "stop" });
  state.runId++;
  $("progress").textContent = "";
  Object.assign(state, {
    name,
    text,
    original: text,
    before: text,
    history: [],
    pointer: 0,
    issues: [],
    reports: [],
    arxiv: [],
  });
  state.comparison = await process({ op: "diff", text, after: text });
  renderResults();
}
function renderResults() {
  $("filename").textContent =
    state.mode === "bib" ? state.name || "hx-bib" : "LaTeX";
  $("entries").textContent =
    state.mode === "bib" && state.name
      ? state.comparison.length + " " + t("entries")
      : "";
  $("stage").textContent =
    state.mode === "bib"
      ? state.pointer
        ? t("steps_staged").replace("{n}", state.pointer)
        : t("no_steps")
      : "";
  $("undo").disabled = state.mode !== "bib" || !state.pointer;
  $("redo").disabled =
    state.mode !== "bib" || state.pointer >= state.history.length;
  $("copy-all").hidden = state.mode !== "bib";
  $("save").hidden = state.mode !== "bib";
  $("copy-all").disabled = !state.name;
  $("save").disabled = !state.name;
  const counts = { modified: 0, added: 0, removed: 0 };
  for (const item of state.comparison)
    if (item.kind in counts) counts[item.kind]++;
  $("diff-count").textContent = t("bib_diff_count")
    .replace("{modified}", counts.modified)
    .replace("{added}", counts.added)
    .replace("{removed}", counts.removed);
  const container = $("diff");
  const scroll = container.scrollTop;
  container.replaceChildren();
  if (!state.name) {
    const intro = el("div", "empty");
    intro.append(el("p", "", t("import_safe")));
    const input = el("textarea", "editor");
    input.setAttribute("aria-label", t("paste_title"));
    input.placeholder = t("paste_placeholder");
    input.value = state.drafts.import_text || "";
    input.addEventListener("input", () => {
      state.drafts.import_text = input.value;
    });
    const importButton = el("button", "primary", t("paste_import"));
    action(importButton, () => loadBib(input.value, "pasted.bib"));
    const sample = el("button", "", t("try_example"));
    action(sample, () =>
      loadBib(
        "@article{demo2025,\n  title={An Example Reference.},\n  author={Doe, Jane and others},\n  journal={TBD},\n  year={2025},\n  abstract={Example text}\n}",
        "example.bib",
      ),
    );
    intro.append(input, importButton, sample);
    container.append(intro);
  } else {
    for (const comparison of state.comparison) {
      const issues = state.issues.filter(
        (issue) => issue.key === comparison.key,
      );
      const online = state.reports.find(
        (result) => result.entry.key === comparison.key,
      );
      const arxiv = state.arxiv.find(
        (result) => result.arxiv_id && result.arxiv_id === comparison.arxiv_id,
      );
      if (
        !state.all &&
        comparison.kind === "unchanged" &&
        !issues.length &&
        !arxiv &&
        (!online || online.verdict === "OK")
      )
        continue;
      container.append(diffCard(comparison, issues, online, arxiv));
    }
    if (!container.children.length)
      container.append(el("div", "empty", t("no_changes")));
  }
  container.scrollTop = scroll;
  const failed = state.arxiv.filter((result) => result.any_error);
  $("retry-failed").hidden = !failed.length;
  $("retry-failed").disabled = state.running;
  $("retry-failed").textContent = t("arxiv_retry_failed").replace(
    "{n}",
    failed.length,
  );
  $("apply").disabled = !state.name;
  $("arxiv").disabled = !state.name || state.running;
  if ($("verify")) $("verify").disabled = !state.name || state.running;
}
function diffCard(
  item,
  issues = [],
  online = null,
  arxiv = null,
  showAll = state.all,
) {
  const card = el("article");
  const head = el("div", "entry-head");
  head.append(
    el("code", "", item.key),
    el("span", "badge " + item.kind, t("kind_" + item.kind)),
  );
  if (item.moved) head.append(el("span", "badge", t("bib_moved")));
  card.append(head);
  const sides = el("div", "sides");
  for (const [side, value] of [
    ["before", item.left],
    ["after", item.right],
  ]) {
    const header = el("div", "side");
    const copy = el("button", "", t("copy_" + side));
    copy.disabled = value === null;
    action(copy, () => invoke("copy_text", { text: value || "" }));
    header.append(el("span", "", t("code_" + side)), copy);
    sides.append(header);
  }
  card.append(sides);
  if (showAll || item.kind !== "unchanged") {
    const table = el("table", "diff-table");
    const left = (item.left || "").split("\n");
    const right = (item.right || "").split("\n");
    const rows = [];
    for (const op of item.opcodes) {
      for (
        let offset = 0;
        offset < Math.max(op.i2 - op.i1, op.j2 - op.j1);
        offset++
      ) {
        const i = op.i1 + offset < op.i2 ? op.i1 + offset : null;
        const j = op.j1 + offset < op.j2 ? op.j1 + offset : null;
        rows.push({ i, j, tag: op.tag });
      }
    }
    let skipped = false;
    for (let n = 0; n < rows.length; n++) {
      const row = rows[n];
      const nearChange = rows
        .slice(Math.max(0, n - 1), n + 2)
        .some((r) => r.tag !== "equal");
      if (!showAll && !item.moved && row.tag === "equal" && !nearChange) {
        if (!skipped) {
          const gap = el("tr");
          gap.append(
            el("td", "", ""),
            el("td", "", "…"),
            el("td", "", ""),
            el("td", "", "…"),
          );
          table.append(gap);
        }
        skipped = true;
        continue;
      }
      skipped = false;
      const tr = el("tr");
      tr.append(
        el("td", "ln", row.i === null ? "" : String(row.i + 1)),
        el("td", row.tag + " old", row.i === null ? "" : left[row.i]),
        el("td", "ln", row.j === null ? "" : String(row.j + 1)),
        el("td", row.tag + " new", row.j === null ? "" : right[row.j]),
      );
      table.append(tr);
    }
    card.append(table);
  }
  const notes = el("div", "notes");
  for (const issue of issues)
    notes.append(
      el(
        "div",
        issue.severity,
        t("sev_" + issue.severity) + " · " + issue.message,
      ),
    );
  if (online)
    for (const [severity, message] of online.issues)
      notes.append(
        el(
          "div",
          severity === "FAIL" ? "error" : "info",
          severity + " · " + message,
        ),
      );
  if (arxiv) {
    const status = arxiv.publication || ["unavailable", ""];
    notes.append(
      el(
        "strong",
        "",
        "arXiv:" + arxiv.arxiv_id + " · " + t("arxiv_status_" + status[0]),
      ),
      el("div", "", status[1]),
    );
    if (arxiv.publication_url) {
      const source = el("button", "", t("arxiv_source_link"));
      action(source, () => invoke("open_link", { url: arxiv.publication_url }));
      notes.append(source);
    }
    if (arxiv.attempt_errors.length) {
      const details = el("details");
      details.append(
        el("summary", "", t("arxiv_attempts").replace("{n}", arxiv.attempts)),
      );
      const errors = el("ul");
      for (const [attempt, detail] of arxiv.attempt_errors)
        errors.append(
          el(
            "li",
            "",
            t("arxiv_attempt_error")
              .replace("{n}", attempt)
              .replace("{detail}", detail),
          ),
        );
      details.append(errors);
      notes.append(details);
    }
    if (arxiv.any_error) {
      const retry = el("button", "", t("arxiv_retry_entry"));
      retry.disabled = state.running;
      action(retry, () => runOnline(true, [arxiv.arxiv_id]));
      notes.append(retry);
    }
  }
  if (notes.children.length) card.append(notes);
  return card;
}
async function runOnline(arxivOnly, ids = null) {
  if (state.running) return;
  state.running = true;
  state.paused = false;
  state.cancelRequested = false;
  $("pause").textContent = t("pause");
  const generation = state.generation;
  const runId = ++state.runId;
  const revision = state.text;
  state.lastScope = arxivOnly ? "arxiv" : "all";
  $("run-controls").hidden = false;
  $("progress").textContent = t("state_running");
  renderResults();
  try {
    const only = state.drafts.verify_keys?.split(/[\s,]+/).filter(Boolean);
    const results = await invoke("check_online", {
      text: state.text,
      arxivOnly,
      ids: ids || (!arxivOnly && only?.length ? only : null),
      delay: Number(state.settings.verify.delay),
      workers: Number(state.settings.verify.workers),
      useCache: ids ? false : state.settings.verify.use_cache,
      runId,
    });
    if (
      generation !== state.generation ||
      (!arxivOnly && revision !== state.text)
    )
      return;
    if (arxivOnly && ids) {
      state.arxiv = state.arxiv.map((old) => {
        const result = results.find(
          (result) => result.arxiv_id === old.arxiv_id,
        );
        return result
          ? {
              ...result,
              attempts: old.attempts + result.attempts,
              attempt_errors: [
                ...old.attempt_errors,
                ...result.attempt_errors.map(([n, detail]) => [
                  old.attempts + n,
                  detail,
                ]),
              ],
            }
          : old;
      });
    } else if (arxivOnly) state.arxiv = results;
    else state.reports = results;
    $("progress").textContent = t(
      state.cancelRequested ? "stopped_partial" : "verify_done",
    ).replace("{n}", results.length);
  } finally {
    state.running = false;
    $("run-controls").hidden = true;
    renderResults();
  }
}
await listen("verification-progress", (event) => {
  const progress = event.payload;
  if (progress.id === state.runId)
    $("progress").textContent =
      `${progress.done}/${progress.total} · ${progress.key}`;
});
action($("apply"), async () => {
  const before = state.text;
  const generation = state.generation;
  $("apply").disabled = true;
  try {
    const result = await process({
      op: "apply",
      text: before,
      settings: state.settings,
      drafts: state.drafts,
    });
    if (generation !== state.generation) return;
    if (result.text !== before) {
      state.history.splice(state.pointer);
      state.history.push({ before, after: result.text });
      state.pointer++;
      state.text = result.text;
      if (state.running && state.lastScope === "all")
        await invoke("control_run", { action: "stop" });
      state.reports = [];
    }
    state.before = before;
    state.comparison = result.comparisons;
    state.issues = result.issues || [];
    renderResults();
  } finally {
    $("apply").disabled = false;
  }
});
async function jump(pointer) {
  if (state.running && state.lastScope === "all")
    await invoke("control_run", { action: "stop" });
  state.pointer = pointer;
  state.text = pointer ? state.history[pointer - 1].after : state.original;
  state.before = pointer ? state.history[pointer - 1].before : state.original;
  state.comparison = await process({
    op: "diff",
    text: state.before,
    after: state.text,
  });
  state.issues = [];
  state.reports = [];
  renderResults();
}
action($("undo"), () => jump(Math.max(0, state.pointer - 1)));
action($("redo"), () =>
  jump(Math.min(state.history.length, state.pointer + 1)),
);
action($("arxiv"), () => runOnline(true));
action($("retry-failed"), () =>
  runOnline(
    true,
    state.arxiv.filter((r) => r.any_error).map((r) => r.arxiv_id),
  ),
);
action($("pause"), async () => {
  state.paused = !state.paused;
  await invoke("control_run", { action: state.paused ? "pause" : "resume" });
  $("pause").textContent = t(state.paused ? "resume" : "pause");
});
action($("stop"), () => {
  state.cancelRequested = true;
  $("progress").textContent = t("verify_stopping");
  return invoke("control_run", { action: "stop" });
});
action($("diff-only"), () => {
  state.all = false;
  $("diff-only").classList.add("selected");
  $("diff-all").classList.remove("selected");
  renderResults();
});
action($("diff-all"), () => {
  state.all = true;
  $("diff-all").classList.add("selected");
  $("diff-only").classList.remove("selected");
  renderResults();
});
action($("copy-all"), () =>
  invoke("copy_text", {
    text: state.mode === "bib" ? state.text : state.tableInput,
  }),
);
action($("save"), () =>
  invoke("save_text", {
    name: state.mode === "bib" ? state.name || "references.bib" : "table.tex",
    text: state.mode === "bib" ? state.text : state.tableInput,
  }),
);
action($("choose-file"), () => {
  $("file").accept = state.mode === "bib" ? ".bib" : ".tex";
  $("file").click();
});
$("file").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    let text = await file.text();
    text = text.replace(/^\uFEFF/, "");
    if (state.mode === "bib") await loadBib(text, file.name);
    else {
      $("table-input").value = text;
      state.tableInput = text;
      updateTableControls();
    }
  } catch (error) {
    toast(String(error));
  }
  event.target.value = "";
});
function switchMode(mode) {
  if (state.mode === mode) return;
  if (state.mode === "bib") state.bibScroll = $("diff").scrollTop;
  state.mode = mode;
  $("bib").hidden = mode !== "bib";
  $("table").hidden = mode !== "table";
  $("nav-bib").classList.toggle("selected", mode === "bib");
  $("nav-table").classList.toggle("selected", mode === "table");
  renderResults();
  if (mode === "bib") $("diff").scrollTop = state.bibScroll || 0;
}
action($("nav-bib"), () => switchMode("bib"));
action($("nav-table"), () => switchMode("table"));
function updateTableControls() {
  const fresh =
    state.tableResult &&
    state.tableInput === state.tableResult.before &&
    JSON.stringify(state.tableOptions) === state.tableConfig;
  $("table-copy").disabled = !fresh;
  $("table-save").disabled = !fresh;
}
$("table-input").addEventListener("input", () => {
  state.tableInput = $("table-input").value;
  updateTableControls();
});
action($("table-format"), async () => {
  const before = state.tableInput;
  const opts = structuredClone(state.tableOptions);
  const result = await process({ op: "table", text: before, options: opts });
  if (!result.tables) throw new Error(t("table_no_tables"));
  state.tableResult = { before, ...result };
  state.tableConfig = JSON.stringify(opts);
  const comparison = await process({
    op: "text-diff",
    text: before,
    after: result.text,
  });
  $("table-result").replaceChildren(diffCard(comparison, [], null, null, true));
  $("table-status").textContent =
    result.tables +
    " " +
    t("table_title") +
    " · " +
    result.warnings.join(" · ");
  updateTableControls();
});
action($("table-copy"), () =>
  invoke("copy_text", { text: state.tableResult.text }),
);
action($("table-save"), () =>
  invoke("save_text", { name: "table.tex", text: state.tableResult.text }),
);
action($("table-example"), () => {
  state.tableInput =
    "\\begin{tabular}{cc} a & b \\\\ c & d \\\\ \\end{tabular}";
  $("table-input").value = state.tableInput;
  updateTableControls();
});
function translate() {
  document.documentElement.lang = state.language === "zh" ? "zh-CN" : "en";
  for (const [id, key] of Object.entries({
    "nav-bib": "nav_bib",
    "nav-table": "nav_table",
    undo: "undo",
    redo: "redo",
    "copy-all": "copy_all",
    save: "download",
    "choose-file": "import_file",
    apply: "bib_apply",
    arxiv: "arxiv_publication_action",
    pause: "pause",
    stop: "stop",
    "diff-title": "bib_diff",
    "diff-only": "bib_diff_only",
    "diff-all": "bib_diff_all",
    "table-label": "table_paste_title",
    "table-title": "table_title",
    "table-format": "table_format_btn",
    "table-copy": "table_copy_result",
    "table-save": "table_download",
    "table-example": "table_example",
  }))
    $(id).textContent = t(key);
  renderSettings();
  $("table-settings").replaceChildren();
  field(
    $("table-settings"),
    t("table_amp"),
    state.tableOptions.amp_style,
    (value) => {
      state.tableOptions.amp_style = value;
      updateTableControls();
    },
    {
      space: t("table_amp_space"),
      align: t("table_amp_align"),
      none: t("table_amp_none"),
    },
  );
  field(
    $("table-settings"),
    t("table_blank"),
    state.tableOptions.blank_lines_around_rules,
    (value) => {
      state.tableOptions.blank_lines_around_rules = value;
      updateTableControls();
    },
  );
  field(
    $("table-settings"),
    t("table_indent_label"),
    state.tableOptions.row_indent,
    (value) => {
      state.tableOptions.row_indent = value;
      updateTableControls();
    },
  );
  renderResults();
  updateTableControls();
}
action($("language"), () => {
  state.language = state.language === "zh" ? "en" : "zh";
  persist();
  translate();
});
const platform = await invoke("platform");
document.documentElement.dataset.platform = platform;
$("window-controls").hidden = platform !== "windows";
action($("window-minimize"), () => nativeWindow.minimize());
action($("window-maximize"), () => nativeWindow.toggleMaximize());
action($("window-close"), () => nativeWindow.close());
$("titlebar").addEventListener("mousedown", (event) => {
  if (event.button !== 0 || event.target.closest("button, input, a, select"))
    return;
  event.preventDefault();
  const command =
    event.detail === 2
      ? nativeWindow.toggleMaximize()
      : nativeWindow.startDragging();
  command.catch((error) => toast(String(error)));
});
translate();
