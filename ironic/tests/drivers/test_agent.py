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
from sqlalchemy.orm import exc as db_exc

from ironic.common import exception
from ironic.common import states
from ironic.drivers.modules import agent
from ironic.openstack.common import test


class FakeNode(object):
    provision_state = states.NOSTATE
    target_provision_state = states.NOSTATE

    def __init__(self, driver_info=None, instance_info=None, uuid=None):
        if driver_info:
            self.driver_info = driver_info
        else:
            self.driver_info = {}
        if instance_info:
            self.instance_info = instance_info
        else:
            self.instance_info = {
                'agent_url': 'http://127.0.0.1/foo',
                'image_source': 'fake-image',
                'configdrive': 'abc123'
            }
        if uuid:
            self.uuid = uuid
        else:
            self.uuid = None

    def save(self, context):
        pass


class FakeTask(object):
    def __init__(self):
        self.drivername = "fake"
        self.context = {}


class FakePort(object):
    def __init__(self, uuid=None, node_id=None):
        self.uuid = uuid or 'fake-uuid'
        self.node_id = node_id or 'fake-node'

    def save(self, context):
        pass


class TestAgentDeploy(test.BaseTestCase):
    def setUp(self):
        super(TestAgentDeploy, self).setUp()
        self.driver = agent.AgentDeploy()
        self.task = FakeTask()

    def test_validate(self):
        self.driver.validate(FakeTask(), FakeNode())

    def test_validate_fail(self):
        node = FakeNode()
        del node.instance_info['agent_url']
        self.assertRaises(exception.InvalidParameterValue,
                          self.driver.validate,
                          FakeTask(),
                          node)

    @mock.patch('ironic.conductor.utils.node_set_boot_device')
    @mock.patch('ironic.conductor.utils.node_power_action')
    @mock.patch('ironic.common.image_service.Service')
    @mock.patch('ironic.drivers.modules.agent.AgentDeploy._get_client')
    def test_deploy(self, get_client_mock, image_service_mock, power_mock,
                    bootdev_mock):
        node = FakeNode()
        info = node.instance_info
        test_temp_url = 'swift+http://example.com/v2.0/container/fake-uuid'
        expected_image_info = {'urls': [test_temp_url]}

        client_mock = mock.Mock()

        glance_mock = mock.Mock()
        glance_mock.show.return_value = {}
        glance_mock.swift_temp_url.return_value = test_temp_url
        image_service_mock.return_value = glance_mock

        client_mock.prepare_image.return_value = None
        get_client_mock.return_value = client_mock

        driver_return = self.driver.deploy(self.task, node)
        client_mock.prepare_image.assert_called_with(node,
                                                     expected_image_info,
                                                     info['configdrive'],
                                                     wait=True)
        power_mock.assert_called_with(self.task, node, states.REBOOT)
        bootdev_mock.assert_called_with(self.task, node, 'disk')
        self.assertEqual(driver_return, states.DEPLOYDONE)

    @mock.patch('ironic.conductor.utils.node_set_boot_device')
    @mock.patch('ironic.conductor.utils.node_power_action')
    def test_tear_down(self, power_mock, bootdev_mock):
        node = FakeNode()

        driver_return = self.driver.tear_down(self.task, node)
        power_mock.assert_called_with(self.task, node, states.REBOOT)
        bootdev_mock.assert_called_with(self.task, node, 'pxe')

        self.assertEqual(driver_return, states.DELETING)

    @mock.patch('ironic.drivers.modules.agent.AgentDeploy._get_client')
    def test_prepare(self, get_client_mock):
        node = FakeNode()
        driver_return = self.driver.prepare(self.task, node)
        self.assertEqual(None, driver_return)


class TestAgentVendor(test.BaseTestCase):
    def setUp(self):
        super(TestAgentVendor, self).setUp()
        self.passthru = agent.AgentVendorInterface()
        self.passthru.db_connection = mock.Mock(autospec=True)
        port_patcher = mock.patch.object(self.passthru.db_connection,
                                        'get_port')
        self.port_mock = port_patcher.start()
        node_patcher = mock.patch.object(self.passthru.db_connection,
                                         'get_node')
        self.node_mock = node_patcher.start()
        time_patcher = mock.patch.object(agent, '_time')
        self.time_mock = time_patcher.start()
        self.fake_time = 1395964422
        self.time_mock.return_value = self.fake_time
        self.task = FakeTask()

    def test_validate(self):
        node = FakeNode()
        kwargs = {
            'agent_url': 'http://127.0.0.1:9999/bar'
        }
        self.passthru.validate(None,
                               node,
                               'deploy',
                               **kwargs)

    def test_validate_bad_params(self):
        node = FakeNode()
        self.assertRaises(exception.InvalidParameterValue,
                          self.passthru.validate,
                          None,
                          node,
                          'deploy')

    @mock.patch('ironic.drivers.modules.agent.AgentVendorInterface._lookup_v0',
                autospec=True)
    def test_lookup_unversioned_success(self, mocked_lookup_v0):
        kwargs = {
            'hardware': [],
        }
        task = FakeTask()
        self.passthru._lookup(task, **kwargs)
        mocked_lookup_v0.assert_called_once_with(self.passthru, task, **kwargs)

    def test_lookup_version_not_found(self):
        kwargs = {
            'version': '999',
        }
        self.assertRaises(exception.InvalidParameterValue,
                          self.passthru._lookup,
                          FakeTask(),
                          **kwargs)

    @mock.patch('ironic.drivers.modules.agent.AgentVendorInterface'
                '._find_node_by_macs')
    def test_lookup_v0(self, find_mock):
        kwargs = {
            'hardware': [
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
        expected_node = FakeNode(uuid='heartbeat')
        find_mock.return_value = expected_node

        node = self.passthru._lookup_v0(FakeTask(), **kwargs)
        self.assertEqual(expected_node, node['node'])

    def test_lookup_v0_bad_kwargs(self):
        self.assertRaises(exception.InvalidParameterValue,
                          self.passthru._lookup_v0,
                          FakeTask())

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
        expected_node = FakeNode(uuid='heartbeat')
        find_mock.return_value = expected_node

        node = self.passthru._lookup_v1(FakeTask(), **kwargs)
        self.assertEqual(expected_node, node['node'])

    def test_lookup_v1_missing_inventory(self):
        self.assertRaises(exception.InvalidParameterValue,
                          self.passthru._lookup_v1,
                          FakeTask())

    def test_lookup_v1_empty_inventory(self):
        self.assertRaises(exception.InvalidParameterValue,
                          self.passthru._lookup_v1,
                          FakeTask(),
                          inventory={})

    def test_find_ports_by_macs(self):
        fake_port = FakePort()

        macs = ['aa:bb:cc:dd:ee:ff']

        self.passthru.dbapi = mock.Mock()
        self.passthru.dbapi.get_port.return_value = fake_port

        ports = self.passthru._find_ports_by_macs(FakeTask, macs)
        self.assertEqual(1, len(ports))
        self.assertEqual(fake_port.uuid, ports[0].uuid)
        self.assertEqual(fake_port.node_id, ports[0].node_id)

    def test_find_ports_by_macs_bad_params(self):
        self.passthru.dbapi = mock.Mock()
        self.passthru.dbapi.get_port.side_effect = exception.PortNotFound

        macs = ['aa:bb:cc:dd:ee:ff']
        empty_ids = self.passthru._find_ports_by_macs(FakeTask, macs)
        self.assertEqual([], empty_ids)

    @mock.patch('ironic.objects.node.Node.get_by_uuid')
    @mock.patch('ironic.drivers.modules.agent.AgentVendorInterface'
                '._get_node_id')
    @mock.patch('ironic.drivers.modules.agent.AgentVendorInterface'
                '._find_ports_by_macs')
    def test_find_node_by_macs(self, ports_mock, node_id_mock, node_mock):
        ports_mock.return_value = [FakePort()]
        node_id_mock.return_value = 'c3e83a6a-f094-4c55-8480-760a44efffc6'
        fake_node = FakeNode()
        node_mock.return_value = fake_node

        macs = ['aa:bb:cc:dd:ee:ff']
        node = self.passthru._find_node_by_macs(FakeTask(), macs)
        self.assertEqual(fake_node, node)

    @mock.patch('ironic.objects.node.Node.get_by_uuid')
    @mock.patch('ironic.drivers.modules.agent.AgentVendorInterface'
                '._get_node_id')
    @mock.patch('ironic.drivers.modules.agent.AgentVendorInterface'
                '._find_ports_by_macs')
    def test_find_node_by_macs_bad_params(self, ports_mock, node_id_mock,
                                          node_mock):
        ports_mock.return_value = []
        node_id_mock.return_value = 'fake-uuid'
        node_mock.side_effect = db_exc.NoResultFound()

        macs = ['aa:bb:cc:dd:ee:ff']
        self.assertRaises(exception.NodeNotFound,
                          self.passthru._find_node_by_macs,
                          FakeTask(),
                          macs)

    def test_get_node_id(self):
        fake_port1 = FakePort(node_id='fake-uuid')
        fake_port2 = FakePort(node_id='fake-uuid')

        node_id = self.passthru._get_node_id([fake_port1, fake_port2])
        self.assertEqual(fake_port2.uuid, node_id)

    def test_get_node_id_exception(self):
        fake_port1 = FakePort(node_id='fake-uuid')
        fake_port2 = FakePort(node_id='other-fake-uuid')

        self.assertRaises(exception.NodeNotFound,
                          self.passthru._get_node_id,
                          [fake_port1, fake_port2])

    def test_heartbeat(self):
        task = FakeTask()
        fake_node = mock.MagicMock()
        fake_node.instance_info = {}
        kwargs = {
            'agent_url': 'http://127.0.0.1:9999/bar'
        }
        self.passthru._heartbeat(task, fake_node, **kwargs)
