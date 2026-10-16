"""Credential-free startup diagnostics, independent of cached authentication imports."""
import logging


def startup_diagnostic(stage, reason, *, field=None, http_status=None):
    """Log only allowlisted labels, never exception text, values, URLs or headers."""
    stages = ("secrets", "supabase", "pinecone_index", "oauth", "service")
    reasons = ("missing_variable", "boolean_true_required", "nonempty_string_required",
               "cloud_name_format", "index_name_format", "index_host_format", "publishable_key_required",
               "secrets_parse_or_shape_error", "project_url_format", "write_key_format", "app_url_format",
               "describe_http_error", "describe_rate_limited", "describe_timeout", "describe_network_error",
               "describe_invalid_json", "describe_failed", "describe_shape_invalid", "dimension_mismatch",
               "metric_mismatch", "host_mismatch", "serverless_shape_invalid", "serverless_cloud_mismatch",
               "serverless_region_mismatch", "index_name_mismatch", "index_not_ready", "unexpected_exception")
    fields = ("FREE_PLANS_CONFIRMED", "SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "APP_URL",
              "CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET", "PINECONE_API_KEY",
              "PINECONE_INDEX", "PINECONE_HOST", "GROQ_API_KEY", "CLOUD_WRITE_KEY")
    safe_stage = stage if isinstance(stage, str) and stage in stages else "startup"
    safe_reason = reason if isinstance(reason, str) and reason in reasons else "unexpected_exception"
    safe_field = " field=" + field if isinstance(field, str) and field in fields else ""
    safe_status = " http_status=" + str(http_status) if type(http_status) is int and 100 <= http_status <= 599 else ""
    logging.getLogger("documind.cloud.startup").warning(
        "documind_startup stage=%s reason=%s%s%s", safe_stage, safe_reason, safe_field, safe_status)


def configuration_error(stage, reason, *, field=None):
    # Resolve the stable error type only when needed, avoiding an auth import cycle.
    from authenticated_cloud.auth import CloudError

    startup_diagnostic(stage, reason, field=field)
    raise CloudError("configuration") from None
