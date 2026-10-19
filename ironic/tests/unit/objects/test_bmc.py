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

import types
from unittest import mock

from ironic.common import context
from ironic.db import api as dbapi
from ironic.db.sqlalchemy.api import Connection as db_conn
from ironic import objects
from ironic.tests.unit.db import base as db_base
from ironic.tests.unit.db import utils as db_utils
from ironic.tests.unit.objects import utils as obj_utils


class TestBMCSettingObject(db_base.DbTestCase):

    def setUp(self):
        super(TestBMCSettingObject, self).setUp()
        self.ctxt = context.get_admin_context()
        self.bmc_setting = db_utils.get_test_bmc_setting()
        self.node_id = self.bmc_setting['node_id']

    @mock.patch.object(dbapi.IMPL, 'get_bmc_setting', autospec=True)
    def test_get(self, mock_get_setting):
        mock_get_setting.return_value = self.bmc_setting

        bmc_obj = objects.BMCSetting.get(self.context, self.node_id,
                                         self.bmc_setting['name'])

        mock_get_setting.assert_called_once_with(self.node_id,
                                                 self.bmc_setting['name'])
        self.assertEqual(self.context, bmc_obj._context)
        self.assertEqual(self.bmc_setting['node_id'], bmc_obj.node_id)
        self.assertEqual(self.bmc_setting['name'], bmc_obj.name)
        self.assertEqual(self.bmc_setting['value'], bmc_obj.value)
        self.assertEqual(self.bmc_setting['attribute_type'],
                         bmc_obj.attribute_type)
        self.assertEqual(self.bmc_setting['allowable_values'],
                         bmc_obj.allowable_values)

    @mock.patch.object(dbapi.IMPL, 'get_bmc_setting_list', autospec=True)
    def test_get_by_node_id(self, mock_get_setting_list):
        bmc_setting2 = db_utils.get_test_bmc_setting(name='SSH_Enable',
                                                     value='Enabled')
        mock_get_setting_list.return_value = [self.bmc_setting, bmc_setting2]
        bmc_obj_list = objects.BMCSettingList.get_by_node_id(
            self.context, self.node_id)

        mock_get_setting_list.assert_called_once_with(self.node_id)
        self.assertEqual(self.context, bmc_obj_list._context)
        self.assertEqual(2, len(bmc_obj_list))
        self.assertEqual(self.bmc_setting['name'], bmc_obj_list[0].name)
        self.assertEqual(bmc_setting2['name'], bmc_obj_list[1].name)

    @mock.patch.object(db_conn, 'create_bmc_setting_list', autospec=True)
    def test_create(self, mock_create_list):
        fake_call_args = {'node_id': self.bmc_setting['node_id'],
                          'name': self.bmc_setting['name'],
                          'value': self.bmc_setting['value'],
                          'attribute_type':
                          self.bmc_setting['attribute_type'],
                          'allowable_values':
                          self.bmc_setting['allowable_values'],
                          'read_only': self.bmc_setting['read_only'],
                          'reset_required':
                          self.bmc_setting['reset_required'],
                          'unique': self.bmc_setting['unique'],
                          'version': self.bmc_setting['version']}
        setting = [{'name': 'IPMI1_Enable', 'value': 'Disabled',
                    'attribute_type': 'Enumeration',
                    'allowable_values': ['Enabled', 'Disabled'],
                    'lower_bound': None, 'max_length': None,
                    'min_length': None, 'read_only': False,
                    'reset_required': True, 'unique': False,
                    'upper_bound': None}]

        bmc_obj = objects.BMCSetting(context=self.context, **fake_call_args)
        mock_create_list.return_value = [self.bmc_setting]
        bmc_obj.create()
        mock_create_list.assert_called_once_with(mock.ANY,
                                                 self.bmc_setting['node_id'],
                                                 setting,
                                                 self.bmc_setting['version'])
        self.assertEqual(self.bmc_setting['name'], bmc_obj.name)
        self.assertEqual(self.bmc_setting['value'], bmc_obj.value)

    @mock.patch.object(db_conn, 'update_bmc_setting_list', autospec=True)
    def test_save(self, mock_update_list):
        fake_call_args = {'node_id': self.bmc_setting['node_id'],
                          'name': self.bmc_setting['name'],
                          'value': self.bmc_setting['value'],
                          'version': self.bmc_setting['version']}
        setting = [{'name': self.bmc_setting['name'],
                    'value': self.bmc_setting['value'],
                    'attribute_type': None, 'allowable_values': None,
                    'lower_bound': None, 'max_length': None,
                    'min_length': None, 'read_only': None,
                    'reset_required': None, 'unique': None,
                    'upper_bound': None}]
        bmc_obj = objects.BMCSetting(context=self.context, **fake_call_args)
        mock_update_list.return_value = [self.bmc_setting]
        bmc_obj.save()
        mock_update_list.assert_called_once_with(mock.ANY,
                                                 self.bmc_setting['node_id'],
                                                 setting,
                                                 self.bmc_setting['version'])
        self.assertEqual(self.bmc_setting['name'], bmc_obj.name)
        self.assertEqual(self.bmc_setting['value'], bmc_obj.value)

    @mock.patch.object(db_conn, 'create_bmc_setting_list', autospec=True)
    def test_list_create(self, mock_create_list):
        bmc_setting2 = db_utils.get_test_bmc_setting(name='SSH_Enable',
                                                     value='Enabled')
        settings = db_utils.get_test_bmc_setting_list()[:-1]
        mock_create_list.return_value = [self.bmc_setting, bmc_setting2]
        bmc_obj_list = objects.BMCSettingList.create(
            self.context, self.node_id, settings)

        mock_create_list.assert_called_once_with(mock.ANY, self.node_id,
                                                 settings, '1.0')
        self.assertEqual(2, len(bmc_obj_list))

    @mock.patch.object(db_conn, 'update_bmc_setting_list', autospec=True)
    def test_list_save(self, mock_update_list):
        bmc_setting2 = db_utils.get_test_bmc_setting(name='SSH_Enable',
                                                     value='Enabled')
        settings = db_utils.get_test_bmc_setting_list()[:-1]
        mock_update_list.return_value = [self.bmc_setting, bmc_setting2]
        bmc_obj_list = objects.BMCSettingList.save(
            self.context, self.node_id, settings)

        mock_update_list.assert_called_once_with(mock.ANY, self.node_id,
                                                 settings, '1.0')
        self.assertEqual(2, len(bmc_obj_list))

    @mock.patch.object(db_conn, 'delete_bmc_setting_list', autospec=True)
    def test_delete(self, mock_delete):
        objects.BMCSetting.delete(self.context, self.node_id,
                                  self.bmc_setting['name'])
        mock_delete.assert_called_once_with(mock.ANY,
                                            self.node_id,
                                            [self.bmc_setting['name']])

    @mock.patch.object(db_conn, 'delete_bmc_setting_list', autospec=True)
    def test_list_delete(self, mock_delete):
        bmc_setting2 = db_utils.get_test_bmc_setting(name='SSH_Enable')
        name_list = [self.bmc_setting['name'], bmc_setting2['name']]
        objects.BMCSettingList.delete(self.context, self.node_id, name_list)
        mock_delete.assert_called_once_with(mock.ANY, self.node_id, name_list)

    @mock.patch('ironic.objects.bmc.BMCSettingList.get_by_node_id',
                spec_set=types.FunctionType)
    def test_sync_node_setting_create_and_update(self, mock_get):
        node = obj_utils.create_test_node(self.ctxt)
        bmc_obj = [obj_utils.create_test_bmc_setting(
            self.ctxt, node_id=node.id)]
        mock_get.return_value = bmc_obj
        settings = db_utils.get_test_bmc_setting_list()
        settings[0]['value'] = 'Enabled'
        create, update, delete, nochange = (
            objects.BMCSettingList.sync_node_setting(self.ctxt, node.id,
                                                     settings))

        self.assertEqual(create, settings[1:])
        self.assertEqual(update, [settings[0]])
        self.assertEqual(delete, [])
        self.assertEqual(nochange, [])

    @mock.patch('ironic.objects.bmc.BMCSettingList.get_by_node_id',
                spec_set=types.FunctionType)
    def test_sync_node_setting_delete_nochange(self, mock_get):
        node = obj_utils.create_test_node(self.ctxt)
        bmc_obj_1 = obj_utils.create_test_bmc_setting(
            self.ctxt, node_id=node.id)
        bmc_obj_2 = obj_utils.create_test_bmc_setting(
            self.ctxt, node_id=node.id, name='Telnet_Enable', value='Disabled')
        mock_get.return_value = [bmc_obj_1, bmc_obj_2]
        settings = db_utils.get_test_bmc_setting_list()
        settings[0]['name'] = 'fake-bmc-option'
        create, update, delete, nochange = (
            objects.BMCSettingList.sync_node_setting(self.ctxt, node.id,
                                                     settings))

        expected_delete = [{'name': 'IPMI1_Enable', 'value': 'Disabled'}]
        self.assertEqual(create, settings[:2])
        self.assertEqual(update, [])
        self.assertEqual(delete, expected_delete)
        self.assertEqual(nochange, [settings[2]])
