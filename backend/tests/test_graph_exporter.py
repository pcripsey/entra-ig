from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.config import Settings
from app.services.graph_exporter import GraphExportService


def test_role_to_row_maps_graph_fields() -> None:
    exporter = GraphExportService(Settings())

    role = SimpleNamespace(
        id='role-1',
        role_template_id='template-1',
        display_name='Global Administrator',
        description='Can manage all aspects of Microsoft Entra ID.',
    )

    assert exporter._role_to_row(role) == {
        'id': 'role-1',
        'roleTemplateId': 'template-1',
        'displayName': 'Global Administrator',
        'description': 'Can manage all aspects of Microsoft Entra ID.',
    }


def test_write_exports_writes_role_csvs(tmp_path) -> None:
    settings = Settings(EXPORT_BASE_DIR=tmp_path / 'exports')
    exporter = GraphExportService(settings)

    result = exporter._write_exports(
        run_id='run-1',
        users=[{'id': 'u1', 'userPrincipalName': 'a@example.com'}],
        groups=[{'id': 'g1', 'displayName': 'Group A'}],
        memberships=[{'group_id': 'g1', 'user_id': 'u1'}],
        roles=[{'id': 'r1', 'roleTemplateId': 't1', 'displayName': 'Role A', 'description': 'desc'}],
        role_memberships=[{'role_id': 'r1', 'user_id': 'u1'}],
    )

    run_dir = tmp_path / 'exports' / 'run-1'
    latest_dir = tmp_path / 'exports' / 'latest'

    assert (run_dir / 'roles.csv').exists()
    assert (run_dir / 'role_memberships.csv').exists()
    assert (latest_dir / 'roles.csv').exists()
    assert (latest_dir / 'role_memberships.csv').exists()

    assert result.roles_count == 1
    assert result.role_memberships_count == 1
    assert result.roles_file == str(run_dir / 'roles.csv')
    assert result.role_memberships_file == str(run_dir / 'role_memberships.csv')


def test_fetch_role_memberships_omits_top_parameter() -> None:
    settings = Settings()

    class TestExporter(GraphExportService):
        async def _run_with_retry(self, operation, *, operation_name):
            return await operation()

        async def _iterate_collection(self, response, client, callback, *, operation_name):
            return None

    captured_request_configurations: list[object] = []

    class FakeMembers:
        async def get(self, *, request_configuration):
            captured_request_configurations.append(request_configuration)
            return SimpleNamespace(value=[])

    class FakeDirectoryRole:
        members = FakeMembers()

    class FakeDirectoryRoles:
        def by_directory_role_id(self, role_id):
            return FakeDirectoryRole()

    class FakeClient:
        directory_roles = FakeDirectoryRoles()

    exporter = TestExporter(settings)
    rows = asyncio.run(exporter._fetch_role_memberships(FakeClient(), [{'id': 'role-1'}]))

    assert rows == []
    assert len(captured_request_configurations) == 1
    query_parameters = captured_request_configurations[0].query_parameters
    assert query_parameters.select == ['id']
    assert getattr(query_parameters, 'top', None) is None
