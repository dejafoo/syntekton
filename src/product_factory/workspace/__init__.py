"""Server-owned workspace preparation.

Bounded ``uploaded_git_bundle`` preflight/upload/finalize lives in
``product_factory.workspace.uploads`` (PM5.E). Full prepare-from-bundle remains
gated behind validated uploads; ``git_ref`` is still the default remote path.
"""

from product_factory.workspace.manager import PreparedWorkspace, WorkspaceManager
from product_factory.workspace.uploads import UploadStore, upload_bounds_summary

__all__ = [
    "PreparedWorkspace",
    "UploadStore",
    "WorkspaceManager",
    "upload_bounds_summary",
]
