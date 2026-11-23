# DepLens scan — GitHub Action

Run a DepLens dependency scan in CI: detect vulnerable, outdated and
license-risky dependencies, publish findings to the **Security → Code scanning**
tab as SARIF, and optionally **fail the build** on a severity threshold.

## Usage

```yaml
# .github/workflows/deplens.yml
name: DepLens
on:
  pull_request:
  push:
    branches: [main]

jobs:
  dependency-scan:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      security-events: write      # required to upload SARIF
    steps:
      - uses: actions/checkout@v4
      - uses: ./.github/actions/scan
        with:
          path: .
          fail-on: high            # none | low | moderate | high | critical
          report-format: sarif
```

## Inputs

| Input | Default | Description |
|-------|---------|-------------|
| `path` | `.` | Project folder to scan (contains `package.json`). |
| `fail-on` | `none` | Fail the build if a vulnerability at/above this severity is found (`none`/`low`/`moderate`/`high`/`critical`). |
| `offline` | `false` | Skip registry/OSV network calls (lockfile-only). |
| `sarif-file` | `deplens.sarif` | Where to write the SARIF 2.1.0 report. |
| `upload-sarif` | `true` | Upload SARIF to GitHub code scanning (needs `security-events: write`). |
| `python-version` | `3.12` | Python used to run DepLens. |

## Outputs

| Output | Description |
|--------|-------------|
| `sarif-file` | Path to the generated SARIF report. |

## Behaviour

- Writes a SARIF 2.1.0 report (one rule per advisory, one result per vulnerable
  package, GitHub `security-severity` set) and uploads it so findings appear in
  the Security tab.
- A one-line health/vulnerability summary is appended to the workflow
  **job summary** (`$GITHUB_STEP_SUMMARY`).
- With `fail-on` other than `none`, the step exits non-zero when any advisory
  meets the threshold — SARIF is still uploaded (the upload step runs on
  `always()`), so findings are published even on a failing build.

## Optional: post a PR comment

Posting a PR comment needs the **caller's** `pull-requests: write` token and PR
context, so it belongs in your workflow rather than inside this action. Example
using the SARIF the action just produced:

```yaml
      - uses: ./.github/actions/scan
        id: deplens
        with:
          fail-on: none            # don't fail; just report
      - name: Comment dependency summary
        if: github.event_name == 'pull_request'
        uses: actions/github-script@v7
        with:
          script: |
            const fs = require('fs');
            const sarif = JSON.parse(fs.readFileSync('${{ steps.deplens.outputs.sarif-file }}', 'utf8'));
            const results = sarif.runs?.[0]?.results ?? [];
            const by = (lvl) => results.filter(r => r.level === lvl).length;
            const body = [
              '## 🛡️ DepLens dependency scan',
              '',
              `- **${results.length}** advisory finding(s): ` +
              `${by('error')} error, ${by('warning')} warning, ${by('note')} note`,
              '',
              'See the **Security → Code scanning** tab for details.',
            ].join('\n');
            await github.rest.issues.createComment({
              owner: context.repo.owner, repo: context.repo.repo,
              issue_number: context.issue.number, body,
            });
```

## Notes

- DepLens is not published to PyPI, so the action installs the in-repo backend
  (`backend/requirements.txt`) and runs `python -m deplens`. When DepLens is
  published as a standalone action repo, swap the install step for
  `pip install deplens`.
- The scan honours the same engine as the CLI/UI — see the project README.
