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

from oslo_config import cfg

from ironic.common.i18n import _

opts = [
    cfg.IntOpt('query_raid_config_job_status_interval',
               default=120,
               min=1,
               help=_('Interval (in seconds) between periodic RAID job status '
                      'checks to determine whether the asynchronous RAID '
                      'configuration was successfully finished or not.')),
    cfg.IntOpt('boot_device_job_status_timeout',
               default=30,
               min=1,
               help=_('Maximum amount of time (in seconds) to wait for '
                      'the boot device configuration job to transition '
                      'to the correct state to allow a reboot or power '
                      'on to complete.')),
    cfg.IntOpt('config_job_max_retries',
               default=240,
               min=1,
               deprecated_for_removal=True,
               deprecated_reason=_('This option has has no effect since '
                                   '26.0.0'),
               help=_('Maximum number of retries for '
                      'the configuration job to complete '
                      'successfully.')),
    cfg.IntOpt('query_import_config_job_status_interval',
               min=0,
               default=60,
               help=_('Number of seconds to wait between checking for '
                      'completed import configuration task')),
    cfg.IntOpt('query_oidc_registration_status_interval',
               min=1,
               default=15,
               help=_('Number of seconds to wait between checking whether '
                      'an iDRAC has completed OpenID Connect dynamic client '
                      'registration')),
    cfg.StrOpt('oidc_discovery_url',
               help=_('Default OpenID Connect provider discovery URL used by '
                      'the idrac-redfish set_oidc_config clean and service '
                      'step when discovery_url is omitted.')),
    cfg.StrOpt('oidc_initial_access_token',
               secret=True,
               help=_('Default RFC 7591 initial access token used by the '
                      'idrac-redfish set_oidc_config clean and service step '
                      'when initial_access_token is omitted.')),
    cfg.StrOpt('oidc_https_certificate_file',
               help=_('Path to a file containing the default PEM-encoded CA '
                      'certificate used by the idrac-redfish '
                      'set_oidc_config clean and service step when '
                      'https_certificate is omitted. The file is read each '
                      'time the step runs, so it can be changed without '
                      'restarting the conductor. If unset, no certificate '
                      'is sent to the iDRAC.')),
    cfg.StrOpt('snmpv3_username',
               help=_('Default iDRAC local username for the SNMPv3 account '
                      'created by the idrac-redfish configure_snmpv3_user '
                      'clean and service step when username is omitted. '
                      'If unset and username is omitted, the step derives a '
                      'name of the form "<prefix>-<lowercase service tag>" '
                      '(see snmpv3_username_prefix) using the Dell service '
                      'tag read from the inspection inventory serial '
                      'number.')),
    cfg.StrOpt('snmpv3_username_prefix',
               default='uc',
               help=_('Username prefix for the SNMPv3 account derived by the '
                      'idrac-redfish configure_snmpv3_user step when neither '
                      'a username argument nor snmpv3_username is set. The '
                      'derived username is "<prefix>-<lowercase service '
                      'tag>". Must be 1 to 16 lowercase ASCII letters or '
                      'digits. The prefix affects only the username, not the '
                      'derived SNMPv3 passphrases.')),
    cfg.StrOpt('snmpv3_master_key',
               secret=True,
               help=_('Dedicated SNMPv3 master secret of at least '
                      '32 characters. '
                      'The configure_snmpv3_user step derives unique '
                      'authentication and privacy passphrases from this key '
                      'and the Dell service tag. Do not reuse the BMC root '
                      'password master. Required; keep stable across restarts '
                      'and coordinate rotation with all BMCs and consumers.')),
    cfg.IntOpt('bios_factory_reset_timeout',
               default=600,
               min=1,
               deprecated_for_removal=True,
               deprecated_reason=_('This option has has no effect since '
                                   '26.0.0'),
               help=_('Maximum time (in seconds) to wait for factory reset of '
                      'BIOS settings to complete.')),
    cfg.IntOpt('raid_job_timeout',
               default=300,
               min=1,
               help=_('Maximum time (in seconds) to wait for RAID job to '
                      'complete'))

]


def register_opts(conf):
    conf.register_opts(opts, group='drac')
