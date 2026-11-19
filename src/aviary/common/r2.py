"""The R2 backup bucket, through its S3 API, plus its lock rules.

A port of catalog-sync's utils/r2.js. Backups of Cloudflare images are keyed
by image id (`images/<id>`), so a restore re-uploads each file under its old
id and every stored delivery URL works again.
"""

from dataclasses import dataclass
from datetime import datetime
from functools import cached_property

import boto3
import httpx
from botocore.config import Config
from botocore.exceptions import ClientError

from aviary.common.config import require_env

IMAGES_PREFIX = "images/"
TRASH_PREFIX = "trash/images/"


@dataclass(frozen=True)
class StoredFile:
    data: bytes
    content_type: str | None


class Bucket:
    def __init__(self) -> None:
        self.account_id = require_env("R2_ACCOUNT_ID")
        self.name = require_env("R2_BUCKET")
        self._key_id = require_env("R2_ACCESS_KEY_ID")
        self._secret = require_env("R2_SECRET_ACCESS_KEY")

    @cached_property
    def s3(self):
        # Checksums only when required: R2 rejects some of the integrity
        # headers newer SDKs send by default.
        return boto3.client(
            "s3",
            region_name="auto",
            endpoint_url=f"https://{self.account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=self._key_id,
            aws_secret_access_key=self._secret,
            config=Config(
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
                max_pool_connections=10,
            ),
        )

    def list_objects(self, prefix: str) -> dict[str, datetime]:
        """Every object under `prefix`, as key -> last modified."""
        objects: dict[str, datetime] = {}
        paginator = self.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.name, Prefix=prefix):
            for item in page.get("Contents", []):
                objects[item["Key"]] = item["LastModified"]
        return objects

    def put(
        self, key: str, data: bytes, content_type: str | None, metadata: dict
    ) -> None:
        extra = {"ContentType": content_type} if content_type else {}
        self.s3.put_object(
            Bucket=self.name, Key=key, Body=data, Metadata=metadata, **extra
        )

    def get(self, key: str) -> StoredFile | None:
        """The object, or None when the key does not exist."""
        try:
            response = self.s3.get_object(Bucket=self.name, Key=key)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == "NoSuchKey":
                return None
            raise
        return StoredFile(response["Body"].read(), response.get("ContentType"))

    def move(self, source: str, target: str) -> None:
        """Copy, then delete: a failed delete leaves the object in both places
        rather than in neither. Metadata is copied along."""
        self.s3.copy_object(
            Bucket=self.name,
            CopySource={"Bucket": self.name, "Key": source},
            Key=target,
        )
        self.s3.delete_object(Bucket=self.name, Key=source)

    def lock_rules(self, http: httpx.Client) -> list[dict]:
        """The bucket's lock rules, through the Cloudflare API (the S3 API has
        no view of them). Needs R2_API_TOKEN, a token with R2 read access."""
        token = require_env("R2_API_TOKEN")
        response = http.get(
            f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}"
            f"/r2/buckets/{self.name}/lock",
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        return (response.json().get("result") or {}).get("rules") or []
