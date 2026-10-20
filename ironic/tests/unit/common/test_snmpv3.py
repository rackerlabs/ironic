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

"""Tests for the versioned SNMPv3 credential contract."""

import contextlib
import io
import json
import unittest
from unittest import mock

from ironic.common import snmpv3


class Snmpv3TestCase(unittest.TestCase):

    master = '0123456789abcdef0123456789abcdef'

    def test_known_vector(self):
        self.assertEqual({
            'username': 'uc-abc1234',
            'auth_passphrase': 'b42a89a8e493e33f1c9c6e7a4c2eea51',
            'privacy_passphrase': 'eb9a69944a64bab95a6e3f67e7c9a745',
        }, snmpv3.derive_credentials(self.master, 'ABC1234'))

    def test_default_prefix(self):
        # The default prefix must reproduce the published test vector.
        explicit = snmpv3.derive_credentials(self.master, 'ABC1234',
                                             prefix='uc')
        self.assertEqual(snmpv3.derive_credentials(self.master, 'ABC1234'),
                         explicit)
        self.assertEqual('uc-abc1234', explicit['username'])

    def test_custom_prefix_changes_only_username(self):
        default = snmpv3.derive_credentials(self.master, 'ABC1234')
        custom = snmpv3.derive_credentials(self.master, 'ABC1234',
                                           prefix='mon01')
        self.assertEqual('mon01-abc1234', custom['username'])
        # Passphrases must be byte-for-byte identical regardless of prefix.
        self.assertEqual(default['auth_passphrase'],
                         custom['auth_passphrase'])
        self.assertEqual(default['privacy_passphrase'],
                         custom['privacy_passphrase'])

    def test_invalid_prefix(self):
        for prefix in ('', 'SL1', 'sl_1', 'sl1é', 'a' * 17, None, 3, 'a b'):
            with self.subTest(prefix=prefix):
                self.assertRaises(ValueError, snmpv3.derive_credentials,
                                  self.master, 'ABC1234', prefix)

    def test_invalid_prefix_does_not_echo_master(self):
        with self.assertRaises(ValueError) as exc:
            snmpv3.derive_credentials(self.master, 'ABC1234', prefix='BAD')
        self.assertNotIn(self.master, str(exc.exception))

    def test_normalization(self):
        expected = snmpv3.derive_credentials(self.master, 'ABC1234')
        self.assertEqual(expected,
                         snmpv3.derive_credentials(self.master, ' abc1234 '))

    def test_separation(self):
        first = snmpv3.derive_credentials(self.master, 'ABC1234')
        other_node = snmpv3.derive_credentials(self.master, 'XYZ5678')
        other_master = snmpv3.derive_credentials('x' * 32, 'ABC1234')
        self.assertNotEqual(first['auth_passphrase'],
                            first['privacy_passphrase'])
        for field in ('auth_passphrase', 'privacy_passphrase'):
            self.assertNotEqual(first[field], other_node[field])
            self.assertNotEqual(first[field], other_master[field])
            self.assertRegex(first[field], r'^[0-9a-f]{32}$')

    def test_invalid_identity(self):
        for tag in (None, '', 'ABC123', 'ABC12345', 'AB-1234',
                    'ABC12é4', 'ABſ1234'):
            with self.subTest(tag=tag):
                self.assertRaises(ValueError, snmpv3.derive_credentials,
                                  self.master, tag)

    def test_invalid_master(self):
        for master in (None, '', 'x' * 31, b'x' * 32):
            with self.subTest(master=master):
                self.assertRaises(ValueError, snmpv3.derive_credentials,
                                  master, 'ABC1234')

    def test_cli(self):
        config = '[drac]\nsnmpv3_master_key = ' + self.master + '\n'
        with mock.patch('builtins.open', mock.mock_open(read_data=config)):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                snmpv3.main(['ABC1234', '--config-file', '/protected/config'])
        self.assertEqual(snmpv3.derive_credentials(self.master, 'ABC1234'),
                         json.loads(output.getvalue()))
        self.assertEqual('uc-abc1234',
                         json.loads(output.getvalue())['username'])
        self.assertNotIn(self.master, output.getvalue())

    def test_cli_prefix(self):
        config = '[drac]\nsnmpv3_master_key = ' + self.master + '\n'
        with mock.patch('builtins.open', mock.mock_open(read_data=config)):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                snmpv3.main(['ABC1234', '--config-file', '/protected/config',
                             '--prefix', 'mon01'])
        result = json.loads(output.getvalue())
        self.assertEqual('mon01-abc1234', result['username'])
        self.assertEqual(snmpv3.derive_credentials(self.master, 'ABC1234'),
                         snmpv3.derive_credentials(self.master, 'ABC1234'))
        # Passphrases are prefix-independent.
        default = snmpv3.derive_credentials(self.master, 'ABC1234')
        self.assertEqual(default['auth_passphrase'],
                         result['auth_passphrase'])
        self.assertEqual(default['privacy_passphrase'],
                         result['privacy_passphrase'])

    def test_cli_errors_do_not_echo_secrets(self):
        for config in ('[drac]\nmalformed secret ' + self.master,
                       '[drac]\nsnmpv3_master_key = short',
                       '[other]\npassword = ' + self.master):
            with self.subTest(config=config):
                with mock.patch('builtins.open',
                                mock.mock_open(read_data=config)):
                    with contextlib.redirect_stderr(io.StringIO()) as err:
                        with self.assertRaises(SystemExit) as exc:
                            snmpv3.main(['ABC1234', '--config-file', 'config'])
                self.assertEqual(2, exc.exception.code)
                self.assertNotIn(self.master, err.getvalue())
                self.assertNotIn('malformed secret', err.getvalue())

    def test_cli_missing_file(self):
        with mock.patch('builtins.open', autospec=True,
                        side_effect=FileNotFoundError):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                with contextlib.redirect_stderr(io.StringIO()) as err:
                    with self.assertRaises(SystemExit) as exc:
                        snmpv3.main(['ABC1234', '--config-file', 'missing'])
        self.assertEqual(2, exc.exception.code)
        self.assertEqual('', out.getvalue())
        self.assertIn('Cannot derive credentials', err.getvalue())

    def test_cli_invalid_tag(self):
        config = '[drac]\nsnmpv3_master_key = ' + self.master + '\n'
        with mock.patch('builtins.open', mock.mock_open(read_data=config)):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                with contextlib.redirect_stderr(io.StringIO()) as err:
                    with self.assertRaises(SystemExit) as exc:
                        snmpv3.main(['invalid-tag', '--config-file', 'config'])
        self.assertEqual(2, exc.exception.code)
        self.assertEqual('', out.getvalue())
        self.assertNotIn(self.master, err.getvalue())

    def test_cli_master_is_literal_text(self):
        master = self.master + '%value'
        config = '[drac]\nsnmpv3_master_key = ' + master + '\n'
        with mock.patch('builtins.open', mock.mock_open(read_data=config)):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                snmpv3.main(['ABC1234', '--config-file', 'config'])
        self.assertEqual(snmpv3.derive_credentials(master, 'ABC1234'),
                         json.loads(output.getvalue()))
