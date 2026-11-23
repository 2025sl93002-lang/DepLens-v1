"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.DepTreeProvider = exports.DepNode = void 0;
const vscode = require("vscode");
const client_1 = require("./client");
function healthColor(pkg) {
    if (pkg.vuln_count > 0 || pkg.max_severity === "critical" || pkg.max_severity === "high") {
        return new vscode.ThemeColor("charts.red");
    }
    if (pkg.update_type === "major" || pkg.max_severity === "moderate" || pkg.maintenance_status === "deprecated") {
        return new vscode.ThemeColor("charts.yellow");
    }
    return new vscode.ThemeColor("charts.green");
}
function nodeContextValue(pkg) {
    if (pkg.update_type && pkg.update_type !== "up-to-date" && pkg.latest) {
        return "deplensPackageUpgradable";
    }
    return "deplensPackage";
}
class GroupNode extends vscode.TreeItem {
    constructor(label, children) {
        super(label, vscode.TreeItemCollapsibleState.Expanded);
        this.children = children;
        this.contextValue = "depGroup";
        this.description = `${children.length}`;
    }
}
class DepNode extends vscode.TreeItem {
    constructor(pkg, scanId) {
        super(pkg.name, vscode.TreeItemCollapsibleState.None);
        this.pkg = pkg;
        this.scanId = scanId;
        this.description = pkg.version + (pkg.latest && pkg.latest !== pkg.version ? ` → ${pkg.latest}` : "");
        this.iconPath = new vscode.ThemeIcon(pkg.vuln_count > 0 ? "shield" : "package", healthColor(pkg));
        this.contextValue = nodeContextValue(pkg);
        this.tooltip = this.buildTooltip();
        this.command = {
            command: "deplens.openDetail",
            title: "Open detail",
            arguments: [scanId, pkg.name],
        };
    }
    buildTooltip() {
        const md = new vscode.MarkdownString();
        md.appendMarkdown(`**${this.pkg.name}** \`${this.pkg.version}\`\n\n`);
        if (this.pkg.vuln_count > 0) {
            md.appendMarkdown(`⚠️ ${this.pkg.vuln_count} vulnerabilit${this.pkg.vuln_count === 1 ? "y" : "ies"} (${this.pkg.max_severity})\n\n`);
        }
        if (this.pkg.latest && this.pkg.latest !== this.pkg.version) {
            md.appendMarkdown(`📦 Latest: \`${this.pkg.latest}\` (${this.pkg.update_type})\n\n`);
        }
        if (this.pkg.usage_status && this.pkg.usage_status !== "confirmed") {
            md.appendMarkdown(`🔍 Usage: ${this.pkg.usage_status}\n\n`);
        }
        if (this.pkg.license) {
            md.appendMarkdown(`🔖 ${this.pkg.license}\n`);
        }
        return md;
    }
}
exports.DepNode = DepNode;
class DepTreeProvider {
    constructor() {
        this._onDidChangeTreeData = new vscode.EventEmitter();
        this.onDidChangeTreeData = this._onDidChangeTreeData.event;
        this.scanId = null;
        this._projectId = null;
        this.cachedPackages = [];
        this.groups = [];
    }
    get projectId() { return this._projectId; }
    get currentScanId() { return this.scanId; }
    async load(scanId, projectId) {
        this.scanId = scanId;
        this._projectId = projectId;
        await this.refresh();
    }
    async refresh() {
        if (this.scanId === null) {
            return;
        }
        const pkgs = await (0, client_1.getPackages)(this.scanId);
        this.cachedPackages = pkgs;
        this.rebuildGroups();
        this._onDidChangeTreeData.fire();
    }
    rebuildGroups() {
        const direct = this.cachedPackages.filter((p) => p.is_direct && p.scope !== "dev");
        const dev = this.cachedPackages.filter((p) => p.is_direct && p.scope === "dev");
        const transitive = this.cachedPackages.filter((p) => !p.is_direct);
        const makeNodes = (list) => list.map((p) => new DepNode(p, this.scanId));
        this.groups = [];
        if (direct.length > 0) {
            this.groups.push(new GroupNode("Direct", makeNodes(direct)));
        }
        if (dev.length > 0) {
            this.groups.push(new GroupNode("Dev", makeNodes(dev)));
        }
        if (transitive.length > 0) {
            this.groups.push(new GroupNode(`Transitive (${transitive.length})`, makeNodes(transitive)));
        }
    }
    clear() {
        this.scanId = null;
        this._projectId = null;
        this.cachedPackages = [];
        this.groups = [];
        this._onDidChangeTreeData.fire();
    }
    getTreeItem(element) { return element; }
    getChildren(element) {
        if (!element) {
            return this.groups;
        }
        if (element instanceof GroupNode) {
            return element.children;
        }
        return [];
    }
}
exports.DepTreeProvider = DepTreeProvider;
//# sourceMappingURL=DepTreeProvider.js.map