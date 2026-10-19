# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

from pecan import rest

from ironic import api
from ironic.api.controllers.v1 import utils as api_utils
from ironic.api.controllers.v1 import versions
from ironic.api import method
from ironic.api.schemas.v1 import bmc as schema
from ironic.api import validation
from ironic.common import args
from ironic.common import exception
from ironic.common import metrics_utils
from ironic import objects

METRICS = metrics_utils.get_metrics_logger(__name__)

_DEFAULT_RETURN_FIELDS = ('name', 'value')
_DEFAULT_FIELDS_WITH_REGISTRY = ('name', 'value', 'attribute_type',
                                 'allowable_values', 'lower_bound',
                                 'max_length', 'min_length', 'read_only',
                                 'reset_required', 'unique', 'upper_bound')


def convert_with_links(rpc_bmc, node_uuid, detail=None, fields=None):
    """Build a dict containing a bmc setting value."""

    if detail:
        fields = _DEFAULT_FIELDS_WITH_REGISTRY

    bmc = api_utils.object_to_dict(
        rpc_bmc,
        include_uuid=False,
        fields=fields,
        link_resource='nodes',
        link_resource_args="%s/bmc/%s" % (node_uuid, rpc_bmc.name),
    )
    return bmc


def collection_from_list(node_ident, bmc_settings, detail=None, fields=None):
    bmc_list = []
    for bmc_setting in bmc_settings:
        bmc_list.append(convert_with_links(bmc_setting, node_ident,
                        detail, fields))
    return {'bmc': bmc_list}


class NodeBmcController(rest.RestController):
    """REST controller for BMC settings."""

    def __init__(self, node_ident=None):
        super(NodeBmcController, self).__init__()
        self.node_ident = node_ident

    @METRICS.timer('NodeBmcController.get_all')
    @method.expose()
    @validation.api_version(min_version=versions.MINOR_116_BMC_SETTINGS)
    # TODO(stephenfin): We are currently using this for side-effects to e.g.
    # convert a CSV string to an array or a string to an integer. We should
    # probably rename this decorator or provide a separate, simpler decorator.
    @args.validate(fields=args.string_list, detail=args.boolean)
    @validation.request_query_schema(schema.index_request_query)
    @validation.response_body_schema(schema.index_response_body)
    def get_all(self, detail=None, fields=None):
        """List node BMC settings."""
        node = api_utils.check_node_policy_and_retrieve(
            'baremetal:node:bmc:get', self.node_ident)

        fields = api_utils.get_request_return_fields(
            fields, detail, _DEFAULT_RETURN_FIELDS,
            lambda: True, lambda: True)

        settings = objects.BMCSettingList.get_by_node_id(
            api.request.context, node.id)
        return collection_from_list(self.node_ident, settings,
                                    detail, fields)

    @METRICS.timer('NodeBmcController.get_one')
    @method.expose()
    @validation.api_version(min_version=versions.MINOR_116_BMC_SETTINGS)
    @validation.request_parameter_schema(schema.show_request_parameter)
    @validation.request_query_schema(schema.show_request_query)
    @validation.response_body_schema(schema.show_response_body)
    def get_one(self, setting_name):
        """Retrieve information about the given BMC setting.

        :param setting_name: Logical name of the setting to retrieve.
        """
        node = api_utils.check_node_policy_and_retrieve(
            'baremetal:node:bmc:get', self.node_ident)

        try:
            setting = objects.BMCSetting.get(api.request.context, node.id,
                                             setting_name)
        except exception.BMCSettingNotFound:
            raise exception.BMCSettingNotFound(node=node.uuid,
                                               name=setting_name)

        return {setting_name: convert_with_links(
            setting, node.uuid, fields=_DEFAULT_FIELDS_WITH_REGISTRY)}
