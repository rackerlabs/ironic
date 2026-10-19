# coding=utf-8
#
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

from oslo_versionedobjects import base as object_base

from ironic.db import api as dbapi
from ironic.objects import base
from ironic.objects import fields as object_fields


@base.IronicObjectRegistry.register
class BMCSetting(base.IronicObject):
    # Version 1.0: Initial version
    VERSION = '1.0'

    dbapi = dbapi.get_instance()

    registry_fields = ('attribute_type', 'allowable_values', 'lower_bound',
                       'max_length', 'min_length', 'read_only',
                       'reset_required', 'unique', 'upper_bound')

    fields = {
        'node_id': object_fields.StringField(nullable=False),
        'name': object_fields.StringField(nullable=False),
        'value': object_fields.StringField(nullable=True),
        'attribute_type': object_fields.StringField(nullable=True),
        'allowable_values': object_fields.ListOfStringsField(
            nullable=True),
        'lower_bound': object_fields.IntegerField(nullable=True),
        'max_length': object_fields.IntegerField(nullable=True),
        'min_length': object_fields.IntegerField(nullable=True),
        'read_only': object_fields.BooleanField(nullable=True),
        'reset_required': object_fields.BooleanField(nullable=True),
        'unique': object_fields.BooleanField(nullable=True),
        'upper_bound': object_fields.IntegerField(nullable=True)
    }

    @object_base.remotable
    def create(self, context=None):
        """Create a BMC Setting record in DB.

        :param context: Security context. NOTE: This should only
                        be used internally by the indirection_api.
                        Unfortunately, RPC requires context as the first
                        argument, even though we don't use it.
                        A context should be set when instantiating the
                        object, e.g.: BMCSetting(context)
        :raises: NodeNotFound if the node id is not found.
        :raises: BMCSettingAlreadyExists if the setting record already exists.
        """
        values = self.do_version_changes_for_db()
        settings = {'name': values['name'], 'value': values['value']}
        for r in self.registry_fields:
            settings[r] = values.get(r)

        db_bmc_setting = self.dbapi.create_bmc_setting_list(
            values['node_id'], [settings], values['version'])
        self._from_db_object(self._context, self, db_bmc_setting[0])

    @object_base.remotable
    def save(self, context=None):
        """Save BMC Setting update in DB.

        :param context: Security context. NOTE: This should only
                        be used internally by the indirection_api.
                        Unfortunately, RPC requires context as the first
                        argument, even though we don't use it.
                        A context should be set when instantiating the
                        object, e.g.: BMCSetting(context)
        :raises: NodeNotFound if the node id is not found.
        :raises: BMCSettingNotFound if the bmc setting name is not found.
        """
        values = self.do_version_changes_for_db()

        settings = {'name': values['name'], 'value': values['value']}
        for r in self.registry_fields:
            settings[r] = values.get(r)

        updated_bmc_setting = self.dbapi.update_bmc_setting_list(
            values['node_id'], [settings], values['version'])
        self._from_db_object(self._context, self, updated_bmc_setting[0])

    @object_base.remotable_classmethod
    def get(cls, context, node_id, name):
        """Get a BMC Setting based on its node_id and name.

        :param context: Security context.
        :param node_id: The node id.
        :param name: BMC setting name to be retrieved.
        :raises: NodeNotFound if the node id is not found.
        :raises: BMCSettingNotFound if the bmc setting name is not found.
        :returns: A :class:'BMCSetting' object.
        """
        db_bmc_setting = cls.dbapi.get_bmc_setting(node_id, name)
        return cls._from_db_object(context, cls(), db_bmc_setting)

    @object_base.remotable_classmethod
    def delete(cls, context, node_id, name):
        """Delete a BMC Setting based on its node_id and name.

        :param context: Security context.
        :param node_id: The node id.
        :param name: BMC setting name to be deleted.
        :raises: NodeNotFound if the node id is not found.
        :raises: BMCSettingNotFound if the bmc setting name is not found.
        """
        cls.dbapi.delete_bmc_setting_list(node_id, [name])


@base.IronicObjectRegistry.register
class BMCSettingList(base.IronicObjectListBase, base.IronicObject):
    # Version 1.0: Initial version
    VERSION = '1.0'

    dbapi = dbapi.get_instance()

    fields = {
        'objects': object_fields.ListOfObjectsField('BMCSetting'),
    }

    @object_base.remotable_classmethod
    def create(cls, context, node_id, settings):
        """Create a list of BMC Setting records in DB.

        :param context: Security context. NOTE: This should only
                        be used internally by the indirection_api.
                        Unfortunately, RPC requires context as the first
                        argument, even though we don't use it.
                        A context should be set when instantiating the
                        object, e.g.: BMCSetting(context)
        :param node_id: The node id.
        :param settings: A list of bmc settings.
        :raises: NodeNotFound if the node id is not found.
        :raises: BMCSettingAlreadyExists if any of the setting records
            already exists.
        :return: A list of BMCSetting objects.
        """
        version = BMCSetting.get_target_version()
        db_setting_list = cls.dbapi.create_bmc_setting_list(
            node_id, settings, version)
        return object_base.obj_make_list(
            context, cls(), BMCSetting, db_setting_list)

    @object_base.remotable_classmethod
    def save(cls, context, node_id, settings):
        """Save a list of BMC Setting updates in DB.

        :param context: Security context. NOTE: This should only
                        be used internally by the indirection_api.
                        Unfortunately, RPC requires context as the first
                        argument, even though we don't use it.
                        A context should be set when instantiating the
                        object, e.g.: BMCSetting(context)
        :param node_id: The node id.
        :param settings: A list of bmc settings.
        :raises: NodeNotFound if the node id is not found.
        :raises: BMCSettingNotFound if any of the bmc setting names
            is not found.
        :return: A list of BMCSetting objects.
        """
        version = BMCSetting.get_target_version()
        updated_setting_list = cls.dbapi.update_bmc_setting_list(
            node_id, settings, version)
        return object_base.obj_make_list(
            context, cls(), BMCSetting, updated_setting_list)

    @object_base.remotable_classmethod
    def delete(cls, context, node_id, names):
        """Delete BMC Settings based on node_id and names.

        :param context: Security context.
        :param node_id: The node id.
        :param names: List of BMC setting names to be deleted.
        :raises: NodeNotFound if the node id is not found.
        :raises: BMCSettingNotFound if any of BMC setting fails to delete.
        """
        cls.dbapi.delete_bmc_setting_list(node_id, names)

    @object_base.remotable_classmethod
    def get_by_node_id(cls, context, node_id):
        """Get BMC Setting based on node_id.

        :param context: Security context.
        :param node_id: The node id.
        :raises: NodeNotFound if the node id is not found.
        :return: A list of BMCSetting objects.
        """
        node_bmc_setting = cls.dbapi.get_bmc_setting_list(node_id)
        return object_base.obj_make_list(
            context, cls(), BMCSetting, node_bmc_setting)

    @object_base.remotable_classmethod
    def sync_node_setting(cls, context, node_id, settings):
        """Returns lists of create/update/delete/unchanged settings.

        This method sync with 'bmc_settings' database table and sorts
        out four lists of create/update/delete/unchanged settings.

        :param context: Security context.
        :param node_id: The node id.
        :param settings: BMC settings to be synced.
        :returns: A 4-tuple of lists of BMC settings to be created,
            updated, deleted and unchanged.
        """
        create_list = []
        update_list = []
        delete_list = []
        nochange_list = []
        current_settings_dict = {}

        given_setting_names = [setting['name'] for setting in settings]
        current_settings = cls.get_by_node_id(context, node_id)

        for setting in current_settings:
            current_settings_dict[setting.name] = setting.value

        for setting in settings:
            if setting['name'] in current_settings_dict:
                if setting['value'] != current_settings_dict[setting['name']]:
                    update_list.append(setting)
                else:
                    nochange_list.append(setting)
            else:
                create_list.append(setting)

        for setting in current_settings:
            if setting.name not in given_setting_names:
                delete_list.append({'name': setting.name,
                                    'value': setting.value})

        return (create_list, update_list, delete_list, nochange_list)
