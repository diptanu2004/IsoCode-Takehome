"""What each role may do (TDD §4). Every protected route checks this map on the server."""

from enum import StrEnum

from app.enums import Role


class Permission(StrEnum):
    UPLOAD_CREATE = "upload.create"  # includes voiding your own uploads
    UPLOAD_VOID_ANY = "upload.void_any"
    MAPPING_APPROVE = "mapping.approve"  # includes rollback
    RECON_VIEW = "recon.view"
    SUGGESTION_DECIDE = "suggestion.decide"
    CLAIM_WORK = "claim.work"  # raise, add partner reference, mark recovered or rejected
    CLAIM_EXPORT = "claim.export"
    CLAIM_WRITE_OFF = "claim.write_off"
    RATE_CARD_VIEW = "rate_card.view"
    RATE_CARD_EDIT = "rate_card.edit"
    SETTINGS_EDIT = "settings.edit"  # tolerance, settlement cycles, return window
    DASHBOARD_VIEW = "dashboard.view"
    USER_MANAGE = "user.manage"
    AUDIT_VIEW_ALL = "audit.view_all"  # everyone can see their own actions


_EXEC = frozenset(
    {
        Permission.UPLOAD_CREATE,
        Permission.MAPPING_APPROVE,
        Permission.RECON_VIEW,
        Permission.SUGGESTION_DECIDE,
        Permission.CLAIM_WORK,
        Permission.CLAIM_EXPORT,
        Permission.RATE_CARD_VIEW,
        Permission.DASHBOARD_VIEW,
    }
)

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.EXEC: _EXEC,
    Role.COFOUNDER: frozenset(Permission),
}
