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
Test class for DRAC management interface
"""

import json
import os
import tempfile
from unittest import mock

import ironic.common.boot_devices
from ironic.common import exception
from ironic.common import states
from ironic.conductor import task_manager
from ironic.conductor import utils as manager_utils
from ironic.drivers.modules.drac import management as drac_mgmt
from ironic.drivers.modules.drac import utils as drac_utils
from ironic.drivers.modules.redfish import utils as redfish_utils
from ironic.tests.unit.drivers.modules.drac import utils as test_utils
from ironic.tests.unit.objects import utils as obj_utils


INFO_DICT = test_utils.INFO_DICT


_OIDC_CERTIFICATE = ('-----BEGIN CERTIFICATE-----\n'
                     'certificate-data\n'
                     '-----END CERTIFICATE-----')


class DracRedfishManagementTestCase(test_utils.BaseDracTest):

    def setUp(self):
        super(DracRedfishManagementTestCase, self).setUp()
        self.node = obj_utils.create_test_node(self.context,
                                               driver='idrac',
                                               driver_info=INFO_DICT)
        self.management = drac_mgmt.DracRedfishManagement()

        self.config(enabled_hardware_types=['idrac'],
                    enabled_power_interfaces=['idrac-redfish'],
                    enabled_management_interfaces=['idrac-redfish'])

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.certificate_dir = tmp.name

    def _write_certificate(self, contents):
        path = os.path.join(self.certificate_dir, 'oidc-ca.pem')
        with open(path, 'w') as cert_file:
            cert_file.write(contents)
        return path

    @mock.patch.object(drac_utils, 'redfish_utils', autospec=True)
    def test_clear_job_queue(self, mock_redfish_utils):
        mock_system = mock_redfish_utils.get_system.return_value
        mock_manager = mock.MagicMock()
        mock_system.managers = [mock_manager]
        mock_manager_oem = mock_manager.get_oem_extension.return_value

        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            task.driver.management.clear_job_queue(task)
            mock_manager_oem.job_service.delete_jobs.assert_called_once_with(
                job_ids=['JID_CLEARALL'])

    @mock.patch.object(drac_mgmt, 'LOG', autospec=True)
    @mock.patch.object(drac_utils, 'redfish_utils', autospec=True)
    def test_clear_job_queue_missing_attr_verify_step(self,
                                                      mock_redfish_utils,
                                                      mock_log):
        mock_system = mock_redfish_utils.get_system.return_value
        mock_manager = mock.MagicMock()
        mock_system.managers = [mock_manager]
        mock_manager_oem = mock_manager.get_oem_extension.return_value
        mock_manager_oem.job_service.delete_jobs.side_effect = (
            exception.RedfishError("Oem/Dell/DellJobService is missing"))
        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            task.node.provision_state = states.VERIFYING
            task.driver.management.clear_job_queue(task)
            mock_log.warning.assert_called_once_with(
                'iDRAC on node %(node)s does not support '
                'clearing Lifecycle Controller job queue '
                'using the idrac-redfish driver. '
                'If using iDRAC9, consider upgrading firmware.',
                {'node': task.node.uuid})

    @mock.patch.object(drac_mgmt, 'LOG', autospec=True)
    @mock.patch.object(drac_utils, 'redfish_utils', autospec=True)
    def test_clear_job_queue_missing_attr_clean_step(self,
                                                     mock_redfish_utils,
                                                     mock_log):
        mock_system = mock_redfish_utils.get_system.return_value
        mock_manager = mock.MagicMock()
        mock_system.managers = [mock_manager]
        mock_manager_oem = mock_manager.get_oem_extension.return_value
        mock_manager_oem.job_service.delete_jobs.side_effect = (
            exception.RedfishError("Oem/Dell/DellJobService is missing"))
        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            task.node.provision_state = states.CLEANING
            self.assertRaises(ironic.common.exception.RedfishError,
                              task.driver.management.clear_job_queue, task)

    @mock.patch.object(redfish_utils, 'wait_until_get_system_ready',
                       autospec=True)
    @mock.patch.object(drac_utils, 'redfish_utils', autospec=True)
    def test_reset_idrac(self, mock_redfish_utils, mock_wait_system_ready):
        mock_system = mock_redfish_utils.get_system.return_value
        mock_manager = mock.MagicMock()
        mock_system.managers = [mock_manager]
        mock_manager_oem = mock_manager.get_oem_extension.return_value

        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            task.driver.management.reset_idrac(task)
            mock_manager_oem.reset_idrac.assert_called_once_with()

    @mock.patch.object(redfish_utils, 'wait_until_get_system_ready',
                       autospec=True)
    @mock.patch.object(drac_mgmt, 'LOG', autospec=True)
    @mock.patch.object(drac_utils, 'redfish_utils', autospec=True)
    def test_reset_idrac_missing_attr_verify_step(self,
                                                  mock_redfish_utils,
                                                  mock_log,
                                                  mock_wait_system_ready):
        mock_system = mock_redfish_utils.get_system.return_value
        mock_manager = mock.MagicMock()
        mock_system.managers = [mock_manager]
        mock_manager_oem = mock_manager.get_oem_extension.return_value
        mock_manager_oem.reset_idrac.side_effect = (
            exception.RedfishError("Oem/Dell/DelliDRACCardService is missing"))
        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            task.node.provision_state = states.VERIFYING
            task.driver.management.reset_idrac(task)
            mock_log.warning.assert_called_once_with(
                'iDRAC on node %(node)s does not support '
                'iDRAC reset using the idrac-redfish driver. '
                'If using iDRAC9, consider upgrading firmware. ',
                {'node': task.node.uuid})

    @mock.patch.object(redfish_utils, 'wait_until_get_system_ready',
                       autospec=True)
    @mock.patch.object(drac_mgmt, 'LOG', autospec=True)
    @mock.patch.object(drac_utils, 'redfish_utils', autospec=True)
    def test_reset_idrac_missing_attr_clean_step(self,
                                                 mock_redfish_utils,
                                                 mock_log,
                                                 mock_wait_system_ready):
        mock_system = mock_redfish_utils.get_system.return_value
        mock_manager = mock.MagicMock()
        mock_system.managers = [mock_manager]
        mock_manager_oem = mock_manager.get_oem_extension.return_value
        mock_manager_oem.reset_idrac.side_effect = (
            exception.RedfishError("Oem/Dell/DelliDRACCardService is missing"))
        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            task.node.provision_state = states.CLEANING
            self.assertRaises(ironic.common.exception.RedfishError,
                              task.driver.management.reset_idrac, task)

    @mock.patch.object(redfish_utils, 'wait_until_get_system_ready',
                       autospec=True)
    @mock.patch.object(drac_utils, 'redfish_utils', autospec=True)
    def test_known_good_state(self, mock_redfish_utils,
                              mock_wait_system_ready):
        mock_system = mock_redfish_utils.get_system.return_value
        mock_manager = mock.MagicMock()
        mock_system.managers = [mock_manager]
        mock_manager_oem = mock_manager.get_oem_extension.return_value

        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            task.driver.management.known_good_state(task)
            mock_manager_oem.job_service.delete_jobs.assert_called_once_with(
                job_ids=['JID_CLEARALL'])
            mock_manager_oem.reset_idrac.assert_called_once_with()

    @mock.patch.object(drac_mgmt.drac_utils, 'set_dell_attributes',
                       autospec=True)
    def test_set_ntp_servers(self, mock_set_attrs):
        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            task.driver.management.set_ntp_servers(
                task, ntp_servers=['10.0.0.1', '10.0.0.2'],
                timezone='US/Central')
        mock_set_attrs.assert_called_once_with(mock.ANY, {
            'NTPConfigGroup.1.NTPEnable': 'Enabled',
            'NTPConfigGroup.1.NTP1': '10.0.0.1',
            'NTPConfigGroup.1.NTP2': '10.0.0.2',
            'NTPConfigGroup.1.NTP3': '',
            'Time.1.Timezone': 'US/Central',
        }, target='iDRAC')

    @mock.patch.object(drac_mgmt.drac_utils, 'set_dell_attributes',
                       autospec=True)
    def test_set_ntp_servers_disable_and_extra(self, mock_set_attrs):
        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            task.driver.management.set_ntp_servers(
                task, ntp_servers=[], enable_ntp=False,
                extra_attributes={'NTPConfigGroup.1.NTPMaxDist': '16'})
        mock_set_attrs.assert_called_once_with(mock.ANY, {
            'NTPConfigGroup.1.NTPEnable': 'Disabled',
            'NTPConfigGroup.1.NTP1': '',
            'NTPConfigGroup.1.NTP2': '',
            'NTPConfigGroup.1.NTP3': '',
            'NTPConfigGroup.1.NTPMaxDist': '16',
        }, target='iDRAC')

    @mock.patch.object(drac_mgmt.drac_utils, 'set_dell_attributes',
                       autospec=True)
    def test_set_ntp_servers_invalid(self, mock_set_attrs):
        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            self.assertRaises(
                exception.InvalidParameterValue,
                task.driver.management.set_ntp_servers,
                task, ntp_servers='10.0.0.1')
        self.assertFalse(mock_set_attrs.called)

    @mock.patch.object(drac_mgmt.drac_utils, 'set_dell_attributes',
                       autospec=True)
    def test_set_ntp_servers_too_many(self, mock_set_attrs):
        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            self.assertRaises(
                exception.InvalidParameterValue,
                task.driver.management.set_ntp_servers,
                task, ntp_servers=['1', '2', '3', '4'])
        self.assertFalse(mock_set_attrs.called)

    @mock.patch.object(drac_mgmt.drac_utils, 'set_dell_attributes',
                       autospec=True)
    def test_set_dns_servers(self, mock_set_attrs):
        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            task.driver.management.set_dns_servers(
                task, dns_servers=['10.0.0.53'],
                dns_domain_name='example.com')
        mock_set_attrs.assert_called_once_with(mock.ANY, {
            'IPv4.1.DNSFromDHCP': 'Disabled',
            'IPv4Static.1.DNSFromDHCP': 'Disabled',
            'NIC.1.DNSDomainFromDHCP': 'Disabled',
            'NIC.1.DNSDomainNameFromDHCP': 'Disabled',
            'IPv4Static.1.DNS1': '10.0.0.53',
            'IPv4Static.1.DNS2': '',
            'NIC.1.DNSDomainName': 'example.com',
        }, target='iDRAC')

    @mock.patch.object(drac_mgmt.drac_utils, 'set_dell_attributes',
                       autospec=True)
    def test_set_ntp_servers_invalid_extra_attributes(self, mock_set_attrs):
        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            self.assertRaises(
                exception.InvalidParameterValue,
                task.driver.management.set_ntp_servers,
                task, ntp_servers=['10.0.0.1'],
                extra_attributes=['NTPConfigGroup.1.NTPMaxDist'])
        self.assertFalse(mock_set_attrs.called)

    @mock.patch.object(drac_mgmt.drac_utils, 'set_dell_attributes',
                       autospec=True)
    def test_set_dns_servers_invalid(self, mock_set_attrs):
        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            self.assertRaises(
                exception.InvalidParameterValue,
                task.driver.management.set_dns_servers,
                task, dns_servers='10.0.0.53')
        self.assertFalse(mock_set_attrs.called)

    @mock.patch.object(drac_mgmt.time, 'time', autospec=True,
                       return_value=1000.0)
    @mock.patch.object(drac_mgmt.drac_utils, 'set_dell_attributes',
                       autospec=True)
    @mock.patch.object(drac_mgmt.drac_utils, 'get_dell_attributes',
                       autospec=True)
    def _test_set_oidc_config(self, mock_get_attrs, mock_set_attrs,
                              mock_time, step_type='clean'):
        mock_get_attrs.return_value = {
            'OpenIDConnectServer.1.RegistrationStatus': json.dumps({
                'Action': 'register', 'HTTP Status': '201',
                'Request Status': 'Success', 'Sequence': '3'})}
        step = {'interface': 'management', 'step': 'set_oidc_config'}
        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            if step_type == 'clean':
                task.node.clean_step = step
            else:
                task.node.service_step = step
            result = task.driver.management.set_oidc_config(
                task, discovery_url='https://idp/.well-known/'
                                    'openid-configuration',
                initial_access_token='initial-token',
                https_certificate=_OIDC_CERTIFICATE, name='corp-sso')
            info = task.node.driver_internal_info
        mock_set_attrs.assert_called_once_with(mock.ANY, {
            'OpenIDConnectServer.1.Enabled': '1',
            'OpenIDConnectServer.1.Name': 'corp-sso',
            'OpenIDConnectServer.1.DiscoveryURL':
                'https://idp/.well-known/openid-configuration',
            'OpenIDConnectServer.1.RegistrationDetails':
                'bearer initial-token',
            'OpenIDConnectServer.1.HttpsCertificate': _OIDC_CERTIFICATE,
        }, target='System')
        mock_get_attrs.assert_called_once_with(mock.ANY, target='System')
        self.assertEqual({'provider_index': 1, 'previous_sequence': '3',
                          'deadline': 1600.0},
                         info['oidc_registration'])
        return result, info

    def test_set_oidc_config_abortable(self):
        method = drac_mgmt.DracRedfishManagement.set_oidc_config
        self.assertTrue(method._is_clean_step)
        self.assertTrue(method._clean_step_abortable)
        self.assertTrue(method._is_service_step)
        self.assertTrue(method._service_step_abortable)

    def test_set_oidc_config_clean(self):
        result, info = self._test_set_oidc_config()
        self.assertEqual(states.CLEANWAIT, result)
        self.assertTrue(info['cleaning_polling'])
        self.assertTrue(info['skip_current_clean_step'])

    def test_set_oidc_config_service(self):
        result, info = self._test_set_oidc_config(step_type='service')
        self.assertEqual(states.SERVICEWAIT, result)
        self.assertTrue(info['servicing_polling'])
        self.assertTrue(info['skip_current_service_step'])

    @mock.patch.object(drac_mgmt.time, 'time', autospec=True,
                       return_value=1000.0)
    @mock.patch.object(drac_mgmt.drac_utils, 'set_dell_attributes',
                       autospec=True)
    @mock.patch.object(drac_mgmt.drac_utils, 'get_dell_attributes',
                       autospec=True)
    def test_set_oidc_config_from_config(self, mock_get_attrs,
                                         mock_set_attrs, mock_time):
        cert_path = self._write_certificate(_OIDC_CERTIFICATE)
        self.config(oidc_discovery_url='https://idp/.well-known/'
                                       'openid-configuration',
                    oidc_initial_access_token='configured-token',
                    oidc_https_certificate_file=cert_path,
                    group='drac')
        mock_get_attrs.return_value = {}
        step = {'interface': 'management', 'step': 'set_oidc_config'}
        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            task.node.service_step = step
            result = task.driver.management.set_oidc_config(
                task, name='corp-sso')
            info = task.node.driver_internal_info
        mock_set_attrs.assert_called_once_with(mock.ANY, {
            'OpenIDConnectServer.1.Enabled': '1',
            'OpenIDConnectServer.1.Name': 'corp-sso',
            'OpenIDConnectServer.1.DiscoveryURL':
                'https://idp/.well-known/openid-configuration',
            'OpenIDConnectServer.1.RegistrationDetails':
                'bearer configured-token',
            'OpenIDConnectServer.1.HttpsCertificate': _OIDC_CERTIFICATE,
        }, target='System')
        self.assertEqual(states.SERVICEWAIT, result)
        self.assertEqual({'provider_index': 1, 'previous_sequence': None,
                          'deadline': 1600.0},
                         info['oidc_registration'])

    @mock.patch.object(drac_mgmt.time, 'time', autospec=True,
                       return_value=1000.0)
    @mock.patch.object(drac_mgmt.drac_utils, 'set_dell_attributes',
                       autospec=True)
    @mock.patch.object(drac_mgmt.drac_utils, 'get_dell_attributes',
                       autospec=True)
    def _test_set_oidc_config_no_certificate(self, mock_get_attrs,
                                             mock_set_attrs, mock_time,
                                             **kwargs):
        self.config(oidc_discovery_url='https://idp/.well-known/'
                                       'openid-configuration',
                    oidc_initial_access_token='configured-token',
                    group='drac')
        mock_get_attrs.return_value = {}
        step = {'interface': 'management', 'step': 'set_oidc_config'}
        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            task.node.service_step = step
            result = task.driver.management.set_oidc_config(task, **kwargs)
        mock_set_attrs.assert_called_once_with(mock.ANY, {
            'OpenIDConnectServer.1.Enabled': '1',
            'OpenIDConnectServer.1.Name': 'SSO',
            'OpenIDConnectServer.1.DiscoveryURL':
                'https://idp/.well-known/openid-configuration',
            'OpenIDConnectServer.1.RegistrationDetails':
                'bearer configured-token',
        }, target='System')
        self.assertEqual(states.SERVICEWAIT, result)

    def test_set_oidc_config_certificate_unset(self):
        self._test_set_oidc_config_no_certificate()

    def test_set_oidc_config_certificate_empty_file(self):
        self.config(oidc_https_certificate_file=self._write_certificate(''),
                    group='drac')
        self._test_set_oidc_config_no_certificate()

    def test_set_oidc_config_certificate_file_reread(self):
        cert_path = self._write_certificate(_OIDC_CERTIFICATE)
        self.config(oidc_https_certificate_file=cert_path, group='drac')
        self.assertEqual(_OIDC_CERTIFICATE,
                         drac_mgmt._read_oidc_https_certificate_file())
        with open(cert_path, 'w') as cert_file:
            cert_file.write('updated')
        self.assertEqual('updated',
                         drac_mgmt._read_oidc_https_certificate_file())

    @mock.patch.object(drac_mgmt.drac_utils, 'set_dell_attributes',
                       autospec=True)
    def test_set_oidc_config_certificate_file_missing(self, mock_set_attrs):
        self.config(oidc_https_certificate_file=os.path.join(
            self.certificate_dir, 'missing.pem'), group='drac')
        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            self.assertRaisesRegex(
                exception.InvalidParameterValue,
                'oidc_https_certificate_file',
                task.driver.management.set_oidc_config, task,
                discovery_url='https://idp/.well-known/openid-configuration',
                initial_access_token='token')
        self.assertFalse(mock_set_attrs.called)

    def test_set_oidc_config_certificate_empty_config(self):
        self.config(oidc_https_certificate_file='', group='drac')
        self._test_set_oidc_config_no_certificate()

    def test_set_oidc_config_certificate_blank_argument(self):
        self._test_set_oidc_config_no_certificate(https_certificate='  \n')

    @mock.patch.object(drac_mgmt.drac_utils, 'set_dell_attributes',
                       autospec=True)
    def test_set_oidc_config_invalid_certificate(self, mock_set_attrs):
        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            self.assertRaisesRegex(
                exception.InvalidParameterValue, 'https_certificate',
                task.driver.management.set_oidc_config, task,
                discovery_url='https://idp/.well-known/openid-configuration',
                initial_access_token='token',
                https_certificate='not a certificate')
        self.assertFalse(mock_set_attrs.called)

    @mock.patch.object(drac_mgmt.drac_utils, 'set_dell_attributes',
                       autospec=True)
    def test_set_oidc_config_missing_token(self, mock_set_attrs):
        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            self.assertRaisesRegex(
                exception.InvalidParameterValue, 'initial_access_token',
                task.driver.management.set_oidc_config, task,
                discovery_url='https://idp/.well-known/openid-configuration',
                https_certificate=_OIDC_CERTIFICATE)
        self.assertFalse(mock_set_attrs.called)

    @mock.patch.object(drac_mgmt.drac_utils, 'set_dell_attributes',
                       autospec=True)
    def test_set_oidc_config_index_and_disable(self, mock_set_attrs):
        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            result = task.driver.management.set_oidc_config(
                task, enable_oidc=False, provider_index=2)
            self.assertNotIn('oidc_registration',
                             task.node.driver_internal_info)
        self.assertIsNone(result)
        mock_set_attrs.assert_called_once_with(mock.ANY, {
            'OpenIDConnectServer.2.Enabled': '0',
        }, target='System')

    @mock.patch.object(drac_mgmt.drac_utils, 'set_dell_attributes',
                       autospec=True)
    def test_set_oidc_config_extra_attributes_override(self, mock_set_attrs):
        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            task.driver.management.set_oidc_config(
                task, enable_oidc=False,
                extra_attributes={'OpenIDConnectServer.1.Enabled': 'Disabled',
                                  'OpenIDConnectServer.1.Name': 'old'})
        mock_set_attrs.assert_called_once_with(mock.ANY, {
            'OpenIDConnectServer.1.Enabled': 'Disabled',
            'OpenIDConnectServer.1.Name': 'old',
        }, target='System')

    @mock.patch.object(drac_mgmt.drac_utils, 'set_dell_attributes',
                       autospec=True)
    def test_set_oidc_config_invalid_provider_index(self, mock_set_attrs):
        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            self.assertRaises(
                exception.InvalidParameterValue,
                task.driver.management.set_oidc_config,
                task, enable_oidc=False, provider_index=17)
        self.assertFalse(mock_set_attrs.called)


    def _oidc_status(self, **status):
        return {'OpenIDConnectServer.9.RegistrationStatus':
                json.dumps(status)}

    def _setup_oidc_wait(self, step_type='clean', deadline=2000.0,
                         previous_sequence='2'):
        step = {'interface': 'management', 'step': 'set_oidc_config'}
        if step_type == 'clean':
            self.node.provision_state = states.CLEANWAIT
            self.node.clean_step = step
        else:
            self.node.provision_state = states.SERVICEWAIT
            self.node.service_step = step
        self.node.set_driver_internal_info('oidc_registration', {
            'provider_index': 9, 'previous_sequence': previous_sequence,
            'deadline': deadline})
        self.node.save()

    def _check_oidc(self, now=1000.0):
        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=True) as task:
            with mock.patch.object(drac_mgmt.time, 'time', autospec=True,
                                   return_value=now):
                self.management._check_oidc_registration(task)
            return task.node.driver_internal_info

    @mock.patch.object(manager_utils, 'notify_conductor_resume_clean',
                       autospec=True)
    @mock.patch.object(drac_utils, 'get_dell_attributes', autospec=True)
    def test__check_oidc_registration_success_clean(self, mock_get_attrs,
                                                    mock_resume):
        self._setup_oidc_wait()
        mock_get_attrs.return_value = self._oidc_status(
            **{'Action': 'register', 'HTTP Status': '201',
               'Request Status': 'Success', 'Sequence': '3'})
        info = self._check_oidc()
        self.assertNotIn('oidc_registration', info)
        mock_resume.assert_called_once_with(mock.ANY)

    @mock.patch.object(manager_utils, 'notify_conductor_resume_service',
                       autospec=True)
    @mock.patch.object(drac_utils, 'get_dell_attributes', autospec=True)
    def test__check_oidc_registration_success_service(self, mock_get_attrs,
                                                      mock_resume):
        self._setup_oidc_wait(step_type='service', previous_sequence=None)
        mock_get_attrs.return_value = self._oidc_status(
            **{'Action': 'register', 'HTTP Status': '201',
               'Request Status': 'Success', 'Sequence': '3'})
        info = self._check_oidc()
        self.assertNotIn('oidc_registration', info)
        mock_resume.assert_called_once_with(mock.ANY)

    @mock.patch.object(manager_utils, 'notify_conductor_resume_clean',
                       autospec=True)
    @mock.patch.object(manager_utils, 'cleaning_error_handler',
                       autospec=True)
    @mock.patch.object(drac_utils, 'get_dell_attributes', autospec=True)
    def test__check_oidc_registration_in_progress(self, mock_get_attrs,
                                                  mock_error, mock_resume):
        self._setup_oidc_wait()
        for status in ({'Action': 'register', 'HTTP Status': '201',
                        'Request Status': 'Success', 'Sequence': '2'},
                       {'Action': 'discover', 'HTTP Status': '200',
                        'Request Status': 'Success', 'Sequence': '3'}):
            mock_get_attrs.return_value = self._oidc_status(**status)
            info = self._check_oidc()
            self.assertIn('oidc_registration', info)
        self.assertFalse(mock_resume.called)
        self.assertFalse(mock_error.called)

    @mock.patch.object(manager_utils, 'cleaning_error_handler',
                       autospec=True)
    @mock.patch.object(drac_utils, 'get_dell_attributes', autospec=True)
    def test__check_oidc_registration_failed(self, mock_get_attrs,
                                             mock_error):
        self._setup_oidc_wait()
        mock_get_attrs.return_value = self._oidc_status(
            **{'Action': 'register', 'HTTP Status': '401',
               'HTTP Error': 'invalid initial access token',
               'Request Status': 'Failed', 'Sequence': '3'})
        info = self._check_oidc()
        self.assertNotIn('oidc_registration', info)
        mock_error.assert_called_once_with(mock.ANY, mock.ANY, mock.ANY)
        self.assertIn('invalid initial access token',
                      mock_error.call_args[0][2])

    @mock.patch.object(manager_utils, 'servicing_error_handler',
                       autospec=True)
    @mock.patch.object(drac_utils, 'get_dell_attributes', autospec=True)
    def test__check_oidc_registration_timeout(self, mock_get_attrs,
                                              mock_error):
        self._setup_oidc_wait(step_type='service')
        mock_get_attrs.return_value = self._oidc_status(
            **{'Action': 'discover', 'HTTP Status': '200',
               'Request Status': 'Success', 'Sequence': '3'})
        info = self._check_oidc(now=2000.0)
        self.assertNotIn('oidc_registration', info)
        mock_error.assert_called_once_with(mock.ANY, mock.ANY, mock.ANY)
        self.assertIn('timed out', mock_error.call_args[0][2])

    @mock.patch.object(manager_utils, 'cleaning_error_handler',
                       autospec=True)
    @mock.patch.object(drac_utils, 'get_dell_attributes', autospec=True)
    def test__check_oidc_registration_read_error(self, mock_get_attrs,
                                                 mock_error):
        self._setup_oidc_wait()
        mock_get_attrs.side_effect = exception.RedfishError(error='boom')
        info = self._check_oidc()
        self.assertIn('oidc_registration', info)
        self.assertFalse(mock_error.called)

        info = self._check_oidc(now=2000.0)
        self.assertNotIn('oidc_registration', info)
        mock_error.assert_called_once_with(mock.ANY, mock.ANY, mock.ANY)
        self.assertIn('boom', mock_error.call_args[0][2])

    @mock.patch.object(drac_utils, 'get_dell_attributes', autospec=True)
    def test__check_oidc_registration_stale(self, mock_get_attrs):
        self._setup_oidc_wait()
        self.node.clean_step = {'interface': 'raid',
                                'step': 'create_configuration'}
        self.node.save()
        info = self._check_oidc()
        self.assertNotIn('oidc_registration', info)
        self.assertFalse(mock_get_attrs.called)

    @mock.patch.object(task_manager, 'acquire', autospec=True)
    def test__clear_oidc_registration_aborted(self, mock_acquire):
        self._setup_oidc_wait()
        # Aborting wipes the current step and leaves the wait state.
        self.node.clean_step = None
        self.node.provision_state = states.CLEANFAIL
        self.node.save()
        mock_manager = mock.Mock()
        mock_manager.iter_nodes.return_value = [
            (self.node.uuid, 'idrac', '', self.node.driver_internal_info)]
        task = mock.Mock(node=self.node,
                         driver=mock.Mock(management=self.management))
        mock_acquire.return_value = mock.MagicMock(
            __enter__=mock.MagicMock(return_value=task))

        self.management._clear_oidc_registration_aborted(
            mock_manager, self.context)

        task.upgrade_lock.assert_called_once_with()
        self.node.refresh()
        self.assertNotIn('oidc_registration', self.node.driver_internal_info)
        filters = mock_manager.iter_nodes.call_args[1]['filters']
        self.assertEqual({states.CLEANFAIL, states.SERVICEFAIL},
                         filters['provision_state_in'])

    @mock.patch.object(task_manager, 'acquire', autospec=True)
    def test__query_oidc_registration_status(self, mock_acquire):
        driver_internal_info = {'oidc_registration': {
            'provider_index': 9, 'previous_sequence': None,
            'deadline': 2000.0}}
        mock_manager = mock.Mock()
        mock_manager.iter_nodes.return_value = [
            (self.node.uuid, 'idrac', '', driver_internal_info)]
        task = mock.Mock(node=self.node,
                         driver=mock.Mock(management=self.management))
        mock_acquire.return_value = mock.MagicMock(
            __enter__=mock.MagicMock(return_value=task))
        self.management._check_oidc_registration = mock.Mock()

        self.management._query_oidc_registration_status(
            mock_manager, self.context)

        self.management._check_oidc_registration.assert_called_once_with(
            task)
        filters = mock_manager.iter_nodes.call_args[1]['filters']
        self.assertEqual({states.CLEANWAIT, states.SERVICEWAIT},
                         filters['provision_state_in'])
