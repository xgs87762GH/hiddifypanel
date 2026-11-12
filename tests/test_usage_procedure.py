"""Offline tests for the add_usage_json self-healing helper.

The module under test has no panel dependencies; load it by path so these tests
also run in a bare Python (no Flask/SQLAlchemy/loguru installed), unlike the
rest of the panel suite.
"""

from __future__ import annotations

import importlib.util
import pathlib
import unittest

_MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "hiddifypanel" / "panel" / "usage_procedure.py"
_spec = importlib.util.spec_from_file_location("hiddifypanel.panel.usage_procedure", _MODULE_PATH)
up = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(up)


class FakeExecute:
    """Records statements; `rows` is what the existence query returns."""

    def __init__(self, rows=((0,),), fail_on=None, error=None):
        self.rows = rows
        self.fail_on = fail_on
        self.error = error or RuntimeError("db down")
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, query, **kwargs):
        self.calls.append((query, kwargs))
        if self.fail_on and self.fail_on in query:
            raise self.error
        if kwargs.get("return_val"):
            return self.rows
        return None


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def fresh():
    up._last_failed_repair = 0.0
    return []


class MissingProcedureErrorTest(unittest.TestCase):
    def test_mysql_1305_is_recognised(self):
        exc = Exception("(MySQLdb.OperationalError) (1305, 'PROCEDURE hiddifypanel.add_usage_json does not exist')")
        self.assertTrue(up.is_missing_procedure_error(exc))

    def test_does_not_exist_is_recognised(self):
        self.assertTrue(up.is_missing_procedure_error(Exception("PROCEDURE add_usage_json does not exist")))

    def test_other_db_errors_are_not_swallowed(self):
        for message in (
            "(1064, 'You have an error in your SQL syntax')",
            "(1305, 'PROCEDURE some_other_proc does not exist')",
            "connection refused",
            "Unknown column 'add_usage_json' in field list",
        ):
            self.assertFalse(up.is_missing_procedure_error(Exception(message)), message)


class EnsureUsageProcedureTest(unittest.TestCase):
    def setUp(self):
        self.logs = fresh()

    def test_skips_non_mysql_dialects_without_touching_db(self):
        ex = FakeExecute()
        self.assertFalse(up.ensure_usage_procedure(ex, "sqlite", log=self.logs.append))
        self.assertFalse(up.ensure_usage_procedure(ex, "postgresql", log=self.logs.append))
        self.assertEqual(ex.calls, [])

    def test_existing_routine_is_left_alone(self):
        ex = FakeExecute(rows=((1,),), fail_on="DROP PROCEDURE")
        self.assertFalse(up.ensure_usage_procedure(ex, "mysql", log=self.logs.append))
        self.assertEqual(len(ex.calls), 1)  # existence query only
        self.assertIn("information_schema.ROUTINES", ex.calls[0][0])
        self.assertEqual(ex.calls[0][1], {"return_val": True})
        self.assertEqual(self.logs, [])

    def test_missing_routine_is_created_idempotently(self):
        ex = FakeExecute(rows=((0,),))
        self.assertTrue(up.ensure_usage_procedure(ex, "mariadb", log=self.logs.append))
        self.assertEqual(len(ex.calls), 2)
        ddl, kwargs = ex.calls[1]
        self.assertIn("DROP PROCEDURE IF EXISTS add_usage_json", ddl)
        self.assertIn("CREATE PROCEDURE add_usage_json", ddl)
        self.assertEqual(kwargs, {"commit": True})
        self.assertEqual(len(self.logs), 1)

    def test_empty_result_counts_as_missing(self):
        ex = FakeExecute(rows=())
        self.assertTrue(up.ensure_usage_procedure(ex, "mysql", log=self.logs.append))
        self.assertEqual(len(ex.calls), 2)

    def test_db_failure_never_raises_and_rolls_back(self):
        rolled_back = []
        ex = FakeExecute(fail_on="information_schema.ROUTINES")
        self.assertFalse(
            up.ensure_usage_procedure(ex, "mysql", log=self.logs.append, rollback=lambda: rolled_back.append(True))
        )
        self.assertEqual(len(rolled_back), 1)
        self.assertEqual(len(self.logs), 1)

    def test_ddl_failure_is_contained_and_throttled(self):
        clock = FakeClock()
        rolled_back = []
        ex = FakeExecute(fail_on="DROP PROCEDURE")
        self.assertFalse(
            up.ensure_usage_procedure(
                ex, "mysql", log=self.logs.append, rollback=lambda: rolled_back.append(True), clock=clock
            )
        )
        self.assertEqual(len(rolled_back), 1)
        self.assertEqual(len(ex.calls), 2)

        # Second tick inside the cooldown does not touch the DB again.
        ex2 = FakeExecute()
        self.assertFalse(up.ensure_usage_procedure(ex2, "mysql", log=self.logs.append, clock=clock))
        self.assertEqual(ex2.calls, [])
        self.assertEqual(len(self.logs), 1)

        # After 5 minutes it retries.
        clock.advance(up.REPAIR_RETRY_SECONDS + 1)
        ex3 = FakeExecute()
        self.assertTrue(up.ensure_usage_procedure(ex3, "mysql", log=self.logs.append, clock=clock))
        self.assertEqual(len(ex3.calls), 2)

    def test_cooldown_expires_and_success_clears_the_marker(self):
        clock = FakeClock()
        ex = FakeExecute(fail_on="DROP PROCEDURE")
        self.assertFalse(up.ensure_usage_procedure(ex, "mysql", clock=clock))
        self.assertNotEqual(up._last_failed_repair, 0.0)

        # Still inside the cooldown: do not touch the DB at all.
        ex2 = FakeExecute()
        self.assertFalse(up.ensure_usage_procedure(ex2, "mysql", clock=clock))
        self.assertEqual(ex2.calls, [])

        # Once the cooldown expires the repair succeeds and clears the marker,
        # so a later failure is throttled from a fresh timestamp.
        clock.advance(up.REPAIR_RETRY_SECONDS + 1)
        ex3 = FakeExecute(rows=((0,),))
        self.assertTrue(up.ensure_usage_procedure(ex3, "mysql", clock=clock))
        self.assertEqual(up._last_failed_repair, 0.0)

        ex4 = FakeExecute(fail_on="information_schema.ROUTINES")
        self.assertFalse(up.ensure_usage_procedure(ex4, "mysql", clock=clock))
        self.assertEqual(len(ex4.calls), 1)

    def test_rollback_exception_is_ignored(self):
        def bad_rollback():
            raise RuntimeError("no session")

        ex = FakeExecute(fail_on="information_schema.ROUTINES")
        self.assertFalse(up.ensure_usage_procedure(ex, "mysql", log=self.logs.append, rollback=bad_rollback))
        self.assertEqual(len(self.logs), 1)


class DdlShapeTest(unittest.TestCase):
    def test_ddl_is_idempotent_and_updates_both_reported_fields(self):
        ddl = up.ADD_USAGE_JSON_PROC_DDL
        self.assertIn("DROP PROCEDURE IF EXISTS add_usage_json", ddl)
        self.assertIn("CREATE PROCEDURE add_usage_json", ddl)
        self.assertIn("JSON_TABLE", ddl)
        self.assertIn("current_usage = current_usage + u_usage", ddl)
        self.assertIn("last_online = cur_time", ddl)
        self.assertIn("last_modified_time = cur_time", ddl)

    def test_existence_query_is_scoped_to_current_schema(self):
        self.assertIn("ROUTINE_SCHEMA = DATABASE()", up.EXISTENCE_SQL)
        self.assertIn("add_usage_json", up.EXISTENCE_SQL)


if __name__ == "__main__":
    unittest.main()
