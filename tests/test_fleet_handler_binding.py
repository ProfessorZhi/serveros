"""Live registration metadata wins over the historical default-handler table."""
from fleet_mcp import cloudcli_native
from test_fleet_mcp_cloudcli_provider import FakeClient, provider, DEVICE


def snapshot(value):
    return cloudcli_native.capability_snapshot(device_ref=DEVICE, observed={
        "run_get": {"_meta": {"cloudcli/handlerBound": value}, "schema": {}}})


def test_live_bound_run_get_is_not_the_historical_default_stub():
    row = snapshot(True)["tools"]["run_get"]
    assert row["state"] == cloudcli_native.USABLE
    assert row["placeholder_default"] is True
    assert row["binding_evidence"] == "LIVE_HANDLER_METADATA"
    assert row["workflow_verified"] is False


def test_live_unbound_handler_is_explicitly_unimplemented():
    result = snapshot(False)
    assert result["tools"]["run_get"]["state"] == cloudcli_native.DECLARED_NOT_IMPLEMENTED
    assert result["declared_not_implemented"] == ["run_get"]


def test_legacy_or_malformed_metadata_does_not_prove_a_handler():
    for value in (None, "true", 1, 0, {}, []):
        result = snapshot(value)
        assert result["tools"]["run_get"]["state"] == "HANDLER_BINDING_UNKNOWN"
        assert result["declared_not_implemented"] == []
        assert result["handler_binding_unknown"] == ["run_get"]


def test_non_placeholder_also_needs_live_registration_evidence():
    result = cloudcli_native.capability_snapshot(device_ref=DEVICE,
                                                observed={"sessions_list": {}})
    assert result["tools"]["sessions_list"]["state"] == "HANDLER_BINDING_UNKNOWN"


def test_cloudcli_provider_preserves_only_the_typed_binding_metadata():
    client = FakeClient()
    client.tools_list = lambda: [{"name": "run_get", "inputSchema": {},
        "_meta": {"cloudcli/handlerBound": True, "unrelated": "not forwarded"}}]
    result = provider(client).capability_snapshot()
    assert result["tools"]["run_get"]["state"] == cloudcli_native.USABLE
    assert "unrelated" not in str(result)
