"""Project deletion: a confirmed target, one bounded effect, a verified readback.

The properties under test are the ones a destructive verb has to get right:

*   the target is resolved from the DEVICE's catalogue (archived rows included)
    and the caller has to repeat the row's own path, so an id pasted against the
    wrong project is refused before anything is touched;
*   authorisation is the device's control floor plus the caller's `control`
    grant — never `allow_create`;
*   the device's acknowledgement is not proof: the catalogue is read again and a
    contradiction is reported as unconfirmed rather than as success.
"""
from dataclasses import replace

import pytest

from fleet_mcp.errors import FleetRefusal
from fleet_mcp.fleet import Principal
from fleet_mcp.runtime import owner_principal
from fleet_mcp.scopes import ALLOWED_SCOPES
from test_fleet_create_session import TOKYO
from test_fleet_owner_max import owned


PATH = "/data/example/project-alpha"


def catalogue_service(tmp_path, *, rows=None):
    """The owner harness with a mutable catalogue and a recording deleter."""
    h = owned(tmp_path)
    state = {
        "rows": [dict(row) for row in (rows if rows is not None else [
            {"projectId": "p-1", "path": PATH, "isArchived": False},
        ])],
        "deletes": [],
        "list_calls": [],
    }
    provider = h.providers[TOKYO]

    def native_call(tool, arguments, **kwargs):
        assert tool == "projects_list", tool
        state["list_calls"].append(dict(arguments))
        return {"projects": [dict(row) for row in state["rows"]]}

    def delete_project(*, project_id, force):
        state["deletes"].append((project_id, force))
        row = next(row for row in state["rows"] if row["projectId"] == project_id)
        was_archived = bool(row.get("isArchived"))
        if force:
            state["rows"].remove(row)
        else:
            row["isArchived"] = True
        return {"projectId": project_id, "path": row["path"],
                "wasArchived": was_archived, "archived": not force,
                "deleted": bool(force), "directoryRemoved": False}

    provider.native_call = native_call
    provider.delete_project = delete_project
    return h, state


def delete(h, *, project_id="p-1", confirm_path=PATH, force=False, principal=None):
    return h.service.delete_project(
        principal if principal is not None else owner_principal(h.config),
        device=TOKYO, project_id=project_id, confirm_path=confirm_path, force=force)


def test_the_default_archives_and_the_readback_agrees(tmp_path):
    h, state = catalogue_service(tmp_path)
    result = delete(h)
    assert result["project_id"] == "p-1"
    assert result["project_path"] == PATH
    assert result["was_archived"] is False
    assert result["archived"] is True and result["deleted"] is False
    assert result["directory_removed"] is False
    assert result["verified"] is True
    assert result["verification"] == "catalogued as archived"
    assert state["deletes"] == [("p-1", False)]
    # Both reads include archived rows: a delete's pre-read must see the row a
    # permanent delete most often targets, and its post-read must still see the
    # row an archive leaves behind.
    assert state["list_calls"] == [{"includeArchived": True},
                                   {"includeArchived": True}]


def test_force_removes_the_row_and_says_no_directory_was_removed(tmp_path):
    h, state = catalogue_service(tmp_path)
    result = delete(h, force=True)
    assert result["deleted"] is True and result["archived"] is False
    assert result["directory_removed"] is False
    assert result["verification"] == "absent from the catalogue, archived included"
    assert state["deletes"] == [("p-1", True)]
    assert state["rows"] == []


def test_an_already_archived_project_reports_that_it_was(tmp_path):
    h, _ = catalogue_service(tmp_path, rows=[
        {"projectId": "p-1", "path": PATH, "isArchived": True}])
    result = delete(h)
    assert result["was_archived"] is True and result["archived"] is True


def test_an_unknown_id_is_project_not_found_and_nothing_is_sent(tmp_path):
    h, state = catalogue_service(tmp_path)
    with pytest.raises(FleetRefusal) as caught:
        delete(h, project_id="no-such-project")
    assert caught.value.code == "PROJECT_NOT_FOUND"
    assert state["deletes"] == []


def test_a_wrong_confirm_path_refuses_before_the_device_is_asked(tmp_path):
    h, state = catalogue_service(tmp_path)
    with pytest.raises(FleetRefusal) as caught:
        delete(h, confirm_path="/data/example/some-other-project")
    assert caught.value.code == "BAD_REQUEST"
    # The refusal names the read that answers the question and does NOT echo the
    # real path: a caller may hold control without read.
    assert "projects_list" in caught.value.detail
    assert PATH not in caught.value.detail
    assert state["deletes"] == []


def test_control_scope_is_required_before_the_device_is_read(tmp_path):
    h, state = catalogue_service(tmp_path)
    read_only = Principal(owner_principal(h.config).name, frozenset({"fleet:read"}), "pat")
    with pytest.raises(FleetRefusal) as caught:
        delete(h, principal=read_only)
    assert caught.value.code == "SCOPE_DENIED"
    assert state["list_calls"] == [] and state["deletes"] == []


def test_a_device_without_control_and_no_owner_profile_refuses(tmp_path):
    h, state = catalogue_service(tmp_path)
    binding = replace(h.config.providers[TOKYO], allow_control=False)
    h.service.config = replace(h.config, providers={TOKYO: binding}, policy=replace(
        h.config.policy, owner_max={}, operator_grants={}))
    with pytest.raises(FleetRefusal) as caught:
        delete(h, principal=Principal("oauth:any-client",
                                      frozenset(ALLOWED_SCOPES), "oauth"))
    assert caught.value.code == "APPROVAL_REQUIRED"
    assert state["deletes"] == []


def test_a_control_scope_without_the_grant_is_write_not_granted(tmp_path):
    h, state = catalogue_service(tmp_path)
    binding = replace(h.config.providers[TOKYO], allow_control=True)
    h.service.config = replace(h.config, providers={TOKYO: binding}, policy=replace(
        h.config.policy, owner_max={}, operator_grants={"*": {TOKYO: ["send"]}}))
    with pytest.raises(FleetRefusal) as caught:
        delete(h, principal=Principal("oauth:any-client",
                                      frozenset(ALLOWED_SCOPES), "oauth"))
    assert caught.value.code == "WRITE_NOT_GRANTED"
    assert caught.value.extra["required_verb"] == "control"
    assert state["deletes"] == []


def test_a_control_grant_on_a_control_enabled_device_admits(tmp_path):
    h, state = catalogue_service(tmp_path)
    binding = replace(h.config.providers[TOKYO], allow_control=True)
    h.service.config = replace(h.config, providers={TOKYO: binding}, policy=replace(
        h.config.policy, owner_max={}, operator_grants={"*": {TOKYO: ["control"]}}))
    result = delete(h, principal=Principal("oauth:any-client",
                                           frozenset(ALLOWED_SCOPES), "oauth"))
    assert result["archived"] is True
    assert state["deletes"] == [("p-1", False)]


def test_creation_grant_is_not_deletion_authority(tmp_path):
    """`allow_create` and the `create` verb do not authorize removal."""
    h, state = catalogue_service(tmp_path)
    binding = replace(h.config.providers[TOKYO], allow_control=False, allow_create=True)
    h.service.config = replace(h.config, providers={TOKYO: binding}, policy=replace(
        h.config.policy, owner_max={}, operator_grants={"*": {TOKYO: ["create"]}}))
    with pytest.raises(FleetRefusal) as caught:
        delete(h, principal=Principal("oauth:any-client",
                                      frozenset(ALLOWED_SCOPES), "oauth"))
    assert caught.value.code in ("APPROVAL_REQUIRED", "WRITE_NOT_GRANTED")
    assert state["deletes"] == []


def test_a_device_that_contradicts_its_own_ack_is_unconfirmed(tmp_path):
    h, state = catalogue_service(tmp_path)
    provider = h.providers[TOKYO]

    def lying_delete(*, project_id, force):
        # Answers "deleted" and leaves the row exactly where it was.
        return {"projectId": project_id, "path": PATH, "wasArchived": False,
                "archived": False, "deleted": True, "directoryRemoved": False}

    provider.delete_project = lying_delete
    with pytest.raises(FleetRefusal) as caught:
        delete(h, force=True)
    assert caught.value.code == "INTERNAL"
    assert caught.value.extra.get("retry_safe") is False
    assert state["rows"] != []


def test_a_malformed_ack_is_not_read_as_success(tmp_path):
    h, _ = catalogue_service(tmp_path)
    h.providers[TOKYO].delete_project = lambda *, project_id, force: {
        "projectId": project_id, "path": PATH, "wasArchived": False,
        "archived": True, "deleted": True, "directoryRemoved": False}
    with pytest.raises(FleetRefusal) as caught:
        delete(h)
    assert caught.value.code == "INTERNAL"
    assert caught.value.extra.get("retry_safe") is False
