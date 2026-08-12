#!/usr/bin/env python3
"""Teambition authentication, transport, and status metadata helpers."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class TeambitionSupportMixin:
    @property
    def configured(self) -> bool:
        return bool(self.app_key and self.app_secret and self.operator_user_id and self.project_id)

    @classmethod
    def _canonical_stage_key(cls, status: str) -> str:
        key = status.strip().lower()
        return cls.STATUS_STAGE_ALIASES.get(key, key)

    @classmethod
    def _load_stage_id_map(cls, cfg: dict[str, Any]) -> dict[str, str]:
        raw = cfg.get("stage_id_map") or cfg.get("stage_ids") or {}
        stage_id_map: dict[str, str] = {}
        if isinstance(raw, dict):
            for key, value in raw.items():
                stage_id = str(value or "").strip()
                if not stage_id:
                    continue
                stage_id_map[cls._canonical_stage_key(str(key))] = stage_id
        for key in cls.STAGE_ENV_KEYS:
            env_key = key.upper()
            env_value = (
                os.environ.get(f"TEAMBITION_STAGE_ID_{env_key}")
                or os.environ.get(f"TEAMBITION_{env_key}_STAGE_ID")
            )
            if env_value:
                stage_id_map[key] = env_value.strip()
        return stage_id_map

    @classmethod
    def _load_taskflowstatus_id_map(cls, cfg: dict[str, Any]) -> dict[str, str]:
        raw = (
            cfg.get("taskflowstatus_id_map")
            or cfg.get("taskflow_status_id_map")
            or cfg.get("tfs_id_map")
            or {}
        )
        status_id_map: dict[str, str] = {}
        if isinstance(raw, dict):
            for key, value in raw.items():
                status_id = str(value or "").strip()
                if status_id:
                    status_id_map[cls._canonical_stage_key(str(key))] = status_id
        for key in cls.STAGE_ENV_KEYS:
            env_key = key.upper()
            env_value = (
                os.environ.get(f"TEAMBITION_TASKFLOWSTATUS_ID_{env_key}")
                or os.environ.get(f"TEAMBITION_TASKFLOW_STATUS_ID_{env_key}")
                or os.environ.get(f"TEAMBITION_TFS_ID_{env_key}")
            )
            if env_value:
                status_id_map[key] = env_value.strip()
        return status_id_map

    @classmethod
    def _load_taskflowstatus_name_map(cls, cfg: dict[str, Any]) -> dict[str, str]:
        raw = (
            cfg.get("taskflowstatus_name_map")
            or cfg.get("taskflow_status_name_map")
            or cfg.get("tfs_name_map")
            or {}
        )
        status_name_map: dict[str, str] = {}
        if isinstance(raw, dict):
            for key, value in raw.items():
                status_name = str(value or "").strip()
                if status_name:
                    status_name_map[cls._canonical_stage_key(str(key))] = status_name
        for key in cls.STAGE_ENV_KEYS:
            env_key = key.upper()
            env_value = (
                os.environ.get(f"TEAMBITION_TASKFLOWSTATUS_NAME_{env_key}")
                or os.environ.get(f"TEAMBITION_TASKFLOW_STATUS_NAME_{env_key}")
                or os.environ.get(f"TEAMBITION_TFS_NAME_{env_key}")
            )
            if env_value:
                status_name_map[key] = env_value.strip()
        return status_name_map

    @staticmethod
    def _b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).decode().rstrip("=")

    @classmethod
    def _sign_jwt_hs256(cls, payload: dict[str, Any], secret: str) -> str:
        header = {"typ": "JWT", "alg": "HS256"}
        head = cls._b64url(json.dumps(header, separators=(",", ":")).encode())
        body = cls._b64url(json.dumps(payload, separators=(",", ":")).encode())
        signing_input = f"{head}.{body}".encode()
        signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
        return f"{head}.{body}.{cls._b64url(signature)}"

    def _open_api_authorization_header(self) -> str:
        if self.open_api_authorization:
            authorization = self.open_api_authorization.strip()
            return authorization if authorization.lower().startswith("bearer ") else f"Bearer {authorization}"
        if not (self.open_app_id and self.open_app_secret):
            raise RuntimeError(
                "TEAMBITION_OPEN_API_AUTHORIZATION_MISSING: 请配置 TEAMBITION_OPEN_API_AUTHORIZATION，"
                "或配置 TEAMBITION_OPEN_APP_ID / TEAMBITION_OPEN_APP_SECRET 以本地签发 appAccessToken"
            )
        now = int(time.time())
        payload = {
            "_appId": self.open_app_id,
            "iat": now,
            "exp": now + 3600,
        }
        return f"Bearer {self._sign_jwt_hs256(payload, self.open_app_secret)}"

    def _get_access_token(self) -> str:
        if self._token and time.time() < self._token_expires - 60:
            return self._token
        url = (
            "https://oapi.dingtalk.com/gettoken?"
            + urllib.parse.urlencode({"appkey": self.app_key, "appsecret": self.app_secret})
        )
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        if data.get("errcode") != 0:
            raise RuntimeError(f"DINGTALK_TOKEN_FAIL: {data.get('errmsg')}")
        self._token = data["access_token"]
        self._token_expires = time.time() + int(data.get("expires_in", 7200))
        return self._token

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        token = self._get_access_token()
        url = f"{self.api_host}{path}"
        headers = {
            "Content-Type": "application/json",
            "x-acs-dingtalk-access-token": token,
        }
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            err_body = e.read().decode(errors="replace")
            raise RuntimeError(f"TEAMBITION_HTTP_{e.code}: {err_body[:500]}") from e

    def _open_request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        query: dict | None = None,
    ) -> dict:
        if not self.tenant_id:
            raise RuntimeError("TEAMBITION_TENANT_ID_MISSING")
        authorization = self._open_api_authorization_header()
        url = f"{self.open_api_host}{path}"
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v not in ("", None)})
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": authorization,
                "Content-Type": "application/json",
                "X-Tenant-Id": self.tenant_id,
                "X-Tenant-Type": self.tenant_type or "organization",
                **({"x-operator-id": self.open_operator_id} if self.open_operator_id else {}),
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            err_body = e.read().decode(errors="replace")
            raise RuntimeError(f"TEAMBITION_OPEN_HTTP_{e.code}: {err_body[:500]}") from e

    @staticmethod
    def _scenario_config_list(data: dict[str, Any]) -> list[dict[str, Any]]:
        result = data.get("result") or data.get("data") or []
        if isinstance(result, list):
            return [x for x in result if isinstance(x, dict)]
        if not isinstance(result, dict):
            return []
        for key in ("data", "list", "items", "scenariofieldconfigs", "scenarioFieldConfigs", "result"):
            value = result.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        return [result] if result else []

    @staticmethod
    def _scenario_config_id(config: dict[str, Any]) -> str:
        for key in ("_id", "id", "scenariofieldconfigId", "scenarioFieldConfigId"):
            if config.get(key):
                return str(config[key])
        return ""

    @staticmethod
    def _scenario_config_name(config: dict[str, Any]) -> str:
        for key in ("name", "displayName", "title", "label"):
            if config.get(key):
                return str(config[key])
        return ""

    def scenario_field_configs(
        self,
        keyword: str = "",
        sfc_ids: str = "",
        page_token: str = "",
        page_size: int | None = 50,
    ) -> list[dict[str, Any]]:
        query: dict[str, Any] = {}
        if keyword:
            query["q"] = keyword
        if sfc_ids:
            query["sfcIds"] = sfc_ids
        if page_token:
            query["pageToken"] = page_token
        if page_size:
            query["pageSize"] = page_size
        data = self._open_request("GET", "/api/v3/scenariofieldconfig/search", query=query)
        error_code = str(data.get("errorCode") or "")
        if error_code and error_code != "0":
            raise RuntimeError(f"TEAMBITION_SCENARIO_CONFIG_FAIL: {error_code} {data.get('errorMessage') or data}")
        configs: list[dict[str, Any]] = []
        for config in self._scenario_config_list(data):
            config_id = self._scenario_config_id(config)
            if not config_id:
                continue
            configs.append(
                {
                    "id": config_id,
                    "name": self._scenario_config_name(config),
                    "raw": config,
                }
            )
        return configs

    @staticmethod
    def _result_list(data: dict[str, Any]) -> list[dict[str, Any]]:
        result = data.get("result") or data.get("data") or []
        if isinstance(result, list):
            return [x for x in result if isinstance(x, dict)]
        if not isinstance(result, dict):
            return []
        for key in ("data", "list", "items", "result"):
            value = result.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        return [result] if result else []

    @staticmethod
    def _open_error_message(data: dict[str, Any]) -> str:
        error_code = str(data.get("errorCode") or "")
        if error_code:
            return f"{error_code} {data.get('errorMessage') or data}"
        code = str(data.get("code") or "")
        if code and code not in {"0", "200", "204"}:
            return f"{code} {data.get('errorMessage') or data}"
        return ""

    def taskflow_statuses(self, work_item_id: str) -> list[dict[str, Any]]:
        data = self._open_request("GET", f"/api/v3/task/{urllib.parse.quote(work_item_id, safe='')}/tfs")
        error = self._open_error_message(data)
        if error:
            raise RuntimeError(f"TEAMBITION_TASKFLOWSTATUS_LIST_FAIL: {error}")
        return self._result_list(data)

    @classmethod
    def _find_taskflowstatus(
        cls,
        stage_key: str,
        statuses: list[dict[str, Any]],
        preferred_name: str = "",
    ) -> dict[str, Any] | None:
        def norm(value: Any) -> str:
            return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")

        preferred = norm(preferred_name)
        if preferred:
            for status in statuses:
                if norm(status.get("name")) == preferred:
                    return status

        for expected_kind in cls.DEFAULT_TASKFLOWSTATUS_KINDS.get(stage_key, ()):
            expected = norm(expected_kind)
            for status in statuses:
                if norm(status.get("kind")) == expected:
                    return status

        expected_names = {norm(name) for name in cls.DEFAULT_TASKFLOWSTATUS_NAMES.get(stage_key, ())}
        for status in statuses:
            if norm(status.get("name")) in expected_names:
                return status
        return None

    def _validate_operator_user_id(self) -> tuple[bool, str]:
        """确认操作者是钉钉 userId（非 Teambition 内部 _id）。"""
        if not self.app_key or not self.app_secret:
            return True, "SKIP_NO_CREDENTIALS"
        try:
            token = self._get_access_token()
            url = f"https://oapi.dingtalk.com/topapi/v2/user/get?access_token={token}"
            body = json.dumps({"userid": self.operator_user_id}).encode()
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
            if data.get("errcode") == 0:
                name = (data.get("result") or {}).get("name") or ""
                return True, f"DINGTALK_USER_OK: {name}"
            if data.get("errcode") == 60121:
                return False, (
                    "DINGTALK_USER_NOT_FOUND: DINGTALK_OPERATOR_USER_ID 须为钉钉通讯录 userId"
                    "（纯数字，非 Teambition 的 24 位十六进制 _id）。"
                    "请在钉钉管理后台 → 通讯录 → 成员详情查看，"
                    "或运行: bash .harness/scripts/work_item.sh diagnose"
                )
            return False, f"DINGTALK_USER_ERR: {data.get('errmsg')}"
        except Exception as e:
            return False, f"DINGTALK_USER_CHECK_FAIL: {e}"
