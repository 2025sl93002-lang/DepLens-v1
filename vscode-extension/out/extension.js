"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.activate = activate;
exports.deactivate = deactivate;
const vscode = require("vscode");
const client_1 = require("./client");
const DepTreeProvider_1 = require("./DepTreeProvider");
const HoverProvider_1 = require("./HoverProvider");
const ApplyFixProvider_1 = require("./ApplyFixProvider");
const DecorationManager_1 = require("./DecorationManager");
const ImpactPanel_1 = require("./ImpactPanel");
const DetailPanel_1 = require("./DetailPanel");
async function activate(context) {
    let cachedDecisions = [];
    const treeProvider = new DepTreeProvider_1.DepTreeProvider();
    const treeView = vscode.window.createTreeView("deplens.deps", {
        treeDataProvider: treeProvider,
        showCollapseAll: true,
    });
    const statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 10);
    statusBar.command = "deplens.refresh";
    statusBar.text = "$(shield) DepLens";
    statusBar.tooltip = "DepLens: Click to refresh";
    statusBar.show();
    const decorationManager = new DecorationManager_1.DecorationManager(() => treeProvider.cachedPackages);
    async function resolveProject() {
        const folders = vscode.workspace.workspaceFolders;
        if (!folders || folders.length === 0) {
            return null;
        }
        const wsPath = folders[0].uri.fsPath;
        const projects = await (0, client_1.getProjects)();
        return (projects.find((p) => p.path === wsPath) ??
            projects.find((p) => wsPath.startsWith(p.path)) ??
            null);
    }
    async function refresh() {
        const online = await (0, client_1.checkHealth)();
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
        (0, client_1.getDecisions)(project.id).then((d) => { cachedDecisions = d; });
        decorationManager.refresh();
    }
    context.subscriptions.push(vscode.commands.registerCommand("deplens.refresh", () => { refresh(); }));
    context.subscriptions.push(vscode.commands.registerCommand("deplens.openDetail", (scanId, name) => {
        (0, DetailPanel_1.openDetail)(scanId, name);
    }));
    context.subscriptions.push(vscode.commands.registerCommand("deplens.simulateImpact", (node) => {
        (0, ImpactPanel_1.openImpactPanel)(node.scanId, node.pkg.name, node.pkg.version, node.pkg.latest ?? null);
    }));
    context.subscriptions.push(vscode.commands.registerCommand("deplens.applyFix", (scanId, packages, fileUri) => {
        (0, ApplyFixProvider_1.executeApplyFix)(scanId, packages, fileUri).then(() => refresh());
    }));
    context.subscriptions.push(vscode.languages.registerHoverProvider({ language: "json", pattern: "**/package.json" }, new HoverProvider_1.HoverProvider(() => treeProvider.cachedPackages, () => cachedDecisions)));
    context.subscriptions.push(vscode.languages.registerCodeActionsProvider({ language: "json", pattern: "**/package.json" }, new ApplyFixProvider_1.ApplyFixProvider(() => treeProvider.cachedPackages, () => treeProvider.currentScanId), { providedCodeActionKinds: ApplyFixProvider_1.ApplyFixProvider.providedCodeActionKinds }));
    context.subscriptions.push(vscode.workspace.onDidSaveTextDocument((doc) => {
        if (doc.fileName.endsWith("package.json")) {
            refresh();
        }
    }));
    const timer = setInterval(() => { refresh(); }, 60000);
    context.subscriptions.push({ dispose: () => clearInterval(timer) });
    context.subscriptions.push(statusBar, treeView, decorationManager);
    refresh();
}
function deactivate() { }
//# sourceMappingURL=extension.js.map