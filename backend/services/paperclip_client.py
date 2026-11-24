"""Paperclip REST client (작고 동기식). backend(board)와 Deep Review worker(agent)가 같이 쓴다.

- backend: Paperclip이 local_trusted + private(loopback)이라 인증 헤더 없이 board로 호출한다.
- worker: Paperclip이 run마다 발급한 짧은 JWT(PAPERCLIP_API_KEY)와 X-Paperclip-Run-Id로 호출한다 (agent 권한).
- token / API key는 로그·오류 메시지에 넣지 않는다.
"""

from __future__ import annotations

import json
import re

import httpx

from config import PAPERCLIP_API_URL


class PaperclipError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class PaperclipUnavailable(PaperclipError):
    """Paperclip 서버에 연결할 수 없음 (꺼짐 / timeout)"""


_TOKEN_RE = re.compile(r"(Bearer\s+)\S+|eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+")


def _scrub(text: str) -> str:
    return _TOKEN_RE.sub(lambda m: (m.group(1) or "") + "***", text or "")


class PaperclipClient:
    def __init__(self, base_url: str = PAPERCLIP_API_URL, token: str | None = None, run_id: str | None = None,
                 timeout: float = 10.0, transport: httpx.BaseTransport | None = None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if run_id:
            headers["X-Paperclip-Run-Id"] = run_id
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, headers=headers, timeout=timeout, transport=transport)

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _req(self, method: str, path: str, body: dict | None = None, params: dict | None = None):
        try:
            res = self._client.request(method, path, json=body, params=params)
        except httpx.TransportError as e:
            raise PaperclipUnavailable(f"Paperclip({self.base_url})에 연결할 수 없습니다: {type(e).__name__}") from e
        if res.status_code >= 400:
            raise PaperclipError(_scrub(f"Paperclip {method} {path} → HTTP {res.status_code}: {res.text[:300]}"), res.status_code)
        if not res.content:
            return None
        try:
            return res.json()
        except ValueError as e:
            raise PaperclipError(f"Paperclip {method} {path}: JSON이 아닌 응답") from e

    # ---- server
    def health(self) -> dict:
        return self._req("GET", "/health")

    # ---- organisation (setup script)
    def list_companies(self) -> list[dict]:
        return self._req("GET", "/companies")

    def create_company(self, body: dict) -> dict:
        return self._req("POST", "/companies", body)

    def patch_company(self, company_id: str, body: dict) -> dict:
        return self._req("PATCH", f"/companies/{company_id}", body)

    def set_company_budget(self, company_id: str, cents: int) -> dict:
        return self._req("PATCH", f"/companies/{company_id}/budgets", {"budgetMonthlyCents": cents})

    def list_agents(self, company_id: str) -> list[dict]:
        return self._req("GET", f"/companies/{company_id}/agents")

    def create_agent(self, company_id: str, body: dict) -> dict:
        return self._req("POST", f"/companies/{company_id}/agents", body)

    def patch_agent(self, agent_id: str, body: dict) -> dict:
        return self._req("PATCH", f"/agents/{agent_id}", body)

    def get_agent(self, agent_id: str) -> dict:
        return self._req("GET", f"/agents/{agent_id}")

    def set_agent_budget(self, agent_id: str, cents: int) -> dict:
        return self._req("PATCH", f"/agents/{agent_id}/budgets", {"budgetMonthlyCents": cents})

    def list_goals(self, company_id: str) -> list[dict]:
        return self._req("GET", f"/companies/{company_id}/goals")

    def create_goal(self, company_id: str, body: dict) -> dict:
        return self._req("POST", f"/companies/{company_id}/goals", body)

    def list_projects(self, company_id: str) -> list[dict]:
        return self._req("GET", f"/companies/{company_id}/projects")

    def create_project(self, company_id: str, body: dict) -> dict:
        return self._req("POST", f"/companies/{company_id}/projects", body)

    def upsert_budget_policy(self, company_id: str, body: dict) -> dict:
        return self._req("POST", f"/companies/{company_id}/budgets/policies", body)

    def budget_overview(self, company_id: str) -> dict:
        return self._req("GET", f"/companies/{company_id}/budgets/overview")

    def resolve_budget_incident(self, company_id: str, incident_id: str, body: dict) -> dict:
        return self._req("POST", f"/companies/{company_id}/budget-incidents/{incident_id}/resolve", body)

    # ---- issues (tickets)
    def create_issue(self, company_id: str, body: dict) -> dict:
        return self._req("POST", f"/companies/{company_id}/issues", body)

    def get_issue(self, issue_id: str) -> dict:
        return self._req("GET", f"/issues/{issue_id}")

    def patch_issue(self, issue_id: str, body: dict) -> dict:
        return self._req("PATCH", f"/issues/{issue_id}", body)

    def checkout_issue(self, issue_id: str, agent_id: str, expected: list[str]) -> dict:
        return self._req("POST", f"/issues/{issue_id}/checkout", {"agentId": agent_id, "expectedStatuses": expected})

    def list_comments(self, issue_id: str) -> list[dict]:
        data = self._req("GET", f"/issues/{issue_id}/comments", params={"order": "asc", "limit": 200})
        return data if isinstance(data, list) else (data or {}).get("items", [])

    def add_comment(self, issue_id: str, body: str) -> dict:
        return self._req("POST", f"/issues/{issue_id}/comments", {"body": body})

    # ---- heartbeat runs
    def get_run(self, run_id: str) -> dict:
        return self._req("GET", f"/heartbeat-runs/{run_id}")

    def list_runs(self, company_id: str, limit: int = 100) -> list[dict]:
        data = self._req("GET", f"/companies/{company_id}/heartbeat-runs", params={"limit": limit})
        return data if isinstance(data, list) else (data or {}).get("items", [])

    def cancel_run(self, run_id: str) -> dict:
        return self._req("POST", f"/heartbeat-runs/{run_id}/cancel", {})

    # ---- costs
    def report_cost(self, company_id: str, body: dict) -> dict:
        return self._req("POST", f"/companies/{company_id}/cost-events", body)


def dumps(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)
