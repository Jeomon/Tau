# Development Setup

This page covers setting up a development environment for tau.

## Prerequisites

- Python 3.12 or higher
- git
- pip or uv (Python package manager)

## Clone the Repository

```bash
git clone https://github.com/Jeomon/Tau.git
cd Tau
```

## Install Dependencies

Using pip (with editable mode for development):

```bash
pip install -e .
```

Using uv:

```bash
uv sync
```

The editable install allows you to modify code and see changes immediately.

## Verify Installation

Check that tau is installed and working:

```bash
tau --print "Say hello"
```

If you see output from the LLM, tau is properly installed and configured.

## Project Structure

See [Project Structure](project-structure.md) for a detailed module breakdown.

## Development Commands

### Run tau Locally

```bash
tau
```

Or with arguments:

```bash
tau -p "Test prompt"
tau --theme dark
```

### Run with Debug Logging

```bash
tau --debug
```

### Run Tests

```bash
python -m pytest
```

Run specific tests:

```bash
python -m pytest tests/test_agent.py -v
python -m pytest tests/ -k "test_read" -v
```

### Run Type Checking

```bash
mypy tau/
pyright tau/
```

### Run Linting

```bash
ruff check tau/
```

### Format Code

```bash
ruff format tau/
```

## Making Changes

### Code Style

- Follow PEP 8
- Use type hints
- Write docstrings for public APIs
- Use meaningful variable names

### Before Committing

1. Ensure code follows style guidelines:
   ```bash
   ruff check tau/
   ruff format tau/
   ```

2. Run type checking:
   ```bash
   mypy tau/
   ```

3. Run tests:
   ```bash
   python -m pytest
   ```

4. Test manually:
   ```bash
   tau -p "test prompt"
   tau --list-models
   ```

## Testing

### Test Structure

Tests are organized by module:

```text
tests/
├── test_agent.py
├── test_inference.py
├── test_engine.py
└── test_tui.py
```

### Writing Tests

Use pytest:

```python
import pytest
from tau.agent import Agent

def test_agent_creation():
    agent = Agent(client=MockClient())
    assert agent is not None

def test_agent_run():
    agent = Agent(client=MockClient())
    result = agent.run("test prompt")
    assert "result" in result.lower()
```

### Running Tests

```bash
# Run all tests
python -m pytest

# Run with verbose output
python -m pytest -v

# Run with coverage
python -m pytest --cov=tau

# Run specific test file
python -m pytest tests/test_agent.py
```

## Debugging

### Using print()

Add debug output:

```python
print(f"Debug: variable = {variable}")
```

### Using pdb

```python
import pdb

# Set breakpoint
pdb.set_trace()

# Or in Python 3.7+
breakpoint()
```

### Using logging

```python
import logging

logger = logging.getLogger(__name__)
logger.debug("Debug message")
logger.info("Info message")
logger.error("Error message")
```

Enable debug logging:

```bash
tau --debug
```

## IDE Setup

### VS Code

Install extensions:
- Python
- Pylance
- Black Formatter
- Prettier

Create `.vscode/settings.json`:

```json
{
  "python.linting.enabled": true,
  "python.linting.pylintEnabled": true,
  "python.formatting.provider": "black",
  "[python]": {
    "editor.formatOnSave": true,
    "editor.defaultFormatter": "ms-python.python"
  }
}
```

### PyCharm

1. Open project folder
2. Set Python interpreter to virtual environment
3. Enable pytest as test runner

## Virtual Environment

Recommended: Use a virtual environment:

```bash
python3.13 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

pip install -e .
```

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature-name`
3. Make changes and commit: `git commit -m "Description"`
4. Push to branch: `git push origin feature-name`
5. Open a pull request

## Common Issues

### Import Errors

If you get import errors, ensure tau is installed in editable mode:

```bash
pip install -e .
```

### Python Version

Check your Python version:

```bash
python --version
```

Ensure it's 3.13 or higher.

### Missing Dependencies

Reinstall dependencies:

```bash
pip install -e . --upgrade
```

## Next Steps

- [Project Structure](project-structure.md) - Codebase organization
- [Architecture](architecture.md) - System design
- [Extensions](extensions.md) - Creating extensions
