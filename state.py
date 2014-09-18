# -*- coding: utf-8 -*-

#    Copyright 2014 Mark Brand - c01db33f (at) gmail.com
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.


import copy
import itertools
import re

from concolica import interlocked
from concolica import memory
from concolica.utils import *
from concolica.vulnerabilities import *

import reil

import smt
import smt.bitvector as bv
import smt.boolean as bl


class Registers(dict):

    __slots__ = ('_depth', '_parent')


    def __init__(self, parent):
        dict.__init__(self)
        self._depth = parent._depth + 1
        if self._depth > 8:
            self._parent = parent._parent
            self.update(self._parent.items())
        else:
            self._parent = parent


    def __contains__(self, item):
        if dict.__contains__(self, item):
            return True

        return item in self._parent


    def __missing__(self, key):
        return self._parent[key]


class State(object):

    _state_id = interlocked.Counter()

    def __init__(self, parent=None):
        self.id = State._state_id.increment()
        self.parent = parent

        if parent:
            self.ip = parent.ip
            self.il_index = parent.il_index

            self.memory = memory.DynamicMemory(parent.memory)
            self.registers = Registers(parent.registers)
            self.call_stack = copy.copy(parent.call_stack)

            self.solver = smt.Solver(parent.solver)
            self.files = copy.deepcopy(parent.files)

        else:
            self.ip = 0
            self.il_index = 0

            self.memory = None
            self.registers = None
            self.call_stack = []

            self.solver = smt.Solver()
            self.files = []


    def fork(self):
        new = None
        if self.registers.dirty or self.memory.dirty:
            # we are substantively different to parent
            new = State(self)
        else:
            new = State(self.parent)
            new.ip = self.ip
            new.il_index = self.il_index


    def read(self, address, size):
        if arbitrary(self, address):
            raise ArbitraryRead(self, address)

        as_ = concretise(self, address)
        try:
            if len(as_) > 1:
                e = None
                value = bv.Symbol(size, unique_name('read'))

                for a in as_:
                    v = None
                    for i in range(0, size // 8):
                        if v is None:
                            v = self.memory[a.value + i]
                        else:
                            v = self.memory[a.value + i].concatenate(v)

                    if e is None:
                        e = (address == a) & (value == v)
                    else:
                        e = e | ((address == a) & (value == v))

                self.solver.add(e)
            else:
                value = self.memory[as_[0].value]

                for i in range(1, size // 8):
                    value = self.memory[as_[0].value + i].concatenate(value)
        except KeyError:
            raise InvalidRead(self, address)

        return value


    def write(self, address, value):
        bytes = []
        for i in range(0, value.size, 8):
            bytes.append(value.extract(start=i, end=i + 8))
        bytes.reverse()

        if arbitrary(self, address):
            raise ArbitraryWrite(self, address, value)

        as_ = concretise(self, address)
        if len(as_) > 1:
            for a in as_:
                for i, byte in enumerate(bytes):
                    self.memory[a.value + i] = smt.bv.IfThenElse(
                            a != address,
                            self.memory[a.value + i],
                            byte)
        else:
            for i, byte in enumerate(bytes):
                self.memory[as_[0].value + i] = byte


    def branch(self, address):
        ss = []
        if isinstance(address, reil.OffsetOperand):
            self.il_index = address.offset
            ss.append(self)
        else:
            #sp =

            self.il_index = 0xfffffff
            if address.symbolic:
                if arbitrary(self, address):
                    raise ArbitraryExecution(self, address)
                else:
                    ss = []
                    as_ = concretise(self, address)
                    if len(as_) > 1:
                        for a in as_:
                            b = self.fork()
                            b.ip = a.value
                            ss.append(b)
                    else:
                        self.ip = as_[0].value
                        ss.append(self)
            else:
                self.ip = address.value
                ss.append(self)

        return ss