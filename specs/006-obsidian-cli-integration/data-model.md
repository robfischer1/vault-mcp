# Data Model: Obsidian CLI Integration

## CLIResult

A uniform envelope for all CLI tool responses.

```typescript
interface CLIResult {
  ok: boolean;
  data?: any;       // Parsed JSON if possible, otherwise raw stdout string
  error?: string;   // Closed vocabulary: cli_not_found, cli_error, cli_unauthorized, cli_timeout
  detail?: string;  // Raw stderr or additional context
}
```

## CLICommand

Internal representation of a whitelisted CLI command.

```typescript
interface CLICommand {
  name: string;        // e.g., "plugin:reload"
  params: string[];    // list of required/optional parameter keys
  description: string;
}
```
