"""Offering a failure to the caller's error policy once."""

from __future__ import annotations

#: Set on an exception once `ignore_error` has declined it, so every
#: enclosing hook re-raises it instead of consulting the policy again with an
#: ancestor's paths.
_OFFERED_ATTR = "_pathapi_sync_offered"


def _offer_error(policy, error: Exception, source, target, event) -> bool:
    """Ask `policy` about `error` once. True means "tolerated". An error the
    policy already declined (deeper in the tree) is re-raised by every outer
    level without asking again."""
    if getattr(error, _OFFERED_ATTR, False):
        return False
    try:
        if policy(error, source, target, event):
            return True
    except BaseException as raised:
        # The policy's own exception ends the sync: an enclosing level must
        # not offer it back to the policy as a failure of its own.
        _mark_offered(raised)
        raise
    _mark_offered(error)
    return False


def _mark_offered(error: BaseException) -> None:
    try:
        setattr(error, _OFFERED_ATTR, True)
    except Exception:
        pass
