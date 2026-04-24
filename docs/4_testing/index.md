# Testing Documentation

## Testing Philosophy
We follow a comprehensive testing strategy that ensures code quality, correctness, and maintainability at all layers of the application.

## Testing Pyramid

```
         ╱╲
        ╱  ╲         E2E Tests (Few)
       ╱____╲        API/Integration Tests
      ╱      ╲
     ╱________╲      Unit Tests (Many)
```

### Unit Tests (Base)
- Test individual functions, methods, and classes in isolation
- Fast execution, no external dependencies
- Focus on Domain and Application layers
- Target: 80%+ coverage

### Integration Tests (Middle)
- Test interaction between components
- May use test databases or in-memory implementations
- Focus on Use Cases and Repository implementations
- Target: Key user flows covered

### E2E Tests (Top)
- Test complete user scenarios through the API
- Use real or test database
- Fewer tests, focus on critical paths
- Target: Main workflows verified

## Test Organization

```
tests/
├── unit/
│   ├── domain/
│   │   ├── test_entities.py
│   │   └── test_value_objects.py
│   └── application/
│       └── test_use_cases.py
├── integration/
│   ├── test_repositories.py
│   └── test_services.py
└── e2e/
    └── test_api.py
```

## Running Tests

### Run All Tests
```bash
pytest
```

### Run Specific Test Types
```bash
pytest tests/unit              # Only unit tests
pytest tests/integration       # Only integration tests
pytest tests/e2e              # Only E2E tests
```

### Run with Coverage
```bash
pytest --cov=src --cov-report=html
# Open htmlcov/index.html to view coverage report
```

### Run Specific Test File
```bash
pytest tests/unit/domain/test_entities.py
```

### Run Specific Test Function
```bash
pytest tests/unit/domain/test_entities.py::test_user_creation
```

## Writing Tests

### Unit Test Example
```python
# tests/unit/domain/test_user.py
import pytest
from uuid import uuid4
from src.domain.entities.user import User

def test_user_creation():
    user = User(
        id=uuid4(),
        email="test@example.com",
        name="Test User"
    )
    assert user.email == "test@example.com"
    assert user.name == "Test User"

def test_user_change_email_valid():
    user = User(id=uuid4(), email="old@example.com", name="Test")
    user.change_email("new@example.com")
    assert user.email == "new@example.com"

def test_user_change_email_invalid():
    user = User(id=uuid4(), email="old@example.com", name="Test")
    with pytest.raises(ValueError, match="Invalid email format"):
        user.change_email("invalid-email")
```

### Integration Test Example
```python
# tests/integration/test_user_repository.py
from src.infrastructure.database.repositories.user_repository_impl import UserRepositoryImpl
from src.domain.entities.user import User
from uuid import uuid4

def test_save_and_retrieve_user(db_session):
    # Arrange
    repo = UserRepositoryImpl(db_session)
    user = User(id=uuid4(), email="test@example.com", name="Test User")

    # Act
    repo.save(user)
    retrieved_user = repo.get_by_id(user.id)

    # Assert
    assert retrieved_user is not None
    assert retrieved_user.email == user.email
    assert retrieved_user.name == user.name
```

### E2E Test Example
```python
# tests/e2e/test_user_api.py
from fastapi.testclient import TestClient

def test_create_user_endpoint(client: TestClient):
    # Act
    response = client.post("/users", json={
        "email": "test@example.com",
        "name": "Test User"
    })

    # Assert
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "test@example.com"
    assert data["name"] == "Test User"
    assert "user_id" in data
```

## Test Fixtures

### Using pytest Fixtures
```python
# tests/conftest.py
import pytest
from fastapi.testclient import TestClient
from src.infrastructure.api.main import app

@pytest.fixture
def client():
    return TestClient(app)

@pytest.fixture
def db_session():
    # Set up test database session
    # Yield session
    # Tear down
    pass
```

## Best Practices

### 1. Arrange-Act-Assert Pattern
```python
def test_example():
    # Arrange: Set up test data
    user = User(id=uuid4(), email="test@example.com", name="Test")

    # Act: Perform the action
    user.change_email("new@example.com")

    # Assert: Verify the result
    assert user.email == "new@example.com"
```

### 2. Test Naming
- Use descriptive names: `test_<method>_<scenario>_<expected_result>`
- Examples:
  - `test_user_creation_with_valid_data_succeeds`
  - `test_change_email_with_invalid_format_raises_error`

### 3. One Assertion Per Test (When Possible)
- Focus on testing one behavior per test
- Makes failures easier to diagnose

### 4. Use Parametrize for Multiple Cases
```python
@pytest.mark.parametrize("email,expected", [
    ("valid@example.com", True),
    ("invalid-email", False),
    ("", False),
])
def test_email_validation(email, expected):
    result = is_valid_email(email)
    assert result == expected
```

### 5. Mock External Dependencies
```python
from unittest.mock import Mock

def test_send_email_use_case():
    # Mock external email service
    email_service = Mock()
    use_case = SendEmailUseCase(email_service)

    use_case.execute("test@example.com", "Hello")

    email_service.send.assert_called_once_with(
        to="test@example.com",
        body="Hello"
    )
```

## Coverage Goals
- **Overall**: Minimum 80% coverage
- **Domain Layer**: Target 95%+ (pure business logic)
- **Application Layer**: Target 90%+ (use cases)
- **Infrastructure Layer**: Target 70%+ (framework code)

## Continuous Integration
All tests run automatically on:
- Pull request creation
- Commits to main branch
- Scheduled nightly builds

Tests must pass before merging to main.

## Further Reading
- [Unit Testing Best Practices](unit_tests.md)
- [Integration Testing Guide](integration_tests.md)
- [E2E Testing Strategies](e2e_tests.md)
