# Copyright (c) 2021 Dell Inc. or its subsidiaries.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

from oslo_log import log
import sushy

from ironic.common import exception
from ironic.common.i18n import _
from ironic.drivers.modules.redfish import utils as redfish_utils

LOG = log.getLogger(__name__)


def execute_oem_manager_method(
        task, process_name, lambda_oem_func):
    """Loads OEM manager and executes passed method on it.

    Known iDRAC Redfish systems has only one manager, but as Redfish
    schema allows a list this method iterates through all values in case
    this changes in future. If there are several managers, this will
    try starting from the first in the list until the first success.

    :param task: a TaskManager instance.
    :param process_name: user friendly name of method to be executed.
        Used in exception and log messages.
    :param lambda_oem_func: method to execute as lambda function with
        input parameter OEM extension manager.
        Example: lambda m: m.reset_idrac()
    :returns: Returned value of lambda_oem_func
    :raises: RedfishError if can't execute OEM function either because
        there are no managers to the system, failed to load OEM
        extension or execution of the OEM method failed itself.
    """

    system = redfish_utils.get_system(task.node)

    if not system.managers:
        raise exception.RedfishError(
            "System %(system)s has no managers" %
            {'system': system.uuid if system.uuid else system.identity})

    oem_error_msgs = []
    for manager in system.managers:
        # This call makes Sushy go fishing in the ocean of Sushy
        # OEM extensions installed on the system. If it finds one
        # for 'Dell' which implements the 'Manager' resource
        # extension, it uses it to create an object which
        # instantiates itself from the OEM JSON. The object is
        # returned here.
        #
        # If the extension could not be found for one manager, it
        # will not be found for any others until it is installed, so
        # abruptly exit the for loop. The vendor and resource name,
        # 'Dell' and 'Manager', respectively, used to search for the
        # extension are invariant in the loop.
        try:
            manager_oem = manager.get_oem_extension('Dell')
        except sushy.exceptions.OEMExtensionNotFoundError as e:
            error_msg = (_("Search for Sushy OEM extension in "
                           "'sushy' package failed for node %(node)s. "
                           "Ensure it is installed. Error: %(error)s") %
                         {'node': task.node.uuid, 'error': e})
            LOG.error(error_msg)
            raise exception.RedfishError(error=error_msg)

        try:
            result = lambda_oem_func(manager_oem)
            LOG.info("Completed: %(process_name)s with system %(system)s "
                     "manager %(manager)s for node %(node)s",
                     {'process_name': process_name,
                      'system': system.uuid if system.uuid else
                      system.identity,
                      'manager': manager.uuid if manager.uuid else
                      manager.identity,
                      'node': task.node.uuid})
            return result
        except sushy.exceptions.SushyError as e:
            error_msg = (_("Manager %(manager)s: %(error)s" %
                         {'manager': manager.uuid if manager.uuid else
                           manager.identity, 'error': e}))
            LOG.debug("Failed: %(process_name)s with system %(system)s "
                      "for node %(node)s. Will try next manager, if "
                      "available. Error: %(error)s",
                      {'process_name': process_name,
                       'system': system.uuid if system.uuid else
                       system.identity,
                       'node': task.node.uuid,
                       'error': error_msg})
            oem_error_msgs.append(error_msg)
    else:
        error_msg = (_('In system %(system)s for node %(node)s all managers '
                       'failed: %(process_name)s. Errors: %(oem_error_msgs)s' %
                     {'system': system.uuid if system.uuid else
                       system.identity,
                      'node': task.node.uuid,
                      'process_name': process_name,
                      'oem_error_msgs': oem_error_msgs if oem_error_msgs else
                      'unknown'}))
        LOG.error(error_msg)
        raise exception.RedfishError(error=error_msg)


def _get_dell_attributes_uri(manager, target='System'):
    """Resolve the URI of a Dell OEM ``DellAttributes`` resource.

    iDRAC exposes its configuration through Dell OEM ``DellAttributes``
    resources (one each for the iDRAC, the System and the Lifecycle
    Controller). These are advertised from the Manager under
    ``Links/Oem/Dell/DellAttributes``.

    :param manager: a sushy Manager object.
    :param target: substring used to pick the desired attribute resource,
        matched against the trailing resource identifier in the advertised
        ``@odata.id`` values. Defaults to ``System``.
    :returns: the URI of the matching ``DellAttributes`` resource.
    :raises: RedfishError if the requested resource is not advertised.
    """
    links = (((manager.json or {}).get('Links') or {}).get('Oem') or {}) \
        .get('Dell', {}).get('DellAttributes') or []

    for member in links:
        uri = member.get('@odata.id')
        if not uri:
            continue
        # Match against the trailing resource id (e.g. ``iDRAC.Embedded.1``)
        # rather than the whole URI: the manager id also appears earlier in
        # the path, so a substring match on the URI is ambiguous.
        member_id = uri.rstrip('/').rsplit('/', 1)[-1]
        if target.lower() in member_id.lower():
            return uri

    raise exception.RedfishError(
        error=_('DellAttributes resource for target %(target)s was not '
                'advertised by manager %(manager)s') %
        {'target': target, 'manager': manager.identity})


def get_dell_attributes(task, target='System'):
    """Return attributes from a Dell OEM ``DellAttributes`` resource."""
    system = redfish_utils.get_system(task.node)
    manager = redfish_utils.get_manager(task.node, system)
    uri = _get_dell_attributes_uri(manager, target=target)

    try:
        response = manager._conn.get(uri)
        data = response.json()
    except (sushy.exceptions.SushyError, ValueError) as e:
        error_msg = (_('Failed to read Dell attributes for node %(node)s '
                       'at %(uri)s. Error: %(error)s') %
                     {'node': task.node.uuid, 'uri': uri, 'error': e})
        LOG.error(error_msg)
        raise exception.RedfishError(error=error_msg)

    return data.get('Attributes', data)


def set_dell_attributes(task, attributes, target='System'):
    """Apply a set of Dell OEM attributes to a node's iDRAC.

    Performs a raw Redfish PATCH against the Dell OEM ``DellAttributes``
    resource. Most iDRAC attributes (NTP, DNS, OIDC, ...) are applied
    immediately, without requiring a configuration job or a reboot.

    Only the attribute names are logged, never their values, so that
    secrets (such as an OIDC client secret) are not written to the logs.

    :param task: a TaskManager instance containing the node to act on.
    :param attributes: a dict of ``{attribute_name: value}`` to apply.
    :param target: which ``DellAttributes`` resource to target. Defaults
        to the System attributes.
    :raises: RedfishError on any error talking to the BMC.
    """
    system = redfish_utils.get_system(task.node)
    manager = redfish_utils.get_manager(task.node, system)
    uri = _get_dell_attributes_uri(manager, target=target)

    LOG.debug('Applying Dell attributes %(attrs)s to node %(node)s at '
              '%(uri)s', {'attrs': sorted(attributes),
                          'node': task.node.uuid, 'uri': uri})
    try:
        manager._conn.patch(uri, data={'Attributes': attributes})
    except sushy.exceptions.SushyError as e:
        error_msg = (_('Failed to apply Dell attributes %(attrs)s to node '
                       '%(node)s at %(uri)s. Error: %(error)s') %
                     {'attrs': sorted(attributes), 'node': task.node.uuid,
                      'uri': uri, 'error': e})
        LOG.error(error_msg)
        raise exception.RedfishError(error=error_msg)

    LOG.info('Applied Dell attributes %(attrs)s to node %(node)s',
             {'attrs': sorted(attributes), 'node': task.node.uuid})
