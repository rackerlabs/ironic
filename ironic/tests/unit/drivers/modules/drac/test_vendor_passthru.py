# Copyright (c) 2021 Dell Inc. or its subsidiaries.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

from unittest import mock

import sushy
from sushy.oem.dell.resources.manager import constants as dell_constants
from sushy.taskmonitor import TaskMonitor

from ironic.common import exception
from ironic.drivers.modules.drac import support_assist
from ironic.drivers.modules.drac import utils as drac_utils
from ironic.drivers.modules.drac import vendor_passthru
from ironic.drivers.modules.redfish import utils as redfish_utils
from ironic.drivers.modules.redfish import vendor as redfish_vendor
from ironic.tests import base


class DracSupportAssistTestCase(base.TestCase):

    def setUp(self):
        super().setUp()
        self.vendor = vendor_passthru.DracRedfishVendorPassthru()
        self.task = mock.Mock()
        self.uri = '/redfish/v1/TaskService/TaskMonitors/JID_123'
        self.config(support_assist_share_url='https://192.0.2.1/export/tsr',
                    group='drac')
        patcher = mock.patch.object(
            drac_utils, 'execute_oem_manager_method', autospec=True)
        self.execute = patcher.start()
        self.addCleanup(patcher.stop)
        self.manager = mock.Mock()
        self.execute.side_effect = lambda task, name, fn: fn(self.manager)
        self.task.node.uuid = '12345678-1234-4234-8234-123456789abc'

    def test_eula_status(self):
        for code, accepted in [('SRV074', True), ('SRV104', False)]:
            with self.subTest(code=code):
                body = {'@Message.ExtendedInfo': [
                    {'MessageId': 'IDRAC.2.19.' + code}]}
                self.manager.lifecycle_service.\
                    support_assist_get_eula_status.return_value = body
                self.assertEqual(
                    {'accepted': accepted, 'details': body},
                    self.vendor.support_assist_get_eula_status(self.task))

    def test_eula_status_unknown(self):
        for body in ({}, None, [], 'invalid',
                     {'@Message.ExtendedInfo': None},
                     {'@Message.ExtendedInfo': [None]},
                     {'@Message.ExtendedInfo': [{'MessageId': None}]},
                     {'@Message.ExtendedInfo': [{'MessageId': 'SRV999'}]}):
            with self.subTest(body=body):
                self.manager.lifecycle_service.\
                    support_assist_get_eula_status.return_value = body
                self.assertRaises(exception.RedfishError,
                                  self.vendor.support_assist_get_eula_status,
                                  self.task)

    def test_accept_eula(self):
        body = {'@Message.ExtendedInfo': [{'MessageId': 'SRV074'}]}
        for result in (body, {}, None):
            with self.subTest(result=result):
                accept = self.manager.lifecycle_service.\
                    support_assist_accept_eula
                accept.return_value = result
                self.assertEqual(result if result is not None else {},
                                 self.vendor.support_assist_accept_eula(
                                     self.task))
                accept.assert_called_with()

    def test_accept_eula_invalid_response(self):
        for body in ([], 'invalid', object()):
            with self.subTest(body=body):
                self.manager.lifecycle_service.\
                    support_assist_accept_eula.return_value = body
                self.assertRaises(exception.RedfishError,
                                  self.vendor.support_assist_accept_eula,
                                  self.task)

    def test_collect_defaults(self):
        self.vendor.validate(self.task, 'support_assist_collect')
        self.manager.lifecycle_service.support_assist_collect.\
            return_value.task_monitor_uri = self.uri
        self.assertEqual({'task_monitor': self.uri},
                         self.vendor.support_assist_collect(self.task))
        self.manager.lifecycle_service.\
            support_assist_collect.assert_called_once_with(
                data_types=[dell_constants.SupportAssistDataType.HARDWARE],
                filter_data=False,
                share_type=dell_constants.SupportAssistShareType.HTTPS,
                ip_address='192.0.2.1', share_name='export/tsr',
                port=None, username=None, password=None)
        self.manager.lifecycle_service.\
            support_assist_accept_eula.assert_not_called()

    def test_collect_absolute_task_monitor(self):
        self.manager.lifecycle_service.support_assist_collect.\
            return_value.task_monitor_uri = 'https://192.0.2.2' + self.uri
        result = self.vendor.support_assist_collect(self.task)
        self.assertEqual({'task_monitor': self.uri}, result)
        self.vendor.validate(self.task, 'get_job_status', **result)

    def test_collect_node_directory(self):
        self.manager.lifecycle_service.support_assist_collect.\
            return_value.task_monitor_uri = self.uri
        for url, expected in (
                ('http://192.0.2.1/supportassist/{node_uuid}/',
                 'supportassist/' + self.task.node.uuid + '/'),
                ('https://192.0.2.1:8443/supportassist/{node_uuid}/',
                 'supportassist/' + self.task.node.uuid + '/')):
            with self.subTest(url=url):
                self.config(support_assist_share_url=url, group='drac')
                self.vendor.support_assist_collect(self.task)
                call = (self.manager.lifecycle_service
                        .support_assist_collect.call_args)
                self.assertEqual(expected, call.kwargs['share_name'])

    def test_collect_selected_data(self):
        self.manager.lifecycle_service.support_assist_collect.\
            return_value.task_monitor_uri = self.uri
        self.vendor.validate(self.task, 'support_assist_collect',
                             data_types=['DebugLogs'], filter_data=True)
        self.vendor.support_assist_collect(
            self.task, data_types=['DebugLogs'], filter_data=True)
        call = self.manager.lifecycle_service.support_assist_collect.call_args
        self.assertEqual([dell_constants.SupportAssistDataType.DEBUG],
                         call.kwargs['data_types'])
        self.assertTrue(call.kwargs['filter_data'])

    def test_collect_eula_rejected(self):
        self.execute.side_effect = exception.RedfishError(
            error='SRV104: EULA not accepted')
        self.assertRaises(exception.RedfishError,
                          self.vendor.support_assist_collect, self.task)
        self.assertEqual(1, self.execute.call_count)

    def test_validate_invalid_arguments(self):
        cases = [
            ('support_assist_collect', {'data_types': 'HWData'}),
            ('support_assist_collect', {'data_types': []}),
            ('support_assist_collect', {'data_types': [{}]}),
            ('support_assist_collect', {'data_types': ['invalid']}),
            ('support_assist_collect', {'filter_data': 'No'}),
            ('support_assist_collect', {'share_url': 'nfs://other/share'}),
            ('support_assist_get_eula_status', {'unexpected': True}),
            ('get_job_status', {}),
            ('get_job_status',
             {'task_monitor': self.uri, 'unexpected': True}),
            ('get_job_status',
             {'task_monitor': 'https://other/' + self.uri}),
            ('get_job_status',
             {'task_monitor': self.uri + '/../other'}),
            ('get_job_status',
             {'task_monitor': self.uri + '?query=1'}),
            ('get_job_status',
             {'task_monitor': '/redfish/v1/TaskService/Tasks/../other'}),
            ('get_job_status',
             {'task_monitor': '/redfish/v1/TaskService/Tasks/JID_123?x=1'}),
            ('get_job_status',
             {'task_monitor': '/redfish/v1/Managers/iDRAC.Embedded.1/'
              'Oem/Dell/Jobs/../other'}),
            ('get_job_status',
             {'task_monitor': '/redfish/v1/Managers/iDRAC.Embedded.1/'
              'Oem/Dell/Jobs/JID_123?x=1'}),
        ]
        for method, kwargs in cases:
            with self.subTest(method=method, kwargs=kwargs):
                self.assertRaises(exception.InvalidParameterValue,
                                  self.vendor.validate, self.task, method,
                                  **kwargs)
        self.execute.assert_not_called()

    def test_validate_share(self):
        for url in (None, 'http://host', 'smb://host/share',
                    'https://user:password@host/path',
                    'http://host/path?query=1', 'http://host:bad/path',
                    'https://host/path#fragment', 'cifs://host/share',
                    'nfs://host/share', 'ftp://host/share',
                    'tftp://host/share'):
            with self.subTest(url=url):
                self.config(support_assist_share_url=url, group='drac')
                self.assertRaises(exception.InvalidParameterValue,
                                  self.vendor.validate, self.task,
                                  'support_assist_collect')
        self.execute.assert_not_called()

    def test_share_credentials(self):
        self.config(support_assist_share_url='https://host:8443/tsr',
                    support_assist_share_username='user',
                    support_assist_share_password='secret', group='drac')
        share = support_assist._share()
        self.assertEqual(8443, share['port'])
        self.assertEqual('tsr', share['share_name'])
        self.assertEqual('user', share['username'])
        self.assertEqual('secret', share['password'])

    @mock.patch.object(redfish_vendor.RedfishVendorPassthru, 'validate',
                       autospec=True)
    def test_inherited_validation(self, validate):
        self.vendor.validate(self.task, 'create_subscription',
                             Destination='https://example.test/events')
        validate.assert_called_once_with(
            self.vendor, self.task, 'create_subscription',
            Destination='https://example.test/events')

    def test_validate_http_method(self):
        self.vendor.validate(self.task, 'support_assist_collect',
                             http_method='POST')
        self.vendor.validate(self.task, 'get_job_status',
                             task_monitor=self.uri, http_method='GET')

    @mock.patch.object(redfish_utils, 'get_task_monitor', autospec=True)
    def test_job_status(self, get_monitor):
        for state in ('Running', 'Completed', 'Exception'):
            with self.subTest(state=state):
                conn = mock.Mock()
                response = conn.get.return_value
                response.status_code = 202
                response.json.return_value = {
                    'Id': 'JID_123', 'Name': 'SupportAssist Collection',
                    'TaskState': state, 'TaskStatus': 'OK',
                    'PercentComplete': 2,
                    'Messages': [{'MessageId': 'IDRAC.2.19.SRV001',
                                  'Message': 'Collection started'}],
                    'Oem': {'Dell': {'JobState': state}},
                }
                get_monitor.return_value = TaskMonitor(conn, self.uri)
                result = self.vendor.get_job_status(
                    self.task, task_monitor=self.uri)
                self.assertEqual(state, result['task_state'])
                self.assertEqual(state == 'Running', result['is_processing'])
                self.assertEqual(state, result['job_state'])
                self.assertEqual(2, result['percent_complete'])
                conn.get.assert_called_once_with(path=self.uri)
                # No extra GET for a task resource or download Location.
                get_monitor.assert_called_with(self.task.node, self.uri)

    @mock.patch.object(redfish_utils, 'get_task_monitor', autospec=True)
    def test_job_status_task_resource(self, get_monitor):
        self.config(support_assist_share_url=None, group='drac')
        uri = '/redfish/v1/TaskService/Tasks/JID_123'
        self.vendor.validate(self.task, 'get_job_status',
                             task_monitor=uri)
        for state in ('Running', 'Completed', 'Exception'):
            with self.subTest(state=state):
                conn = mock.Mock()
                response = conn.get.return_value
                response.status_code = 200
                response.json.return_value = {'TaskState': state}
                get_monitor.return_value = TaskMonitor(conn, uri)
                result = self.vendor.get_job_status(
                    self.task, task_monitor=uri)
                self.assertEqual(state, result['task_state'])
                self.assertEqual(state == 'Running', result['is_processing'])
                self.assertEqual(uri, result['task_monitor'])
                get_monitor.assert_called_with(self.task.node, uri)
                conn.get.assert_called_once_with(path=uri)

    @mock.patch.object(redfish_utils, 'get_task_monitor', autospec=True)
    def test_job_status_dell_job(self, get_monitor):
        uri = ('/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/Jobs/'
               'JID_953546045040')
        self.vendor.validate(self.task, 'get_job_status', task_monitor=uri)
        for state in ('Scheduled', 'Running', 'Completed', 'Failed',
                      'CompletedWithErrors'):
            with self.subTest(state=state):
                conn = mock.Mock()
                response = conn.get.return_value
                response.status_code = 200
                message = {'MessageId': 'SRV088', 'Message': 'Job status',
                           'MessageArgs': []}
                response.json.return_value = {
                    'JobState': state, 'PercentComplete': 100, **message}
                get_monitor.return_value = TaskMonitor(conn, uri)
                result = self.vendor.get_job_status(
                    self.task, task_monitor=uri)
                self.assertEqual(state, result['task_state'])
                self.assertEqual(state, result['job_state'])
                self.assertEqual(state in ('Scheduled', 'Running'),
                                 result['is_processing'])
                self.assertEqual([message], result['messages'])
                self.assertEqual(100, result['percent_complete'])
                self.assertEqual(uri, result['task_monitor'])
                conn.get.assert_called_once_with(path=uri)

    @mock.patch.object(redfish_utils, 'get_task_monitor', autospec=True)
    def test_job_completed_without_body(self, get_monitor):
        conn = mock.Mock()
        response = conn.get.return_value
        response.status_code = 204
        response.content = b''
        response.headers = {'Location': '/redfish/v1/Oem/Dell/sacollect.zip'}
        get_monitor.return_value = TaskMonitor(conn, self.uri)
        result = self.vendor.get_job_status(
            self.task, task_monitor=self.uri)
        self.assertEqual('Completed', result['task_state'])
        self.assertFalse(result['is_processing'])
        conn.get.assert_called_once_with(path=self.uri)

    @mock.patch.object(redfish_utils, 'get_task_monitor', autospec=True)
    def test_job_bmc_error(self, get_monitor):
        get_monitor.side_effect = sushy.exceptions.SushyError('job failed')
        self.assertRaises(exception.RedfishError,
                          self.vendor.get_job_status,
                          self.task, task_monitor=self.uri)

    @mock.patch.object(redfish_utils, 'get_task_monitor', autospec=True)
    def test_job_non_json_response(self, get_monitor):
        response = get_monitor.return_value.response
        response.content = b'<html>Not JSON</html>'
        response.json.side_effect = ValueError('Invalid JSON')
        self.assertRaises(exception.RedfishError,
                          self.vendor.get_job_status,
                          self.task, task_monitor=self.uri)

    @mock.patch.object(redfish_utils, 'get_task_monitor', autospec=True)
    def test_job_invalid_response_shape(self, get_monitor):
        for data in (None, [], 'invalid', {'Oem': None}, {'Oem': []},
                     {'Oem': {'Dell': None}}, {'Oem': {'Dell': 'invalid'}}):
            with self.subTest(data=data):
                get_monitor.return_value.response.json.return_value = data
                self.assertRaises(exception.RedfishError,
                                  self.vendor.get_job_status,
                                  self.task, task_monitor=self.uri)

    def test_routes(self):
        routes = self.vendor.vendor_routes
        self.assertIn('create_subscription', routes)
        self.assertNotIn('support_assist_register', routes)
        for name in ('support_assist_get_eula_status',
                     'support_assist_accept_eula',
                     'support_assist_collect',
                     'get_job_status'):
            self.assertFalse(routes[name]['async'])
        self.assertTrue(self.vendor.supported)
