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

from oslo.config import cfg

from ironic.common import tftp
from ironic.db import api as dbapi
from ironic.openstack.common import context
from ironic.tests.conductor import utils as mgr_utils
from ironic.tests.db import base as db_base
from ironic.tests.objects import utils as object_utils

CONF = cfg.CONF


class TestNetworkUtils(db_base.DbTestCase):
    def setUp(self):
        super(TestNetworkUtils, self).setUp()
        mgr_utils.mock_the_extension_manager(driver="fake")
        self.dbapi = dbapi.get_instance()
        self.context = context.get_admin_context()
        self.node = object_utils.create_test_node(self.context)

    #TODO(JoshNang) add tests here
    def test_build_pxe_config(self):
        pass

    def test_create_pxe_config(self):
        pass

    def test_get_pxe_mac_path(self):
        mac = '00:11:22:33:44:55:66'
        self.assertEqual('/tftpboot/pxelinux.cfg/01-00-11-22-33-44-55-66',
                         tftp.get_pxe_mac_path(mac))

    def test_get_pxe_config_file_path(self):
        self.assertEqual(os.path.join(CONF.tftp.tftp_root,
                                      self.node.uuid,
                                      'config'),
                         tftp.get_pxe_config_file_path(self.node.uuid))

    def test_dhcp_options_for_instance(self):
        self.config(tftp_server='192.0.2.1', group='tftp')
        expected_info = [{'opt_name': 'bootfile-name',
                          'opt_value': CONF.pxe.pxe_bootfile_name},
                         {'opt_name': 'server-ip-address',
                          'opt_value': '192.0.2.1'},
                         {'opt_name': 'tftp-server',
                          'opt_value': '192.0.2.1'}
                         ]
        self.assertEqual(expected_info, tftp.dhcp_options_for_instance(
            CONF.pxe.pxe_bootfile_name))
