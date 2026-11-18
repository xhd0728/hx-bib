"""Shared responsive styles for the local workspace."""

CSS = """
:root { --hx-ink:#0f172a; --hx-body:#334155; --hx-muted:#5b6577; --hx-line:#e6e8f0;
        --hx-brand:#4338ca; --hx-brand-ink:#3730a3; --hx-brand-soft:#eef2ff;
        --hx-surface:#fff; --hx-bg:#f6f7fb; }
body { font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
       background: var(--hx-bg); color: var(--hx-body); }
.q-btn { border-radius: 10px; font-weight: 600; text-transform: none; }
.q-btn--unelevated.bg-primary { box-shadow: 0 2px 10px -4px rgba(67,56,202,.55); }
.q-btn--unelevated.bg-primary:hover { box-shadow: 0 3px 14px -4px rgba(67,56,202,.65); }
.q-field--outlined .q-field__control { border-radius: 10px; background:#fff; }
.q-field--outlined .q-field__control:before { border-color:#d8dce8; }
.q-field--outlined:hover .q-field__control:before { border-color:#b9c0d4; }
.q-field--outlined.q-field--focused .q-field__control:after { border-width:2px; }
/* segmented button groups (ui.toggle) — same visual language as the header switch */
.q-btn-toggle { background:#eef0f6; border-radius:12px; padding:3px; }
.q-btn-toggle .q-btn { border-radius:9px !important; font-weight:600; font-size:12.5px;
                       color:#4b5563; min-height:28px; padding:2px 12px;
                       transition:background .15s,color .15s,box-shadow .15s; }
.q-btn-toggle .q-btn:hover { background:rgba(255,255,255,.55); color:var(--hx-ink); }
.q-btn-toggle .q-btn.bg-white { background:#fff !important; color:var(--hx-brand-ink) !important;
                                box-shadow:0 1px 3px rgba(15,23,42,.14); }
/* subtle scrollbars */
::-webkit-scrollbar { width:10px; height:10px; }
::-webkit-scrollbar-track { background:transparent; }
::-webkit-scrollbar-thumb { background:#cbd5e1; border-radius:8px;
                            border:2px solid transparent; background-clip:padding-box; }
::-webkit-scrollbar-thumb:hover { background:#94a3b8; background-clip:padding-box;
                                  border:2px solid transparent; }
/* switches in brand indigo */
.q-toggle__inner { color:#b6bfd0; }
.q-toggle__inner--truthy { color:var(--hx-brand); }
.hx-title, .hx-brand, .hx-hero-title, .hx-panel-title { font-weight: 800;
       letter-spacing: -0.02em; color: var(--hx-ink); }
.hx-card { border-radius: 16px; background: var(--hx-surface); border: 1px solid var(--hx-line);
           box-shadow: 0 1px 2px rgba(15,23,42,.04), 0 10px 30px -18px rgba(15,23,42,.15); }
.hx-chip { display:inline-block; padding:2px 10px; border-radius:999px; font-size:12px;
           font-weight:700; white-space:nowrap; }
.hx-tab-note { color:#4b5563; font-size:13px; }
.hx-empty { border:1.5px dashed #cbd5e1; border-radius:12px; padding:22px 16px; width:100%;
            text-align:center; color:#64748b; font-size:13.5px; background:#fafbfc; }
/* ---- app header ---- */
.hx-header { position: sticky; top: 0; z-index: 50; background: rgba(255,255,255,.86);
             backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
             border-bottom: 1px solid var(--hx-line); }
.hx-header-inner { min-height:60px; display:grid; grid-template-columns:minmax(0,1fr) auto minmax(0,1fr); }
.hx-header-inner > .nicegui-row { min-width:0; }
.hx-header-inner > .hx-seg { justify-self:center; }
.hx-header-inner > .hx-lang { justify-self:end; }
.hx-logo { width:34px; height:34px; border-radius:10px; display:flex; align-items:center;
           justify-content:center; background:linear-gradient(135deg,#4338ca,#7c3aed);
           color:#fff; font-weight:800; font-size:13px; letter-spacing:.02em;
           box-shadow:0 4px 10px -4px rgba(67,56,202,.55); flex:none; }
.hx-brand { font-size:19px; line-height:1.1; }
.hx-brand-sub { font-size:12px; color:var(--hx-muted); line-height:1.2; }
/* ---- header mode switch (BibTeX tools ⇄ LaTeX tables) ---- */
.hx-seg { display:flex; gap:2px; padding:3px; background:#eef0f6;
          border:1px solid var(--hx-line); border-radius:12px; }
.hx-seg-item { display:inline-flex; align-items:center; gap:7px; text-decoration:none !important;
               font-size:13px; font-weight:700; color:#4b5563 !important; padding:6px 14px;
               border:none; background:transparent; cursor:pointer; font-family:inherit;
               border-radius:9px; transition:background .15s,color .15s,box-shadow .15s;
               white-space:nowrap; }
.hx-seg-item:hover { color:var(--hx-ink) !important; background:rgba(255,255,255,.55); }
.hx-seg-item .hx-seg-ico { font-size:16px; color:inherit; }
.hx-seg-active { background:#fff; color:var(--hx-brand-ink) !important;
                 box-shadow:0 1px 3px rgba(15,23,42,.14); cursor:default; }
.hx-seg-active:hover { background:#fff; }
@media (max-width: 680px) { .hx-brand-sub { display:none; } }
.hx-lang { color:var(--hx-brand-ink) !important; font-weight:700; }
/* ---- hero (upload screen) ---- */
.hx-hero-badge { display:inline-flex; align-items:center; gap:6px; padding:4px 12px;
                 border-radius:999px; background:#ecfdf5; color:#047857; font-size:12.5px;
                 font-weight:600; border:1px solid #d1fae5; }
.hx-hero-title { font-size:30px; line-height:1.2; margin:8px 0 2px; text-align:center; }
.hx-hero-sub { color:var(--hx-muted); font-size:14.5px; text-align:center; }
.hx-features { display:flex; flex-wrap:wrap; gap:8px; justify-content:center; }
.hx-feature { display:inline-flex; align-items:center; gap:6px; padding:6px 13px;
              border-radius:999px; background:#fff; border:1px solid var(--hx-line);
              color:#475569; font-size:12.5px; font-weight:600; }
.hx-feature .material-icons { color:var(--hx-brand); font-size:15px; }
.hx-divider { display:flex; align-items:center; gap:12px; color:var(--hx-muted);
              font-size:12.5px; font-weight:600; }
.hx-divider::before, .hx-divider::after { content:""; flex:1; height:1px;
              background:var(--hx-line); }
/* ---- uploader as dropzone ---- */
.hx-uploader { width:100%; }
.hx-uploader .q-uploader__header { background:linear-gradient(180deg,#fafbff,#f4f6fd) !important;
              color:var(--hx-ink) !important; border:2px dashed #c7d2fe; border-radius:14px;
              transition:border-color .15s, background .15s; }
.hx-uploader .q-uploader__header:hover { border-color:#818cf8; background:#eef2ff !important; }
.hx-uploader .q-uploader__header-content { padding:24px 16px; justify-content:center; text-align:center; }
.hx-uploader .q-uploader__title { font-weight:700; font-size:15px; }
.hx-uploader .q-uploader__subtitle { display:none; }
.hx-uploader .q-uploader__header .q-btn { background:#fff; border:1px solid var(--hx-line);
              color:var(--hx-brand-ink); border-radius:10px; }
/* auto-upload: hide the empty file list so the dropzone stays compact
   (still shows entries when a file errors out) */
.hx-uploader .q-uploader__list:not(:has(.q-uploader__file)) { display:none; }
/* ---- workspace tabs as a sticky segmented bar (pinned below the header) ---- */
.hx-tabs { position:sticky; top:60px; z-index:40; background:#f1f3f9;
           border:1px solid var(--hx-line); border-radius:14px; padding:4px;
           box-shadow:0 5px 18px -14px rgba(15,23,42,.4); }
.hx-tabs .q-tab { min-height:38px; border-radius:10px; padding:0 14px; color:#4b5563;
                  font-weight:600; font-size:13.5px;
                  transition:background .15s, color .15s, box-shadow .15s; }
.hx-tabs .q-tab:hover { background:rgba(255,255,255,.6); color:var(--hx-ink); }
.hx-tabs .q-tab--active { background:#fff; color:var(--hx-brand-ink);
                          box-shadow:0 1px 3px rgba(15,23,42,.14); }
.hx-tabs .q-tab--active:hover { background:#fff; }
.hx-tabs .q-tab__indicator { display:none; }
.hx-tabs .q-tabs__content { gap:2px; }
.hx-tabs .q-tabs__arrow { color:#64748b; }
/* ---- panel headers / option bars ---- */
.hx-panel-icon { width:36px; height:36px; border-radius:10px; background:var(--hx-brand-soft);
                 display:flex; align-items:center; justify-content:center;
                 color:var(--hx-brand-ink); flex:none; }
.hx-panel-title { font-size:16.5px; font-weight:700; line-height:1.25; color:var(--hx-ink); }
.hx-optbar { display:flex; flex-wrap:wrap; align-items:center; gap:12px 20px;
             padding:12px 14px; background:#f8fafc; border:1px solid var(--hx-line);
             border-radius:12px; }
.hx-optlabel { font-size:12px; font-weight:700; color:var(--hx-muted);
               text-transform:uppercase; letter-spacing:.04em; }
.hx-kbd { font-family:ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size:11.5px;
          color:#475569; background:#f1f5f9; border:1px solid var(--hx-line);
          border-bottom-width:2px; border-radius:6px; padding:1px 7px; }
.hx-venue-card { transition:border-color .15s; }
.hx-venue-card:hover { border-color:#c7d2fe; }
.hx-vrow { display:flex; align-items:center; gap:8px; padding:3px 4px 3px 8px;
           border-radius:8px; background:#f8fafc; border:1px solid #eef2f7; }
.hx-vrow-label { flex:none; width:54px; font-size:11px; font-weight:700; color:#475569;
                 letter-spacing:.04em; }
.hx-vrow-val { font-family:ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
               font-size:11.5px; color:#334155; }
.hx-style-ex { font-family:ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
               font-size:12px; color:#64748b; }
/* ---- status bar + stage history ---- */
.hx-status { background:rgba(255,255,255,.94); }
.hx-stage { border-radius:999px !important; color:#5b6577 !important; font-weight:600;
            font-size:12.5px !important; min-height:28px; padding:0 10px; }
.hx-stage:hover { background:#f1f5f9; }
.hx-stage-active { background:var(--hx-brand-soft) !important;
                   color:var(--hx-brand-ink) !important; }
.hx-stage-future { opacity:.45; }
.hx-diff { width:100%; min-width:560px; border-collapse:collapse; font:12.5px/1.55 ui-monospace,
           SFMono-Regular, Menlo, Consolas, monospace; table-layout: fixed; }
.hx-diff td { padding:1px 8px; vertical-align:top; white-space:pre-wrap; word-break:break-all; }
.hx-diff td.ln { width:44px; color:#6b7280; text-align:right; user-select:none; }
.hx-diff td.old, .hx-diff td.new { width: calc(50% - 44px); }
.hx-diff tr.del td.old { background:#fee2e2; }
.hx-diff tr.ins td.new { background:#dcfce7; }
.hx-diff tr.replace td.old { background:#fee2e2; }
.hx-diff tr.replace td.new { background:#dcfce7; }
.hx-diff tr.equal td { color:#6b7280; }
.hx-diff td.empty { background: repeating-linear-gradient(45deg,
  #fafbfc, #fafbfc 5px, #f1f3f6 5px, #f1f3f6 10px); }
.hx-ediff { border:1px solid #e5e7eb; border-radius:12px; margin:12px 0;
            overflow:hidden; background:#fff; }
.hx-ediff-head { display:flex; align-items:center; gap:8px; flex-wrap:wrap;
                 padding:7px 12px; font-size:13px; background:#f8fafc;
                 border-bottom:1px solid #eef2f7; }
.hx-ediff-added .hx-ediff-head { background:#f0fdf4; border-bottom-color:#dcfce7; }
.hx-ediff-removed .hx-ediff-head { background:#fef2f2; border-bottom-color:#fee2e2; }
.hx-ediff-modified .hx-ediff-head { background:#fffbeb; border-bottom-color:#fef3c7; }
.hx-ediff-unchanged { border-color:#eef2f7; }
.hx-ediff-unchanged .hx-ediff-head { border-bottom:none; opacity:.72; }
.hx-ediff-key { font-family:ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
                font-weight:700; color:#0f172a; }
.hx-ediff-body { overflow-x:auto; }
.hx-kind { display:inline-block; padding:1px 9px; border-radius:999px;
           font-size:11.5px; font-weight:700; }
.hx-kind-added { color:#15803d; background:#dcfce7; }
.hx-kind-removed { color:#b91c1c; background:#fee2e2; }
.hx-kind-modified { color:#92400e; background:#fef3c7; }
.hx-kind-unchanged { color:#475569; background:#f1f5f9; }
.hx-changes { width:100%; border-collapse:collapse; font-size:13.5px; }
.hx-changes th { text-align:left; font-size:12px; color:#4b5563; text-transform:uppercase;
                 letter-spacing:.05em; border-bottom:1px solid #e5e7eb; padding:6px 8px; }
.hx-changes td { padding:6px 8px; border-bottom:1px solid #f1f5f9; vertical-align:top; }
.hx-field-add { color:#15803d; background:#dcfce7; border-radius:5px; padding:0 5px; font-size:12px; }
.hx-field-del { color:#b91c1c; background:#fee2e2; border-radius:5px; padding:0 5px; font-size:12px; }
.hx-field-mod { color:#92400e; background:#fef3c7; border-radius:5px; padding:0 5px; font-size:12px; }
.hx-issue { display:flex; gap:8px; padding:4px 0; font-size:13.5px; }
/* ---- validation issue cards (expandable, severity-annotated) ---- */
.hx-check-clean { display:flex; align-items:center; gap:8px; color:#15803d;
                  font-size:14px; font-weight:600; padding:6px 2px; }
.hx-check-clean .material-icons { font-size:20px; }
.hx-issue-card { border:1px solid var(--hx-line); border-left:4px solid #cbd5e1;
                 border-radius:10px; background:#fff; margin:7px 0; overflow:hidden; }
.hx-issue-card summary { display:flex; align-items:center; gap:10px; padding:8px 12px;
                         cursor:pointer; list-style:none; flex-wrap:wrap; }
.hx-issue-card summary::-webkit-details-marker { display:none; }
.hx-issue-card[open] summary { background:#fafbfc; border-bottom:1px solid #eef2f7; }
.hx-issue-ico { font-size:18px; flex:none; }
.hx-issue-key { font:700 12.5px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
                color:#0f172a; }
.hx-issue-msg { color:#334155; font-size:13.5px; flex:1 1 240px; min-width:0; }
.hx-issue-meta { color:#6b7280; font-size:12px; white-space:nowrap; }
.hx-issue-caret { color:#94a3b8; font-size:18px; transition:transform .15s; }
.hx-issue-card[open] .hx-issue-caret { transform:rotate(180deg); }
.hx-issue-body { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
                 gap:10px 18px; padding:10px 14px 12px 42px; background:#fff; }
.hx-issue-body .lbl { font-size:11px; font-weight:700; color:var(--hx-muted);
                      text-transform:uppercase; letter-spacing:.04em; }
.hx-issue-body .val { font-size:13px; color:#334155; }
.hx-issue-body .val code { font-family:ui-monospace, SFMono-Regular, Menlo, Consolas,
                           monospace; font-weight:700; color:#0f172a; }
.hx-issue-hint { grid-column:1/-1; background:#f8fafc; border:1px dashed #e2e8f0;
                 border-radius:8px; padding:7px 10px; font-size:12.5px; color:#475569; }
.hx-evidence { background:#f8fafc; border:1px dashed #e2e8f0; border-radius:8px;
               padding:8px 10px; margin-top:8px; font-size:12.5px; color:#475569; }
.hx-names { margin:4px 0 0; padding-left:20px; }
.hx-names li.bad { background:#fee2e2; color:#b91c1c; font-weight:600; border-radius:4px; }
.hx-pre { margin:0; padding:8px 10px; background:#f8fafc; border:1px solid #eef2f7;
          border-radius:8px; font:12px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas,
          monospace; max-height:220px; overflow:auto; white-space:pre; }
/* ---- LaTeX table results: full result + per-table before/after ---- */
.hx-tblock { border:1px solid #e5e7eb; border-radius:12px; background:#fff;
             margin:10px 0; overflow:hidden; }
.hx-tblock > summary { display:flex; align-items:center; gap:8px; flex-wrap:wrap;
                       padding:10px 14px; cursor:pointer; list-style:none;
                       background:#f8fafc; font-size:13.5px; }
.hx-tblock > summary::-webkit-details-marker { display:none; }
.hx-tblock[open] > summary { border-bottom:1px solid #eee; }
.hx-tblock-title { font-weight:700; color:var(--hx-ink); }
.hx-tblock > .hx-tblock-body { padding:12px 14px; }
.hx-tblock .hx-pre { max-height:420px; }
.hx-tcompare > summary { background:#fff; }
.hx-tcompare-ico { font-size:16px; color:#64748b; transition:transform .15s; }
.hx-tcompare[open] .hx-tcompare-ico { transform:rotate(90deg); }
.hx-tpanes { display:grid; grid-template-columns:1fr 1fr; gap:12px; align-items:start; }
@media (max-width: 860px) { .hx-tpanes { grid-template-columns:1fr; } }
.hx-tpane { min-width:0; }
.hx-tpane-label { display:inline-flex; align-items:center; gap:5px; font-size:12px;
                  font-weight:700; padding:2px 10px; border-radius:999px; }
.hx-tpane-label .material-icons { font-size:14px; }
.hx-tpane-before { color:#b91c1c; background:#fef2f2; }
.hx-tpane-after { color:#15803d; background:#f0fdf4; }
.hx-tpane .hx-pre { max-height:360px; }
.hx-tnote { font-size:12.5px; color:#475569; background:#f8fafc; border:1px dashed #e2e8f0;
            border-radius:8px; padding:7px 10px; margin:8px 0 0; grid-column:1/-1; }
/* ---- 3-step journey strips ---- */
.hx-flow { display:flex; gap:6px; align-items:center; justify-content:center;
           flex-wrap:wrap; margin-top:14px; }
.hx-flow-step { display:inline-flex; align-items:center; gap:7px; padding:5px 13px;
                border-radius:999px; background:#fff; border:1px solid var(--hx-line);
                font-size:12.5px; font-weight:600; color:#475569; }
.hx-flow-num { display:inline-flex; width:17px; height:17px; border-radius:50%;
               align-items:center; justify-content:center; background:#4338ca; color:#fff;
               font-size:11px; font-weight:800; }
.hx-flow-arrow { color:#94a3b8; font-size:16px; }
.hx-steps { display:flex; gap:4px; flex-wrap:wrap; align-items:center; margin:2px 0; }
.hx-steps span.hx-step { display:inline-flex; align-items:center; gap:6px; font-size:12px;
                         color:#64748b; font-weight:600; }
.hx-steps b { display:inline-flex; width:16px; height:16px; border-radius:50%;
              background:#eef2ff; color:#4338ca; align-items:center;
              justify-content:center; font-size:10.5px; font-weight:800; }
.hx-steps .material-icons { font-size:14px; color:#cbd5e1; }
.hx-entry-card { border:1px solid #e5e7eb !important; border-radius:10px !important;
                 box-shadow:none !important; }
.q-tab { text-transform: none; }
button:focus-visible, .q-field:focus-within { outline: 2px solid #6366f1; outline-offset: 1px; }
/* The document never changes height when a result/tab changes. Each workspace
   panel owns its scrolling; toolbar and navigation remain in normal flex flow. */
html, body { height:100%; overflow:hidden; }
.nicegui-content { padding:0; gap:0; height:100dvh; }
.hx-page { height:100dvh; display:flex; flex-direction:column; overflow:hidden; }
.hx-page > .nicegui-refreshable { flex:none; }
.hx-header { position:relative; flex:none; background:#fff; }
.hx-header-inner { max-width:1440px; }
.hx-main { max-width:1440px; flex:1; min-height:0; display:flex; overflow:hidden; }
.hx-main > .nicegui-refreshable { display:flex; flex:1; min-width:0; min-height:0; }
.hx-body { display:flex; flex-direction:column; gap:16px; flex:1; min-height:0;
           overflow:auto; scrollbar-gutter:stable; }
.hx-workspace { height:100%; flex:1; min-height:0; overflow:hidden; }
.hx-workspace > .hx-status, .hx-workspace > .hx-tabs { flex:none; }
.hx-tabs { position:relative; top:auto; border-radius:12px; box-shadow:none; }
.hx-tabs .q-tab { min-height:42px; }
.hx-workspace > .nicegui-refreshable { flex:1; min-height:0; width:100%; overflow:hidden; }
.hx-panel-scroll { height:100%; overflow:auto; scrollbar-gutter:stable; padding-right:4px; }
.hx-card { border-radius:14px; box-shadow:0 1px 3px rgba(15,23,42,.035); }
.hx-operation-grid { display:grid; grid-template-columns:minmax(300px,360px) minmax(0,1fr);
                     gap:16px; height:100%; align-items:start; }
.hx-operation-grid > .hx-options, .hx-operation-grid > .hx-preview {
    max-height:100%; overflow:auto; scrollbar-gutter:stable; min-width:0; }
.hx-options > .nicegui-row:not(.hx-panel-head) { display:grid; grid-template-columns:repeat(2,minmax(0,1fr));
                            gap:12px; width:100%; align-items:center; }
.hx-options .q-field { width:100%; min-width:0; }
.hx-options .q-field__bottom { min-height:0; padding-top:3px; }
.hx-options .hx-optbar { flex-direction:column; align-items:stretch; padding:12px; }
.hx-options > .q-btn { width:100%; min-height:40px; }
.hx-options .q-toggle { margin-left:-8px; min-height:36px; }
.hx-preview { min-width:0; }
.hx-preview > .hx-card { padding:20px; }
.hx-preview .hx-empty { min-height:240px; display:flex; flex-direction:column;
                        justify-content:center; align-items:center; gap:10px; }
.hx-preview-content { min-height:220px; max-height:55dvh; overflow:auto;
                      scrollbar-gutter:stable; }
.hx-preview-content > .nicegui-html { width:100%; }
.hx-preview-actions { border-top:1px solid var(--hx-line); padding-top:14px; }
.hx-check-shortcuts .q-btn__content { justify-content:flex-start; text-align:left; }
.hx-check-run { position:sticky; top:0; z-index:2; flex:none; }
.hx-check-metrics { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; }
.hx-check-metric { display:grid; grid-template-columns:auto 1fr; align-items:center; gap:4px 10px;
                   padding:12px; border:1px solid var(--hx-line); border-radius:12px; background:#f8fafc; }
.hx-check-metric .material-icons { color:var(--metric-color); font-size:20px; grid-row:1/3; }
.hx-check-metric strong, .hx-metric-number { font-size:24px; line-height:1.2;
                                             font-weight:700; color:var(--hx-ink); }
.hx-check-metric > span:last-child, .hx-metric-label { font-size:12px; color:var(--hx-muted);
                                                     overflow-wrap:anywhere; }
.hx-check-issues { height:clamp(220px,42dvh,440px); flex:none; overflow:auto; scrollbar-gutter:stable; }
.hx-check-empty { min-height:300px !important; }
.hx-verify-controls { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }
.hx-verify-controls .q-btn { min-height:40px; }
.hx-verify-status { line-height:1.4; min-height:2.8em; display:flex; align-items:center; }
.hx-verify-list, [data-hx-scroll="preview-verify"], [data-hx-scroll="panel-verify"] { overflow-anchor:none; }
.hx-preview .hx-verify-empty { min-height:clamp(220px,42dvh,440px); text-align:center; }
.hx-verify-empty .material-icons { font-size:36px; color:#818cf8; }
.hx-verify-entry summary { display:grid; grid-template-columns:auto minmax(0,1fr) auto auto; gap:4px 10px; }
.hx-verify-entry .hx-chip { justify-self:start; white-space:normal; }
.hx-verify-entry-title { grid-column:1/-1; font-size:13.5px; color:#334155; overflow-wrap:anywhere;
                         display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
.hx-verify-entry[open] .hx-verify-entry-title { display:block; }
.hx-verify-details { padding:8px 12px 12px; overflow-wrap:anywhere; }
.hx-verify-authors { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; margin-top:4px; }
.hx-status { gap:8px; }
.hx-status-meta { min-height:28px; }
.hx-filename { max-width:240px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.hx-history { overflow:auto; height:32px; flex:none; }
.hx-history .q-btn { flex:none; }
.hx-vrow-val { flex:1; min-width:0; overflow-wrap:anywhere; }
.hx-vrow .q-btn { flex:none; }
.hx-venue-standalone { flex:1; min-height:0; }
.hx-venue-filters { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }
.hx-venue-apply-controls { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:10px; }
.hx-options > .hx-venue-jump { display:none; }
.hx-venue-view > .q-btn { flex:1; min-width:0; }
.hx-venue-frame { height:clamp(260px,49dvh,520px); flex:none; min-width:0; }
.hx-venue-catalog { height:100%; flex-wrap:nowrap; }
.hx-venue-scroll, .hx-venue-preview-slot { overflow:auto; scrollbar-gutter:stable; overflow-anchor:none; }
.hx-venue-scroll { flex:1; min-height:0; display:block; }
.hx-venue-preview-slot { height:100%; flex-wrap:nowrap; }
.hx-venue-preview > .hx-card { padding:12px; box-shadow:none; }
.hx-venue-preview .hx-preview-content { min-height:140px; max-height:32dvh; }
.hx-venue-results, [data-hx-scroll="panel-venues"], [data-hx-scroll="panel-venues-only"] { overflow-anchor:none; }
.hx-venue-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,290px),1fr));
                 gap:10px; }
.hx-venue-card { min-width:0; }
.hx-venue-tags { flex-wrap:wrap; gap:6px; }
.hx-venue-abbr { font:800 15px ui-monospace,monospace; color:var(--hx-ink); overflow-wrap:anywhere; }
.hx-venue-family { background:#eef2ff; color:#4338ca; }
.hx-venue-kind { background:#f1f5f9; color:#475569; }
.hx-venue-used { background:#ecfdf5; color:#047857; }
.hx-venue-name { flex:1; font-size:13px; line-height:1.45; color:#475569; overflow-wrap:anywhere; }
.hx-preview .hx-venue-empty { min-height:100%; text-align:center; }
.hx-venue-empty > .q-icon { color:#818cf8; }
@media (min-width:901px) {
  .hx-operation-grid > .hx-venue-results { height:100%; overflow:hidden; }
  .hx-venue-results > .hx-card { max-height:100%; flex-wrap:nowrap; }
  .hx-venue-frame { flex:1 1 auto; min-height:0; }
}
.hx-panel-title, .hx-tab-note, .hx-issue-key { overflow-wrap:anywhere; }
.q-field textarea { resize:vertical; }
.q-dialog .q-card { min-width:0 !important; width:420px; max-width:calc(100vw - 32px); }
@media (max-width:900px) {
  .hx-operation-grid { grid-template-columns:minmax(0,1fr); height:auto; }
  .hx-operation-grid > .hx-options, .hx-operation-grid > .hx-preview { max-height:none; overflow:visible; }
  .hx-options > .nicegui-row:not(.hx-panel-head) { grid-template-columns:repeat(3,minmax(0,1fr)); }
  .hx-options .hx-optbar { flex-direction:row; flex-wrap:wrap; }
  .hx-options > .hx-venue-jump { display:inline-flex; }
}
@media (max-width:600px) {
  .hx-header-inner { display:grid; grid-template-columns:1fr auto; gap:8px; padding:10px 16px; }
  .hx-header .hx-seg { grid-row:2; grid-column:1/-1; justify-content:center; }
  .hx-header .hx-lang { grid-row:1; grid-column:2; }
  .hx-header .hx-seg-item { justify-content:center; flex:1; padding:6px 10px; }
  .hx-main { padding:12px 8px; }
  .hx-hero-title { font-size:25px; }
  .hx-hero-sub { font-size:13px; padding:0 8px; }
  .hx-body > .hx-card, .hx-panel-scroll > .hx-card, .hx-options, .hx-preview > .hx-card { padding:16px; }
  .hx-options > .nicegui-row:not(.hx-panel-head) { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .hx-body .q-field { max-width:100%; min-width:0; }
  .hx-status-meta { gap:6px; }
  .hx-status-meta .hx-chip { font-size:11px; padding:2px 6px; }
  .hx-filename { max-width:140px; }
  .hx-status .q-btn { font-size:12px; }
  .hx-tabs .q-tab { min-height:40px; padding:0 12px; }
  .hx-flow-arrow { display:none; }
  .hx-flow-step { padding:4px 8px; font-size:11px; }
  .hx-preview-content { max-height:65dvh; }
  .hx-check-metrics { gap:6px; }
  .hx-check-metric { padding:10px; gap:4px; }
  .hx-check-metric strong, .hx-metric-number { font-size:22px; }
  .hx-check-metric .material-icons { font-size:16px; }
  .hx-verify-entry summary { grid-template-columns:minmax(0,1fr) auto auto; }
  .hx-verify-entry .hx-issue-key { grid-area:2/1/3/-1; }
  .hx-venue-apply-controls { grid-template-columns:minmax(0,1fr); }
}
@media (prefers-reduced-motion:reduce) {
  *, *::before, *::after { animation:none !important; transition:none !important; scroll-behavior:auto !important; }
}
"""
