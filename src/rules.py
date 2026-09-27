from datetime import datetime, timedelta, timezone

from .domain import (
    ConflictError,
    InvalidTransition,
    PermissionDenied,
    ValidationError,
)

REQUIRED_WITNESSES = 2


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _bottles(entity):
    bottles = entity["data"].get("bottles") or {}
    return {
        "a": dict(bottles.get("a") or {}),
        "b": dict(bottles.get("b") or {}),
    }


def b_unseal_missing(entity):
    """列出B瓶启封前仍缺少的手续，手续齐全时返回空列表。"""
    retest = entity["data"].get("b_retest")
    if not retest:
        return ["B瓶复检未发起"]
    witnesses = retest.get("witnesses") or []
    if len(witnesses) < REQUIRED_WITNESSES:
        return [
            "见证人登记不足（已登记%d/%d）" % (len(witnesses), REQUIRED_WITNESSES)
        ]
    return []


def _validate_athlete(actor, data, lookup):
    if len(data.get("discipline", "")) < 2:
        raise ValidationError("discipline is too short")


def _validate_sample(actor, data, lookup):
    athlete = _find_one(lookup, "athlete", "id", data.get("athlete_id"))
    if not athlete or athlete["status"] != "active":
        raise ValidationError("sample requires an active athlete")
    if not data.get("sample_code", "").strip():
        raise ValidationError("sample_code is required")


ADVERSE_STATUSES = ("adverse", "b_retest_pending", "b_opened", "b_closed")


def _validate_case(actor, data, lookup):
    sample = _find_one(lookup, "sample", "id", data.get("sample_id"))
    if not sample or sample["status"] not in ADVERSE_STATUSES:
        raise ValidationError("case requires an adverse sample")


def _validate_seal(actor, entity, data, lookup):
    seal_a = str(data.get("seal_a", "")).strip()
    seal_b = str(data.get("seal_b", "")).strip()
    if seal_a == seal_b:
        raise ValidationError("A瓶和B瓶的封条号必须不同")
    return {
        "bottles": {
            "a": {"seal_id": seal_a, "status": "sealed"},
            "b": {"seal_id": seal_b, "status": "sealed"},
        },
        "unseal_records": [],
    }


def _validate_analyze(actor, entity, data, lookup):
    bottles = _bottles(entity)
    a_bottle = bottles["a"]
    if a_bottle.get("status") == "opened":
        raise ValidationError("A瓶已启封")
    now = _now()
    a_bottle.update({"status": "opened", "opened_at": now, "opened_by": actor.user_id})
    records = list(entity["data"].get("unseal_records") or [])
    records.append({
        "bottle": "A",
        "seal_id": a_bottle.get("seal_id"),
        "opened_at": now,
        "opened_by": actor.user_id,
        "witnesses": [],
        "purpose": "实验室日常检测",
    })
    return {"bottles": bottles, "unseal_records": records}


def _validate_request_b_retest(actor, entity, data, lookup):
    return {
        "b_retest": {
            "requested_by": actor.user_id,
            "requested_at": _now(),
            "reason": str(data.get("reason", "")),
            "witnesses": [],
        }
    }


def _validate_register_witness(actor, entity, data, lookup):
    retest = dict(entity["data"].get("b_retest") or {})
    if not retest:
        raise ValidationError("B瓶复检未发起，无法登记见证人")
    witnesses = list(retest.get("witnesses") or [])
    witness = str(data.get("witness", "")).strip()
    if witness in witnesses:
        raise ValidationError("见证人已登记: " + witness)
    if len(witnesses) >= REQUIRED_WITNESSES:
        raise ValidationError("见证人已登记满%d名" % REQUIRED_WITNESSES)
    witnesses.append(witness)
    retest["witnesses"] = witnesses
    return {"b_retest": retest}


def _validate_open_b(actor, entity, data, lookup):
    missing = b_unseal_missing(entity)
    if missing:
        raise ValidationError("手续不全，B瓶保持封存，缺少: " + "；".join(missing))
    bottles = _bottles(entity)
    b_bottle = bottles["b"]
    now = _now()
    witnesses = list(entity["data"]["b_retest"]["witnesses"])
    b_bottle.update({
        "status": "opened",
        "opened_at": now,
        "opened_by": actor.user_id,
        "witnesses": witnesses,
    })
    records = list(entity["data"].get("unseal_records") or [])
    records.append({
        "bottle": "B",
        "seal_id": b_bottle.get("seal_id"),
        "opened_at": now,
        "opened_by": actor.user_id,
        "witnesses": witnesses,
        "purpose": "B瓶复检",
    })
    return {"bottles": bottles, "unseal_records": records}


def _validate_record_b_result(actor, entity, data, lookup):
    return {"b_result_recorded_by": actor.user_id, "b_result_recorded_at": _now()}


def _validate_report_adverse(actor, entity, data, lookup):
    if entity["data"].get("result") != "adverse":
        raise ValidationError("only an adverse lab result can open a case")
    return {"confirmed_by": actor.user_id}


def _validate_case_decision(actor, entity, data, lookup):
    if data.get("decision") not in ("sanction", "no_sanction"):
        raise ValidationError("decision must be sanction or no_sanction")
    return {"decided_by": actor.user_id}


CUSTOM_CREATE = {'athlete': _validate_athlete, 'sample': _validate_sample, 'case': _validate_case}
CUSTOM_TRANSITIONS = {('sample', 'seal'): _validate_seal, ('sample', 'analyze'): _validate_analyze, ('sample', 'report_adverse'): _validate_report_adverse, ('sample', 'request_b_retest'): _validate_request_b_retest, ('sample', 'register_witness'): _validate_register_witness, ('sample', 'open_b'): _validate_open_b, ('sample', 'record_b_result'): _validate_record_b_result, ('case', 'decide'): _validate_case_decision, ('case', 'resolve_appeal'): _validate_case_decision}


class RuleEngine:
    ALIASES = {'athletes': 'athlete', 'samples': 'sample', 'cases': 'case'}
    INITIAL_STATUS = {'athlete': 'active', 'sample': 'scheduled', 'case': 'open'}
    TRANSITIONS = {'athlete': {'retire': (('active',), 'retired')}, 'sample': {'collect': (('scheduled',), 'collected'), 'seal': (('collected',), 'sealed'), 'ship': (('sealed',), 'in_transit'), 'receive': (('in_transit',), 'received'), 'analyze': (('received',), 'analyzed'), 'report_adverse': (('analyzed',), 'adverse'), 'clear': (('analyzed',), 'cleared'), 'request_b_retest': (('adverse',), 'b_retest_pending'), 'register_witness': (('b_retest_pending',), 'b_retest_pending'), 'open_b': (('adverse', 'b_retest_pending'), 'b_opened'), 'record_b_result': (('b_opened',), 'b_closed')}, 'case': {'provisional_suspend': (('open',), 'suspended'), 'schedule_hearing': (('suspended',), 'hearing'), 'decide': (('hearing',), 'closed'), 'appeal': (('closed',), 'appeal'), 'resolve_appeal': (('appeal',), 'closed')}}
    CREATE_REQUIRED = {'athlete': ('name', 'discipline'), 'sample': ('athlete_id', 'sample_code', 'event'), 'case': ('athlete_id', 'sample_id', 'alleged_rule')}
    ACTION_REQUIRED = {('sample', 'collect'): ('collected_at',), ('sample', 'seal'): ('seal_a', 'seal_b'), ('sample', 'ship'): ('carrier',), ('sample', 'receive'): ('lab_id',), ('sample', 'analyze'): ('result',), ('sample', 'clear'): ('reason',), ('sample', 'register_witness'): ('witness',), ('sample', 'record_b_result'): ('b_result',), ('case', 'provisional_suspend'): ('reason',), ('case', 'schedule_hearing'): ('hearing_at',), ('case', 'decide'): ('decision',), ('case', 'appeal'): ('grounds',), ('case', 'resolve_appeal'): ('decision',)}
    CREATE_ROLES = {'athlete': ('admin', 'panel'), 'sample': ('admin', 'inspector'), 'case': ('admin', 'panel')}
    ROLE_ACTIONS = {'retire': ('admin', 'panel'), 'collect': ('admin', 'inspector'), 'seal': ('admin', 'inspector'), 'ship': ('admin', 'inspector'), 'receive': ('admin', 'lab'), 'analyze': ('admin', 'lab'), 'report_adverse': ('admin', 'lab'), 'clear': ('admin', 'lab'), 'request_b_retest': ('admin',), 'register_witness': ('admin',), 'open_b': ('admin', 'lab'), 'record_b_result': ('admin', 'lab'), 'provisional_suspend': ('admin', 'panel'), 'schedule_hearing': ('admin', 'panel'), 'decide': ('admin', 'panel'), 'appeal': ('admin', 'panel'), 'resolve_appeal': ('admin', 'panel')}

    def normalize_kind(self, kind):
        return self.ALIASES.get(kind, kind)

    def initial_status(self, kind):
        kind = self.normalize_kind(kind)
        if kind not in self.INITIAL_STATUS:
            raise ValidationError("unknown kind: " + str(kind))
        return self.INITIAL_STATUS[kind]

    @staticmethod
    def _ensure_role(actor, allowed):
        if "*" not in allowed and actor.role not in allowed:
            raise PermissionDenied("role %s is not allowed here" % actor.role)

    @staticmethod
    def _require(data, fields):
        for field in fields:
            value = data.get(field)
            if value is None or value == "" or value == [] or value == {}:
                raise ValidationError("missing required field: " + field)

    def validate_create(self, actor, kind, data, lookup=None):
        kind = self.normalize_kind(kind)
        if kind not in self.INITIAL_STATUS:
            raise ValidationError("unknown kind: " + str(kind))
        self._ensure_role(actor, self.CREATE_ROLES.get(kind, ("admin",)))
        self._require(data, self.CREATE_REQUIRED.get(kind, ()))
        custom = CUSTOM_CREATE.get(kind)
        if custom:
            custom(actor, data, lookup)
        return dict(data)

    def validate_transition(self, actor, entity, action, data, lookup=None):
        kind = self.normalize_kind(entity["kind"])
        transition = self.TRANSITIONS.get(kind, {}).get(action)
        if not transition:
            raise InvalidTransition("unknown action %s for %s" % (action, kind))
        allowed_statuses, next_status = transition
        if entity["status"] not in allowed_statuses:
            raise InvalidTransition(
                "cannot %s from status %s" % (action, entity["status"])
            )
        allowed_roles = self.ROLE_ACTIONS.get(
            (kind, action), self.ROLE_ACTIONS.get(action, ("admin",))
        )
        self._ensure_role(actor, allowed_roles)
        self._require(data, self.ACTION_REQUIRED.get((kind, action), ()))
        custom = CUSTOM_TRANSITIONS.get((kind, action))
        extra = custom(actor, entity, data, lookup) if custom else {}
        patch = dict(data)
        if extra:
            patch.update(extra)
        return next_status, patch

    def bottle_view(self, entity):
        """样本双瓶状态视图：封条号、启封信息、B瓶缺少的手续和启封记录。"""
        data = entity.get("data", {})
        bottles = data.get("bottles") or {}
        view = {"unseal_records": list(data.get("unseal_records") or [])}
        for key in ("a", "b"):
            info = dict(bottles.get(key) or {})
            view[key] = info if info else {"status": "unregistered"}
        if view["b"].get("status") == "sealed":
            view["b"]["missing"] = b_unseal_missing(entity)
            if entity.get("status") == "cleared":
                view["b"]["note"] = "A瓶结果正常，B瓶继续封存"
        retest = data.get("b_retest")
        if retest:
            view["b_retest"] = retest
        return view


def _find_one(lookup, kind, field, value):
    if lookup is None:
        return None
    rows = lookup(kind, field, value) or []
    return rows[0] if rows else None


def _date_ordinal(value):
    return datetime.fromisoformat(str(value)[:10]).date().toordinal()
