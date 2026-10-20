# Copyright 2026 Rackspace Technology.
# All Rights Reserved.
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

"""Versioned, deterministic per-node SNMPv3 credentials.

This module intentionally uses only the Python standard library so enrollment
consumers can use the same derivation without loading conductor configuration.

The derived username is ``<prefix>-<lowercase service tag>``, where ``prefix``
defaults to ``uc``. The prefix affects only the username; it is excluded from
the versioned passphrase-derivation contract, so changing it never changes the
auth or privacy passphrases.
"""

import argparse
import configparser
import hmac
import json
import re


def normalize_service_tag(service_tag):
    """Return the canonical Dell service tag, rejecting invalid identities."""
    if not isinstance(service_tag, str):
        raise ValueError('A service tag is required')
    value = service_tag.strip()
    if not value.isascii() or not re.fullmatch(r'[A-Za-z0-9]{7}', value):
        raise ValueError('A Dell service tag must contain seven ASCII '
                         'letters or digits')
    return value.upper()


def normalize_prefix(prefix):
    """Return the username prefix, rejecting invalid values.

    The prefix affects only the username (``<prefix>-<lowercase tag>``); it is
    not part of the versioned passphrase-derivation contract.
    """
    if not isinstance(prefix, str) or not re.fullmatch(r'[a-z0-9]{1,16}',
                                                       prefix):
        raise ValueError('The username prefix must contain 1 to 16 lowercase '
                         'ASCII letters or digits')
    return prefix


def derive_credentials(master_key, service_tag, prefix='uc'):
    """Derive v1 username, auth and privacy credentials for a Dell node.

    The key is used as UTF-8 text, not hex-decoded. Each passphrase is the
    first 32 lowercase hex characters of HMAC-SHA256 over the ASCII message
    ``ironic-snmpv3:v1:<purpose>:<UPPERCASE_SERVICE_TAG>``. Purposes are
    ``auth`` and ``privacy``. These inputs and normalization are a versioned
    contract: changing them would change credentials already on the BMCs.

    ``prefix`` (default ``uc``) affects only the derived username, which is
    ``<prefix>-<lowercase service tag>``. It is deliberately excluded from the
    HMAC message, so changing the prefix never changes the passphrases.
    """
    if not isinstance(master_key, str) or len(master_key) < 32:
        raise ValueError('SNMPv3 master key must contain at least '
                         '32 characters')
    tag = normalize_service_tag(service_tag)
    prefix = normalize_prefix(prefix)
    result = {'username': prefix + '-' + tag.lower()}
    for purpose in ('auth', 'privacy'):
        message = ('ironic-snmpv3:v1:%s:%s' % (purpose, tag)).encode('ascii')
        result[purpose + '_passphrase'] = hmac.new(
            master_key.encode('utf-8'), message, 'sha256').hexdigest()[:32]
    return result


def main(argv=None):
    """Explicitly export one node's credentials for trusted provisioning."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('service_tag')
    parser.add_argument('--config-file', required=True,
                        help='Protected INI file with [drac]snmpv3_master_key')
    parser.add_argument('--prefix', default='uc',
                        help='Username prefix, producing '
                             '<prefix>-<lowercase service tag> (default uc)')
    args = parser.parse_args(argv)
    config = configparser.ConfigParser(interpolation=None)
    try:
        with open(args.config_file, encoding='utf-8') as stream:
            config.read_file(stream)
        master = config.get('drac', 'snmpv3_master_key')
        credentials = derive_credentials(master, args.service_tag,
                                         prefix=args.prefix)
    except (OSError, configparser.Error, ValueError):
        # Parser errors may contain the INI line holding the master secret.
        parser.error('Cannot derive credentials: check the protected config '
                     'file, master key length, and seven-character '
                     'service tag')
    print(json.dumps(credentials))


if __name__ == '__main__':
    main()
