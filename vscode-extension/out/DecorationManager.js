"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.DecorationManager = void 0;
const vscode = require("vscode");
const UNUSED_DECORATION = vscode.window.createTextEditorDecorationType({
    opacity: "0.5",
    after: {
        contentText: "  ⚠ possibly unused",
        color: new vscode.ThemeColor("editorWarning.foreground"),
        fontStyle: "italic",
    },
});
const UNCONFIRMED_DECORATION = vscode.window.createTextEditorDecorationType({
    after: {
        contentText: "  · usage unconfirmed",
        color: new vscode.ThemeColor("editorHint.foreground"),
        fontStyle: "italic",
    },
});
class DecorationManager {
    constructor(getCache) {
        this.getCache = getCache;
        this.disposables = [];
        this.disposables.push(vscode.window.onDidChangeActiveTextEditor((editor) => {
            if (editor) {
                this.update(editor);
            }
        }));
        if (vscode.window.activeTextEditor) {
            this.update(vscode.window.activeTextEditor);
        }
    }
    refresh() {
        if (vscode.window.activeTextEditor) {
            this.update(vscode.window.activeTextEditor);
        }
    }
    update(editor) {
        if (!editor.document.fileName.endsWith("package.json")) {
            editor.setDecorations(UNUSED_DECORATION, []);
            editor.setDecorations(UNCONFIRMED_DECORATION, []);
            return;
        }
        const pkgs = this.getCache();
        if (pkgs.length === 0) {
            return;
        }
        const text = editor.document.getText();
        const unused = [];
        const unconfirmed = [];
        for (const pkg of pkgs) {
            if (!pkg.is_direct) {
                continue;
            }
            if (!pkg.usage_status || pkg.usage_status === "confirmed") {
                continue;
            }
            const range = this.findPackageRange(editor.document, text, pkg.name);
            if (!range) {
                continue;
            }
            if (pkg.usage_status === "none") {
                unused.push({ range, hoverMessage: `DepLens: No usage of ${pkg.name} detected in source files.` });
            }
            else if (pkg.usage_status === "incomplete" || pkg.usage_status === "potential") {
                unconfirmed.push({ range, hoverMessage: `DepLens: Usage of ${pkg.name} could not be fully confirmed.` });
            }
        }
        editor.setDecorations(UNUSED_DECORATION, unused);
        editor.setDecorations(UNCONFIRMED_DECORATION, unconfirmed);
    }
    findPackageRange(document, text, name) {
        const escaped = name.replace(/[@/]/g, "\\$&");
        const re = new RegExp(`"(${escaped})"\\s*:`);
        const lines = text.split("\n");
        let inDeps = false;
        let depDepth = 0;
        let braceDepth = 0;
        for (let i = 0; i < lines.length; i++) {
            const line = lines[i];
            if (/"(dependencies|devDependencies|peerDependencies)"/.test(line)) {
                inDeps = true;
                depDepth = braceDepth;
            }
            braceDepth += (line.match(/{/g) ?? []).length;
            braceDepth -= (line.match(/}/g) ?? []).length;
            if (inDeps && braceDepth <= depDepth) {
                inDeps = false;
            }
            if (inDeps && re.test(line)) {
                const match = re.exec(line);
                if (match) {
                    const start = new vscode.Position(i, line.indexOf(`"${name}"`));
                    const end = new vscode.Position(i, line.indexOf(`"${name}"`) + name.length + 2);
                    return new vscode.Range(start, end);
                }
            }
        }
        return null;
    }
    dispose() {
        UNUSED_DECORATION.dispose();
        UNCONFIRMED_DECORATION.dispose();
        this.disposables.forEach((d) => d.dispose());
    }
}
exports.DecorationManager = DecorationManager;
//# sourceMappingURL=DecorationManager.js.map