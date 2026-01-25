# WAT Framework Project

A reliable AI system built on the **WAT architecture**: Workflows, Agents, Tools.

## Architecture

This framework separates concerns to maximize reliability:

- **Workflows** (Layer 1): Markdown SOPs in [workflows/](workflows/) that define what needs to be done
- **Agents** (Layer 2): AI decision-maker that reads workflows and orchestrates tools
- **Tools** (Layer 3): Python scripts in [tools/](tools/) that execute deterministic operations

**Why this works:** By keeping AI focused on reasoning and delegation rather than direct execution, we maintain high accuracy even in multi-step processes.

## Directory Structure

```
.tmp/               # Temporary files (gitignored, regenerated as needed)
tools/              # Python scripts for deterministic execution
workflows/          # Markdown SOPs defining processes
.env                # API keys and credentials (gitignored)
CLAUDE.md           # Agent instructions and framework documentation
```

## Getting Started

1. **Set up environment variables:**
   ```bash
   cp .env.example .env
   # Edit .env with your actual API keys
   ```

2. **Create your first workflow:**
   - Add a markdown file to [workflows/](workflows/)
   - Define: objective, inputs, tools to use, outputs, edge cases

3. **Build tools as needed:**
   - Add Python scripts to [tools/](tools/)
   - Keep them focused, testable, and deterministic

4. **Run with the agent:**
   - Agent reads the workflow
   - Executes tools in the correct sequence
   - Handles failures and asks clarifying questions

## Core Principles

- **Deterministic execution**: Tools handle the actual work, AI handles coordination
- **Self-improvement**: When errors occur, fix the tool and update the workflow
- **Cloud-first deliverables**: Final outputs go to cloud services, local files are just for processing
- **Workflow evolution**: Instructions improve over time through learning and iteration

## Read More

See [CLAUDE.md](CLAUDE.md) for complete agent instructions and operational details.
