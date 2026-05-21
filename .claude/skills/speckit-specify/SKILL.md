---
name: speckit-specify
description: 'Spec-kit workflow command: speckit-specify'
compatibility: Requires spec-kit project structure with .specify/ directory
metadata:
  author: github-spec-kit
  source: security-governance:commands/speckit.specify.md
---

Before continuing, apply the Security Governance preset:

- determine whether the primary implementation language is memory-safe
- document a short justification if the language is not memory-safe
- determine whether `NIST SSDF`, `CWE Top 25`, `OWASP ASVS`, `SBOM`, `VEX`,
  and `SLSA` are relevant
- document `N/A` decisions with rationale
- identify which security evidence artefacts should be created or updated under
  `docs/security/`

{CORE_TEMPLATE}
