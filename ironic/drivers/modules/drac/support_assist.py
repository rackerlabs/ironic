#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

"""Dell SupportAssist operations for the idrac-redfish vendor interface."""

from urllib import parse

from oslo_config import cfg
from sushy.oem.dell.resources.manager import constants as dell_constants

from ironic.common import exception
from ironic.common.i18n import _
from ironic.drivers.modules.drac import utils as drac_utils

CONF = cfg.CONF

METHOD_ARGUMENTS = {
    'support_assist_get_eula_status': set(),
    'support_assist_accept_eula': set(),
    'support_assist_collect': {'data_types', 'filter_data'},
}


def validate(method, **kwargs):
    """Validate arguments for a SupportAssist vendor method."""
    unknown = set(kwargs) - METHOD_ARGUMENTS[method] - {'http_method'}
    if unknown:
        raise exception.InvalidParameterValue(
            _('Unknown SupportAssist arguments: %s') % sorted(unknown))
    if method == 'support_assist_collect':
        data_types = kwargs.get('data_types', ['HWData'])
        allowed = {item.value for item in
                   dell_constants.SupportAssistDataType}
        if (not isinstance(data_types, list) or not data_types
                or any(not isinstance(item, str) or item not in allowed
                       for item in data_types)):
            raise exception.InvalidParameterValue(
                _('data_types must be a non-empty list drawn from %s')
                % sorted(allowed))
        if not isinstance(kwargs.get('filter_data', False), bool):
            raise exception.InvalidParameterValue(
                _('filter_data must be a boolean'))
        _share()


def _share():
    """Translate the configured destination into Dell action inputs."""
    try:
        url = parse.urlsplit(CONF.drac.support_assist_share_url or '')
        share_type = dell_constants.SupportAssistShareType(
            url.scheme.upper())
        port = url.port
        if (url.scheme not in ('http', 'https')
                or not url.hostname or not url.path.strip('/')
                or url.username is not None or url.password is not None
                or url.query or url.fragment):
            raise ValueError()
    except ValueError:
        # Never echo a malformed URL: it may contain credentials.
        raise exception.InvalidParameterValue(
            _('Configure [drac]support_assist_share_url as a '
              'HTTP or HTTPS URL with a host and destination '
              'path, without credentials, query or fragment. Explicit '
              'ports are supported.'))
    return {
        'share_type': share_type,
        'ip_address': url.hostname,
        'share_name': parse.unquote(url.path).lstrip('/'),
        'port': port,
        'username': CONF.drac.support_assist_share_username,
        'password': CONF.drac.support_assist_share_password,
    }


def get_eula_status(task, **kwargs):
    """Return EULA status and the original Dell status messages."""
    result = drac_utils.execute_oem_manager_method(
        task, 'get SupportAssist EULA status',
        lambda m: m.lifecycle_service.support_assist_get_eula_status())
    try:
        codes = {item.get('MessageId', '').rsplit('.', 1)[-1]
                 for item in result.get('@Message.ExtendedInfo', [])}
    except (AttributeError, TypeError):
        raise exception.RedfishError(
            error=_('iDRAC returned a malformed SupportAssist EULA status'))
    if 'SRV074' in codes:
        accepted = True
    elif 'SRV104' in codes:
        accepted = False
    else:
        raise exception.RedfishError(
            error=_('iDRAC returned an unrecognized SupportAssist '
                    'EULA status'))
    return {'accepted': accepted, 'details': result}


def accept_eula(task, **kwargs):
    """Accept EULA only at the operator's explicit request."""
    result = drac_utils.execute_oem_manager_method(
        task, 'accept SupportAssist EULA',
        lambda m: m.lifecycle_service.support_assist_accept_eula())
    if result is None:
        return {}
    if not isinstance(result, dict):
        raise exception.RedfishError(
            error=_('iDRAC returned a malformed SupportAssist EULA '
                    'acceptance response'))
    return result


def collect(task, **kwargs):
    """Start collection and return a handle without waiting."""
    share = _share()
    share['share_name'] = share['share_name'].replace(
        '{node_uuid}', task.node.uuid)
    monitor = drac_utils.execute_oem_manager_method(
        task, 'start SupportAssist collection',
        lambda m: m.lifecycle_service.support_assist_collect(
            data_types=[dell_constants.SupportAssistDataType(item)
                        for item in kwargs.get('data_types', ['HWData'])],
            filter_data=kwargs.get('filter_data', False),
            **share))
    return {'task_monitor': parse.urlsplit(monitor.task_monitor_uri).path}
