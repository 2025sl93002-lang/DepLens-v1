import * as vscode from "vscode";
import { simulateImpact } from "./client";
import type { ImpactReport, ImpactFinding } from "./types";

const panels = new Map<string, vscode.WebviewPanel>();

export async function openImpactPanel(
  scanId: number,
  pkgName: string,
  pkgVersion: string,
  latestVersion: string | null
): Promise<void> {
  const actions: vscode.QuickPickItem[] = [
    { label: "$(trash) Simulate removing this package", description: "See blast radius if removed" },
  ];
  if (latestVersion && latestVersion !== pkgVersion) {
    actions.unshift({
      label: `$(arrow-up) Simulate upgrading to ${latestVersion}`,
      description: "See what changes when upgraded",
    });
  }

  const pick = await vscode.window.showQuickPick(actions, {
    title: `DepLens Impact Simulator — ${pkgName}`,
    placeHolder: "Choose simulation type",
  });
  if (!pick) {
    return;
  }

  const isUpgrade = pick.label.startsWith("$(arrow-up)");
  const action = isUpgrade ? "upgrade" : "remove";
  const proposed = isUpgrade ? latestVersion ?? undefined : undefined;

  const key = `${scanId}:${pkgName}:${action}`;
  const existing = panels.get(key);
  if (existing) {
    existing.reveal(vscode.ViewColumn.Beside);
    return;
  }

  const panel = vscode.window.createWebviewPanel(
    "deplensImpact",
    `Impact: ${pkgName}`,
    vscode.ViewColumn.Beside,
    { enableScripts: false }
  );
  panels.set(key, panel);
  panel.onDidDispose(() => panels.delete(key));
  panel.webview.html = loadingHtml(pkgName, action);

  await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: `DepLens: Simulating ${action} of ${pkgName}…` },
    async () => {
      const report = await simulateImpact(scanId, pkgName, action, proposed);
      panel.webview.html = report ? renderHtml(report) : errorHtml(pkgName);
    }
  );
}

function severityColor(s: string): string {
  if (s === "critical") { return "#c0372c"; }
  if (s === "warning") { return "#b45309"; }
  return "#2563eb";
}

function renderHtml(r: ImpactReport): string {
  const findingsHtml =
    r.findings.length > 0
      ? r.findings
          .map(
            (f: ImpactFinding) => `
        <div class="finding finding-${f.severity}">
          <span class="badge" style="background:${severityColor(f.severity)}">${f.severity}</span>
          <strong>${f.message}</strong>
          ${f.detail ? `<p>${f.detail}</p>` : ""}
          <code>${f.evidence}</code>
        </div>`
          )
          .join("")
      : "<p class='ok'>✅ No significant risks detected.</p>";

  const affectedPkgs =
    r.affected_packages.length > 0
      ? `<ul>${r.affected_packages.map((p: string) => `<li><code>${p}</code></li>`).join("")}</ul>`
      : "<p>None.</p>";

  const affectedFiles =
    r.affected_files.length > 0
      ? `<ul>${r.affected_files
          .slice(0, 10)
          .map((f: string) => `<li><code>${f}</code></li>`)
          .join("")}${r.affected_files.length > 10 ? `<li>…and ${r.affected_files.length - 10} more</li>` : ""}</ul>`
      : "<p>None.</p>";

  const limits =
    r.limitations.length > 0
      ? `<p class="dim">${r.limitations.join(" · ")}</p>`
      : "";

  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline';">
<style>
  body { font-family: var(--vscode-font-family); font-size: var(--vscode-font-size); color: var(--vscode-foreground); background: var(--vscode-editor-background); padding: 16px 24px; max-width: 800px; }
  h1 { font-size: 1.3em; margin-bottom: 2px; }
  .meta { color: var(--vscode-descriptionForeground); margin-bottom: 20px; font-size: .9em; }
  .summary { background: var(--vscode-sideBar-background); border-radius: 6px; padding: 12px 16px; margin-bottom: 20px; border-left: 3px solid var(--vscode-focusBorder); }
  h2 { font-size: 1em; border-bottom: 1px solid var(--vscode-panel-border); padding-bottom: 4px; margin: 20px 0 10px; }
  .finding { background: var(--vscode-sideBar-background); border-radius: 6px; padding: 10px 14px; margin-bottom: 8px; }
  .badge { color: #fff; padding: 1px 7px; border-radius: 3px; font-size: 11px; font-weight: 600; text-transform: uppercase; margin-right: 8px; }
  code { font-family: var(--vscode-editor-font-family); font-size: .85em; opacity: .8; display: block; margin-top: 4px; }
  ul { margin: 0; padding-left: 20px; }
  li { margin-bottom: 4px; }
  .ok { color: #15803d; }
  .dim { color: var(--vscode-descriptionForeground); font-size: .85em; margin-top: 20px; }
  p { margin: 4px 0; }
</style>
</head>
<body>
  <h1>Impact: ${r.action} <code style="display:inline">${r.package}</code></h1>
  <div class="meta">${r.current_version ? `v${r.current_version}` : ""}${r.proposed_version ? ` → v${r.proposed_version}` : ""}</div>

  <div class="summary">${r.summary}</div>

  <h2>Risk findings</h2>
  ${findingsHtml}

  <h2>Affected packages (${r.affected_packages.length})</h2>
  ${affectedPkgs}

  <h2>Affected files (${r.affected_files.length})</h2>
  ${affectedFiles}

  ${r.verification.length > 0 ? `<h2>Verification checklist</h2><ul>${r.verification.map((v: string) => `<li>☐ ${v}</li>`).join("")}</ul>` : ""}

  ${limits}
</body>
</html>`;
}

function loadingHtml(name: string, action: string): string {
  return `<!DOCTYPE html><html><body style="font-family:var(--vscode-font-family);padding:24px;">
  <p>Simulating ${action} of <strong>${name}</strong>…</p></body></html>`;
}

function errorHtml(name: string): string {
  return `<!DOCTYPE html><html><body style="font-family:var(--vscode-font-family);padding:24px;">
  <p>Could not simulate impact for <strong>${name}</strong>. Is the DepLens backend running?</p></body></html>`;
}
