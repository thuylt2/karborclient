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

"""
Command-line interface to the karbor Project.
"""

from __future__ import print_function

import argparse
import copy
import sys

from keystoneauth1 import discover
from keystoneauth1 import exceptions as ks_exc
from keystoneauth1.identity.generic import password
from keystoneauth1.identity.generic import token
from keystoneauth1 import loading
from oslo_log import handlers
from oslo_log import log as logging
from oslo_utils import encodeutils
from oslo_utils import importutils

import six
import six.moves.urllib.parse as urlparse

import karborclient
from karborclient import client as karbor_client
from karborclient.common.apiclient import exceptions as exc
from karborclient.common import utils


logger = logging.getLogger(__name__)


class KarborShell(object):

    def _append_global_identity_args(self, parser, argv):
        loading.register_session_argparse_arguments(parser)
        # Peek into argv to see if os-auth-token (or the deprecated
        # os_auth_token) or the new os-token or the environment variable
        # OS_AUTH_TOKEN were given. In which case, the token auth plugin is
        # what the user wants. Else, we'll default to password.
        default_auth_plugin = 'password'
        token_opts = ['os-token', 'os-auth-token', 'os_auth-token']
        if argv and any(i in token_opts for i in argv):
            default_auth_plugin = 'token'
        loading.register_auth_argparse_arguments(
            parser, argv, default=default_auth_plugin)

    def get_base_parser(self, argv):

        parser = argparse.ArgumentParser(
            prog='karbor',
            description=__doc__.strip(),
            epilog='See "karbor help COMMAND" '
                   'for help on a specific command.',
            add_help=False,
            formatter_class=HelpFormatter,
        )

        # Global arguments
        parser.add_argument('-h', '--help',
                            action='store_true',
                            help=argparse.SUPPRESS, )

        parser.add_argument('--version',
                            action='version',
                            version=karborclient.__version__,
                            help="Show program's version number and exit.")

        parser.add_argument('-d', '--debug',
                            default=bool(utils.env('KARBORCLIENT_DEBUG')),
                            action='store_true',
                            help='Defaults to env[KARBORCLIENT_DEBUG].')

        parser.add_argument('-v', '--verbose',
                            default=False, action="store_true",
                            help="Print more verbose output.")

        # os-cert, os-key, insecure, ca-file are all added
        # by keystone session register_cli_opts later
        parser.add_argument('--cert-file',
                            dest='os_cert',
                            help='DEPRECATED! Use --os-cert.')

        parser.add_argument('--key-file',
                            dest='os_key',
                            help='DEPRECATED! Use --os-key.')

        parser.add_argument('--ca-file',
                            dest='os_cacert',
                            help='DEPRECATED! Use --os-cacert.')

        parser.add_argument('--api-timeout',
                            help='Number of seconds to wait for an '
                                 'API response, '
                                 'defaults to system socket timeout.')

        parser.add_argument('--os_tenant_id',
                            default=utils.env('OS_TENANT_ID'),
                            help='Defaults to env[OS_TENANT_ID].')

        parser.add_argument('--os_tenant_name',
                            default=utils.env('OS_TENANT_NAME'),
                            help='Defaults to env[OS_TENANT_NAME].')

        parser.add_argument('--os-region-name',
                            default=utils.env('OS_REGION_NAME'),
                            help='Defaults to env[OS_REGION_NAME].')

        parser.add_argument('--os-auth-token',
                            default=utils.env('OS_AUTH_TOKEN'),
                            help='Defaults to env[OS_AUTH_TOKEN].')

        parser.add_argument('--os-no-client-auth',
                            default=utils.env('OS_NO_CLIENT_AUTH'),
                            action='store_true',
                            help="Do not contact keystone for a token. "
                                 "Defaults to env[OS_NO_CLIENT_AUTH].")
        parser.add_argument('--karbor-url',
                            default=utils.env('KARBOR_URL'),
                            help='Defaults to env[KARBOR_URL].')

        parser.add_argument('--karbor-api-version',
                            default=utils.env(
                                'KARBOR_API_VERSION', default='1'),
                            help='Defaults to env[KARBOR_API_VERSION] '
                                 'or 1.')

        parser.add_argument('--os-service-type',
                            default=utils.env('OS_SERVICE_TYPE'),
                            help='Defaults to env[OS_SERVICE_TYPE].')

        parser.add_argument('--os-endpoint-type',
                            default=utils.env('OS_ENDPOINT_TYPE'),
                            help='Defaults to env[OS_ENDPOINT_TYPE].')

        parser.add_argument('--include-password',
                            default=bool(utils.env('KARBOR_INCLUDE_PASSWORD')),
                            action='store_true',
                            help='Send os-username and os-password to karbor.')

        self._append_global_identity_args(parser, argv)

        return parser

    def get_subcommand_parser(self, version, argv=None):
        parser = self.get_base_parser(argv)

        self.subcommands = {}
        subparsers = parser.add_subparsers(metavar='<subcommand>')
        submodule = importutils.import_versioned_module(
            'karborclient', version, 'shell'
        )
        self._find_actions(subparsers, submodule)
        self._find_actions(subparsers, self)

        return parser

    def _find_actions(self, subparsers, actions_module):
        for attr in (a for a in dir(actions_module) if a.startswith('do_')):
            # I prefer to be hypen-separated instead of underscores.
            command = attr[3:].replace('_', '-')
            callback = getattr(actions_module, attr)
            desc = callback.__doc__ or ''
            help = desc.strip().split('\n')[0]
            arguments = getattr(callback, 'arguments', [])

            subparser = subparsers.add_parser(command, help=help,
                                              description=desc,
                                              add_help=False,
                                              formatter_class=HelpFormatter)
            subparser.add_argument('-h', '--help', action='help',
                                   help=argparse.SUPPRESS)
            self.subcommands[command] = subparser
            for (args, kwargs) in arguments:
                subparser.add_argument(*args, **kwargs)
            subparser.set_defaults(func=callback)

    def _discover_auth_versions(self, session, auth_url):
        # discover the API versions the server is supporting base on the
        # given URL
        v2_auth_url = None
        v3_auth_url = None
        try:
            ks_discover = discover.Discover(session=session, url=auth_url)
            v2_auth_url = ks_discover.url_for('2.0')
            v3_auth_url = ks_discover.url_for('3.0')
        except ks_exc.ClientException as e:
            # Identity service may not support discover API version.
            # Lets trying to figure out the API version from the original URL.
            url_parts = urlparse.urlparse(auth_url)
            (scheme, netloc, path, params, query, fragment) = url_parts
            path = path.lower()
            if path.startswith('/v3'):
                v3_auth_url = auth_url
            elif path.startswith('/v2'):
                v2_auth_url = auth_url
            else:
                # not enough information to determine the auth version
                msg = ('Unable to determine the Keystone version '
                       'to authenticate with using the given '
                       'auth_url. Identity service may not support API '
                       'version discovery. Please provide a versioned '
                       'auth_url instead. error=%s') % (e)
                raise exc.CommandError(msg)

        return (v2_auth_url, v3_auth_url)

    def _get_keystone_auth(self, session, auth_url, **kwargs):
        auth_token = kwargs.pop('auth_token', None)
        if auth_token:
            return token.Token(
                auth_url,
                auth_token,
                project_id=kwargs.pop('project_id'),
                project_name=kwargs.pop('project_name'),
                project_domain_id=kwargs.pop('project_domain_id'),
                project_domain_name=kwargs.pop('project_domain_name'))

        # NOTE(starodubcevna): this is a workaround for the bug:
        # https://bugs.launchpad.net/python-openstackclient/+bug/1447704
        # Change that fix this error in keystoneclient was abandoned,
        # so we should use workaround until we move to keystoneauth.
        # The idea of the code came from glanceclient.

        (v2_auth_url, v3_auth_url) = self._discover_auth_versions(
            session=session,
            auth_url=auth_url)

        if v3_auth_url:
            # NOTE(starodubcevna): set user_domain_id and project_domain_id
            # to default as it done in other projects.
            return password.Password(auth_url,
                                     username=kwargs.pop('username'),
                                     user_id=kwargs.pop('user_id'),
                                     password=kwargs.pop('password'),
                                     user_domain_id=kwargs.pop(
                                         'user_domain_id') or 'default',
                                     user_domain_name=kwargs.pop(
                                         'user_domain_name'),
                                     project_id=kwargs.pop('project_id'),
                                     project_name=kwargs.pop('project_name'),
                                     project_domain_id=kwargs.pop(
                                         'project_domain_id') or 'default')
        elif v2_auth_url:
            return password.Password(auth_url,
                                     username=kwargs.pop('username'),
                                     user_id=kwargs.pop('user_id'),
                                     password=kwargs.pop('password'),
                                     project_id=kwargs.pop('project_id'),
                                     project_name=kwargs.pop('project_name'))
        else:
            # if we get here it means domain information is provided
            # (caller meant to use Keystone V3) but the auth url is
            # actually Keystone V2. Obviously we can't authenticate a V3
            # user using V2.
            exc.CommandError("Credential and auth_url mismatch. The given "
                             "auth_url is using Keystone V2 endpoint, which "
                             "may not able to handle Keystone V3 credentials. "
                             "Please provide a correct Keystone V3 auth_url.")

    def _setup_logging(self, debug):
        # Output the logs to command-line interface
        color_handler = handlers.ColorHandler(sys.stdout)
        logger_root = logging.getLogger(None).logger
        logger_root.level = logging.DEBUG if debug else logging.WARNING
        logger_root.addHandler(color_handler)

        # Set the logger level of special library
        logging.getLogger('iso8601') \
            .logger.setLevel(logging.WARNING)
        logging.getLogger('urllib3.connectionpool') \
            .logger.setLevel(logging.WARNING)

    def main(self, argv):
        # Parse args once to find version
        base_argv = copy.deepcopy(argv)
        parser = self.get_base_parser(argv)
        (options, args) = parser.parse_known_args(base_argv)
        self._setup_logging(options.debug)

        # build available subcommands based on version
        api_version = options.karbor_api_version
        subcommand_parser = self.get_subcommand_parser(api_version, argv)
        self.parser = subcommand_parser

        ks_session = None
        keystone_auth = None

        # Handle top-level --help/-h before attempting to parse
        # a command off the command line.
        if (not args and options.help) or not argv:
            self.do_help(options)
            return 0

        # Parse args again and call whatever callback was selected.
        args = subcommand_parser.parse_args(argv)

        # Short-circuit and deal with help command right away.
        if args.func == self.do_help:
            self.do_help(args)
            return 0
        elif args.func == self.do_bash_completion:
            self.do_bash_completion(args)
            return 0

        if not args.os_username and not args.os_auth_token:
            raise exc.CommandError("You must provide a username via"
                                   " either --os-username or env[OS_USERNAME]"
                                   " or a token via --os-auth-token or"
                                   " env[OS_AUTH_TOKEN]")

        if args.os_no_client_auth:
            if not args.karbor_url:
                raise exc.CommandError(
                    "If you specify --os-no-client-auth"
                    " you must also specify a Karbor API URL"
                    " via either --karbor-url or env[KARBOR_URL]")

        else:
            # Tenant name or ID is needed to make keystoneclient retrieve a
            # service catalog, it's not required if os_no_client_auth is
            # specified, neither is the auth URL.
            if not any([args.os_tenant_name, args.os_tenant_id,
                        args.os_project_id, args.os_project_name]):
                raise exc.CommandError("You must provide a project name or"
                                       " project id via --os-project-name,"
                                       " --os-project-id, env[OS_PROJECT_ID]"
                                       " or env[OS_PROJECT_NAME]. You may"
                                       " use os-project and os-tenant"
                                       " interchangeably.")
            if not args.os_auth_url:
                raise exc.CommandError("You must provide an auth url via"
                                       " either --os-auth-url or via"
                                       " env[OS_AUTH_URL]")

        endpoint = args.karbor_url

        if args.os_no_client_auth:
            # Authenticate through karbor, don't use session
            kwargs = {
                'username': args.os_username,
                'password': args.os_password,
                'auth_token': args.os_auth_token,
                'auth_url': args.os_auth_url,
                'token': args.os_auth_token,
                'insecure': args.insecure,
                'timeout': args.api_timeout
            }

            if args.os_region_name:
                kwargs['region_name'] = args.os_region_name
        else:
            # Create a keystone session and keystone auth
            ks_session = loading.load_session_from_argparse_arguments(args)
            project_id = args.os_project_id or args.os_tenant_id
            project_name = args.os_project_name or args.os_tenant_name

            keystone_auth = self._get_keystone_auth(
                ks_session,
                args.os_auth_url,
                username=args.os_username,
                user_id=args.os_user_id,
                user_domain_id=args.os_user_domain_id,
                user_domain_name=args.os_user_domain_name,
                password=args.os_password,
                auth_token=args.os_auth_token,
                project_id=project_id,
                project_name=project_name,
                project_domain_id=args.os_project_domain_id,
                project_domain_name=args.os_project_domain_name)

            endpoint_type = args.os_endpoint_type or 'publicURL'
            service_type = args.os_service_type or 'data-protect'

            endpoint = keystone_auth.get_endpoint(
                ks_session,
                service_type=service_type,
                region_name=args.os_region_name)

            kwargs = {
                'session': ks_session,
                'auth': keystone_auth,
                'service_type': service_type,
                'endpoint_type': endpoint_type,
                'region_name': args.os_region_name,
            }

        if args.api_timeout:
            kwargs['timeout'] = args.api_timeout

        self.cs = karbor_client.Client(api_version, endpoint, **kwargs)

        args.func(self.cs, args)

    def do_bash_completion(self, args):
        """Prints all of the commands and options to stdout."""
        commands = set()
        options = set()
        for sc_str, sc in self.subcommands.items():
            commands.add(sc_str)
            for option in list(sc._optionals._option_string_actions):
                options.add(option)

        commands.remove('bash-completion')
        print(' '.join(commands | options))

    @utils.arg('command', metavar='<subcommand>', nargs='?',
               help='Display help for <subcommand>')
    def do_help(self, args):
        """Display help about this program or one of its subcommands.

        """
        if getattr(args, 'command', None):
            if args.command in self.subcommands:
                self.subcommands[args.command].print_help()
            else:
                msg = "'%s' is not a valid subcommand"
                raise exc.CommandError(msg % args.command)
        else:
            self.parser.print_help()


class HelpFormatter(argparse.HelpFormatter):
    def start_section(self, heading):
        # Title-case the headings
        heading = '%s%s' % (heading[0].upper(), heading[1:])
        super(HelpFormatter, self).start_section(heading)


def main(args=sys.argv[1:]):
    try:
        KarborShell().main(args)

    except KeyboardInterrupt:
        print('... terminating karbor client', file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        if '--debug' in args or '-d' in args:
            raise
        else:
            print(encodeutils.safe_encode(six.text_type(e)), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
