# Contributing to Python Scaffolding

Thank you for your interest in contributing! This document provides guidelines and instructions for contributing to this project.

## Table of Contents
- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Workflow](#development-workflow)
- [Code Standards](#code-standards)
- [Testing](#testing)
- [Pull Request Process](#pull-request-process)

## Code of Conduct

This project adheres to a code of professional conduct. By participating, you are expected to:
- Be respectful and inclusive
- Focus on constructive feedback
- Accept responsibility and learn from mistakes
- Prioritize what's best for the community

## Getting Started

### Prerequisites
- Python 3.13 or higher
- uv (recommended) or pip
- Git
- Familiarity with Clean Architecture principles

### Setup
1. Fork the repository
2. Clone your fork:
   ```bash
   git clone https://github.com/YOUR_USERNAME/python-scaffolding.git
   cd python-scaffolding
   ```
3. Set up the development environment:
   ```bash
   uv venv
   source .venv/bin/activate
   uv sync
   ```
4. Create a branch for your work:
   ```bash
   git checkout -b feature/your-feature-name
   ```

## Development Workflow

We follow a structured planning and development workflow:

### 1. Planning Phase
Before writing code, create planning documents in `dev/active/[feature-name]/`:

- **[feature-name]-plan.md**: Technical Design Specification
  - Overview and goals
  - Technical approach
  - Architecture decisions
  - Implementation details

- **[feature-name]-context.md**: Context and dependencies
  - Why this feature is needed
  - Dependencies and integration points
  - Files to be modified

- **[feature-name]-tasks.md**: Implementation checklist
  - Breakdown of tasks
  - Testing requirements
  - Documentation updates

### 2. Development Phase
- Write tests first (TDD approach)
- Implement the feature following Clean Architecture
- Keep commits small and focused
- Write clear commit messages

### 3. Documentation Phase
Update the `docs/` directory as needed:
- Update relevant guides if adding new concepts
- Update architecture docs if changing design
- Update API documentation if modifying endpoints

### 4. Completion Phase
- Ensure all tests pass
- Run code quality checks
- Update CHANGELOG if applicable
- Move planning docs from `dev/active/` to `dev/archive/`

## Code Standards

### Clean Architecture
Follow the three-layer architecture:
- **Domain**: Pure business logic, no framework dependencies
- **Application**: Use cases that orchestrate domain logic
- **Infrastructure**: Framework-specific code (FastAPI, SQLAlchemy, etc.)

### Python Style
- Follow PEP 8 style guide
- Use type hints for all function signatures
- Write docstrings for all public APIs
- Keep functions small and focused

### Code Quality Tools
Before submitting, run:
```bash
# Linting
ruff check .

# Formatting
ruff format .

# Type checking
mypy src/

# Tests
pytest
```

### Example Code Style
```python
from dataclasses import dataclass
from uuid import UUID

@dataclass
class User:
    """Represents a user in the system.

    Attributes:
        id: Unique identifier for the user
        email: User's email address
        name: User's full name
    """
    id: UUID
    email: str
    name: str

    def change_email(self, new_email: str) -> None:
        """Update the user's email address.

        Args:
            new_email: The new email address

        Raises:
            ValueError: If email format is invalid
        """
        if '@' not in new_email:
            raise ValueError("Invalid email format")
        self.email = new_email
```

## Testing

### Testing Requirements
- Write tests for all new code
- Maintain minimum 80% code coverage
- Follow the testing pyramid (more unit tests, fewer integration/E2E tests)

### Test Organization
```
tests/
├── unit/          # Fast, isolated tests
├── integration/   # Tests with database/external dependencies
└── e2e/          # End-to-end API tests
```

### Running Tests
```bash
# All tests
pytest

# Specific test type
pytest tests/unit
pytest tests/integration
pytest tests/e2e

# With coverage
pytest --cov=src --cov-report=html
```

### Writing Tests
Use the Arrange-Act-Assert pattern:
```python
def test_user_change_email():
    # Arrange
    user = User(id=uuid4(), email="old@example.com", name="Test")

    # Act
    user.change_email("new@example.com")

    # Assert
    assert user.email == "new@example.com"
```

## Pull Request Process

### Before Submitting
1. Ensure all tests pass
2. Run code quality checks (ruff, mypy)
3. Update documentation
4. Rebase on latest main
5. Write clear commit messages

### PR Guidelines
- Use a descriptive title
- Reference related issues
- Describe what changed and why
- Include screenshots for UI changes
- List testing performed

### PR Template
```markdown
## Description
[Brief description of changes]

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## Testing
- [ ] Unit tests added/updated
- [ ] Integration tests added/updated
- [ ] Manual testing performed

## Checklist
- [ ] Code follows project style guidelines
- [ ] Self-review completed
- [ ] Comments added for complex code
- [ ] Documentation updated
- [ ] No new warnings generated
- [ ] Tests pass locally
```

### Review Process
- At least one approval required
- All CI checks must pass
- Address review comments
- Maintain professional discussion

## Git Commit Messages

### Format
```
<type>: <subject>

<body>

<footer>
```

### Types
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting)
- `refactor`: Code refactoring
- `test`: Test additions/changes
- `chore`: Build process or auxiliary tool changes

### Example
```
feat: add user authentication

Implement JWT-based authentication for API endpoints.
- Add User entity to domain layer
- Create authentication use case
- Implement JWT token generation

Closes #123
```

## Questions or Issues?

- Check existing issues and discussions
- Review documentation in `docs/`
- Ask in pull request comments
- Open a new issue for bugs or feature requests

## License

By contributing, you agree that your contributions will be licensed under the same license as the project.
