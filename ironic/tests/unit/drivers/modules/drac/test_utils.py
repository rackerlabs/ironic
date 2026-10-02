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

from ironic.common import exception
from ironic.conductor import task_manager
from ironic.drivers.modules.drac import utils as drac_utils
from ironic.drivers.modules.redfish import utils as redfish_utils
from ironic.tests.unit.drivers.modules.drac import utils as test_utils
from ironic.tests.unit.objects import utils as obj_utils

INFO_DICT = test_utils.INFO_DICT


@mock.patch.object(redfish_utils, 'get_system', autospec=True)
class DracUtilsOemManagerTestCase(test_utils.BaseDracTest):

    def setUp(self):
        super(DracUtilsOemManagerTestCase, self).setUp()
        self.node = obj_utils.create_test_node(self.context,
                                               driver='idrac',
                                               driver_info=INFO_DICT)
        self.config(enabled_hardware_types=['idrac'],
                    enabled_management_interfaces=['idrac-redfish'])

    def test_execute_oem_manager_method(self, mock_get_system):
        fake_manager_oem = mock.Mock()
        fake_manager_oem.test_method.return_value = 42
        fake_manager = mock.Mock()
        fake_manager.get_oem_extension.return_value = fake_manager_oem
        mock_get_system.return_value.managers = [fake_manager]

        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            result = drac_utils.execute_oem_manager_method(
                task, 'test method', lambda m: m.test_method())

            self.assertEqual(42, result)

    def test_execute_oem_manager_method_no_managers(self, mock_get_system):
        mock_get_system.return_value.managers = []

        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            self.assertRaises(
                exception.RedfishError,
                drac_utils.execute_oem_manager_method,
                task,
                'test method',
                lambda m: m.test_method())

    def test_execute_oem_manager_method_oem_not_found(self, mock_get_system):
        fake_manager = mock.Mock()
        fake_manager.get_oem_extension.side_effect = (
            sushy.exceptions.OEMExtensionNotFoundError)
        mock_get_system.return_value.managers = [fake_manager]

        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            self.assertRaises(
                exception.RedfishError,
                drac_utils.execute_oem_manager_method,
                task,
                'test method',
                lambda m: m.test_method())

    def test_execute_oem_manager_method_managers_fail(self, mock_get_system):
        fake_manager_oem1 = mock.Mock()
        fake_manager_oem1.test_method.side_effect = (
            sushy.exceptions.SushyError)
        fake_manager1 = mock.Mock()
        fake_manager1.get_oem_extension.return_value = fake_manager_oem1
        fake_manager_oem2 = mock.Mock()
        fake_manager_oem2.test_method.side_effect = (
            sushy.exceptions.SushyError)
        fake_manager2 = mock.Mock()
        fake_manager2.get_oem_extension.return_value = fake_manager_oem2
        mock_get_system.return_value.managers = [fake_manager1, fake_manager2]

        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            self.assertRaises(
                exception.RedfishError,
                drac_utils.execute_oem_manager_method,
                task,
                'test method',
                lambda m: m.test_method())


@mock.patch.object(redfish_utils, 'get_manager', autospec=True)
@mock.patch.object(redfish_utils, 'get_system', autospec=True)
class DracUtilsDellAttributesTestCase(test_utils.BaseDracTest):

    def setUp(self):
        super(DracUtilsDellAttributesTestCase, self).setUp()
        self.node = obj_utils.create_test_node(self.context,
                                               driver='idrac',
                                               driver_info=INFO_DICT)
        self.config(enabled_hardware_types=['idrac'],
                    enabled_management_interfaces=['idrac-redfish'])

    def _fake_manager(self, links=None, path='/redfish/v1/Managers/'
                      'iDRAC.Embedded.1', identity='iDRAC.Embedded.1'):
        manager = mock.Mock()
        manager.json = {'Links': {'Oem': {'Dell': {
            'DellAttributes': links if links is not None else []}}}}
        manager.path = path
        manager.identity = identity
        return manager

    def test_get_dell_attributes_uri_matches_system(self, mock_get_system,
                                                    mock_get_manager):
        system_uri = ('/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/'
                      'DellAttributes/System.Embedded.1')
        idrac_uri = ('/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/'
                     'DellAttributes/iDRAC.Embedded.1')
        manager = self._fake_manager(links=[
            {'@odata.id': system_uri},
            {'@odata.id': idrac_uri},
        ])
        self.assertEqual(system_uri,
                         drac_utils._get_dell_attributes_uri(manager))

    def test_get_dell_attributes_uri_matches_explicit_target(
            self, mock_get_system, mock_get_manager):
        idrac_uri = ('/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/'
                     'DellAttributes/iDRAC.Embedded.1')
        manager = self._fake_manager(links=[{'@odata.id': idrac_uri}])
        self.assertEqual(
            idrac_uri, drac_utils._get_dell_attributes_uri(
                manager, target='iDRAC'))

    def test_get_dell_attributes_uri_missing_target(self, mock_get_system,
                                                    mock_get_manager):
        first = ('/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/'
                 'DellAttributes/System.Embedded.1')
        manager = self._fake_manager(links=[{'@odata.id': first}])
        self.assertRaises(exception.RedfishError,
                          drac_utils._get_dell_attributes_uri,
                          manager, target='Nope')

    def test_get_dell_attributes_uri_not_advertised(self, mock_get_system,
                                                    mock_get_manager):
        manager = self._fake_manager(links=[])
        self.assertRaises(exception.RedfishError,
                          drac_utils._get_dell_attributes_uri, manager)

    def test_set_dell_attributes(self, mock_get_system, mock_get_manager):
        system_uri = ('/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/'
                      'DellAttributes/System.Embedded.1')
        manager = self._fake_manager(links=[{'@odata.id': system_uri}])
        mock_get_manager.return_value = manager

        attributes = {'NTPConfigGroup.1.NTP1': '10.0.0.1'}
        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            drac_utils.set_dell_attributes(task, attributes)

        manager._conn.patch.assert_called_once_with(
            system_uri, data={'Attributes': attributes})

    def test_get_dell_attributes(self, mock_get_system, mock_get_manager):
        system_uri = ('/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/'
                      'DellAttributes/System.Embedded.1')
        manager = self._fake_manager(links=[{'@odata.id': system_uri}])
        manager._conn.get.return_value.json.return_value = {
            'Attributes': {'Time.1.Timezone': 'UTC'}}
        mock_get_manager.return_value = manager

        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            result = drac_utils.get_dell_attributes(task)

        self.assertEqual({'Time.1.Timezone': 'UTC'}, result)
        manager._conn.get.assert_called_once_with(system_uri)

    def test_set_dell_attributes_error(self, mock_get_system,
                                       mock_get_manager):
        system_uri = ('/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/'
                      'DellAttributes/System.Embedded.1')
        manager = self._fake_manager(links=[{'@odata.id': system_uri}])
        manager._conn.patch.side_effect = sushy.exceptions.SushyError
        mock_get_manager.return_value = manager

        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            self.assertRaises(
                exception.RedfishError,
                drac_utils.set_dell_attributes,
                task, {'NTPConfigGroup.1.NTP1': '10.0.0.1'})
