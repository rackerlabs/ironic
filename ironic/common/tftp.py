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

import jinja2
from oslo.config import cfg

from ironic.common import utils
from ironic.drivers import utils as driver_utils
from ironic.openstack.common import fileutils
from ironic.openstack.common import log as logging


tftp_opts = [
    cfg.StrOpt('tftp_server',
               default='$my_ip',
               help='IP address of Ironic compute node\'s tftp server.',
               deprecated_group='pxe'),
    cfg.StrOpt('tftp_root',
               default='/tftpboot',
               help='Ironic compute node\'s tftp root path.',
               deprecated_group='pxe'),
    # NOTE(dekehn): Additional boot files options may be created in the event
    #  other architectures require different boot files.
    cfg.StrOpt('pxe_bootfile_name',
               default='pxelinux.0',
               help='Neutron bootfile DHCP parameter.',
               deprecated_group='pxe'),
    ]

CONF = cfg.CONF
CONF.register_opts(tftp_opts, group='tftp')

LOG = logging.getLogger(__name__)


def create_pxe_config(task, node, pxe_options, pxe_config_template):
    """Generate pxe configuration file and link mac ports to it for
    tftp booting.
    """
    fileutils.ensure_tree(os.path.join(CONF.tftp.tftp_root,
                                       node.uuid))
    fileutils.ensure_tree(os.path.join(CONF.tftp.tftp_root,
                                       'pxelinux.cfg'))

    pxe_config_file_path = get_pxe_config_file_path(node.uuid)
    pxe_config = build_pxe_config(node, pxe_options, pxe_config_template)
    utils.write_to_file(pxe_config_file_path, pxe_config)
    for port in driver_utils.get_node_mac_addresses(task, node):
        mac_path = get_pxe_mac_path(port)
        utils.unlink_without_raise(mac_path)
        utils.create_link_without_raise(pxe_config_file_path, mac_path)


def build_pxe_config(node, pxe_options, pxe_config_template):
    """Build the PXE config file for a node

    This method builds the PXE boot configuration file for a node,
    given all the required parameters.

    :param pxe_options: A dict of values to set on the configuarion file
    :returns: A formated string with the file content.
    """
    LOG.debug(_("Building PXE config for deployment %s.") % node['id'])

    tmpl_path, tmpl_file = os.path.split(pxe_config_template)
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(tmpl_path))
    template = env.get_template(tmpl_file)
    return template.render({'pxe_options': pxe_options,
                            'ROOT': '{{ ROOT }}'})


def get_pxe_mac_path(mac):
    """Convert a MAC address into a PXE config file name.

    :param mac: A mac address string in the format xx:xx:xx:xx:xx:xx.
    :returns: the path to the config file.
    """
    return os.path.join(
            CONF.tftp.tftp_root,
            'pxelinux.cfg',
            "01-" + mac.replace(":", "-").lower()
        )


def get_pxe_config_file_path(node_uuid):
    """Generate the path for an instances PXE config file."""
    return os.path.join(CONF.tftp.tftp_root, node_uuid, 'config')


def get_pxe_bootfile_name():
    """Returns the pxe_bootfile_name option."""
    return CONF.tftp.pxe_bootfile_name


def dhcp_options_for_instance():
    """Retrives the DHCP PXE boot options."""
    return [{'opt_name': 'bootfile-name',
             'opt_value': get_pxe_bootfile_name()},
            {'opt_name': 'server-ip-address',
             'opt_value': CONF.tftp.tftp_server},
            {'opt_name': 'tftp-server',
             'opt_value': CONF.tftp.tftp_server}
            ]
