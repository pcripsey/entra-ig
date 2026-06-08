from __future__ import annotations

import asyncio
import csv
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from azure.identity.aio import ClientSecretCredential
from kiota_abstractions.api_error import APIError
from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph import GraphServiceClient
from msgraph.generated.directory_roles.directory_roles_request_builder import DirectoryRolesRequestBuilder
from msgraph.generated.directory_roles.item.members.members_request_builder import (
    MembersRequestBuilder as RoleMembersRequestBuilder,
)
from msgraph.generated.groups.groups_request_builder import GroupsRequestBuilder
from msgraph.generated.groups.delta.delta_request_builder import DeltaRequestBuilder as GroupsDeltaRequestBuilder
from msgraph.generated.groups.item.members.members_request_builder import MembersRequestBuilder
from msgraph.generated.users.users_request_builder import UsersRequestBuilder
from msgraph.generated.users.delta.delta_request_builder import DeltaRequestBuilder as UsersDeltaRequestBuilder
from msgraph_core.tasks.page_iterator import PageIterator

from app.config import Settings
from app.logging_config import LOGGER_NAME
from app.services.sanitizer import to_csv_value

if TYPE_CHECKING:
    from app.database import RunStore

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
ROLE_COLUMNS = ('id', 'roleTemplateId', 'displayName', 'description')
ROLE_MEMBERSHIP_COLUMNS = ('role_id', 'user_id')


@dataclass(slots=True)
class ExportResult:
    users_count: int
    groups_count: int
    memberships_count: int
    roles_count: int
    role_memberships_count: int
    users_file: str
    groups_file: str
    memberships_file: str
    roles_file: str
    role_memberships_file: str


@dataclass
class LiveProgress:
    stage: str = 'Initializing'
    users_fetched: int = 0
    groups_fetched: int = 0
    memberships_fetched: int = 0
    roles_fetched: int = 0
    role_memberships_fetched: int = 0
    # Throttle tracking
    throttle_count: int = 0
    last_throttled_at: str | None = None
    last_throttled_operation: str | None = None


class GraphExportService:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._logger = logging.getLogger(LOGGER_NAME)
        self._max_retry_attempts: int = settings.max_retry_attempts
        self._max_retry_delay_seconds: int = settings.max_retry_delay_seconds

    def update_retry_config(self, *, max_retry_attempts: int, max_retry_delay_seconds: int) -> None:
        self._max_retry_attempts = max_retry_attempts
        self._max_retry_delay_seconds = max_retry_delay_seconds

    async def export(self, run_id: str, sync_type: str = 'full', run_store: RunStore | None = None, progress: LiveProgress | None = None) -> ExportResult:
        if not self._settings.graph_configured:
            raise RuntimeError('TENANT_ID, CLIENT_ID, and CLIENT_SECRET must all be configured.')

        if sync_type == 'incremental' and run_store is not None:
            return await self._export_incremental(run_id, run_store, progress)
        return await self._export_full(run_id, run_store, progress)

    async def _export_full(self, run_id: str, run_store: RunStore | None, progress: LiveProgress | None = None) -> ExportResult:
        credential, client = self._create_graph_client(
            tenant_id=self._settings.tenant_id,
            client_id=self._settings.client_id,
            client_secret=self._settings.client_secret,
            graph_scope=self._settings.graph_scope,
        )
        try:
            if progress is not None:
                progress.stage = 'Fetching users'
            users, users_delta_link = await self._fetch_users(client, progress)
            if progress is not None:
                progress.stage = 'Fetching groups'
            groups, groups_delta_link = await self._fetch_groups(client, progress)
            if progress is not None:
                progress.stage = 'Fetching memberships'
            memberships = await self._fetch_memberships(client, groups, progress)
            if progress is not None:
                progress.stage = 'Fetching roles'
            roles = await self._fetch_roles(client, progress)
            if progress is not None:
                progress.stage = 'Fetching role memberships'
            role_memberships = await self._fetch_role_memberships(client, roles, progress)
            if progress is not None:
                progress.stage = 'Writing exports'
            result = self._write_exports(run_id, users, groups, memberships, roles, role_memberships)

            # Store delta tokens so future incremental syncs have a baseline.
            if run_store is not None:
                tokens: dict[str, str] = {}
                if users_delta_link:
                    tokens['users'] = users_delta_link
                if groups_delta_link:
                    tokens['groups'] = groups_delta_link
                if tokens:
                    await run_store.update_delta_tokens(tokens)

            return result
        finally:
            await credential.close()

    async def _export_incremental(self, run_id: str, run_store: RunStore, progress: LiveProgress | None = None) -> ExportResult:
        """Incremental export using Microsoft Graph delta queries.

        Fetches only changed users and groups since the last sync, merges those
        changes into the latest exported CSVs, then re-fetches all memberships
        for the current group set to ensure accuracy.  Falls back to a full
        export when no stored delta tokens are found or the tokens have expired.
        """
        delta_tokens = await run_store.get_delta_tokens()
        users_token = delta_tokens.get('users')
        groups_token = delta_tokens.get('groups')

        latest_dir = self._settings.export_base_dir / 'latest'
        users_csv = latest_dir / 'users.csv'
        groups_csv = latest_dir / 'groups.csv'

        if not users_token or not groups_token or not users_csv.exists() or not groups_csv.exists():
            self._logger.info(
                'No delta tokens or baseline exports found; falling back to full sync for run %s.', run_id
            )
            return await self._export_full(run_id, run_store, progress)

        credential, client = self._create_graph_client(
            tenant_id=self._settings.tenant_id,
            client_id=self._settings.client_id,
            client_secret=self._settings.client_secret,
            graph_scope=self._settings.graph_scope,
        )
        try:
            try:
                if progress is not None:
                    progress.stage = 'Fetching users'
                modified_users, deleted_user_ids, new_users_token = await self._fetch_users_delta(
                    client, users_token, progress
                )
                if progress is not None:
                    progress.stage = 'Fetching groups'
                modified_groups, deleted_group_ids, new_groups_token = await self._fetch_groups_delta(
                    client, groups_token, progress
                )
            except APIError as exc:
                status_code = getattr(exc, 'response_status_code', None)
                if status_code == 410:
                    # Delta token expired — start fresh.
                    self._logger.warning(
                        'Delta token expired (HTTP 410); falling back to full sync for run %s.', run_id
                    )
                    return await self._export_full(run_id, run_store, progress)
                raise

            # Load existing baseline data.
            existing_users = self._read_csv(users_csv, 'id')
            existing_groups = self._read_csv(groups_csv, 'id')

            # Apply user deltas.
            for user in modified_users:
                existing_users[user['id']] = user
            for uid in deleted_user_ids:
                existing_users.pop(uid, None)

            # Apply group deltas.
            for group in modified_groups:
                existing_groups[group['id']] = group
            for gid in deleted_group_ids:
                existing_groups.pop(gid, None)

            users = sorted(existing_users.values(), key=lambda r: (r.get('userPrincipalName', ''), r.get('id', '')))
            groups = sorted(existing_groups.values(), key=lambda r: (r.get('displayName', ''), r.get('id', '')))

            if progress is not None:
                progress.stage = 'Fetching memberships'
            memberships = await self._fetch_memberships(client, groups, progress)
            # Roles and role memberships do not support delta queries; always re-fetch in full.
            if progress is not None:
                progress.stage = 'Fetching roles'
            roles = await self._fetch_roles(client, progress)
            if progress is not None:
                progress.stage = 'Fetching role memberships'
            role_memberships = await self._fetch_role_memberships(client, roles, progress)
            if progress is not None:
                progress.stage = 'Writing exports'
            result = self._write_exports(run_id, users, groups, memberships, roles, role_memberships)

            # Persist refreshed delta tokens.
            new_tokens: dict[str, str] = {}
            if new_users_token:
                new_tokens['users'] = new_users_token
            if new_groups_token:
                new_tokens['groups'] = new_groups_token
            if new_tokens:
                await run_store.update_delta_tokens(new_tokens)

            return result
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

    async def _fetch_users(self, client: GraphServiceClient, progress: LiveProgress | None = None) -> tuple[list[dict[str, str]], str | None]:
        request_configuration = RequestConfiguration(
            query_parameters=UsersRequestBuilder.UsersRequestBuilderGetQueryParameters(
                select=['id', 'userPrincipalName', 'displayName', 'mail', 'jobTitle', 'department', 'accountEnabled'],
                top=self._settings.graph_page_size,
            )
        )
        response = await self._run_with_retry(
            lambda: client.users.get(request_configuration=request_configuration),
            operation_name='fetch users',
            progress=progress,
        )

        rows: list[dict[str, str]] = []

        def collect(user: Any) -> bool:
            rows.append(self._user_to_row(user))
            if progress is not None:
                progress.users_fetched += 1
            return True

        delta_link = await self._iterate_collection(response, client, collect, operation_name='fetch users', progress=progress)
        rows.sort(key=lambda row: (row['userPrincipalName'], row['id']))
        return rows, delta_link

    async def _fetch_users_delta(
        self, client: GraphServiceClient, delta_token: str, progress: LiveProgress | None = None
    ) -> tuple[list[dict[str, str]], list[str], str | None]:
        """Fetch user changes since the last sync using a stored delta link.

        Returns (modified_rows, deleted_ids, new_delta_link).
        """
        builder = UsersDeltaRequestBuilder(client.request_adapter, delta_token)
        response = await self._run_with_retry(
            builder.get,
            operation_name='fetch users delta',
            progress=progress,
        )

        modified: list[dict[str, str]] = []
        deleted_ids: list[str] = []

        def collect(user: Any) -> bool:
            additional_data = getattr(user, 'additional_data', {}) or {}
            if '@removed' in additional_data:
                user_id = to_csv_value(getattr(user, 'id', None))
                if user_id:
                    deleted_ids.append(user_id)
            else:
                modified.append(self._user_to_row(user))
            if progress is not None:
                progress.users_fetched += 1
            return True

        delta_link = await self._iterate_collection(response, client, collect, operation_name='fetch users delta', progress=progress)
        return modified, deleted_ids, delta_link

    async def _fetch_groups(self, client: GraphServiceClient, progress: LiveProgress | None = None) -> tuple[list[dict[str, str]], str | None]:
        request_configuration = RequestConfiguration(
            query_parameters=GroupsRequestBuilder.GroupsRequestBuilderGetQueryParameters(
                select=['id', 'displayName', 'description', 'securityEnabled', 'mailEnabled'],
                top=self._settings.graph_page_size,
            )
        )
        response = await self._run_with_retry(
            lambda: client.groups.get(request_configuration=request_configuration),
            operation_name='fetch groups',
            progress=progress,
        )

        rows: list[dict[str, str]] = []

        def collect(group: Any) -> bool:
            rows.append(self._group_to_row(group))
            if progress is not None:
                progress.groups_fetched += 1
            return True

        delta_link = await self._iterate_collection(response, client, collect, operation_name='fetch groups', progress=progress)
        rows.sort(key=lambda row: (row['displayName'], row['id']))
        return rows, delta_link

    async def _fetch_groups_delta(
        self, client: GraphServiceClient, delta_token: str, progress: LiveProgress | None = None
    ) -> tuple[list[dict[str, str]], list[str], str | None]:
        """Fetch group changes since the last sync using a stored delta link.

        Returns (modified_rows, deleted_ids, new_delta_link).
        """
        builder = GroupsDeltaRequestBuilder(client.request_adapter, delta_token)
        response = await self._run_with_retry(
            builder.get,
            operation_name='fetch groups delta',
            progress=progress,
        )

        modified: list[dict[str, str]] = []
        deleted_ids: list[str] = []

        def collect(group: Any) -> bool:
            additional_data = getattr(group, 'additional_data', {}) or {}
            if '@removed' in additional_data:
                group_id = to_csv_value(getattr(group, 'id', None))
                if group_id:
                    deleted_ids.append(group_id)
            else:
                modified.append(self._group_to_row(group))
            if progress is not None:
                progress.groups_fetched += 1
            return True

        delta_link = await self._iterate_collection(response, client, collect, operation_name='fetch groups delta', progress=progress)
        return modified, deleted_ids, delta_link

    async def _fetch_memberships(self, client: GraphServiceClient, groups: list[dict[str, str]], progress: LiveProgress | None = None) -> list[dict[str, str]]:
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
                    progress=progress,
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
                    if progress is not None:
                        progress.memberships_fetched += 1
                    return True

                await self._iterate_collection(
                    response,
                    client,
                    collect,
                    operation_name=f'fetch memberships for {group_id}',
                    progress=progress,
                )
                self._logger.info('Processed group membership page set for %s', group_id)

        await asyncio.gather(*(collect_group_members(group) for group in groups))

        rows = [
            {'group_id': group_id, 'user_id': user_id}
            for group_id, user_id in sorted(membership_pairs, key=lambda item: (item[0], item[1]))
        ]
        return rows

    async def _fetch_roles(self, client: GraphServiceClient, progress: LiveProgress | None = None) -> list[dict[str, str]]:
        request_configuration = RequestConfiguration(
            query_parameters=DirectoryRolesRequestBuilder.DirectoryRolesRequestBuilderGetQueryParameters(
                select=['id', 'roleTemplateId', 'displayName', 'description'],
            )
        )
        response = await self._run_with_retry(
            lambda: client.directory_roles.get(request_configuration=request_configuration),
            operation_name='fetch roles',
            progress=progress,
        )

        rows: list[dict[str, str]] = []

        def collect(role: Any) -> bool:
            rows.append(self._role_to_row(role))
            if progress is not None:
                progress.roles_fetched += 1
            return True

        await self._iterate_collection(response, client, collect, operation_name='fetch roles', progress=progress)
        rows.sort(key=lambda row: (row['displayName'], row['id']))
        return rows

    async def _fetch_role_memberships(
        self, client: GraphServiceClient, roles: list[dict[str, str]], progress: LiveProgress | None = None
    ) -> list[dict[str, str]]:
        semaphore = asyncio.Semaphore(self._settings.membership_concurrency)
        membership_pairs: set[tuple[str, str]] = set()

        async def collect_role_members(role: dict[str, str]) -> None:
            async with semaphore:
                role_id = role['id']
                request_configuration = RequestConfiguration(
                    query_parameters=RoleMembersRequestBuilder.MembersRequestBuilderGetQueryParameters(
                        select=['id'],
                    )
                )
                response = await self._run_with_retry(
                    lambda: client.directory_roles.by_directory_role_id(role_id).members.get(
                        request_configuration=request_configuration,
                    ),
                    operation_name=f'fetch role memberships for {role_id}',
                    progress=progress,
                )
                if response is None:
                    return

                def collect(member: Any) -> bool:
                    user_id = to_csv_value(getattr(member, 'id', None))
                    if not user_id:
                        return True
                    if getattr(member, 'odata_type', '') != '#microsoft.graph.user':
                        return True
                    membership_pairs.add((role_id, user_id))
                    if progress is not None:
                        progress.role_memberships_fetched += 1
                    return True

                await self._iterate_collection(
                    response,
                    client,
                    collect,
                    operation_name=f'fetch role memberships for {role_id}',
                    progress=progress,
                )
                self._logger.info('Processed role membership page set for %s', role_id)

        await asyncio.gather(*(collect_role_members(role) for role in roles))

        rows = [
            {'role_id': role_id, 'user_id': user_id}
            for role_id, user_id in sorted(membership_pairs, key=lambda item: (item[0], item[1]))
        ]
        return rows

    async def _iterate_collection(
        self,
        response: Any,
        client: GraphServiceClient,
        callback: Callable[[Any], bool],
        *,
        operation_name: str,
        progress: LiveProgress | None = None,
    ) -> str | None:
        """Iterate all pages of a collection, invoking callback for each item.

        Returns the ``odata_delta_link`` from the final page when present (delta
        queries only), or ``None`` for regular paginated responses.
        """
        if response is None:
            return None

        iterator = PageIterator(response, client.request_adapter)
        while True:
            iterator.enumerate(callback)
            if not iterator.current_page or not iterator.current_page.odata_next_link:
                delta_link: str | None = (
                    getattr(iterator.current_page, 'odata_delta_link', None)
                    if iterator.current_page
                    else None
                )
                return delta_link
            next_page = await self._run_with_retry(
                iterator.next,
                operation_name=f'{operation_name} page',
                progress=progress,
            )
            if not next_page:
                return None
            iterator.current_page = next_page
            iterator.pause_index = 0

    async def _run_with_retry(
        self,
        operation: Callable[[], Any],
        *,
        operation_name: str,
        progress: LiveProgress | None = None,
    ) -> Any:
        """Execute *operation*, retrying on HTTP 429 with exponential back-off.

        Back-off logic:
        - Always honour the ``Retry-After`` response header when present.
        - Otherwise use exponential back-off starting at 1 s, capped at
          ``settings.max_retry_delay_seconds``.
        - Give up (re-raise) after ``settings.max_retry_attempts`` total attempts.
        - Each throttle hit is recorded on *progress* (if provided) so the
          admin console can surface it in real time.
        """
        delay_seconds = 1.0
        max_attempts = self._max_retry_attempts
        max_delay = self._max_retry_delay_seconds

        for attempt in range(1, max_attempts + 1):
            try:
                return await operation()
            except APIError as exc:
                status_code = getattr(exc, 'response_status_code', None)
                if status_code != 429 or attempt == max_attempts:
                    raise

                # --- record throttle hit in live progress ---
                throttled_at = datetime.now(timezone.utc).isoformat()
                if progress is not None:
                    progress.throttle_count += 1
                    progress.last_throttled_at = throttled_at
                    progress.last_throttled_operation = operation_name

                # --- determine wait time ---
                retry_after: float | None = None
                headers = getattr(exc, 'response_headers', None) or {}
                if 'Retry-After' in headers:
                    try:
                        retry_after = float(headers['Retry-After'])
                    except (TypeError, ValueError):
                        retry_after = None

                wait_time = retry_after if retry_after is not None else delay_seconds
                # Always cap at the configured maximum delay.
                wait_time = min(wait_time, max_delay)

                self._logger.warning(
                    'Graph throttled (HTTP 429) during "%s". '
                    'Attempt %s/%s; sleeping %.1fs (Retry-After header: %s).',
                    operation_name,
                    attempt,
                    max_attempts,
                    wait_time,
                    retry_after,
                )
                await asyncio.sleep(wait_time)
                # Advance the exponential back-off for the next attempt (used when
                # no Retry-After header is present).
                delay_seconds = min(delay_seconds * 2, max_delay)

    def _user_to_row(self, user: Any) -> dict[str, str]:
        return {
            'id': to_csv_value(getattr(user, 'id', None)),
            'userPrincipalName': to_csv_value(getattr(user, 'user_principal_name', None)),
            'displayName': to_csv_value(getattr(user, 'display_name', None)),
            'mail': to_csv_value(getattr(user, 'mail', None)),
            'jobTitle': to_csv_value(getattr(user, 'job_title', None)),
            'department': to_csv_value(getattr(user, 'department', None)),
            'accountEnabled': to_csv_value(getattr(user, 'account_enabled', None)),
        }

    def _group_to_row(self, group: Any) -> dict[str, str]:
        return {
            'id': to_csv_value(getattr(group, 'id', None)),
            'displayName': to_csv_value(getattr(group, 'display_name', None)),
            'description': to_csv_value(getattr(group, 'description', None)),
            'securityEnabled': to_csv_value(getattr(group, 'security_enabled', None)),
            'mailEnabled': to_csv_value(getattr(group, 'mail_enabled', None)),
        }

    def _role_to_row(self, role: Any) -> dict[str, str]:
        return {
            'id': to_csv_value(getattr(role, 'id', None)),
            'roleTemplateId': to_csv_value(getattr(role, 'role_template_id', None)),
            'displayName': to_csv_value(getattr(role, 'display_name', None)),
            'description': to_csv_value(getattr(role, 'description', None)),
        }

    def _read_csv(self, file_path: Path, key_column: str) -> dict[str, dict[str, str]]:
        result: dict[str, dict[str, str]] = {}
        with file_path.open('r', newline='', encoding='utf-8') as csv_file:
            reader = csv.DictReader(csv_file)
            if reader.fieldnames is None or key_column not in reader.fieldnames:
                raise ValueError(f'CSV {file_path} is missing expected key column "{key_column}".')
            for row in reader:
                key = row.get(key_column, '')
                if key:
                    result[key] = dict(row)
        return result

    def _write_exports(
        self,
        run_id: str,
        users: list[dict[str, str]],
        groups: list[dict[str, str]],
        memberships: list[dict[str, str]],
        roles: list[dict[str, str]],
        role_memberships: list[dict[str, str]],
    ) -> ExportResult:
        run_directory = self._settings.export_base_dir / run_id
        latest_directory = self._settings.export_base_dir / 'latest'
        run_directory.mkdir(parents=True, exist_ok=True)
        latest_directory.mkdir(parents=True, exist_ok=True)

        users_file = self._write_csv(run_directory / 'users.csv', USER_COLUMNS, users)
        groups_file = self._write_csv(run_directory / 'groups.csv', GROUP_COLUMNS, groups)
        memberships_file = self._write_csv(run_directory / 'memberships.csv', MEMBERSHIP_COLUMNS, memberships)
        roles_file = self._write_csv(run_directory / 'roles.csv', ROLE_COLUMNS, roles)
        role_memberships_file = self._write_csv(
            run_directory / 'role_memberships.csv', ROLE_MEMBERSHIP_COLUMNS, role_memberships
        )

        shutil.copyfile(users_file, latest_directory / 'users.csv')
        shutil.copyfile(groups_file, latest_directory / 'groups.csv')
        shutil.copyfile(memberships_file, latest_directory / 'memberships.csv')
        shutil.copyfile(roles_file, latest_directory / 'roles.csv')
        shutil.copyfile(role_memberships_file, latest_directory / 'role_memberships.csv')

        return ExportResult(
            users_count=len(users),
            groups_count=len(groups),
            memberships_count=len(memberships),
            roles_count=len(roles),
            role_memberships_count=len(role_memberships),
            users_file=str(users_file),
            groups_file=str(groups_file),
            memberships_file=str(memberships_file),
            roles_file=str(roles_file),
            role_memberships_file=str(role_memberships_file),
        )

    def _write_csv(self, file_path: Path, columns: tuple[str, ...], rows: list[dict[str, str]]) -> Path:
        with file_path.open('w', newline='', encoding='utf-8') as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=list(columns))
            writer.writeheader()
            for row in rows:
                writer.writerow({column: row.get(column, '') for column in columns})
        return file_path
