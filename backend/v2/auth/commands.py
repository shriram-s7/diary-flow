"""Authenticated command bridge; resource policy is mandatory even for retries."""
from v2.db.commands import execute
from .service import AuthError


def execute_authenticated(service, token, grant_id, context, kind, payload, handler, *, sim_time, policy):
    def authorize(tx):
        identity = service.identity(tx, token, context.run_id, grant_id)
        if context.actor_id != identity.user_id:
            raise AuthError(403, "Command actor does not match this session.")
        policy(tx, identity, payload)
        if identity.real_actor_id != identity.user_id:
            service.audit(tx, "view_as_command_authorized", identity.real_actor_id, identity.user_id)
    return execute(service.engine, context, kind, payload, handler,
                   sim_time=sim_time, authorize=authorize)
