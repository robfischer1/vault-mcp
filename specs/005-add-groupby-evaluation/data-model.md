# Data Model: Bases Tier 5 — Grouping

## Entities

### QueryResult (Updated)
The central result container for base execution.

| Field | Type | Description |
| :--- | :--- | :--- |
| `notes` | `list[dict]` | The flat list of matched and sorted notes. |
| `groups` | `list[GroupResult]` | The partitioned results (new field). |
| `summaries` | `dict[str, Any]` | Aggregated values. |
| `total` | `int` | Total count of matched notes. |

### GroupResult
Represents a single group within the query result.

| Field | Type | Description |
| :--- | :--- | :--- |
| `label` | `string` | The display name of the group (evaluated key). |
| `notes` | `list[dict]` | The subset of notes belonging to this group. |
| `count` | `int` | Number of notes in this group. |

### ViewConfig (Existing update)
The configuration parsed from YAML already contains `groupBy`.

| Field | Type | Description |
| :--- | :--- | :--- |
| `groupBy` | `GroupByConfig | None` | The grouping directive. |

### GroupByConfig
Parsed configuration for grouping.

| Field | Type | Description |
| :--- | :--- | :--- |
| `property` | `string` | The property or formula name to group by. |
| `direction` | `string` | `ASC` or `DESC`. |
