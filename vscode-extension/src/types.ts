export interface Scan {
  id: number;
  created_at: string;
  health_grade: string | null;
}

export interface Project {
  id: number;
  name: string;
  path: string;
  ecosystem: string;
  last_scan: Scan | null;
}

export interface PackageRow {
  name: string;
  version: string;
  latest: string | null;
  is_direct: boolean;
  scope: string;
  max_severity: string | null;
  vuln_count: number;
  update_type: string | null;
  license: string | null;
  license_verdict: string | null;
  maintenance_status: string | null;
  usage_status: string | null;
  depth: number;
  dependents: number;
}

export interface Vulnerability {
  osv_id: string;
  cve: string | null;
  severity: string;
  summary: string;
  url: string;
  fixed_version: string | null;
}

export interface PackageDetail extends PackageRow {
  vulnerabilities: Vulnerability[];
  dependency_paths: string[][];
  dependent_names: string[];
}

export interface UsageResult {
  name: string;
  status: string;
  files: string[];
}

export interface FixChange {
  package: string;
  from_spec: string | null;
  to_spec: string;
}

export interface FixEdit {
  file: string;
  content: string;
}

export interface FixPlan {
  changes: FixChange[];
  edits: FixEdit[];
}

export interface FixApplyResult {
  written: boolean;
  changes: FixChange[];
  install_command: string;
}

export interface ImpactFinding {
  severity: string;
  message: string;
  detail: string | null;
  evidence: string;
}

export interface ImpactReport {
  package: string;
  action: string;
  current_version: string | null;
  proposed_version: string | null;
  summary: string;
  findings: ImpactFinding[];
  affected_packages: string[];
  affected_files: string[];
  verification: string[];
  limitations: string[];
}

export interface Decision {
  id: number;
  project_id: number;
  package: string;
  decision: string;
  reason: string | null;
  notes: string | null;
  created_at: string;
}
