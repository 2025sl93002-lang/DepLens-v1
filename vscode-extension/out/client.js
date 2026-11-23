"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.checkHealth = checkHealth;
exports.getProjects = getProjects;
exports.getPackages = getPackages;
exports.getPackageDetail = getPackageDetail;
exports.getUsage = getUsage;
exports.getFixPlan = getFixPlan;
exports.applyFix = applyFix;
exports.simulateImpact = simulateImpact;
exports.getDecisions = getDecisions;
const BASE = "http://localhost:8000/api";
async function get(path) {
    try {
        const res = await fetch(`${BASE}${path}`);
        if (!res.ok) {
            return null;
        }
        return res.json();
    }
    catch {
        return null;
    }
}
async function post(path, body) {
    try {
        const res = await fetch(`${BASE}${path}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        if (!res.ok) {
            return null;
        }
        return res.json();
    }
    catch {
        return null;
    }
}
async function checkHealth() {
    const r = await get("/health");
    return r?.status === "ok";
}
async function getProjects() {
    const r = await get("/projects");
    return r?.projects ?? [];
}
async function getPackages(scanId) {
    const r = await get(`/scans/${scanId}/packages`);
    return r?.packages ?? [];
}
async function getPackageDetail(scanId, name) {
    return get(`/scans/${scanId}/package/${encodeURIComponent(name)}`);
}
async function getUsage(scanId) {
    const r = await get(`/scans/${scanId}/usage`);
    return r?.usage ?? [];
}
async function getFixPlan(scanId, packages) {
    return post(`/scans/${scanId}/fix-plan`, { packages: packages ?? null });
}
async function applyFix(scanId, packages) {
    return post(`/scans/${scanId}/fix-apply`, { packages: packages ?? null });
}
async function simulateImpact(scanId, pkg, action, proposedVersion) {
    return post(`/scans/${scanId}/impact`, {
        package: pkg,
        action,
        proposed_version: proposedVersion ?? null,
        online: false,
    });
}
async function getDecisions(projectId) {
    const r = await get(`/projects/${projectId}/decisions`);
    return r?.decisions ?? [];
}
//# sourceMappingURL=client.js.map