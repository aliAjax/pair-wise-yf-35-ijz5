from datetime import datetime, timezone

from .domain import (
    ConflictError,
    InvalidTransition,
    PermissionDenied,
    ValidationError,
)


BOTTLE_A = "A"
BOTTLE_B = "B"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _bottles(entity):
    bottles = entity["data"].get("bottles")
    return dict(bottles) if isinstance(bottles, dict) else {}


def _unseal_records(entity):
    records = entity["data"].get("unseal_records")
    return list(records) if isinstance(records, list) else []


def _unseal_record(bottle, seal_id, actor, witnesses, opened_at):
    return {
        "bottle": bottle,
        "seal_id": seal_id,
        "opened_at": opened_at,
        "operator_id": actor.user_id,
        "witnesses": list(witnesses),
    }


def _validate_athlete(actor, data, lookup):
    if len(data.get("discipline", "")) < 2:
        raise ValidationError("discipline is too short")


def _validate_sample(actor, data, lookup):
    athlete = _find_one(lookup, "athlete", "id", data.get("athlete_id"))
    if not athlete or athlete["status"] != "active":
        raise ValidationError("sample requires an active athlete")
    if not data.get("sample_code", "").strip():
        raise ValidationError("sample_code is required")


def _validate_case(actor, data, lookup):
    sample = _find_one(lookup, "sample", "id", data.get("sample_id"))
    if not sample or sample["status"] != "adverse":
        raise ValidationError("case requires an adverse sample")


def _validate_seal(actor, entity, data, lookup):
    # 样本封存时分别登记 A 瓶和 B 瓶的封条号
    seal_a = str(data.get("seal_id_a", "")).strip()
    seal_b = str(data.get("seal_id_b", "")).strip()
    if not seal_a:
        raise ValidationError("missing required field: seal_id_a")
    if not seal_b:
        raise ValidationError("missing required field: seal_id_b")
    if seal_a == seal_b:
        raise ValidationError("seal_id_a and seal_id_b must differ")
    return {
        "seal_id_a": seal_a,
        "seal_id_b": seal_b,
        "bottles": {
            BOTTLE_A: {"seal_id": seal_a, "state": "sealed"},
            BOTTLE_B: {"seal_id": seal_b, "state": "sealed"},
        },
    }


def _parse_witnesses(data):
    witnesses = data.get("witnesses") or []
    if isinstance(witnesses, str):
        witnesses = [witnesses]
    if not isinstance(witnesses, list):
        raise ValidationError("witnesses must be a list")
    return [str(item).strip() for item in witnesses if str(item).strip()]


def _validate_analyze_a(actor, entity, data, lookup):
    # 实验室日常检测只处理 A 瓶
    result = data.get("result")
    if result not in ("adverse", "normal"):
        raise ValidationError("result must be adverse or normal")
    bottles = _bottles(entity)
    bottle = bottles.get(BOTTLE_A)
    if not bottle:
        raise ValidationError("sample was not sealed with A/B bottles")
    if bottle.get("state") != "sealed":
        raise ValidationError("A bottle is not sealed")
    witnesses = _parse_witnesses(data)
    opened_at = str(data.get("opened_at", "")).strip() or _now()
    record = _unseal_record(
        BOTTLE_A, bottle.get("seal_id"), actor, witnesses, opened_at
    )
    bottles[BOTTLE_A] = dict(bottle, state="opened", opened_at=opened_at)
    return {
        "result": result,
        "a_result": result,
        "bottles": bottles,
        "unseal_records": _unseal_records(entity) + [record],
    }


def _validate_request_b_retest(actor, entity, data, lookup):
    # A 瓶出现异常结果后，管理员才能发起 B 瓶复检
    bottles = _bottles(entity)
    bottle_b = bottles.get(BOTTLE_B)
    if not bottle_b:
        raise ValidationError("sample was not sealed with A/B bottles")
    if entity["data"].get("a_result") != "adverse":
        raise ValidationError("B retest requires an adverse A result")
    if bottle_b.get("state") != "sealed":
        raise ValidationError("B bottle is not sealed")
    return {
        "retest_requested_by": actor.user_id,
        "retest_requested_at": _now(),
    }


def _validate_open_b(actor, entity, data, lookup):
    # B 瓶须在两名见证人登记完整后才能启封；手续不全就保持封存
    bottles = _bottles(entity)
    bottle_b = bottles.get(BOTTLE_B)
    if not bottle_b:
        raise ValidationError("sample was not sealed with A/B bottles")
    if bottle_b.get("state") != "sealed":
        raise ValidationError("B bottle is not sealed")
    if not entity["data"].get("retest_requested_at"):
        raise ValidationError("B retest must be requested before opening B bottle")

    witnesses = _parse_witnesses(data)
    missing = []
    if len(witnesses) < 1:
        missing.append("见证人1")
    if len(witnesses) < 2:
        missing.append("见证人2")
    if missing:
        raise ValidationError("手续不全，B瓶保持封存，缺少: " + "、".join(missing))
    witness_1, witness_2 = witnesses[0], witnesses[1]
    if witness_1 == witness_2:
        raise ValidationError("两名见证人不能为同一人")
    if actor.user_id in (witness_1, witness_2):
        raise ValidationError("经办人不能同时担任见证人")

    opened_at = str(data.get("opened_at", "")).strip() or _now()
    record = _unseal_record(
        BOTTLE_B, bottle_b.get("seal_id"), actor, [witness_1, witness_2], opened_at
    )
    bottles[BOTTLE_B] = dict(bottle_b, state="opened", opened_at=opened_at)
    return {
        "bottles": bottles,
        "unseal_records": _unseal_records(entity) + [record],
    }


def _validate_analyze_b(actor, entity, data, lookup):
    bottles = _bottles(entity)
    bottle_b = bottles.get(BOTTLE_B)
    if not bottle_b:
        raise ValidationError("sample was not sealed with A/B bottles")
    if bottle_b.get("state") != "opened":
        raise ValidationError("B bottle must be unsealed before analysis")
    result = data.get("b_result")
    if result not in ("adverse", "normal"):
        raise ValidationError("b_result must be adverse or normal")
    bottles[BOTTLE_B] = dict(bottle_b, result=result)
    return {"b_result": result, "bottles": bottles}


def _validate_report_adverse(actor, entity, data, lookup):
    if entity["data"].get("result") != "adverse":
        raise ValidationError("only an adverse lab result can open a case")
    return {"confirmed_by": actor.user_id}


def _validate_case_decision(actor, entity, data, lookup):
    if data.get("decision") not in ("sanction", "no_sanction"):
        raise ValidationError("decision must be sanction or no_sanction")
    return {"decided_by": actor.user_id}


CUSTOM_CREATE = {'athlete': _validate_athlete, 'sample': _validate_sample, 'case': _validate_case}
CUSTOM_TRANSITIONS = {
    ('sample', 'seal'): _validate_seal,
    ('sample', 'analyze'): _validate_analyze_a,
    ('sample', 'report_adverse'): _validate_report_adverse,
    ('sample', 'request_b_retest'): _validate_request_b_retest,
    ('sample', 'open_b'): _validate_open_b,
    ('sample', 'analyze_b'): _validate_analyze_b,
    ('case', 'decide'): _validate_case_decision,
    ('case', 'resolve_appeal'): _validate_case_decision,
}


class RuleEngine:
    ALIASES = {'athletes': 'athlete', 'samples': 'sample', 'cases': 'case'}
    INITIAL_STATUS = {'athlete': 'active', 'sample': 'scheduled', 'case': 'open'}
    TRANSITIONS = {
        'athlete': {
            'retire': (('active',), 'retired'),
        },
        'sample': {
            'collect': (('scheduled',), 'collected'),
            'seal': (('collected',), 'sealed'),
            'ship': (('sealed',), 'in_transit'),
            'receive': (('in_transit',), 'received'),
            # 日常检测只处理 A 瓶
            'analyze': (('received', 'analyzed_a'), 'analyzed_a'),
            'report_adverse': (('analyzed_a',), 'adverse'),
            'clear': (('analyzed_a',), 'cleared'),
            # A 瓶出现异常结果后管理员即可发起 B 瓶复检（阳性确认前/后均可）
            'request_b_retest': (('analyzed_a', 'adverse'), 'b_retest_requested'),
            'open_b': (('b_retest_requested',), 'b_opened'),
            'analyze_b': (('b_opened',), 'b_analyzed'),
        },
        'case': {
            'provisional_suspend': (('open',), 'suspended'),
            'schedule_hearing': (('suspended',), 'hearing'),
            'decide': (('hearing',), 'closed'),
            'appeal': (('closed',), 'appeal'),
            'resolve_appeal': (('appeal',), 'closed'),
        },
    }
    CREATE_REQUIRED = {'athlete': ('name', 'discipline'), 'sample': ('athlete_id', 'sample_code', 'event'), 'case': ('athlete_id', 'sample_id', 'alleged_rule')}
    ACTION_REQUIRED = {
        ('sample', 'collect'): ('collected_at',),
        ('sample', 'seal'): ('seal_id_a', 'seal_id_b'),
        ('sample', 'ship'): ('carrier',),
        ('sample', 'receive'): ('lab_id',),
        ('sample', 'analyze'): ('result',),
        ('sample', 'analyze_b'): ('b_result',),
        ('sample', 'clear'): ('reason',),
        ('case', 'provisional_suspend'): ('reason',),
        ('case', 'schedule_hearing'): ('hearing_at',),
        ('case', 'decide'): ('decision',),
        ('case', 'appeal'): ('grounds',),
        ('case', 'resolve_appeal'): ('decision',),
    }
    CREATE_ROLES = {'athlete': ('admin', 'panel'), 'sample': ('admin', 'inspector'), 'case': ('admin', 'panel')}
    ROLE_ACTIONS = {
        'retire': ('admin', 'panel'),
        'collect': ('admin', 'inspector'),
        'seal': ('admin', 'inspector'),
        'ship': ('admin', 'inspector'),
        'receive': ('admin', 'lab'),
        'analyze': ('admin', 'lab'),
        'analyze_b': ('admin', 'lab'),
        'report_adverse': ('admin', 'lab'),
        'clear': ('admin', 'lab'),
        'request_b_retest': ('admin',),
        'open_b': ('admin', 'inspector'),
        'provisional_suspend': ('admin', 'panel'),
        'schedule_hearing': ('admin', 'panel'),
        'decide': ('admin', 'panel'),
        'appeal': ('admin', 'panel'),
        'resolve_appeal': ('admin', 'panel'),
    }

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


def _find_one(lookup, kind, field, value):
    if lookup is None:
        return None
    rows = lookup(kind, field, value) or []
    return rows[0] if rows else None


def _date_ordinal(value):
    return datetime.fromisoformat(str(value)[:10]).date().toordinal()
