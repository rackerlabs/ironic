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

from ironic.common import exception
from ironic.db import api as dbapi
from ironic.drivers.modules import agent_utils
from ironic.openstack.common import context
from ironic.tests.db import base as db_base
from ironic.tests.db import utils as db_utils

INSTANCE_INFO = db_utils.get_test_agent_instance_info()
DRIVER_INFO = db_utils.get_test_agent_driver_info()


class FakeTask(object):
    def __init__(self):
        self.drivername = 'fake_pxe'
        self.context = {}


class TestAgentDeploy(db_base.DbTestCase):
    def test_flatten_dict(self):
        hardware = {
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
        }
        expected_hardware = {
            'interfaces/0/mac_address': 'aa:bb:cc:dd:ee:fe',
            'interfaces/0/name': 'eth0',
            'interfaces/0/switch_chassis_descr': 'tor1',
            'interfaces/0/switch_chassis_id': '1',
            'interfaces/0/switch_port_descr': 'port24',
            'interfaces/0/switch_port_id': '24',
            'interfaces/1/mac_address': 'aa:bb:cc:dd:ee:ff',
            'interfaces/1/name': 'eth1',
            'interfaces/1/switch_chassis_descr': 'tor2',
            'interfaces/1/switch_chassis_id': '2',
            'interfaces/1/switch_port_descr': 'port24',
            'interfaces/1/switch_port_id': '24',
        }

        flattened_hardware = agent_utils.flatten_dict(hardware)
        self.assertEqual(expected_hardware, flattened_hardware)

    def test_unflatten_dict(self):
        # TODO(JoshNang) fix unflatten
        expected_hardware = {
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
        }
        hardware = {
            'interfaces/0/mac_address': 'aa:bb:cc:dd:ee:fe',
            'interfaces/0/name': 'eth0',
            'interfaces/0/switch_chassis_descr': 'tor1',
            'interfaces/0/switch_chassis_id': '1',
            'interfaces/0/switch_port_descr': 'port24',
            'interfaces/0/switch_port_id': '24',
            'interfaces/1/mac_address': 'aa:bb:cc:dd:ee:ff',
            'interfaces/1/name': 'eth1',
            'interfaces/1/switch_chassis_descr': 'tor2',
            'interfaces/1/switch_chassis_id': '2',
            'interfaces/1/switch_port_descr': 'port24',
            'interfaces/1/switch_port_id': '24',
        }

        returned_hardware = agent_utils.unflatten_dict(hardware)
        self.assertEqual(expected_hardware, returned_hardware)


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
        self.node = self._create_test_node(**n)

        self.api = agent_utils.AgentNeutronAPI(self.context)
        self.flattened_hardware = {
            'interfaces/0/mac_address': 'aa:bb:cc:dd:ee:fe',
            'interfaces/0/name': 'eth0',
            'interfaces/0/switch_chassis_descr': 'tor1',
            'interfaces/0/switch_chassis_id': '1',
            'interfaces/0/switch_port_descr': 'port24',
            'interfaces/0/switch_port_id': '24',
            'interfaces/1/mac_address': 'aa:bb:cc:dd:ee:ff',
            'interfaces/1/name': 'eth1',
            'interfaces/1/switch_chassis_descr': 'tor2',
            'interfaces/1/switch_chassis_id': '2',
            'interfaces/1/switch_port_descr': 'port24',
            'interfaces/1/switch_port_id': '24',
        }
        self.unflattened_hardware = {
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
            }

        }

    def _create_test_node(self, **kwargs):
        n = db_utils.get_test_node(**kwargs)
        return self.dbapi.create_node(n)

    @mock.patch('ironic.drivers.modules.agent_utils.unflatten_dict')
    def test_get_node_portmap(self, unflatten_mock):
        unflatten_mock.return_value = self.unflattened_hardware
        expected_portmap = [{'port_id': '24', 'system_name': '1'},
                            {'port_id': '24', 'system_name': '2'}]
        portmap = self.api._get_node_portmap(self.node)
        self.assertEqual(expected_portmap, portmap)

    @mock.patch('ironic.drivers.modules.agent_utils.unflatten_dict')
    def test_get_node_portmap_invalid(self, unflatten_mock):
        unflatten_mock.return_value = {}
        self.assertRaises(exception.InvalidParameterValue,
                          self.api._get_node_portmap,
                          self.node)

    #TODO(JoshNang) Add tests for neutron calls
