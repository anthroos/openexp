#!/usr/bin/env python3
"""OpenExp v2 Backlog CLI — Jira-like ticket tracker.

Usage:
    python3 backlog_cli.py                  # show all tickets
    python3 backlog_cli.py --stage 1        # show Stage 1 only
    python3 backlog_cli.py --todo           # show only TODO tickets
    python3 backlog_cli.py start S1-01      # mark ticket IN_PROGRESS
    python3 backlog_cli.py done S1-01       # mark ticket DONE
    python3 backlog_cli.py block S1-01      # mark ticket BLOCKED
"""
import sys
from datetime import date
from pathlib import Path

import yaml


BACKLOG_PATH = Path(__file__).parent / "backlog.yaml"


def load_backlog():
    return yaml.safe_load(BACKLOG_PATH.read_text())


def save_backlog(data):
    BACKLOG_PATH.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True))


def get_all_tickets(data):
    """Yield (stage_key, stage_name, ticket) for all tickets."""
    for key, val in data.items():
        if not key.startswith("stage_"):
            continue
        stage_name = val.get("name", key)
        for ticket in val.get("tickets", []):
            yield key, stage_name, ticket


def find_ticket(data, ticket_id):
    """Find ticket by ID and return (stage_key, ticket_index, ticket)."""
    for key, val in data.items():
        if not key.startswith("stage_"):
            continue
        for i, ticket in enumerate(val.get("tickets", [])):
            if ticket["id"] == ticket_id:
                return key, i, ticket
    return None, None, None


STATUS_COLORS = {
    "DONE": "\033[32m",       # green
    "IN_PROGRESS": "\033[33m", # yellow
    "TODO": "\033[37m",        # white
    "BLOCKED": "\033[31m",     # red
}
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"


def show_board(data, stage_filter=None, status_filter=None):
    """Print Kanban-style board."""
    total = {"TODO": 0, "IN_PROGRESS": 0, "DONE": 0, "BLOCKED": 0}

    for key, val in data.items():
        if not key.startswith("stage_"):
            continue

        stage_num = key.split("_")[1]
        if stage_filter is not None and stage_num != str(stage_filter):
            continue

        stage_name = val.get("name", key)
        stage_status = val.get("status", "TODO")
        tickets = val.get("tickets", [])

        # Count
        for t in tickets:
            s = t.get("status", "TODO")
            total[s] = total.get(s, 0) + 1

        # Filter
        if status_filter:
            tickets = [t for t in tickets if t.get("status", "TODO") == status_filter]
            if not tickets:
                continue

        color = STATUS_COLORS.get(stage_status, "")
        print(f"\n{BOLD}{'=' * 60}")
        print(f"  Stage {stage_num}: {stage_name} [{color}{stage_status}{RESET}{BOLD}]")
        print(f"{'=' * 60}{RESET}")

        dod = val.get("definition_of_done", "")
        if dod and not status_filter:
            print(f"  {DIM}DoD: {dod.strip()[:80]}{RESET}")

        for t in tickets:
            tid = t["id"]
            title = t["title"]
            status = t.get("status", "TODO")
            priority = t.get("priority", "")
            color = STATUS_COLORS.get(status, "")

            pri_str = f" {priority}" if priority else ""
            done_str = f" ({t['done_at']})" if t.get("done_at") else ""

            print(f"  {color}[{status:^11}]{RESET} {BOLD}{tid}{RESET}{pri_str} — {title}{done_str}")

    # Summary
    print(f"\n{DIM}{'─' * 40}")
    print(f"  Total: {sum(total.values())} tickets")
    print(f"  DONE: {total['DONE']}  IN_PROGRESS: {total['IN_PROGRESS']}  TODO: {total['TODO']}  BLOCKED: {total['BLOCKED']}")
    print(f"{'─' * 40}{RESET}")


def update_status(data, ticket_id, new_status):
    """Update ticket status and save."""
    stage_key, idx, ticket = find_ticket(data, ticket_id)
    if ticket is None:
        print(f"Ticket {ticket_id} not found.")
        sys.exit(1)

    old = ticket.get("status", "TODO")
    ticket["status"] = new_status
    if new_status == "DONE":
        ticket["done_at"] = str(date.today())

    data[stage_key]["tickets"][idx] = ticket

    # Auto-update stage status
    tickets = data[stage_key]["tickets"]
    statuses = {t.get("status", "TODO") for t in tickets}
    if statuses == {"DONE"}:
        data[stage_key]["status"] = "DONE"
    elif "IN_PROGRESS" in statuses:
        data[stage_key]["status"] = "IN_PROGRESS"

    save_backlog(data)
    print(f"{ticket_id}: {old} -> {new_status}")


def main():
    data = load_backlog()

    if len(sys.argv) < 2:
        show_board(data)
        return

    cmd = sys.argv[1]

    if cmd == "--todo":
        show_board(data, status_filter="TODO")
    elif cmd == "--progress":
        show_board(data, status_filter="IN_PROGRESS")
    elif cmd == "--done":
        show_board(data, status_filter="DONE")
    elif cmd == "--stage" and len(sys.argv) > 2:
        show_board(data, stage_filter=sys.argv[2])
    elif cmd == "start" and len(sys.argv) > 2:
        update_status(data, sys.argv[2], "IN_PROGRESS")
    elif cmd == "done" and len(sys.argv) > 2:
        update_status(data, sys.argv[2], "DONE")
    elif cmd == "block" and len(sys.argv) > 2:
        update_status(data, sys.argv[2], "BLOCKED")
    elif cmd == "todo" and len(sys.argv) > 2:
        update_status(data, sys.argv[2], "TODO")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
