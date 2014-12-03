# -*- coding: utf-8 -*-

import struct

import gxf
import gdb

from pygments.token import Token


# TODO: move this someplace else, utils?
def repr_long_str(s, maxl=None, maxc=6):

    rep = []
    total = 0
    accu = 0

    def add_accu(until):

        if accu >= until:
            return total

        # +3 because it takes 3 to add ... anyway.
        if maxl is not None and until > accu+maxl-total+3:
            limit = accu+maxl-total
        else:
            limit = until

        rep.append("%r" % s[accu:limit])

        if limit < until:
            rep.append("...")

        return total + limit-accu

    count, last = 0, None
    for i, c in enumerate(s):
        if c == last:
            count += 1

            # If this is the last char we force the fallthrough.
            if i < len(s) - 1:
                continue
            i += 1

        # we are done with everything before this character
        # If this is a repetition we add it to reps,
        # otherwise just let it be added to the accu.

        if count > maxc:

            total = add_accu(i - count)

            if maxl is not None and total >= maxl:
                accu = len(s)
                break

            rep.append("%r*%d" % (s[i-count], count))
            total += 3
            accu = i

        last = c
        count = 1

    total = add_accu(len(s))
    return "+".join(rep)


class RefChain(list, gxf.Formattable):
    def __init__(self, memory, addr):

        chain = []

        while True:

            if any(x[0] == addr for x in chain):
                val = ...
                break

            try:
                m = memory.get_section_or_map(addr)
                val = memory.read_ptr(addr)
            except gxf.MemoryError:
                break

            chain.append([addr, m, val])
            addr = val


        if not chain:
            # This wasn't even a valid pointer. We use the wanabee
            # address as value. (maybe taken from a register or other)
            chain.append([None, None, addr])

        # Now we examine the last element of the chain and we
        # try to find a better representation of its value.
        chain[-1][2] = self.guesstype(memory, *chain[-1])

        self.chain = chain

        list.__init__(self, self.chain)
        gxf.Formattable.__init__(self)

    def guesstype(self, memory, addr, m, val):

        # TODO: We should take little/big endian into account
        # to do this properly.

        if 0x20 < val < 0x7e:
            return gxf.Formattable(((Token.Numeric.Integer, str(val)),
                                    (Token.Comment, " %r" % chr(val))))
        elif val < 256:
            return gxf.Formattable(((Token.Numeric.Integer, str(val)),))

        bval = struct.pack("q" if int(val) < 0 else "Q", int(val))
        aval = struct.unpack("Q", bval)[0]

        # We only check utf8, do we need more?

        invalid = None
        try:
            sval = bval.decode("utf8")
        except UnicodeDecodeError as e:
            invalid = e.start
            sval = bval[:invalid].decode("utf8")

        nullbyte = sval.find("\x00")
        wval = sval[:nullbyte] if nullbyte >= 0 else sval

        if len(wval) == 8 or len(wval) == 4:
            # 4 bytes might be ok if this is 32 bit.

            if m is None or addr is None:
                # This didnt come from an address.
                # We can't read the full string.
                return repr(wval)

            # Read full string if al 8 bytes are good (and no nullbyte)
            # this should also reduce false positives with bytecode since
            # a decoding error *before* a null byte will make us forget
            # about trying to represent this as a string.

            try:
                wval = memory.read_str(addr, encoding="utf8")
                return repr_long_str(wval, 128)

            except UnicodeDecodeError:
                # fallthrough
                pass
        elif 3 <= len(wval) < 8 and invalid != len(wval):
            return repr(wval)
        elif 2 <= len(wval) < 8 and invalid is None:
            # We might not have had more than three characters but this
            # might still be somewhere where a lot of printable data is.
            return repr(wval)

        if m is not None and "x" in m.perms:
            # Not a string and executable, this might be disassembly.
            disline = gxf.disassemble_lines(addr, ignfct=True).lines[0]
            if disline.inst is not None:
                return disline

        # Not a string, not disassembly, what else?
        return aval

    def fmttokens(self):

        for addr, m, val in self[:-1]:
            yield from m.fmtaddr(addr)
            yield (Token.Comment, " : ")

        # The last element is special, we want to check if it can
        # format itself. We also have some special handling for
        # known types such as DisasselmblyLines.
        addr, mmap, val = self[-1]

        if isinstance(val, gxf.DisassemblyLine):
            # We format the address itself the way we do the others
            # but we let the instruction print the ':' because it
            # might want to print the function name before it.
            yield from mmap.fmtaddr(addr)
            yield (Token.Text, " ")
            yield from val.fmttokens(offset=val.addressidx+1,
                                     skipleading=True, style=None)

        else:
            if mmap is not None and addr is not None:
                yield from mmap.fmtaddr(addr)
                yield (Token.Comment, " : ")

            if isinstance(val, gxf.Formattable):
                yield from val.fmttokens()
            elif isinstance(val, str):
                yield (Token.Text, "%s" % val)
            else:
                yield (Token.Text, ("%d" if abs(val) < 128 else "%#x") % int(val))




class MMap(gxf.Formattable):

    def __init__(self, start, end, perms, backing=None, comment=None):
        if not backing:
            backing = None
        self.start = start
        self.end = end
        self.perms = perms
        self.backing = backing
        self.comment = None

    def __contains__(self, addr):
        return self.start <= addr < self.end

    def fmttokens(self):

        ttype = Token.Comment
        if "r" in self.perms:
            ttype = Token.Text
        if "w" in self.perms:
            ttype = Token.Generic.Heading
        if "x" in self.perms:
            ttype = Token.Generic.Subheading
        if "w" in self.perms and "x" in self.perms:
            ttype = Token.Generic.Deleted

        yield (ttype, "%#x-%#x %s %s %s\n" % (self.start, self.end, self.perms,
                                                   self.backing, self.comment or ""))

    def fmtaddr(self, addr):

        if "x" in self.perms:
            token = Token.Generic.Deleted
        elif "w" in self.perms:
            token = Token.Keyword
        elif "r" in self.perms:
            token = Token.Generic.Inserted
        else:
            token = Token.Text

        yield (token, "%#.x" % addr)

class Section(MMap):

    def __init__(self, start, end, name, tags):
        self.tags = tags

        perms = "%s%s%sp" % ("r",
                             "w" if not "READONLY" in tags else "-",
                             "x" if "CODE" in tags else "-")

        super().__init__(start, end, perms, name, comment=" ".join(self.tags))

class Memory(gxf.Formattable):

    def __init__(self):

        self.inf = gxf.inferiors.get_selected_inferior()

        if not self.inf.threads():
            raise ValueError("inferior is not running")

        self.maps = self._read_maps()
        self.sections = self._read_sections()

        # TODO: implement fallback using a virtual MMap that maps everything.
        #       let it fail when we try to read from it later on.
        #       Make sure this idea works before relying on it.

    def _read_maps(self):

        # Ok if this fails:
        # either the process isn't running or you don't have access to its /proc/
        # if /proc/ isnt available please just `mount -t procfs proc /proc`

        try:
            mapf = open("/proc/%d/maps" % self.inf.pid)
        except IOError:
            return []

        maps = []

        for line in mapf:
            startend, perms, _ = line.split(None, 2)
            _, backing = line.rsplit(None, 1)
            start, end = (int(x, 16) for x in startend.split("-"))
            maps.append(MMap(start, end, perms, backing))

        return maps

    def _read_sections(self):
        data = gxf.execute("maintenance info sections")

        sections = []

        for line in data.splitlines()[2:]:
            try:
                _, startend, _, _, name, tags = line.split(None, 5)
                start, end = (int(x, 16) for x in startend.split("->"))
            except:
                continue
            tags = tags.split()
            if "LOAD" in tags:
                sections.append(Section(start, end, name, tags))

        return sections

    def read_ptr(self, addr):
        # TODO: maybe use read_memory stuff ?
        return gxf.parse_and_eval("*(void **)%#x" % addr)

    def read_str(self, addr, *args, **kwargs):
        ptr = gxf.parse_and_eval("(char *)%#x" % addr)
        return ptr.string(*args, **kwargs)

    def get_section_or_map(self, addr):
        for s in self.sections:
            if addr in s: return s
        for m in self.maps:
            if addr in m: return m
        raise gxf.MemoryError(addr)

    def get_map(self, addr):
        for m in self.maps:
            if addr in m: return m
        for s in self.sections:
            if addr in s: return s
        raise gxf.MemoryError(addr)

    def refchain(self, addr):
        return RefChain(self, addr)

    def fmttokens(self, address=None):
        for section in self.sections:
            if address is None or address in section:
                yield from section.fmttokens()
        for mmap in self.maps:
            if address is None or address in mmap:
                yield from mmap.fmttokens()

    def output(self, *args, **kwargs):
        print(self.format(*args, **kwargs), end="")
