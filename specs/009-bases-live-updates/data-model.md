# Data Model: Bases Live Updates

## Dataclasses

```python
@dataclass
class Subscription:
    handle: str
    path: str
    view: str | None
    base_index: int
    session_id: str  # To target the correct client
    last_result_hash: str | None = None
```

## Subscription Manager

The `SubscriptionManager` will be a singleton (or part of the server state) that holds:
*   `subscriptions: dict[str, Subscription]` - mapped by handle.
*   `lock: threading.Lock` - for thread-safe access from tool calls and watcher thread.

## Hashing Results

To avoid pushing notifications when the result set hasn't changed (e.g., a file change didn't affect the filters or formulas of a specific base), we will hash the `QueryResult`.

The hash should include:
*   The list of note paths in order.
*   The formula values for each note.
*   The summaries results.

If `current_hash == last_result_hash`, skip the push.
