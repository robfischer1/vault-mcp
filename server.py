#!/usr/bin/env python3
"""Transitional compatibility shim — not part of the service-repo standard.

The canonical entry point is the ``vault-mcp`` console script
(``vault_mcp.server:main``, declared in ``[project.scripts]``). This root-level
shim only preserves the legacy ``python server.py`` invocation for the live nas01
NSSM service until it is cut over to the console script (the upcoming larger
refactor; SRSC F2.2 containerization is deferred). Remove it at that cutover.
"""

from vault_mcp.server import main

if __name__ == "__main__":
    main()
