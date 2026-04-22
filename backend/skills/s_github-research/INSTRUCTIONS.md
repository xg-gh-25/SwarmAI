# GitHub Research

Produce comprehensive, well-sourced research reports on GitHub repositories and open-source projects by combining API data, web intelligence, and commit-level analysis.

## Output Location

Save reports to:
```
~/.swarm-ai/SwarmWS/Knowledge/Reports/YYYY-MM-DD-<repo-name>-research.md
```

## Workflow

### Step 1: Scope the Research

Identify from the user's request:

| Dimension | Example |
|-----------|---------|
| **Target** | repo URL, org name, or project name |
| **Focus** | health, architecture, adoption, security, comparison |
| **Depth** | overview (5 min) vs. full report (20+ min) |
| **Audience** | "should we adopt this?" vs. "how does it work internally?" |

If any dimension is ambiguous, infer from context. Only ask if truly unclear.

### Step 2: Round 1 -- GitHub API Data Extraction

Use `gh` CLI to pull structured data. Run these in parallel where possible:

```bash
# Core metadata
gh api repos/{owner}/{repo}
gh api repos/{owner}/{repo}/contributors?per_page=10
gh api repos/{owner}/{repo}/releases?per_page=5

# Activity signals
gh api repos/{owner}/{repo}/stats/commit_activity
gh api repos/{owner}/{repo}/stats/contributors
gh api repos/{owner}/{repo}/pulls?state=open&per_page=5
gh api repos/{owner}/{repo}/issues?state=open&per_page=10

# Community health
gh api repos/{owner}/{repo}/community/profile
gh api repos/{owner}/{repo}/license
```

Extract and record:

| Metric | What to Capture |
|--------|----------------|
| Stars / Forks / Watchers | Popularity signal |
| Open Issues / PRs | Maintenance health |
| Last commit date | Activity recency |
| Release cadence | Maturity signal |
| Top contributors | Bus factor |
| License | Adoption risk |

### Step 3: Round 2 -- Web Discovery

Use WebFetch to search for external intelligence:

| Search Query Pattern | Purpose |
|---------------------|---------|
| `"{repo name}" site:news.ycombinator.com` | Community sentiment |
| `"{repo name}" benchmark OR comparison` | Performance context |
| `"{repo name}" migration OR "moved to"` | Ecosystem shifts |
| `"{repo name}" vulnerability OR CVE` | Security signals |
| `"{repo name}" production OR "in production"` | Real-world adoption |

**Source priority ranking:**
1. Official docs and changelogs
2. Technical blog posts from known engineers
3. Conference talks / papers
4. News articles
5. Community discussions (HN, Reddit, Discord)
6. Social media

### Step 4: Round 3 -- Deep Investigation

Based on gaps found in Rounds 1-2, conduct targeted deep dives:

- **Architecture**: Read key source files, trace entry points, identify patterns
- **Dependencies**: Check dependency tree for risks (`gh api repos/{owner}/{repo}/dependency-graph/sbom`)
- **Governance**: Who merges PRs? How are decisions made? Check GOVERNANCE.md, CONTRIBUTING.md
- **Funding**: Check for sponsors, OpenCollective, corporate backing
- **Competitors**: Search for alternatives, compare feature matrices

### Step 5: Round 4 -- Commit & Community Deep Dive

```bash
# Recent commit patterns
gh api repos/{owner}/{repo}/commits?per_page=30

# PR merge velocity
gh api repos/{owner}/{repo}/pulls?state=closed&per_page=20&sort=updated

# Issue response time (sample)
gh api repos/{owner}/{repo}/issues?state=closed&per_page=10&sort=updated
```

Analyze:
- Commit frequency trends (accelerating, stable, declining?)
- PR review turnaround time
- Issue triage responsiveness
- Contributor diversity (single maintainer vs. broad team)

### Step 6: Synthesis & Report Generation

**Never write from general knowledge alone.** Every claim must trace to data collected in Rounds 1-4.

#### Report Template

```markdown
# {Repo Name} -- Research Report

**Date:** YYYY-MM-DD
**Target:** {owner}/{repo}
**Focus:** {focus area}

## Executive Summary

{3-5 sentences: what this project is, its current state, and the key finding}

## Key Metrics

| Metric | Value | Signal |
|--------|-------|--------|
| Stars | X | {interpretation} |
| ... | ... | ... |

## Timeline

{Mermaid timeline diagram of major releases/events}

## Technical Analysis

### Architecture
{Key architectural decisions, patterns, tech stack}

### Code Quality Signals
{Test coverage, CI status, linting, type safety}

### Dependency Health
{Major dependencies, supply chain risks}

## Community & Governance

### Contributor Analysis
{Bus factor, top contributors, corporate vs individual}

### Maintenance Health
{Issue response time, PR velocity, release cadence}

## Adoption & Ecosystem

{Who uses it, integrations, competing projects}

## Strengths & Risks

| Strengths | Risks |
|-----------|-------|
| {strength 1} | {risk 1} |
| ... | ... |

## Confidence Scores

| Claim Category | Confidence | Basis |
|---------------|------------|-------|
| Metrics | High | Direct API data |
| Architecture | Medium/High | Source code analysis |
| Adoption | Medium | Web sources, may be incomplete |
| Trajectory | Low/Medium | Extrapolation from trends |

## Sources

1. {source with URL}
2. ...
```

#### Confidence Scoring

| Level | Criteria |
|-------|----------|
| **High** | Direct API data or official docs, verified by multiple sources |
| **Medium** | Reliable secondary sources, consistent across 2+ references |
| **Low** | Single source, community discussion, or extrapolation |

Flag any claim where confidence is Low. Never present Low-confidence claims as facts.

### Step 7: Completeness Check

Before delivering, verify:

- [ ] All 4 research rounds completed
- [ ] Every factual claim has a source
- [ ] Confidence scores assigned to each section
- [ ] Strengths AND risks identified (no one-sided report)
- [ ] Executive summary is self-contained (reader can stop there)
- [ ] Mermaid diagrams render correctly
- [ ] Report saved to Knowledge/Reports/

## Adaptation for Orgs & Comparisons

**For organizations:** Replace repo-level API calls with org-level. Add section on repo portfolio and team structure.

**For comparisons:** Run Rounds 1-4 for each project. Add comparison matrix and recommendation section with explicit tradeoffs.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `gh` not authenticated | Fall back to WebFetch for public GitHub pages |
| API rate limited | Space out requests, prioritize highest-value endpoints |
| Repo is private | Note limitation, research only public signals |
| Very new repo (<6 months) | Reduce weight on trajectory analysis, note limited history |

