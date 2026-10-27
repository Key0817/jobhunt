"""Native, read-only Gmail operations used by the alert importer."""
import base64
from urllib.parse import quote, urlencode

from ..gmail_oauth import gmail_get


class GmailAPIClient:
    def __init__(self, token):
        self._token = token

    def label_id(self, name: str) -> str | None:
        labels = gmail_get(self._token, "labels").get("labels", [])
        return next((label["id"] for label in labels if label.get("name") == name), None)

    def message_ids(self, label_id: str) -> tuple[list[str], bool]:
        query = {"labelIds": label_id, "q": "is:unread", "maxResults": 500}
        ids = []
        for _ in range(5):
            result = gmail_get(self._token, "messages", urlencode(query))
            ids.extend(item["id"] for item in result.get("messages", []))
            token = result.get("nextPageToken")
            if not token:
                return list(dict.fromkeys(ids)), False
            query["pageToken"] = token
        return list(dict.fromkeys(ids)), True

    def message_size(self, message_id: str) -> int:
        data = gmail_get(self._token, "messages/" + quote(message_id, safe=""), "format=minimal&fields=sizeEstimate")
        return int(data["sizeEstimate"])

    def raw_message(self, message_id: str) -> bytes:
        data = gmail_get(self._token, "messages/" + quote(message_id, safe=""), "format=raw&fields=raw")
        raw = data["raw"]
        return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
