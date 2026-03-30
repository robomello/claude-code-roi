---
name: cost-estimate
description: >
  This skill should be used when the user asks "cost estimate", "ROI",
  "how much code", "productivity report", "lines of code",
  "how much did Claude write", "developer cost comparison", "cost savings",
  "value of Claude", "token usage", "API cost", or "cost-estimate".
  Analyzes ALL Claude Code activity -- tokens, code, time, agents, tools --
  and compares against API costs and senior developer benchmarks.
  DO NOT trigger on: general pricing questions about Claude plans,
  API token cost calculations, questions about other AI tools' pricing.
---

## On Activation
1. Read `learnings.md` in this skill directory before starting work.

# Claude Code ROI Estimator

Comprehensive analysis of ALL Claude Code activity -- tokens, code output,
session time, tool usage, agent runs -- compared against API costs and
senior developer benchmarks.

## Instructions

Run `python3 ~/skills/cost-estimate/roi.py` with the appropriate flags.

### Default: Full report
```bash
python3 ~/skills/cost-estimate/roi.py
```

### Specific time period
```bash
python3 ~/skills/cost-estimate/roi.py --days 30
python3 ~/skills/cost-estimate/roi.py --days 7
```

### Detailed tool/agent breakdown (parses JSONL, slower)
```bash
python3 ~/skills/cost-estimate/roi.py --full
python3 ~/skills/cost-estimate/roi.py --full --days 7 -v
```

### Specific repo
```bash
python3 ~/skills/cost-estimate/roi.py --repo ~/DashClaw -v
```

### JSON output (for sharing, piping, dashboards)
```bash
python3 ~/skills/cost-estimate/roi.py --json
```

### CSV output (for spreadsheets)
```bash
python3 ~/skills/cost-estimate/roi.py --csv
```

### Custom benchmarks
```bash
python3 ~/skills/cost-estimate/roi.py --senior-rate 120 --senior-loc 40 --subscription 200
```

### Faster report (skip session JSONL time analysis)
```bash
python3 ~/skills/cost-estimate/roi.py --no-sessions
```

After running, present the output to the user. If they ask to share it, run with `--json` and save to a file.

## What It Analyzes

1. **Tokens** -- Per-model breakdown (Opus, Sonnet, Haiku) with API cost equivalent
2. **Cache efficiency** -- Hit rate, tokens served from cache, money saved
3. **Lines of code** -- Git commits with `Co-Authored-By: Claude` tag
4. **Session time** -- Active Claude hours from JSONL with idle-gap filtering
5. **Senior dev estimate** -- Equivalent human hours at 50 LOC/hr (configurable)
6. **Cost comparison** -- Subscription vs API pay-per-token vs senior dev
7. **Agent activity** -- Subagent invocations and sessions
8. **Tool usage** -- Breakdown by tool type and bash command category (--full)
9. **Non-git file edits** -- Files changed outside repos
10. **Productivity patterns** -- Peak hours, busiest days

## Pricing & Benchmarks

For current infrastructure costs, API pricing, Etsy fees, and developer rate benchmarks, read `references/pricing-reference.md`.

## Benchmarks

| Benchmark | Default | Flag | Rationale |
|-----------|---------|------|-----------|
| Senior dev LOC/hr | 50 | `--senior-loc` | Production-quality code (not prototype). Industry average for reviewed, tested code. |
| Senior dev rate | $100/hr | `--senior-rate` | US market average for senior software engineers (2026). |
| Subscription | $200/mo | `--subscription` | Claude Max plan cost. |

## Interpreting Results

- **Speed multiplier 2.5x** = Claude produced code 2.5x faster than a senior dev at 50 LOC/hr
- **ROI 5.0x** = The code Claude produced would have cost 5x more if done by a senior dev at $100/hr
- **API savings $X** = You saved $X by using the $200/mo subscription instead of API pay-per-token
- **Cache hit rate 90%** = 90% of input tokens were served from prompt cache at 10x discount

## Data Sources

- Token usage: `~/.claude/stats-cache.json` (per-model, per-day)
- Git repos: Auto-discovered under ~/ (excludes .nvm, node_modules, ai-toolkit, gpu-burn)
- Session data: `~/.claude/projects/*/*.jsonl`
- Agent data: `~/.claude/projects/**/subagents/` directory structure
- File history: `~/.claude/file-history/` directory
- Tool usage: Parsed from JSONL (--full mode only)

## Gotchas

- **No Co-Authored-By tag** -- Commits without the tag aren't counted for code output
- **Token scaling** -- When using --days, token data is scaled from all-time totals using message count ratio (stats-cache doesn't store per-day token breakdowns by type)
- **Idle gap** -- 30-min idle-gap filtering may under-count active time if you take breaks
- **--full is slower** -- Parses all JSONL files. Use --days to limit scope
