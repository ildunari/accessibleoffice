import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { open as openDialog } from "@tauri-apps/plugin-dialog";

type Mode = "scan" | "auto" | "full";
type Severity = "error" | "warning" | "tip" | "intelligent_services";
type SeverityFilter = Severity | "all";
type Target =
  | { kind: "file"; path: string }
  | { kind: "folder"; path: string; total: number; docx: number; pptx: number; sample: string[] };

interface Finding {
  id: string;
  rule_id: string;
  severity: Severity;
  wcag_sc: string[];
  officecli_path: string;
  current_value: string;
  plain_impact: string;
  why_human_needed: string;
}

interface Manifest {
  schema_version: string;
  file_path: string;
  file_format: string;
  stage_1_findings_total: number;
  stage_2_fixes_applied: unknown[];
  stage_3_fixes_applied: unknown[];
  residual_findings: Finding[];
  validation: { status: string };
}

interface CliStatus {
  found: boolean;
  path: string | null;
  version: string | null;
}

interface CostEvent {
  total_usd?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
}

interface FolderScan {
  folder: string;
  docx_count: number;
  pptx_count: number;
  total: number;
  sample: string[];
}

interface BatchFile {
  name: string;
  status: "queued" | "running" | "done" | "failed";
  s2?: number;
  s3?: number;
  residual?: number;
  elapsed_sec?: number;
  error?: string;
}

interface BatchSummary {
  files: number;
  done: number;
  failed: number;
  cost_usd: number;
}

const MODE_DESCRIPTIONS: Record<Mode, { title: string; subtitle: string }> = {
  scan: { title: "Scan", subtitle: "Detect issues. No writes." },
  auto: { title: "Auto-fix", subtitle: "Deterministic only. Fast, no AI." },
  full: { title: "Full", subtitle: "Detect + fix + agent (Claude Code)." },
};

const SEVERITY_COLOR: Record<Severity, string> = {
  error: "text-[var(--color-error)] bg-[color-mix(in_oklch,var(--color-error)_15%,transparent)]",
  warning: "text-[var(--color-warning)] bg-[color-mix(in_oklch,var(--color-warning)_18%,transparent)]",
  tip: "text-[var(--color-text-muted)] bg-[color-mix(in_oklch,var(--color-text-muted)_15%,transparent)]",
  intelligent_services: "text-[var(--color-accent)] bg-[color-mix(in_oklch,var(--color-accent)_15%,transparent)]",
};

const INSTALL_CMD = "pipx install git+https://github.com/ildunari/a11yfix.git";
const CLAUDE_CODE_URL = "https://claude.com/product/claude-code";
const MAX_LOG_LINES = 400;

function newRunId(): string {
  return `run-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function formatElapsed(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const total = Math.floor(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}.${Math.floor((ms % 1000) / 100)}s`;
}

function fileBasename(p: string): string {
  return p.split(/[\\/]/).pop() || p;
}

function isOfficeFile(p: string): boolean {
  return /\.(docx|pptx)$/i.test(p);
}

function manifestPathFor(filePath: string): string {
  const base = filePath.replace(/\.(docx|pptx)$/i, "");
  const ext = filePath.toLowerCase().endsWith(".pptx") ? ".pptx.manifest.json" : ".docx.manifest.json";
  return base + ext;
}

export default function App() {
  const [target, setTarget] = useState<Target | null>(null);
  const [mode, setMode] = useState<Mode>("auto");
  const [maxCostUsd, setMaxCostUsd] = useState<number>(0.5);
  const [running, setRunning] = useState(false);
  const [logLines, setLogLines] = useState<string[]>([]);
  const [showLog, setShowLog] = useState(false);
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [cli, setCli] = useState<CliStatus | null>(null);
  const [claudeCode, setClaudeCode] = useState<CliStatus | null>(null);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [cost, setCost] = useState<CostEvent | null>(null);
  const [modeNote, setModeNote] = useState<string | null>(null);
  const [severityFilter, setSeverityFilter] = useState<SeverityFilter>("all");
  const [copiedRuleId, setCopiedRuleId] = useState<string | null>(null);
  const [batchFiles, setBatchFiles] = useState<BatchFile[]>([]);
  const [batchStateDir, setBatchStateDir] = useState<string | null>(null);
  const [batchSummary, setBatchSummary] = useState<BatchSummary | null>(null);
  const runIdRef = useRef<string | null>(null);

  const recheckCli = useCallback(async () => {
    try {
      const result = await invoke<CliStatus>("check_cli");
      setCli(result);
    } catch {
      setCli({ found: false, path: null, version: null });
    }
  }, []);

  const recheckClaudeCode = useCallback(async () => {
    try {
      const result = await invoke<CliStatus>("check_claude_code");
      setClaudeCode(result);
    } catch {
      setClaudeCode({ found: false, path: null, version: null });
    }
  }, []);

  useEffect(() => {
    recheckCli();
    recheckClaudeCode();
  }, [recheckCli, recheckClaudeCode]);

  useEffect(() => {
    if (mode === "full" && claudeCode && !claudeCode.found) setMode("auto");
  }, [claudeCode, mode]);

  useEffect(() => {
    const subs = [
      listen<string>("a11yfix-log", (e) =>
        setLogLines((prev) => [...prev.slice(-MAX_LOG_LINES), e.payload])
      ),
      listen<{ run_id: string; elapsed_ms: number }>("a11yfix-progress", (e) => {
        if (e.payload.run_id === runIdRef.current) setElapsedMs(e.payload.elapsed_ms);
      }),
      listen<CostEvent>("a11yfix-cost", (e) => setCost(e.payload)),
      listen<string>("a11yfix-mode-note", (e) => setModeNote(e.payload)),
      listen<{ batch_id: string; mode: string; shards: number }>("accofc-batch-start", () => {
        setBatchFiles([]);
        setBatchSummary(null);
      }),
      listen<{ dir: string }>("accofc-batch-state-dir", (e) => setBatchStateDir(e.payload.dir)),
      listen<BatchFile>("accofc-batch-file", (e) => {
        setBatchFiles((prev) => {
          const updated = [...prev];
          const existing = updated.findIndex((f) => f.name === e.payload.name);
          if (existing >= 0) updated[existing] = e.payload;
          else updated.push(e.payload);
          return updated;
        });
      }),
      listen<BatchSummary>("accofc-batch-done", (e) => setBatchSummary(e.payload)),
    ];
    return () => {
      subs.forEach((s) => s.then((fn) => fn()));
    };
  }, []);

  // Native Tauri drag-drop handles BOTH files and folders. Folders show up as a path with no
  // extension; we then call scan_folder to get a count + sample for the preview.
  useEffect(() => {
    const subs = [
      listen<{ paths: string[] } | string[]>("tauri://drag-drop", async (e) => {
        const payload = e.payload as { paths?: string[] } | string[];
        const paths = Array.isArray(payload) ? payload : payload.paths || [];
        setDragOver(false);
        if (paths.length === 0) return;
        const first = paths[0];
        if (isOfficeFile(first)) {
          setTarget({ kind: "file", path: first });
          resetResults();
        } else {
          // Treat anything else as a folder candidate; scan_folder validates.
          await loadFolder(first);
        }
      }),
      listen("tauri://drag-enter", () => setDragOver(true)),
      listen("tauri://drag-leave", () => setDragOver(false)),
    ];
    return () => {
      subs.forEach((s) => s.then((fn) => fn()));
    };
  }, []);

  function resetResults() {
    setManifest(null);
    setError(null);
    setLogLines([]);
    setCost(null);
    setModeNote(null);
    setElapsedMs(0);
    setSeverityFilter("all");
    setBatchFiles([]);
    setBatchStateDir(null);
    setBatchSummary(null);
  }

  async function loadFolder(folderPath: string) {
    try {
      const scan = await invoke<FolderScan>("scan_folder", { folderPath });
      if (scan.total === 0) {
        setError(`No .docx or .pptx files found under ${folderPath}`);
        setTarget(null);
        return;
      }
      setTarget({
        kind: "folder",
        path: scan.folder,
        total: scan.total,
        docx: scan.docx_count,
        pptx: scan.pptx_count,
        sample: scan.sample,
      });
      resetResults();
    } catch (e) {
      setError(String(e));
    }
  }

  async function pickFile() {
    const selected = await openDialog({
      multiple: false,
      directory: false,
      filters: [{ name: "Office", extensions: ["docx", "pptx"] }],
    });
    if (typeof selected === "string") {
      setTarget({ kind: "file", path: selected });
      resetResults();
    }
  }

  async function pickFolder() {
    const selected = await openDialog({ multiple: false, directory: true });
    if (typeof selected === "string") {
      await loadFolder(selected);
    }
  }

  async function run() {
    if (!target || !cli?.found) return;
    if (mode === "full" && !claudeCode?.found) return;
    setRunning(true);
    resetResults();
    const id = newRunId();
    runIdRef.current = id;
    try {
      if (target.kind === "file") {
        const m = await invoke<Manifest>("run_a11yfix", { runId: id, filePath: target.path, mode });
        setManifest(m);
      } else {
        await invoke("run_batch", {
          runId: id,
          folderPath: target.path,
          mode,
          maxCostUsd: maxCostUsd > 0 ? maxCostUsd : null,
        });
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setRunning(false);
      runIdRef.current = null;
    }
  }

  async function cancel() {
    if (!runIdRef.current) return;
    await invoke("cancel_run", { runId: runIdRef.current }).catch(() => {});
  }

  async function revealManifest() {
    if (!target || target.kind !== "file") return;
    const path = manifestPathFor(target.path);
    await invoke("reveal_in_finder", { path }).catch((e) => setError(String(e)));
  }

  async function revealBatchOutput() {
    if (target?.kind === "folder") {
      await invoke("reveal_in_finder", { path: target.path }).catch(() => {});
    }
  }

  async function revealStateDir() {
    if (batchStateDir) {
      await invoke("reveal_in_finder", { path: batchStateDir }).catch(() => {});
    }
  }

  async function openClaudeCodePage() {
    await invoke("open_url", { url: CLAUDE_CODE_URL }).catch(() => {});
  }

  async function copyOfficecliCommand(f: Finding) {
    const cmd = `officecli ${f.officecli_path}`;
    await navigator.clipboard.writeText(cmd);
    setCopiedRuleId(f.id);
    setTimeout(() => setCopiedRuleId((cur) => (cur === f.id ? null : cur)), 1500);
  }

  const counts = useMemo(() => {
    if (!manifest) return null;
    const c: Record<Severity, number> = { error: 0, warning: 0, tip: 0, intelligent_services: 0 };
    for (const f of manifest.residual_findings) c[f.severity] = (c[f.severity] || 0) + 1;
    return c;
  }, [manifest]);

  const filteredFindings = useMemo(() => {
    if (!manifest) return [];
    if (severityFilter === "all") return manifest.residual_findings;
    return manifest.residual_findings.filter((f) => f.severity === severityFilter);
  }, [manifest, severityFilter]);

  const batchAggregate = useMemo(() => {
    let s2 = 0,
      s3 = 0,
      residual = 0,
      done = 0,
      failed = 0;
    for (const f of batchFiles) {
      if (f.status === "done") {
        done++;
        s2 += f.s2 || 0;
        s3 += f.s3 || 0;
        residual += f.residual || 0;
      } else if (f.status === "failed") failed++;
    }
    return { s2, s3, residual, done, failed, total: batchFiles.length };
  }, [batchFiles]);

  const targetTotal = target?.kind === "folder" ? target.total : 1;
  const batchProgressPct =
    target?.kind === "folder" && targetTotal > 0
      ? Math.min(100, Math.round(((batchAggregate.done + batchAggregate.failed) / targetTotal) * 100))
      : 0;

  return (
    <div className="flex h-full flex-col">
      <div data-tauri-drag-region className="titlebar h-7 shrink-0" />

      <main className="flex-1 overflow-y-auto px-8 pb-10 pt-4">
        <h1 className="font-display text-3xl font-semibold tracking-tight">AccessibleOffice</h1>
        <p className="mt-1 text-sm text-[var(--color-text-muted)]">
          Drop a Word or PowerPoint file — or a folder of them — to scan and auto-fix accessibility issues.
        </p>

        {cli && !cli.found && (
          <section className="fade-in mt-6 rounded-[var(--radius-card)] border border-[var(--color-warning)] bg-[color-mix(in_oklch,var(--color-warning)_8%,transparent)] p-5">
            <div className="flex items-start gap-3">
              <div className="text-lg">⚠</div>
              <div className="flex-1">
                <div className="font-medium">AccessibleOffice CLI not found.</div>
                <p className="mt-1 text-sm text-[var(--color-text-muted)]">
                  Install once, then come back. The desktop app is a thin GUI over the Python CLI.
                </p>
                <div className="mt-3 flex items-center gap-2">
                  <code className="flex-1 truncate rounded-[var(--radius-pill)] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 font-mono text-xs">
                    {INSTALL_CMD}
                  </code>
                  <button
                    onClick={() => navigator.clipboard.writeText(INSTALL_CMD)}
                    className="rounded-[var(--radius-pill)] border border-[var(--color-border)] px-3 py-1.5 text-xs hover:bg-[color-mix(in_oklch,var(--color-text)_4%,transparent)]"
                  >
                    Copy
                  </button>
                  <button
                    onClick={recheckCli}
                    className="rounded-[var(--radius-pill)] bg-[var(--color-accent)] px-3 py-1.5 text-xs font-medium text-[var(--color-accent-fg)] hover:opacity-90"
                  >
                    Check again
                  </button>
                </div>
              </div>
            </div>
          </section>
        )}

        {/* DROP ZONE */}
        <section
          className={`fade-in mt-6 rounded-[var(--radius-card)] border-2 border-dashed p-10 text-center transition-all duration-200
            ${dragOver
              ? "drop-pulse border-[var(--color-accent)] bg-[color-mix(in_oklch,var(--color-accent)_10%,transparent)]"
              : "border-[var(--color-border)] bg-[var(--color-surface-elevated)]"}`}
        >
          {target ? (
            <TargetPreview target={target} onClear={() => setTarget(null)} />
          ) : (
            <div className="flex flex-col items-center gap-3">
              <DropIcon />
              <div className="text-base font-medium">Drag a .docx, .pptx, or a folder here</div>
              <div className="text-xs text-[var(--color-text-muted)]">or pick one</div>
              <div className="mt-1 flex gap-2">
                <button
                  onClick={pickFile}
                  className="rounded-[var(--radius-pill)] bg-[var(--color-accent)] px-5 py-2 text-sm font-medium text-[var(--color-accent-fg)] shadow-sm transition hover:opacity-90 active:scale-[0.98]"
                >
                  Choose file…
                </button>
                <button
                  onClick={pickFolder}
                  className="rounded-[var(--radius-pill)] border border-[var(--color-border)] bg-[var(--color-surface)] px-5 py-2 text-sm font-medium transition hover:bg-[color-mix(in_oklch,var(--color-text)_4%,transparent)] active:scale-[0.98]"
                >
                  Choose folder…
                </button>
              </div>
            </div>
          )}
        </section>

        {/* MODE PICKER */}
        <section className="fade-in mt-8">
          <div className="text-sm font-medium text-[var(--color-text-muted)]">Mode</div>
          <div className="mt-2 grid grid-cols-3 gap-3">
            {(Object.keys(MODE_DESCRIPTIONS) as Mode[]).map((m) => {
              const active = mode === m;
              const fullDisabled = m === "full" && claudeCode != null && !claudeCode.found;
              const tooltip = fullDisabled
                ? "Claude Code is not installed. Click to open the install page."
                : m === "full"
                ? "Detects, auto-fixes, then runs Claude Code on residual issues."
                : undefined;
              return (
                <button
                  key={m}
                  onClick={() => {
                    if (fullDisabled) {
                      openClaudeCodePage();
                      return;
                    }
                    setMode(m);
                  }}
                  title={tooltip}
                  className={`relative rounded-[var(--radius-card)] border p-4 text-left transition
                    ${fullDisabled
                      ? "cursor-help border-[var(--color-border)] bg-[var(--color-surface-elevated)] opacity-50"
                      : active
                      ? "border-[var(--color-accent)] bg-[color-mix(in_oklch,var(--color-accent)_8%,transparent)] ring-1 ring-[var(--color-accent)]"
                      : "border-[var(--color-border)] bg-[var(--color-surface-elevated)] hover:bg-[color-mix(in_oklch,var(--color-text)_4%,transparent)]"}`}
                >
                  <div className="flex items-center gap-1.5">
                    <span className="font-medium">{MODE_DESCRIPTIONS[m].title}</span>
                    {fullDisabled && (
                      <span className="rounded-[var(--radius-pill)] bg-[var(--color-border)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--color-text-muted)]">
                        needs Claude Code
                      </span>
                    )}
                  </div>
                  <div className="mt-0.5 text-xs text-[var(--color-text-muted)]">
                    {fullDisabled ? "Click to install Claude Code." : MODE_DESCRIPTIONS[m].subtitle}
                  </div>
                </button>
              );
            })}
          </div>
        </section>

        {/* BATCH SETTINGS — only when target is a folder */}
        {target?.kind === "folder" && (
          <section className="fade-in mt-6 rounded-[var(--radius-card)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-4">
            <div className="flex items-center justify-between gap-4">
              <div>
                <div className="text-sm font-medium">Cost cap (batch)</div>
                <div className="text-xs text-[var(--color-text-muted)]">
                  When the total AI cost reaches this, remaining files fall back to deterministic auto-fix.
                </div>
              </div>
              <label className="flex items-center gap-2 text-sm">
                $
                <input
                  type="number"
                  step="0.1"
                  min="0"
                  value={maxCostUsd}
                  onChange={(e) => setMaxCostUsd(parseFloat(e.target.value) || 0)}
                  className="w-24 rounded-[var(--radius-pill)] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1 font-mono text-sm focus:outline-none focus:ring-1 focus:ring-[var(--color-accent)]"
                />
              </label>
            </div>
          </section>
        )}

        {/* RUN ROW */}
        <section className="mt-6 flex items-center gap-3">
          {!running ? (
            <button
              disabled={!target || !cli?.found || (mode === "full" && !claudeCode?.found)}
              onClick={run}
              className="rounded-[var(--radius-pill)] bg-[var(--color-text)] px-6 py-2.5 text-sm font-medium text-[var(--color-surface)] shadow-sm transition disabled:cursor-not-allowed disabled:opacity-40 enabled:hover:opacity-90 enabled:active:scale-[0.98]"
            >
              {target?.kind === "folder"
                ? `Run ${MODE_DESCRIPTIONS[mode].title.toLowerCase()} on ${target.total} files`
                : `Run ${MODE_DESCRIPTIONS[mode].title.toLowerCase()}`}
            </button>
          ) : (
            <button
              onClick={cancel}
              className="rounded-[var(--radius-pill)] border border-[var(--color-error)] bg-[color-mix(in_oklch,var(--color-error)_10%,transparent)] px-6 py-2.5 text-sm font-medium text-[var(--color-error)] hover:bg-[color-mix(in_oklch,var(--color-error)_18%,transparent)]"
            >
              Cancel
            </button>
          )}

          {running && (
            <div className="flex flex-1 items-center gap-3">
              <Spinner />
              <div className="flex-1">
                <div className="h-1.5 overflow-hidden rounded-full bg-[var(--color-border)]">
                  {target?.kind === "folder" ? (
                    <div
                      className="h-full bg-[var(--color-accent)] transition-all"
                      style={{ width: `${batchProgressPct}%` }}
                    />
                  ) : (
                    <div className="progress-bar h-full bg-[var(--color-accent)]" />
                  )}
                </div>
              </div>
              <div className="font-mono text-xs text-[var(--color-text-muted)]">
                {target?.kind === "folder" && (
                  <span className="mr-3">
                    {batchAggregate.done + batchAggregate.failed}/{targetTotal}
                  </span>
                )}
                {formatElapsed(elapsedMs)}
                {cost?.total_usd != null && <span className="ml-3">${cost.total_usd.toFixed(4)}</span>}
              </div>
            </div>
          )}

          {error && <span className="text-xs text-[var(--color-error)]">{error}</span>}
        </section>

        {modeNote && (
          <p className="mt-3 rounded-[var(--radius-card)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-4 py-2 text-xs text-[var(--color-text-muted)]">
            {modeNote}
          </p>
        )}

        {/* SINGLE-FILE RESULTS */}
        {target?.kind === "file" && manifest && counts && (
          <section className="fade-in mt-10">
            <div className="flex items-baseline justify-between">
              <h2 className="font-display text-xl font-semibold">Results</h2>
              <div className="flex items-center gap-3 text-xs text-[var(--color-text-muted)]">
                <span>
                  {manifest.stage_1_findings_total} detected ·{" "}
                  {manifest.stage_2_fixes_applied.length + manifest.stage_3_fixes_applied.length} fixed ·{" "}
                  {manifest.residual_findings.length} residual
                </span>
                <button
                  onClick={revealManifest}
                  className="rounded-[var(--radius-pill)] border border-[var(--color-border)] px-2.5 py-1 hover:bg-[color-mix(in_oklch,var(--color-text)_4%,transparent)]"
                >
                  Show manifest
                </button>
              </div>
            </div>

            <div className="mt-3 grid grid-cols-4 gap-3">
              <FilterStat
                label="Errors"
                value={counts.error}
                tone="error"
                active={severityFilter === "error"}
                onClick={() => setSeverityFilter(severityFilter === "error" ? "all" : "error")}
              />
              <FilterStat
                label="Warnings"
                value={counts.warning}
                tone="warning"
                active={severityFilter === "warning"}
                onClick={() => setSeverityFilter(severityFilter === "warning" ? "all" : "warning")}
              />
              <FilterStat
                label="Tips"
                value={counts.tip}
                tone="tip"
                active={severityFilter === "tip"}
                onClick={() => setSeverityFilter(severityFilter === "tip" ? "all" : "tip")}
              />
              <FilterStat label="Validation" value={manifest.validation.status} tone="muted" />
            </div>

            {filteredFindings.length > 0 ? (
              <div className="mt-6 overflow-hidden rounded-[var(--radius-card)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)]">
                <table className="w-full text-sm">
                  <thead className="bg-[color-mix(in_oklch,var(--color-text)_3%,transparent)] text-[var(--color-text-muted)]">
                    <tr>
                      <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider">Rule</th>
                      <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider">Sev</th>
                      <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider">Location</th>
                      <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider">Why</th>
                      <th className="px-4 py-2 text-right text-xs font-medium uppercase tracking-wider">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredFindings.slice(0, 100).map((f) => (
                      <tr key={f.id} className="border-t border-[var(--color-border)] align-top">
                        <td className="px-4 py-2 font-mono text-xs">{f.rule_id}</td>
                        <td className="px-4 py-2">
                          <span className={`rounded-[var(--radius-pill)] px-2 py-0.5 text-xs font-medium ${SEVERITY_COLOR[f.severity]}`}>
                            {f.severity}
                          </span>
                        </td>
                        <td className="px-4 py-2 font-mono text-xs text-[var(--color-text-muted)]">
                          {f.officecli_path}
                        </td>
                        <td className="px-4 py-2 text-xs text-[var(--color-text-muted)]">
                          {f.why_human_needed || f.plain_impact}
                        </td>
                        <td className="px-4 py-2 text-right">
                          <button
                            onClick={() => copyOfficecliCommand(f)}
                            className="rounded-[var(--radius-pill)] border border-[var(--color-border)] px-2 py-0.5 text-xs hover:bg-[color-mix(in_oklch,var(--color-text)_4%,transparent)]"
                          >
                            {copiedRuleId === f.id ? "Copied" : "Copy"}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {filteredFindings.length > 100 && (
                  <div className="px-4 py-2 text-xs text-[var(--color-text-muted)]">
                    … {filteredFindings.length - 100} more
                  </div>
                )}
              </div>
            ) : (
              <div className="mt-6 rounded-[var(--radius-card)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-6 text-center text-sm text-[var(--color-text-muted)]">
                No residual issues for this filter. ✓
              </div>
            )}
          </section>
        )}

        {/* BATCH RESULTS */}
        {target?.kind === "folder" && (batchFiles.length > 0 || batchSummary) && (
          <section className="fade-in mt-10">
            <div className="flex items-baseline justify-between">
              <h2 className="font-display text-xl font-semibold">Batch results</h2>
              <div className="flex items-center gap-3 text-xs text-[var(--color-text-muted)]">
                <span>
                  {batchAggregate.done} done · {batchAggregate.failed} failed ·{" "}
                  {batchAggregate.s2 + batchAggregate.s3} fixes · {batchAggregate.residual} residual
                  {batchSummary && <> · ${batchSummary.cost_usd.toFixed(4)}</>}
                </span>
                <button
                  onClick={revealBatchOutput}
                  className="rounded-[var(--radius-pill)] border border-[var(--color-border)] px-2.5 py-1 hover:bg-[color-mix(in_oklch,var(--color-text)_4%,transparent)]"
                >
                  Open folder
                </button>
                {batchStateDir && (
                  <button
                    onClick={revealStateDir}
                    title={batchStateDir}
                    className="rounded-[var(--radius-pill)] border border-[var(--color-border)] px-2.5 py-1 hover:bg-[color-mix(in_oklch,var(--color-text)_4%,transparent)]"
                  >
                    State dir
                  </button>
                )}
              </div>
            </div>

            <div className="mt-3 grid grid-cols-4 gap-3">
              <Stat label="Files done" value={batchAggregate.done} tone="success" />
              <Stat label="Failed" value={batchAggregate.failed} tone="error" />
              <Stat label="Auto fixes" value={batchAggregate.s2 + batchAggregate.s3} tone="tip" />
              <Stat label="Residual" value={batchAggregate.residual} tone="warning" />
            </div>

            <div className="mt-6 overflow-hidden rounded-[var(--radius-card)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)]">
              <table className="w-full text-sm">
                <thead className="bg-[color-mix(in_oklch,var(--color-text)_3%,transparent)] text-[var(--color-text-muted)]">
                  <tr>
                    <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider">File</th>
                    <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider">Status</th>
                    <th className="px-4 py-2 text-right text-xs font-medium uppercase tracking-wider">Auto</th>
                    <th className="px-4 py-2 text-right text-xs font-medium uppercase tracking-wider">AI</th>
                    <th className="px-4 py-2 text-right text-xs font-medium uppercase tracking-wider">Residual</th>
                    <th className="px-4 py-2 text-right text-xs font-medium uppercase tracking-wider">Time</th>
                  </tr>
                </thead>
                <tbody>
                  {batchFiles.slice(0, 200).map((f) => (
                    <tr key={f.name} className="border-t border-[var(--color-border)]">
                      <td className="px-4 py-2 font-mono text-xs" title={f.error || ""}>
                        {f.name}
                      </td>
                      <td className="px-4 py-2">
                        <BatchStatusBadge file={f} />
                      </td>
                      <td className="px-4 py-2 text-right font-mono text-xs">{f.s2 ?? "—"}</td>
                      <td className="px-4 py-2 text-right font-mono text-xs">{f.s3 ?? "—"}</td>
                      <td className="px-4 py-2 text-right font-mono text-xs">{f.residual ?? "—"}</td>
                      <td className="px-4 py-2 text-right font-mono text-xs text-[var(--color-text-muted)]">
                        {f.elapsed_sec != null ? `${f.elapsed_sec.toFixed(1)}s` : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {batchFiles.length > 200 && (
                <div className="px-4 py-2 text-xs text-[var(--color-text-muted)]">
                  … {batchFiles.length - 200} more
                </div>
              )}
            </div>
          </section>
        )}

        {/* LOG (collapsed by default) */}
        {logLines.length > 0 && (
          <section className="mt-10">
            <button
              onClick={() => setShowLog((v) => !v)}
              className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            >
              {showLog ? "▾" : "▸"} Log ({logLines.length})
            </button>
            {showLog && (
              <pre className="mt-2 max-h-72 overflow-auto rounded-[var(--radius-card)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-3 font-mono text-xs">
                {logLines.join("\n")}
              </pre>
            )}
          </section>
        )}

        <p className="mt-8 text-center text-[10px] text-[var(--color-text-muted)]">
          {cli?.found && cli.version && <span>CLI: {cli.version}</span>}
          {cli?.found && claudeCode?.found && (
            <span> · Claude Code: {claudeCode.version || "installed"}</span>
          )}
        </p>
      </main>
    </div>
  );
}

function TargetPreview({ target, onClear }: { target: Target; onClear: () => void }) {
  if (target.kind === "file") {
    return (
      <div className="flex flex-col items-center gap-2">
        <div className="rounded-[var(--radius-pill)] bg-[color-mix(in_oklch,var(--color-accent)_12%,transparent)] px-3 py-1 text-xs font-medium text-[var(--color-accent)]">
          {target.path.toLowerCase().endsWith(".pptx") ? "PowerPoint" : "Word"}
        </div>
        <div className="font-mono text-sm">{fileBasename(target.path)}</div>
        <div className="text-xs text-[var(--color-text-muted)]">{target.path}</div>
        <button
          onClick={onClear}
          className="mt-2 text-xs text-[var(--color-accent)] underline-offset-2 hover:underline"
        >
          Choose a different target
        </button>
      </div>
    );
  }
  return (
    <div className="flex flex-col items-center gap-2">
      <div className="rounded-[var(--radius-pill)] bg-[color-mix(in_oklch,var(--color-accent)_12%,transparent)] px-3 py-1 text-xs font-medium text-[var(--color-accent)]">
        Folder · {target.total} file{target.total === 1 ? "" : "s"}
      </div>
      <div className="font-mono text-sm">{fileBasename(target.path) || target.path}</div>
      <div className="text-xs text-[var(--color-text-muted)]">{target.path}</div>
      <div className="mt-1 text-xs text-[var(--color-text-muted)]">
        {target.docx} Word · {target.pptx} PowerPoint
      </div>
      {target.sample.length > 0 && (
        <div className="mt-2 max-w-md text-[10px] text-[var(--color-text-muted)]">
          {target.sample.slice(0, 5).join(" · ")}
          {target.sample.length > 5 && " · …"}
        </div>
      )}
      <button
        onClick={onClear}
        className="mt-2 text-xs text-[var(--color-accent)] underline-offset-2 hover:underline"
      >
        Choose a different target
      </button>
    </div>
  );
}

function BatchStatusBadge({ file }: { file: BatchFile }) {
  const map: Record<BatchFile["status"], { label: string; cls: string }> = {
    queued: { label: "queued", cls: "text-[var(--color-text-muted)] bg-[var(--color-border)]" },
    running: {
      label: "running",
      cls: "text-[var(--color-accent)] bg-[color-mix(in_oklch,var(--color-accent)_15%,transparent)]",
    },
    done: {
      label: "done",
      cls: "text-[var(--color-success)] bg-[color-mix(in_oklch,var(--color-success)_15%,transparent)]",
    },
    failed: {
      label: "failed",
      cls: "text-[var(--color-error)] bg-[color-mix(in_oklch,var(--color-error)_15%,transparent)]",
    },
  };
  const m = map[file.status];
  return (
    <span
      title={file.error}
      className={`rounded-[var(--radius-pill)] px-2 py-0.5 text-xs font-medium ${m.cls}`}
    >
      {m.label}
    </span>
  );
}

function Spinner() {
  return (
    <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-[var(--color-text-muted)] border-t-transparent" />
  );
}

function DropIcon() {
  return (
    <svg
      width="40"
      height="40"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="opacity-40"
      aria-hidden
    >
      <path d="M14 3v4a1 1 0 0 0 1 1h4" />
      <path d="M17 21H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h7l5 5v11a2 2 0 0 1-2 2z" />
      <path d="M12 11v6" />
      <path d="m9 14 3 3 3-3" />
    </svg>
  );
}

function FilterStat({
  label,
  value,
  tone,
  active,
  onClick,
}: {
  label: string;
  value: number | string;
  tone: "error" | "warning" | "tip" | "muted";
  active?: boolean;
  onClick?: () => void;
}) {
  const toneCls = {
    error: "text-[var(--color-error)]",
    warning: "text-[var(--color-warning)]",
    tip: "text-[var(--color-text)]",
    muted: "text-[var(--color-text-muted)]",
  }[tone];
  const interactive = !!onClick;
  const cls = `rounded-[var(--radius-card)] border p-4 text-left transition
    ${active ? "border-[var(--color-accent)] ring-1 ring-[var(--color-accent)]" : "border-[var(--color-border)]"}
    bg-[var(--color-surface-elevated)]
    ${interactive ? "cursor-pointer hover:bg-[color-mix(in_oklch,var(--color-text)_4%,transparent)]" : ""}`;
  return (
    <button onClick={onClick} disabled={!interactive} className={cls}>
      <div className="text-xs text-[var(--color-text-muted)]">{label}</div>
      <div className={`mt-1 font-display text-2xl font-semibold ${toneCls}`}>{value}</div>
    </button>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number | string;
  tone: "error" | "warning" | "tip" | "muted" | "success";
}) {
  const toneCls = {
    error: "text-[var(--color-error)]",
    warning: "text-[var(--color-warning)]",
    tip: "text-[var(--color-text)]",
    muted: "text-[var(--color-text-muted)]",
    success: "text-[var(--color-success)]",
  }[tone];
  return (
    <div className="rounded-[var(--radius-card)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-4">
      <div className="text-xs text-[var(--color-text-muted)]">{label}</div>
      <div className={`mt-1 font-display text-2xl font-semibold ${toneCls}`}>{value}</div>
    </div>
  );
}
