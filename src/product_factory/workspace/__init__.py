"""Server-owned workspace preparation.

TODO(PM3.C follow-up): add bounded ``uploaded_git_bundle`` preflight/upload/
finalize only after the git-ref clone path is established, with digest and size
caps. PM3.C1 intentionally exposes only ``git_ref``.
"""

from product_factory.workspace.manager import PreparedWorkspace, WorkspaceManager

__all__ = ["PreparedWorkspace", "WorkspaceManager"]
