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


from ironic.drivers.modules import agent_utils
from ironic.tests.db import base as db_base
from ironic.tests.db import utils as db_utils

INSTANCE_INFO = db_utils.get_test_agent_instance_info()
DRIVER_INFO = db_utils.get_test_agent_driver_info()


class FakeTask(object):
    def __init__(self):
        self.drivername = 'fake_pxe'
        self.context = {}


class TestAgentDeploy(db_base.DbTestCase):
    def setUp(self):
        super(TestAgentDeploy, self).setUp()

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
