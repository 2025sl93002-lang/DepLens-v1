# DepLens — Parts Manifest

This repo is built by 4 contributors, each owning one part.
Push your part on a branch, open a PR, and merge in order (1 → 2 → 3 → 4 is safest but
parts 1 & 3 are independent).

The project won't run until all parts are merged.

---

## Part 1 — Backend core  `@person1`
> parsers · semver engine · graph service · scanner · database · CLI

```
backend/deplens/__init__.py
backend/deplens/__main__.py        (CLI entry-point — serve/scan commands)
backend/deplens/main.py            (FastAPI app factory)
backend/deplens/db.py
backend/deplens/scanner.py
backend/deplens/report.py
backend/deplens/semver.py
backend/deplens/parsers/
backend/deplens/services/          (graph, registry, osv, safety, apply_fix)
```

---

## Part 2 — Analyzers & API  `@person2`
> all analyzer modules · all FastAPI routers

```
backend/deplens/analyzers/         (usage, versions, licenses, maintenance, health, impact, remediation)
backend/deplens/api/               (deps, projects, scans, impact, apply_fix, decisions, settings, reports)
```

---

## Part 3 — Frontend  `@person3`
> React / TypeScript UI — all pages, components, state, hooks

```
frontend/src/api/
frontend/src/components/
frontend/src/hooks/
frontend/src/lib/
frontend/src/pages/
frontend/src/state/
frontend/src/types/
frontend/src/main.tsx
frontend/src/App.tsx
```

---

## Part 4 — Tests, docs, CI, fixtures  `@person4`
> backend test suite · docs · GitHub Actions · demo fixture

```
backend/tests/
docs/                              (architecture, scoring-model, roadmap, etc.)
.github/                           (ci.yml, issue templates)
fixtures/demo-project/
```

---

## How to push your part

```bash
# 1. Clone the repo (after person 1 pushes the scaffold)
git clone https://github.com/YOUR_ORG/deplens.git
cd deplens

# 2. Set your GitHub identity so the commit shows your username
git config user.name  "Your Name"
git config user.email "YOUR_USERNAME@users.noreply.github.com"

# 3. Create a branch for your part
git checkout -b part-X-your-name

# 4. Add your files, commit, push, open PR
git add backend/deplens/...   # only your paths
git commit -m "feat: add Part X — <description>"
git push -u origin part-X-your-name
# Then open a PR on GitHub
```

> Tip: use the noreply email above so GitHub links the commit to your profile
> without exposing your real address. Find your username at github.com/settings/emails.
