# Contract: Base Update Notification

The `vault-mcp` server sends notifications to clients when a subscribed Base query's results may have changed.

## Notification: `base/update`

Sent when a filesystem event matches the dependencies of an active subscription.

### Payload
```json
{
  "method": "notifications/base/update",
  "params": {
    "handle": "sub_123456",
    "path": "Outputs/Plans/ProjectX.md",
    "event": "file_changed",
    "affected_path": "Entities/Tasks/TaskA.md",
    "note": "Re-execution of execute_base recommended."
  }
}
```

### Fields
- `handle`: The unique subscription handle returned by `subscribe_to_base`.
- `path`: The vault-relative path of the file containing the Base query.
- `event`: One of `file_changed`, `file_created`, `file_deleted`, `definition_changed`.
- `affected_path`: The vault-relative path of the file that triggered the notification.
- `note`: Human-readable hint.

## Subscription Handle Format

Handles are opaque strings, likely prefixed with `sub_` followed by a random hex string or UUID.

```
sub_a1b2c3d4
```
