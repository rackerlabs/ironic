# Copyright 2013 Hewlett-Packard Development Company, L.P.
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

import hashlib
import hmac
import time

from oslo.config import cfg
import six.moves.urllib.parse as urlparse

from ironic.common import exception as exc
from ironic.common.glance_service import base_image_service
from ironic.common.glance_service import service
from ironic.common.glance_service import service_utils
from ironic.common import utils


glance_opts = [
    cfg.ListOpt('allowed_direct_url_schemes',
                default=[],
                help='A list of URL schemes that can be downloaded directly '
                'via the direct_url.  Currently supported schemes: '
                '[file].'),
    # To upload this key to Swift:
    # swift post -m Temp-Url-Key:correcthorsebatterystaple
    cfg.StrOpt('swift_temp_url_key',
                help='The secret token given to Swift to allow temporary URL '
                     'downloads.'),
    cfg.ListOpt('swift_temp_url_methods',
                default=['GET'],
                help='A list of HTTP methods allowed for the temporary URL in '
                     'Swift. Must be one of ["GET", "HEAD", "PUT", "POST", '
                     '"DELETE"].'),
    cfg.IntOpt('swift_temp_url_duration',
                default=3600,
                help='The length of time in seconds that the temporary URL '
                     'will be valid for.'),
    cfg.StrOpt('swift_scheme',
                help='Override the scheme for the Swift server. Should be '
                     'either "http" or "https".'),
    cfg.StrOpt('swift_endpoint_url',
                help='Override the base URL for the Swift server (without '
                     'scheme). Should not have a trailing "/" or API version. '
                     'For example, use "swift.example.com".'),
    cfg.StrOpt('swift_path',
                help='Override the the string between host and Swift '
                     'container name, including version string. Should not '
                     'include leading or trailing slashes. For example, '
                     '"v1/TENANT_NAME_TENANT_ID".'),
    cfg.StrOpt('swift_backend_container',
                help='Override the name of the Swift container in which '
                     'Glance stores its images.')
]

CONF = cfg.CONF
CONF.register_opts(glance_opts, group='glance')


class GlanceImageService(base_image_service.BaseImageService,
                         service.ImageService):

    def detail(self, **kwargs):
        return self._detail(method='list', **kwargs)

    def show(self, image_id):
        return self._show(image_id, method='get')

    def download(self, image_id, data=None):
        return self._download(image_id, method='data', data=data)

    def create(self, image_meta, data=None):
        image_id = self._create(image_meta, method='create', data=None)['id']
        return self.update(image_id, None, data)

    def update(self, image_id, image_meta, data=None, purge_props=False):
        # NOTE(ghe): purge_props not working until bug 1206472 solved
        return self._update(image_id, image_meta, data, method='update',
                            purge_props=False)

    def delete(self, image_id):
        return self._delete(image_id, method='delete')

    def swift_temp_url(self, image_info, duration=None):
        """Generate a no-auth Swift temporary URL.

        Returns a temporary URL that is good for duration seconds. This
        allows Ironic to download a Glance image without passing around an
        auth_token. If Glance has 'show_image_direct_url' enabled, we will
        use the URL from that to find the Swift URL. Otherwise, we will infer
        the URL from the Glance image-id and require a set of config options
        to be set: 'swift_scheme', 'swift_endpoint_url', 'swift_path',
        'swift_backend_container'.

        :param image_info: The return from a GET request to Glance for a
        certain image_id. Should be a dictionary, with keys like 'name' and
        'checksum'. See
        http://docs.openstack.org/developer/glance/glanceapi.html for
        examples.
        :returns: A signed Swift URL that can be downloaded from without auth.

        :raises: ImageNotFound, InvalidParameterValue
        """
        no_direct_url_required_params = [
            'swift_scheme', 'swift_endpoint_url', 'swift_path',
            'swift_backend_container']

        self._validate_temp_url_config()
        duration = duration or CONF.glance.swift_temp_url_duration
        if duration is None:
            raise exc.InvalidParameterValue(_('Must either call with a '
                                              'duration or set the config '
                                              'option '
                                              'swift_temp_url_duration'))

        direct_url = image_info['properties'].get('direct_url')
        if not direct_url:
            # If not direct_url, we can still get the Swift url if enough
            # static config settings are set. We'll just use image_id as the
            # object_id
            for param in no_direct_url_required_params:
                if not getattr(CONF.glance, param):

                    raise exc.InvalidMAC(_(
                        'Images must either have a direct_url or each of %s '
                        'to use swift temp urls.') %
                        no_direct_url_required_params)
            url_fragments = {
                'host': CONF.glance.swift_endpoint_url,
                'scheme': CONF.glance.swift_scheme,
                'path': CONF.glance.swift_path,
                'container': CONF.glance.swift_backend_container,
                'object_id': image_info['properties'].get('image_id')
            }

        else:
            url_fragments = self._swift_url_fragments(direct_url)

        template = '/{path}/{container}/{object_id}'
        url_path = template.format(
            path=url_fragments['path'].lstrip('/').rstrip('/'),
            container=url_fragments['container'],
            object_id=url_fragments['object_id']
        )

        expiration = int(time.time() + duration)
        # Make a normalized space delimited list of upper case version of
        # provided methods.
        methods = ' '.join(map(lambda x: x.upper(),
                               CONF.glance.swift_temp_url_methods))
        hmac_body = '\n'.join([methods, str(expiration), url_path])
        key = CONF.glance.swift_temp_url_key
        # Encode to UTF-8
        try:
            sig = hmac.new(key.encode(),
                           hmac_body.encode(),
                           hashlib.sha1).hexdigest()
        except UnicodeDecodeError:
            raise exc.InvalidParameterValue(_('Could not convert '
                                              'swift temp url arguments '
                                              'to Unicode for url.'))

        return ('{scheme}://{host}{url_path}?temp_url_sig='
                '{sig}&temp_url_expires={exp}'.format(
                    scheme=url_fragments['scheme'],
                    host=url_fragments['host'],
                    url_path=url_path,
                    sig=sig,
                    exp=expiration)
                )

    def _validate_temp_url_config(self):
        """Validate all the settings for a temp url."""
        if not CONF.glance.swift_temp_url_key:
            raise exc.InvalidParameterValue(_(
                'Swift temporary URLs require a shared secret to be created. '
                'You must provide swift_temp_url_key as a config option.'))
        # Normalize
        methods = map(lambda x: x.upper(), CONF.glance.swift_temp_url_methods)
        if not methods:
            raise exc.InvalidParameterValue(_('Must have at least one HTTP '
                                              'method.'))

        valid_methods = ['GET', 'HEAD', 'PUT', 'POST', 'DELETE']
        if len(set(methods) - set(valid_methods)) > 0:
            raise exc.InvalidParameterValue(
                _('Parameter '"swift_temp_url_methods"' must be set to one or '
                  'more of these methods:  %s') % valid_methods)

    def _swift_url_fragments(self, direct_url):
        """Get the temp URL fragments from thge direct_url."""
        parsed = urlparse.urlparse(direct_url)

        # Remove username/password if they exist.
        swift_host = parsed.netloc.split('@')[-1]

        try:
            empty, path, container, object_id = parsed.path.split('/')
        except ValueError:
            raise exc.ImageUnacceptable(_(
                'The Glance direct_url %s could not be decoded in "path", '
                '"container" and "object_id" variables. You may need to set '
                'the Swift config options to customize how the Swift temporary'
                ' URLs are built.') % direct_url)
        if not utils.is_uuid_like(object_id):
            raise exc.ImageUnacceptable(_('The object name %s in the Glance '
                                          'direct_url isn\'t a valid UUID.') %
                                          object_id)

        return {
            'host': CONF.glance.swift_endpoint_url or swift_host,
            'scheme': CONF.glance.swift_scheme or parsed.scheme,
            'path': CONF.glance.swift_path or path,
            'container': CONF.glance.swift_backend_container or container,
            'object_id': object_id,
        }

    def _get_location(self, image_id):
        """Returns the direct url representing the backend storage location,
        or None if this attribute is not shown by Glance.
        """
        image_meta = self.call('get', image_id)

        if not service_utils.is_image_available(self.context, image_meta):
            raise exc.ImageNotFound(image_id=image_id)

        return getattr(image_meta, 'direct_url', None)
