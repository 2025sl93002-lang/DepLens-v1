import * as vscode from "vscode";
import { getProjects, checkHealth, getDecisions } from "./client";
import { DepTreeProvider, DepNode } from "./DepTreeProvider";
import { HoverProvider } from "./HoverProvider";
import { ApplyFixProvider, executeApplyFix } from "./ApplyFixProvider";
import { DecorationManager } from "./DecorationManager";
import { openImpactPanel } from "./ImpactPanel";
import { openDetail } from "./DetailPanel";
import { Project, Decision } from "./types";

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  let cachedDecisions: Decision[] = [];

  const treeProvider = new DepTreeProvider();
  const treeView = vscode.window.createTreeView("deplens.deps", {
    treeDataProvider: treeProvider,
    showCollapseAll: true,
  });

  const statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 10);
  statusBar.command = "deplens.refresh";
  statusBar.text = "$(shield) DepLens";
  statusBar.tooltip = "DepLens: Click to refresh";
  statusBar.show();

  const decorationManager = new DecorationManager(() => treeProvider.cachedPackages);

  // --- project resolution ---
  async function resolveProject(): Promise<Project | null> {
    const folders = vscode.workspace.workspaceFolders;
    if (!folders || folders.length === 0) { return null; }
    const wsPath = folders[0].uri.fsPath;
    const projects = await getProjects();
    return (
      projects.find((p) => p.path === wsPath) ??
      projects.find((p) => wsPath.startsWith(p.path)) ??
      null
    );
  }

  async function refresh(): Promise<void> {
    const online = await checkHealth();
    if (!online) {
      statusBar.text = "$(shield) DepLens: offline";
      statusBar.tooltip = "DepLens backend not running at localhost:8000";
      treeView.message = "Backend offline. Start DepLens and refresh.";
      treeProvider.clear();
      return;
    }

    const project = await resolveProject();
    if (!project || !project.last_scan) {
      statusBar.text = "$(shield) DepLens: no scan";
      statusBar.tooltip = "No DepLens scan found for this workspace";
      treeView.message = "No scan for this workspace. Open localhost:8000 to run one.";
      treeProvider.clear();
      return;
    }

    treeView.message = undefined;
    const grade = project.last_scan.health_grade ?? "?";
    statusBar.text = `$(shield) DepLens: ${grade}`;
    statusBar.tooltip = `Health grade: ${grade} — click to refresh`;
    treeView.title = `DepLens (${project.name})`;

    await treeProvider.load(project.last_scan.id, project.id);

    // Load decisions in the background
    getDecisions(project.id).then((d) => { cachedDecisions = d; });

    // Refresh decorations after packages loaded
    decorationManager.refresh();
  }

  // --- commands ---
  context.subscriptions.push(
    vscode.commands.registerCommand("deplens.refresh", () => { refresh(); })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(
      "deplens.openDetail",
      (scanId: number, name: string) => { openDetail(scanId, name); }
    )
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(
      "deplens.simulateImpact",
      (node: DepNode) => {
        openImpactPanel(node.scanId, node.pkg.name, node.pkg.version, node.pkg.latest ?? null);
      }
    )
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(
      "deplens.applyFix",
      (scanId: number, packages: string[] | null, fileUri: vscode.Uri) => {
        executeApplyFix(scanId, packages, fileUri).then(() => refresh());
      }
    )
  );

  // --- providers ---
  context.subscriptions.push(
    vscode.languages.registerHoverProvider(
      { language: "json", pattern: "**/package.json" },
      new HoverProvider(
        () => treeProvider.cachedPackages,
        () => cachedDecisions
      )
    )
  );

  context.subscriptions.push(
    vscode.languages.registerCodeActionsProvider(
      { language: "json", pattern: "**/package.json" },
      new ApplyFixProvider(
        () => treeProvider.cachedPackages,
        () => treeProvider.currentScanId
      ),
      { providedCodeActionKinds: ApplyFixProvider.providedCodeActionKinds }
    )
  );

  // --- auto-refresh on package.json save ---
  context.subscriptions.push(
    vscode.workspace.onDidSaveTextDocument((doc) => {
      if (doc.fileName.endsWith("package.json")) { refresh(); }
    })
  );

  // --- periodic refresh (60s) ---
  const timer = setInterval(() => { refresh(); }, 60_000);
  context.subscriptions.push({ dispose: () => clearInterval(timer) });

  context.subscriptions.push(statusBar, treeView, decorationManager);

  refresh();
}

export function deactivate(): void {}
