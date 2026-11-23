import type { Project, PackageRow, PackageDetail, ImpactReport, FixPlan, FixApplyResult, Decision } from "./types";

const BASE = "http://localhost:8000/api";

async function get<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${BASE}${path}`);
    if (!res.ok) {
      return null;
    }
    return res.json() as T;
  } catch {
    return null;
  }
}

async function post<T>(path: string, body: unknown): Promise<T | null> {
  try {
    const res = await fetch(`${BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      return null;
    }
    return res.json() as T;
  } catch {
    return null;
  }
}

export async function checkHealth(): Promise<boolean> {
  const r = await get<{ status: string }>("/health");
  return r?.status === "ok";
}

export async function getProjects(): Promise<Project[]> {
  const r = await get<{ projects: Project[] }>("/projects");
  return r?.projects ?? [];
}

export async function getPackages(scanId: number): Promise<PackageRow[]> {
  const r = await get<{ packages: PackageRow[] }>(`/scans/${scanId}/packages`);
  return r?.packages ?? [];
}

export async function getPackageDetail(scanId: number, name: string): Promise<PackageDetail | null> {
  return get<PackageDetail>(`/scans/${scanId}/package/${encodeURIComponent(name)}`);
}

export async function getUsage(scanId: number): Promise<{ name: string; status: string; files: string[] }[]> {
  const r = await get<{ usage: { name: string; status: string; files: string[] }[] }>(`/scans/${scanId}/usage`);
  return r?.usage ?? [];
}

export async function getFixPlan(scanId: number, packages?: string[]): Promise<FixPlan | null> {
  return post<FixPlan>(`/scans/${scanId}/fix-plan`, { packages: packages ?? null });
}

export async function applyFix(scanId: number, packages?: string[]): Promise<FixApplyResult | null> {
  return post<FixApplyResult>(`/scans/${scanId}/fix-apply`, { packages: packages ?? null });
}

export async function simulateImpact(
  scanId: number,
  pkg: string,
  action: string,
  proposedVersion?: string
): Promise<ImpactReport | null> {
  return post<ImpactReport>(`/scans/${scanId}/impact`, {
    package: pkg,
    action,
    proposed_version: proposedVersion ?? null,
    online: false,
  });
}

export async function getDecisions(projectId: number): Promise<Decision[]> {
  const r = await get<{ decisions: Decision[] }>(`/projects/${projectId}/decisions`);
  return r?.decisions ?? [];
}
