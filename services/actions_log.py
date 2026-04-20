# services/actions_log.py
from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from models import ActionLog, User
from utils import ActionType


async def log_action(
    session: AsyncSession,
    *,
    user: Optional[User],
    action: ActionType | str,
    entity: Optional[str] = None,
    entity_id: Optional[int] = None,
    detail: Optional[str] = None,
) -> None:
    """
    Registra una acción administrativa relevante en action_logs.

    - No hace commit (usa la transacción activa).
    - Nunca debe lanzar excepciones que rompan la lógica principal.
    - Diseñado para ser llamado desde routers o servicios.
    """
    try:
        log = ActionLog(
            user_id=user.id if user else None,
            action=str(action),
            entity=entity,
            entity_id=entity_id,
            detail=detail,
        )
        session.add(log)

        # Flush sin commit: respeta la transacción principal
        await session.flush()

    except Exception:
        # El logging NO debe romper la aplicación jamás
        # Fallos en logs se silencian por diseño
        pass
