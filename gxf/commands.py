# -*- coding: utf-8 -*-

import sys
import argparse
import traceback

from contextlib import contextmanager

# if you didn't get argcomplete from its git after my patches
# where applied you should use the monkey patched version:
# import gxf.mpargcomplete as argcomplete
import argcomplete

import gdb


class GdbCompleterRequired(Exception):

    def __init__(self, gdbcompleter):
        self.gdbcompleter = gdbcompleter


class GdbCompleter(object):

    def __init__(self, gdbcompleter):
        self.gdbcompleter = gdbcompleter

    def __call__(self, *args, **kwargs):
        raise GdbCompleterRequired(self.gdbcompleter)


class LocationType(object):
    argcompleter = GdbCompleter(gdb.COMPLETE_LOCATION)

    def __call__(self, arg):
        return arg


class CommandType(object):
    argcompleter = GdbCompleter(gdb.COMPLETE_COMMAND)

    def __call__(self, arg):
        return arg


class SymbolType(object):
    argcompleter = GdbCompleter(gdb.COMPLETE_SYMBOL)

    def __call__(self, arg):
        try:
            return gdb.lookup_symbol(arg)
        except Exception as e:
            raise argparse.ArgumentTypeError(e)


class GlobalSymbolType(object):
    argcompleter = GdbCompleter(gdb.COMPLETE_SYMBOL)

    def __call__(self, arg):
        try:
            return gdb.lookup_global_symbol(arg)
        except Exception as e:
            raise argparse.ArgumentTypeError(e)


class ValueType(object):
    argcompleter = GdbCompleter(gdb.COMPLETE_EXPRESSION)

    def __call__(self, arg):
        try:
            return gdb.parse_and_eval(arg)
        except Exception as e:
            raise argparse.ArgumentTypeError(e)


class FileType(argparse.FileType):
    argcompleter = GdbCompleter(gdb.COMPLETE_FILENAME)


class StrType(str):
    argcompleter = GdbCompleter(gdb.COMPLETE_NONE)


class ArgumentParser(argparse.ArgumentParser):
    def exit(self, code=None, message=None):
        if message is not None:
            sys.exit(message.strip())
        else:
            sys.exit(code)


class register(object):

    global_prefix = []

    @staticmethod
    @contextmanager
    def prefix(prefix):
        register.global_prefix.append(prefix)
        yield
        register.global_prefix.pop()

    def __init__(self, cmdname=None, cmdtype=gdb.COMMAND_USER,
                 repeat=False, prefix=False, parent=[]):
        self.cmdname = cmdname
        self.cmdtype = cmdtype
        self.crepeat = repeat
        self.cprefix = prefix

        if not isinstance(parent, list):
            parent = [parent]
        self.parents = parent

    def __call__(self, cmd):
        if self.cmdname is None:
            self.cmdname = cmd.__name__.lower()

        name = " ".join(self.global_prefix + self.parents + [self.cmdname])
        cmd(name, self.cmdtype, self.crepeat, self.cprefix)
        return cmd


class Command(gdb.Command):

    description = ""
    epilog = ""

    def __init__(self, cmdname, cmdtype, repeat, prefix):

        self.cmdname = cmdname
        self.cmdtype = cmdtype
        self.repeat = repeat
        self.prefix = prefix

        # Setup a parser for this command.
        self.parser = ArgumentParser(
            prog=self.cmdname,
            description=self.__doc__)
        self.setup(self.parser)

        # We need to set the .completer hints in order for
        # argcomplete to know what to do on types we known.
        for action in self.parser._actions:
            if hasattr(action.type, "argcompleter"):
                action.completer = action.type.argcompleter

        # And prepare everything for autocompletion.
        self.completer = argcomplete.CompletionFinder(
            self.parser, always_complete_options=True)

        # gdb generates its help from the docstring.
        # We temporarilly overwrite it with argparse's output.
        old_doc, self.__doc__ = self.__doc__, self.parser.format_help().strip()

        # Call gdb's init. This will cause the command to be registerd.
        super().__init__(cmdname, cmdtype, prefix=prefix)

        # Restore the docstring so that it is usefull when looking
        # up help in python or when used for any other puprpose.
        self.__doc__ = old_doc

    def setup(self, parser):
        pass

    def invoke(self, args, isatty):

        if not self.repeat:
            self.dont_repeat()

        # Not sure we trust gdb to split the line as we want it, but
        # until there are problems we'll let him give it a shot.
        args = gdb.string_to_argv(args)

        try:
            self.run(self.parser.parse_args(args))
        except SystemExit as e:
            if isinstance(e.code, str):
                raise gdb.GdbError(e)
            elif e.code:
                raise gdb.GdbError("command exited with status %s." % e.code)
        except gdb.GdbError:
            raise
        except:
            traceback.print_exc()

    def complete(self, text, word):

        # We never want to fallback on readline.
        # Even more so when argcomplete does this by calling bash -c compgen.
        check_output = argcomplete.subprocess.check_output
        argcomplete.subprocess.check_output = lambda *a, **k: b""

        (cword_prequote, cword_prefix, cword_suffix,
         comp_words, first_colon_pos) = argcomplete.split_line(text)

        comp_words.insert(0, sys.argv[0])

        try:
            completions = self.completer._get_completions(
                comp_words, cword_prefix, cword_prequote, first_colon_pos)

        except GdbCompleterRequired as e:
            return e.gdbcompleter
        except:
            # This is not expected, but gdb doesn't give us a
            # backtrace in this case, we print it ourself.
            traceback.print_exc()
            raise
        finally:
            # Don't forget we cheated, fix this and hope no one saw us.
            argcomplete.subprocess.check_output = check_output

        # The characters used by gdb to decide what a 'word' is aren't
        # controled by us. We need to workaround this by ommiting the
        # first part of the actual word...
        # The downside of this trick is we won't see the whole word
        # in gdb's listing, only what he considers the word...
        return [c[len(cword_prefix) - len(word):].strip() for c in completions]
