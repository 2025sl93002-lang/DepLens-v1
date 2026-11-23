import { Project, PackageRow, PackageDetail, UsageResult, FixEdit, ImpactReport, Decision } from "./types";

const BASE = "http://localhost:8000/api";

async function get<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${BASE}${path}`);
    if (!res.ok) { return null; }
    return res.json() as Promise<T>;
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
    if (!res.ok) { return null; }
    return res.json() as Promise<T>;
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

export async function getPackageDetail(
  scanId: number,
  name: string
): Promise<PackageDetail | null> {
  return get<PackageDetail>(`/scans/${scanId}/package/${encodeURIComponent(name)}`);
}

export async function getUsage(scanId: number): Promise<UsageResult[]> {
  const r = await get<{ usage: UsageResult[] }>(`/scans/${scanId}/usage`);
  return r?.usage ?? [];
}

export async function getFixPlan(
  scanId: number,
  packages?: string[]
): Promise<FixEdit | null> {
  return post<FixEdit>(`/scans/${scanId}/fix-plan`, { packages: packages ?? null });
}

export async function applyFix(
  scanId: number,
  packages?: string[]
): Promise<FixEdit | null> {
  return post<FixEdit>(`/scans/${scanId}/fix-apply`, { packages: packages ?? null });
}

export async function simulateImpact(
  scanId: number,
  pkg: string,
  action: "remove" | "upgrade",
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
