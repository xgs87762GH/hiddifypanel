"""Self-healing for the MariaDB ``add_usage_json`` stored procedure.

The routine is created by the ``_v140`` migration. A restore that brings the
data back with ``db_version`` already at the latest value makes ``init_db()``
return early, so the migration never runs again and the routine stays missing.
Usage accounting then fails with MySQL 1305 on every tick, filling the panel
log while the admin UI shows zero traffic and "never online" for every user.

This module deliberately has no ``hiddifypanel`` imports: it is loaded very
early during app init and is unit-tested without the panel's dependencies.
The database calls are injected so the logic stays fully offline-testable.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

PROCEDURE_NAME = "add_usage_json"

EXISTENCE_SQL = (
    "SELECT COUNT(*) FROM information_schema.ROUTINES "
    "WHERE ROUTINE_SCHEMA = DATABASE() AND ROUTINE_NAME = 'add_usage_json'"
)

MYSQL_DIALECTS = frozenset({"mysql", "mariadb"})

# Single source of truth for the DDL: _v140 and the startup repair both use it.
# Keep the statements exactly as the migration used them (DROP + CREATE in one
# batch), so installing a fresh panel and repairing an existing one are the
# same operation.
ADD_USAGE_JSON_PROC_DDL = """
DROP PROCEDURE IF EXISTS add_usage_json;

CREATE PROCEDURE add_usage_json(IN usage_data JSON, IN cur_time DATETIME)
BEGIN
  DECLARE u_id INT DEFAULT NULL;
  DECLARE u_uuid CHAR(36) DEFAULT NULL;
  DECLARE u_usage BIGINT;
  DECLARE done BOOL DEFAULT FALSE;
  DECLARE cur_date DATE;


  DECLARE cur CURSOR FOR
    SELECT  jt.uuid, jt.usage FROM JSON_TABLE(
      usage_data, '$[*]' COLUMNS (
        uuid CHAR(36) PATH '$.uuid', `usage` BIGINT PATH '$.usage')) AS jt;

  DECLARE CONTINUE HANDLER FOR NOT FOUND SET done = TRUE;
  SET cur_date = DATE(cur_time);
  OPEN cur;

  read_loop: LOOP
    FETCH cur INTO  u_uuid, u_usage;
    IF done THEN
      LEAVE read_loop;
    END IF;


    UPDATE `user`
    SET current_usage = current_usage + u_usage,
        last_online = cur_time,
        last_modified_time = cur_time,
        start_date = CASE WHEN start_date IS NULL THEN cur_date ELSE start_date END
    WHERE uuid = u_uuid;


    COMMIT;
  END LOOP;

  CLOSE cur;
END
    """

# A failed repair (missing CREATE ROUTINE grant, dropped DB, ...) is retried at
# most once every 5 minutes instead of on every minute's usage tick. A
# successful repair clears the cooldown so a routine deleted later is rebuilt
# immediately.
REPAIR_RETRY_SECONDS = 300.0
_last_failed_repair = 0.0


def is_missing_procedure_error(exc: BaseException) -> bool:
    """True only for our routine being absent (MySQL 1305), never for other DB errors."""
    message = str(exc).lower()
    if PROCEDURE_NAME not in message:
        return False
    return "1305" in message or "does not exist" in message or "undefined" in message


def ensure_usage_procedure(
    execute: Callable[..., Any],
    dialect_name: str,
    log: Callable[[str], None] | None = None,
    rollback: Callable[[], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Create ``add_usage_json`` if it is missing. Returns True when it was created.

    ``execute`` must match :func:`hiddifypanel.database.db_execute`: it runs a
    statement and returns rows when called with ``return_val=True``.

    Never raises: a broken or permission-limited database must not stop the
    panel from booting. The caller decides what to do when we return False.
    """
    global _last_failed_repair

    if dialect_name not in MYSQL_DIALECTS:
        return False
    if _last_failed_repair and clock() - _last_failed_repair < REPAIR_RETRY_SECONDS:
        return False

    try:
        rows = execute(EXISTENCE_SQL, return_val=True)
        if rows and rows[0][0]:
            _last_failed_repair = 0.0
            return False
        execute(ADD_USAGE_JSON_PROC_DDL, commit=True)
        _last_failed_repair = 0.0
        if log:
            log("recreated missing SQL procedure add_usage_json")
        return True
    except Exception as exc:
        _last_failed_repair = clock()
        # SQLAlchemy leaves the session in a failed state after a statement
        # error; without this the rest of init_db() would fail too.
        if rollback is not None:
            try:
                rollback()
            except Exception:
                pass
        if log:
            log(f"could not ensure add_usage_json exists: {exc}")
        return False
