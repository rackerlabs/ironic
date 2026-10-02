# -*- coding: utf-8 -*-
#
# Copyright 2014 Red Hat, Inc.
# All Rights Reserved.
# Copyright (c) 2017-2021 Dell Inc. or its subsidiaries.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""
DRAC management interface
"""

import json
import time

import jsonschema
from jsonschema import exceptions as json_schema_exc
from oslo_log import log as logging
import sushy

from ironic.common import boot_devices
from ironic.common import exception
from ironic.common.i18n import _
from ironic.common import metrics_utils
from ironic.common import molds
from ironic.common import states
from ironic.conductor import periodics
from ironic.conductor import utils as manager_utils
from ironic.conf import CONF
from ironic.drivers import base
from ironic.drivers.modules import deploy_utils
from ironic.drivers.modules.drac import utils as drac_utils
from ironic.drivers.modules.redfish import management as redfish_management
from ironic.drivers.modules.redfish import utils as redfish_utils


LOG = logging.getLogger(__name__)

METRICS = metrics_utils.get_metrics_logger(__name__)

# This dictionary is used to map boot device names between two (2) name
# spaces. The name spaces are:
#
#     1) ironic boot devices
#     2) iDRAC boot sources
#
# Mapping can be performed in both directions.
#
# The keys are ironic boot device types. Each value is a list of strings
# that appear in the identifiers of iDRAC boot sources.
#
# The iDRAC represents boot sources with class DCIM_BootSourceSetting
# [1]. Each instance of that class contains a unique identifier, which
# is called an instance identifier, InstanceID,
#
# An InstanceID contains the Fully Qualified Device Descriptor (FQDD) of
# the physical device that hosts the boot source [2].
#
# [1] "Dell EMC BIOS and Boot Management Profile", Version 4.0.0, July
#     10, 2017, Section 7.2 "Boot Management", pp. 44-47 --
#     http://en.community.dell.com/techcenter/extras/m/white_papers/20444495/download
# [2] "Lifecycle Controller Version 3.15.15.15 User's Guide", Dell EMC,
#     2017, Table 13, "Easy-to-use Names of System Components", pp. 71-74 --
#     http://topics-cdn.dell.com/pdf/idrac9-lifecycle-controller-v3.15.15.15_users-guide2_en-us.pdf
_BOOT_DEVICES_MAP = {
    boot_devices.DISK: ['AHCI', 'Disk', 'RAID'],
    boot_devices.PXE: ['NIC'],
    boot_devices.CDROM: ['Optical'],
}

_DRAC_BOOT_MODES = ['Bios', 'Uefi']

# BootMode constant
_NON_PERSISTENT_BOOT_MODE = 'OneTime'

# Clear job id's constant
_CLEAR_JOB_IDS = 'JID_CLEARALL'

# Clean steps constant
_CLEAR_JOBS_CLEAN_STEPS = ['clear_job_queue', 'known_good_state']

_CONF_MOLD_SCHEMA = {
    'type': 'object',
    'properties': {
        'oem': {
            'type': 'object',
            'properties': {
                'interface': {'const': 'idrac-redfish'},
                'data': {'type': 'object', 'minProperties': 1}
            },
            'required': ['interface', 'data']
        }

    },
    'required': ['oem'],
    'additionalProperties': False
}

# iDRAC supports up to three NTP servers and two static IPv4 DNS servers.
_MAX_NTP_SERVERS = 3
_MAX_DNS_SERVERS = 2
_MAX_OIDC_PROVIDERS = 16
# driver_internal_info key holding the state of an in-progress OIDC
# registration that the periodic task polls.
_OIDC_REGISTRATION_INFO = 'oidc_registration'

_SET_NTP_ARGSINFO = {
    'ntp_servers': {
        'description': (
            'A list of up to three NTP server addresses to configure on the '
            'iDRAC.'),
        'required': True,
    },
    'enable_ntp': {
        'description': (
            'Whether to enable NTP time synchronisation. Defaults to True.'),
        'required': False,
    },
    'timezone': {
        'description': (
            'Optional iDRAC timezone string, e.g. "US/Central".'),
        'required': False,
    },
    'extra_attributes': {
        'description': (
            'Optional dict of raw Dell OEM attribute name/value pairs '
            'merged into the PATCH, for iDRAC firmware whose attribute '
            'names differ.'),
        'required': False,
    },
}

_SET_DNS_ARGSINFO = {
    'dns_servers': {
        'description': (
            'A list of up to two static IPv4 DNS server addresses to '
            'configure on the iDRAC.'),
        'required': True,
    },
    'dns_domain_name': {
        'description': (
            'Optional DNS domain name to set on the iDRAC.'),
        'required': False,
    },
    'extra_attributes': {
        'description': (
            'Optional dict of raw Dell OEM attribute name/value pairs '
            'merged into the PATCH, for iDRAC firmware whose attribute '
            'names differ.'),
        'required': False,
    },
}

_SET_OIDC_ARGSINFO = {
    'discovery_url': {
        'description': (
            'The OpenID Connect provider discovery URL '
            '(.well-known/openid-configuration).'),
        'required': False,
    },
    'initial_access_token': {
        'description': (
            'The RFC 7591 initial access token used by iDRAC to register '
            'itself with the provider. This value is stored in the runbook '
            'step arguments.'),
        'required': False,
    },
    'https_certificate': {
        'description': (
            'The PEM-encoded CA certificate used to validate the provider.'),
        'required': False,
    },
    'name': {
        'description': (
            'A display name for the OpenID Connect provider entry.'),
        'required': False,
    },
    'enable_oidc': {
        'description': (
            'Whether to enable this OpenID Connect provider. Defaults to '
            'True.'),
        'required': False,
    },
    'provider_index': {
        'description': (
            'Which iDRAC OpenIDConnectServer slot to program (1-based). '
            'Defaults to 1.'),
        'required': False,
    },
    'registration_timeout': {
        'description': (
            'Seconds to wait for iDRAC dynamic client registration to '
            'finish. Defaults to 600.'),
        'required': False,
    },
    'extra_attributes': {
        'description': (
            'Optional dict of raw Dell OEM attribute name/value pairs '
            'merged into the PATCH, for iDRAC firmware whose attribute '
            'names differ.'),
        'required': False,
    },
}


def _bool_to_idrac(value):
    """Map a Python boolean to the iDRAC 'Enabled'/'Disabled' string."""
    return 'Enabled' if value else 'Disabled'


def _validate_server_list(name, values, maximum, required):
    """Validate an ordered list of BMC network-service endpoints."""
    if not isinstance(values, list):
        raise exception.InvalidParameterValue(
            _('%(name)s must be a list of server addresses') % {'name': name})
    if len(values) > maximum:
        raise exception.InvalidParameterValue(
            _('%(name)s supports at most %(maximum)d server addresses') %
            {'name': name, 'maximum': maximum})
    if required and not values:
        raise exception.InvalidParameterValue(
            _('%(name)s must contain at least one server address') %
            {'name': name})
    if any(not isinstance(value, str) or not value.strip()
           for value in values):
        raise exception.InvalidParameterValue(
            _('%(name)s entries must be non-empty strings') % {'name': name})


def _merge_extra_attributes(attributes, extra_attributes):
    """Merge caller-supplied raw Dell OEM attributes over computed ones."""
    if extra_attributes is None:
        return
    if not isinstance(extra_attributes, dict):
        raise exception.InvalidParameterValue(
            _('extra_attributes must be a dictionary of attribute names to '
              'values'))
    attributes.update(extra_attributes)


def _parse_oidc_registration_status(value):
    """Decode iDRAC's JSON-encoded OIDC registration status attribute."""
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        status = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return status if isinstance(status, dict) else {}


def _check_oidc_registration_status(status, previous_sequence):
    """Classify an iDRAC OIDC registration status.

    :param status: the decoded RegistrationStatus attribute.
    :param previous_sequence: the status Sequence observed before the
        provider was configured, or None if there was none.
    :returns: a tuple of (result, error) where result is ``'success'``,
        ``'failed'`` or None while registration is still in progress, and
        error is a description of the failure.
    """
    # A status with the same sequence as before the PATCH predates this
    # configuration attempt.
    if (previous_sequence is not None
            and status.get('Sequence') == previous_sequence):
        return None, None

    request_status = str(status.get('Request Status') or '').lower()
    action = str(status.get('Action') or '').lower()
    if request_status == 'failed':
        error = (_('%(action)s failed: HTTP status %(status)s; %(error)s') %
                 {'action': action or 'registration',
                  'status': status.get('HTTP Status') or 'unknown',
                  'error': (status.get('HTTP Error')
                            or 'no error details returned')})
        return 'failed', error
    if (action == 'register' and request_status == 'success'
            and str(status.get('HTTP Status')) == '201'):
        return 'success', None
    return None, None


def _is_boot_order_flexibly_programmable(persistent, bios_settings):
    return persistent and 'SetBootOrderFqdd1' in bios_settings


def _flexibly_program_boot_order(device, drac_boot_mode):
    if device == boot_devices.DISK:
        if drac_boot_mode == 'Bios':
            bios_settings = {'SetBootOrderFqdd1': 'HardDisk.List.1-1'}
        else:
            # 'Uefi'
            bios_settings = {
                'SetBootOrderFqdd1': '*.*.*',  # Disks, which are all else
                'SetBootOrderFqdd2': 'NIC.*.*',
                'SetBootOrderFqdd3': 'Optical.*.*',
                'SetBootOrderFqdd4': 'Floppy.*.*',
            }
    elif device == boot_devices.PXE:
        bios_settings = {'SetBootOrderFqdd1': 'NIC.*.*'}
    else:
        # boot_devices.CDROM
        bios_settings = {'SetBootOrderFqdd1': 'Optical.*.*'}

    return bios_settings


def _validate_conf_mold(data):
    """Validates iDRAC configuration mold JSON schema

    :param data: dictionary of configuration mold data
    :raises InvalidParameterValue: If configuration mold validation fails
    """
    try:
        jsonschema.validate(data, _CONF_MOLD_SCHEMA)
    except json_schema_exc.ValidationError as e:
        raise exception.InvalidParameterValue(
            _("Invalid configuration mold: %(error)s") % {'error': e})


class DracRedfishManagement(redfish_management.RedfishManagement):
    """iDRAC Redfish interface for management-related actions."""

    EXPORT_CONFIGURATION_ARGSINFO = {
        "export_configuration_location": {
            "description": "URL of location to save the configuration to.",
            "required": True,
        }
    }

    IMPORT_CONFIGURATION_ARGSINFO = {
        "import_configuration_location": {
            "description": "URL of location to fetch desired configuration "
                           "from.",
            "required": True,
        }
    }

    IMPORT_EXPORT_CONFIGURATION_ARGSINFO = {**EXPORT_CONFIGURATION_ARGSINFO,
                                            **IMPORT_CONFIGURATION_ARGSINFO}

    @base.deploy_step(priority=0, argsinfo=EXPORT_CONFIGURATION_ARGSINFO)
    @base.clean_step(priority=0, argsinfo=EXPORT_CONFIGURATION_ARGSINFO,
                     requires_ramdisk=False)
    def export_configuration(self, task, export_configuration_location):
        """(Deprecated) Export the configuration of the server.

        Exports the configuration of the server against which the step is run
        and stores it in specific format in indicated location.

        Uses Dell's Server Configuration Profile (SCP) from `sushy` oem
        extension to get ALL configuration for cloning.

        :param task: A task from TaskManager.
        :param export_configuration_location: URL of location to save the
            configuration to.

        :raises: MissingParameterValue if missing configuration name of a file
            to save the configuration to
        :raises: DracOperatationError when no managagers for Redfish system
            found or configuration export from SCP failed
        :raises: RedfishError when loading OEM extension failed
        """
        if not export_configuration_location:
            raise exception.MissingParameterValue(
                _('export_configuration_location missing'))

        system = redfish_utils.get_system(task.node)
        configuration = None

        if not system.managers:
            raise exception.DracOperationError(
                error=(_("No managers found for %(node)s") %
                       {'node': task.node.uuid}))

        configuration = drac_utils.execute_oem_manager_method(
            task, 'export system configuration',
            lambda m: m.export_system_configuration(
                include_destructive_fields=False))

        if configuration and configuration.status_code == 200:
            configuration = {"oem": {"interface": "idrac-redfish",
                                     "data": configuration.json()}}
            molds.save_configuration(task,
                                     export_configuration_location,
                                     configuration)
        else:
            raise exception.DracOperationError(
                error=(_("No configuration exported for node %(node)s") %
                       {'node': task.node.uuid}))

    @base.deploy_step(priority=0, argsinfo=IMPORT_CONFIGURATION_ARGSINFO)
    @base.clean_step(priority=0, argsinfo=IMPORT_CONFIGURATION_ARGSINFO,
                     requires_ramdisk=False)
    def import_configuration(self, task, import_configuration_location):
        """(Deprecated) Import and apply the configuration to the server.

        Gets pre-created configuration from storage by given location and
        imports that into given server. Uses Dell's Server Configuration
        Profile (SCP).

        :param task: A task from TaskManager.
        :param import_configuration_location: URL of location to fetch desired
            configuration from.

        :raises: MissingParameterValue if missing configuration name of a file
            to fetch the configuration from
        """
        if not import_configuration_location:
            raise exception.MissingParameterValue(
                _('import_configuration_location missing'))

        configuration = molds.get_configuration(task,
                                                import_configuration_location)
        if not configuration:
            raise exception.DracOperationError(
                error=(_("No configuration found for node %(node)s by name "
                         "%(configuration_name)s") %
                       {'node': task.node.uuid,
                        'configuration_name': import_configuration_location}))

        _validate_conf_mold(configuration)

        task_monitor = drac_utils.execute_oem_manager_method(
            task, 'import system configuration',
            lambda m: m.import_system_configuration(
                json.dumps(configuration["oem"]["data"])),)

        task.node.set_driver_internal_info('import_task_monitor_url',
                                           task_monitor.task_monitor_uri)

        deploy_utils.set_async_step_flags(
            task.node,
            reboot=True,
            skip_current_step=True,
            polling=True)
        return deploy_utils.reboot_to_finish_step(task)

    @base.clean_step(priority=0,
                     argsinfo=IMPORT_EXPORT_CONFIGURATION_ARGSINFO,
                     requires_ramdisk=False)
    @base.deploy_step(priority=0,
                      argsinfo=IMPORT_EXPORT_CONFIGURATION_ARGSINFO)
    def import_export_configuration(self, task, import_configuration_location,
                                    export_configuration_location):
        """Import and export configuration in one go.

        Gets pre-created configuration from storage by given name and
        imports that into given server. After that exports the configuration of
        the server against which the step is run and stores it in specific
        format in indicated storage as configured by Ironic.

        :param import_configuration_location: URL of location to fetch desired
            configuration from.
        :param export_configuration_location: URL of location to save the
            configuration to.
        """
        # Import is async operation, setting sub-step to store export config
        # and indicate that it's being executed as part of composite step
        task.node.set_driver_internal_info('export_configuration_location',
                                           export_configuration_location)
        task.node.save()

        return self.import_configuration(task, import_configuration_location)
        # Export executed as part of Import async periodic task status check

    @METRICS.timer('DracRedfishManagement._query_import_configuration_status')
    @periodics.node_periodic(
        purpose='checking async import configuration task',
        spacing=CONF.drac.query_import_config_job_status_interval,
        filters={'reserved': False, 'maintenance': False},
        predicate_extra_fields=['driver_internal_info'],
        predicate=lambda n: (
            n.driver_internal_info.get('import_task_monitor_url')
        ),
    )
    def _query_import_configuration_status(self, task, manager, context):
        """Period job to check import configuration task."""
        self._check_import_configuration_task(
            task, task.node.driver_internal_info.get(
                'import_task_monitor_url'))

    def _check_import_configuration_task(self, task, task_monitor_url):
        """Checks progress of running import configuration task"""

        node = task.node
        try:
            task_monitor = redfish_utils.get_task_monitor(
                node, task_monitor_url)
        except exception.RedfishError as e:
            error_msg = (_("Failed import configuration task: "
                           "%(task_monitor_url)s. Message: '%(message)s'. "
                           "Most likely this happened because could not find "
                           "the task anymore as it got deleted by iDRAC. "
                           "If not already, upgrade iDRAC firmware to "
                           "5.00.00.00 or later that preserves tasks for "
                           "longer or decrease "
                           "[drac]query_import_config_job_status_interval")
                         % {'task_monitor_url': task_monitor_url,
                            'message': e})
            log_msg = ("Import configuration task failed for node "
                       "%(node)s. %(error)s" % {'node': task.node.uuid,
                                                'error': error_msg})
            node.del_driver_internal_info('import_task_monitor_url')
            node.save()
            self._set_failed(task, log_msg, error_msg)
            return

        if not task_monitor.is_processing:
            import_task = task_monitor.get_task()

            task.upgrade_lock()
            node.del_driver_internal_info('import_task_monitor_url')

            succeeded = False
            if (import_task.task_state == sushy.TASK_STATE_COMPLETED
                and import_task.task_status in
                    [sushy.HEALTH_OK, sushy.HEALTH_WARNING]):

                # Task could complete with errors (partial success)
                # iDRAC 5.00.00.00 has stopped reporting Critical messages
                # so checking also by message_id
                succeeded = not any(m.message for m in import_task.messages
                                    if (m.severity
                                        and m.severity != sushy.SEVERITY_OK)
                                    or (m.message_id and 'SYS055'
                                        in m.message_id))

            if succeeded:
                LOG.info('Configuration import %(task_monitor_url)s '
                         'successful for node %(node)s',
                         {'node': node.uuid,
                          'task_monitor_url': task_monitor_url})

                # If import executed as part of import_export_configuration
                export_configuration_location = node.driver_internal_info.get(
                    'export_configuration_location')
                if export_configuration_location:
                    # then do sync export configuration before finishing
                    self._cleanup_export_substep(node)
                    try:
                        self.export_configuration(
                            task, export_configuration_location)
                    except (sushy.exceptions.SushyError,
                            exception.IronicException) as e:
                        error_msg = (_("Failed export configuration. %(exc)s" %
                                       {'exc': e}))
                        log_msg = ("Export configuration failed for node "
                                   "%(node)s. %(error)s" %
                                   {'node': task.node.uuid,
                                    'error': error_msg})
                        self._set_failed(task, log_msg, error_msg)
                        return
                self._set_success(task)
            else:
                # Select all messages, skipping OEM messages that don't have
                # `message` field populated.
                messages = [m.message for m in import_task.messages
                            if m.message is not None
                            and ((m.severity
                                  and m.severity != sushy.SEVERITY_OK)
                                 or (m.message_id
                                     and 'SYS055' in m.message_id))]
                error_msg = (_("Failed import configuration task: "
                               "%(task_monitor_url)s. Message: '%(message)s'.")
                             % {'task_monitor_url': task_monitor_url,
                                'message': ', '.join(messages)})
                log_msg = ("Import configuration task failed for node "
                           "%(node)s. %(error)s" % {'node': task.node.uuid,
                                                    'error': error_msg})
                self._set_failed(task, log_msg, error_msg)
            node.save()
        else:
            LOG.debug('Import configuration %(task_monitor_url)s in progress '
                      'for node %(node)s',
                      {'node': node.uuid,
                       'task_monitor_url': task_monitor_url})

    def _set_success(self, task):
        if task.node.clean_step:
            manager_utils.notify_conductor_resume_clean(task)
        elif task.node.service_step:
            manager_utils.notify_conductor_resume_service(task)
        else:
            manager_utils.notify_conductor_resume_deploy(task)

    def _set_failed(self, task, log_msg, error_msg):
        if task.node.clean_step:
            manager_utils.cleaning_error_handler(task, log_msg, error_msg)
        elif task.node.service_step:
            manager_utils.servicing_error_handler(task, log_msg, error_msg)
        else:
            manager_utils.deploying_error_handler(task, log_msg, error_msg)

    def _cleanup_export_substep(self, node):
        node.del_driver_internal_info('export_configuration_location')

    @METRICS.timer('DracRedfishManagement.clear_job_queue')
    @base.verify_step(priority=0)
    @base.clean_step(priority=0, requires_ramdisk=False)
    def clear_job_queue(self, task):
        """Clear iDRAC job queue.

        :param task: a TaskManager instance containing the node to act
                     on.
        :raises: RedfishError on an error.
        """
        try:
            drac_utils.execute_oem_manager_method(
                task, 'clear job queue',
                lambda m: m.job_service.delete_jobs(job_ids=['JID_CLEARALL']))
        except exception.RedfishError as exc:
            if "Oem/Dell/DellJobService is missing" in str(exc):
                LOG.warning('iDRAC on node %(node)s does not support '
                            'clearing Lifecycle Controller job queue '
                            'using the idrac-redfish driver. '
                            'If using iDRAC9, consider upgrading firmware.',
                            {'node': task.node.uuid})
            if task.node.provision_state != states.VERIFYING:
                raise

    @METRICS.timer('DracRedfishManagement.reset_idrac')
    @base.verify_step(priority=0)
    @base.clean_step(priority=0, requires_ramdisk=False)
    def reset_idrac(self, task):
        """Reset the iDRAC.

        :param task: a TaskManager instance containing the node to act
                     on.
        :raises: RedfishError on an error.
        """
        try:
            drac_utils.execute_oem_manager_method(
                task, 'reset iDRAC', lambda m: m.reset_idrac())
            redfish_utils.wait_until_get_system_ready(task.node)
            LOG.info('Reset iDRAC for node %(node)s done',
                     {'node': task.node.uuid})
        except exception.RedfishError as exc:
            if "Oem/Dell/DelliDRACCardService is missing" in str(exc):
                LOG.warning('iDRAC on node %(node)s does not support '
                            'iDRAC reset using the idrac-redfish driver. '
                            'If using iDRAC9, consider upgrading firmware. ',
                            {'node': task.node.uuid})
            if task.node.provision_state != states.VERIFYING:
                raise

    @METRICS.timer('DracRedfishManagement.known_good_state')
    @base.verify_step(priority=0)
    @base.clean_step(priority=0, requires_ramdisk=False)
    def known_good_state(self, task):
        """Reset iDRAC to known good state.

        An iDRAC is reset to a known good state by resetting it and
        clearing its job queue.

        :param task: a TaskManager instance containing the node to act
                     on.
        :raises: RedfishError on an error.
        """
        self.reset_idrac(task)
        self.clear_job_queue(task)
        LOG.info('Reset iDRAC to known good state for node %(node)s',
                 {'node': task.node.uuid})

    @METRICS.timer('DracRedfishManagement.set_ntp_servers')
    @base.clean_step(priority=0, argsinfo=_SET_NTP_ARGSINFO,
                     requires_ramdisk=False)
    @base.service_step(priority=0, abortable=False,
                       argsinfo=_SET_NTP_ARGSINFO, requires_ramdisk=False)
    def set_ntp_servers(self, task, ntp_servers, enable_ntp=True,
                        timezone=None, extra_attributes=None):
        """Program the iDRAC NTP server settings.

        This is an out-of-band service step, invocable from a runbook, that
        PATCHes the Dell OEM iDRAC attributes directly. No database changes
        are made and the settings apply immediately on the iDRAC.

        :param task: a TaskManager instance containing the node to act on.
        :param ntp_servers: a list of NTP server addresses (up to three are
            used by the iDRAC).
        :param enable_ntp: whether to enable NTP synchronisation. Default
            True.
        :param timezone: optional iDRAC timezone string.
        :param extra_attributes: optional dict of raw Dell OEM attributes
            merged into the PATCH.
        :raises: InvalidParameterValue if ntp_servers is invalid.
        :raises: RedfishError on an error talking to the BMC.
        """
        _validate_server_list('ntp_servers', ntp_servers,
                              _MAX_NTP_SERVERS, enable_ntp)

        attributes = {'NTPConfigGroup.1.NTPEnable': _bool_to_idrac(enable_ntp)}
        for index in range(_MAX_NTP_SERVERS):
            value = ntp_servers[index] if index < len(ntp_servers) else ''
            attributes['NTPConfigGroup.1.NTP%d' % (index + 1)] = value

        if timezone:
            attributes['Time.1.Timezone'] = timezone

        _merge_extra_attributes(attributes, extra_attributes)

        drac_utils.set_dell_attributes(task, attributes, target='iDRAC')
        LOG.info('Set NTP servers for node %(node)s', {'node': task.node.uuid})

    @METRICS.timer('DracRedfishManagement.set_dns_servers')
    @base.clean_step(priority=0, argsinfo=_SET_DNS_ARGSINFO,
                     requires_ramdisk=False)
    @base.service_step(priority=0, abortable=False,
                       argsinfo=_SET_DNS_ARGSINFO, requires_ramdisk=False)
    def set_dns_servers(self, task, dns_servers, dns_domain_name=None,
                        extra_attributes=None):
        """Program the iDRAC DNS server settings.

        This is an out-of-band service step, invocable from a runbook, that
        PATCHes the Dell OEM iDRAC attributes directly. It configures the
        static IPv4 DNS servers and always disables learning the DNS servers
        and domain name from DHCP so the static values take effect.

        :param task: a TaskManager instance containing the node to act on.
        :param dns_servers: a list of DNS server addresses (up to two static
            IPv4 servers are used by the iDRAC).
        :param dns_domain_name: optional DNS domain name to set.
        :param extra_attributes: optional dict of raw Dell OEM attributes
            merged into the PATCH.
        :raises: InvalidParameterValue if dns_servers is invalid.
        :raises: RedfishError on an error talking to the BMC.
        """
        _validate_server_list('dns_servers', dns_servers,
                              _MAX_DNS_SERVERS, True)

        # Static DNS settings only take effect when the iDRAC is not told to
        # learn them from DHCP. iDRAC exposes duplicate DHCP flags for both
        # the servers and the domain name, so disable all of them.
        attributes = {
            'IPv4.1.DNSFromDHCP': 'Disabled',
            'IPv4Static.1.DNSFromDHCP': 'Disabled',
            'NIC.1.DNSDomainFromDHCP': 'Disabled',
            'NIC.1.DNSDomainNameFromDHCP': 'Disabled',
        }
        for index in range(_MAX_DNS_SERVERS):
            value = dns_servers[index] if index < len(dns_servers) else ''
            attributes['IPv4Static.1.DNS%d' % (index + 1)] = value

        if dns_domain_name is not None:
            attributes['NIC.1.DNSDomainName'] = dns_domain_name

        _merge_extra_attributes(attributes, extra_attributes)

        drac_utils.set_dell_attributes(task, attributes, target='iDRAC')
        LOG.info('Set DNS servers for node %(node)s', {'node': task.node.uuid})

    # TODO(cardoe): The initial_access_token step argument is not redacted
    # in the following places. Each must be fixed before this step lands:
    #
    # * node.clean_step / node.service_step: stored in the database and
    #   returned unmasked by the node API while the step runs.
    # * node.driver_internal_info clean_steps / service_steps: stored as
    #   plaintext in the database (the node API does mask them).
    # * node.last_error and node history: when the step raises (e.g. an
    #   invalid argument or a rejected PATCH), the conductor embeds the
    #   full step dict, args included, in the error message
    #   (do_next_clean_step in conductor/cleaning.py,
    #   do_next_service_step in conductor/servicing.py), which
    #   cleaning_error_handler / servicing_error_handler in
    #   conductor/utils.py store and log at ERROR.
    # * Conductor logs: the "remaining steps", "Executing <step>" and
    #   "finished ... step <step>" INFO messages in conductor/cleaning.py
    #   and conductor/servicing.py, and the step list DEBUG messages in
    #   conductor/steps.py.
    # * Notifications: the node notification payload carries clean_step
    #   and last_error (NodePayload in objects/node.py).
    # * Runbooks: stored as plaintext in the database (the runbook API does
    #   mask args).
    # * BMC errors: if the iDRAC rejects the PATCH, the error raised by
    #   drac_utils.set_dell_attributes includes sushy's message, which may
    #   echo the rejected RegistrationDetails value.
    @METRICS.timer('DracRedfishManagement.set_oidc_config')
    @base.clean_step(priority=0, argsinfo=_SET_OIDC_ARGSINFO,
                     requires_ramdisk=False)
    @base.service_step(priority=0, abortable=False,
                       argsinfo=_SET_OIDC_ARGSINFO, requires_ramdisk=False)
    def set_oidc_config(self, task, discovery_url=None,
                        initial_access_token=None, https_certificate=None,
                        name='SSO', enable_oidc=True, provider_index=1,
                        registration_timeout=600, extra_attributes=None):
        """Program the iDRAC OpenID Connect (SSO) settings.

        This is an out-of-band service step, invocable from a runbook, that
        PATCHes the Dell OEM iDRAC attributes directly to configure an
        OpenID Connect provider for single sign-on. When enabling a provider,
        iDRAC uses the initial access token to dynamically register an RFC
        7591 client. Registration takes minutes, so the step is asynchronous:
        it returns a wait state and a periodic task completes it once iDRAC
        reports the outcome. This step does not log attribute values, but
        the token is part of the step arguments and so is recorded wherever
        Ironic records those (see the TODO above).

        :param task: a TaskManager instance containing the node to act on.
        :param discovery_url: the provider discovery URL.
        :param initial_access_token: initial access token for dynamic client
            registration.
        :param https_certificate: PEM-encoded CA certificate used to validate
            the provider.
        :param name: a display name for the provider entry.
        :param enable_oidc: whether to enable the provider. Default True.
        :param provider_index: which OpenIDConnectServer slot to program
            (1-based). Default 1.
        :param registration_timeout: seconds to wait for dynamic client
            registration to complete. Default 600.
        :param extra_attributes: optional dict of raw Dell OEM attributes
            merged into the PATCH.
        :raises: InvalidParameterValue if an argument is invalid.
        :raises: RedfishError on an error talking to the BMC.
        :returns: states.CLEANWAIT or states.SERVICEWAIT while registration
            is pending when enabling a provider, otherwise None.
        """
        if (not isinstance(provider_index, int)
                or isinstance(provider_index, bool)
                or not 1 <= provider_index <= _MAX_OIDC_PROVIDERS):
            raise exception.InvalidParameterValue(
                _('provider_index must be an integer from 1 to %(maximum)d') %
                {'maximum': _MAX_OIDC_PROVIDERS})
        if (not isinstance(registration_timeout, (int, float))
                or isinstance(registration_timeout, bool)
                or registration_timeout <= 0):
            raise exception.InvalidParameterValue(
                _('registration_timeout must be a positive number'))
        if enable_oidc:
            if (not isinstance(discovery_url, str)
                    or not discovery_url.startswith('https://')):
                raise exception.InvalidParameterValue(
                    _('discovery_url must be an HTTPS URL'))
            if (not isinstance(initial_access_token, str)
                    or not initial_access_token.strip()):
                raise exception.InvalidParameterValue(
                    _('initial_access_token must be a non-empty string'))
            if (not isinstance(https_certificate, str)
                    or '-----BEGIN CERTIFICATE-----' not in https_certificate
                    or '-----END CERTIFICATE-----' not in https_certificate):
                raise exception.InvalidParameterValue(
                    _('https_certificate must contain a PEM certificate'))

        prefix = 'OpenIDConnectServer.%d.' % provider_index
        status_attribute = prefix + 'RegistrationStatus'
        attributes = {prefix + 'Enabled': '1' if enable_oidc else '0'}
        if enable_oidc:
            attributes.update({
                prefix + 'Name': name,
                prefix + 'DiscoveryURL': discovery_url,
                prefix + 'RegistrationDetails':
                    'bearer ' + initial_access_token,
                prefix + 'HttpsCertificate': https_certificate,
            })
        _merge_extra_attributes(attributes, extra_attributes)

        previous_status = {}
        if enable_oidc:
            current = drac_utils.get_dell_attributes(task, target='System')
            previous_status = _parse_oidc_registration_status(
                current.get(status_attribute))

        drac_utils.set_dell_attributes(task, attributes, target='System')

        if not enable_oidc:
            LOG.info('Disabled OIDC provider %(index)d for node %(node)s',
                     {'index': provider_index, 'node': task.node.uuid})
            return

        node = task.node
        node.set_driver_internal_info(_OIDC_REGISTRATION_INFO, {
            'provider_index': provider_index,
            'previous_sequence': previous_status.get('Sequence'),
            'deadline': time.time() + registration_timeout,
        })
        deploy_utils.set_async_step_flags(node, skip_current_step=True,
                                          polling=True)
        LOG.info('Configured OIDC provider %(index)d for node %(node)s; '
                 'waiting for iDRAC to register with the provider',
                 {'index': provider_index, 'node': node.uuid})
        return deploy_utils.get_async_step_return_state(node)

    @METRICS.timer('DracRedfishManagement._query_oidc_registration_status')
    @periodics.node_periodic(
        purpose='checking iDRAC OIDC dynamic client registration',
        spacing=CONF.drac.query_oidc_registration_status_interval,
        filters={'reserved': False, 'maintenance': False,
                 'provision_state_in': {states.CLEANWAIT,
                                        states.SERVICEWAIT}},
        predicate_extra_fields=['driver_internal_info'],
        predicate=lambda n: (
            n.driver_internal_info.get(_OIDC_REGISTRATION_INFO)
        ),
    )
    def _query_oidc_registration_status(self, task, manager, context):
        """Periodic job to check iDRAC OIDC registration."""
        self._check_oidc_registration(task)

    def _check_oidc_registration(self, task):
        """Check the progress of an iDRAC OIDC dynamic registration."""
        node = task.node
        info = node.driver_internal_info.get(_OIDC_REGISTRATION_INFO)
        current_step = node.clean_step or node.service_step or {}
        if current_step.get('step') != 'set_oidc_config':
            # Left over from an earlier operation; nothing is waiting on it.
            task.upgrade_lock()
            task.node.del_driver_internal_info(_OIDC_REGISTRATION_INFO)
            task.node.save()
            return

        provider_index = info['provider_index']
        status_attribute = ('OpenIDConnectServer.%d.RegistrationStatus'
                            % provider_index)
        timed_out = time.time() >= info['deadline']
        try:
            current = drac_utils.get_dell_attributes(task, target='System')
        except exception.RedfishError as e:
            if not timed_out:
                LOG.warning('Unable to read OIDC registration status for '
                            'node %(node)s, will retry: %(error)s',
                            {'node': node.uuid, 'error': e})
                return
            result, error = 'failed', str(e)
        else:
            status = _parse_oidc_registration_status(
                current.get(status_attribute))
            LOG.debug('iDRAC OIDC registration status for node %(node)s '
                      'provider %(index)d: %(status)s',
                      {'node': node.uuid, 'index': provider_index,
                       'status': status})
            result, error = _check_oidc_registration_status(
                status, info.get('previous_sequence'))
            if result is None:
                if not timed_out:
                    return
                result = 'failed'
                error = _('timed out waiting for registration to complete')

        # upgrade_lock() reloads task.node, so use the new object from here.
        task.upgrade_lock()
        node = task.node
        node.del_driver_internal_info(_OIDC_REGISTRATION_INFO)
        node.save()
        if result == 'success':
            LOG.info('iDRAC registered OIDC provider %(index)d for node '
                     '%(node)s', {'index': provider_index, 'node': node.uuid})
            self._set_success(task)
        else:
            error_msg = (_('iDRAC OIDC provider %(index)d registration '
                           'failed for node %(node)s: %(error)s') %
                         {'index': provider_index, 'node': node.uuid,
                          'error': error})
            self._set_failed(task, error_msg, error_msg)
