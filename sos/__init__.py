# Copyright 2010 Red Hat, Inc.
# Author: Adam Stokes <astokes@fedoraproject.org>

# This file is part of the sos project: https://github.com/sosreport/sos
#
# This copyrighted material is made available to anyone wishing to use,
# modify, copy, or redistribute it subject to the terms and conditions of
# version 2 of the GNU General Public License.
#
# See the LICENSE file in the source distribution for further information.


"""
This module houses the i18n setup and message function. The default is to use
gettext to internationalize messages.
"""
__version__ = "3.9"

import logging
import six
import sys
import tempfile

from argparse import ArgumentParser
from sos.options import SoSOptions, SosListOption
from sos.utilities import TempFileUtil


class SoSComponent():
    """Any sub-command that sos supports needs to subclass SoSComponent in
    order to be properly supported by the sos binary.

    This class contains the standardized entrypoint for subcommands, as well as
    building out supported options from both globally shared option lists, and
    options supported by that specific subcommand.

    When sos initializes, it will load an unintialized instance of each class
    found within one recursion of the module root directory that subclasses
    SoSComponent.

    If sos is able to match the user-specified subcommand to one that exists
    locally, then that SoSComponent is initialized, logging is setup, and a
    policy is loaded. From there, the component's execute() method takes over.

    Added in 4.0
    """

    desc = 'unset'

    arg_defaults = {}
    configure_logging = True

    _arg_defaults = {
        "config_file": '/etc/sos.conf',
        "quiet": False,
        "tmp_dir": '',
        "sysroot": None,
        "verbosity": 0
    }

    def __init__(self, parser, parsed_args, cmdline_args):
        self.parser = parser
        self.args = parsed_args
        self.cmdline = cmdline_args
        self.exit_process = False

        try:
            import signal
            signal.signal(signal.SIGTERM, self.get_exit_handler())
        except Exception:
            pass

        # update args from component's arg_defaults defintion
        self._arg_defaults.update(self.arg_defaults)
        self.opts = self.load_options()
        if self.configure_logging:
            tmpdir = self.opts.tmp_dir or tempfile.gettempdir()
            self.tmpdir = tempfile.mkdtemp(prefix="sos.", dir=tmpdir)
            self.tempfile_util = TempFileUtil(self.tmpdir)
            self._setup_logging()

    def get_exit_handler(self):
        def exit_handler(signum, frame):
            self.exit_process = True
            self._exit()
        return exit_handler

    def _exit(self, error=0):
        raise SystemExit(error)

    @classmethod
    def add_parser_options(cls, parser):
        """This should be overridden by each subcommand to add its own unique
        options to the parser
        """
        pass

    def load_options(self):
        """Compile arguments loaded from defaults, config files, and the command
        line into a usable set of options
        """
        # load the defaults defined by the component and the shared options
        opts = SoSOptions(arg_defaults=self._arg_defaults)

        for option in self.parser._actions:
            if option.default != '==SUPPRESS==':
                option.default = None

        # load values from cmdline
        cmdopts = SoSOptions().from_args(self.parser.parse_args())
        opts.merge(cmdopts)

        # load values from config file
        opts.update_from_conf(opts.config_file)

        return opts

    def _setup_logging(self):
        """Creates the log handler that shall be used by all components and any
        and all related bits to those components that need to log either to the
        console or to the log file for that run of sos.
        """
        # main soslog
        self.soslog = logging.getLogger('sos')
        self.soslog.setLevel(logging.DEBUG)
        self.sos_log_file = self.get_temp_file()
        flog = logging.StreamHandler(self.sos_log_file)
        flog.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)s: %(message)s'))
        flog.setLevel(logging.INFO)
        self.soslog.addHandler(flog)

        if not self.opts.quiet:
            console = logging.StreamHandler(sys.stdout)
            console.setFormatter(logging.Formatter('%(message)s'))
            if self.opts.verbosity and self.opts.verbosity > 1:
                console.setLevel(logging.DEBUG)
                flog.setLevel(logging.DEBUG)
            elif self.opts.verbosity and self.opts.verbosity > 0:
                console.setLevel(logging.INFO)
                flog.setLevel(logging.DEBUG)
            else:
                console.setLevel(logging.WARNING)
            self.soslog.addHandler(console)
            # log ERROR or higher logs to stderr instead
            console_err = logging.StreamHandler(sys.stderr)
            console_err.setFormatter(logging.Formatter('%(message)s'))
            console_err.setLevel(logging.ERROR)
            self.soslog.addHandler(console_err)

        # ui log
        self.ui_log = logging.getLogger('sos_ui')
        self.ui_log.setLevel(logging.INFO)
        self.sos_ui_log_file = self.get_temp_file()
        ui_fhandler = logging.StreamHandler(self.sos_ui_log_file)
        ui_fhandler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)s: %(message)s'))

        self.ui_log.addHandler(ui_fhandler)

        if not self.opts.quiet:
            ui_console = logging.StreamHandler(sys.stdout)
            ui_console.setFormatter(logging.Formatter('%(message)s'))
            ui_console.setLevel(logging.INFO)
            self.ui_log.addHandler(ui_console)

    def get_temp_file(self):
        return self.tempfile_util.new()


class SoS():
    """Main entrypoint for sos from the command line

    Upon intialization, this class loads the basic option parser which will
    include the options shared by support components/subcommands. This is also
    where all subcommands present in the local installation are discovered,
    loaded, and if a matching one is found, intialized.
    """

    def __init__(self, args):
        self.cmdline = args
        # define the local subcommands that exist on the system
        import sos.report
        self._components = {'report': sos.report.SoSReport}
        # build the top-level parser
        _com_string = ''
        for com in self._components:
            _com_string += "\t%s\t\t\t%s\n" % (com, self._components[com].desc)
        usage_string = ("%(prog)s <component> [options]\n\n"
                        "Available components:\n")
        usage_string = usage_string + _com_string
        epilog = ("See `sos <component> --help` for more information")
        self.parser = ArgumentParser(usage=usage_string, epilog=epilog)
        self.parser.register('action', 'extend', SosListOption)
        # set the component subparsers
        self.subparsers = self.parser.add_subparsers(
            dest='component',
            help='sos component to run'
        )
        self.subparsers.required = True
        # now build the parser for each component.
        # this needs to be done here, as otherwise --help will be unavailable
        # for the component subparsers
        for comp in self._components:
            _com_subparser = self.subparsers.add_parser(comp)
            _com_subparser.usage = "sos %s [options]" % comp
            _com_subparser.register('action', 'extend', SosListOption)
            self._add_common_options(_com_subparser)
            self._components[comp].add_parser_options(_com_subparser)
        self.args = self.parser.parse_args()
        self._init_component()

    def _add_common_options(self, parser):
        """Adds the options shared across components to the parser
        """
        parser.add_argument("--config-file", type=str, action="store",
                            dest="config_file", default="/etc/sos.conf",
                            help="specify alternate configuration file")
        parser.add_argument("-q", "--quiet", action="store_true",
                            dest="quiet", default=False,
                            help="only print fatal errors")
        parser.add_argument("-s", "--sysroot", action="store", dest="sysroot",
                            help="system root directory path (default='/')",
                            default=None)
        parser.add_argument("--tmp-dir", action="store",
                            dest="tmp_dir",
                            help="specify alternate temporary directory",
                            default=None)
        parser.add_argument("-v", "--verbose", action="count",
                            dest="verbosity", default=0,
                            help="increase verbosity")

    def _init_component(self):
        """Determine which component has been requested by the user, and then
        initialize that component.
        """
        _com = self.args.component
        if not _com in self._components.keys():
            print("Unknown subcommand '%s' specified" % _com)
        try:
            self._component = self._components[_com](self.parser, self.args,
                                                     self.cmdline)
        except Exception as err:
            print("Could not initialize '%s': %s" % (_com, err))
            sys.exit(1)

    def execute(self):
        self._component.execute()

# vim: set et ts=4 sw=4 :
