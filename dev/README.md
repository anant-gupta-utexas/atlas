# Dev Folder - Work in Progress Documentation

This folder contains **work-in-progress technical designs** and planning space for new features.

## Structure

- `active/`: Features currently being developed
- `archive/`: Historical record of completed features

## Workflow

### 1. Start Planning
Before writing code, create a new folder in `dev/active/[feature-name]/`.

### 2. Define the Spec
Inside your feature folder, create the required documents:

- **`[feature-name]-plan.md`**: The Technical Design Specification (TDS)
  - Overview of the feature
  - Technical approach
  - Architecture decisions
  - Implementation details

- **`[feature-name]-context.md`**: Key decisions, dependencies, and files to be touched
  - Why this feature is needed
  - Dependencies on other systems/features
  - List of files that will be modified
  - Integration points

- **`[feature-name]-tasks.md`**: A checklist for tracking progress
  - Breakdown of implementation steps
  - Testing requirements
  - Documentation updates needed

### 3. Build
- Get your TDS reviewed by the team
- Implement the feature following Clean Architecture principles
- Write tests alongside your code
- Follow the task checklist

### 4. Update Core Docs
As part of your "Definition of Done," you must update the main `docs/` directory to reflect any changes:
- Update API documentation if endpoints changed
- Update system design diagrams if architecture changed
- Update guides if new concepts introduced
- Update testing docs if new test patterns added

### 5. Archive
Once your feature is merged and live, move your folder from `dev/active/` to `dev/archive/` to maintain a historical record of decisions.

## Example Structure

```
dev/
├── active/
│   └── user-authentication/
│       ├── user-authentication-plan.md
│       ├── user-authentication-context.md
│       └── user-authentication-tasks.md
└── archive/
    └── initial-setup/
        ├── initial-setup-plan.md
        ├── initial-setup-context.md
        └── initial-setup-tasks.md
```

## Benefits

- **Historical Record**: Understand why decisions were made
- **Onboarding**: New team members can see how features were built
- **Knowledge Sharing**: Learn from past implementations
- **Planning**: Think before coding leads to better architecture
