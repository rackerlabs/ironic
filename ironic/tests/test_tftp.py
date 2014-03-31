#
# Copyright 2014 Rackspace, Inc
# All Rights Reserved
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

import os

import mock
from oslo.config import cfg

from ironic.common import exception
from ironic.common import neutron
from ironic.common import tftp
from ironic.conductor import task_manager
from ironic.db import api as dbapi
from ironic.openstack.common import context
from ironic.tests.conductor import utils as mgr_utils
from ironic.tests.db import base as db_base
from ironic.tests.db import utils as db_utils


CONF = cfg.CONF


class TestNetworkUtils(db_base.DbTestCase):
    def setUp(self):
        super(TestNetworkUtils, self).setUp()
        mgr_utils.mock_the_extension_manager(driver="fake")
        self.dbapi = dbapi.get_instance()
        self.node = self._create_test_node()
        self.context = context.get_admin_context()

    def _create_test_node(self, **kwargs):
        n = db_utils.get_test_node(**kwargs)
        return self.dbapi.create_node(n)

    def test_build_pxe_config(self):
        pass

    def test__get_pxe_mac_path(self):
        mac = '00:11:22:33:44:55:66'
        self.assertEqual('/tftpboot/pxelinux.cfg/01-00-11-22-33-44-55-66',
                         tftp.get_pxe_mac_path(mac))

    def test__get_pxe_config_file_path(self):
        self.assertEqual(os.path.join(CONF.tftp.tftp_root,
                                      self.node.uuid,
                                      'config'),
                         tftp.get_pxe_config_file_path(self.node.uuid))

    def test__dhcp_options_for_instance(self):
        self.config(pxe_bootfile_name='test_pxe_bootfile', group='tftp')
        self.config(tftp_server='192.0.2.1', group='tftp')
        expected_info = [{'opt_name': 'bootfile-name',
                          'opt_value': 'test_pxe_bootfile'},
                         {'opt_name': 'server-ip-address',
                          'opt_value': '192.0.2.1'},
                         {'opt_name': 'tftp-server',
                          'opt_value': '192.0.2.1'}
                         ]
        self.assertEqual(expected_info, tftp.dhcp_options_for_instance())

    def test__update_neutron(self):
        opts = tftp.dhcp_options_for_instance()
        with mock.patch.object(neutron, 'get_node_vif_ids') as mock_gnvi:
            mock_gnvi.return_value = {'port-uuid': 'vif-uuid'}
            with mock.patch.object(neutron.NeutronAPI,
                                   'update_port_dhcp_opts') as mock_updo:
                with task_manager.acquire(self.context,
                                          self.node.uuid) as task:
                    tftp.update_neutron(task, self.node)
                mock_updo.assertCalleOnceWith('vif-uuid', opts)

    def test__update_neutron_no_vif_data(self):
        with mock.patch.object(neutron, 'get_node_vif_ids') as mock_gnvi:
            mock_gnvi.return_value = {}
            with mock.patch.object(neutron.NeutronAPI,
                                   '__init__') as mock_init:
                with task_manager.acquire(self.context,
                                          self.node.uuid) as task:
                    tftp.update_neutron(task, self.node)
                mock_init.assert_not_called()

    def test__update_neutron_some_failures(self):
        # confirm update is called twice, one fails, but no exception raised
        with mock.patch.object(neutron, 'get_node_vif_ids') as mock_gnvi:
            mock_gnvi.return_value = {'p1': 'v1', 'p2': 'v2'}
            with mock.patch.object(neutron.NeutronAPI,
                                   'update_port_dhcp_opts') as mock_updo:
                exc = exception.FailedToUpdateDHCPOptOnPort('fake exception')
                mock_updo.side_effect = [None, exc]
                with task_manager.acquire(self.context,
                                          self.node.uuid) as task:
                    tftp.update_neutron(task, self.node)
                self.assertEqual(2, mock_updo.call_count)

    def test__update_neutron_fails(self):
        # confirm update is called twice, both fail, and exception is raised
        with mock.patch.object(neutron, 'get_node_vif_ids') as mock_gnvi:
            mock_gnvi.return_value = {'p1': 'v1', 'p2': 'v2'}
            with mock.patch.object(neutron.NeutronAPI,
                                   'update_port_dhcp_opts') as mock_updo:
                exc = exception.FailedToUpdateDHCPOptOnPort('fake exception')
                mock_updo.side_effect = [exc, exc]
                with task_manager.acquire(self.context,
                                          self.node.uuid) as task:
                    self.assertRaises(exception.FailedToUpdateDHCPOptOnPort,
                                      tftp.update_neutron,
                                      task, self.node)
                self.assertEqual(2, mock_updo.call_count)
