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

import os

import mock

from ironic.common import exception
from ironic.common import states
from ironic.conductor import task_manager
from ironic.db import api as dbapi
from ironic.drivers.modules import agent
from ironic.openstack.common import context
from ironic.tests.conductor import utils as mgr_utils
from ironic.tests.db import base as db_base
from ironic.tests.db import utils as db_utils
from ironic.tests.objects import utils as object_utils


INSTANCE_INFO = db_utils.get_test_agent_instance_info()
DRIVER_INFO = db_utils.get_test_agent_driver_info()


class FakeTask(object):
    def __init__(self):
        self.drivername = 'fake_pxe'
        self.context = {}


class TestAgentDeploy(db_base.DbTestCase):
    def setUp(self):
        super(TestAgentDeploy, self).setUp()
        mgr_utils.mock_the_extension_manager(driver="fake_pxe")
        self.dbapi = dbapi.get_instance()
        self.driver = agent.AgentDeploy()
        self.task = FakeTask()
        self.context = context.get_admin_context()
        self.task.context = self.context
        n = {
              'driver': 'fake_pxe',
              'instance_info': INSTANCE_INFO,
              'driver_info': DRIVER_INFO
        }
        self.node = object_utils.create_test_node(self.context, **n)

    def _create_test_port(self, **kwargs):
        p = db_utils.get_test_port(**kwargs)
        return self.dbapi.create_port(p)

    def test_validate(self):
        self.driver.validate(self.context, self.node)

    def test_validate_fail(self):
        del self.node.driver_info['agent_url']
        self.assertRaises(exception.InvalidParameterValue,
                          self.driver.validate,
                          self.task,
                          self.node)

    @mock.patch('ironic.conductor.utils.node_set_boot_device')
    @mock.patch('ironic.conductor.utils.node_power_action')
    def test_deploy_with_power_off(self, power_mock, bootdev_mock):
        self.node.power_state = states.POWER_OFF
        driver_return = self.driver.deploy(self.task, self.node)
        self.assertEqual(driver_return, states.DEPLOYWAIT)
        bootdev_mock.assert_called_once_with(self.task, 'pxe')
        power_mock.assert_called_once_with(self.task,
                                           self.node,
                                           states.POWER_ON)

    @mock.patch('ironic.conductor.utils.node_set_boot_device')
    @mock.patch('ironic.conductor.utils.node_power_action')
    def test_deploy_with_power_on(self, power_mock, bootdev_mock):
        self.node.power_state = states.POWER_ON
        driver_return = self.driver.deploy(self.task, self.node)
        self.assertEqual(driver_return, states.DEPLOYWAIT)
        self.assertEqual(0, power_mock.call_count)
        self.assertEqual(0, bootdev_mock.call_count)

    @mock.patch('ironic.common.neutron.update_neutron')
    def test_take_over(self, update_neutron_mock):
        self.config(dhcp_provider='neutron', group='agent')
        with task_manager.acquire(
                self.context, self.node['uuid'], shared=True) as task:
            task.driver.deploy.take_over(task, self.node)
            update_neutron_mock.assert_called_once_with(task, self.node)

    @mock.patch('ironic.drivers.modules.agent._get_neutron_client')
    @mock.patch('ironic.conductor.utils.node_set_boot_device')
    @mock.patch('ironic.conductor.utils.node_power_action')
    def test_tear_down(self, power_mock, bootdev_mock, neutron_mock):
        driver_return = self.driver.tear_down(self.task, self.node)
        expected_power_calls = [
            mock.call(self.task, self.node, states.POWER_OFF),
            mock.call(self.task, self.node, states.POWER_ON)
        ]
        power_mock.assert_has_calls(expected_power_calls)
        bootdev_mock.assert_called_with(self.task, 'pxe', persistent=True)
        neutron_mock.assert_called_with(self.task.context)

        self.assertEqual(driver_return, states.DELETING)

    @mock.patch('ironic.drivers.utils.get_node_mac_addresses')
    @mock.patch('os.unlink')
    def test_clean_up(self, unlink_mock, node_macs_mock):
        self.config(tftp_root='/tftpboot', group='tftp')
        mac_addr = 'aa:bb:cc:dd:ee:ff'
        port = self.dbapi.create_port(
            db_utils.get_test_port(
            id=6,
            address=mac_addr,
            uuid='bb43dc0b-03f2-4d2e-ae87-c02d7f33cc53',
            node_id='123'))
        node_macs_mock.return_value = [port.address]

        pxe_config_file = '/tftpboot/%s/config' % self.node['uuid']
        mac_config_file = os.path.join(
            '/tftpboot/'
            'pxelinux.cfg',
            "01-" + mac_addr.replace(":", "-").lower()
        )
        with task_manager.acquire(self.context, [self.node['uuid']],
                                  shared=True) as task:
            self.driver.clean_up(task, self.node)
        calls = [
            mock.call(pxe_config_file),
            mock.call(mac_config_file),
        ]
        unlink_mock.assert_has_calls(calls)


class TestAgentVendor(db_base.DbTestCase):
    def setUp(self):
        super(TestAgentVendor, self).setUp()
        mgr_utils.mock_the_extension_manager(driver="fake_pxe")
        self.dbapi = dbapi.get_instance()
        self.passthru = agent.AgentVendorInterface()
        self.passthru.db_connection = mock.Mock(autospec=True)
        self.task = FakeTask()
        self.context = context.get_admin_context()
        self.task.context = self.context
        n = {
              'driver': 'fake_pxe',
              'instance_info': INSTANCE_INFO,
              'driver_info': DRIVER_INFO
        }
        self.node = object_utils.create_test_node(self.context, **n)
        self.task.node = self.node

    def _create_test_port(self, **kwargs):
        p = db_utils.get_test_port(**kwargs)
        return self.dbapi.create_port(p)

    def test_validate(self):
        with task_manager.acquire(self.context, self.node.uuid) as task:
            self.passthru.validate(task)

    @mock.patch('ironic.common.image_service.Service')
    def test_continue_deploy(self, image_service_mock):
        instance_info = db_utils.get_test_agent_instance_info()
        test_temp_url = 'swift+http://example.com/v2.0/container/fake-uuid'
        expected_image_info = {'urls': [test_temp_url]}

        client_mock = mock.Mock()
        glance_mock = mock.Mock()
        glance_mock.show.return_value = {}
        glance_mock.swift_temp_url.return_value = test_temp_url
        image_service_mock.return_value = glance_mock

        self.passthru._client = client_mock
        with task_manager.acquire(self.context, [self.node.uuid],
                                      shared=True) as task:
            self.passthru._continue_deploy(task)

            client_mock.prepare_image.assert_called_with(task.node,
                expected_image_info,
                instance_info['configdrive'])
            self.assertEqual(task.node.provision_state, states.DEPLOYING)

    @mock.patch('ironic.drivers.modules.agent.AgentVendorInterface._lookup',
                autospec=True)
    def test_lookup_unversioned_success(self, mocked_lookup):
        kwargs = {
            'hardware': [],
        }
        with task_manager.acquire(self.context, [self.node.uuid]) as task:
            self.passthru._lookup(task, **kwargs)
            mocked_lookup.assert_called_once_with(
                self.passthru, task, **kwargs)

    def test_lookup_version_not_found(self):
        kwargs = {
            'version': '999',
        }
        with task_manager.acquire(self.context, [self.node.uuid]) as task:
            self.assertRaises(exception.InvalidParameterValue,
                              self.passthru._lookup,
                              task.context,
                              **kwargs)

    @mock.patch('ironic.drivers.modules.agent.AgentVendorInterface'
                '._find_node_by_macs')
    def test_lookup_v0(self, find_mock):
        kwargs = {
            'hardware': [
                {
                    'id': 'aa:bb:cc:dd:ee:fa',
                    'type': 'mac_address'
                },
                {
                    'id': 'ff:ee:dd:cc:bb:aa',
                    'type': 'mac_address'
                }

                ]
        }
        find_mock.return_value = self.node
        with task_manager.acquire(self.context, [self.node.uuid]) as task:
            node = self.passthru._lookup(task.context, **kwargs)
        self.assertEqual(self.node, node['node'])

    def test_lookup_v0_bad_kwargs(self):
        with task_manager.acquire(self.context, [self.node.uuid]) as task:
            self.assertRaises(exception.InvalidParameterValue,
                              self.passthru._lookup,
                              task.context)

    @mock.patch('ironic.drivers.modules.agent.AgentVendorInterface'
                '._find_node_by_macs')
    def test_lookup_v1(self, find_mock):
        kwargs = {
            'version': '1',
            'inventory': [
                {
                    'id': 'aa:bb:cc:dd:ee:ff',
                    'type': 'mac_address'
                },
                {
                    'id': 'ff:ee:dd:cc:bb:aa',
                    'type': 'mac_address'
                }

                ]
        }
        find_mock.return_value = self.node

        with task_manager.acquire(self.context, [self.node.uuid]) as task:
            node = self.passthru._lookup(task.context, **kwargs)
        self.assertEqual(self.node, node['node'])

    def test_lookup_v1_missing_inventory(self):
        with task_manager.acquire(self.context, [self.node.uuid]) as task:
            self.assertRaises(exception.InvalidParameterValue,
                              self.passthru._lookup,
                              task.context)

    def test_lookup_v1_empty_inventory(self):
        with task_manager.acquire(self.context, [self.node.uuid]) as task:
            self.assertRaises(exception.InvalidParameterValue,
                              self.passthru._lookup,
                              task.context,
                              inventory={})

    @mock.patch('ironic.drivers.modules.agent.AgentVendorInterface'
                '._find_node_by_macs')
    def test_lookup_v2(self, find_mock):
        kwargs = {
            'version': '2',
            'inventory': {
                'interfaces': [
                    {
                        'mac_address': 'aa:bb:cc:dd:ee:ff',
                        'name': 'eth0'
                    },
                    {
                        'mac_address': 'ff:ee:dd:cc:bb:aa',
                        'name': 'eth1'
                    }

                ]
            }
        }
        find_mock.return_value = self.node
        with task_manager.acquire(self.context, [self.node.uuid]) as task:
            node = self.passthru._lookup(task.context, **kwargs)
        self.assertEqual(self.node, node['node'])

    def test_lookup_v2_missing_inventory(self):
        with task_manager.acquire(self.context, [self.node.uuid]) as task:
            self.assertRaises(exception.InvalidParameterValue,
                              self.passthru._lookup,
                              task.context)

    def test_lookup_v2_empty_inventory(self):
        with task_manager.acquire(self.context, [self.node.uuid]) as task:
            self.assertRaises(exception.InvalidParameterValue,
                              self.passthru._lookup,
                              task.context,
                              inventory={})

    def test_lookup_v2_empty_interfaces(self):
        with task_manager.acquire(self.context, [self.node.uuid]) as task:
            self.assertRaises(exception.NodeNotFound,
                              self.passthru._lookup,
                              task.context,
                              version='2',
                              inventory={'interfaces': []})

    def test_find_ports_by_macs(self):
        fake_port = self._create_test_port()

        macs = ['aa:bb:cc:dd:ee:ff']

        self.passthru.dbapi = mock.Mock()
        self.passthru.dbapi.get_port.return_value = fake_port

        ports = self.passthru._find_ports_by_macs(FakeTask, macs)
        self.assertEqual(1, len(ports))
        self.assertEqual(fake_port.uuid, ports[0].uuid)
        self.assertEqual(fake_port.node_id, ports[0].node_id)

    def test_find_ports_by_macs_bad_params(self):
        self.passthru.dbapi = mock.Mock()
        self.passthru.dbapi.get_port.side_effect = exception.PortNotFound(
            port="123")

        macs = ['aa:bb:cc:dd:ee:ff']
        empty_ids = self.passthru._find_ports_by_macs(FakeTask, macs)
        self.assertEqual([], empty_ids)

    @mock.patch('ironic.objects.node.Node.get_by_id')
    @mock.patch('ironic.drivers.modules.agent.AgentVendorInterface'
                '._get_node_id')
    @mock.patch('ironic.drivers.modules.agent.AgentVendorInterface'
                '._find_ports_by_macs')
    def test_find_node_by_macs(self, ports_mock, node_id_mock, node_mock):
        ports_mock.return_value = [self._create_test_port()]
        node_id_mock.return_value = '1'
        node_mock.return_value = self.node

        macs = ['aa:bb:cc:dd:ee:ff']
        node = self.passthru._find_node_by_macs(FakeTask(), macs)
        self.assertEqual(node, node)

    @mock.patch('ironic.drivers.modules.agent.AgentVendorInterface'
                '._find_ports_by_macs')
    def test_find_node_by_macs_no_ports(self, ports_mock):
        ports_mock.return_value = []

        macs = ['aa:bb:cc:dd:ee:ff']
        self.assertRaises(exception.NodeNotFound,
                          self.passthru._find_node_by_macs,
                          FakeTask(),
                          macs)

    @mock.patch('ironic.objects.node.Node.get_by_uuid')
    @mock.patch('ironic.drivers.modules.agent.AgentVendorInterface'
                '._get_node_id')
    @mock.patch('ironic.drivers.modules.agent.AgentVendorInterface'
                '._find_ports_by_macs')
    def test_find_node_by_macs_nodenotfound(self, ports_mock, node_id_mock,
                                            node_mock):
        port = self._create_test_port()
        ports_mock.return_value = [port]
        node_id_mock.return_value = 'fake-uuid'
        node_mock.side_effect = exception.NodeNotFound(node="123")

        macs = ['aa:bb:cc:dd:ee:ff']
        self.assertRaises(exception.NodeNotFound,
                          self.passthru._find_node_by_macs,
                          FakeTask(),
                          macs)

    def test_get_node_id(self):
        fake_port1 = self._create_test_port(node_id=123,
                                            address="aa:bb:cc:dd:ee:fe")
        fake_port2 = self._create_test_port(node_id=123,
                                            id=42,
                                            address="aa:bb:cc:dd:ee:fb",
                                            uuid='1be26c0b-03f2-4d2e-ae87-c02'
                                                 'd7f33c782')

        node_id = self.passthru._get_node_id([fake_port1, fake_port2])
        self.assertEqual(fake_port2.node_id, node_id)

    def test_get_node_id_exception(self):
        fake_port1 = self._create_test_port(node_id=123,
                                            address="aa:bb:cc:dd:ee:fc")
        fake_port2 = self._create_test_port(node_id=321,
                                            id=42,
                                            address="aa:bb:cc:dd:ee:fd",
                                            uuid='1be26c0b-03f2-4d2e-ae87-c02'
                                                 'd7f33c782')

        self.assertRaises(exception.NodeNotFound,
                          self.passthru._get_node_id,
                          [fake_port1, fake_port2])

    def test_heartbeat(self):
        kwargs = {
            'agent_url': 'http://127.0.0.1:9999/bar'
        }
        with task_manager.acquire(
                self.context, self.node['uuid'], shared=True) as task:
            self.passthru._heartbeat(task, **kwargs)

    @mock.patch('ironic.drivers.modules.agent_utils.flatten_dict')
    def test_save_hardware(self, flatten_mock):
        hardware = {
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

        flatten_mock.return_value = {
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

        expected_hardware = {
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
        with task_manager.acquire(
                self.context, self.node['uuid'], shared=True) as task:
            self.passthru._save_hardware(task.context, task.node, hardware,
                                         version=2)
            self.assertEqual(task.node.extra, expected_hardware)
