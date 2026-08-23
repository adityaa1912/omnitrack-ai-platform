from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..auth.dependencies import CurrentUser, get_db, require_role
from ..cache.json_cache import JsonCache
from .engine import AlertRuleEngine
from .manager import AlertManager, AlertTransitionError
from .models import AlertInstance, AlertRule, AlertStateHistory, NotificationAttempt
from .schemas import AcknowledgeRequest, ResolveRequest, RuleCreate, RuleUpdate, SuppressRequest

router = APIRouter(prefix="/alerts", tags=["alerts"])

_engine: Optional[AlertRuleEngine] = None
_manager: Optional[AlertManager] = None
_cache: Optional[JsonCache] = None


def set_manager(
    engine: AlertRuleEngine, manager: AlertManager, cache: Optional[JsonCache] = None
) -> None:
    global _engine, _manager, _cache
    _engine = engine
    _manager = manager
    _cache = cache


def _require_engine() -> None:
    if _engine is None or _manager is None:
        raise HTTPException(status_code=503, detail="Alert engine not initialized")


def _rule_out(row: AlertRule) -> Dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "stream_id": row.stream_id,
        "enabled": row.enabled,
        "rule_type": row.rule_type,
        "conditions": row.conditions,
        "condition_logic": row.condition_logic,
        "severity": row.severity,
        "priority": row.priority,
        "cooldown_seconds": row.cooldown_seconds,
        "dedup_key_template": row.dedup_key_template,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _alert_out(row: AlertInstance) -> Dict[str, Any]:
    return {
        "id": row.id,
        "rule_id": row.rule_id,
        "stream_id": row.stream_id,
        "state": row.state,
        "severity": row.severity,
        "priority": row.priority,
        "dedup_key": row.dedup_key,
        "message": row.message,
        "details": row.details,
        "triggered_at": row.triggered_at.isoformat() if row.triggered_at else None,
        "acknowledged_at": row.acknowledged_at.isoformat() if row.acknowledged_at else None,
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
        "suppressed_until": row.suppressed_until.isoformat() if row.suppressed_until else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "acknowledged_by": row.acknowledged_by,
        "notification_count": row.notification_count,
    }


@router.post("/rules", status_code=201)
async def create_rule(
    body: RuleCreate,
    user: CurrentUser = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    _require_engine()
    rule = AlertRule(
        name=body.name,
        stream_id=body.stream_id,
        enabled=body.enabled,
        rule_type=body.rule_type,
        conditions=[c.model_dump(exclude_none=True) for c in body.conditions],
        condition_logic=body.condition_logic,
        severity=body.severity,
        priority=body.priority,
        cooldown_seconds=body.cooldown_seconds,
        dedup_key_template=body.dedup_key_template,
        extra=body.extra,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    _engine.reload_rules()
    return _rule_out(rule)


@router.put("/rules/{rule_id}")
async def update_rule(
    rule_id: int,
    body: RuleUpdate,
    user: CurrentUser = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    _require_engine()
    rule = db.query(AlertRule).filter(AlertRule.id == rule_id).first()
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    data = body.model_dump(exclude_unset=True)
    if "conditions" in data and data["conditions"] is not None:
        data["conditions"] = [c.model_dump(exclude_none=True) for c in body.conditions]
    for field, value in data.items():
        setattr(rule, field, value)
    db.commit()
    db.refresh(rule)
    _engine.reload_rules()
    return _rule_out(rule)


@router.delete("/rules/{rule_id}")
async def delete_rule(
    rule_id: int,
    disable_only: bool = Query(default=False),
    user: CurrentUser = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    _require_engine()
    rule = db.query(AlertRule).filter(AlertRule.id == rule_id).first()
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    if disable_only:
        rule.enabled = False
        db.commit()
        result = {"status": "disabled", "rule_id": rule_id}
    else:
        db.delete(rule)
        db.commit()
        result = {"status": "deleted", "rule_id": rule_id}
    _engine.reload_rules()
    return result


@router.get("/rules")
async def list_rules(
    stream_id: Optional[str] = Query(default=None),
    user: CurrentUser = Depends(require_role("viewer")),
    db: Session = Depends(get_db),
):
    _require_engine()
    query = db.query(AlertRule)
    if stream_id is not None:
        query = query.filter(AlertRule.stream_id == stream_id)
    rows = query.order_by(AlertRule.priority.desc()).all()
    return {"rules": [_rule_out(row) for row in rows]}


@router.get("/active")
async def list_active_alerts(
    stream_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    user: CurrentUser = Depends(require_role("viewer")),
    db: Session = Depends(get_db),
):
    _require_engine()
    query = db.query(AlertInstance).filter(
        AlertInstance.state.in_(("triggered", "acknowledged", "suppressed"))
    )
    if stream_id is not None:
        query = query.filter(AlertInstance.stream_id == stream_id)
    rows = query.order_by(AlertInstance.triggered_at.desc()).limit(limit).all()
    return {"alerts": [_alert_out(row) for row in rows]}


@router.get("/history")
async def list_alert_history(
    stream_id: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    hours: int = Query(default=168, ge=1, le=8760),
    limit: int = Query(default=100, ge=1, le=1000),
    user: CurrentUser = Depends(require_role("viewer")),
    db: Session = Depends(get_db),
):
    _require_engine()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    query = db.query(AlertInstance).filter(AlertInstance.triggered_at >= cutoff)
    if stream_id is not None:
        query = query.filter(AlertInstance.stream_id == stream_id)
    if state is not None:
        query = query.filter(AlertInstance.state == state)
    rows = query.order_by(AlertInstance.triggered_at.desc()).limit(limit).all()
    return {"alerts": [_alert_out(row) for row in rows]}


@router.get("/{alert_id}")
async def get_alert(
    alert_id: int,
    user: CurrentUser = Depends(require_role("viewer")),
    db: Session = Depends(get_db),
):
    _require_engine()
    row = db.query(AlertInstance).filter(AlertInstance.id == alert_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    history = (
        db.query(AlertStateHistory)
        .filter(AlertStateHistory.alert_instance_id == alert_id)
        .order_by(AlertStateHistory.changed_at.asc())
        .all()
    )
    notifications = (
        db.query(NotificationAttempt)
        .filter(NotificationAttempt.alert_instance_id == alert_id)
        .order_by(NotificationAttempt.created_at.asc())
        .all()
    )
    out = _alert_out(row)
    out["history"] = [
        {
            "from_state": h.from_state,
            "to_state": h.to_state,
            "changed_at": h.changed_at.isoformat() if h.changed_at else None,
            "changed_by": h.changed_by,
            "note": h.note,
        }
        for h in history
    ]
    out["notifications"] = [
        {
            "provider": n.provider,
            "status": n.status,
            "attempt_number": n.attempt_number,
            "error": n.error,
            "created_at": n.created_at.isoformat() if n.created_at else None,
        }
        for n in notifications
    ]
    return out


def _map_transition_error(exc: AlertTransitionError) -> HTTPException:
    message = str(exc)
    if "not found" in message.lower():
        return HTTPException(status_code=404, detail=message)
    return HTTPException(status_code=409, detail=message)


@router.post("/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: int,
    body: AcknowledgeRequest = AcknowledgeRequest(),
    user: CurrentUser = Depends(require_role("operator")),
):
    _require_engine()
    try:
        return _manager.acknowledge(alert_id, user.username, body.note)
    except AlertTransitionError as exc:
        raise _map_transition_error(exc)


@router.post("/{alert_id}/resolve")
async def resolve_alert(
    alert_id: int,
    body: ResolveRequest = ResolveRequest(),
    user: CurrentUser = Depends(require_role("operator")),
):
    _require_engine()
    try:
        return _manager.resolve(alert_id, user.username, body.note)
    except AlertTransitionError as exc:
        raise _map_transition_error(exc)


@router.post("/{alert_id}/suppress")
async def suppress_alert(
    alert_id: int,
    body: SuppressRequest,
    user: CurrentUser = Depends(require_role("operator")),
):
    _require_engine()
    try:
        return _manager.suppress(alert_id, user.username, body.seconds, body.note)
    except AlertTransitionError as exc:
        raise _map_transition_error(exc)
