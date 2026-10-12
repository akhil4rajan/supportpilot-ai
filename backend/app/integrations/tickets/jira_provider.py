from __future__ import annotations

import asyncio
import logging
from typing import Any

from jira import JIRA
from jira.exceptions import JIRAError

from app.tenancy.models import JiraConfig, env

from .base import TicketError, TicketRequest, TicketResult

log = logging.getLogger(__name__)


def _wiki_safe(text: str) -> str:
    return text.replace("{noformat}", "(noformat)").replace("{code", "(code")


class JiraTicketProvider:
    """Creates Jira issues with the `jira` (pycontribs) package.

    The library is synchronous, so every call runs in a worker thread. Uses REST API v2, where the
    description is Jira wiki-markup/plain text (works on Jira Cloud and Server/Data Center).
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

    def _create_sync(self, req: TicketRequest) -> TicketResult:
        jira = self._jira()
        fields: dict[str, Any] = {
            "project": {"key": self.cfg.project_key},
            "summary": req.summary[:250].replace("\n", " "),
            "description": self._description(req),
            "issuetype": {"name": self.cfg.issue_type},
            "labels": self.cfg.labels,
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
        except JIRAError as e:
            log.warning("jira create_issue failed: %s %s", e.status_code, e.text)
            raise TicketError(f"Jira rejected the ticket (HTTP {e.status_code})") from e
        except Exception as e:  # network, auth, timeouts
            raise TicketError(f"Could not reach Jira: {e}") from e
        return TicketResult(key=issue.key, url=f"{self.cfg.server.rstrip('/')}/browse/{issue.key}", provider="jira")

    async def create_ticket(self, req: TicketRequest) -> TicketResult:
        return await asyncio.to_thread(self._create_sync, req)
