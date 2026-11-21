"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.ApplyFixProvider = void 0;
exports.executeApplyFix = executeApplyFix;
const vscode = require("vscode");
const client_1 = require("./client");
class ApplyFixProvider {
    constructor(getCache, getScanId) {
        this.getCache = getCache;
        this.getScanId = getScanId;
    }
    provideCodeActions(document, range) {
        if (!document.fileName.endsWith("package.json")) {
            return [];
        }
        const scanId = this.getScanId();
        if (scanId === null) {
            return [];
        }
        const upgradable = this.getCache().filter((p) => p.is_direct && p.latest && p.latest !== p.version && p.update_type !== "up-to-date");
        if (upgradable.length === 0) {
            return [];
        }
        const actions = [];
        const lineText = document.lineAt(range.start.line).text;
        for (const pkg of upgradable) {
            if (lineText.includes(`"${pkg.name}"`)) {
                actions.push(this.makeUpgradeAction(pkg, scanId, document));
            }
        }
        if (upgradable.length > 1) {
            actions.push(this.makeUpgradeAllAction(upgradable, scanId, document));
        }
        return actions;
    }
    makeUpgradeAction(pkg, scanId, document) {
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
    makeUpgradeAllAction(pkgs, scanId, document) {
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
exports.ApplyFixProvider = ApplyFixProvider;
ApplyFixProvider.providedCodeActionKinds = [vscode.CodeActionKind.QuickFix];
async function executeApplyFix(scanId, packages, fileUri) {
    const plan = await (0, client_1.getFixPlan)(scanId, packages ?? undefined);
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
    const choice = await vscode.window.showInformationMessage(`DepLens will apply ${plan.changes.length} change${plan.changes.length > 1 ? "s" : ""}:\n${summary}`, { modal: true }, "Apply");
    if (choice !== "Apply") {
        return;
    }
    const result = await (0, client_1.applyFix)(scanId, packages ?? undefined);
    if (!result || !result.written) {
        vscode.window.showErrorMessage("DepLens: Fix could not be applied.");
        return;
    }
    vscode.window.showInformationMessage(`DepLens: Applied ${result.changes.length} upgrade${result.changes.length > 1 ? "s" : ""}. Run \`${result.install_command}\` to update node_modules.`);
}
//# sourceMappingURL=ApplyFixProvider.js.map