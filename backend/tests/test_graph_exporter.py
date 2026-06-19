from __future__ import annotations

import asyncio
import csv
import logging
from types import SimpleNamespace

from app.config import Settings
from app.services import graph_exporter
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
    )

    assert exporter._group_to_row(group) == {
        'id': 'g1',
        'displayName': 'Group A',
        'description': 'desc',
        'securityEnabled': 'true',
        'mailEnabled': 'false',
        'mailNickname': 'groupa',
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


def test_iterate_collection_reads_delta_link_from_additional_data(monkeypatch) -> None:
    class FakePageIterator:
        def __init__(self, response, request_adapter):
            self.current_page = response
            self.pause_index = 0

        def enumerate(self, callback):
            for item in self.current_page.value:
                callback(item)

    monkeypatch.setattr(graph_exporter, 'PageIterator', FakePageIterator)

    exporter = GraphExportService(Settings())
    page = SimpleNamespace(
        value=[],
        odata_next_link=None,
        odata_delta_link=None,
        additional_data={'@odata.deltaLink': 'https://graph.microsoft.com/v1.0/users/delta?$deltatoken=abc123'},
    )

    delta_link = asyncio.run(
        exporter._iterate_collection(
            page,
            SimpleNamespace(request_adapter=None),
            lambda _: True,
            operation_name='test iterate collection',
        )
    )

    assert delta_link == 'https://graph.microsoft.com/v1.0/users/delta?$deltatoken=abc123'


def test_iterate_collection_reads_delta_link_from_last_page_of_multipage_response(monkeypatch) -> None:
    """Delta link must be read from the final raw page, not iterator.current_page (a PageResult)."""
    page2 = SimpleNamespace(
        value=['item2'],
        odata_next_link=None,
        odata_delta_link='https://graph.microsoft.com/v1.0/users/delta?$deltatoken=final',
        additional_data={},
    )

    class FakePageIterator:
        def __init__(self, response, request_adapter):
            self.current_page = response
            self.pause_index = 0

        def enumerate(self, callback):
            for item in self.current_page.value:
                callback(item)

        async def fetch_next_page(self):
            return page2

    monkeypatch.setattr(graph_exporter, 'PageIterator', FakePageIterator)

    exporter = GraphExportService(Settings())
    page1 = SimpleNamespace(
        value=['item1'],
        odata_next_link='https://graph.microsoft.com/v1.0/users/delta?$skiptoken=page2',
        odata_delta_link=None,
        additional_data={},
    )

    collected: list[str] = []

    delta_link = asyncio.run(
        exporter._iterate_collection(
            page1,
            SimpleNamespace(request_adapter=None),
            lambda item: collected.append(item) or True,
            operation_name='test iterate collection multipage',
        )
    )

    assert delta_link == 'https://graph.microsoft.com/v1.0/users/delta?$deltatoken=final'
    assert collected == ['item1', 'item2']


def test_iterate_collection_prefers_direct_delta_link(monkeypatch) -> None:
    class FakePageIterator:
        def __init__(self, response, request_adapter):
            self.current_page = response
            self.pause_index = 0

        def enumerate(self, callback):
            for item in self.current_page.value:
                callback(item)

    monkeypatch.setattr(graph_exporter, 'PageIterator', FakePageIterator)

    exporter = GraphExportService(Settings())
    page = SimpleNamespace(
        value=[],
        odata_next_link=None,
        odata_delta_link='https://graph.microsoft.com/v1.0/users/delta?$deltatoken=direct',
        additional_data={'@odata.deltaLink': 'https://graph.microsoft.com/v1.0/users/delta?$deltatoken=ignored'},
    )

    delta_link = asyncio.run(
        exporter._iterate_collection(
            page,
            SimpleNamespace(request_adapter=None),
            lambda _: True,
            operation_name='test iterate collection',
        )
    )

    assert delta_link == 'https://graph.microsoft.com/v1.0/users/delta?$deltatoken=direct'


def test_iterate_collection_returns_delta_link_from_additional_data_on_last_page_of_multipage_response(monkeypatch) -> None:
    """Regression test: delta link in additional_data on the last raw page must not be lost.

    Simulates the SDK stripping odata_delta_link from the fetch_next_page() result while
    still placing it in additional_data, which is the real-world failure mode for large
    collections (e.g. 70k+ users spanning many pages).
    """
    # The last page has odata_delta_link=None (stripped by SDK) but it's in additional_data.
    page2 = SimpleNamespace(
        value=['item2'],
        odata_next_link=None,
        odata_delta_link=None,
        additional_data={'@odata.deltaLink': 'https://graph.microsoft.com/v1.0/users/delta?$deltatoken=fromadditional'},
    )

    class FakePageIterator:
        def __init__(self, response, request_adapter):
            self.current_page = response
            self.pause_index = 0

        def enumerate(self, callback):
            for item in self.current_page.value:
                callback(item)

        async def fetch_next_page(self):
            return page2

    monkeypatch.setattr(graph_exporter, 'PageIterator', FakePageIterator)

    exporter = GraphExportService(Settings())
    page1 = SimpleNamespace(
        value=['item1'],
        odata_next_link='https://graph.microsoft.com/v1.0/users/delta?$skiptoken=page2',
        odata_delta_link=None,
        additional_data={},
    )

    collected: list[str] = []

    delta_link = asyncio.run(
        exporter._iterate_collection(
            page1,
            SimpleNamespace(request_adapter=None),
            lambda item: collected.append(item) or True,
            operation_name='test iterate collection multipage additional_data',
        )
    )

    assert delta_link == 'https://graph.microsoft.com/v1.0/users/delta?$deltatoken=fromadditional'
    assert collected == ['item1', 'item2']


class FakeAPIError(graph_exporter.APIError):
    def __init__(self, status_code: int):
        super().__init__('test error')
        self.response_status_code = status_code


class PassthroughExporter(GraphExportService):
    async def _run_with_retry(self, operation, *, operation_name, progress=None):
        return await operation()

    async def _iterate_collection(self, response, client, callback, *, operation_name, progress=None):
        for item in getattr(response, 'value', []):
            callback(item)
        return None


def test_fetch_memberships_ignores_404_and_logs_other_exceptions(caplog) -> None:
    class FakeMembers:
        def __init__(self, group_id: str):
            self.group_id = group_id

        async def get(self, *, request_configuration):
            if self.group_id == 'missing':
                raise FakeAPIError(404)
            if self.group_id == 'boom':
                raise RuntimeError('membership boom')
            return SimpleNamespace(value=[SimpleNamespace(id='user-1', odata_type='#microsoft.graph.user')])

    class FakeGroup:
        def __init__(self, group_id: str):
            self.members = FakeMembers(group_id)

    class FakeGroups:
        def by_group_id(self, group_id: str):
            return FakeGroup(group_id)

    class FakeClient:
        groups = FakeGroups()

    exporter = PassthroughExporter(Settings())
    with caplog.at_level(logging.WARNING, logger=graph_exporter.LOGGER_NAME):
        rows = asyncio.run(
            exporter._fetch_memberships(
                FakeClient(),
                [{'id': 'ok'}, {'id': 'missing'}, {'id': 'boom'}],
            )
        )

    assert rows == [{'group_id': 'ok', 'user_id': 'user-1'}]
    assert any('skipping memberships fetch' in message for message in caplog.messages)
    assert any('Unexpected exception while fetching memberships' in message for message in caplog.messages)


def test_fetch_group_owners_ignores_404_and_logs_other_exceptions(caplog) -> None:
    class FakeOwners:
        def __init__(self, group_id: str):
            self.group_id = group_id

        async def get(self):
            if self.group_id == 'missing':
                raise FakeAPIError(404)
            if self.group_id == 'boom':
                raise RuntimeError('owner boom')
            return SimpleNamespace(value=[SimpleNamespace(id='owner-1', odata_type='#microsoft.graph.user')])

    class FakeGroup:
        def __init__(self, group_id: str):
            self.owners = FakeOwners(group_id)

    class FakeGroups:
        def by_group_id(self, group_id: str):
            return FakeGroup(group_id)

    class FakeClient:
        groups = FakeGroups()

    exporter = PassthroughExporter(Settings())
    with caplog.at_level(logging.WARNING, logger=graph_exporter.LOGGER_NAME):
        owners = asyncio.run(
            exporter._fetch_group_owners(
                FakeClient(),
                [{'id': 'ok'}, {'id': 'missing'}, {'id': 'boom'}],
            )
        )

    assert owners == {'ok': ['owner-1']}
    assert any('skipping owners fetch' in message for message in caplog.messages)
    assert any('Unexpected exception while fetching group owners' in message for message in caplog.messages)


def test_fetch_nested_groups_ignores_404_and_logs_other_exceptions(caplog) -> None:
    class FakeMembers:
        def __init__(self, group_id: str):
            self.group_id = group_id

        async def get(self, *, request_configuration):
            if self.group_id == 'missing':
                raise FakeAPIError(404)
            if self.group_id == 'boom':
                raise RuntimeError('nested boom')
            return SimpleNamespace(value=[SimpleNamespace(id='child-1', odata_type='#microsoft.graph.group')])

    class FakeGroup:
        def __init__(self, group_id: str):
            self.members = FakeMembers(group_id)

    class FakeGroups:
        def by_group_id(self, group_id: str):
            return FakeGroup(group_id)

    class FakeClient:
        groups = FakeGroups()

    exporter = PassthroughExporter(Settings())
    with caplog.at_level(logging.WARNING, logger=graph_exporter.LOGGER_NAME):
        nested = asyncio.run(
            exporter._fetch_nested_groups(
                FakeClient(),
                [{'id': 'ok'}, {'id': 'missing'}, {'id': 'boom'}],
            )
        )

    assert nested == [{'parentId': 'ok', 'childId': 'child-1'}]
    assert any('skipping nested groups fetch' in message for message in caplog.messages)
    assert any('Unexpected exception while fetching nested groups' in message for message in caplog.messages)


def test_fetch_role_memberships_ignores_404_and_logs_other_exceptions(caplog) -> None:
    class FakeMembers:
        def __init__(self, role_id: str):
            self.role_id = role_id

        async def get(self, *, request_configuration):
            if self.role_id == 'missing':
                raise FakeAPIError(404)
            if self.role_id == 'boom':
                raise RuntimeError('role membership boom')
            return SimpleNamespace(value=[SimpleNamespace(id='user-1', odata_type='#microsoft.graph.user')])

    class FakeRole:
        def __init__(self, role_id: str):
            self.members = FakeMembers(role_id)

    class FakeDirectoryRoles:
        def by_directory_role_id(self, role_id: str):
            return FakeRole(role_id)

    class FakeClient:
        directory_roles = FakeDirectoryRoles()

    exporter = PassthroughExporter(Settings())
    with caplog.at_level(logging.WARNING, logger=graph_exporter.LOGGER_NAME):
        rows = asyncio.run(
            exporter._fetch_role_memberships(
                FakeClient(),
                [{'id': 'ok'}, {'id': 'missing'}, {'id': 'boom'}],
            )
        )

    assert rows == [{'role_id': 'ok', 'user_id': 'user-1'}]
    assert any('skipping role memberships fetch' in message for message in caplog.messages)
    assert any('Unexpected exception while fetching role memberships' in message for message in caplog.messages)
