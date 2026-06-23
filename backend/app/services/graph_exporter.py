from __future__ import annotations

import asyncio
import csv
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import urlparse, parse_qs

from azure.identity.aio import ClientSecretCredential
from kiota_abstractions.api_error import APIError
from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph import GraphServiceClient
from msgraph.generated.directory_roles.directory_roles_request_builder import DirectoryRolesRequestBuilder
from msgraph.generated.directory_roles.item.members.members_request_builder import (
    MembersRequestBuilder as RoleMembersRequestBuilder,
)
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
MAX_SHORT_DESCRIPTION_LENGTH = 255


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
    group_owners_fetched: int = 0
    nested_groups_fetched: int = 0
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

    def _extract_delta_token(self, url: str | None) -> str | None:
        """Helper to extract raw token out of complete Microsoft Graph delta link URLs."""
        if not url:
            return None
        parsed = urlparse(url)
        queries = parse_qs(parsed.query)
        token_list = queries.get('$deltatoken') or queries.get('deltatoken')
        return token_list[0] if token_list else url  # Fallback to entire string if parse fails

    async def export(self, run_id: str, sync_type: str = 'full', run_store: RunStore | None = None, progress: LiveProgress | None = None) -> ExportResult:
        if not self._settings.graph_configured:
            raise RuntimeError('TENANT_ID, CLIENT_ID, and CLIENT_SECRET must all be configured.')

        self._logger.debug('Starting %s export for run %s', sync_type, run_id)
        if sync_type == 'incremental' and run_store is not None:
            return await self._export_incremental(run_id, run_store, progress)
        return await self._export_full(run_id, run_store, progress)

    async def _export_full(self, run_id: str, run_store: RunStore | None, progress: LiveProgress | None = None) -> ExportResult:
        self._logger.debug('Creating Graph client for full export run %s', run_id)
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
            self._logger.debug('Fetched %d users for run %s', len(users), run_id)
            if progress is not None:
                progress.stage = 'Fetching groups'
            groups, groups_delta_link = await self._fetch_groups(client, progress)
            self._logger.debug('Fetched %d groups for run %s', len(groups), run_id)
            if progress is not None:
                progress.stage = 'Fetching memberships'
            memberships = await self._fetch_memberships(client, groups, progress)
            self._logger.debug('Fetched %d memberships for run %s', len(memberships), run_id)
            if progress is not None:
                progress.stage = 'Fetching group owners'
            group_owners = await self._fetch_group_owners(client, groups, progress)
            self._logger.debug('Fetched %d group owner relationships for run %s', len(group_owners), run_id)
            if progress is not None:
                progress.stage = 'Fetching nested groups'
            nested_groups = await self._fetch_nested_groups(client, groups, progress)
            self._logger.debug('Fetched %d nested group relationships for run %s', len(nested_groups), run_id)
            if progress is not None:
                progress.stage = 'Fetching roles'
            roles = await self._fetch_roles(client, progress)
            self._logger.debug('Fetched %d roles for run %s', len(roles), run_id)
            if progress is not None:
                progress.stage = 'Fetching role memberships'
            role_memberships = await self._fetch_role_memberships(client, roles, progress)
            self._logger.debug('Fetched %d role memberships for run %s', len(role_memberships), run_id)
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

            # Store extracted baseline delta tokens so future incremental syncs have a clean state string.
            if run_store is not None:
                tokens: dict[str, str] = {}
                clean_user_token = self._extract_delta_token(users_delta_link)
                clean_group_token = self._extract_delta_token(groups_delta_link)

                if clean_user_token:
                    tokens['users'] = clean_user_token
                else:
                    self._logger.warning(
                        'No users delta link returned for run %s; future incremental syncs will fall back to full.',
                        run_id,
                    )
                if clean_group_token:
                    tokens['groups'] = clean_group_token
                else:
                    self._logger.warning(
                        'No groups delta link returned for run %s; future incremental syncs will fall back to full.',
                        run_id,
                    )
                if tokens:
                    await run_store.update_delta_tokens(tokens)
                    self._logger.debug('Stored delta tokens for run %s', run_id)

            return result
        finally:
            await credential.close()

    async def _export_incremental(self, run_id: str, run_store: RunStore, progress: LiveProgress | None = None) -> ExportResult:
        """Incremental export using Microsoft Graph delta queries.

        Fetches only changed users and groups since the last sync, merges those
        changes into the latest exported CSVs, then re-fetches all memberships
        for the current group set to ensure accuracy. Falls back to a full
        export when no stored delta tokens are found or the tokens have expired.
        """
        delta_tokens = await run_store.get_delta_tokens()
        users_token = delta_tokens.get('users')
        groups_token = delta_tokens.get('groups')
        self._logger.debug('Retrieved delta tokens for incremental run %s: users_token=%s, groups_token=%s', run_id, bool(users_token), bool(groups_token))

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
                self._logger.debug('Incremental run %s: %d modified users, %d deleted users', run_id, len(modified_users), len(deleted_user_ids))
                if progress is not None:
                    progress.stage = 'Fetching groups'
                modified_groups, deleted_group_ids, new_groups_token = await self._fetch_groups_delta(
                    client, groups_token, progress
                )
                self._logger.debug('Incremental run %s: %d modified groups, %d deleted groups', run_id, len(modified_groups), len(deleted_group_ids))
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
            self._logger.debug('Incremental run %s: merged baseline → %d users, %d groups', run_id, len(users), len(groups))

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

            # Persist clean, refreshed delta tokens.
            new_tokens: dict[str, str] = {}
            clean_user_token = self._extract_delta_token(new_users_token)
            clean_group_token = self._extract_delta_token(new_groups_token)

            if clean_user_token:
                new_tokens['users'] = clean_user_token
            if clean_group_token:
                new_tokens['groups'] = clean_group_token
            if new_tokens:
                await run_store.update_delta_tokens(new_tokens)
                self._logger.debug('Stored refreshed delta tokens for incremental run %s', run_id)

            return result
        finally:
            await credential.close()

    async def check_connection(self, *, overrides: dict[str, str | None] | None = None) -> tuple[bool, str]:
        config = self._resolve_connection_settings(overrides)
        if not config['tenant_id'] or not config['client_id'] or not config['client_secret']:
            return False, 'Graph credentials are not configured.'

        self._logger.debug('Testing Graph connection (tenant=%s, client=%s)', config.get('tenant_id'), config.get('client_id'))
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
            self._logger.debug('Graph connection check succeeded')
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
        self._logger.debug('Fetching all users (page size %d)', self._settings.graph_page_size)
        request_configuration = RequestConfiguration(
            query_parameters=UsersDeltaRequestBuilder.DeltaRequestBuilderGetQueryParameters(
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
            lambda: client.users.delta.get(request_configuration=request_configuration),
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
        self._logger.debug('Finished fetching users: %d rows collected', len(rows))
        return rows, delta_link

    async def _fetch_users_delta(
        self, client: GraphServiceClient, delta_token: str, progress: LiveProgress | None = None
    ) -> tuple[list[dict[str, str]], list[str], str | None]:
        """Fetch user changes since the last sync using a stored delta link."""
        self._logger.debug('Fetching user delta changes')
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
        self._logger.debug('Finished user delta: %d modified, %d deleted', len(modified), len(deleted_ids))
        return modified, deleted_ids, delta_link

    async def _fetch_groups(self, client: GraphServiceClient, progress: LiveProgress | None = None) -> tuple[list[dict[str, str]], str | None]:
        self._logger.debug('Fetching all groups (page size %d)', self._settings.graph_page_size)
        request_configuration = RequestConfiguration(
            query_parameters=GroupsDeltaRequestBuilder.DeltaRequestBuilderGetQueryParameters(
                select=[
                    'id',
                    'displayName',
                    'description',
                    'securityEnabled',
                    'mailEnabled',
                    'mailNickname',
                ],
                top=self._settings.graph_page_size,
            )
        )
        response = await self._run_with_retry(
            lambda: client.groups.delta.get(request_configuration=request_configuration),
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
        self._logger.debug('Finished fetching groups: %d rows collected', len(rows))
        return rows, delta_link

    async def _fetch_groups_delta(
        self, client: GraphServiceClient, delta_token: str, progress: LiveProgress | None = None
    ) -> tuple[list[dict[str, str]], list[str], str | None]:
        """Fetch group changes since the last sync using a stored delta link."""
        self._logger.debug('Fetching group delta changes')
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
        self._logger.debug('Finished group delta: %d modified, %d deleted', len(modified), len(deleted_ids))
        return modified, deleted_ids, delta_link

    async def _fetch_memberships(self, client: GraphServiceClient, groups: list[dict[str, str]], progress: LiveProgress | None = None) -> list[dict[str, str]]:
        semaphore =
