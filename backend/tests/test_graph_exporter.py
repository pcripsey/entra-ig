from __future__ import annotations

import asyncio
import csv
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
        users=[
            {
                'id': 'u1',
                'userPrincipalName': 'a@example.com',
                'displayName': 'User A',
                'mail': 'a@example.com',
                'jobTitle': 'Engineer',
                'department': 'IT',
                'accountEnabled': 'true',
                'givenName': 'Alice',
                'surname': 'Anderson',
                'mailNickname': 'alice',
                'employeeId': 'E123',
                'employeeType': 'Employee',
                'companyName': 'Acme',
                'streetAddress': '123 Main',
                'officeLocation': 'HQ',
                'businessPhone': '111',
                'mobilePhone': '222',
                'preferredLanguage': 'en-US',
                'country': 'US',
                'city': 'NYC',
                'state': 'NY',
                'onPremisesDistinguishedName': 'CN=Alice',
                'onPremisesImmutableId': 'GUID-1',
                'userType': 'Member',
                'otherMails': 'alias@example.com',
                'sapId': 'SAP-1',
                'managerId': 'mgr-1',
            }
        ],
        groups=[
            {
                'id': 'g1',
                'displayName': 'Group A',
                'description': 'Group description',
                'securityEnabled': 'true',
                'mailEnabled': 'false',
                'mailNickname': 'groupa',
                'onPremisesObjectIdentifier': 'OBJ-1',
                'onPremisesDistinguishedName': 'CN=GroupA',
            }
        ],
        memberships=[{'group_id': 'g1', 'user_id': 'u1'}],
        roles=[{'id': 'r1', 'roleTemplateId': 't1', 'displayName': 'Role A', 'description': 'desc'}],
        role_memberships=[{'role_id': 'r1', 'user_id': 'u1'}],
        group_owners={'g1': ['u1']},
        nested_groups=[{'parentId': 'g1', 'childId': 'g2'}],
    )

    run_dir = tmp_path / 'exports' / 'run-1'
    latest_dir = tmp_path / 'exports' / 'latest'

    assert (run_dir / 'Identity.csv').exists()
    assert (run_dir / 'ig_account_import.csv').exists()
    assert (run_dir / 'ig_group_import.csv').exists()
    assert (run_dir / 'ig_group_to_user_membership.csv').exists()
    assert (run_dir / 'ig_parent_group_to_child_group.csv').exists()
    assert (run_dir / 'ig_permission_import.csv').exists()
    assert (run_dir / 'ig_holder_to_permissions_mapping.csv').exists()
    assert (run_dir / 'ig_permission_to_holders_mapping.csv').exists()
    assert (run_dir / 'ig_permission_hierarchy_child_parent.csv').exists()
    assert (run_dir / 'ig_permission_hierarchy_parent_child.csv').exists()
    assert (run_dir / 'roles.csv').exists()
    assert (run_dir / 'role_memberships.csv').exists()
    assert (latest_dir / 'roles.csv').exists()
    assert (latest_dir / 'role_memberships.csv').exists()
    assert (latest_dir / 'Identity.csv').exists()

    assert result.roles_count == 1
    assert result.role_memberships_count == 1
    assert result.nested_groups_count == 1
    assert result.identity_file == str(run_dir / 'Identity.csv')
    assert result.account_file == str(run_dir / 'ig_account_import.csv')
    assert result.roles_file == str(run_dir / 'roles.csv')
    assert result.role_memberships_file == str(run_dir / 'role_memberships.csv')

    with (run_dir / 'ig_permission_hierarchy_child_parent.csv').open(
        newline='', encoding='utf-8'
    ) as csv_file:
        assert list(csv.reader(csv_file)) == [['permissionId', 'parentPermissionId', 'assignmentType']]

    with (run_dir / 'ig_permission_hierarchy_parent_child.csv').open(
        newline='', encoding='utf-8'
    ) as csv_file:
        assert list(csv.reader(csv_file)) == [['permissionId', 'childPermissionId', 'assignmentType']]


def test_user_to_row_maps_expanded_user_fields() -> None:
    exporter = GraphExportService(Settings())
    user = SimpleNamespace(
        id='u1',
        user_principal_name='a@example.com',
        display_name='User A',
        mail='a@example.com',
        job_title='Engineer',
        department='IT',
        account_enabled=True,
        given_name='Alice',
        surname='Anderson',
        mail_nickname='alice',
        employee_id='E123',
        employee_type='Employee',
        company_name='Acme',
        street_address='123 Main',
        office_location='HQ',
        business_phones=['111', '222'],
        mobile_phone='333',
        preferred_language='en-US',
        country='US',
        city='NYC',
        state='NY',
        on_premises_distinguished_name='CN=Alice',
        on_premises_immutable_id='GUID-1',
        user_type='Member',
        other_mails=['alias1@example.com', 'alias2@example.com'],
        on_premises_extension_attributes=SimpleNamespace(extension_attribute1='SAP-1'),
        manager=SimpleNamespace(id='mgr-1'),
    )

    assert exporter._user_to_row(user) == {
        'id': 'u1',
        'userPrincipalName': 'a@example.com',
        'displayName': 'User A',
        'mail': 'a@example.com',
        'jobTitle': 'Engineer',
        'department': 'IT',
        'accountEnabled': 'true',
        'givenName': 'Alice',
        'surname': 'Anderson',
        'mailNickname': 'alice',
        'employeeId': 'E123',
        'employeeType': 'Employee',
        'companyName': 'Acme',
        'streetAddress': '123 Main',
        'officeLocation': 'HQ',
        'businessPhone': '111',
        'mobilePhone': '333',
        'preferredLanguage': 'en-US',
        'country': 'US',
        'city': 'NYC',
        'state': 'NY',
        'onPremisesDistinguishedName': 'CN=Alice',
        'onPremisesImmutableId': 'GUID-1',
        'userType': 'Member',
        'otherMails': 'alias1@example.com;alias2@example.com',
        'sapId': 'SAP-1',
        'managerId': 'mgr-1',
    }


def test_group_to_row_maps_expanded_group_fields() -> None:
    exporter = GraphExportService(Settings())
    group = SimpleNamespace(
        id='g1',
        display_name='Group A',
        description='desc',
        security_enabled=True,
        mail_enabled=False,
        mail_nickname='groupa',
        on_premises_object_identifier='OBJ-1',
        on_premises_distinguished_name='CN=GroupA',
    )

    assert exporter._group_to_row(group) == {
        'id': 'g1',
        'displayName': 'Group A',
        'description': 'desc',
        'securityEnabled': 'true',
        'mailEnabled': 'false',
        'mailNickname': 'groupa',
        'onPremisesObjectIdentifier': 'OBJ-1',
        'onPremisesDistinguishedName': 'CN=GroupA',
    }


def test_fetch_role_memberships_omits_top_parameter() -> None:
    settings = Settings()

    class TestExporter(GraphExportService):
        async def _run_with_retry(self, operation, *, operation_name, progress=None):
            return await operation()

        async def _iterate_collection(self, response, client, callback, *, operation_name, progress=None):
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


def test_fetch_users_uses_delta_endpoint() -> None:
    """_fetch_users must call client.users.delta.get() to obtain an odata_delta_link."""
    settings = Settings()

    class TestExporter(GraphExportService):
        async def _run_with_retry(self, operation, *, operation_name, progress=None):
            return await operation()

        async def _iterate_collection(self, response, client, callback, *, operation_name, progress=None):
            return 'https://graph.microsoft.com/v1.0/users/delta?$deltatoken=test-token'

    delta_called = []

    class FakeDelta:
        async def get(self, *, request_configuration):
            delta_called.append(request_configuration)
            return SimpleNamespace(value=[])

    class FakeUsers:
        delta = FakeDelta()

        async def get(self, *, request_configuration):
            raise AssertionError('Must use users.delta.get(), not users.get()')

    class FakeClient:
        users = FakeUsers()
        request_adapter = None

    exporter = TestExporter(settings)
    rows, delta_link = asyncio.run(exporter._fetch_users(FakeClient()))

    assert rows == []
    assert len(delta_called) == 1
    assert delta_link == 'https://graph.microsoft.com/v1.0/users/delta?$deltatoken=test-token'


def test_fetch_groups_uses_delta_endpoint() -> None:
    """_fetch_groups must call client.groups.delta.get() to obtain an odata_delta_link."""
    settings = Settings()

    class TestExporter(GraphExportService):
        async def _run_with_retry(self, operation, *, operation_name, progress=None):
            return await operation()

        async def _iterate_collection(self, response, client, callback, *, operation_name, progress=None):
            return 'https://graph.microsoft.com/v1.0/groups/delta?$deltatoken=test-token'

    delta_called = []

    class FakeDelta:
        async def get(self, *, request_configuration):
            delta_called.append(request_configuration)
            return SimpleNamespace(value=[])

    class FakeGroups:
        delta = FakeDelta()

        async def get(self, *, request_configuration):
            raise AssertionError('Must use groups.delta.get(), not groups.get()')

    class FakeClient:
        groups = FakeGroups()
        request_adapter = None

    exporter = TestExporter(settings)
    rows, delta_link = asyncio.run(exporter._fetch_groups(FakeClient()))

    assert rows == []
    assert len(delta_called) == 1
    assert delta_link == 'https://graph.microsoft.com/v1.0/groups/delta?$deltatoken=test-token'
