# Copyright 2014 Rackspace, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import mock
from neutronclient.common import exceptions as neutron_exceptions

from ironic.common import exception
from ironic.db import api as dbapi
from ironic.drivers.modules import agent_utils
from ironic.openstack.common import context
from ironic.tests.db import base as db_base
from ironic.tests.db import utils as db_utils
from ironic.tests.objects import utils as object_utils


INSTANCE_INFO = db_utils.get_test_agent_instance_info()
DRIVER_INFO = db_utils.get_test_agent_driver_info()


unflattened_hardware = {
    'hardware': {
        'interfaces': [
            {
                'mac_address': 'aa:bb:cc:dd:ee:fe',
                'name': 'eth0',
                'switch_port_descr': 'port24',
                'switch_port_id': '24',
                'switch_chassis_descr': 'tor1',
                'switch_chassis_id': '1',
            },
            {
                'mac_address': 'aa:bb:cc:dd:ee:ff',
                'name': 'eth1',
                'switch_port_descr': 'port24',
                'switch_port_id': '24',
                'switch_chassis_descr': 'tor2',
                'switch_chassis_id': '2',
            }
        ],
        'memory': {
            'size': '1024'
        }
    }
}


flattened_hardware = {
    'hardware/interfaces/0/mac_address': 'aa:bb:cc:dd:ee:fe',
    'hardware/interfaces/0/name': 'eth0',
    'hardware/interfaces/0/switch_chassis_descr': 'tor1',
    'hardware/interfaces/0/switch_chassis_id': '1',
    'hardware/interfaces/0/switch_port_descr': 'port24',
    'hardware/interfaces/0/switch_port_id': '24',
    'hardware/interfaces/1/mac_address': 'aa:bb:cc:dd:ee:ff',
    'hardware/interfaces/1/name': 'eth1',
    'hardware/interfaces/1/switch_chassis_descr': 'tor2',
    'hardware/interfaces/1/switch_chassis_id': '2',
    'hardware/interfaces/1/switch_port_descr': 'port24',
    'hardware/interfaces/1/switch_port_id': '24',
    'hardware/memory/size': '1024'
}

fake_list_ports = {
    'ports': [
        {u'status': u'ACTIVE',
         u'subnets': [u'4ed3c970-6cc8-4c2d-a04d-d473c804f659'],
         u'name': u'public',
         u'provider:physical_network': u'public',
         u'admin_state_up': True,
         u'tenant_id': u'mytenant',
         u'switch:trunked': True,
         u'provider:network_type': u'vlan',
         u'router:external': False,
         u'shared': True,
         u'id': u'b1be7c12-beba-428e-ae91-8b2b0d6539b1',
         u'provider:segmentation_id': 1
        },
        {u'status': u'ACTIVE',
         u'subnets': [u'dd42d857-7d8c-42eb-ab98-058279092b22'],
         u'name': u'private',
         u'provider:physical_network': u'private',
         u'admin_state_up': True,
         u'tenant_id': u'mytenant',
         u'switch:trunked': True,
         u'provider:network_type': u'vlan',
         u'router:external': False,
         u'shared': True,
         u'id': u'1c7a39c8-841e-46c4-8d46-6692ab731b8a',
         u'provider:segmentation_id': 2
        },
    ]
}


class TestAgentDeploy(db_base.DbTestCase):
    def test_flatten_dict(self):
        flattened = agent_utils.flatten_dict(unflattened_hardware)
        self.assertEqual(flattened_hardware, flattened)

    def test_unflatten_dict(self):
        unflattened = agent_utils.unflatten_dict(flattened_hardware)
        self.assertEqual(unflattened_hardware, unflattened)


class TestAgentNeutronAPI(db_base.DbTestCase):
    def setUp(self):
        super(TestAgentNeutronAPI, self).setUp()
        self.dbapi = dbapi.get_instance()
        self.context = context.get_admin_context()
        n = {
              'driver': 'fake_pxe',
              'instance_info': INSTANCE_INFO,
              'driver_info': DRIVER_INFO
        }
        self.node = object_utils.create_test_node(self.context, **n)
        self.api = agent_utils.AgentNeutronAPI(self.context)
        self.fake_portmap = [
            {'port_id': '24', 'system_name': '1'},
            {'port_id': '24', 'system_name': '2'}
        ]
        self.fake_ports = [
            self._create_test_port(
                node_id=self.node.id,
                address="aa:bb:cc:dd:ee:fe",
                extra={'vif_port_id': 'b1be7c12-beba-428e-ae91-8b2b0d6539b1'}),
            self._create_test_port(
                node_id=self.node.id,
                id=42,
                address="aa:bb:cc:dd:ee:fb",
                uuid='1be26c0b-03f2-4d2e-ae87-c02'
                     'd7f33c782',
                extra={'vif_port_id': '1c7a39c8-841e-46c4-8d46-6692ab731b8a'})
        ]
        self.provisioning_network_uuid = 'fa70a0d7-3a2c-4359-9399-f891cf2fb4c4'
        self.config(provisioning_network_uuid=self.provisioning_network_uuid,
                    group='agent')

    def _create_test_port(self, **kwargs):
        p = db_utils.get_test_port(**kwargs)
        return self.dbapi.create_port(p)

    @mock.patch('ironic.drivers.modules.agent_utils.unflatten_dict')
    def test_get_node_portmap(self, unflatten_mock):
        unflatten_mock.return_value = unflattened_hardware
        expected_portmap = [{'port': '24', 'switch_id': '1', 'name': 'eth0'},
                            {'port': '24', 'switch_id': '2', 'name': 'eth1'}]
        portmap = self.api._get_node_portmap(self.node)
        self.assertEqual(expected_portmap, portmap)

    @mock.patch('ironic.drivers.modules.agent_utils.unflatten_dict')
    def test_get_node_portmap_by_index(self, unflatten_mock):
        nameless_unflattened = unflattened_hardware
        del nameless_unflattened['hardware']['interfaces'][0]['name']
        del nameless_unflattened['hardware']['interfaces'][1]['name']
        unflatten_mock.return_value = nameless_unflattened
        expected_portmap = [{'port': '24', 'switch_id': '1', 'name': 'eth0'},
                            {'port': '24', 'switch_id': '2', 'name': 'eth1'}]
        portmap = self.api._get_node_portmap(self.node)
        self.assertEqual(expected_portmap, portmap)

    @mock.patch('ironic.drivers.modules.agent_utils.unflatten_dict')
    def test_get_node_portmap_invalid(self, unflatten_mock):
        unflatten_mock.return_value = {}
        self.assertRaises(exception.InvalidParameterValue,
                          self.api._get_node_portmap,
                          self.node)

    @mock.patch('neutronclient.v2_0.client.Client.list_ports')
    @mock.patch('neutronclient.v2_0.client.Client.create_port')
    @mock.patch('ironic.drivers.modules.agent_utils.AgentNeutronAPI.'
                '_get_node_portmap')
    def test_add_provisioning_network(
            self, portmap_mock, create_port_mock, list_mock):
        portmap_mock.return_value = self.fake_portmap
        expected_params = {
            'port': {
                'switch:ports': self.fake_portmap,
                'switch:hardware_id': self.node.uuid,
                'commit': True,
                'trunked': False,
                'network_id': self.provisioning_network_uuid,
                'tenant_id': 'fake'
            }
        }
        self.api.add_provisioning_network(self.node)
        create_port_mock.assert_called_with(expected_params)

    @mock.patch('neutronclient.v2_0.client.Client.list_ports')
    @mock.patch('ironic.drivers.modules.agent_utils.AgentNeutronAPI.'
                '_get_node_portmap')
    def test_add_provisioning_network_no_portmap(
            self, portmap_mock, list_mock):
        portmap_mock.return_value = []
        self.assertRaises(exception.NoValidPortmaps,
                          self.api.add_provisioning_network,
                          self.node)

    @mock.patch('neutronclient.v2_0.client.Client.list_ports')
    @mock.patch('neutronclient.v2_0.client.Client.create_port')
    @mock.patch('ironic.drivers.modules.agent_utils.AgentNeutronAPI.'
                '_get_node_portmap')
    def test_add_provisioning_network_connection_failed(
            self, portmap_mock, create_port_mock, list_mock):
        portmap_mock.return_value = self.fake_portmap
        create_port_mock.side_effect = neutron_exceptions.ConnectionFailed
        self.assertRaises(exception.NetworkError,
                          self.api.add_provisioning_network,
                          self.node)

    @mock.patch('neutronclient.v2_0.client.Client.delete_port')
    @mock.patch('neutronclient.v2_0.client.Client.list_ports')
    def test_remove_provisioning_network(self, list_ports_mock,
                                         delete_port_mock):
        list_ports_mock.return_value = fake_list_ports
        delete_calls = [
            mock.call(fake_list_ports['ports'][0]['id']),
            mock.call(fake_list_ports['ports'][1]['id'])
        ]
        self.api.remove_provisioning_network(self.node)
        delete_port_mock.assert_has_calls(delete_calls)

    @mock.patch('neutronclient.v2_0.client.Client.list_ports')
    def test_remove_provisioning_network_list_connection_failed(
            self, list_ports_mock):
        list_ports_mock.side_effect = neutron_exceptions.ConnectionFailed
        self.assertRaises(exception.NetworkError,
                          self.api.remove_provisioning_network,
                          self.node)

    @mock.patch('neutronclient.v2_0.client.Client.delete_port')
    @mock.patch('neutronclient.v2_0.client.Client.list_ports')
    def test_remove_provisioning_network_delete_connection_failed(
            self, list_ports_mock, delete_port_mock):
        list_ports_mock.return_value = fake_list_ports
        delete_port_mock.side_effect = neutron_exceptions.ConnectionFailed
        self.assertRaises(exception.NetworkError,
                          self.api.remove_provisioning_network,
                          self.node)

    @mock.patch('neutronclient.v2_0.client.Client.update_port')
    @mock.patch('ironic.drivers.modules.agent_utils.AgentNeutronAPI.'
                '_get_node_portmap')
    def test_configure_instance_networks(self, portmap_mock, update_port_mock):
        portmap_mock.return_value = self.fake_portmap
        self.api.dbapi = mock.Mock()
        self.api.dbapi.get_ports_by_node_id.return_value = self.fake_ports
        expected_params = {
            'port': {
                'switch:ports': self.fake_portmap,
                'switch:hardware_id': self.node.uuid,
                'commit': True,
                'trunked': True
            }
        }
        self.api.configure_instance_networks(self.node)
        update_calls = [
            mock.call(fake_list_ports['ports'][0]['id'],
                      expected_params),
            mock.call(fake_list_ports['ports'][1]['id'],
                      expected_params)
        ]
        update_port_mock.assert_has_calls(update_calls)

    @mock.patch('ironic.drivers.modules.agent_utils.AgentNeutronAPI.'
                '_get_node_portmap')
    def test_configure_instance_networks_no_portmap(self, portmap_mock):
        portmap_mock.return_value = []
        self.assertRaises(exception.NoValidPortmaps,
                          self.api.configure_instance_networks,
                          self.node)

    @mock.patch('ironic.drivers.modules.agent_utils.AgentNeutronAPI.'
                '_get_node_portmap')
    def test_configure_instance_networks_no_ports(self, portmap_mock):
        portmap_mock.return_value = self.fake_portmap
        self.api.dbapi = mock.Mock()
        self.api.dbapi.get_ports_by_node_id.return_value = []
        self.assertRaises(exception.NetworkError,
                          self.api.configure_instance_networks,
                          self.node)

    @mock.patch('neutronclient.v2_0.client.Client.update_port')
    @mock.patch('ironic.drivers.modules.agent_utils.AgentNeutronAPI.'
                '_get_node_portmap')
    def test_configure_instance_networks_connection_failed(
            self, portmap_mock, update_port_mock):
        portmap_mock.return_value = self.fake_portmap
        self.api.dbapi = mock.Mock()
        self.api.dbapi.get_ports_by_node_id.return_value = self.fake_ports
        update_port_mock.side_effect = neutron_exceptions.ConnectionFailed
        self.assertRaises(exception.NetworkError,
                          self.api.configure_instance_networks,
                          self.node)

    @mock.patch('neutronclient.v2_0.client.Client.list_ports')
    @mock.patch('neutronclient.v2_0.client.Client.update_port')
    def test_deconfigure_instance_networks(self, update_port_mock, list_mock):
        list_mock.return_value = {'ports': [
            {'id': self.fake_ports[0]['extra']['vif_port_id'],
             'network_id': '00000000-0000-0000-0000-000000000000'},
            {'id': self.fake_ports[1]['extra']['vif_port_id'],
             'network_id': '11111111-1111-1111-1111-111111111111'}
        ]}
        expected_params = {
            'port': {
                'switch:ports': [],
                'switch:hardware_id': self.node.uuid,
                'commit': False
            }
        }
        self.api.deconfigure_instance_networks(self.node)
        update_calls = [
            mock.call(fake_list_ports['ports'][0]['id'],
                      expected_params),
            mock.call(fake_list_ports['ports'][1]['id'],
                      expected_params)
        ]
        update_port_mock.assert_has_calls(update_calls)

    @mock.patch('neutronclient.v2_0.client.Client.list_ports')
    def test_deconfigure_instance_networks_no_ports(self, list_mock):
        list_mock.return_value = {'ports': []}
        self.assertRaises(exception.NetworkError,
                          self.api.deconfigure_instance_networks,
                          self.node)

    @mock.patch('neutronclient.v2_0.client.Client.list_ports')
    @mock.patch('neutronclient.v2_0.client.Client.update_port')
    def test_deconfigure_instance_networks_connection_failed(
            self, update_port_mock, list_mock):
        list_mock.return_value = {'ports': [
            {'id': self.fake_ports[0]['extra']['vif_port_id'],
             'network_id': '00000000-0000-0000-0000-000000000000'},
            {'id': self.fake_ports[1]['extra']['vif_port_id'],
             'network_id': '11111111-1111-1111-1111-111111111111'}
        ]}
        update_port_mock.side_effect = neutron_exceptions.ConnectionFailed
        self.assertRaises(exception.NetworkError,
                          self.api.deconfigure_instance_networks,
                          self.node)
