from __future__ import annotations

import asyncio
import csv
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from azure.identity.aio import ClientSecretCredential
from kiota_abstractions.api_error import APIError
from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph import GraphServiceClient
from msgraph.generated.groups.groups_request_builder import GroupsRequestBuilder
from msgraph.generated.groups.item.members.members_request_builder import MembersRequestBuilder
from msgraph.generated.users.users_request_builder import UsersRequestBuilder
from msgraph_core.tasks.page_iterator import PageIterator

from app.config import Settings
from app.logging_config import LOGGER_NAME
from app.services.sanitizer import to_csv_value

import logging


USER_COLUMNS = (
    'id',
    'userPrincipalName',
    'displayName',
    'mail',
    'jobTitle',
    'department',
    'accountEnabled',
)
GROUP_COLUMNS = ('id', 'displayName', 'description', 'securityEnabled', 'mailEnabled')
MEMBERSHIP_COLUMNS = ('group_id', 'user_id')


@dataclass(slots=True)
class ExportResult:
    users_count: int
    groups_count: int
    memberships_count: int
    users_file: str
    groups_file: str
    memberships_file: str


class GraphExportService:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._logger = logging.getLogger(LOGGER_NAME)

    async def export(self, run_id: str) -> ExportResult:
        if not self._settings.graph_configured:
            raise RuntimeError('TENANT_ID, CLIENT_ID, and CLIENT_SECRET must all be configured.')

        credential, client = self._create_graph_client(
            tenant_id=self._settings.tenant_id,
            client_id=self._settings.client_id,
            client_secret=self._settings.client_secret,
            graph_scope=self._settings.graph_scope,
        )

        try:
            users = await self._fetch_users(client)
            groups = await self._fetch_groups(client)
            memberships = await self._fetch_memberships(client, groups)
            return self._write_exports(run_id, users, groups, memberships)
        finally:
            await credential.close()

    async def check_connection(self, *, overrides: dict[str, str | None] | None = None) -> tuple[bool, str]:
        config = self._resolve_connection_settings(overrides)
        if not config['tenant_id'] or not config['client_id'] or not config['client_secret']:
            return False, 'Graph credentials are not configured.'

        credential, client = self._create_graph_client(
            tenant_id=config['tenant_id'],
            client_id=config['client_id'],
            client_secret=config['client_secret'],
            graph_scope=config['graph_scope'],
        )

        try:
            request_configuration = RequestConfiguration(
                query_parameters=UsersRequestBuilder.UsersRequestBuilderGetQueryParameters(
                    select=['id'],
                    top=1,
                )
            )
            await self._run_with_retry(
                lambda: client.users.get(request_configuration=request_configuration),
                operation_name='graph connectivity check',
            )
            return True, 'Microsoft Graph connection succeeded.'
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)
        finally:
            await credential.close()

    def _resolve_connection_settings(self, overrides: dict[str, str | None] | None = None) -> dict[str, str | None]:
        overrides = overrides or {}
        return {
            'tenant_id': overrides.get('tenant_id') or self._settings.tenant_id,
            'client_id': overrides.get('client_id') or self._settings.client_id,
            'client_secret': overrides.get('client_secret') or self._settings.client_secret,
            'graph_scope': overrides.get('graph_scope') or self._settings.graph_scope,
        }

    def _create_graph_client(
        self,
        *,
        tenant_id: str | None,
        client_id: str | None,
        client_secret: str | None,
        graph_scope: str | None,
    ) -> tuple[ClientSecretCredential, GraphServiceClient]:
        credential = ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
        )
        client = GraphServiceClient(credentials=credential, scopes=[graph_scope or self._settings.graph_scope])
        return credential, client

    async def _fetch_users(self, client: GraphServiceClient) -> list[dict[str, str]]:
        request_configuration = RequestConfiguration(
            query_parameters=UsersRequestBuilder.UsersRequestBuilderGetQueryParameters(
                select=['id', 'userPrincipalName', 'displayName', 'mail', 'jobTitle', 'department', 'accountEnabled'],
                top=self._settings.graph_page_size,
            )
        )
        response = await self._run_with_retry(
            lambda: client.users.get(request_configuration=request_configuration),
            operation_name='fetch users',
        )

        rows: list[dict[str, str]] = []

        def collect(user: Any) -> bool:
            rows.append(
                {
                    'id': to_csv_value(getattr(user, 'id', None)),
                    'userPrincipalName': to_csv_value(getattr(user, 'user_principal_name', None)),
                    'displayName': to_csv_value(getattr(user, 'display_name', None)),
                    'mail': to_csv_value(getattr(user, 'mail', None)),
                    'jobTitle': to_csv_value(getattr(user, 'job_title', None)),
                    'department': to_csv_value(getattr(user, 'department', None)),
                    'accountEnabled': to_csv_value(getattr(user, 'account_enabled', None)),
                }
            )
            return True

        await self._iterate_collection(response, client, collect, operation_name='fetch users')
        rows.sort(key=lambda row: (row['userPrincipalName'], row['id']))
        return rows

    async def _fetch_groups(self, client: GraphServiceClient) -> list[dict[str, str]]:
        request_configuration = RequestConfiguration(
            query_parameters=GroupsRequestBuilder.GroupsRequestBuilderGetQueryParameters(
                select=['id', 'displayName', 'description', 'securityEnabled', 'mailEnabled'],
                top=self._settings.graph_page_size,
            )
        )
        response = await self._run_with_retry(
            lambda: client.groups.get(request_configuration=request_configuration),
            operation_name='fetch groups',
        )

        rows: list[dict[str, str]] = []

        def collect(group: Any) -> bool:
            rows.append(
                {
                    'id': to_csv_value(getattr(group, 'id', None)),
                    'displayName': to_csv_value(getattr(group, 'display_name', None)),
                    'description': to_csv_value(getattr(group, 'description', None)),
                    'securityEnabled': to_csv_value(getattr(group, 'security_enabled', None)),
                    'mailEnabled': to_csv_value(getattr(group, 'mail_enabled', None)),
                }
            )
            return True

        await self._iterate_collection(response, client, collect, operation_name='fetch groups')
        rows.sort(key=lambda row: (row['displayName'], row['id']))
        return rows

    async def _fetch_memberships(self, client: GraphServiceClient, groups: list[dict[str, str]]) -> list[dict[str, str]]:
        semaphore = asyncio.Semaphore(self._settings.membership_concurrency)
        membership_pairs: set[tuple[str, str]] = set()
        async def collect_group_members(group: dict[str, str]) -> None:
            async with semaphore:
                group_id = group['id']
                request_configuration = RequestConfiguration(
                    query_parameters=MembersRequestBuilder.MembersRequestBuilderGetQueryParameters(
                        select=['id'],
                        top=self._settings.graph_page_size,
                    )
                )
                response = await self._run_with_retry(
                    lambda: client.groups.by_group_id(group_id).members.get(
                        request_configuration=request_configuration,
                    ),
                    operation_name=f'fetch memberships for {group_id}',
                )
                if response is None:
                    return

                def collect(member: Any) -> bool:
                    user_id = to_csv_value(getattr(member, 'id', None))
                    if not user_id:
                        return True
                    # The members endpoint can return users, service principals, and devices.
                    # Keeping only user objects ensures the relationship CSV maps accounts to
                    # OpenText entitlements without polluting the file with unsupported object types.
                    if getattr(member, 'odata_type', '') != '#microsoft.graph.user':
                        return True
                    membership_pairs.add((group_id, user_id))
                    return True

                await self._iterate_collection(
                    response,
                    client,
                    collect,
                    operation_name=f'fetch memberships for {group_id}',
                )
                self._logger.info('Processed group membership page set for %s', group_id)

        await asyncio.gather(*(collect_group_members(group) for group in groups))

        rows = [
            {'group_id': group_id, 'user_id': user_id}
            for group_id, user_id in sorted(membership_pairs, key=lambda item: (item[0], item[1]))
        ]
        return rows

    async def _iterate_collection(
        self,
        response: Any,
        client: GraphServiceClient,
        callback: Callable[[Any], bool],
        *,
        operation_name: str,
    ) -> None:
        if response is None:
            return

        iterator = PageIterator(response, client.request_adapter)
        while True:
            iterator.enumerate(callback)
            if not iterator.current_page or not iterator.current_page.odata_next_link:
                return
            next_page = await self._run_with_retry(iterator.next, operation_name=f'{operation_name} page')
            if not next_page:
                return
            iterator.current_page = next_page
            iterator.pause_index = 0

    async def _run_with_retry(self, operation: Callable[[], Any], *, operation_name: str) -> Any:
        delay_seconds = 1.0
        for attempt in range(1, self._settings.max_retry_attempts + 1):
            try:
                return await operation()
            except APIError as exc:
                status_code = getattr(exc, 'response_status_code', None)
                if status_code != 429 or attempt == self._settings.max_retry_attempts:
                    raise

                retry_after = None
                headers = getattr(exc, 'response_headers', None) or {}
                if 'Retry-After' in headers:
                    retry_after = float(headers['Retry-After'])
                wait_time = retry_after if retry_after is not None else delay_seconds
                self._logger.warning(
                    'Graph throttled during %s. Attempt %s/%s; sleeping %.1fs.',
                    operation_name,
                    attempt,
                    self._settings.max_retry_attempts,
                    wait_time,
                )
                await asyncio.sleep(wait_time)
                delay_seconds = min(delay_seconds * 2, self._settings.max_retry_delay_seconds)

    def _write_exports(
        self,
        run_id: str,
        users: list[dict[str, str]],
        groups: list[dict[str, str]],
        memberships: list[dict[str, str]],
    ) -> ExportResult:
        run_directory = self._settings.export_base_dir / run_id
        latest_directory = self._settings.export_base_dir / 'latest'
        run_directory.mkdir(parents=True, exist_ok=True)
        latest_directory.mkdir(parents=True, exist_ok=True)

        users_file = self._write_csv(run_directory / 'users.csv', USER_COLUMNS, users)
        groups_file = self._write_csv(run_directory / 'groups.csv', GROUP_COLUMNS, groups)
        memberships_file = self._write_csv(run_directory / 'memberships.csv', MEMBERSHIP_COLUMNS, memberships)

        shutil.copyfile(users_file, latest_directory / 'users.csv')
        shutil.copyfile(groups_file, latest_directory / 'groups.csv')
        shutil.copyfile(memberships_file, latest_directory / 'memberships.csv')

        return ExportResult(
            users_count=len(users),
            groups_count=len(groups),
            memberships_count=len(memberships),
            users_file=str(users_file),
            groups_file=str(groups_file),
            memberships_file=str(memberships_file),
        )

    def _write_csv(self, file_path: Path, columns: tuple[str, ...], rows: list[dict[str, str]]) -> Path:
        with file_path.open('w', newline='', encoding='utf-8') as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=list(columns))
            writer.writeheader()
            for row in rows:
                writer.writerow({column: row.get(column, '') for column in columns})
        return file_path
