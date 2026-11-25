# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

from unittest import mock

from ironic.conductor import task_manager
from ironic.conf import CONF
from ironic.drivers.modules.inspector.hooks import pci_devices as \
    pci_devices_hook
from ironic.tests import base
from ironic.tests.unit.db import base as db_base
from ironic.tests.unit.objects import utils as obj_utils

_PLUGIN_DATA = {
    'pci_devices': [
        {
            'vendor_id': '8086', 'product_id': '2922', 'class': '010601',
            'revision': '02', 'bus': '0000:00:1f.2'
        },
        {
            'vendor_id': '8086', 'product_id': '2918', 'class': '060100',
            'revision': '02', 'bus': '0000:00:1f.0'
        },
        {
            'vendor_id': '8086', 'product_id': '2930', 'class': '0c0500',
            'revision': '02', 'bus': '0000:00:1f.3'
        },
        {
            'vendor_id': '1b36', 'product_id': '000c', 'class': '060400',
            'revision': '00', 'bus': '0000:00:01.2'
        },
        {
            'vendor_id': '1b36', 'product_id': '000c', 'class': '060400',
            'revision': '00', 'bus': '0000:00:01.0'
        },
        {
            'vendor_id': '1b36', 'product_id': '000d', 'class': '0c0330',
            'revision': '01', 'bus': '0000:02:00.0'
        }
    ]
}

_ALIASES = {('8086', '2922'): 'EightyTwentyTwo',
            ('8086', '2918'): 'EightyEighteen',
            ('1b36', '000c'): 'OneBZeroC'}


class PciDevicesTestCase(db_base.DbTestCase):
    def setUp(self):
        super().setUp()
        CONF.set_override('enabled_inspect_interfaces',
                          ['agent', 'no-inspect'])
        self.node = obj_utils.create_test_node(self.context,
                                               inspect_interface='agent')
        self.inventory = {'fake': 'fake-inventory'}
        self.plugin_data = _PLUGIN_DATA.copy()
        self.pci_devices_hook = pci_devices_hook.PciDevicesHook()
        self.pci_devices_hook._aliases = _ALIASES

    def test_pci_devices(self):
        with task_manager.acquire(self.context, self.node.id) as task:
            self.pci_devices_hook.__call__(task, self.inventory,
                                           self.plugin_data)
            self.node.refresh()
            result = self.node.properties.get('capabilities', '')
            expected = 'EightyTwentyTwo:1,EightyEighteen:1,OneBZeroC:2'
            self.assertEqual(expected, result)

    def test_pci_devices_from_inventory(self):
        self.inventory = {
            'pci_devices': self.plugin_data.pop('pci_devices'),
        }
        with task_manager.acquire(self.context, self.node.id) as task:
            self.pci_devices_hook.__call__(task, self.inventory,
                                           self.plugin_data)
            self.node.refresh()
            result = self.node.properties.get('capabilities', '')
            expected = 'EightyTwentyTwo:1,EightyEighteen:1,OneBZeroC:2'
            self.assertEqual(expected, result)

    def test_pci_devices_prefer_device_id(self):
        self.plugin_data = {'pci_devices': [
            {'vendor_id': '8086', 'device_id': '2922', 'product_id': '2930'},
            {'vendor_id': '8086', 'product_id': '2918'},
        ]}
        with task_manager.acquire(self.context, self.node.id) as task:
            self.pci_devices_hook.__call__(task, self.inventory,
                                           self.plugin_data)
            self.node.refresh()
            result = self.node.properties.get('capabilities', '')
            expected = 'EightyTwentyTwo:1,EightyEighteen:1'
            self.assertEqual(expected, result)

    def test_pci_devices_missing_ids(self):
        # A BMC may not report the IDs of a device
        self.inventory = {'pci_devices': [
            {'device_id': '2922', 'class': 'Bridge'},
            {'vendor_id': '8086', 'class': 'Bridge'},
            {'vendor_id': '1b36', 'device_id': '000c'},
        ]}
        self.plugin_data = {}
        with task_manager.acquire(self.context, self.node.id) as task:
            self.pci_devices_hook.__call__(task, self.inventory,
                                           self.plugin_data)
            self.node.refresh()
            result = self.node.properties.get('capabilities', '')
            self.assertEqual('OneBZeroC:1', result)


class ParsePciAliasEntriesTestCase(base.TestCase):

    def test_parse_device_id(self):
        CONF.set_override('pci_device_alias', [
            '{"vendor_id": "8086", "device_id": "2922", "name": "dev1"}',
        ], group='inspector')
        self.assertEqual({('8086', '2922'): 'dev1'},
                         pci_devices_hook._parse_pci_alias_entries())

    @mock.patch.object(pci_devices_hook.LOG, 'warning', autospec=True)
    def test_parse_deprecated_product_id(self, mock_warn):
        CONF.set_override('pci_device_alias', [
            '{"vendor_id": "8086", "product_id": "2922", "name": "dev1"}',
        ], group='inspector')
        self.assertEqual({('8086', '2922'): 'dev1'},
                         pci_devices_hook._parse_pci_alias_entries())
        mock_warn.assert_called_once()

    @mock.patch.object(pci_devices_hook.LOG, 'error', autospec=True)
    def test_parse_device_id_and_product_id(self, mock_error):
        CONF.set_override('pci_device_alias', [
            '{"vendor_id": "8086", "device_id": "2922", '
            '"product_id": "2922", "name": "dev1"}',
        ], group='inspector')
        self.assertEqual({}, pci_devices_hook._parse_pci_alias_entries())
        mock_error.assert_called_once()
