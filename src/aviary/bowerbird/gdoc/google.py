"""Read-only Google Docs and Drive calls, as the sync's service account.

The service account key is the JSON in GOOGLE_SERVICE_ACCOUNT (the secret
catalog-sync writes to keebdex.json). Scopes are read-only: the sync never
changes a doc.
"""

import json
from typing import Any

import google.auth.transport.requests
import httpx
from google.oauth2 import service_account

from aviary.common.config import require_env

SCOPES = [
    "https://www.googleapis.com/auth/documents.readonly",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
]
DOCS_URL = "https://docs.googleapis.com/v1/documents/{id}"
DRIVE_URL = "https://www.googleapis.com/drive/v3/files/{id}"


class DocNotFound(Exception):
    """Google answered 404: the doc was deleted or is no longer shared."""


class Google:
    def __init__(self, http: httpx.Client):
        info = json.loads(require_env("GOOGLE_SERVICE_ACCOUNT"))
        self.credentials = service_account.Credentials.from_service_account_info(
            info, scopes=SCOPES
        )
        self.http = http

    def _get(self, url: str, **params: Any) -> Any:
        if not self.credentials.valid:
            self.credentials.refresh(google.auth.transport.requests.Request())
        response = self.http.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {self.credentials.token}"},
        )
        if response.status_code == 404:
            raise DocNotFound(url)
        response.raise_for_status()
        return response.json()

    def document(self, document_id: str) -> Any:
        return self._get(DOCS_URL.format(id=document_id))

    def file(self, file_id: str) -> Any:
        return self._get(
            DRIVE_URL.format(id=file_id),
            fields="capabilities/canReadRevisions,lastModifyingUser,modifiedTime",
        )

    def revisions(self, file_id: str) -> list[Any]:
        revisions: list[Any] = []
        token = None
        while True:
            params = {
                "fields": "nextPageToken,revisions/lastModifyingUser",
                "pageSize": 1000,
            }
            if token:
                params["pageToken"] = token
            page = self._get(DRIVE_URL.format(id=file_id) + "/revisions", **params)
            revisions += page.get("revisions", [])
            token = page.get("nextPageToken")
            if not token:
                return revisions
