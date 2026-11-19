export type Severity = "critical" | "high" | "moderate" | "low" | "unknown";
export type UpdateType =
  | "up-to-date"
  | "patch"
  | "minor"
  | "major"
  | "prerelease"
  | "unknown";
export type UsageStatus = "confirmed" | "potential" | "none" | "incomplete";
export type LicenseVerdict = "allowed" | "review" | "restricted";
export type MaintenanceStatus =
  | "active"
  | "low-activity"
  | "concerns"
  | "deprecated"
  | "unknown";

export interface ScanRef {
  id: number;
  created_at: number;
  status: string;
  health_score: number | null;
  health_grade: string | null;
}

export interface Project {
  id: number;
  name: string;
  path: string;
  ecosystem: string;
  created_at: number;
  last_scan: ScanRef | null;
}

export interface PackageRow {
  id: number;
  node_id: string;
  name: string;
  version: string;
  is_direct: boolean;
  scope: string;
  depth: number;
  dependents: number;
  path_count: number;
  update_type: UpdateType | null;
  latest: string | null;
  license: string | null;
  license_family: string | null;
  license_verdict: LicenseVerdict | null;
  usage_status: UsageStatus | null;
  maintenance_status: MaintenanceStatus | null;
  max_severity: Severity | null;
  vuln_count: number;
  data: unknown;
}

export interface Vulnerability {
  id: number;
  package: string;
  installed_version: string;
  osv_id: string;
  cve: string | null;
  severity: Severity;
  summary: string;
  fixed_version: string | null;
  url: string;
  published: string | null;
  data: unknown;
}

export interface PackageDetail extends Omit<PackageRow, "id"> {
  vulnerabilities: Vulnerability[];
  dependency_paths: string[][];
  dependent_names: string[];
}

export interface UsageResult {
  package: string;
  version: string;
  status: UsageStatus;
  confidence: string | null;
  explanation: string | null;
  evidence: { file: string; line: number; method: string; snippet: string }[];
  override: { state: string; note: string | null } | null;
}

export interface FixChange {
  package: string;
  kind: "dependency" | "override";
  field: string | null;
  from_spec: string | null;
  to_spec: string;
}

export interface FixEdit {
  manifest_path: string;
  changes: FixChange[];
  diff: string;
  old_content: string;
  new_content: string;
  install_command: string;
  warnings: string[];
  written: boolean;
  backup_path: string | null;
}

export interface ImpactFinding {
  kind: string;
  severity: "info" | "warning" | "critical";
  evidence: string;
  message: string;
  detail: string | null;
}

export interface ImpactReport {
  package: string;
  current_version: string | null;
  action: string;
  proposed_version: string | null;
  alternative: string | null;
  summary: string;
  dependency_paths: string[][];
  affected_packages: string[];
  affected_files: string[];
  findings: ImpactFinding[];
  verification: string[];
  limitations: string[];
}

export interface Decision {
  id: number;
  package: string;
  current_version: string | null;
  action: string;
  proposed_version: string | null;
  decision: string;
  reason: string | null;
  notes: string | null;
  status: string;
  created_at: number;
  updated_at: number;
}
