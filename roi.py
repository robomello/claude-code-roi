#!/usr/bin/env python3
"""
Claude Code ROI Estimator
=========================
Comprehensive analysis of ALL Claude Code activity -- tokens, code output,
session time, tool usage, agent runs -- compared against API costs and
senior developer benchmarks.

Usage:
    python3 roi.py                        # Full report (all time)
    python3 roi.py --days 30              # Last 30 days
    python3 roi.py --days 7               # Last 7 days
    python3 roi.py --repo ~/DashClaw      # Single repo only
    python3 roi.py --full                 # Detailed tool/agent breakdown
    python3 roi.py --json                 # JSON output
    python3 roi.py --csv                  # CSV export
    python3 roi.py -v                     # Verbose (repo + language breakdown)
    python3 roi.py --senior-rate 120      # Custom hourly rate
    python3 roi.py --subscription 200     # Custom monthly cost
    python3 roi.py --no-sessions          # Skip JSONL parsing (faster)

Author: Roberto de Mello (built with Claude Code)
"""

import argparse
import json
import os
import re
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# -- Configuration ------------------------------------------------------------

SENIOR_DEV_LOC_PER_HOUR = 50       # Production-quality lines/hour (industry avg)
SENIOR_DEV_HOURLY_RATE = 100       # USD/hour (US senior dev average)
CLAUDE_MONTHLY_COST = 200          # Claude Max subscription (USD/month)
IDLE_GAP_MINUTES = 30              # Gap > this = user was idle
MAX_SESSION_HOURS = 24             # Cap per session to filter resumed sessions

HOME = Path.home()
STATS_CACHE = HOME / ".claude" / "stats-cache.json"
PROJECTS_DIR = HOME / ".claude" / "projects"
FILE_HISTORY_DIR = HOME / ".claude" / "file-history"

# Skip these dirs when scanning for repos
SKIP_DIRS = [".nvm", "node_modules", "ai-toolkit", "gpu-burn", ".cache", ".local"]

# Token pricing per 1M tokens (March 2026 API rates)
TOKEN_PRICES = {
    "claude-opus-4-6":            {"input": 15.0,  "output": 75.0,  "cache_read": 1.5,  "cache_create": 18.75},
    "claude-opus-4-5-20251101":   {"input": 15.0,  "output": 75.0,  "cache_read": 1.5,  "cache_create": 18.75},
    "claude-sonnet-4-6":          {"input": 3.0,   "output": 15.0,  "cache_read": 0.3,  "cache_create": 3.75},
    "claude-sonnet-4-5-20250929": {"input": 3.0,   "output": 15.0,  "cache_read": 0.3,  "cache_create": 3.75},
    "claude-haiku-4-5-20251001":  {"input": 0.8,   "output": 4.0,   "cache_read": 0.08, "cache_create": 1.0},
}

# Friendly model names for display
MODEL_NAMES = {
    "claude-opus-4-6": "Opus 4.6",
    "claude-opus-4-5-20251101": "Opus 4.5",
    "claude-sonnet-4-6": "Sonnet 4.6",
    "claude-sonnet-4-5-20250929": "Sonnet 4.5",
    "claude-haiku-4-5-20251001": "Haiku 4.5",
}

# Tool categories for --full mode
TOOL_CATEGORIES = {
    "Code Writing": ["Write", "Edit", "NotebookEdit"],
    "Code Reading": ["Read", "Grep", "Glob"],
    "System Ops": ["Bash"],
    "Web Research": ["WebSearch", "WebFetch"],
    "Planning": ["TodoRead", "TodoWrite", "EnterPlanMode", "ExitPlanMode",
                 "TaskCreate", "TaskUpdate", "TaskGet", "TaskList"],
    "Agents": ["Agent"],
}

# Bash command classification prefixes
BASH_CATEGORIES = {
    "docker": ["docker", "docker-compose"],
    "git": ["git"],
    "package_mgmt": ["npm", "npx", "yarn", "pip", "pip3", "pipx", "apt", "apt-get"],
    "network": ["curl", "wget", "aria2c", "ssh", "scp"],
    "filesystem": ["cd", "ls", "cat", "find", "wc", "du", "df", "mkdir",
                   "rm", "cp", "mv", "chmod", "chown", "ln", "touch"],
    "scripting": ["python", "python3", "node", "bash", "sh"],
}


# -- Git Analysis -------------------------------------------------------------

def discover_repos():
    """Find all git repos under home directory (max depth 3)."""
    repos = []
    try:
        result = subprocess.run(
            ["find", str(HOME), "-maxdepth", "3", "-name", ".git", "-type", "d"],
            capture_output=True, text=True, timeout=15
        )
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            repo_path = str(Path(line).parent)
            if any(s in repo_path for s in SKIP_DIRS):
                continue
            repos.append(repo_path)
    except Exception:
        pass
    return sorted(repos)


def get_claude_commits(repo_path, since_date=None, until_date=None):
    """Get all Claude-authored commits with LOC stats."""
    cmd = [
        "git", "-C", repo_path, "log", "--all",
        "--grep=Co-Authored-By: Claude",
        "--shortstat", "--format=%H|%ai|%s"
    ]
    if since_date:
        cmd.append(f"--since={since_date}")
    if until_date:
        cmd.append(f"--until={until_date}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return []
    except Exception:
        return []

    commits = []
    lines = result.stdout.strip().split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if "|" in line and len(line.split("|")) >= 3:
            parts = line.split("|", 2)
            commit_hash = parts[0]
            date_str = parts[1].strip()
            subject = parts[2].strip()

            insertions = deletions = files_changed = 0

            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines):
                stat_line = lines[j].strip()
                if "file" in stat_line and "changed" in stat_line:
                    m = re.search(r"(\d+) files? changed", stat_line)
                    if m:
                        files_changed = int(m.group(1))
                    m = re.search(r"(\d+) insertions?\(\+\)", stat_line)
                    if m:
                        insertions = int(m.group(1))
                    m = re.search(r"(\d+) deletions?\(-\)", stat_line)
                    if m:
                        deletions = int(m.group(1))
                    i = j

            try:
                commit_date = datetime.strptime(date_str[:19], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                commit_date = None

            commits.append({
                "hash": commit_hash[:8],
                "date": commit_date,
                "date_str": date_str[:10],
                "subject": subject,
                "insertions": insertions,
                "deletions": deletions,
                "files_changed": files_changed,
                "repo": os.path.basename(repo_path),
            })
        i += 1

    return commits


def get_file_types(repo_path, since_date=None, until_date=None):
    """Get file type breakdown for Claude commits."""
    cmd = [
        "git", "-C", repo_path, "log", "--all",
        "--grep=Co-Authored-By: Claude",
        "--numstat", "--format="
    ]
    if since_date:
        cmd.append(f"--since={since_date}")
    if until_date:
        cmd.append(f"--until={until_date}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception:
        return {}

    ext_counts = defaultdict(lambda: {"added": 0, "removed": 0})
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) == 3:
            try:
                added = int(parts[0])
                removed = int(parts[1])
            except ValueError:
                continue
            ext = Path(parts[2]).suffix or "(no ext)"
            ext_counts[ext]["added"] += added
            ext_counts[ext]["removed"] += removed

    return dict(ext_counts)


# -- Session Analysis ---------------------------------------------------------

def load_stats_cache():
    """Load Claude Code stats cache."""
    if not STATS_CACHE.exists():
        return None
    try:
        with open(STATS_CACHE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def calculate_session_time(days=None, since_date=None, until_date=None):
    """
    Calculate active Claude session time from JSONL session files.
    Uses gap-based filtering: gaps > IDLE_GAP_MINUTES are excluded.
    """
    if not PROJECTS_DIR.exists():
        return {"active_hours": 0, "active_sessions": 0, "sessions_analyzed": 0}

    cutoff = None
    end_cutoff = None
    if since_date:
        cutoff = datetime.strptime(since_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if until_date:
            end_cutoff = datetime.strptime(until_date, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, tzinfo=timezone.utc
            )
    elif days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    total_active_seconds = 0
    session_count = 0
    sessions_analyzed = 0

    for proj_dir in PROJECTS_DIR.iterdir():
        if not proj_dir.is_dir():
            continue
        for jf in proj_dir.glob("*.jsonl"):
            if cutoff:
                try:
                    mtime = datetime.fromtimestamp(
                        os.path.getmtime(jf), tz=timezone.utc
                    )
                    if mtime < cutoff:
                        continue
                except OSError:
                    continue

            timestamps = []
            try:
                with open(jf) as f:
                    for line in f:
                        try:
                            obj = json.loads(line)
                            ts = obj.get("timestamp", "")
                            if ts and obj.get("type") in (
                                "user", "assistant", "progress"
                            ):
                                timestamps.append(ts)
                        except (json.JSONDecodeError, KeyError):
                            pass
            except (OSError, PermissionError):
                continue

            if len(timestamps) < 2:
                continue

            sessions_analyzed += 1

            parsed = []
            for ts in timestamps:
                try:
                    t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if cutoff and t < cutoff:
                        continue
                    if end_cutoff and t > end_cutoff:
                        continue
                    parsed.append(t)
                except ValueError:
                    pass

            if len(parsed) < 2:
                continue

            parsed.sort()

            active_seconds = 0
            for k in range(1, len(parsed)):
                gap = (parsed[k] - parsed[k - 1]).total_seconds()
                if gap <= IDLE_GAP_MINUTES * 60:
                    active_seconds += gap

            active_seconds = min(active_seconds, MAX_SESSION_HOURS * 3600)

            if active_seconds > 0:
                total_active_seconds += active_seconds
                session_count += 1

    return {
        "active_hours": total_active_seconds / 3600,
        "active_sessions": session_count,
        "sessions_analyzed": sessions_analyzed,
    }


# -- Token & Cost Analysis ----------------------------------------------------

def calculate_api_cost_per_model(stats):
    """Calculate API cost broken down by model."""
    if not stats:
        return {}

    result = {}
    for model, usage in stats.get("modelUsage", {}).items():
        prices = _get_model_prices(model)

        input_cost = usage.get("inputTokens", 0) / 1_000_000 * prices["input"]
        output_cost = usage.get("outputTokens", 0) / 1_000_000 * prices["output"]
        cache_read_cost = usage.get("cacheReadInputTokens", 0) / 1_000_000 * prices["cache_read"]
        cache_create_cost = usage.get("cacheCreationInputTokens", 0) / 1_000_000 * prices["cache_create"]

        result[model] = {
            "input_tokens": usage.get("inputTokens", 0),
            "output_tokens": usage.get("outputTokens", 0),
            "cache_read_tokens": usage.get("cacheReadInputTokens", 0),
            "cache_create_tokens": usage.get("cacheCreationInputTokens", 0),
            "input_cost": input_cost,
            "output_cost": output_cost,
            "cache_read_cost": cache_read_cost,
            "cache_create_cost": cache_create_cost,
            "total_cost": input_cost + output_cost + cache_read_cost + cache_create_cost,
        }

    return result



def calculate_cache_efficiency(stats):
    """Calculate prompt cache hit rate and savings."""
    if not stats:
        return {"hit_rate_pct": 0, "savings_usd": 0, "cache_read_tokens": 0,
                "cache_create_tokens": 0, "fresh_input_tokens": 0}

    total_cache_read = 0
    total_cache_create = 0
    total_fresh_input = 0
    total_savings = 0.0

    for model, usage in stats.get("modelUsage", {}).items():
        prices = _get_model_prices(model)
        cache_read = usage.get("cacheReadInputTokens", 0)
        cache_create = usage.get("cacheCreationInputTokens", 0)
        fresh_input = usage.get("inputTokens", 0)

        total_cache_read += cache_read
        total_cache_create += cache_create
        total_fresh_input += fresh_input

        # Savings = what cache reads would cost at full input price minus actual cache price
        savings = (cache_read / 1_000_000) * (prices["input"] - prices["cache_read"])
        total_savings += savings

    total_all = total_cache_read + total_cache_create + total_fresh_input
    hit_rate = (total_cache_read / total_all * 100) if total_all > 0 else 0

    return {
        "hit_rate_pct": hit_rate,
        "savings_usd": total_savings,
        "cache_read_tokens": total_cache_read,
        "cache_create_tokens": total_cache_create,
        "fresh_input_tokens": total_fresh_input,
    }


def _get_model_prices(model):
    """Get pricing for a model, with fallback matching."""
    prices = TOKEN_PRICES.get(model)
    if not prices:
        for key, p in TOKEN_PRICES.items():
            if model.startswith(key.rsplit("-", 1)[0]):
                prices = p
                break
    if not prices:
        prices = TOKEN_PRICES["claude-opus-4-6"]
    return prices


# -- Agent Activity -----------------------------------------------------------

def count_agents(days=None, since_date=None, until_date=None):
    """Count subagent invocations from directory structure (fast, no JSONL parsing)."""
    if not PROJECTS_DIR.exists():
        return {"total_invocations": 0, "sessions_with_agents": 0}

    cutoff = None
    end_cutoff = None
    if since_date:
        cutoff = datetime.strptime(since_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if until_date:
            end_cutoff = datetime.strptime(until_date, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, tzinfo=timezone.utc
            )
    elif days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    total_agents = 0
    sessions_with_agents = 0

    for proj_dir in PROJECTS_DIR.iterdir():
        if not proj_dir.is_dir():
            continue
        for session_dir in proj_dir.iterdir():
            if not session_dir.is_dir():
                continue
            subagents_dir = session_dir / "subagents"
            if not subagents_dir.exists():
                continue

            try:
                mtime = datetime.fromtimestamp(
                    os.path.getmtime(subagents_dir), tz=timezone.utc
                )
                if cutoff and mtime < cutoff:
                    continue
                if end_cutoff and mtime > end_cutoff:
                    continue
            except OSError:
                continue

            agent_files = list(subagents_dir.glob("agent-*.jsonl"))
            if agent_files:
                total_agents += len(agent_files)
                sessions_with_agents += 1

    return {
        "total_invocations": total_agents,
        "sessions_with_agents": sessions_with_agents,
    }


# -- File History (Non-Git Edits) ---------------------------------------------

def count_file_edits():
    """Count non-git file edits from file-history directory."""
    if not FILE_HISTORY_DIR.exists():
        return {"sessions_with_edits": 0, "total_versions": 0, "unique_files": 0}

    sessions = 0
    total_versions = 0
    unique_hashes = set()

    for session_dir in FILE_HISTORY_DIR.iterdir():
        if not session_dir.is_dir():
            continue
        sessions += 1
        for entry in session_dir.iterdir():
            name = entry.name
            if "@v" in name:
                total_versions += 1
                file_hash = name.split("@v")[0]
                unique_hashes.add(file_hash)

    return {
        "sessions_with_edits": sessions,
        "total_versions": total_versions,
        "unique_files": len(unique_hashes),
    }


# -- Productivity Patterns ----------------------------------------------------

def get_productivity_patterns(stats, days=None, since_date=None, until_date=None):
    """Extract productivity patterns from stats cache."""
    if not stats:
        return {}

    daily_activity = stats.get("dailyActivity", [])
    if since_date:
        daily_activity = [d for d in daily_activity if d["date"] >= since_date]
        if until_date:
            daily_activity = [d for d in daily_activity if d["date"] <= until_date]
    elif days:
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        daily_activity = [d for d in daily_activity if d["date"] >= since]

    if not daily_activity:
        return {}

    # Busiest day
    busiest = max(daily_activity, key=lambda d: d.get("messageCount", 0))
    busiest_weekday = datetime.strptime(busiest["date"], "%Y-%m-%d").strftime("%A")

    # Peak hour
    hour_counts = stats.get("hourCounts", {})
    if hour_counts:
        peak_hour = max(hour_counts.items(), key=lambda x: int(x[1]))
        peak_hour_num = int(peak_hour[0])
        peak_label = datetime.strptime(str(peak_hour_num), "%H").strftime("%-I %p")
    else:
        peak_hour_num = 0
        peak_label = "N/A"

    total_msgs = sum(d.get("messageCount", 0) for d in daily_activity)
    days_active = len(daily_activity)

    return {
        "peak_hour": peak_hour_num,
        "peak_hour_label": peak_label,
        "peak_hour_sessions": int(peak_hour[1]) if hour_counts else 0,
        "busiest_date": busiest["date"],
        "busiest_weekday": busiest_weekday,
        "busiest_messages": busiest.get("messageCount", 0),
        "avg_messages_per_day": total_msgs // max(days_active, 1),
        "days_active": days_active,
    }


# -- Tool Usage (--full mode, parses JSONL) ------------------------------------

def parse_tool_usage(days=None, since_date=None, until_date=None):
    """Parse JSONL session files for tool_use breakdown. Only with --full."""
    if not PROJECTS_DIR.exists():
        return {"tool_counts": {}, "categories": {}, "bash_subcategories": {}}

    cutoff = None
    end_cutoff = None
    if since_date:
        cutoff = datetime.strptime(since_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if until_date:
            end_cutoff = datetime.strptime(until_date, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, tzinfo=timezone.utc
            )
    elif days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    tool_counts = defaultdict(int)
    bash_commands = []

    for proj_dir in PROJECTS_DIR.iterdir():
        if not proj_dir.is_dir():
            continue
        for jf in proj_dir.glob("*.jsonl"):
            try:
                mtime = datetime.fromtimestamp(
                    os.path.getmtime(jf), tz=timezone.utc
                )
                if cutoff and mtime < cutoff:
                    continue
                if end_cutoff and mtime > end_cutoff:
                    continue
            except OSError:
                continue

            try:
                with open(jf) as f:
                    for line in f:
                        # Fast-path: skip lines without tool_use
                        if '"tool_use"' not in line:
                            continue
                        try:
                            obj = json.loads(line)
                            if obj.get("type") != "assistant":
                                continue
                            content = obj.get("message", {}).get("content", [])
                            for block in content:
                                if block.get("type") == "tool_use":
                                    name = block.get("name", "unknown")
                                    tool_counts[name] += 1
                                    if name == "Bash":
                                        cmd = block.get("input", {}).get("command", "")
                                        if cmd:
                                            bash_commands.append(cmd)
                        except (json.JSONDecodeError, KeyError, TypeError):
                            pass
            except (OSError, PermissionError):
                continue

    # Classify bash commands
    bash_cats = defaultdict(int)
    for cmd in bash_commands:
        first_token = cmd.strip().split()[0] if cmd.strip() else ""
        classified = False
        for cat, prefixes in BASH_CATEGORIES.items():
            if first_token in prefixes:
                bash_cats[cat] += 1
                classified = True
                break
        if not classified:
            bash_cats["other"] += 1

    # Build category summary
    categories = {}
    for cat_name, tool_names in TOOL_CATEGORIES.items():
        count = sum(tool_counts.get(t, 0) for t in tool_names)
        if count > 0:
            categories[cat_name] = count

    return {
        "tool_counts": dict(tool_counts),
        "categories": categories,
        "bash_subcategories": dict(bash_cats),
    }


# -- Formatters ---------------------------------------------------------------

def fmt_num(n):
    return f"{n:,}"


def fmt_hours(h):
    if h < 1:
        return f"{h * 60:.0f} min"
    if h < 24:
        return f"{h:.1f}h"
    days = h / 24
    if days < 7:
        return f"{days:.1f} days ({h:.0f}h)"
    weeks = days / 7
    return f"{weeks:.1f} weeks ({h:.0f}h)"


def fmt_money(m):
    if abs(m) >= 1000:
        return f"${m:,.0f}"
    return f"${m:,.2f}"


def fmt_tokens(n):
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


# -- Report Output ------------------------------------------------------------

def generate_report(args):
    """Generate and print the full ROI report."""
    since_date = None
    until_date = None
    period_label = "All Time"
    if args.since:
        since_date = args.since
        until_date = args.until
        if until_date:
            period_label = f"{since_date} to {until_date}"
        else:
            period_label = f"Since {since_date}"
    elif args.days:
        since_date = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")
        period_label = f"Last {args.days} Days"

    # -- Discover and scan repos --
    if args.repo:
        repos = [os.path.expanduser(args.repo)]
    else:
        repos = discover_repos()

    if not args.json and not args.csv:
        print(f"\n  Scanning {len(repos)} repos...", end="", flush=True)

    # -- Collect git data --
    all_commits = []
    repo_stats = {}
    all_file_types = defaultdict(lambda: {"added": 0, "removed": 0})

    for repo in repos:
        commits = get_claude_commits(repo, since_date, until_date)
        if commits:
            total_ins = sum(c["insertions"] for c in commits)
            total_del = sum(c["deletions"] for c in commits)
            repo_stats[os.path.basename(repo)] = {
                "path": repo,
                "commits": len(commits),
                "insertions": total_ins,
                "deletions": total_del,
                "net": total_ins - total_del,
                "files_changed": sum(c["files_changed"] for c in commits),
                "first_commit": min(c["date_str"] for c in commits if c["date_str"]),
                "last_commit": max(c["date_str"] for c in commits if c["date_str"]),
            }
            all_commits.extend(commits)

            ft = get_file_types(repo, since_date, until_date)
            for ext, counts in ft.items():
                all_file_types[ext]["added"] += counts["added"]
                all_file_types[ext]["removed"] += counts["removed"]

    if not args.json and not args.csv:
        print(f" {len(repo_stats)} have Claude commits")

    has_commits = len(all_commits) > 0

    # -- Aggregate LOC --
    total_insertions = sum(c["insertions"] for c in all_commits)
    total_deletions = sum(c["deletions"] for c in all_commits)
    total_net = total_insertions - total_deletions
    total_loc_touched = total_insertions + total_deletions
    total_commits = len(all_commits)
    total_files = sum(c["files_changed"] for c in all_commits)
    repos_touched = len(repo_stats)

    dates = [c["date"] for c in all_commits if c["date"]]
    if dates:
        first_date = min(dates)
        last_date = max(dates)
        calendar_days = (last_date - first_date).days + 1
    else:
        first_date = last_date = None
        calendar_days = 1

    # -- Session timing --
    if not args.no_sessions:
        if not args.json and not args.csv:
            print("  Analyzing sessions...", end="", flush=True)
        session_data = calculate_session_time(args.days, since_date, until_date)
        if not args.json and not args.csv:
            print(f" {session_data['active_sessions']} active sessions")
    else:
        session_data = {"active_hours": 0, "active_sessions": 0, "sessions_analyzed": 0}

    claude_hours = session_data["active_hours"]

    # -- Stats cache --
    stats = load_stats_cache()
    daily_activity = stats.get("dailyActivity", []) if stats else []
    if since_date and daily_activity:
        daily_activity = [d for d in daily_activity if d["date"] >= since_date]
    if until_date and daily_activity:
        daily_activity = [d for d in daily_activity if d["date"] <= until_date]

    total_messages = sum(d.get("messageCount", 0) for d in daily_activity)
    total_sessions = sum(d.get("sessionCount", 0) for d in daily_activity)
    total_tool_calls = sum(d.get("toolCallCount", 0) for d in daily_activity)
    days_active = len(daily_activity)

    # -- Token & cost analysis --
    model_costs = calculate_api_cost_per_model(stats) if stats else {}
    api_cost_total = sum(m["total_cost"] for m in model_costs.values())

    # Scale for time period (when filtering by --days or --since)
    is_filtered = args.days or args.since
    if is_filtered and stats:
        all_messages = stats.get("totalMessages", 1)
        scale_factor = total_messages / max(all_messages, 1)
        api_cost = api_cost_total * scale_factor
    else:
        api_cost = api_cost_total
        scale_factor = 1.0

    # Total tokens (scaled by period if --days)
    total_output_tokens = int(sum(m.get("output_tokens", 0) for m in model_costs.values()) * scale_factor)
    total_input_tokens = int(sum(m.get("input_tokens", 0) for m in model_costs.values()) * scale_factor)
    total_cache_read = int(sum(m.get("cache_read_tokens", 0) for m in model_costs.values()) * scale_factor)
    total_cache_create = int(sum(m.get("cache_create_tokens", 0) for m in model_costs.values()) * scale_factor)
    total_all_tokens = total_input_tokens + total_output_tokens + total_cache_read + total_cache_create

    # Scale per-model costs for display (immutable -- build new dict)
    if scale_factor < 1.0:
        model_costs = {
            model: {
                k: (int(v * scale_factor) if k.endswith("_tokens") else v * scale_factor)
                for k, v in costs.items()
            }
            for model, costs in model_costs.items()
        }

    # -- Cache efficiency --
    cache_data = calculate_cache_efficiency(stats)
    if scale_factor < 1.0:
        cache_data = {
            "hit_rate_pct": cache_data["hit_rate_pct"],
            "savings_usd": cache_data["savings_usd"] * scale_factor,
            "cache_read_tokens": int(cache_data["cache_read_tokens"] * scale_factor),
            "cache_create_tokens": int(cache_data["cache_create_tokens"] * scale_factor),
            "fresh_input_tokens": int(cache_data["fresh_input_tokens"] * scale_factor),
        }

    # -- Agent activity --
    if not args.json and not args.csv:
        print("  Counting agents...", end="", flush=True)
    agent_data = count_agents(args.days, since_date, until_date)
    if not args.json and not args.csv:
        print(f" {agent_data['total_invocations']} invocations")

    # -- File history --
    file_edit_data = count_file_edits()

    # -- Productivity patterns --
    patterns = get_productivity_patterns(stats, args.days, since_date, until_date)

    # -- Tool usage (--full only) --
    tool_data = None
    if args.full:
        if not args.json and not args.csv:
            print("  Parsing tool usage (full scan)...", end="", flush=True)
        tool_data = parse_tool_usage(args.days, since_date, until_date)
        if not args.json and not args.csv:
            total_tools = sum(tool_data["tool_counts"].values())
            print(f" {fmt_num(total_tools)} tool calls")

    # -- Calculations --
    # Senior dev hours: LOC-based estimate OR Claude's active hours, whichever is higher.
    # If Claude spent 3 hours on research/debugging with no commits, a senior dev
    # would need at least those same 3 hours at $100/hr.
    loc_hours = total_loc_touched / SENIOR_DEV_LOC_PER_HOUR if has_commits else 0
    senior_hours = max(loc_hours, claude_hours)
    senior_cost = senior_hours * SENIOR_DEV_HOURLY_RATE

    # Subscription cost for period
    if args.since:
        s = datetime.strptime(args.since, "%Y-%m-%d")
        u = datetime.strptime(args.until, "%Y-%m-%d") if args.until else datetime.now()
        range_days = (u - s).days + 1
        sub_cost = CLAUDE_MONTHLY_COST * (range_days / 30)
    elif args.days:
        sub_cost = CLAUDE_MONTHLY_COST * (args.days / 30)
    else:
        sub_cost = CLAUDE_MONTHLY_COST * max(1, calendar_days / 30)

    # Effective hourly rate: what you actually pay per hour of Claude work
    claude_hourly_rate = sub_cost / max(claude_hours, 0.1)

    # Multipliers
    time_mult = senior_hours / max(claude_hours, 0.1)
    cost_mult = senior_cost / max(sub_cost, 1)
    hourly_mult = SENIOR_DEV_HOURLY_RATE / max(claude_hourly_rate, 0.01)
    time_saved = senior_hours - claude_hours
    time_saved_pct = (time_saved / max(senior_hours, 0.1)) * 100

    # -- CSV output --
    if args.csv:
        print("repo,commits,insertions,deletions,net_loc,files_changed,first_commit,last_commit")
        for name, st in sorted(repo_stats.items(), key=lambda x: -x[1]["insertions"]):
            print(f"{name},{st['commits']},{st['insertions']},{st['deletions']},"
                  f"{st['net']},{st['files_changed']},{st['first_commit']},{st['last_commit']}")
        print(f"TOTAL,{total_commits},{total_insertions},{total_deletions},"
              f"{total_net},{total_files},,")
        return

    # -- JSON output --
    if args.json:
        report = {
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "period": period_label,
            "since": since_date,
            "until": until_date,
            "calendar_days": calendar_days,
            "code_output": {
                "total_commits": total_commits,
                "repos_touched": repos_touched,
                "insertions": total_insertions,
                "deletions": total_deletions,
                "net_lines": total_net,
                "total_loc_touched": total_loc_touched,
                "files_changed": total_files,
                "first_date": str(first_date)[:10] if first_date else None,
                "last_date": str(last_date)[:10] if last_date else None,
            },
            "token_usage": {
                "total_tokens": total_all_tokens,
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "cache_read_tokens": total_cache_read,
                "cache_create_tokens": total_cache_create,
                "per_model": {
                    MODEL_NAMES.get(m, m): v for m, v in model_costs.items()
                },
            },
            "cache_efficiency": cache_data,
            "time_comparison": {
                "claude_active_hours": round(claude_hours, 1),
                "senior_dev_hours": round(senior_hours, 1),
                "time_saved_hours": round(time_saved, 1),
                "time_saved_pct": round(time_saved_pct, 1),
                "speed_multiplier": round(time_mult, 1),
            },
            "cost_comparison": {
                "subscription_usd": round(sub_cost, 2),
                "claude_effective_hourly_rate": round(claude_hourly_rate, 2),
                "senior_dev_hourly_rate": SENIOR_DEV_HOURLY_RATE,
                "hourly_cost_multiplier": round(hourly_mult, 1),
                "api_equivalent_usd": round(api_cost, 2),
                "senior_dev_usd": round(senior_cost, 2),
                "savings_vs_api": round(api_cost - sub_cost, 2),
                "savings_vs_senior": round(senior_cost - sub_cost, 2),
                "roi_multiplier": round(cost_mult, 1),
            },
            "agent_activity": agent_data,
            "file_edits_non_git": file_edit_data,
            "productivity_patterns": patterns,
            "session_stats": {
                "total_messages": total_messages,
                "total_sessions": total_sessions,
                "total_tool_calls": total_tool_calls,
                "days_active": days_active,
            },
            "assumptions": {
                "senior_rate_usd_hr": SENIOR_DEV_HOURLY_RATE,
                "senior_loc_per_hr": SENIOR_DEV_LOC_PER_HOUR,
                "subscription_monthly_usd": CLAUDE_MONTHLY_COST,
            },
            "repos": repo_stats,
            "daily_activity": daily_activity,
            "daily_model_tokens": [
                d for d in (stats.get("dailyModelTokens", []) if stats else [])
                if (not since_date or d["date"] >= since_date)
                and (not until_date or d["date"] <= until_date)
            ],
            "hour_counts": stats.get("hourCounts", {}) if stats else {},
        }
        if tool_data:
            report["tool_usage"] = tool_data
        print(json.dumps(report, indent=2, default=str))
        return

    # -- Formatted terminal report --
    W = 66

    def sep(char="="):
        print(f"  {char * (W - 2)}")

    def section(title):
        print()
        sep("-")
        print(f"  {title}")
        sep("-")

    def row(label, value, indent=4):
        pad = " " * indent
        dots = "." * max(1, W - indent - len(label) - len(str(value)) - 2)
        print(f"{pad}{label} {dots} {value}")

    # Header
    print()
    sep()
    print(f"  {'CLAUDE CODE ROI REPORT':^{W - 2}}")
    if first_date and last_date:
        sub = f"{first_date.strftime('%b %d, %Y')} - {last_date.strftime('%b %d, %Y')} ({calendar_days}d)"
    else:
        sub = period_label
    print(f"  {sub:^{W - 2}}")
    sep()

    # Executive Summary -- the manager slide
    savings_vs_api = api_cost - sub_cost

    section("EXECUTIVE SUMMARY")

    print()
    print(f"    WORK DELIVERED")
    if has_commits:
        row("Lines of code written", fmt_num(total_insertions), 6)
        row("Lines of code removed", fmt_num(total_deletions), 6)
        row("Net new lines of code", fmt_num(total_net), 6)
        row("Files changed", fmt_num(total_files), 6)
        row("Commits", fmt_num(total_commits), 6)
        row("Repositories", str(repos_touched), 6)
    row("Messages exchanged", fmt_num(total_messages), 6)
    row("Sessions", fmt_num(total_sessions), 6)
    row("Tool executions", fmt_num(total_tool_calls), 6)
    row("Subagent invocations", fmt_num(agent_data["total_invocations"]), 6)
    row("Non-git files edited", fmt_num(file_edit_data["unique_files"]), 6)
    row("Tokens processed", fmt_tokens(total_all_tokens), 6)
    row("Days active", str(days_active), 6)

    print()
    print(f"    WHAT THIS WORK WOULD COST")
    print()
    print(f"      {'Option':<34} {'Total Cost':>14} {'Per Hour':>12}")
    print(f"      {'':_<34} {'':_>14} {'':_>12}")
    print(f"      {'Hire a senior developer':<34} {fmt_money(senior_cost):>14} {'$' + str(SENIOR_DEV_HOURLY_RATE) + '/hr':>12}")
    print(f"      {'Claude API (pay-per-token)':<34} {fmt_money(api_cost):>14} {'--':>12}")
    print(f"      {'Claude Max subscription (actual)':<34} {fmt_money(sub_cost):>14} {fmt_money(claude_hourly_rate) + '/hr':>12}")
    print()
    if has_commits:
        row("Saved vs senior developer", fmt_money(senior_cost - sub_cost), 6)
    if savings_vs_api > 0:
        row("Saved vs API pay-per-token", fmt_money(savings_vs_api), 6)
    print()
    print(f"      * Effective rate = ${CLAUDE_MONTHLY_COST}/mo subscription")
    print(f"        / {fmt_hours(claude_hours)} active work = {fmt_money(claude_hourly_rate)}/hr")

    # Code Output
    if has_commits:
        section("CODE OUTPUT")
        row("Lines of code added", fmt_num(total_insertions))
        row("Lines of code removed", fmt_num(total_deletions))
        row("Net new lines of code", fmt_num(total_net))
        row("Total lines of code touched", fmt_num(total_loc_touched))
        row("Files changed", fmt_num(total_files))
        row("Commits", fmt_num(total_commits))
        row("Repositories", str(repos_touched))

    # Time Comparison
    section("TIME COMPARISON")
    row("Claude active work time", fmt_hours(claude_hours))
    if has_commits:
        claude_loc_per_hr = total_loc_touched / max(claude_hours, 0.1)
        row("Claude lines of code/hr", fmt_num(int(claude_loc_per_hr)))
        row("Senior dev lines of code/hr", fmt_num(SENIOR_DEV_LOC_PER_HOUR))
        row("Same work by senior dev", fmt_hours(senior_hours))
        row("Time saved", f"{fmt_hours(time_saved)} ({time_saved_pct:.0f}%)")
        row("Speed multiplier", f"{time_mult:.1f}x faster")

    # Cost Comparison (detailed)
    section("COST BREAKDOWN")
    row(f"Claude Max subscription ({calendar_days}d)", fmt_money(sub_cost))
    row(f"Effective hourly rate", f"{fmt_money(claude_hourly_rate)}/hr")
    print(f"      ({fmt_money(sub_cost)} / {fmt_hours(claude_hours)} active work)")
    row(f"Senior developer ({SENIOR_DEV_HOURLY_RATE}/hr)", fmt_money(senior_cost))
    if has_commits:
        print(f"      ({fmt_hours(senior_hours)} at ${SENIOR_DEV_HOURLY_RATE}/hr)")
    row("API pay-per-token equivalent", fmt_money(api_cost))
    print()
    row("Hourly rate comparison", f"{hourly_mult:.0f}x cheaper than senior dev")
    if has_commits and cost_mult > 0:
        row("Total cost ROI", f"{cost_mult:.0f}x return on investment")

    # Token Usage
    section("TOKEN USAGE (API COST BASIS)")
    row("Total tokens processed", fmt_tokens(total_all_tokens))
    row("Output tokens (Claude wrote)", fmt_tokens(total_output_tokens))
    row("Input tokens (fresh)", fmt_tokens(total_input_tokens))
    row("Cache read tokens", fmt_tokens(total_cache_read))
    row("Cache create tokens", fmt_tokens(total_cache_create))
    print()
    print(f"    {'Model':<18} {'Output':>10} {'API Cost':>12}")
    print(f"    {'':_<18} {'':_>10} {'':_>12}")
    for model, costs in sorted(model_costs.items(), key=lambda x: -x[1]["total_cost"]):
        name = MODEL_NAMES.get(model, model[:18])
        print(f"    {name:<18} {fmt_tokens(costs['output_tokens']):>10} "
              f"{fmt_money(costs['total_cost']):>12}")

    # Cache Efficiency
    section("CACHE EFFICIENCY")
    row("Cache hit rate", f"{cache_data['hit_rate_pct']:.1f}%")
    row("Tokens served from cache", fmt_tokens(cache_data["cache_read_tokens"]))
    row("Money saved by caching", fmt_money(cache_data["savings_usd"]))

    # Agent Activity
    section("AGENT ACTIVITY")
    row("Subagent invocations", fmt_num(agent_data["total_invocations"]))
    row("Sessions with agents", fmt_num(agent_data["sessions_with_agents"]))

    # Tool Usage (--full only)
    if tool_data:
        section("TOOL USAGE BREAKDOWN")
        sorted_tools = sorted(tool_data["tool_counts"].items(), key=lambda x: -x[1])
        show_tools = sorted_tools[:15] if not args.verbose else sorted_tools
        print(f"    {'Tool':<22} {'Count':>8} {'%':>7}")
        print(f"    {'':_<22} {'':_>8} {'':_>7}")
        total_tc = sum(tool_data["tool_counts"].values())
        for tool_name, count in show_tools:
            pct = count / max(total_tc, 1) * 100
            print(f"    {tool_name:<22} {fmt_num(count):>8} {pct:>6.1f}%")
        if not args.verbose and len(sorted_tools) > 15:
            print(f"    ... and {len(sorted_tools) - 15} more tools (use -v)")

        if tool_data["categories"]:
            print()
            print(f"    {'Category':<22} {'Count':>8}")
            print(f"    {'':_<22} {'':_>8}")
            for cat, count in sorted(tool_data["categories"].items(), key=lambda x: -x[1]):
                print(f"    {cat:<22} {fmt_num(count):>8}")

        if tool_data["bash_subcategories"]:
            print()
            print(f"    {'Bash Category':<22} {'Count':>8}")
            print(f"    {'':_<22} {'':_>8}")
            for cat, count in sorted(tool_data["bash_subcategories"].items(), key=lambda x: -x[1]):
                print(f"    {cat:<22} {fmt_num(count):>8}")

    # Non-Git File Edits
    section("NON-GIT FILE EDITS")
    row("Sessions with edits", str(file_edit_data["sessions_with_edits"]))
    row("Total file versions", fmt_num(file_edit_data["total_versions"]))
    row("Unique files edited", fmt_num(file_edit_data["unique_files"]))

    # Productivity Patterns
    if patterns:
        section("PRODUCTIVITY PATTERNS")
        row("Peak hour", f"{patterns['peak_hour_label']} ({patterns['peak_hour_sessions']} sessions)")
        row("Busiest day", f"{patterns['busiest_date']} ({patterns['busiest_weekday']})")
        row("Messages that day", fmt_num(patterns["busiest_messages"]))
        row("Avg messages/active day", fmt_num(patterns["avg_messages_per_day"]))
        row("Days active", str(patterns["days_active"]))

    # Claude Activity
    section("CLAUDE ACTIVITY")
    row("Messages", fmt_num(total_messages))
    row("Sessions", fmt_num(total_sessions))
    row("Tool calls", fmt_num(total_tool_calls))
    row("Days active", str(days_active))
    if days_active > 0:
        row("Avg messages/day", fmt_num(total_messages // days_active))
        row("Avg tool calls/day", fmt_num(total_tool_calls // days_active))

    # Per-Repo Breakdown
    sorted_repos = sorted(repo_stats.items(), key=lambda x: -x[1]["insertions"])
    show_repos = sorted_repos if args.verbose else sorted_repos[:5]

    if show_repos:
        section("TOP REPOS BY LOC")
        hdr = f"    {'Repo':<22} {'Commits':>7} {'Added':>9} {'Removed':>9} {'Net':>9}"
        print(hdr)
        print(f"    {'':_<22} {'':_>7} {'':_>9} {'':_>9} {'':_>9}")
        for name, st in show_repos:
            print(f"    {name:<22} {st['commits']:>7} "
                  f"{'+' + fmt_num(st['insertions']):>9} "
                  f"{'-' + fmt_num(st['deletions']):>9} "
                  f"{fmt_num(st['net']):>9}")
        if not args.verbose and len(sorted_repos) > 5:
            print(f"    ... and {len(sorted_repos) - 5} more repos (use -v to see all)")

    # File Types (verbose only)
    if args.verbose and all_file_types:
        section("FILE TYPES")
        sorted_types = sorted(
            all_file_types.items(), key=lambda x: x[1]["added"], reverse=True
        )[:15]
        print(f"    {'Extension':<12} {'Added':>10} {'Removed':>10} {'Total':>10}")
        print(f"    {'':_<12} {'':_>10} {'':_>10} {'':_>10}")
        for ext, counts in sorted_types:
            total = counts["added"] + counts["removed"]
            print(f"    {ext:<12} {'+' + fmt_num(counts['added']):>10} "
                  f"{'-' + fmt_num(counts['removed']):>10} "
                  f"{fmt_num(total):>10}")

    # Bottom Line
    print()
    sep("=")
    print(f"  BOTTOM LINE")
    if has_commits:
        print(f"  Claude delivered {fmt_num(total_loc_touched)} lines of code")
        print(f"  across {repos_touched} repositories in {fmt_hours(claude_hours)}.")
        print()
        print(f"  A senior developer at ${SENIOR_DEV_HOURLY_RATE}/hr would need")
        print(f"  {fmt_hours(senior_hours)} and cost {fmt_money(senior_cost)}.")
        print()
    print(f"  Actual cost: {fmt_money(sub_cost)} (subscription)")
    print(f"  Effective rate: {fmt_money(claude_hourly_rate)}/hr vs ${SENIOR_DEV_HOURLY_RATE}/hr")
    if has_commits:
        print(f"  Net savings: {fmt_money(senior_cost - sub_cost)}")
    sep("=")
    print()
    print(f"  Assumptions:")
    print(f"    Senior dev: {SENIOR_DEV_LOC_PER_HOUR} lines of code/hr, ${SENIOR_DEV_HOURLY_RATE}/hr")
    print(f"    (includes design, testing, debugging, code review, documentation)")
    print(f"    Subscription: ${CLAUDE_MONTHLY_COST}/mo (Claude Max plan)")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"  Generated: {ts}")
    print()


# -- Main ---------------------------------------------------------------------

def main():
    global SENIOR_DEV_HOURLY_RATE, SENIOR_DEV_LOC_PER_HOUR, CLAUDE_MONTHLY_COST

    parser = argparse.ArgumentParser(
        description="Claude Code ROI Estimator - Comprehensive activity & cost analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 roi.py                    Full report (all time)
  python3 roi.py --days 30          Last 30 days
  python3 roi.py --full             Detailed tool/agent breakdown
  python3 roi.py --repo ~/DashClaw  Single repo only
  python3 roi.py --json             JSON for pipelines
  python3 roi.py --csv              CSV for spreadsheets
  python3 roi.py -v                 Verbose breakdown
  python3 roi.py --no-sessions      Skip JSONL parsing (faster)
        """,
    )
    parser.add_argument("--days", type=int, help="Only analyze last N days")
    parser.add_argument("--since", type=str, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--until", type=str, help="End date (YYYY-MM-DD)")
    parser.add_argument("--repo", type=str, help="Analyze a single repo")
    parser.add_argument("--full", action="store_true",
                        help="Full tool usage breakdown (parses JSONL, slower)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--csv", action="store_true", help="CSV output")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Show full repo and file type breakdown")
    parser.add_argument("--senior-rate", type=int, default=SENIOR_DEV_HOURLY_RATE,
                        help=f"Senior dev hourly rate (default: ${SENIOR_DEV_HOURLY_RATE})")
    parser.add_argument("--senior-loc", type=int, default=SENIOR_DEV_LOC_PER_HOUR,
                        help=f"Senior dev LOC/hr (default: {SENIOR_DEV_LOC_PER_HOUR})")
    parser.add_argument("--subscription", type=int, default=CLAUDE_MONTHLY_COST,
                        help=f"Monthly cost (default: ${CLAUDE_MONTHLY_COST})")
    parser.add_argument("--no-sessions", action="store_true",
                        help="Skip JSONL session analysis (faster)")

    args = parser.parse_args()

    SENIOR_DEV_HOURLY_RATE = args.senior_rate
    SENIOR_DEV_LOC_PER_HOUR = args.senior_loc
    CLAUDE_MONTHLY_COST = args.subscription

    generate_report(args)


if __name__ == "__main__":
    main()
