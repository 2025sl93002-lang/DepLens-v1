import * as vscode from "vscode";
import { PackageRow } from "./types";
import { getPackages } from "./client";

function healthColor(pkg: PackageRow): vscode.ThemeColor {
  if (pkg.vuln_count > 0 || pkg.max_severity === "critical" || pkg.max_severity === "high") {
    return new vscode.ThemeColor("charts.red");
  }
  if (pkg.update_type === "major" || pkg.max_severity === "moderate" || pkg.maintenance_status === "deprecated") {
    return new vscode.ThemeColor("charts.yellow");
  }
  return new vscode.ThemeColor("charts.green");
}

function nodeContextValue(pkg: PackageRow): string {
  if (pkg.update_type && pkg.update_type !== "up-to-date" && pkg.latest) {
    return "deplensPackageUpgradable";
  }
  return "deplensPackage";
}

class GroupNode extends vscode.TreeItem {
  constructor(
    public readonly label: string,
    public readonly children: DepNode[]
  ) {
    super(label, vscode.TreeItemCollapsibleState.Expanded);
    this.contextValue = "depGroup";
    this.description = `${children.length}`;
  }
}

export class DepNode extends vscode.TreeItem {
  constructor(
    public readonly pkg: PackageRow,
    public readonly scanId: number
  ) {
    super(pkg.name, vscode.TreeItemCollapsibleState.None);
    this.description = pkg.version + (pkg.latest && pkg.latest !== pkg.version ? ` → ${pkg.latest}` : "");
    this.iconPath = new vscode.ThemeIcon(
      pkg.vuln_count > 0 ? "shield" : "package",
      healthColor(pkg)
    );
    this.contextValue = nodeContextValue(pkg);
    this.tooltip = this.buildTooltip();
    this.command = {
      command: "deplens.openDetail",
      title: "Open detail",
      arguments: [scanId, pkg.name],
    };
  }

  private buildTooltip(): vscode.MarkdownString {
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

type TreeNode = GroupNode | DepNode;

export class DepTreeProvider implements vscode.TreeDataProvider<TreeNode> {
  private _onDidChangeTreeData = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

  private scanId: number | null = null;
  private _projectId: number | null = null;
  cachedPackages: PackageRow[] = [];
  private groups: GroupNode[] = [];

  get projectId(): number | null { return this._projectId; }
  get currentScanId(): number | null { return this.scanId; }

  async load(scanId: number, projectId: number): Promise<void> {
    this.scanId = scanId;
    this._projectId = projectId;
    await this.refresh();
  }

  async refresh(): Promise<void> {
    if (this.scanId === null) { return; }
    const pkgs = await getPackages(this.scanId);
    this.cachedPackages = pkgs;
    this.rebuildGroups();
    this._onDidChangeTreeData.fire();
  }

  private rebuildGroups(): void {
    const direct = this.cachedPackages.filter((p) => p.is_direct && p.scope !== "dev");
    const dev = this.cachedPackages.filter((p) => p.is_direct && p.scope === "dev");
    const transitive = this.cachedPackages.filter((p) => !p.is_direct);
    const makeNodes = (list: PackageRow[]) => list.map((p) => new DepNode(p, this.scanId!));

    this.groups = [];
    if (direct.length > 0) { this.groups.push(new GroupNode("Direct", makeNodes(direct))); }
    if (dev.length > 0) { this.groups.push(new GroupNode("Dev", makeNodes(dev))); }
    if (transitive.length > 0) { this.groups.push(new GroupNode(`Transitive (${transitive.length})`, makeNodes(transitive))); }
  }

  clear(): void {
    this.scanId = null;
    this._projectId = null;
    this.cachedPackages = [];
    this.groups = [];
    this._onDidChangeTreeData.fire();
  }

  getTreeItem(element: TreeNode): vscode.TreeItem { return element; }

  getChildren(element?: TreeNode): TreeNode[] {
    if (!element) { return this.groups; }
    if (element instanceof GroupNode) { return element.children; }
    return [];
  }
}
