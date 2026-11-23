import * as vscode from "vscode";
import { PackageRow } from "./types";
import { getFixPlan, applyFix } from "./client";

export class ApplyFixProvider implements vscode.CodeActionProvider {
  static readonly providedCodeActionKinds = [vscode.CodeActionKind.QuickFix];

  constructor(
    private getCache: () => PackageRow[],
    private getScanId: () => number | null
  ) {}

  provideCodeActions(
    document: vscode.TextDocument,
    range: vscode.Range
  ): vscode.CodeAction[] {
    if (!document.fileName.endsWith("package.json")) { return []; }
    const scanId = this.getScanId();
    if (scanId === null) { return []; }

    const upgradable = this.getCache().filter(
      (p) => p.is_direct && p.latest && p.latest !== p.version && p.update_type !== "up-to-date"
    );
    if (upgradable.length === 0) { return []; }

    const actions: vscode.CodeAction[] = [];
    const lineText = document.lineAt(range.start.line).text;

    // Individual upgrade for the package on the current line
    for (const pkg of upgradable) {
      if (lineText.includes(`"${pkg.name}"`)) {
        actions.push(this.makeUpgradeAction(pkg, scanId, document));
      }
    }

    // "Upgrade all" action shown anywhere in the file
    if (upgradable.length > 1) {
      actions.push(this.makeUpgradeAllAction(upgradable, scanId, document));
    }

    return actions;
  }

  private makeUpgradeAction(
    pkg: PackageRow,
    scanId: number,
    document: vscode.TextDocument
  ): vscode.CodeAction {
    const label = `DepLens: Upgrade ${pkg.name} ${pkg.version} → ${pkg.latest}`;
    const action = new vscode.CodeAction(label, vscode.CodeActionKind.QuickFix);
    action.command = {
      command: "deplens.applyFix",
      title: label,
      arguments: [scanId, [pkg.name], document.uri],
    };
    action.isPreferred = pkg.vuln_count > 0;
    return action;
  }

  private makeUpgradeAllAction(
    pkgs: PackageRow[],
    scanId: number,
    document: vscode.TextDocument
  ): vscode.CodeAction {
    const label = `DepLens: Upgrade all ${pkgs.length} outdated packages`;
    const action = new vscode.CodeAction(label, vscode.CodeActionKind.QuickFix);
    action.command = {
      command: "deplens.applyFix",
      title: label,
      arguments: [scanId, null, document.uri],
    };
    return action;
  }
}

export async function executeApplyFix(
  scanId: number,
  packages: string[] | null,
  fileUri: vscode.Uri
): Promise<void> {
  // Show plan first so user knows what will change
  const plan = await getFixPlan(scanId, packages ?? undefined);
  if (!plan) {
    vscode.window.showErrorMessage("DepLens: Could not compute fix plan. Is the backend running?");
    return;
  }

  if (plan.changes.length === 0) {
    vscode.window.showInformationMessage("DepLens: Nothing to upgrade.");
    return;
  }

  const summary = plan.changes
    .map((c) => `${c.package}: ${c.from_spec ?? "?"} → ${c.to_spec}`)
    .join("\n");

  const choice = await vscode.window.showInformationMessage(
    `DepLens will apply ${plan.changes.length} change${plan.changes.length > 1 ? "s" : ""}:\n${summary}`,
    { modal: true },
    "Apply"
  );
  if (choice !== "Apply") { return; }

  const result = await applyFix(scanId, packages ?? undefined);
  if (!result || !result.written) {
    vscode.window.showErrorMessage("DepLens: Fix could not be applied.");
    return;
  }

  vscode.window.showInformationMessage(
    `DepLens: Applied ${result.changes.length} upgrade${result.changes.length > 1 ? "s" : ""}. Run \`${result.install_command}\` to update node_modules.`
  );
}
