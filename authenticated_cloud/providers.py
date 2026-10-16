"""Private, bounded provider operations. IDs are derived from verified ownership."""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from uuid import UUID

import httpx

from authenticated_cloud.auth import CloudError
from authenticated_cloud.diagnostics import configuration_error, startup_diagnostic

MAX_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, repr=False)
class Config:
    supabase_url: str
    supabase_key: str
    app_url: str
    cloud_name: str
    cloud_key: str
    cloud_secret: str
    pinecone_key: str
    pinecone_index: str
    pinecone_host: str
    groq_key: str
    write_key: str

    @classmethod
    def load(cls, values):
        try:
            if "FREE_PLANS_CONFIRMED" not in values:
                configuration_error("secrets", "missing_variable", field="FREE_PLANS_CONFIRMED")
            if values["FREE_PLANS_CONFIRMED"] is not True:
                configuration_error("secrets", "boolean_true_required", field="FREE_PLANS_CONFIRMED")
            keys = ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "APP_URL", "CLOUDINARY_CLOUD_NAME",
                    "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET", "PINECONE_API_KEY",
                    "PINECONE_INDEX", "PINECONE_HOST", "GROQ_API_KEY", "CLOUD_WRITE_KEY")
            for key in keys:
                if key not in values:
                    configuration_error("secrets", "missing_variable", field=key)
                if not isinstance(values[key], str) or not values[key]:
                    configuration_error("secrets", "nonempty_string_required", field=key)
            result = cls(*(values[key] for key in keys))
            if not re.fullmatch(r"[a-z0-9-]+", result.cloud_name):
                configuration_error("secrets", "cloud_name_format", field="CLOUDINARY_CLOUD_NAME")
            if not re.fullmatch(r"[a-z0-9-]+", result.pinecone_index):
                configuration_error("secrets", "index_name_format", field="PINECONE_INDEX")
            if not re.fullmatch(r"https://[a-z0-9.-]+\.svc\.[a-z0-9.-]+\.pinecone\.io", result.pinecone_host):
                configuration_error("secrets", "index_host_format", field="PINECONE_HOST")
            # New publishable key, NOT a service-role or legacy JWT key.
            if not result.supabase_key.startswith("sb_publishable_"):
                configuration_error("secrets", "publishable_key_required", field="SUPABASE_PUBLISHABLE_KEY")
            return result
        except (KeyError, TypeError, ValueError):
            configuration_error("secrets", "secrets_parse_or_shape_error")


def scope(user_id, doc_id=None):
    try:
        owner = str(UUID(user_id))
        if owner != user_id:
            raise ValueError
        namespace = "dm-" + UUID(owner).hex
        if doc_id is None:
            return namespace
        if not isinstance(doc_id, str) or not re.fullmatch(r"[a-f0-9]{64}", doc_id):
            raise ValueError
        return namespace, f"documind/{namespace}/{doc_id}.pdf"
    except (ValueError, TypeError, AttributeError):
        raise CloudError("document") from None


class RemoteStore:
    def __init__(self, config, *, transport=None, clock=time.time):
        self.config, self.transport, self.clock = config, transport, clock

    def _request(self, method, url, *, _startup=False, **kwargs):
        try:
            with httpx.Client(timeout=30, follow_redirects=False, transport=self.transport) as client:
                result = client.request(method, url, **kwargs)
            if result.status_code == 429:
                if _startup:
                    startup_diagnostic("pinecone_index", "describe_rate_limited", http_status=429)
                raise CloudError("limit")
            result.raise_for_status()
            return result.json()
        except CloudError:
            raise
        except httpx.HTTPStatusError as error:
            if _startup:
                startup_diagnostic("pinecone_index", "describe_http_error", http_status=error.response.status_code)
            raise CloudError("unavailable") from None
        except httpx.TimeoutException:
            if _startup:
                startup_diagnostic("pinecone_index", "describe_timeout")
            raise CloudError("unavailable") from None
        except httpx.RequestError:
            if _startup:
                startup_diagnostic("pinecone_index", "describe_network_error")
            raise CloudError("unavailable") from None
        except ValueError:
            if _startup:
                startup_diagnostic("pinecone_index", "describe_invalid_json")
            raise CloudError("unavailable") from None
        except Exception:
            if _startup:
                startup_diagnostic("pinecone_index", "describe_failed")
            raise CloudError("unavailable") from None

    def _cloud(self, operation, fields, data=None):
        values = {**fields, "timestamp": str(int(self.clock())), "type": "private"}
        signed = "&".join(f"{key}={values[key]}" for key in sorted(values))
        values["signature"] = hashlib.sha256((signed + self.config.cloud_secret).encode()).hexdigest()
        values["api_key"] = self.config.cloud_key
        return self._request("POST", f"https://api.cloudinary.com/v1_1/{self.config.cloud_name}/raw/{operation}",
                             data=values, **({"files": {"file": ("document.pdf", data, "application/pdf")}}
                                           if data is not None else {}))

    def upload_pdf(self, owner, doc_id, data):
        _, public_id = scope(owner, doc_id)
        if not isinstance(data, bytes) or len(data) > MAX_BYTES or not data.startswith(b"%PDF-"):
            raise CloudError("pdf")
        result = self._cloud("upload", {"public_id": public_id, "overwrite": "false"}, data)
        if (result.get("public_id") != public_id or result.get("resource_type") != "raw"
                or result.get("type") != "private" or result.get("bytes") != len(data)):
            raise CloudError("unavailable")

    def delete_pdf(self, owner, doc_id):
        _, public_id = scope(owner, doc_id)
        result = self._cloud("destroy", {"public_id": public_id, "invalidate": "true"})
        if result.get("result") not in ("ok", "not found"):
            raise CloudError("unavailable")

    def download_pdf(self, owner, doc_id):
        _, public_id = scope(owner, doc_id)
        fields = {"public_id": public_id, "format": "pdf", "type": "private", "timestamp": str(int(self.clock())),
                  "expires_at": str(int(self.clock()) + 30)}
        signature = hashlib.sha256(("&".join(f"{key}={fields[key]}" for key in sorted(fields))
                                    + self.config.cloud_secret).encode()).hexdigest()
        try:
            # Private download is fetched server-side. Its URL is never returned to Streamlit.
            with httpx.Client(timeout=30, follow_redirects=False, transport=self.transport) as client:
                with client.stream("GET", f"https://api.cloudinary.com/v1_1/{self.config.cloud_name}/raw/download",
                                   params={**fields, "signature": signature, "api_key": self.config.cloud_key}) as response:
                    response.raise_for_status()
                    data = bytearray()
                    for part in response.iter_bytes(chunk_size=65536):
                        data.extend(part)
                        if len(data) > MAX_BYTES:
                            raise ValueError
            result = bytes(data)
            if not result.startswith(b"%PDF-") or hashlib.sha256(result).hexdigest() != doc_id:
                raise ValueError
            return result
        except Exception:
            raise CloudError("unavailable") from None

    def _pinecone(self, path, payload):
        return self._request("POST", self.config.pinecone_host + path, json=payload,
                             headers={"Api-Key": self.config.pinecone_key, "X-Pinecone-API-Version": "2025-10"})

    def validate_index(self):
        result = self._request("GET", "https://api.pinecone.io/indexes/" + self.config.pinecone_index,
                               _startup=True,
                               headers={"Api-Key": self.config.pinecone_key, "X-Pinecone-API-Version": "2025-10"})
        if not isinstance(result, dict):
            configuration_error("pinecone_index", "describe_shape_invalid")
        if result.get("dimension") != 384:
            configuration_error("pinecone_index", "dimension_mismatch")
        if result.get("metric") != "cosine":
            configuration_error("pinecone_index", "metric_mismatch")
        if not isinstance(result.get("host"), str) or "https://" + result["host"] != self.config.pinecone_host:
            configuration_error("pinecone_index", "host_mismatch", field="PINECONE_HOST")
        spec = result.get("spec")
        serverless = spec.get("serverless") if isinstance(spec, dict) else None
        if not isinstance(serverless, dict):
            configuration_error("pinecone_index", "serverless_shape_invalid")
        if serverless.get("cloud") != "aws":
            configuration_error("pinecone_index", "serverless_cloud_mismatch")
        if serverless.get("region") != "us-east-1":
            configuration_error("pinecone_index", "serverless_region_mismatch")
        if serverless != {"cloud": "aws", "region": "us-east-1"}:
            configuration_error("pinecone_index", "serverless_extra_fields")
        status = result.get("status")
        if not isinstance(status, dict) or status.get("ready") is not True:
            configuration_error("pinecone_index", "index_not_ready")

    def upsert(self, owner, doc_id, chunks, vectors):
        namespace, _ = scope(owner, doc_id)
        if not 0 < len(chunks) <= 200 or len(vectors) != len(chunks):
            raise CloudError("pdf")
        rows = [{"id": f"{doc_id}:{i:04d}", "values": list(map(float, vector)),
                 "metadata": {"owner": owner, "doc_id": doc_id, "text": chunk.text,
                              "page": chunk.page_number, "page_end": chunk.page_end}}
                for i, (chunk, vector) in enumerate(zip(chunks, vectors))]
        for i in range(0, len(rows), 25):
            self._pinecone("/vectors/upsert", {"namespace": namespace, "vectors": rows[i:i + 25]})

    def query(self, owner, documents, vector):
        namespace = scope(owner)
        result = self._pinecone("/query", {"namespace": namespace, "vector": list(map(float, vector)),
            "topK": 6, "includeMetadata": True, "includeValues": False,
            "filter": {"$and": [{"owner": {"$eq": owner}}, {"doc_id": {"$in": list(documents)}}]}})
        matches = result.get("matches", [])
        if len(matches) > 6:
            raise CloudError("unavailable")
        for match in matches:
            metadata = match.get("metadata", {})
            doc_id = metadata.get("doc_id")
            if (metadata.get("owner") != owner or doc_id not in documents
                    or not re.fullmatch(re.escape(str(doc_id)) + r":[0-9]{4}", str(match.get("id", "")))
                    or not isinstance(metadata.get("text"), str) or len(metadata["text"]) > 12000
                    or not 1 <= metadata.get("page", 0) <= metadata.get("page_end", 0) <= documents[doc_id]["pages"]):
                raise CloudError("document")
        return matches

    def delete_vectors(self, owner, doc_id):
        namespace, _ = scope(owner, doc_id)
        # Explicit IDs, never delete_all or a namespace supplied by a caller.
        self._pinecone("/vectors/delete", {"namespace": namespace,
                       "ids": [f"{doc_id}:{i:04d}" for i in range(200)]})
