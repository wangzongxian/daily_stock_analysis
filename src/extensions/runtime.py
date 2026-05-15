# -*- coding: utf-8 -*-
"""Minimal Extension Runtime for built-in DSA actions."""

from __future__ import annotations

import concurrent.futures
import time
from collections.abc import Mapping
from typing import Any, Dict, Optional

from src.extensions.actions import (
    ActionContext,
    ActionDefinition,
    ActionError,
    ActionMode,
    ActionResult,
    new_run_id,
    stable_input_hash,
)
from src.extensions.permissions import ActionPermissionGuard, ActionRuntimeError
from src.extensions.registry import ExtensionRegistry, PluginStatus
from src.extensions.tasks import ActionTaskRunner


class ExtensionRuntime:
    """Register and execute auditable DSA actions."""

    def __init__(self, *, registry=None, permission_guard=None, task_runner=None):
        self.registry: ExtensionRegistry = registry or ExtensionRegistry()
        self.permission_guard = permission_guard or ActionPermissionGuard()
        self.task_runner = task_runner or ActionTaskRunner()

    def register_action(self, action: ActionDefinition) -> None:
        self.registry.register_action(action)

    def execute_action(
        self,
        action_id: str,
        payload: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any] | ActionContext] = None,
        *,
        async_mode: Optional[bool] = None,
    ) -> ActionResult:
        run_id = new_run_id()
        if payload is None:
            payload = {}
        elif not isinstance(payload, Mapping):
            return self._failure(
                action_id,
                run_id,
                "invalid_input",
                "Action payload must be a mapping.",
                None,
                {"payload_type": type(payload).__name__},
            )
        else:
            payload = dict(payload)
        input_hash = stable_input_hash(payload)
        action = self.registry.get_action(action_id)
        if action is None:
            return self._failure(action_id, run_id, "action_not_found", "Action is not registered.", input_hash)

        try:
            context_obj = ActionContext.from_mapping(context)
        except (TypeError, ValueError) as exc:
            return self._failure(
                action.action_id,
                run_id,
                "invalid_context",
                "Action context is invalid.",
                input_hash,
                {"exception_type": exc.__class__.__name__},
            )

        preflight_failure = self._preflight(action, payload, context_obj, run_id, input_hash)
        if preflight_failure is not None:
            return preflight_failure
        if context_obj.dry_run:
            return self._dry_run_result(action, context_obj, run_id, input_hash)

        run_async = async_mode if async_mode is not None else action.mode == ActionMode.ASYNC
        if run_async:
            return self._submit_async(action, payload, context_obj, run_id, input_hash)
        return self._execute_now(action, payload, context_obj, run_id, input_hash, preflight_done=True)

    def _submit_async(self, action, payload, context, run_id, input_hash):
        try:
            task = self.task_runner.submit(
                action=action,
                payload=payload,
                context=context,
                run_id=run_id,
                run_callable=lambda: self._execute_async_task(action, payload, context, run_id, input_hash),
            )
        except ActionRuntimeError as exc:
            return self._failure(action.action_id, run_id, exc.code, exc.message, input_hash, exc.details)
        except Exception as exc:
            return self._failure(
                action.action_id,
                run_id,
                "task_submission_failed",
                "Action task submission failed.",
                input_hash,
                {"exception_type": exc.__class__.__name__},
            )
        return ActionResult(
            action.action_id,
            run_id,
            True,
            "accepted",
            task_id=task.task_id,
            input_hash=input_hash,
            metadata={"task_type": "plugin", "caller": context.caller},
        )

    def _execute_async_task(self, action, payload, context, run_id, input_hash):
        result = self._execute_now(action, payload, context, run_id, input_hash).to_dict()
        if not result.get("ok", False):
            raise RuntimeError(self._failure_message(result))
        return result

    def _execute_now(self, action, payload, context, run_id, input_hash, *, preflight_done=False):
        try:
            if not preflight_done:
                preflight_failure = self._preflight(action, payload, context, run_id, input_hash)
                if preflight_failure is not None:
                    return preflight_failure
            result = self._call_with_timeout(action, payload, context)
        except ActionRuntimeError as exc:
            return self._failure(action.action_id, run_id, exc.code, exc.message, input_hash, exc.details)
        except concurrent.futures.TimeoutError:
            return self._failure(
                action.action_id,
                run_id,
                "timeout",
                "Action execution timed out.",
                input_hash,
                {"timeout_seconds": self._timeout_seconds(action, context)},
            )
        except Exception as exc:
            return self._failure(
                action.action_id,
                run_id,
                "handler_error",
                "Action handler failed.",
                input_hash,
                {"exception_type": exc.__class__.__name__},
            )

        if isinstance(result, ActionResult):
            return result
        if not isinstance(result, dict):
            result = {"value": result}
        else:
            result = dict(result)
        degradation = result.pop("degradation", None)
        return ActionResult(
            action.action_id,
            run_id,
            True,
            "completed",
            data=result,
            degradation=degradation,
            input_hash=input_hash,
            metadata={"caller": context.caller},
        )

    def _call_with_timeout(self, action, payload, context):
        timeout_seconds = self._timeout_seconds(action, context)
        if timeout_seconds <= 0:
            return action.handler(payload, context)

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(action.handler, payload, context)
        try:
            return future.result(timeout=timeout_seconds)
        except concurrent.futures.TimeoutError:
            raise
        finally:
            executor.shutdown(wait=False)

    def _preflight(self, action, payload, context, run_id, input_hash):
        plugin_failure = self._check_plugin_status(action, run_id, input_hash)
        if plugin_failure is not None:
            return plugin_failure
        input_failure = self._validate_input_schema(action, payload, run_id, input_hash)
        if input_failure is not None:
            return input_failure
        try:
            self.permission_guard.check(action, context)
        except ActionRuntimeError as exc:
            return self._failure(action.action_id, run_id, exc.code, exc.message, input_hash, exc.details)
        return None

    def _check_plugin_status(self, action, run_id, input_hash):
        plugin = self.registry.get_plugin(action.plugin_id)
        if plugin is None:
            return self._failure(
                action.action_id,
                run_id,
                "plugin_not_found",
                "Action plugin is not registered.",
                input_hash,
                {"plugin_id": action.plugin_id},
            )
        if plugin.status not in {PluginStatus.ENABLED, PluginStatus.DEGRADED}:
            return self._failure(
                action.action_id,
                run_id,
                "plugin_not_enabled",
                "Action plugin is not enabled.",
                input_hash,
                {"plugin_id": plugin.plugin_id, "plugin_status": plugin.status.value},
            )
        return None

    def _validate_input_schema(self, action, payload, run_id, input_hash):
        schema = action.input_schema if isinstance(action.input_schema, dict) else {}
        required = schema.get("required") or []
        if not isinstance(required, (list, tuple)):
            required = []
        missing = []
        for field in required:
            key = str(field)
            value = payload.get(key)
            if key not in payload or value is None or (isinstance(value, str) and not value.strip()):
                missing.append(key)
        if missing:
            return self._failure(
                action.action_id,
                run_id,
                "invalid_input",
                "Action input is missing required fields.",
                input_hash,
                {"missing_fields": missing},
            )

        properties = schema.get("properties") or {}
        if not isinstance(properties, dict):
            return None
        field_errors = {}
        for field, field_schema in properties.items():
            key = str(field)
            if key not in payload or payload.get(key) is None or not isinstance(field_schema, dict):
                continue
            expected_type = field_schema.get("type")
            if expected_type and not self._matches_json_type(payload.get(key), expected_type):
                field_errors[key] = f"must be {self._json_type_label(expected_type)}"
        if not field_errors:
            return None
        return self._failure(
            action.action_id,
            run_id,
            "invalid_input",
            "Action input contains invalid fields.",
            input_hash,
            {"field_errors": field_errors},
        )

    @classmethod
    def _matches_json_type(cls, value, expected_type):
        if isinstance(expected_type, (list, tuple)):
            return any(cls._matches_json_type(value, item) for item in expected_type)
        if expected_type == "array":
            return isinstance(value, list)
        if expected_type == "boolean":
            return isinstance(value, bool)
        if expected_type == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected_type == "null":
            return value is None
        if expected_type == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if expected_type == "object":
            return isinstance(value, dict)
        if expected_type == "string":
            return isinstance(value, str)
        return True

    @classmethod
    def _json_type_label(cls, expected_type):
        if isinstance(expected_type, (list, tuple)):
            return " or ".join(cls._json_type_label(item) for item in expected_type)
        return {
            "array": "an array",
            "boolean": "a boolean",
            "integer": "an integer",
            "null": "null",
            "number": "a number",
            "object": "an object",
            "string": "a string",
        }.get(expected_type, str(expected_type))

    @staticmethod
    def _dry_run_result(action, context, run_id, input_hash):
        return ActionResult(
            action.action_id,
            run_id,
            True,
            "completed",
            data={"status": "validated", "dry_run": True},
            input_hash=input_hash,
            metadata={"caller": context.caller},
        )

    @staticmethod
    def _timeout_seconds(action, context):
        budget_timeout = context.budget.timeout_seconds
        return action.timeout_seconds if budget_timeout <= 0 else min(action.timeout_seconds, budget_timeout)

    @staticmethod
    def _failure_message(result):
        error = result.get("error") if isinstance(result, dict) else None
        error = error if isinstance(error, dict) else {}
        code = str(error.get("code") or result.get("status") or "action_failed")
        message = str(error.get("message") or "Action execution failed.")
        return f"{code}: {message}"

    @staticmethod
    def _failure(action_id, run_id, code, message, input_hash, details=None):
        return ActionResult(action_id, run_id, False, "failed", error=ActionError(code, message, details or {}), input_hash=input_hash)
