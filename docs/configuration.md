# Configuration

OpenExp is configured via environment variables. Copy `.env.example` to `.env` and customize.

## Required

### Qdrant
OpenExp uses Qdrant as its vector database. The setup script starts it via Docker automatically.

| Variable | Default | Description |
|----------|---------|-------------|
| `QDRANT_HOST` | `localhost` | Qdrant server hostname |
| `QDRANT_PORT` | `6333` | Qdrant HTTP port |
| `QDRANT_API_KEY` | *(none)* | Optional: enables Qdrant auth (also passed to Docker) |
| `OPENEXP_COLLECTION` | `openexp_memories` | Collection name in Qdrant |

## Optional

### Data Storage
| Variable | Default | Description |
|----------|---------|-------------|
| `OPENEXP_DATA_DIR` | `~/.openexp/data` | Q-cache, predictions, retrieval logs |
| `OPENEXP_OBSERVATIONS_DIR` | `~/.openexp/observations` | Where hooks write observations |
| `OPENEXP_SESSIONS_DIR` | `~/.openexp/sessions` | Session summary markdown files |

### Embedding Model
| Variable | Default | Description |
|----------|---------|-------------|
| `OPENEXP_EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | FastEmbed model (runs locally) |
| `OPENEXP_EMBEDDING_DIM` | `384` | Vector dimensions |

### LLM Enrichment (Optional)
| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | *(none)* | Enables LLM-based auto-categorization |
| `OPENEXP_ENRICHMENT_MODEL` | `claude-haiku-4-5-20251001` | Model for enrichment |

Without `ANTHROPIC_API_KEY`, memories are stored with basic metadata. The system works well without enrichment — it just won't auto-categorize memory types or extract tags.

### Experiences
| Variable | Default | Description |
|----------|---------|-------------|
| `OPENEXP_EXPERIENCE` | `default` | Active experience profile (`default`, `sales`, `dealflow`, or custom) |
| `OPENEXP_EXPERIENCES_DIR` | `~/.openexp/experiences` | Directory for user-created experience YAML files |

See [Experiences Guide](experiences.md) for details on creating custom experiences.

### Ingest Pipeline
| Variable | Default | Description |
|----------|---------|-------------|
| `OPENEXP_INGEST_BATCH_SIZE` | `50` | Observations per batch during ingest |

### Outcome Resolvers
| Variable | Default | Description |
|----------|---------|-------------|
| `OPENEXP_OUTCOME_RESOLVERS` | *(none)* | Comma-separated list of `module:ClassName` resolvers |
| `OPENEXP_CRM_DIR` | *(none)* | Path to CRM directory (for `CRMCSVResolver`) |

Example `.env` for CRM outcome resolution:
```
OPENEXP_OUTCOME_RESOLVERS=openexp.resolvers.crm_csv:CRMCSVResolver
OPENEXP_CRM_DIR=/path/to/your/crm
```

The CRM directory should contain `relationships/deals.csv` and `relationships/leads.csv`.

## Claude Code Integration

The setup script registers OpenExp in `~/.claude/settings.local.json`:

### MCP Server
```json
{
  "mcpServers": {
    "openexp": {
      "command": "/path/to/openexp/.venv/bin/python3",
      "args": ["-m", "openexp.mcp_server"],
      "cwd": "/path/to/openexp"
    }
  }
}
```

### Hooks
```json
{
  "hooks": {
    "SessionStart": [
      {"type": "command", "command": "/path/to/openexp/openexp/hooks/session-start.sh"}
    ],
    "UserPromptSubmit": [
      {"type": "command", "command": "/path/to/openexp/openexp/hooks/user-prompt-recall.sh"}
    ],
    "PostToolUse": [
      {"type": "command", "command": "/path/to/openexp/openexp/hooks/post-tool-use.sh"}
    ],
    "SessionEnd": [
      {"type": "command", "command": "/path/to/openexp/openexp/hooks/session-end.sh", "timeout": 30}
    ]
  }
}
```
