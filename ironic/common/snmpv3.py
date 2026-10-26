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
import os
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


def _read_json_source(spec):
    """Return a field from a JSON file named by a ``path:a.b.c`` spec."""
    path, sep, dotted_key = spec.rpartition(':')
    keys = dotted_key.split('.')
    if not sep or not path or not all(keys):
        raise ValueError('a JSON source must be path:key (key may be a.b.c)')
    with open(path, encoding='utf-8') as stream:
        value = json.load(stream)
    for key in keys:
        value = value[key]
    return value


def _parse_key_map(spec):
    """Parse ``old=new,...`` into a mapping of output-key renames."""
    mapping = {}
    for pair in spec.split(','):
        old, sep, new = pair.partition('=')
        if not sep or not old or not new:
            raise ValueError('key map entries must be old=new')
        mapping[old] = new
    return mapping


def _read_master_key(config_file):
    """Return [drac]snmpv3_master_key from a protected INI file."""
    config = configparser.ConfigParser(interpolation=None)
    with open(config_file, encoding='utf-8') as stream:
        config.read_file(stream)
    return config.get('drac', 'snmpv3_master_key')


def _write_secret_file(path, text):
    """Write ``text`` via a 0600 temporary sibling renamed over ``path``.

    The rename forces owner-only permissions even over an existing file and
    never exposes a partially written file.
    """
    directory = os.path.dirname(path) or '.'
    temporary = os.path.join(directory, '.%s.tmp' % os.path.basename(path))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                         0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(text)
        os.replace(temporary, path)
    except BaseException:
        os.unlink(temporary)
        raise


def _build_parser():
    """Build the command-line parser for :func:`main`."""
    parser = argparse.ArgumentParser(description=__doc__)
    tag_source = parser.add_mutually_exclusive_group(required=True)
    tag_source.add_argument('service_tag', nargs='?',
                            help='Dell service tag (seven ASCII characters)')
    tag_source.add_argument(
        '--service-tag-from', metavar='PATH:KEY',
        help='read the tag from JSON, e.g. /run/node.json:node.service_tag')
    parser.add_argument('--config-file', required=True,
                        help='INI file with [drac]snmpv3_master_key')
    prefix_source = parser.add_mutually_exclusive_group()
    prefix_source.add_argument(
        '--prefix', default='uc', help='username prefix (default uc)')
    prefix_source.add_argument(
        '--prefix-from', metavar='PATH:KEY',
        help='read the prefix from JSON, e.g. /run/site.json:profile.prefix')
    parser.add_argument(
        '--output-file',
        help='write JSON to this 0600 file instead of stdout')
    parser.add_argument(
        '--key-map', metavar='OLD=NEW,...',
        help='rename output keys, e.g. username=sl1_snmp_username')
    return parser


def _resolve_credentials(args):
    """Derive the credentials as a dict, reading every secret-bearing input."""
    service_tag = (args.service_tag if args.service_tag is not None
                   else _read_json_source(args.service_tag_from))
    prefix = (_read_json_source(args.prefix_from)
              if args.prefix_from else args.prefix)
    credentials = derive_credentials(_read_master_key(args.config_file),
                                     service_tag, prefix=prefix)
    if args.key_map:
        renames = _parse_key_map(args.key_map)
        credentials = {renames.get(key, key): value
                       for key, value in credentials.items()}
    return credentials


def main(argv=None):
    """Export one node's credentials as JSON for trusted provisioning."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        credentials = _resolve_credentials(args)
    except (OSError, TypeError, KeyError, configparser.Error, ValueError):
        # The underlying error can carry the master, so report a fixed message.
        parser.error('Cannot derive credentials: check the protected config '
                     'file, master key length, service tag and prefix inputs')
    serialized = json.dumps(credentials)
    if not args.output_file:
        print(serialized)
        return
    try:
        _write_secret_file(args.output_file, serialized)
    except OSError as error:
        raise SystemExit('Cannot write %s: %s' % (
            args.output_file, error.strerror or error))


if __name__ == '__main__':
    main()
