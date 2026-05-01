import { useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { open as openDialog } from "@tauri-apps/plugin-dialog";

type Mode = "scan" | "auto" | "full";

type Severity = "error" | "warning" | "tip" | "intelligent_services";

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

const MODE_DESCRIPTIONS: Record<Mode, { title: string; subtitle: string }> = {
  scan: { title: "Scan", subtitle: "Detect issues. No writes." },
  auto: { title: "Auto-fix", subtitle: "Deterministic only. Fast, no AI." },
  full: { title: "Full", subtitle: "Detect + fix + interactive Claude session." },
};

const SEVERITY_COLOR: Record<Severity, string> = {
  error: "text-[--color-error] bg-[color-mix(in_oklch,var(--color-error)_15%,transparent)]",
  warning: "text-[--color-warning] bg-[color-mix(in_oklch,var(--color-warning)_18%,transparent)]",
  tip: "text-[--color-text-muted] bg-[color-mix(in_oklch,var(--color-text-muted)_15%,transparent)]",
  intelligent_services: "text-[--color-accent] bg-[color-mix(in_oklch,var(--color-accent)_15%,transparent)]",
};

export default function App() {
  const [filePath, setFilePath] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("auto");
  const [running, setRunning] = useState(false);
  const [logLines, setLogLines] = useState<string[]>([]);
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);

  useEffect(() => {
    const unsub = listen<string>("a11yfix-log", (e) => {
      setLogLines((prev) => [...prev.slice(-200), e.payload]);
    });
    return () => {
      unsub.then((fn) => fn());
    };
  }, []);

  // Listen for Tauri's native file-drop event (works without browser drag handlers).
  useEffect(() => {
    const unsub = listen<{ paths: string[] } | string[]>("tauri://drag-drop", (e) => {
      const payload = e.payload as { paths?: string[] } | string[];
      const paths = Array.isArray(payload) ? payload : payload.paths || [];
      const first = paths.find((p) => /\.(docx|pptx)$/i.test(p));
      if (first) setFilePath(first);
      setDragOver(false);
    });
    const unsubEnter = listen("tauri://drag-enter", () => setDragOver(true));
    const unsubLeave = listen("tauri://drag-leave", () => setDragOver(false));
    return () => {
      unsub.then((fn) => fn());
      unsubEnter.then((fn) => fn());
      unsubLeave.then((fn) => fn());
    };
  }, []);

  const fileName = useMemo(
    () => (filePath ? filePath.split("/").pop() || filePath : null),
    [filePath]
  );

  async function pickFile() {
    const selected = await openDialog({
      multiple: false,
      directory: false,
      filters: [{ name: "Office", extensions: ["docx", "pptx"] }],
    });
    if (typeof selected === "string") setFilePath(selected);
  }

  async function run() {
    if (!filePath) return;
    setRunning(true);
    setError(null);
    setManifest(null);
    setLogLines([]);
    try {
      const m = await invoke<Manifest>("run_a11yfix", { filePath, mode });
      setManifest(m);
    } catch (e) {
      setError(String(e));
    } finally {
      setRunning(false);
    }
  }

  const counts = useMemo(() => {
    if (!manifest) return null;
    const c: Record<Severity, number> = { error: 0, warning: 0, tip: 0, intelligent_services: 0 };
    for (const f of manifest.residual_findings) c[f.severity] = (c[f.severity] || 0) + 1;
    return c;
  }, [manifest]);

  return (
    <div className="flex h-full flex-col">
      <Titlebar />

      <main className="flex-1 overflow-y-auto px-8 py-6">
        <h1 className="font-display text-3xl font-semibold tracking-tight">Office accessibility</h1>
        <p className="mt-1 text-sm text-[--color-text-muted]">
          Drop a Word or PowerPoint file. Pick a mode. Get a manifest you can hand to anyone.
        </p>

        {/* DROP ZONE */}
        <section
          className={`fade-in mt-6 rounded-[--radius-card] border-2 border-dashed p-10 text-center transition-all duration-200
            ${dragOver
              ? "border-[--color-accent] bg-[color-mix(in_oklch,var(--color-accent)_8%,transparent)]"
              : "border-[--color-border] bg-[--color-surface-elevated]"}`}
        >
          {filePath ? (
            <div className="flex flex-col items-center gap-2">
              <div className="rounded-[--radius-pill] bg-[color-mix(in_oklch,var(--color-accent)_12%,transparent)] px-3 py-1 text-xs font-medium text-[--color-accent]">
                {filePath.toLowerCase().endsWith(".pptx") ? "PowerPoint" : "Word"}
              </div>
              <div className="font-mono text-sm">{fileName}</div>
              <div className="text-xs text-[--color-text-muted]">{filePath}</div>
              <button
                onClick={pickFile}
                className="mt-2 text-xs text-[--color-accent] underline-offset-2 hover:underline"
              >
                Choose a different file
              </button>
            </div>
          ) : (
            <div className="flex flex-col items-center gap-3">
              <div className="text-5xl opacity-40">⌘</div>
              <div className="text-base font-medium">Drag a .docx or .pptx here</div>
              <div className="text-sm text-[--color-text-muted]">or</div>
              <button
                onClick={pickFile}
                className="rounded-[--radius-pill] bg-[--color-accent] px-5 py-2 text-sm font-medium text-[--color-accent-fg] shadow-sm transition hover:opacity-90 active:scale-[0.98]"
              >
                Choose file…
              </button>
            </div>
          )}
        </section>

        {/* MODE PICKER */}
        <section className="fade-in mt-8">
          <div className="text-sm font-medium text-[--color-text-muted]">Mode</div>
          <div className="mt-2 grid grid-cols-3 gap-3">
            {(Object.keys(MODE_DESCRIPTIONS) as Mode[]).map((m) => {
              const active = mode === m;
              return (
                <button
                  key={m}
                  onClick={() => setMode(m)}
                  className={`rounded-[--radius-card] border p-4 text-left transition
                    ${active
                      ? "border-[--color-accent] bg-[color-mix(in_oklch,var(--color-accent)_8%,transparent)] ring-1 ring-[--color-accent]"
                      : "border-[--color-border] bg-[--color-surface-elevated] hover:bg-[color-mix(in_oklch,var(--color-text)_4%,transparent)]"}`}
                >
                  <div className="font-medium">{MODE_DESCRIPTIONS[m].title}</div>
                  <div className="mt-0.5 text-xs text-[--color-text-muted]">
                    {MODE_DESCRIPTIONS[m].subtitle}
                  </div>
                </button>
              );
            })}
          </div>
        </section>

        {/* RUN BUTTON */}
        <section className="mt-6 flex items-center gap-3">
          <button
            disabled={!filePath || running}
            onClick={run}
            className="rounded-[--radius-pill] bg-[--color-text] px-6 py-2.5 text-sm font-medium text-[--color-surface] shadow-sm transition disabled:cursor-not-allowed disabled:opacity-40 enabled:hover:opacity-90 enabled:active:scale-[0.98]"
          >
            {running ? "Running…" : `Run ${MODE_DESCRIPTIONS[mode].title.toLowerCase()}`}
          </button>
          {running && <Spinner />}
          {error && <span className="text-xs text-[--color-error]">{error}</span>}
        </section>

        {/* RESULTS */}
        {manifest && counts && (
          <section className="fade-in mt-10">
            <div className="flex items-baseline justify-between">
              <h2 className="font-display text-xl font-semibold">Results</h2>
              <span className="text-xs text-[--color-text-muted]">
                {manifest.stage_1_findings_total} detected ·{" "}
                {manifest.stage_2_fixes_applied.length + manifest.stage_3_fixes_applied.length} fixed ·{" "}
                {manifest.residual_findings.length} residual
              </span>
            </div>

            <div className="mt-3 grid grid-cols-4 gap-3">
              <Stat label="Errors" value={counts.error} tone="error" />
              <Stat label="Warnings" value={counts.warning} tone="warning" />
              <Stat label="Tips" value={counts.tip} tone="tip" />
              <Stat label="Validation" value={manifest.validation.status} tone="muted" />
            </div>

            {manifest.residual_findings.length > 0 && (
              <div className="mt-6 overflow-hidden rounded-[--radius-card] border border-[--color-border] bg-[--color-surface-elevated]">
                <table className="w-full text-sm">
                  <thead className="bg-[color-mix(in_oklch,var(--color-text)_3%,transparent)] text-[--color-text-muted]">
                    <tr>
                      <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider">Rule</th>
                      <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider">Sev</th>
                      <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider">Location</th>
                      <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider">Why</th>
                    </tr>
                  </thead>
                  <tbody>
                    {manifest.residual_findings.slice(0, 50).map((f) => (
                      <tr key={f.id} className="border-t border-[--color-border]">
                        <td className="px-4 py-2 font-mono text-xs">{f.rule_id}</td>
                        <td className="px-4 py-2">
                          <span className={`rounded-[--radius-pill] px-2 py-0.5 text-xs font-medium ${SEVERITY_COLOR[f.severity]}`}>
                            {f.severity}
                          </span>
                        </td>
                        <td className="px-4 py-2 font-mono text-xs text-[--color-text-muted]">{f.officecli_path}</td>
                        <td className="px-4 py-2 text-xs text-[--color-text-muted]">{f.why_human_needed || f.plain_impact}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {manifest.residual_findings.length > 50 && (
                  <div className="px-4 py-2 text-xs text-[--color-text-muted]">
                    … {manifest.residual_findings.length - 50} more
                  </div>
                )}
              </div>
            )}
          </section>
        )}

        {/* LIVE LOG */}
        {logLines.length > 0 && (
          <section className="mt-10">
            <h2 className="text-xs font-medium uppercase tracking-wider text-[--color-text-muted]">Log</h2>
            <pre className="mt-2 max-h-64 overflow-auto rounded-[--radius-card] border border-[--color-border] bg-[--color-surface-elevated] p-3 font-mono text-xs">
              {logLines.join("\n")}
            </pre>
          </section>
        )}
      </main>
    </div>
  );
}

function Titlebar() {
  return (
    <header className="titlebar h-10 shrink-0 border-b border-[--color-border] bg-[--color-surface-elevated]/80 backdrop-blur" />
  );
}

function Spinner() {
  return (
    <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-[--color-text-muted] border-t-transparent" />
  );
}

function Stat({ label, value, tone }: { label: string; value: number | string; tone: "error" | "warning" | "tip" | "muted" }) {
  const toneCls = {
    error: "text-[--color-error]",
    warning: "text-[--color-warning]",
    tip: "text-[--color-text]",
    muted: "text-[--color-text-muted]",
  }[tone];
  return (
    <div className="rounded-[--radius-card] border border-[--color-border] bg-[--color-surface-elevated] p-4">
      <div className="text-xs text-[--color-text-muted]">{label}</div>
      <div className={`mt-1 font-display text-2xl font-semibold ${toneCls}`}>{value}</div>
    </div>
  );
}
