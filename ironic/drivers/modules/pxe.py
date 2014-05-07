# -*- encoding: utf-8 -*-
#
# Copyright 2013 Hewlett-Packard Development Company, L.P.
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
"""
PXE Driver and supporting meta-classes.
"""

import os

from oslo.config import cfg

from ironic.common import exception
from ironic.common import image_service as service
from ironic.common import keystone
from ironic.common import neutron
from ironic.common import paths
from ironic.common import states
from ironic.common import tftp
from ironic.common import utils
from ironic.conductor import task_manager
from ironic.conductor import utils as manager_utils
from ironic.drivers import base
from ironic.drivers.modules import deploy_utils
from ironic.drivers.modules import image_cache
from ironic.drivers import utils as driver_utils
from ironic.openstack.common import fileutils
from ironic.openstack.common import log as logging
from ironic.openstack.common import strutils


pxe_opts = [
    cfg.StrOpt('pxe_append_params',
               default='nofb nomodeset vga=normal',
               help='Additional append parameters for baremetal PXE boot.'),
    cfg.StrOpt('pxe_config_template',
               default=paths.basedir_def(
                    'drivers/modules/pxe_config.template'),
               help='Template file for PXE configuration.'),
    cfg.StrOpt('default_ephemeral_format',
               default='ext4',
               help='Default file system format for ephemeral partition, '
                    'if one is created.'),
    cfg.StrOpt('images_path',
               default='/var/lib/ironic/images/',
               help='Directory where images are stored on disk.'),
    cfg.StrOpt('tftp_master_path',
               default='/tftpboot/master_images',
               help='Directory where master tftp images are stored on disk.'),
    cfg.StrOpt('instance_master_path',
               default='/var/lib/ironic/master_images',
               help='Directory where master instance images are stored on '
                    'disk.'),
    # NOTE(dekehn): Additional boot files options may be created in the event
    #  other architectures require different boot files.
    cfg.StrOpt('pxe_bootfile_name',
               default='pxelinux.0',
               help='Neutron bootfile DHCP parameter.'),
    cfg.IntOpt('image_cache_size',
               default=1024,
               help='Maximum size (in MiB) of cache for master images, '
               'including those in use'),
    cfg.IntOpt('image_cache_ttl',
               default=60,
               help='Maximum TTL (in minutes) for old master images in cache'),
    ]

LOG = logging.getLogger(__name__)

CONF = cfg.CONF
CONF.register_opts(pxe_opts, group='pxe')
CONF.import_opt('use_ipv6', 'ironic.netconf')


def _parse_driver_info(node):
    """Gets the driver-specific Node deployment info.

    This method validates whether the 'driver_info' property of the
    supplied node contains the required information for this driver to
    deploy images to the node.

    :param node: a single Node to validate.
    :returns: A dict with the driver_info values.
    """

    info = node.driver_info or {}
    d_info = {}
    d_info['image_source'] = info.get('pxe_image_source')
    d_info['deploy_kernel'] = info.get('pxe_deploy_kernel')
    d_info['deploy_ramdisk'] = info.get('pxe_deploy_ramdisk')
    d_info['root_gb'] = info.get('pxe_root_gb')

    missing_info = []
    for label in d_info:
        if not d_info[label]:
            missing_info.append("pxe_%s" % label)
    if missing_info:
        raise exception.InvalidParameterValue(_(
                "Can not validate PXE bootloader. The following parameters "
                "were not passed to ironic: %s") % missing_info)

    # Internal use only
    d_info['deploy_key'] = info.get('pxe_deploy_key')

    d_info['swap_mb'] = info.get('pxe_swap_mb', 0)
    d_info['ephemeral_gb'] = info.get('pxe_ephemeral_gb', 0)
    d_info['ephemeral_format'] = info.get('pxe_ephemeral_format')

    err_msg_invalid = _("Can not validate PXE bootloader. Invalid parameter "
                        "pxe_%(param)s. Reason: %(reason)s")
    for param in ('root_gb', 'swap_mb', 'ephemeral_gb'):
        try:
            int(d_info[param])
        except ValueError:
            reason = _("'%s' is not an integer value.") % d_info[param]
            raise exception.InvalidParameterValue(err_msg_invalid %
                                            {'param': param, 'reason': reason})

    if d_info['ephemeral_gb'] and not d_info['ephemeral_format']:
        d_info['ephemeral_format'] = CONF.pxe.default_ephemeral_format

    preserve_ephemeral = info.get('pxe_preserve_ephemeral', False)
    try:
        d_info['preserve_ephemeral'] = strutils.bool_from_string(
                                            preserve_ephemeral, strict=True)
    except ValueError as e:
        raise exception.InvalidParameterValue(err_msg_invalid %
                                  {'param': 'preserve_ephemeral', 'reason': e})
    return d_info


def build_pxe_config_options(node, pxe_info, ctx):
    """Build the PXE config file for a node

    This method builds the PXE boot configuration file for a node,
    given all the required parameters.

    The resulting file has both a "deploy" and "boot" label, which correspond
    to the two phases of booting. This may be extended later.

    :param pxe_options: A dict of values to set on the configuarion file
    :returns: A formated string with the file content.
    """
    LOG.debug(_("Building PXE config for deployment %s.") % node['id'])

    # NOTE: we should strip '/' from the end because this is intended for
    # hardcoded ramdisk script
    ironic_api = (CONF.conductor.api_url or
                  keystone.get_service_url()).rstrip('/')

    deploy_key = utils.random_alnum(32)
    driver_info = node['driver_info']
    driver_info['pxe_deploy_key'] = deploy_key
    node['driver_info'] = driver_info
    node.save(ctx)

    pxe_options = {
        'deployment_id': node['uuid'],
        'deployment_key': deploy_key,
        'deployment_iscsi_iqn': "iqn-%s" % node.uuid,
        'deployment_aki_path': pxe_info['deploy_kernel'][1],
        'deployment_ari_path': pxe_info['deploy_ramdisk'][1],
        'aki_path': pxe_info['kernel'][1],
        'ari_path': pxe_info['ramdisk'][1],
        'ironic_api_url': ironic_api,
        'pxe_append_params': CONF.pxe.pxe_append_params,
    }
    return tftp.build_pxe_config(node,
                                  pxe_options,
                                  CONF.pxe.pxe_config_template)


def _get_image_dir_path(node_uuid):
    """Generate the dir for an instances disk."""
    return os.path.join(CONF.pxe.images_path, node_uuid)


def _get_image_file_path(node_uuid):
    """Generate the full path for an instances disk."""
    return os.path.join(_get_image_dir_path(node_uuid), 'disk')


def _get_token_file_path(node_uuid):
    """Generate the path for PKI token file."""
    return os.path.join(CONF.tftp.tftp_root, 'token-' + node_uuid)


class PXEImageCache(image_cache.ImageCache):
    def __init__(self, master_dir, image_service=None):
        super(PXEImageCache, self).__init__(
            master_dir,
            # MiB -> B
            cache_size=CONF.pxe.image_cache_size * 1024 * 1024,
            # min -> sec
            cache_ttl=CONF.pxe.image_cache_ttl * 60,
            image_service=image_service)


def _cache_tftp_images(ctx, node, pxe_info):
    """Fetch the necessary kernels and ramdisks for the instance."""
    fileutils.ensure_tree(
        os.path.join(CONF.tftp.tftp_root, node.uuid))
    LOG.debug(_("Fetching kernel and ramdisk for node %s") %
              node.uuid)
    image_cache = PXEImageCache(CONF.pxe.tftp_master_path)
    for label in pxe_info:
        (uuid, path) = pxe_info[label]
        image_cache.fetch_image(uuid, path, ctx=ctx)


def _cache_instance_image(ctx, node):
    """Fetch the instance's image from Glance

    This method pulls the relevant AMI and associated kernel and ramdisk,
    and the deploy kernel and ramdisk from Glance, and writes them
    to the appropriate places on local disk.

    Both sets of kernel and ramdisk are needed for PXE booting, so these
    are stored under CONF.tftp.tftp_root.

    At present, the AMI is cached and certain files are injected.
    Debian/ubuntu-specific assumptions are made regarding the injected
    files. In a future revision, this functionality will be replaced by a
    more scalable and os-agnostic approach: the deployment ramdisk will
    fetch from Glance directly, and write its own last-mile configuration.

    """
    d_info = _parse_driver_info(node)
    fileutils.ensure_tree(_get_image_dir_path(node.uuid))
    image_path = _get_image_file_path(node.uuid)
    uuid = d_info['image_source']

    LOG.debug(_("Fetching image %(ami)s for node %(uuid)s") %
              {'ami': uuid, 'uuid': node.uuid})

    image_cache = PXEImageCache(CONF.pxe.instance_master_path)
    image_cache.fetch_image(uuid, image_path, ctx=ctx)

    return (uuid, image_path)


def _get_tftp_image_info(node, ctx):
    """Generate the paths for tftp files for this instance

    Raises IronicException if
    - instance does not contain kernel_id or ramdisk_id
    - deploy_kernel_id or deploy_ramdisk_id can not be read from
      driver_info and defaults are not set

    """
    d_info = _parse_driver_info(node)
    image_info = {
            'deploy_kernel': [None, None],
            'deploy_ramdisk': [None, None],
            }

    for label in image_info:
        image_info[label][0] = str(d_info[label]).split('/')[-1]
        image_info[label][1] = os.path.join(CONF.tftp.tftp_root,
                                            node.uuid, label)

    driver_info = node.driver_info
    labels = ('kernel', 'ramdisk')
    if not (driver_info.get('pxe_kernel') and driver_info.get('pxe_ramdisk')):
        glance_service = service.Service(version=1, context=ctx)
        iproperties = glance_service.show(d_info['image_source'])['properties']
        for label in labels:
            driver_info['pxe_' + label] = str(iproperties[label +
                                              '_id']).split('/')[-1]
        node.driver_info = driver_info
        node.save(ctx)

    for label in labels:
        image_info[label] = [None, None]
        image_info[label][0] = driver_info['pxe_' + label]
        image_info[label][1] = os.path.join(CONF.tftp.tftp_root,
                                            node.uuid, label)

    return image_info


def _cache_images(node, pxe_info, ctx):
    """Prepare all the images for this instance."""
    #TODO(ghe):parallized downloads

    #TODO(ghe): Embedded image client in ramdisk
    # - Get rid of iscsi, image location in baremetal service node and
    # image service, no master image, no image outdated...
    # - security concerns
    _cache_tftp_images(ctx, node, pxe_info)
    _cache_instance_image(ctx, node)
    #TODO(ghe): file injection
    # http://lists.openstack.org/pipermail/openstack-dev/2013-May/008728.html
    # http://lists.openstack.org/pipermail/openstack-dev/2013-July/011769.html
    # _inject_into_image(d_info, network_info, injected_files, admin_password)


def _destroy_images(d_info, node_uuid):
    """Delete instance's image file."""
    utils.unlink_without_raise(_get_image_file_path(node_uuid))
    utils.rmtree_without_raise(_get_image_dir_path(node_uuid))
    PXEImageCache(CONF.pxe.instance_master_path).clean_up()


def _create_token_file(task, node):
    """Save PKI token to file."""
    token_file_path = _get_token_file_path(node['uuid'])
    token = task.context.auth_token
    if token:
        utils.write_to_file(token_file_path, token)
    else:
        utils.unlink_without_raise(token_file_path)


def _destroy_token_file(node):
    """Delete PKI token file."""
    token_file_path = _get_token_file_path(node['uuid'])
    utils.unlink_without_raise(token_file_path)


def _remove_internal_attrs(task, node):
    """Remove internal attributes from driver_info."""
    internal_attrs = ('pxe_deploy_key', 'pxe_kernel', 'pxe_ramdisk')
    driver_info = node.driver_info
    for attr in internal_attrs:
        driver_info.pop(attr, None)
    node.driver_info = driver_info
    node.save(task.context)


class PXEDeploy(base.DeployInterface):
    """PXE Deploy Interface: just a stub until the real driver is ported."""

    def validate(self, task, node):
        """Validate the driver-specific Node deployment info.

        :param task: a task from TaskManager.
        :param node: a single Node to validate.
        :returns: InvalidParameterValue.
        """
        if not driver_utils.get_node_mac_addresses(task, node):
            raise exception.InvalidParameterValue(_("Node %s does not have "
                                "any port associated with it.") % node.uuid)
        _parse_driver_info(node)

        # Try to get the URL of the Ironic API
        try:
            # TODO(lucasagomes): Validate the format of the URL
            CONF.conductor.api_url or keystone.get_service_url()
        except (exception.CatalogFailure,
                exception.CatalogNotFound,
                exception.CatalogUnauthorized):
            raise exception.InvalidParameterValue(_(
                "Couldn't get the URL of the Ironic API service from the "
                "configuration file or keystone catalog."))

    @task_manager.require_exclusive_lock
    def deploy(self, task, node):
        """Perform start deployment a node.

        Creates a temporary keystone token file, updates the Neutron DHCP port
        options for next boot, and issues a reboot request to the power driver.
        This causes the node to boot into the deployment ramdisk and triggers
        the next phase of PXE-based deployment via
        VendorPassthru._continue_deploy().

        :param task: a TaskManager instance.
        :param node: the Node to act upon.
        :returns: deploy state DEPLOYING.
        """
        # TODO(yuriyz): more secure way needed for pass auth token
        #               to deploy ramdisk
        _create_token_file(task, node)
        neutron.update_neutron(task, node)
        manager_utils.node_set_boot_device(task, 'pxe', persistent=True)
        manager_utils.node_power_action(task, node, states.REBOOT)

        return states.DEPLOYWAIT

    @task_manager.require_exclusive_lock
    def tear_down(self, task, node):
        """Tear down a previous deployment.

        Power off the node. All actual clean-up is done in the clean_up()
        method which should be called separately.

        :param task: a TaskManager instance.
        :param node: the Node to act upon.
        :returns: deploy state DELETED.
        """
        manager_utils.node_power_action(task, node, states.POWER_OFF)
        _remove_internal_attrs(task, node)

        return states.DELETED

    def prepare(self, task, node):
        """Prepare the deployment environment for this node.

        Generates the TFTP configuration for PXE-booting both the deployment
        and user images, fetches the images from Glance and adds them to the
        local cache.

        :param task: a TaskManager instance.
        :param node: the Node to act upon.
        """
        # TODO(deva): optimize this if rerun on existing files
        pxe_info = _get_tftp_image_info(node, task.context)
        pxe_options = build_pxe_config_options(node, pxe_info, task.context)
        tftp.create_pxe_config(task, node, pxe_options)
        _cache_images(node, pxe_info, task.context)

    def clean_up(self, task, node):
        """Clean up the deployment environment for this node.

        Delete the deploy and user images from the local cache, if no remaining
        active nodes require them. Removes the TFTP configuration files for
        this node. As a precaution, this method also ensures the keystone auth
        token file was removed.

        :param task: a TaskManager instance.
        :param node: the Node to act upon.
        """
        pxe_info = _get_tftp_image_info(node, task.context)
        d_info = _parse_driver_info(node)
        for label in pxe_info:
            path = pxe_info[label][1]
            utils.unlink_without_raise(path)
        PXEImageCache(CONF.pxe.tftp_master_path).clean_up()

        utils.unlink_without_raise(tftp.get_pxe_config_file_path(
                node.uuid))
        for port in driver_utils.get_node_mac_addresses(task, node):
            mac_path = tftp.get_pxe_mac_path(port)
            utils.unlink_without_raise(mac_path)

        utils.rmtree_without_raise(
                os.path.join(CONF.tftp.tftp_root, node.uuid))

        _destroy_images(d_info, node.uuid)
        _destroy_token_file(node)

    def take_over(self, task, node):
        neutron.update_neutron(task, node)


class VendorPassthru(base.VendorInterface):
    """Interface to mix IPMI and PXE vendor-specific interfaces."""

    def _get_deploy_info(self, node, **kwargs):
        d_info = _parse_driver_info(node)

        deploy_key = kwargs.get('key')
        if d_info['deploy_key'] != deploy_key:
            raise exception.InvalidParameterValue(_("Deploy key does not"
                                                    " match"))

        params = {'address': kwargs.get('address'),
                  'port': kwargs.get('port', '3260'),
                  'iqn': kwargs.get('iqn'),
                  'lun': kwargs.get('lun', '1'),
                  'image_path': _get_image_file_path(node.uuid),
                  'pxe_config_path':
                      tftp.get_pxe_config_file_path(node.uuid),
                  'root_mb': 1024 * int(d_info['root_gb']),
                  'swap_mb': int(d_info['swap_mb']),
                  'ephemeral_mb': 1024 * int(d_info['ephemeral_gb']),
                  'preserve_ephemeral': d_info['preserve_ephemeral'],
            }

        missing = [key for key in params.keys() if params[key] is None]
        if missing:
            raise exception.InvalidParameterValue(_(
                    "Parameters %s were not passed to ironic"
                    " for deploy.") % missing)

        # ephemeral_format is nullable
        params['ephemeral_format'] = d_info.get('ephemeral_format')

        return params

    def validate(self, task, **kwargs):
        method = kwargs['method']
        if method == 'pass_deploy_info':
            self._get_deploy_info(task.node, **kwargs)
        else:
            raise exception.InvalidParameterValue(_(
                "Unsupported method (%s) passed to PXE driver.")
                % method)

        return True

    @task_manager.require_exclusive_lock
    def _continue_deploy(self, task, **kwargs):
        """Resume a deployment upon getting POST data from deploy ramdisk.

        This method raises no exceptions because it is intended to be
        invoked asynchronously as a callback from the deploy ramdisk.
        """
        node = task.node

        def _set_failed_state(msg):
            node.provision_state = states.DEPLOYFAIL
            node.target_provision_state = states.NOSTATE
            node.save(task.context)
            try:
                manager_utils.node_power_action(task, node, states.POWER_OFF)
            except Exception:
                msg = (_('Node %s failed to power off while handling deploy '
                         'failure. This may be a serious condition. Node '
                         'should be removed from Ironic or put in maintenance '
                         'mode until the problem is resolved.') % node.uuid)
                LOG.error(msg)
            finally:
                # NOTE(deva): node_power_action() erases node.last_error
                #             so we need to set it again here.
                node.last_error = msg
                node.save(task.context)

        if node.provision_state != states.DEPLOYWAIT:
            LOG.error(_('Node %s is not waiting to be deployed.') %
                      node.uuid)
            return
        node.provision_state = states.DEPLOYING
        node.save(task.context)
        # remove cached keystone token immediately
        _destroy_token_file(node)

        params = self._get_deploy_info(node, **kwargs)
        ramdisk_error = kwargs.get('error')

        if ramdisk_error:
            LOG.error(_('Error returned from PXE deploy ramdisk: %s')
                    % ramdisk_error)
            _set_failed_state(_('Failure in PXE deploy ramdisk.'))
            return

        LOG.info(_('Continuing deployment for node %(node)s, params '
                   '%(params)s') % {'node': node.uuid, 'params': params})

        try:
            deploy_utils.deploy(**params)
        except Exception as e:
            LOG.error(_('PXE deploy failed for instance %(instance)s. '
                        'Error: %(error)s') % {'instance': node.instance_uuid,
                                               'error': e})
            _set_failed_state(_('PXE driver failed to continue deployment.'))
        else:
            LOG.info(_('Deployment to node %s done') % node.uuid)
            node.provision_state = states.ACTIVE
            node.target_provision_state = states.NOSTATE
            node.save(task.context)

    def vendor_passthru(self, task, **kwargs):
        method = kwargs['method']
        if method == 'pass_deploy_info':
            self._continue_deploy(task, **kwargs)
