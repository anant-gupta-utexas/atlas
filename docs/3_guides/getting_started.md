# Getting Started

## Prerequisites
- Python 3.13 or higher
- uv (recommended) or pip
- Docker (for local development with docker-compose)
- Git

## Installation

### 1. Clone the Repository
```bash
git clone <repository-url>
cd python-scaffolding
```

### 2. Set Up Python Environment

#### Using uv (recommended)
```bash
# Install uv if you haven't already
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create virtual environment and install dependencies
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
uv sync
```

#### Using pip
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -e .
```

### 3. Set Up Local Environment
```bash
# Copy environment template (if exists)
cp .env.example .env

# Edit .env with your local configuration
```

### 4. Run the Application
```bash
# Run locally
python main.py

# Or with Docker
docker-compose up --build
```

### 5. Run Tests
```bash
pytest
```

## Development Workflow

### Before Starting a New Feature
1. Create a feature branch: `git checkout -b feature/my-feature`
2. Create planning documents in `dev/active/my-feature/`
3. Get your Technical Design Specification (TDS) reviewed

### During Development
1. Write tests first (TDD approach)
2. Implement the feature following Clean Architecture
3. Ensure all tests pass
4. Update documentation in `docs/` as needed

### Before Merging
1. Run full test suite: `pytest`
2. Check code quality: `ruff check .`
3. Format code: `ruff format .`
4. Update CHANGELOG (if applicable)
5. Move feature docs from `dev/active/` to `dev/archive/`

## Project Structure
See [CLAUDE.md](../../CLAUDE.md) for detailed project structure documentation.

## Common Commands

### Testing
```bash
pytest                          # Run all tests
pytest tests/unit              # Run unit tests only
pytest tests/integration       # Run integration tests only
pytest --cov                   # Run with coverage report
```

### Code Quality
```bash
ruff check .                   # Lint code
ruff format .                  # Format code
mypy src/                      # Type checking
```

### Database (if applicable)
```bash
# Add migration commands here when database is set up
```

## Troubleshooting

### Common Issues

#### Virtual Environment Issues
```bash
# Deactivate and recreate virtual environment
deactivate
rm -rf .venv
uv venv
source .venv/bin/activate
uv sync
```

#### Dependency Conflicts
```bash
# Clear cache and reinstall
uv cache clean
uv sync --reinstall
```

## Next Steps
- Read [Core Concepts](core_concepts.md)
- Review [System Design](../2_architecture/system_design.md)
- Check [Testing Guide](../4_testing/index.md)
