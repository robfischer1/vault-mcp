# Data Model: Bases Live Subscriptions

## Subscription

A `Subscription` represents a client's interest in the results of a specific Base query.

```python
@dataclass
class Subscription:
    handle: str            # Unique identifier for the subscription
    path: str              # Vault-relative path to the file containing the Base
    view_name: str | None  # Optional view name
    base_index: int        # Index of the Base in the file
    session_id: str | None # Optional MCP session identifier

    # Dependencies (computed on registration)
    dependency_keys: set[str]   # Frontmatter keys (e.g., "status", "note.type")
    depends_on_links: bool      # True if query uses file.links/backlinks
    depends_on_structure: bool  # True if query depends on folder/path/filename
```

## SubscriptionManager

The `SubscriptionManager` maintains the active subscriptions and matches filesystem events against them.

### State
- `_subscriptions: dict[str, Subscription]` - Map of handle -> Subscription.
- `_key_map: dict[str, set[str]]` - Map of dependency key -> set of handles.
- `_path_map: dict[str, set[str]]` - Map of file path -> set of handles (for "definition changed" events).

### Methods
- `register(path: str, base: Base, view_name: str | None = None) -> str`: Creates a new subscription, extracts dependencies, and returns a handle.
- `unregister(handle: str)`: Removes a subscription.
- `on_file_changed(path: Path, new_frontmatter: dict | None)`: Triggered by `VaultIndex`.
    - If `path` is in `_path_map`, notify (the Base definition changed).
    - If `new_frontmatter` contains keys in `_key_map`, notify affected handles.
    - If `depends_on_links` is true and links changed, notify.

## Dependency Extraction (in `bases.py`)

We need a function to extract dependencies from a `Base` object:
- `extract_dependencies(base: Base, view_name: str | None) -> tuple[set[str], bool, bool]`
- Scans `base.filters` recursively.
- Scans `base.formulas` (Tier 1 and Tier 2).
- Scans `base.views[view_name].filters` and `summaries`.
