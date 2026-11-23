import * as vscode from "vscode";
import { PackageRow, Decision } from "./types";

export class HoverProvider implements vscode.HoverProvider {
  constructor(
    private getCache: () => PackageRow[],
    private getDecisions: () => Decision[]
  ) {}

  provideHover(
    document: vscode.TextDocument,
    position: vscode.Position
  ): vscode.Hover | undefined {
    const pkgName = this.packageNameAtPosition(document, position);
    if (!pkgName) { return undefined; }

    const pkg = this.getCache().find((p) => p.name === pkgName);
    if (!pkg) { return undefined; }

    const md = new vscode.MarkdownString();
    md.isTrusted = true;

    md.appendMarkdown(`### DepLens — ${pkg.name}\n\n`);

    // Version
    const installed = `\`${pkg.version}\``;
    const latest =
      pkg.latest && pkg.latest !== pkg.version
        ? ` → \`${pkg.latest}\` *(${pkg.update_type})*`
        : " *(up to date)*";
    md.appendMarkdown(`**Version:** ${installed}${latest}\n\n`);

    // Security
    if (pkg.vuln_count > 0) {
      md.appendMarkdown(`**⚠️ Vulnerabilities:** ${pkg.vuln_count} (${pkg.max_severity})\n\n`);
    } else {
      md.appendMarkdown(`**Security:** ✅ No known vulnerabilities\n\n`);
    }

    // License
    if (pkg.license) {
      const verdict =
        pkg.license_verdict === "restricted" ? " ⛔" :
        pkg.license_verdict === "review" ? " ⚠️" : "";
      md.appendMarkdown(`**License:** ${pkg.license}${verdict}\n\n`);
    }

    // Usage status (unique to DepLens — RHDA doesn't have this)
    if (pkg.usage_status) {
      const usageIcon =
        pkg.usage_status === "confirmed" ? "✅" :
        pkg.usage_status === "none" ? "⚠️ possibly unused —" :
        pkg.usage_status === "potential" ? "🔍" : "❓";
      md.appendMarkdown(`**Usage:** ${usageIcon} ${pkg.usage_status}\n\n`);
    }

    // Maintenance
    if (pkg.maintenance_status && pkg.maintenance_status !== "active") {
      md.appendMarkdown(`**Maintenance:** ⚠️ ${pkg.maintenance_status}\n\n`);
    }

    // Decision badge (unique to DepLens — RHDA doesn't have this)
    const decision = this.getDecisions().find((d) => d.package === pkg.name);
    if (decision) {
      const decisionIcon =
        decision.decision === "accept" ? "✅" :
        decision.decision === "reject" ? "❌" : "📋";
      md.appendMarkdown(`---\n\n`);
      md.appendMarkdown(`**${decisionIcon} Recorded decision:** ${decision.decision}`);
      if (decision.reason) {
        md.appendMarkdown(` — ${decision.reason}`);
      }
      md.appendMarkdown(`\n\n`);
      if (decision.notes) {
        md.appendMarkdown(`*${decision.notes}*\n\n`);
      }
    }

    return new vscode.Hover(md);
  }

  private packageNameAtPosition(
    document: vscode.TextDocument,
    position: vscode.Position
  ): string | null {
    const text = document.getText();
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(text);
    } catch {
      return null;
    }

    const depSections = ["dependencies", "devDependencies", "peerDependencies", "optionalDependencies"];
    const allDeps = depSections.flatMap((s) => Object.keys((parsed[s] as Record<string, unknown>) ?? {}));

    const range = document.getWordRangeAtPosition(position, /[@\w/.-]+/);
    if (!range) { return null; }
    const word = document.getText(range);
    if (allDeps.includes(word)) { return word; }

    const line = document.lineAt(position.line).text;
    const lineMatch = line.match(/"(@?[\w/.-]+)"\s*:/);
    if (lineMatch && allDeps.includes(lineMatch[1])) {
      return lineMatch[1];
    }
    return null;
  }
}
