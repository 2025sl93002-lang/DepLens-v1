import * as vscode from "vscode";
import { PackageDetail, Severity } from "./types";
import { getPackageDetail } from "./client";

const panels = new Map<string, vscode.WebviewPanel>();

export async function openDetail(
  scanId: number,
  name: string
): Promise<void> {
  const key = `${scanId}:${name}`;
  const existing = panels.get(key);
  if (existing) {
    existing.reveal(vscode.ViewColumn.Beside);
    return;
  }

  const panel = vscode.window.createWebviewPanel(
    "deplensDetail",
    `DepLens: ${name}`,
    vscode.ViewColumn.Beside,
    { enableScripts: false }
  );
  panels.set(key, panel);
  panel.onDidDispose(() => panels.delete(key));

  panel.webview.html = loadingHtml(name);

  const detail = await getPackageDetail(scanId, name);
  panel.webview.html = detail ? renderHtml(detail) : errorHtml(name);
}

function severityBadge(s: Severity): string {
  const colors: Record<Severity, string> = {
    critical: "#9f1239",
    high: "#c0372c",
    moderate: "#b45309",
    low: "#15803d",
    unknown: "#64748b",
  };
  return `<span style="background:${colors[s]};color:#fff;padding:1px 6px;border-radius:3px;font-size:11px;font-weight:600;text-transform:uppercase;">${s}</span>`;
}

function renderHtml(d: PackageDetail): string {
  const vulns = d.vulnerabilities
    .map(
      (v) => `
      <tr>
        <td>${severityBadge(v.severity)}</td>
        <td><a href="${v.url}">${v.cve ?? v.osv_id}</a></td>
        <td>${v.summary}</td>
        <td>${v.fixed_version ?? "—"}</td>
      </tr>`
    )
    .join("");

  const paths = d.dependency_paths
    .slice(0, 8)
    .map((p) => `<li><code>${p.join(" → ")}</code></li>`)
    .join("");

  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline';">
<style>
  body { font-family: var(--vscode-font-family); font-size: var(--vscode-font-size); color: var(--vscode-foreground); background: var(--vscode-editor-background); padding: 16px 24px; }
  h1 { font-size: 1.3em; margin-bottom: 4px; }
  .sub { color: var(--vscode-descriptionForeground); margin-bottom: 20px; }
  .grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 24px; }
  .cell { background: var(--vscode-sideBar-background); border-radius: 6px; padding: 10px 14px; }
  .cell-label { font-size: 11px; text-transform: uppercase; letter-spacing: .05em; color: var(--vscode-descriptionForeground); }
  .cell-value { font-size: 1.1em; font-weight: 600; margin-top: 2px; }
  h2 { font-size: 1em; border-bottom: 1px solid var(--vscode-panel-border); padding-bottom: 4px; margin: 20px 0 10px; }
  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: .05em; color: var(--vscode-descriptionForeground); padding-bottom: 6px; }
  td { padding: 6px 8px 6px 0; vertical-align: top; border-bottom: 1px solid var(--vscode-panel-border); }
  a { color: var(--vscode-textLink-foreground); }
  code { font-family: var(--vscode-editor-font-family); font-size: .9em; }
  ul { margin: 0; padding-left: 20px; }
  li { margin-bottom: 4px; }
</style>
</head>
<body>
  <h1>${d.name}</h1>
  <div class="sub">${d.scope} dependency · depth ${d.depth} · ${d.dependents} dependent${d.dependents !== 1 ? "s" : ""}</div>

  <div class="grid">
    <div class="cell"><div class="cell-label">Installed</div><div class="cell-value">${d.version ?? "—"}</div></div>
    <div class="cell"><div class="cell-label">Latest</div><div class="cell-value">${d.latest ?? "—"}</div></div>
    <div class="cell"><div class="cell-label">License</div><div class="cell-value">${d.license ?? "—"}</div></div>
    <div class="cell"><div class="cell-label">Vulnerabilities</div><div class="cell-value">${d.vuln_count ?? 0}</div></div>
    <div class="cell"><div class="cell-label">Update</div><div class="cell-value">${d.update_type ?? "—"}</div></div>
    <div class="cell"><div class="cell-label">Maintenance</div><div class="cell-value">${d.maintenance_status ?? "—"}</div></div>
  </div>

  ${
    (d.vulnerabilities?.length ?? 0) > 0
      ? `<h2>Vulnerabilities (${d.vulnerabilities.length})</h2>
         <table><thead><tr><th>Severity</th><th>ID</th><th>Summary</th><th>Fix</th></tr></thead>
         <tbody>${vulns}</tbody></table>`
      : "<p>No known vulnerabilities.</p>"
  }

  ${
    d.dependency_paths.length > 0
      ? `<h2>Dependency paths</h2><ul>${paths}</ul>`
      : ""
  }
</body>
</html>`;
}

function loadingHtml(name: string): string {
  return `<!DOCTYPE html><html><body style="font-family:var(--vscode-font-family);padding:24px;">
  <p>Loading ${name}…</p></body></html>`;
}

function errorHtml(name: string): string {
  return `<!DOCTYPE html><html><body style="font-family:var(--vscode-font-family);padding:24px;">
  <p>Could not load details for <strong>${name}</strong>. Is the DepLens backend running?</p></body></html>`;
}
