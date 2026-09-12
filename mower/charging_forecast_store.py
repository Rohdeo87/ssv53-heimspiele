"""Bounded calibration journal in the existing table, isolated from controller state."""
from copy import deepcopy
import hashlib
import json
import threading
import time

from azure.core import MatchConditions
from azure.core.exceptions import ResourceNotFoundError
from azure.data.tables import UpdateMode
from mower.irrigation_journal import _table_client
from mower.charging_forecast import advance, epoch, own_display, quality

PARTITION = "ssv53-charging-calibration-v1"
_CACHE = {}
_LOCK = threading.Lock()
_IO = {"connection_timeout": 3, "read_timeout": 3, "retry_total": 0}


def enabled(env):
    return str(env.get("CHARGING_LEARNER_ENABLED", "true")).lower() == "true"


def key(env, mower_id):
    return (env.get("SSV53_STORAGE_ACCOUNT_URL"), env.get("SSV53_STATE_TABLE_NAME"),
            hashlib.sha256(str(mower_id).encode()).hexdigest())


def decode(entity):
    if not entity:
        return None
    state = json.loads(entity["StateJson"])
    state["history"] = json.loads(entity["HistoryJson"])
    state["outcomes"] = json.loads(entity["OutcomesJson"])
    return state


def encode(state, row_key):
    main = {k:v for k,v in state.items() if k not in {"history","outcomes"}}
    result = {"PartitionKey": PARTITION, "RowKey": row_key,
              "StateJson": json.dumps(main, separators=(",",":")),
              "HistoryJson": json.dumps(state["history"], separators=(",",":")),
              "OutcomesJson": json.dumps(state["outcomes"], separators=(",",":"))}
    if any(len(value.encode("utf-16-le")) > 64000 for value in result.values()):
        raise ValueError("Charging journal field exceeds bounded storage size")
    return result


def read(client, row_key):
    try:
        return client.get_entity(PARTITION, row_key, **_IO)
    except ResourceNotFoundError:
        return None


def cache(cache_key, state):
    with _LOCK:
        _CACHE[cache_key] = (time.monotonic(), deepcopy(state))
        while len(_CACHE) > 8:
            del _CACHE[min(_CACHE, key=lambda k:_CACHE[k][0])]


def record(result, env, *, client=None):
    if not enabled(env):
        return {"enabled": False}
    mower = (result.details or {}).get("mower") or {}
    if not mower.get("mower_id"):
        return {"recorded": False, "reason": "NO_MOWER"}
    cache_key = key(env, mower["mower_id"])
    client = client or _table_client(env)
    entity = read(client, cache_key[2])
    previous = decode(entity)
    now = epoch(result.executed_at_utc)
    state = advance(previous, mower, now)
    encoded = encode(state, cache_key[2])
    # Never overwrite a concurrent update. A conflict is reported; the next
    # regular timer tick collects again. No control-cycle retry is triggered.
    if entity is None:
        client.create_entity(encoded, **_IO)
    else:
        client.update_entity(encoded, mode=UpdateMode.REPLACE,
                             etag=entity.metadata["etag"], match_condition=MatchConditions.IfNotModified, **_IO)
    cache(cache_key, state)
    active = state.get("active") or {}
    return {"recorded": True, "trainingSessions": len(state["history"]),
            "outcomes": len(state["outcomes"]), "active": bool(active),
            "shadowPredictionAt": active.get("prediction"),
            "ownDisplay": active.get("ownDisplay", False),
            "quality": quality(state["outcomes"], mower.get("battery_percent") or 0, now)}


def display(mower, now_utc, env, *, client=None):
    if not enabled(env) or mower.get("activity") != "CHARGING" or not mower.get("mower_id"):
        return None
    try:
        cache_key = key(env, mower["mower_id"])
        with _LOCK:
            cached = _CACHE.get(cache_key)
        if cached and time.monotonic()-cached[0] < 30:
            state = deepcopy(cached[1])
        else:
            state = decode(read(client or _table_client(env), cache_key[2]))
            cache(cache_key, state)
        return own_display(state, mower, now_utc.timestamp())
    except Exception:
        return None  # Optional display storage cannot invalidate live status.
