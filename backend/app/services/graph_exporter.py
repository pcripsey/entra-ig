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
    'givenName',
    'surname',
    'mailNickname',
    'employeeId',
    'employeeType',
    'companyName',
    'streetAddress',
    'officeLocation',
    'businessPhone',
    'mobilePhone',
    'preferredLanguage',
    'country',
    'city',
    'state',
    'onPremisesDistinguishedName',
    'onPremisesImmutableId',
    'userType',
    'otherMails',
    'sapId',
    'managerId',
)
GROUP_COLUMNS = (
    'id',
    'displayName',
    'description',
    'securityEnabled',
    'mailEnabled',
    'mailNickname',
    'onPremisesObjectIdentifier',
    'onPremisesDistinguishedName',
)
MEMBERSHIP_COLUMNS = ('group_id', 'user_id')
ROLE_COLUMNS = ('id', 'roleTemplateId', 'displayName', 'description')
ROLE_MEMBERSHIP_COLUMNS = ('role_id', 'user_id')

# IG output column definitions
IDENTITY_COLUMNS = (
    'identityId',
    'employeeNumber',
    'company',
    'street',
    'cn',
    'AzureUserID',
    'AzureMailNickname',
    'hrEmpNumber',
    'department',
    'ldapDN',
    'email',
    'employeeType',
    'firstName',
    'fullName',
    'objectGUID',
    'phoneHome',
    'jobCode',
    'lastName',
    'location',
    'middleName',
    'phoneOffice',
    'phoneMobile',
    'preferredLocale',
    'provisioningID',
    'secondarySupervisorId',
    'primarySupervisorId',
    'affiliatedIdentity',
    'employeeStatus',
    'country',
    'city',
    'state',
    'geoLocation',
    'userRisk',
    'workforceID',
    'idmDN',
    'idmTreeName',
    'loginAttribute',
    'title',
)

IG_ACCOUNT_COLUMNS = (
    'accountId',
    'displayName',
    'description',
    'type',
    'risk',
    'cost',
    'SAP_ID',
    'aliases',
    'connectedAccountProvisioningID',
    'disabled',
    'privileged',
    'state',
    'accountProvisioningID',
    'accountUserMapping',
    'accountCustodianMapping',
    'idmAccountID',
    'provisioningDriverID',
    'provisioningDriverLogicalID',
)

IG_GROUP_COLUMNS = (
    'groupId',
    'groupOwners',
    'objectGUID',
    'groupMembers',
    'name',
    'longDescription',
    'ldapDN',
    'alternateName',
    'shortDescription',
)

IG_GROUP_MEMBERSHIP_COLUMNS = ('groupId', 'members')
IG_PARENT_CHILD_GROUP_COLUMNS = ('parentId', 'childId')

IG_PERMISSION_COLUMNS = (
    'permissionId',
    'displayName',
    'description',
    'type',
    'assignable',
    'owner',
    'risk',
    'cost',
    'holder',
    'childPermissionId',
    'parentPermissionId',
    'hiddenFromCatalog',
    'provisioningTargetAttribute',
    'provisionedByThisPermission',
    'nativeValueForProvisioning',
    'uniqueApplicationID',
    'staticPermissionFlag',
    'provisioningDriverID',
    'provisioningApplicationLogicalID',
)

IG_HOLDER_TO_PERMISSION_COLUMNS = (
    'assignmentId',
    'accountId',
    'permissionId',
    'usage',
    'risk',
    'revocable',
    'assignmentType',
    'assignmentValue',
)

IG_PERMISSION_TO_HOLDER_COLUMNS = (
    'permissionId',
    'accountId',
    'assignmentId',
    'assignmentType',
    'assignmentRisk',
    'usage',
    'revocable',
    'assignmentValue',
)

IG_PERMISSION_HIERARCHY_COLUMNS = ('permissionId', 'parentPermissionId', 'assignmentType')
IG_PERMISSION_HIERARCHY_PC_COLUMNS = ('permissionId', 'childPermissionId', 'assignmentType')


@dataclass(slots=True)
class ExportResult:
    users_count: int
    groups_count: int
    memberships_count: int
    roles_count: int
    role_memberships_count: int
    nested_groups_count: int
    identity_file: str
    account_file: str
    group_file: str
    group_membership_file: str
    parent_child_group_file: str
    permission_file: str
    holder_to_permission_file: str
    permission_to_holder_file: str
    permission_hierarchy_cp_file: str
    permission_hierarchy_pc_file: str
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
                progress.stage = 'Fetching group owners'
            group_owners = await self._fetch_group_owners(client, groups, progress)
            if progress is not None:
                progress.stage = 'Fetching nested groups'
            nested_groups = await self._fetch_nested_groups(client, groups, progress)
            if progress is not None:
                progress.stage = 'Fetching roles'
            roles = await self._fetch_roles(client, progress)
            if progress is not None:
                progress.stage = 'Fetching role memberships'
            role_memberships = await self._fetch_role_memberships(client, roles, progress)
            if progress is not None:
                progress.stage = 'Writing exports'
            result = self._write_exports(
                run_id,
                users,
                groups,
                memberships,
                roles,
                role_memberships,
                group_owners,
                nested_groups,
            )

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
            if progress is not None:
                progress.stage = 'Fetching group owners'
            group_owners = await self._fetch_group_owners(client, groups, progress)
            if progress is not None:
                progress.stage = 'Fetching nested groups'
            nested_groups = await self._fetch_nested_groups(client, groups, progress)
            # Roles and role memberships do not support delta queries; always re-fetch in full.
            if progress is not None:
                progress.stage = 'Fetching roles'
            roles = await self._fetch_roles(client, progress)
            if progress is not None:
                progress.stage = 'Fetching role memberships'
            role_memberships = await self._fetch_role_memberships(client, roles, progress)
            if progress is not None:
                progress.stage = 'Writing exports'
            result = self._write_exports(
                run_id,
                users,
                groups,
                memberships,
                roles,
                role_memberships,
                group_owners,
                nested_groups,
            )

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
                select=[
                    'id',
                    'userPrincipalName',
                    'displayName',
                    'mail',
                    'jobTitle',
                    'department',
                    'accountEnabled',
                    'givenName',
                    'surname',
                    'mailNickname',
                    'employeeId',
                    'employeeType',
                    'companyName',
                    'streetAddress',
                    'officeLocation',
                    'businessPhones',
                    'mobilePhone',
                    'preferredLanguage',
                    'country',
                    'city',
                    'state',
                    'onPremisesDistinguishedName',
                    'onPremisesImmutableId',
                    'userType',
                    'otherMails',
                    'onPremisesExtensionAttributes',
                ],
                expand=['manager($select=id)'],
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
                select=[
                    'id',
                    'displayName',
                    'description',
                    'securityEnabled',
                    'mailEnabled',
                    'mailNickname',
                    'onPremisesObjectIdentifier',
                    'onPremisesDistinguishedName',
                ],
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

    async def _fetch_group_owners(
        self, client: GraphServiceClient, groups: list[dict[str, str]], progress: LiveProgress | None = None
    ) -> dict[str, list[str]]:
        """Returns a dict of group_id -> [owner_user_id, ...]."""
        semaphore = asyncio.Semaphore(self._settings.membership_concurrency)
        owners_map: dict[str, list[str]] = {}

        async def collect_group_owners(group: dict[str, str]) -> None:
            async with semaphore:
                group_id = group['id']
                response = await self._run_with_retry(
                    lambda: client.groups.by_group_id(group_id).owners.get(),
                    operation_name=f'fetch owners for {group_id}',
                    progress=progress,
                )
                if response is None:
                    return

                owner_ids: list[str] = []

                def collect(owner: Any) -> bool:
                    if getattr(owner, 'odata_type', '') == '#microsoft.graph.user':
                        uid = to_csv_value(getattr(owner, 'id', None))
                        if uid:
                            owner_ids.append(uid)
                    return True

                await self._iterate_collection(
                    response,
                    client,
                    collect,
                    operation_name=f'fetch owners for {group_id}',
                    progress=progress,
                )
                owners_map[group_id] = sorted(set(owner_ids))

        await asyncio.gather(*(collect_group_owners(group) for group in groups))
        return owners_map

    async def _fetch_nested_groups(
        self, client: GraphServiceClient, groups: list[dict[str, str]], progress: LiveProgress | None = None
    ) -> list[dict[str, str]]:
        """Returns list of {parentId, childId} dicts for nested group membership."""
        semaphore = asyncio.Semaphore(self._settings.membership_concurrency)
        nesting_pairs: set[tuple[str, str]] = set()

        async def collect_group_children(group: dict[str, str]) -> None:
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
                    operation_name=f'fetch nested groups for {group_id}',
                    progress=progress,
                )
                if response is None:
                    return

                def collect(member: Any) -> bool:
                    if getattr(member, 'odata_type', '') == '#microsoft.graph.group':
                        child_id = to_csv_value(getattr(member, 'id', None))
                        if child_id:
                            nesting_pairs.add((group_id, child_id))
                    return True

                await self._iterate_collection(
                    response,
                    client,
                    collect,
                    operation_name=f'fetch nested groups for {group_id}',
                    progress=progress,
                )

        await asyncio.gather(*(collect_group_children(group) for group in groups))
        return [{'parentId': parent_id, 'childId': child_id} for parent_id, child_id in sorted(nesting_pairs)]

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
        manager = getattr(user, 'manager', None)
        manager_id = to_csv_value(getattr(manager, 'id', None)) if manager else ''
        business_phones = getattr(user, 'business_phones', None) or []
        other_mails = getattr(user, 'other_mails', None) or []
        ext_attrs = getattr(user, 'on_premises_extension_attributes', None)
        sap_id = ''
        if ext_attrs:
            sap_id = to_csv_value(getattr(ext_attrs, 'extension_attribute1', None))
        return {
            'id': to_csv_value(getattr(user, 'id', None)),
            'userPrincipalName': to_csv_value(getattr(user, 'user_principal_name', None)),
            'displayName': to_csv_value(getattr(user, 'display_name', None)),
            'mail': to_csv_value(getattr(user, 'mail', None)),
            'jobTitle': to_csv_value(getattr(user, 'job_title', None)),
            'department': to_csv_value(getattr(user, 'department', None)),
            'accountEnabled': to_csv_value(getattr(user, 'account_enabled', None)),
            'givenName': to_csv_value(getattr(user, 'given_name', None)),
            'surname': to_csv_value(getattr(user, 'surname', None)),
            'mailNickname': to_csv_value(getattr(user, 'mail_nickname', None)),
            'employeeId': to_csv_value(getattr(user, 'employee_id', None)),
            'employeeType': to_csv_value(getattr(user, 'employee_type', None)),
            'companyName': to_csv_value(getattr(user, 'company_name', None)),
            'streetAddress': to_csv_value(getattr(user, 'street_address', None)),
            'officeLocation': to_csv_value(getattr(user, 'office_location', None)),
            'businessPhone': to_csv_value(business_phones[0]) if business_phones else '',
            'mobilePhone': to_csv_value(getattr(user, 'mobile_phone', None)),
            'preferredLanguage': to_csv_value(getattr(user, 'preferred_language', None)),
            'country': to_csv_value(getattr(user, 'country', None)),
            'city': to_csv_value(getattr(user, 'city', None)),
            'state': to_csv_value(getattr(user, 'state', None)),
            'onPremisesDistinguishedName': to_csv_value(getattr(user, 'on_premises_distinguished_name', None)),
            'onPremisesImmutableId': to_csv_value(getattr(user, 'on_premises_immutable_id', None)),
            'userType': to_csv_value(getattr(user, 'user_type', None)),
            'otherMails': ';'.join(to_csv_value(mail) for mail in other_mails if to_csv_value(mail)),
            'sapId': sap_id,
            'managerId': manager_id,
        }

    def _group_to_row(self, group: Any) -> dict[str, str]:
        return {
            'id': to_csv_value(getattr(group, 'id', None)),
            'displayName': to_csv_value(getattr(group, 'display_name', None)),
            'description': to_csv_value(getattr(group, 'description', None)),
            'securityEnabled': to_csv_value(getattr(group, 'security_enabled', None)),
            'mailEnabled': to_csv_value(getattr(group, 'mail_enabled', None)),
            'mailNickname': to_csv_value(getattr(group, 'mail_nickname', None)),
            'onPremisesObjectIdentifier': to_csv_value(getattr(group, 'on_premises_object_identifier', None)),
            'onPremisesDistinguishedName': to_csv_value(getattr(group, 'on_premises_distinguished_name', None)),
        }

    def _role_to_row(self, role: Any) -> dict[str, str]:
        return {
            'id': to_csv_value(getattr(role, 'id', None)),
            'roleTemplateId': to_csv_value(getattr(role, 'role_template_id', None)),
            'displayName': to_csv_value(getattr(role, 'display_name', None)),
            'description': to_csv_value(getattr(role, 'description', None)),
        }

    def _user_to_identity_row(self, user: dict[str, str]) -> dict[str, str]:
        return {
            'identityId': user.get('id', ''),
            'employeeNumber': user.get('employeeId', ''),
            'company': user.get('companyName', ''),
            'street': user.get('streetAddress', ''),
            'cn': user.get('displayName', ''),
            'AzureUserID': user.get('id', ''),
            'AzureMailNickname': user.get('mailNickname', ''),
            'hrEmpNumber': user.get('employeeId', ''),
            'department': user.get('department', ''),
            'ldapDN': user.get('onPremisesDistinguishedName', ''),
            'email': user.get('mail', ''),
            'employeeType': user.get('employeeType', ''),
            'firstName': user.get('givenName', ''),
            'fullName': user.get('displayName', ''),
            'objectGUID': user.get('onPremisesImmutableId', ''),
            'phoneHome': '',
            'jobCode': user.get('jobTitle', ''),
            'lastName': user.get('surname', ''),
            'location': user.get('officeLocation', ''),
            'middleName': '',
            'phoneOffice': user.get('businessPhone', ''),
            'phoneMobile': user.get('mobilePhone', ''),
            'preferredLocale': user.get('preferredLanguage', ''),
            'provisioningID': user.get('userPrincipalName', ''),
            'secondarySupervisorId': '',
            'primarySupervisorId': user.get('managerId', ''),
            'affiliatedIdentity': '',
            'employeeStatus': 'active' if user.get('accountEnabled', '').lower() == 'true' else 'inactive',
            'country': user.get('country', ''),
            'city': user.get('city', ''),
            'state': user.get('state', ''),
            'geoLocation': '',
            'userRisk': '',
            'workforceID': user.get('employeeId', ''),
            'idmDN': '',
            'idmTreeName': '',
            'loginAttribute': user.get('userPrincipalName', ''),
            'title': user.get('jobTitle', ''),
        }

    def _user_to_account_row(self, user: dict[str, str], privileged_ids: set[str]) -> dict[str, str]:
        user_id = user.get('id', '')
        is_disabled = user.get('accountEnabled', '').lower() != 'true'
        return {
            'accountId': user_id,
            'displayName': user.get('displayName', ''),
            'description': '',
            'type': user.get('userType', 'Member') or 'Member',
            'risk': '',
            'cost': '',
            'SAP_ID': user.get('sapId', ''),
            'aliases': user.get('otherMails', ''),
            'connectedAccountProvisioningID': '',
            'disabled': 'true' if is_disabled else 'false',
            'privileged': 'true' if user_id in privileged_ids else 'false',
            'state': 'disabled' if is_disabled else 'active',
            'accountProvisioningID': user.get('userPrincipalName', ''),
            'accountUserMapping': user_id,
            'accountCustodianMapping': user.get('managerId', ''),
            'idmAccountID': '',
            'provisioningDriverID': '',
            'provisioningDriverLogicalID': '',
        }

    def _group_to_ig_row(
        self,
        group: dict[str, str],
        owners_map: dict[str, list[str]],
        members_by_group: dict[str, list[str]],
    ) -> dict[str, str]:
        group_id = group.get('id', '')
        owner_ids = ';'.join(owners_map.get(group_id, []))
        member_ids = ';'.join(members_by_group.get(group_id, []))
        description = group.get('description', '')
        return {
            'groupId': group_id,
            'groupOwners': owner_ids,
            'objectGUID': group.get('onPremisesObjectIdentifier', ''),
            'groupMembers': member_ids,
            'name': group.get('displayName', ''),
            'longDescription': description,
            'ldapDN': group.get('onPremisesDistinguishedName', ''),
            'alternateName': group.get('mailNickname', ''),
            'shortDescription': description[:255] if description else '',
        }

    def _role_to_ig_permission_row(self, role: dict[str, str], holder_count: int) -> dict[str, str]:
        return {
            'permissionId': role.get('id', ''),
            'displayName': role.get('displayName', ''),
            'description': role.get('description', ''),
            'type': 'DirectoryRole',
            'assignable': 'true',
            'owner': '',
            'risk': '',
            'cost': '',
            'holder': str(holder_count),
            'childPermissionId': '',
            'parentPermissionId': '',
            'hiddenFromCatalog': 'false',
            'provisioningTargetAttribute': 'roleTemplateId',
            'provisionedByThisPermission': 'false',
            'nativeValueForProvisioning': role.get('roleTemplateId', ''),
            'uniqueApplicationID': '',
            'staticPermissionFlag': 'false',
            'provisioningDriverID': '',
            'provisioningApplicationLogicalID': '',
        }

    def _role_membership_to_holder_row(self, rm: dict[str, str]) -> dict[str, str]:
        assignment_id = f"{rm['user_id']}_{rm['role_id']}"
        return {
            'assignmentId': assignment_id,
            'accountId': rm['user_id'],
            'permissionId': rm['role_id'],
            'usage': '',
            'risk': '',
            'revocable': 'true',
            'assignmentType': 'DIRECT',
            'assignmentValue': '',
        }

    def _role_membership_to_permission_row(self, rm: dict[str, str]) -> dict[str, str]:
        assignment_id = f"{rm['user_id']}_{rm['role_id']}"
        return {
            'permissionId': rm['role_id'],
            'accountId': rm['user_id'],
            'assignmentId': assignment_id,
            'assignmentType': 'DIRECT',
            'assignmentRisk': '',
            'usage': '',
            'revocable': 'true',
            'assignmentValue': '',
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
        group_owners: dict[str, list[str]],
        nested_groups: list[dict[str, str]],
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
        members_by_group: dict[str, list[str]] = {}
        for membership in memberships:
            group_id = membership.get('group_id', '')
            user_id = membership.get('user_id', '')
            if group_id and user_id:
                members_by_group.setdefault(group_id, []).append(user_id)
        for member_ids in members_by_group.values():
            member_ids.sort()

        privileged_ids = {rm.get('user_id', '') for rm in role_memberships if rm.get('user_id', '')}
        holder_counts: dict[str, int] = {}
        for rm in role_memberships:
            role_id = rm.get('role_id', '')
            if role_id:
                holder_counts[role_id] = holder_counts.get(role_id, 0) + 1

        identity_rows = [self._user_to_identity_row(user) for user in users]
        account_rows = [self._user_to_account_row(user, privileged_ids) for user in users]
        group_rows = [self._group_to_ig_row(group, group_owners, members_by_group) for group in groups]
        group_membership_rows = [{'groupId': m['group_id'], 'members': m['user_id']} for m in memberships]
        permission_rows = [
            self._role_to_ig_permission_row(role, holder_counts.get(role.get('id', ''), 0)) for role in roles
        ]
        holder_to_permission_rows = [self._role_membership_to_holder_row(rm) for rm in role_memberships]
        permission_to_holder_rows = [self._role_membership_to_permission_row(rm) for rm in role_memberships]

        identity_file = self._write_csv(run_directory / 'Identity.csv', IDENTITY_COLUMNS, identity_rows)
        account_file = self._write_csv(run_directory / 'ig_account_import.csv', IG_ACCOUNT_COLUMNS, account_rows)
        group_file = self._write_csv(run_directory / 'ig_group_import.csv', IG_GROUP_COLUMNS, group_rows)
        group_membership_file = self._write_csv(
            run_directory / 'ig_group_to_user_membership.csv', IG_GROUP_MEMBERSHIP_COLUMNS, group_membership_rows
        )
        parent_child_group_file = self._write_csv(
            run_directory / 'ig_parent_group_to_child_group.csv', IG_PARENT_CHILD_GROUP_COLUMNS, nested_groups
        )
        permission_file = self._write_csv(
            run_directory / 'ig_permission_import.csv', IG_PERMISSION_COLUMNS, permission_rows
        )
        holder_to_permission_file = self._write_csv(
            run_directory / 'ig_holder_to_permissions_mapping.csv',
            IG_HOLDER_TO_PERMISSION_COLUMNS,
            holder_to_permission_rows,
        )
        permission_to_holder_file = self._write_csv(
            run_directory / 'ig_permission_to_holders_mapping.csv',
            IG_PERMISSION_TO_HOLDER_COLUMNS,
            permission_to_holder_rows,
        )
        permission_hierarchy_cp_file = self._write_csv(
            run_directory / 'ig_permission_hierarchy_child_parent.csv',
            IG_PERMISSION_HIERARCHY_COLUMNS,
            [],
        )
        permission_hierarchy_pc_file = self._write_csv(
            run_directory / 'ig_permission_hierarchy_parent_child.csv',
            IG_PERMISSION_HIERARCHY_PC_COLUMNS,
            [],
        )

        shutil.copyfile(users_file, latest_directory / 'users.csv')
        shutil.copyfile(groups_file, latest_directory / 'groups.csv')
        shutil.copyfile(memberships_file, latest_directory / 'memberships.csv')
        shutil.copyfile(roles_file, latest_directory / 'roles.csv')
        shutil.copyfile(role_memberships_file, latest_directory / 'role_memberships.csv')
        shutil.copyfile(identity_file, latest_directory / 'Identity.csv')
        shutil.copyfile(account_file, latest_directory / 'ig_account_import.csv')
        shutil.copyfile(group_file, latest_directory / 'ig_group_import.csv')
        shutil.copyfile(group_membership_file, latest_directory / 'ig_group_to_user_membership.csv')
        shutil.copyfile(parent_child_group_file, latest_directory / 'ig_parent_group_to_child_group.csv')
        shutil.copyfile(permission_file, latest_directory / 'ig_permission_import.csv')
        shutil.copyfile(holder_to_permission_file, latest_directory / 'ig_holder_to_permissions_mapping.csv')
        shutil.copyfile(permission_to_holder_file, latest_directory / 'ig_permission_to_holders_mapping.csv')
        shutil.copyfile(
            permission_hierarchy_cp_file,
            latest_directory / 'ig_permission_hierarchy_child_parent.csv',
        )
        shutil.copyfile(
            permission_hierarchy_pc_file,
            latest_directory / 'ig_permission_hierarchy_parent_child.csv',
        )

        return ExportResult(
            users_count=len(users),
            groups_count=len(groups),
            memberships_count=len(memberships),
            roles_count=len(roles),
            role_memberships_count=len(role_memberships),
            nested_groups_count=len(nested_groups),
            identity_file=str(identity_file),
            account_file=str(account_file),
            group_file=str(group_file),
            group_membership_file=str(group_membership_file),
            parent_child_group_file=str(parent_child_group_file),
            permission_file=str(permission_file),
            holder_to_permission_file=str(holder_to_permission_file),
            permission_to_holder_file=str(permission_to_holder_file),
            permission_hierarchy_cp_file=str(permission_hierarchy_cp_file),
            permission_hierarchy_pc_file=str(permission_hierarchy_pc_file),
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
