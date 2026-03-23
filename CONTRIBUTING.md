# Contributing to OpenExp

Thanks for your interest in contributing! Here's how to get started.

## Development Setup

```bash
# Clone and set up
git clone https://github.com/anthroos/openexp.git
cd openexp
./setup.sh

# Activate the venv
source .venv/bin/activate
```

Prerequisites: Python 3.11+, Docker (for Qdrant), jq.

## Workflow

1. **Branch from main:** `git checkout -b feat/your-feature`
2. **Make changes**
3. **Run tests:** `pytest tests/ -v`
4. **Check for personal data:** `grep -rn "sk-ant\|api_key.*=.*['\"]sk" $(git ls-files)`
5. **Push and open a PR**
6. **Squash merge** after review

## Running Tests

```bash
# All tests
.venv/bin/python3 -m pytest tests/ -v

# Specific test file
.venv/bin/python3 -m pytest tests/test_q_value.py -v
```

## Code Guidelines

- No hardcoded paths — use environment variables or relative paths
- No personal data in code (API keys, usernames, company names)
- `.env` is gitignored — never commit it
- Keep dependencies minimal — avoid adding new packages without discussion

## Areas Where Help Is Welcome

- **Reward signals** — beyond commits/PRs, what indicates a productive session?
- **Compaction** — merging duplicate or outdated memories automatically
- **Multi-project learning** — sharing relevant context across projects
- **Benchmarks** — measuring retrieval quality improvement over time
- **More lifecycle transitions** — automated contradiction detection

## Questions?

Open an issue or start a discussion. We're happy to help you get oriented.
