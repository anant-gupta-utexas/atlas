# Python Scaffolding

A modern Python project template following Clean Architecture principles.

## Quick Start

### Prerequisites
- Python 3.13 or higher
- [uv](https://github.com/astral-sh/uv) (recommended) or pip

### Installation

```bash
git clone <repository-url>
cd python-scaffolding

uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

uv sync

python main.py
```

## Development

### Code Quality

```bash
ruff check .
ruff format .
mypy src/
```

### Testing

```bash
pytest
pytest --cov=src --cov-report=html
```

## Documentation

- **[CLAUDE.md](CLAUDE.md)**: Project signpost, structure, and workflow
- **[Getting Started Guide](docs/3_guides/getting_started.md)**: Detailed setup instructions
- **[Core Concepts](docs/3_guides/core_concepts.md)**: Clean Architecture principles
- **[System Design](docs/2_architecture/system_design.md)**: High-level architecture
- **[Testing Guide](docs/4_testing/index.md)**: Testing strategy and best practices

## Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for details.

## License

MIT License - see the [LICENSE](LICENSE) file for details.
