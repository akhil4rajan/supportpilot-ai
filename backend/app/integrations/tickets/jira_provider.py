from __future__ import annotations

import asyncio
import logging
from typing import Any

from jira import JIRA
from jira.exceptions import JIRAError

from app.tenancy.models import JiraConfig, env

from .base import TicketError, TicketRequest, TicketResult

log = logging.getLogger(__name__)
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


def _wiki_safe(text: str) -> str:
    return text.replace("{noformat}", "(noformat)").replace("{code", "(code")


class JiraTicketProvider:
    """Creates Jira issues with the `jira` (pycontribs) package.

    The library is synchronous, so every call runs in a worker thread. Uses REST API v2, where the
    description is Jira wiki-markup/plain text (works on Jira Cloud and Server/Data Center).

    Idempotency: every issue gets a label `sp-<idempotency_key>`. On a retry (attempt > 0) we first search for that
    label, because a timed-out create may actually have succeeded - this prevents duplicate issues.
    """

    def __init__(self, cfg: JiraConfig, client: JIRA | None = None) -> None:
        self.cfg = cfg
        self._client = client

    def _jira(self) -> JIRA:
        if self._client is None:
            email, token = env(self.cfg.email_env), env(self.cfg.api_token_env)
            if not email or not token:
                raise TicketError(f"Jira credentials missing: set {self.cfg.email_env} and {self.cfg.api_token_env}")
            self._client = JIRA(server=self.cfg.server, basic_auth=(email, token), timeout=15, max_retries=1)
        return self._client

    def _description(self, req: TicketRequest) -> str:
        lines = [_wiki_safe(req.description), "", "h3. Reporter"]
        lines.append(f"* Name: {req.reporter_name or 'n/a'}")
        lines.append(f"* Email: {req.reporter_email or 'n/a'}")
        lines.append(f"* Customer ID: {req.reporter_id or 'anonymous'}")
        lines.append(f"* Conversation: {req.conversation_id or 'n/a'}")
        if req.category:
            lines.append(f"* Category: {req.category}")
        if req.transcript:
            lines += ["", "h3. Chat transcript", "{noformat}", _wiki_safe(req.transcript)[:20000], "{noformat}"]
        return "\n".join(lines)

    def _result(self, key: str) -> TicketResult:
        return TicketResult(key=key, url=f"{self.cfg.server.rstrip('/')}/browse/{key}", provider="jira")

    @staticmethod
    def _classify(e: Exception) -> TicketError:
        if isinstance(e, JIRAError):
            retry = (e.status_code in RETRYABLE_STATUS) if e.status_code else True
            return TicketError(f"Jira rejected the ticket (HTTP {e.status_code})", retryable=retry)
        return TicketError(f"Could not reach Jira: {e}", retryable=True)  # network/timeouts

    def _create_sync(self, req: TicketRequest) -> TicketResult:
        jira = self._jira()
        label = f"sp-{req.idempotency_key}" if req.idempotency_key else None
        if label and req.attempt > 0:
            try:
                found = jira.search_issues(f'project = "{self.cfg.project_key}" AND labels = "{label}"', maxResults=1)
                if found:
                    log.info("jira: issue for %s already exists (%s); not creating a duplicate", label, found[0].key)
                    return self._result(found[0].key)
            except Exception as e:  # noqa: BLE001 - if we cannot check, attempting the create is the lesser evil
                log.warning("jira idempotency lookup failed: %s", e)
        fields: dict[str, Any] = {
            "project": {"key": self.cfg.project_key},
            "summary": req.summary[:250].replace("\n", " "),
            "description": self._description(req),
            "issuetype": {"name": self.cfg.issue_type},
            "labels": [*self.cfg.labels, *([label] if label else [])],
        }
        prio = self.cfg.priority_map.get(req.priority)
        if self.cfg.set_priority and prio:
            fields["priority"] = {"name": prio}
        try:
            try:
                issue = jira.create_issue(fields=fields)
            except JIRAError as e:
                # Priority is frequently not on the project's create screen; retry without it.
                if "priority" in fields and e.text and "priority" in e.text.lower():
                    fields.pop("priority")
                    issue = jira.create_issue(fields=fields)
                else:
                    raise
        except Exception as e:  # noqa: BLE001
            err = self._classify(e)
            log.warning("jira create_issue failed (retryable=%s): %s", err.retryable, e)
            raise err from e
        return self._result(issue.key)

    async def create_ticket(self, req: TicketRequest) -> TicketResult:
        return await asyncio.to_thread(self._create_sync, req)
