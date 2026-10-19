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

"""Tests for manipulating BMCSetting via the DB API"""

from ironic.common import exception
from ironic.tests.unit.db import base
from ironic.tests.unit.db import utils as db_utils


class DbBMCSettingTestCase(base.DbTestCase):

    def setUp(self):
        super(DbBMCSettingTestCase, self).setUp()
        self.node = db_utils.create_test_node()

    def test_get_bmc_setting(self):
        db_utils.create_test_bmc_setting(node_id=self.node.id)
        result = self.dbapi.get_bmc_setting(self.node.id, 'IPMI1_Enable')
        self.assertEqual(result['node_id'], self.node.id)
        self.assertEqual(result['name'], 'IPMI1_Enable')
        self.assertEqual(result['value'], 'Disabled')
        self.assertEqual(result['version'], '1.0')

    def test_get_bmc_setting_node_not_exist(self):
        self.assertRaises(exception.NodeNotFound,
                          self.dbapi.get_bmc_setting,
                          '456',
                          'IPMI1_Enable')

    def test_get_bmc_setting_setting_not_exist(self):
        db_utils.create_test_bmc_setting(node_id=self.node.id)
        self.assertRaises(exception.BMCSettingNotFound,
                          self.dbapi.get_bmc_setting,
                          self.node.id, 'bmc_name')

    def test_get_bmc_setting_list(self):
        db_utils.create_test_bmc_setting(node_id=self.node.id)
        result = self.dbapi.get_bmc_setting_list(
            node_id=self.node.id)
        self.assertEqual(result[0]['node_id'], self.node.id)
        self.assertEqual(result[0]['name'], 'IPMI1_Enable')
        self.assertEqual(result[0]['value'], 'Disabled')
        self.assertEqual(result[0]['version'], '1.0')
        self.assertEqual(len(result), 1)

    def test_get_bmc_setting_list_node_not_exist(self):
        self.assertRaises(exception.NodeNotFound,
                          self.dbapi.get_bmc_setting_list,
                          '456')

    def test_create_bmc_setting_list(self):
        settings = db_utils.get_test_bmc_setting_list()
        result = self.dbapi.create_bmc_setting_list(
            self.node.id, settings, '1.0')
        self.assertCountEqual(
            ['IPMI1_Enable', 'SSH_Enable', 'Telnet_Enable'],
            [setting.name for setting in result])
        self.assertCountEqual(['Disabled', 'Enabled', 'Disabled'],
                              [setting.value for setting in result])

    def test_create_bmc_setting_list_duplicate(self):
        settings = db_utils.get_test_bmc_setting_list()
        self.dbapi.create_bmc_setting_list(self.node.id, settings, '1.0')
        self.assertRaises(exception.BMCSettingAlreadyExists,
                          self.dbapi.create_bmc_setting_list,
                          self.node.id, settings, '1.0')

    def test_create_bmc_setting_list_node_not_exist(self):
        self.assertRaises(exception.NodeNotFound,
                          self.dbapi.create_bmc_setting_list,
                          '456', [], '1.0')

    def test_update_bmc_setting_list(self):
        settings = db_utils.get_test_bmc_setting_list()
        self.dbapi.create_bmc_setting_list(self.node.id, settings, '1.0')
        settings = [{'name': 'IPMI1_Enable', 'value': 'Enabled'},
                    {'name': 'SSH_Enable', 'value': 'Disabled'},
                    {'name': 'Telnet_Enable', 'value': 'Enabled'}]
        result = self.dbapi.update_bmc_setting_list(
            self.node.id, settings, '1.0')
        self.assertCountEqual(['Enabled', 'Disabled', 'Enabled'],
                              [setting.value for setting in result])

    def test_update_bmc_setting_list_setting_not_exist(self):
        settings = db_utils.get_test_bmc_setting_list()
        self.dbapi.create_bmc_setting_list(self.node.id, settings, '1.0')
        for setting in settings:
            setting['name'] = 'bmc_name'
        self.assertRaises(exception.BMCSettingNotFound,
                          self.dbapi.update_bmc_setting_list,
                          self.node.id, settings, '1.0')

    def test_update_bmc_setting_list_node_not_exist(self):
        self.assertRaises(exception.NodeNotFound,
                          self.dbapi.update_bmc_setting_list,
                          '456', [], '1.0')

    def test_delete_bmc_setting_list(self):
        settings = db_utils.get_test_bmc_setting_list()
        self.dbapi.create_bmc_setting_list(self.node.id, settings, '1.0')
        name_list = [setting['name'] for setting in settings]
        self.dbapi.delete_bmc_setting_list(self.node.id, name_list)
        self.assertRaises(exception.BMCSettingNotFound,
                          self.dbapi.get_bmc_setting,
                          self.node.id, 'IPMI1_Enable')
        self.assertRaises(exception.BMCSettingNotFound,
                          self.dbapi.get_bmc_setting,
                          self.node.id, 'SSH_Enable')
        self.assertRaises(exception.BMCSettingNotFound,
                          self.dbapi.get_bmc_setting,
                          self.node.id, 'Telnet_Enable')

    def test_delete_bmc_setting_list_node_not_exist(self):
        self.assertRaises(exception.NodeNotFound,
                          self.dbapi.delete_bmc_setting_list,
                          '456', ['IPMI1_Enable'])

    def test_delete_bmc_setting_list_setting_not_exist(self):
        settings = db_utils.get_test_bmc_setting_list()
        self.dbapi.create_bmc_setting_list(self.node.id, settings, '1.0')
        self.assertRaises(exception.BMCSettingListNotFound,
                          self.dbapi.delete_bmc_setting_list,
                          self.node.id, ['fake-bmc-option'])
