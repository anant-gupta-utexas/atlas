# Core Concepts

## Clean Architecture

### What is Clean Architecture?
Clean Architecture is a software design philosophy that separates concerns into layers, with dependencies flowing inward toward the core business logic. This makes the codebase:
- **Testable**: Business logic can be tested without UI, database, or external services
- **Independent**: UI, database, and frameworks can be changed without affecting business rules
- **Maintainable**: Clear separation makes code easier to understand and modify

### The Three Layers

#### 1. Domain Layer (`src/domain/`)
**Purpose**: Pure business logic with zero external dependencies

**Contains**:
- **Entities**: Core business objects that represent domain concepts
- **Value Objects**: Immutable objects that represent domain values
- **Domain Services**: Business logic that doesn't naturally fit in entities
- **Repository Interfaces**: Abstract contracts for data access
- **Domain Events**: Events that represent something significant in the domain

**Example Entity**:
```python
# src/domain/entities/user.py
from dataclasses import dataclass
from uuid import UUID

@dataclass
class User:
    id: UUID
    email: str
    name: str

    def change_email(self, new_email: str) -> None:
        """Business rule: email must contain @"""
        if '@' not in new_email:
            raise ValueError("Invalid email format")
        self.email = new_email
```

**Rules**:
- NO imports from Application or Infrastructure layers
- NO framework dependencies (FastAPI, SQLAlchemy, etc.)
- Only pure Python and domain logic

#### 2. Application Layer (`src/application/`)
**Purpose**: Orchestrate domain logic to fulfill use cases

**Contains**:
- **Use Cases**: Application-specific business rules (e.g., CreateUser, UpdateProfile)
- **DTOs**: Data Transfer Objects for input/output
- **Service Interfaces**: Contracts for external services (email, payments, etc.)

**Example Use Case**:
```python
# src/application/use_cases/create_user.py
from dataclasses import dataclass
from uuid import UUID, uuid4

from src.domain.entities.user import User
from src.domain.repositories.user_repository import UserRepository

@dataclass
class CreateUserInput:
    email: str
    name: str

@dataclass
class CreateUserOutput:
    user_id: UUID
    email: str
    name: str

class CreateUserUseCase:
    def __init__(self, user_repository: UserRepository):
        self.user_repository = user_repository

    def execute(self, input_data: CreateUserInput) -> CreateUserOutput:
        # Orchestrate domain logic
        user = User(
            id=uuid4(),
            email=input_data.email,
            name=input_data.name
        )

        # Use repository (defined in domain, implemented in infrastructure)
        self.user_repository.save(user)

        return CreateUserOutput(
            user_id=user.id,
            email=user.email,
            name=user.name
        )
```

**Rules**:
- Can import from Domain layer
- NO imports from Infrastructure layer
- Uses interfaces (defined in Domain) instead of concrete implementations

#### 3. Infrastructure Layer (`src/infrastructure/`)
**Purpose**: Handle external concerns and framework-specific code

**Contains**:
- **API**: FastAPI routes, request/response models
- **Database**: SQLAlchemy models, repository implementations
- **External Services**: Third-party API clients, email services, etc.

**Example API Route**:
```python
# src/infrastructure/api/routes/users.py
from fastapi import APIRouter, Depends
from src.application.use_cases.create_user import CreateUserUseCase, CreateUserInput
from src.infrastructure.dependencies import get_create_user_use_case

router = APIRouter()

@router.post("/users")
def create_user(
    email: str,
    name: str,
    use_case: CreateUserUseCase = Depends(get_create_user_use_case)
):
    input_data = CreateUserInput(email=email, name=name)
    output = use_case.execute(input_data)
    return output
```

**Example Repository Implementation**:
```python
# src/infrastructure/database/repositories/user_repository_impl.py
from src.domain.entities.user import User
from src.domain.repositories.user_repository import UserRepository
from src.infrastructure.database.models import UserModel

class UserRepositoryImpl(UserRepository):
    def __init__(self, session):
        self.session = session

    def save(self, user: User) -> None:
        user_model = UserModel(
            id=user.id,
            email=user.email,
            name=user.name
        )
        self.session.add(user_model)
        self.session.commit()
```

**Rules**:
- Can import from both Domain and Application layers
- Contains all framework-specific code
- Implements interfaces defined in Domain layer

## Key Principles

### 1. Dependency Inversion
- High-level modules should not depend on low-level modules
- Both should depend on abstractions (interfaces)
- Infrastructure implements interfaces defined in Domain

### 2. Single Responsibility
- Each class/module has one reason to change
- Separate business logic from technical concerns

### 3. Testability
- Domain logic can be tested without any infrastructure
- Use cases can be tested with mock repositories
- Infrastructure can be tested separately

## Testing Strategy

### Unit Tests (Domain Layer)
```python
def test_user_change_email():
    user = User(id=uuid4(), email="old@example.com", name="Test")
    user.change_email("new@example.com")
    assert user.email == "new@example.com"

def test_user_change_email_invalid():
    user = User(id=uuid4(), email="old@example.com", name="Test")
    with pytest.raises(ValueError):
        user.change_email("invalid-email")
```

### Integration Tests (Use Cases)
```python
def test_create_user_use_case():
    # Use in-memory repository for testing
    repo = InMemoryUserRepository()
    use_case = CreateUserUseCase(repo)

    result = use_case.execute(
        CreateUserInput(email="test@example.com", name="Test User")
    )

    assert result.email == "test@example.com"
    assert repo.count() == 1
```

### E2E Tests (API)
```python
def test_create_user_endpoint(client):
    response = client.post("/users", json={
        "email": "test@example.com",
        "name": "Test User"
    })
    assert response.status_code == 200
    assert response.json()["email"] == "test@example.com"
```

## Common Patterns

### Repository Pattern
Abstract data access behind interfaces defined in Domain layer.

### Use Case Pattern
Each use case represents a single business operation.

### DTO Pattern
Transfer data between layers without coupling to domain entities.

### Dependency Injection
Inject dependencies (repositories, services) instead of creating them.

## Best Practices

1. **Keep Domain Pure**: No framework dependencies in domain layer
2. **Use Type Hints**: All functions should have type annotations
3. **Write Tests First**: TDD approach ensures testability
4. **Follow SOLID**: Single Responsibility, Open/Closed, Liskov Substitution, Interface Segregation, Dependency Inversion
5. **Document Decisions**: Use `dev/` folder for planning and context
